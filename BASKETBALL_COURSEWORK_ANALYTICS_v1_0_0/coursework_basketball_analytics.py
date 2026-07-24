#!/usr/bin/env python3
"""Coursework basketball analytics for archived parser snapshots.

The program predicts the distribution of points still to be scored from
historical quarter data. It supports concurrent files and independent
checkpoints after each quarter. Commercial metadata is outside the model,
and the program has no network or external-delivery integration.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import sys
from typing import Any, Iterable, Optional


SYSTEM_VERSION = '1.0.0'
OUTPUT_KEY = 'coursework_basketball_analytics'
READINESS_VALUES = {'READY', 'REVIEW_REQUIRED', 'INSUFFICIENT_DATA'}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(',', '.')
    if text.endswith('%'):
        text = text[:-1]
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_int(value: Any) -> Optional[int]:
    number = to_number(value)
    return None if number is None else int(round(number))


def first(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if mapping.get(name) not in (None, ''):
            return mapping[name]
    return None


def percentile(values: Iterable[float], probability: float) -> Optional[float]:
    rows = sorted(float(value) for value in values)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * max(0.0, min(1.0, float(probability)))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    fraction = position - lower
    return rows[lower] * (1.0 - fraction) + rows[upper] * fraction


def infer_quarter_minutes(mapping: dict[str, Any], tournament: str = '') -> int:
    rules = mapping.get('rules') if isinstance(mapping.get('rules'), dict) else {}
    explicit = to_int(first(rules, ['quarter_minutes', 'period_minutes']))
    if explicit is None:
        explicit = to_int(first(mapping, ['quarter_minutes', 'period_minutes']))
    if explicit is not None and 5 <= explicit <= 15:
        return explicit
    name = str(tournament or '').upper()
    if 'WNBA' in name or 'FIBA' in name or 'EUROLEAGUE' in name:
        return 10
    if re.search(r'\bNBA\b', name):
        return 12
    return 10


def extract_quarters(match: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Optional[float]]]:
    block = match.get('quarters') if isinstance(match.get('quarters'), dict) else {}
    quarters: list[dict[str, Optional[float]]] = []
    for number in range(1, 5):
        row = block.get(f'q{number}') if isinstance(block.get(f'q{number}'), dict) else {}
        home = to_number(row.get('home'))
        away = to_number(row.get('away'))
        total = to_number(row.get('total'))
        if home is None:
            home = to_number(raw.get(f'q{number}h'))
        if away is None:
            away = to_number(raw.get(f'q{number}a'))
        if total is None:
            total = to_number(raw.get(f'q{number}t'))
        if total is None and home is not None and away is not None:
            total = home + away
        quarters.append({'home': home, 'away': away, 'total': total})
    return quarters


def _parse_elapsed_from_text(text: str, quarter_minutes: int) -> Optional[float]:
    value = str(text or '')
    match = re.search(r'([1-4])[^\d]{0,20}(?:чверть|quarter)[^\d]*(\d+)?', value, re.IGNORECASE)
    if not match:
        return None
    quarter = int(match.group(1))
    played = int(match.group(2) or 0)
    return (quarter - 1) * quarter_minutes + min(quarter_minutes, played)


def infer_checkpoint(source: dict[str, Any], canonical: Optional[dict[str, Any]] = None) -> int:
    context = source.get('analysis_context') if isinstance(source.get('analysis_context'), dict) else {}
    explicit = to_int(first(context, ['trigger_checkpoint', 'checkpoint']))
    if explicit is not None:
        return max(0, min(4, explicit))
    if canonical is None:
        canonical = canonical_snapshot(source)
    if canonical.get('finished'):
        return 4
    elapsed = float(canonical.get('elapsed_minutes') or 0.0)
    quarter_minutes = max(1, int(canonical.get('quarter_minutes') or 10))
    return max(0, min(3, int(elapsed // quarter_minutes)))


def canonical_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    match = source.get('match') if isinstance(source.get('match'), dict) else {}
    raw_data = source.get('raw_data') if isinstance(source.get('raw_data'), dict) else {}
    raw = raw_data.get('main_match') if isinstance(raw_data.get('main_match'), dict) else {}
    match_id = str(first(match, ['id', 'match_id']) or first(raw, ['mid', 'id']) or '').strip()
    home_team = str(first(match, ['home_team', 'home']) or first(raw, ['ht', 'home_team']) or '').strip()
    away_team = str(first(match, ['away_team', 'away']) or first(raw, ['at', 'away_team']) or '').strip()
    name = str(match.get('name') or f'{home_team} — {away_team}').strip()
    tournament = str(first(match, ['tournament', 'league']) or first(raw, ['tour']) or '')
    format_mapping = deepcopy(match)
    if isinstance(source.get('rules'), dict):
        format_mapping['rules'] = deepcopy(source['rules'])
    quarter_minutes = infer_quarter_minutes(format_mapping, tournament)
    regulation_minutes = quarter_minutes * 4
    elapsed = to_number(first(match, ['match_minute_played', 'elapsed_minutes']))
    period = to_int(first(match, ['period', 'quarter', 'current_quarter']))
    period_played = to_number(first(match, ['period_minute_played', 'quarter_minute_played']))
    period_left = to_number(first(match, ['period_minute_left', 'quarter_minute_left']))
    if elapsed is None and period is not None and period_played is not None:
        elapsed = (period - 1) * quarter_minutes + period_played
    status_text = str(first(match, ['stage', 'status']) or first(raw, ['st', 'status']) or '')
    if elapsed is None:
        elapsed = _parse_elapsed_from_text(status_text, quarter_minutes)
    finished = bool(re.search(r'FINISHED|FINAL|ENDED|ЗАВЕРШ', status_text, re.IGNORECASE))
    if elapsed is None:
        elapsed = float(regulation_minutes if finished else 0.0)
    elapsed = max(0.0, min(float(regulation_minutes), float(elapsed)))
    if period is None:
        period = 4 if finished else min(4, int(elapsed // quarter_minutes) + 1)
    if period_left is None:
        played_in_period = elapsed - (period - 1) * quarter_minutes
        period_left = max(0.0, quarter_minutes - played_in_period)
    score = match.get('score') if isinstance(match.get('score'), dict) else {}
    home_score = to_number(score.get('home'))
    away_score = to_number(score.get('away'))
    total_score = to_number(score.get('total'))
    if home_score is None:
        home_score = to_number(first(raw, ['hs', 'home_score'])) or 0.0
    if away_score is None:
        away_score = to_number(first(raw, ['as_', 'away_score'])) or 0.0
    if total_score is None:
        total_score = to_number(first(raw, ['tot', 'total']))
    if total_score is None:
        total_score = home_score + away_score
    quarters = extract_quarters(match, raw)
    schema_errors: list[str] = []
    if not match_id:
        schema_errors.append('match_id')
    if not home_team:
        schema_errors.append('home_team')
    if not away_team:
        schema_errors.append('away_team')
    canonical = {
        'match_id': match_id,
        'name': name,
        'home_team': home_team,
        'away_team': away_team,
        'tournament': tournament,
        'quarter_minutes': quarter_minutes,
        'regulation_minutes': regulation_minutes,
        'elapsed_minutes': elapsed,
        'current_quarter': period,
        'current_quarter_minutes_left': max(0.0, min(float(quarter_minutes), float(period_left))),
        'finished': finished or elapsed >= regulation_minutes,
        'score': {'home': home_score, 'away': away_score, 'total': total_score},
        'quarters': quarters,
        'schema_errors': schema_errors,
    }
    canonical['checkpoint'] = infer_checkpoint(source, canonical)
    return canonical


def safe_snapshot_for_hash(source: dict[str, Any]) -> dict[str, Any]:
    """Keep only sporting/research input sections in the stable identity."""
    context = source.get('analysis_context') if isinstance(source.get('analysis_context'), dict) else {}
    return {
        'schema_version': source.get('schema_version'),
        'match': deepcopy(source.get('match') or {}),
        'rules': deepcopy(source.get('rules') or {}),
        'analysis_context': {
            'trigger_checkpoint': first(context, ['trigger_checkpoint', 'checkpoint']),
            'research_replay': bool(context.get('research_replay', False)),
        },
        'live_team_stats': deepcopy(source.get('live_team_stats') or {}),
        'raw_data': deepcopy(source.get('raw_data') or {}),
    }


def academic_input_hash(source: dict[str, Any]) -> str:
    payload = json.dumps(
        safe_snapshot_for_hash(source),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def run_key(match_id: str, checkpoint: int, input_hash: str) -> str:
    text = f'{match_id}|Q{int(checkpoint)}|{input_hash}'
    return 'ACADEMIC-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:24].upper()


def history_game_key(row: dict[str, Any]) -> str:
    explicit = str(first(row, ['mid', 'match_id', 'id']) or '').strip()
    if explicit:
        return explicit
    text = '|'.join(str(first(row, names) or '') for names in (
        ['dt', 'date'], ['ht', 'home_team'], ['at', 'away_team'],
        ['hs', 'home_score'], ['as_', 'away_score'],
    ))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:20]


def is_technical_result(row: dict[str, Any]) -> bool:
    scores = (to_int(first(row, ['hs', 'home_score'])), to_int(first(row, ['as_', 'away_score'])))
    return scores in {(20, 0), (0, 20)}


def history_quarter_totals(row: dict[str, Any]) -> list[Optional[float]]:
    values: list[Optional[float]] = []
    for quarter in range(1, 5):
        total = to_number(first(row, [f'q{quarter}t', f'q{quarter}_total']))
        home = to_number(first(row, [f'q{quarter}h', f'home_q{quarter}']))
        away = to_number(first(row, [f'q{quarter}a', f'away_q{quarter}']))
        if total is None and home is not None and away is not None:
            total = home + away
        values.append(total)
    return values


def build_unique_history_pool(source: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    raw_data = source.get('raw_data') if isinstance(source.get('raw_data'), dict) else {}
    pools = [
        raw_data.get('team_a_hist') or [],
        raw_data.get('team_b_hist') or [],
        raw_data.get('h2h_hist') or [],
    ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded = {'current_match': 0, 'technical_result': 0, 'duplicate': 0, 'incomplete_quarters': 0}
    n_raw = 0
    for pool in pools:
        for row in pool:
            if not isinstance(row, dict):
                continue
            n_raw += 1
            key = history_game_key(row)
            if canonical['match_id'] and key == canonical['match_id']:
                excluded['current_match'] += 1
                continue
            if is_technical_result(row):
                excluded['technical_result'] += 1
                continue
            if key in seen:
                excluded['duplicate'] += 1
                continue
            quarter_totals = history_quarter_totals(row)
            if any(value is None for value in quarter_totals):
                excluded['incomplete_quarters'] += 1
                continue
            seen.add(key)
            rows.append({
                'game_id': key,
                'quarter_totals': [float(value) for value in quarter_totals if value is not None],
                'regulation_minutes': infer_quarter_minutes(row, str(first(row, ['tour', 'tournament']) or '')) * 4,
            })
    same_format = [row for row in rows if row['regulation_minutes'] == canonical['regulation_minutes']]
    return {
        'games': same_format,
        'n_raw': n_raw,
        'n_unique_complete': len(rows),
        'n_same_format': len(same_format),
        'game_ids': [row['game_id'] for row in same_format],
        'excluded': excluded,
    }


def resolve_remaining_plan(canonical: dict[str, Any]) -> dict[str, Any]:
    if canonical['finished'] or canonical['checkpoint'] >= 4:
        return {
            'future_quarters': [],
            'current_quarter': canonical['current_quarter'],
            'current_quarter_remaining_ratio': 0.0,
            'partial_current_quarter': False,
        }
    quarter = max(1, min(4, int(canonical['current_quarter'] or canonical['checkpoint'] + 1)))
    ratio = max(0.0, min(1.0, canonical['current_quarter_minutes_left'] / canonical['quarter_minutes']))
    first_unfinished = max(canonical['checkpoint'] + 1, quarter)
    future = list(range(first_unfinished, 5))
    if ratio <= 0 and future and future[0] == quarter:
        future = future[1:]
    return {
        'future_quarters': future,
        'current_quarter': quarter,
        'current_quarter_remaining_ratio': ratio if quarter in future else 0.0,
        'partial_current_quarter': bool(quarter in future and 0.0 < ratio < 1.0),
    }


def historical_remaining_values(pool: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    values: list[float] = []
    ids: list[str] = []
    current_quarter = int(plan.get('current_quarter') or 1)
    current_ratio = float(plan.get('current_quarter_remaining_ratio') or 0.0)
    for game in pool.get('games') or []:
        total = 0.0
        valid = True
        for quarter in plan.get('future_quarters') or []:
            rows = game.get('quarter_totals') or []
            if quarter < 1 or quarter > len(rows):
                valid = False
                break
            value = to_number(rows[quarter - 1])
            if value is None:
                valid = False
                break
            if quarter == current_quarter:
                value *= current_ratio
            total += value
        if valid and (plan.get('future_quarters') or []):
            ids.append(str(game['game_id']))
            values.append(total)
    return {
        'values': values,
        'game_ids': ids,
        'n_unique': len(values),
        'approximation_used': bool(plan.get('partial_current_quarter')),
    }


def describe_distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if not rows:
        return {
            'n': 0, 'mean': None, 'median': None, 'standard_deviation': None,
            'p10': None, 'p25': None, 'p75': None, 'p90': None,
            'minimum': None, 'maximum': None,
        }
    return {
        'n': len(rows),
        'mean': statistics.fmean(rows),
        'median': statistics.median(rows),
        'standard_deviation': statistics.stdev(rows) if len(rows) > 1 else 0.0,
        'p10': percentile(rows, 0.10),
        'p25': percentile(rows, 0.25),
        'p75': percentile(rows, 0.75),
        'p90': percentile(rows, 0.90),
        'minimum': min(rows),
        'maximum': max(rows),
    }


def quarter_profile(pool: dict[str, Any], future_quarters: Iterable[int]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for quarter in future_quarters:
        values = [
            float(game['quarter_totals'][quarter - 1])
            for game in pool.get('games') or []
            if len(game.get('quarter_totals') or []) >= quarter
        ]
        result[f'Q{quarter}'] = describe_distribution(values)
    return result


def readiness_for(sample_n: int, schema_errors: Iterable[str]) -> str:
    if list(schema_errors) or sample_n < 8:
        return 'INSUFFICIENT_DATA'
    if sample_n < 20:
        return 'REVIEW_REQUIRED'
    return 'READY'


def build_forecast(canonical: dict[str, Any], remaining: dict[str, Any]) -> dict[str, Any]:
    distribution = describe_distribution(remaining.get('values') or [])
    current_total = float(canonical['score']['total'])
    median_remaining = distribution['median']
    if canonical['finished']:
        forecast_total = current_total
        interval = [current_total, current_total]
    elif median_remaining is None:
        forecast_total = None
        interval = [None, None]
    else:
        forecast_total = current_total + float(median_remaining)
        interval = [
            current_total + float(distribution['p10']),
            current_total + float(distribution['p90']),
        ]
    return {
        'current_total_points': current_total,
        'historical_remaining_distribution': distribution,
        'forecast_final_total_points': forecast_total,
        'central_interval_p10_p90': interval,
        'formula': 'current_total_points + median(historical_remaining_points)',
        'already_scored_points_added_once': True,
        'approximation_used': bool(remaining.get('approximation_used')),
    }


def validate_output(result: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    model = result.get(OUTPUT_KEY) or {}
    if model.get('data_readiness') not in READINESS_VALUES:
        errors.append('INVALID_READINESS')
    forecast = model.get('forecast') or {}
    for key in ('current_total_points', 'forecast_final_total_points'):
        value = forecast.get(key)
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(float(value))):
            errors.append(f'INVALID_{key.upper()}')
    sample_n = int((forecast.get('historical_remaining_distribution') or {}).get('n') or 0)
    ids = (model.get('history_audit') or {}).get('sample_game_ids') or []
    if sample_n != len(ids):
        errors.append('SAMPLE_ID_COUNT_MISMATCH')
    if model.get('external_delivery', {}).get('enabled') is not False:
        errors.append('EXTERNAL_DELIVERY_NOT_DISABLED')
    return {'all_passed': not errors, 'errors': errors}


def calculate_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    canonical = canonical_snapshot(source)
    input_hash = academic_input_hash(source)
    identity = run_key(canonical['match_id'], canonical['checkpoint'], input_hash)
    pool = build_unique_history_pool(source, canonical)
    plan = resolve_remaining_plan(canonical)
    remaining = historical_remaining_values(pool, plan)
    forecast = build_forecast(canonical, remaining)
    readiness = readiness_for(
        int(forecast['historical_remaining_distribution']['n']),
        canonical['schema_errors'],
    )
    result = {
        OUTPUT_KEY: {
            'system_version': SYSTEM_VERSION,
            'research_context': True,
            'created_at': utc_now(),
            'input_hash': input_hash,
            'run_key': identity,
            'match': {
                'match_id': canonical['match_id'],
                'name': canonical['name'],
                'home_team': canonical['home_team'],
                'away_team': canonical['away_team'],
                'quarter_minutes': canonical['quarter_minutes'],
                'regulation_minutes': canonical['regulation_minutes'],
            },
            'checkpoint': canonical['checkpoint'],
            'snapshot_state': {
                'elapsed_minutes': canonical['elapsed_minutes'],
                'current_quarter': canonical['current_quarter'],
                'current_quarter_minutes_left': canonical['current_quarter_minutes_left'],
                'score': canonical['score'],
                'finished': canonical['finished'],
            },
            'data_readiness': readiness,
            'data_quality': {
                'schema_errors': canonical['schema_errors'],
                'same_format_history_n': pool['n_same_format'],
                'ignored_non_sport_sections': True,
            },
            'remaining_plan': plan,
            'forecast': forecast,
            'quarter_profiles': quarter_profile(pool, plan['future_quarters']),
            'history_audit': {
                'n_raw': pool['n_raw'],
                'n_unique_complete': pool['n_unique_complete'],
                'n_same_format': pool['n_same_format'],
                'sample_game_ids': remaining['game_ids'],
                'excluded': pool['excluded'],
            },
            'external_delivery': {'enabled': False, 'reason': 'COURSEWORK_OFFLINE_ONLY'},
        }
    }
    result[OUTPUT_KEY]['integrity_report'] = validate_output(result)
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).expanduser().resolve().open('r', encoding='utf-8') as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError('input JSON root must be an object')
    return value


def save_json(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    temporary.replace(target)


class AcademicStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=30000')
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS processed_runs (
                    run_key TEXT PRIMARY KEY,
                    match_id TEXT NOT NULL,
                    checkpoint INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    data_readiness TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                'CREATE INDEX IF NOT EXISTS idx_processed_match_checkpoint ON processed_runs(match_id, checkpoint)'
            )

    def contains(self, identity: str) -> bool:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                'SELECT 1 FROM processed_runs WHERE run_key = ?', (identity,)
            ).fetchone()
        return row is not None

    def record(self, result: dict[str, Any], output_path: str | Path) -> None:
        model = result[OUTPUT_KEY]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                '''
                INSERT OR IGNORE INTO processed_runs
                (run_key, match_id, checkpoint, input_hash, output_path, data_readiness, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    model['run_key'], model['match']['match_id'], model['checkpoint'],
                    model['input_hash'], str(Path(output_path).resolve()),
                    model['data_readiness'], model['created_at'],
                ),
            )

    def count(self) -> int:
        with closing(self._connect()) as connection, connection:
            return int(connection.execute('SELECT COUNT(*) FROM processed_runs').fetchone()[0])


def output_path_for(source_path: Path, outbox: Path, checkpoint: int, input_hash: str) -> Path:
    return outbox / f'{source_path.stem}_cp{checkpoint}_{input_hash[:12]}_analytics.json'


def process_file(
    source_path: str | Path,
    output_path: str | Path,
    *,
    store: Optional[AcademicStore] = None,
) -> dict[str, Any]:
    source = load_json(source_path)
    result = calculate_snapshot(source)
    save_json(output_path, result)
    if store is not None:
        store.record(result, output_path)
    return result


def process_batch(
    paths: Iterable[str | Path],
    outbox: str | Path,
    *,
    db_path: str | Path,
    workers: int = 4,
) -> dict[str, Any]:
    outbox_path = Path(outbox).expanduser().resolve()
    outbox_path.mkdir(parents=True, exist_ok=True)
    store = AcademicStore(db_path)
    unique_paths: list[Path] = []
    seen_paths: set[str] = set()
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if str(resolved) not in seen_paths:
            seen_paths.add(str(resolved))
            unique_paths.append(resolved)
    prepared: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for path in unique_paths:
        try:
            source = load_json(path)
            canonical = canonical_snapshot(source)
            input_hash = academic_input_hash(source)
            identity = run_key(canonical['match_id'], canonical['checkpoint'], input_hash)
            output = output_path_for(path, outbox_path, canonical['checkpoint'], input_hash)
        except Exception as exc:
            items.append({'source': str(path), 'state': 'FAILED_PREFLIGHT', 'error': f'{type(exc).__name__}: {exc}'})
            continue
        base = {
            'source': str(path), 'output': str(output), 'match_id': canonical['match_id'],
            'checkpoint': canonical['checkpoint'], 'input_hash': input_hash, 'run_key': identity,
        }
        if identity in seen_runs:
            items.append({**base, 'state': 'SKIPPED_DUPLICATE_IN_BATCH'})
        elif store.contains(identity):
            items.append({**base, 'state': 'SKIPPED_DUPLICATE_PROCESSED'})
        else:
            seen_runs.add(identity)
            prepared.append({**base, 'source_path': path, 'output_path': output})
    worker_count = max(1, min(int(workers), max(1, len(prepared))))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix='coursework') as executor:
        futures = {
            executor.submit(process_file, row['source_path'], row['output_path'], store=store): row
            for row in prepared
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()[OUTPUT_KEY]
                items.append({
                    **{key: row[key] for key in ('source', 'output', 'match_id', 'checkpoint', 'input_hash', 'run_key')},
                    'state': 'PROCESSED',
                    'data_readiness': result['data_readiness'],
                    'integrity_passed': result['integrity_report']['all_passed'],
                })
            except Exception as exc:
                items.append({
                    **{key: row[key] for key in ('source', 'output', 'match_id', 'checkpoint', 'input_hash', 'run_key')},
                    'state': 'FAILED_PROCESSING',
                    'error': f'{type(exc).__name__}: {exc}',
                })
    items.sort(key=lambda row: (str(row.get('source') or ''), str(row.get('state') or '')))
    return {
        'system_version': SYSTEM_VERSION,
        'research_context': True,
        'requested_count': len(unique_paths),
        'worker_count': worker_count,
        'processed_count': sum(row.get('state') == 'PROCESSED' for row in items),
        'skipped_duplicate_count': sum(str(row.get('state')).startswith('SKIPPED_DUPLICATE') for row in items),
        'failed_count': sum(str(row.get('state')).startswith('FAILED') for row in items),
        'stored_run_count': store.count(),
        'items': items,
    }


def cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Coursework basketball analytics for archived parser snapshots')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='Analyze one archived snapshot')
    run.add_argument('--input', required=True)
    run.add_argument('--output', required=True)
    run.add_argument('--db', default='coursework_analytics.sqlite3')
    batch = sub.add_parser('batch', help='Analyze multiple archived snapshots concurrently')
    batch.add_argument('--inputs', nargs='*', default=[])
    batch.add_argument('--input-dir')
    batch.add_argument('--outbox', required=True)
    batch.add_argument('--db', default='coursework_analytics.sqlite3')
    batch.add_argument('--workers', type=int, default=4)
    check = sub.add_parser('check', help='Validate local runtime')
    check.add_argument('--db', default='coursework_analytics.sqlite3')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            store = AcademicStore(args.db)
            source = load_json(args.input)
            canonical = canonical_snapshot(source)
            input_hash = academic_input_hash(source)
            identity = run_key(canonical['match_id'], canonical['checkpoint'], input_hash)
            if store.contains(identity):
                summary = {'state': 'SKIPPED_DUPLICATE_PROCESSED', 'run_key': identity}
            else:
                model = process_file(args.input, args.output, store=store)[OUTPUT_KEY]
                summary = {
                    'state': 'PROCESSED', 'run_key': model['run_key'],
                    'checkpoint': model['checkpoint'], 'data_readiness': model['data_readiness'],
                    'integrity_passed': model['integrity_report']['all_passed'],
                }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.command == 'batch':
            paths = list(args.inputs)
            if args.input_dir:
                paths.extend(sorted(Path(args.input_dir).expanduser().resolve().glob('*.json')))
            if not paths:
                raise ValueError('batch requires --inputs or --input-dir')
            print(json.dumps(process_batch(paths, args.outbox, db_path=args.db, workers=args.workers), ensure_ascii=False, indent=2))
        else:
            store = AcademicStore(args.db)
            print(json.dumps({
                'system_version': SYSTEM_VERSION,
                'database_ready': True,
                'stored_run_count': store.count(),
                'external_delivery_enabled': False,
            }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
