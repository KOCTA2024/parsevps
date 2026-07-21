#!/usr/bin/env python3
"""SUPER_BASKET VPS SYSTEM v5.0 - single-file edition.

PROGRAMMER INTEGRATION:
    python super_basket_vps_system.py run --match /path/to/match.json
    python super_basket_vps_system.py watch --inbox /srv/basket/inbox --outbox /srv/basket/outbox

The existing parser remains the source of match/history/line data.  This file:
1) calculates P_hist -> P_scenario -> P_live -> P_raw -> P_final;
2) applies stat/conflict/router/Team-IT/Q4 gates;
3) lets GPT audit (never recalculate or upgrade) a RISK/PLAY signal;
4) sends approved RISK/PLAY signals to Telegram;
5) stores signals/outcomes in SQLite and activates conservative calibration
   only after a sufficient number of settled predictions.

Required only for GPT review:  pip install -U openai
Everything else uses Python's standard library.
"""
from __future__ import annotations

import argparse
import html
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional


# ===== schema_adapter.py =====
def to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.')
    if not text:
        return None
    if text.endswith('%'):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None

def to_int(value: Any) -> Optional[int]:
    number = to_number(value)
    return None if number is None else int(round(number))

def first(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ''):
            return mapping[name]
    return None

def alias_value(mapping: dict[str, Any], canonical: str, aliases: dict[str, list[str]]) -> Any:
    return first(mapping, aliases.get(canonical, [canonical]))

def percentile(values: list[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

def clock_to_seconds(clock: Any) -> Optional[int]:
    if clock is None:
        return None
    if isinstance(clock, (int, float)):
        return max(0, int(round(float(clock))))
    text = str(clock).strip()
    match = re.fullmatch('(\\d{1,2}):(\\d{2})', text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    number = to_number(text)
    if number is not None:
        return max(0, int(round(number)))
    return None

def _format_info(mapping: dict[str, Any], tournament: str, config: dict[str, Any]) -> dict[str, Any]:
    """Resolve game duration with explicit JSON taking priority over name heuristics."""
    settings = config['match_format']
    rules = mapping.get('rules', {}) if isinstance(mapping.get('rules'), dict) else {}
    explicit_quarter = to_int(first(rules, ['quarter_minutes', 'period_minutes']))
    if explicit_quarter is None:
        explicit_quarter = to_int(first(mapping, ['quarter_minutes', 'period_minutes', 'q_duration_min']))
    quarters = to_int(first(rules, ['quarters', 'regulation_quarters']))
    if quarters is None:
        quarters = to_int(first(mapping, ['quarters_count', 'regulation_quarters']))
    quarters = quarters or int(settings.get('default_quarters', 4))
    source = 'explicit_json' if explicit_quarter else 'tournament_fallback'
    quarter_minutes = explicit_quarter
    if quarter_minutes is None:
        for pattern in settings.get('ten_minute_league_patterns', []):
            if re.search(pattern, tournament or '', flags=re.IGNORECASE):
                quarter_minutes = 10
                break
    if quarter_minutes is None:
        for pattern in settings.get('twelve_minute_league_patterns', []):
            if re.search(pattern, tournament or '', flags=re.IGNORECASE):
                quarter_minutes = 12
                break
    if quarter_minutes is None:
        quarter_minutes = int(settings.get('default_quarter_minutes', 10))
        source = 'default_fallback'
    regulation_minutes = to_int(first(rules, ['regulation_minutes']))
    if regulation_minutes is None:
        regulation_minutes = to_int(first(mapping, ['regulation_minutes']))
    regulation_minutes = regulation_minutes or quarters * quarter_minutes
    competition_type = str(
        first(mapping, ['competition_type', 'season_type', 'league_type'])
        or first(rules, ['competition_type'])
        or ''
    ).strip()
    format_key = str(first(rules, ['format_key']) or first(mapping, ['format_key']) or '').strip()
    if not format_key:
        format_key = f'{competition_type or "GENERIC"}_{quarters}x{quarter_minutes}'
    warnings: list[str] = []
    if source != 'explicit_json':
        warnings.append('QUARTER_MINUTES_NOT_EXPLICIT')
    if regulation_minutes != quarters * quarter_minutes:
        warnings.append('REGULATION_DURATION_INCONSISTENT')
    return {
        'quarters': quarters,
        'quarter_minutes': quarter_minutes,
        'regulation_minutes': regulation_minutes,
        'overtime_minutes': to_int(first(rules, ['overtime_minutes'])) or 5,
        'competition_type': competition_type,
        'format_key': format_key,
        'source': source,
        'warnings': warnings,
    }

def _stage(elapsed_seconds: int, full_seconds: int, quarter_seconds: int, explicit: str) -> str:
    if elapsed_seconds <= 0:
        return 'PRE_MATCH'
    half = full_seconds // 2
    after_three = quarter_seconds * 3
    if elapsed_seconds == half:
        return 'HT'
    if elapsed_seconds == after_three:
        return 'AFTER_3Q'
    if elapsed_seconds >= after_three:
        return 'Q4_CONFIRMATION'
    if elapsed_seconds < half:
        return 'EARLY_LIVE'
    if 'HT' in (explicit or '').upper():
        return 'HT'
    return 'CURRENT_Q1_Q3'

_STATUS_QUARTER_RE = re.compile("\\((\\d+)[^\\d)]*?чверть(?:\\s*(\\d+)')?\\s*\\)", re.IGNORECASE)
_STATUS_BREAK_RE = re.compile('після\\s*Q(\\d+)', re.IGNORECASE)
_STATUS_FINISHED_RE = re.compile('\\bFT\\b|FINAL|FINISHED|ENDED|ЗАВЕРШЕНО|КІНЕЦЬ', re.IGNORECASE)

def _parse_status_clock(status: str, quarter_seconds: int, full_seconds: int) -> Optional[tuple[int, Optional[int], Optional[int]]]:
    """Best-effort parser for the raw provider 'st' status string, used only as a fallback
    when the payload has no numeric match_minute_played/period fields (this feed's 'match'
    block is empty and only raw_data.main_match.st carries live-time info), e.g.:
      "Live (2-а чверть 1')"  -> mid-quarter: quarter=2, minute=1
      "Live (4-а чверть)"     -> quarter just started: quarter=4, minute=0
      "Перерва (після Q2)"    -> half-time break: exactly 2 quarters elapsed
      "Finished"              -> full game elapsed
    Returns (elapsed_seconds, period, period_played_seconds) or None if unrecognised.
    """
    text = (status or '').strip()
    if not text:
        return None
    upper = text.upper()
    if upper.startswith('LIVE'):
        match = _STATUS_QUARTER_RE.search(text)
        if match:
            period = int(match.group(1))
            played_minutes = int(match.group(2)) if match.group(2) else 0
            played_seconds = min(quarter_seconds, played_minutes * 60)
            elapsed_seconds = min(full_seconds, (period - 1) * quarter_seconds + played_seconds)
            return (elapsed_seconds, period, played_seconds)
        # "Live" but sub-stage text not recognised (e.g. overtime) - don't fall back to
        # PRE_MATCH; conservatively treat as deep in the 4th quarter.
        return (max(0, full_seconds - 1), None, None)
    match = _STATUS_BREAK_RE.search(text)
    if match:
        completed = int(match.group(1))
        return (min(full_seconds, completed * quarter_seconds), None, None)
    if _STATUS_FINISHED_RE.search(upper):
        return (full_seconds, None, None)
    return None

def _game_key(row: dict[str, Any]) -> str:
    match_id = str(first(row, ['mid', 'match_id', 'id']) or '').strip()
    if match_id:
        return 'id:' + match_id
    parts = [row.get('dt'), row.get('ht'), row.get('at'), row.get('hs'), row.get('as_')]
    return 'fallback:' + '|'.join((str(part or '') for part in parts))

def _technical(row: dict[str, Any]) -> bool:
    home = to_int(first(row, ['hs', 'home_score', 'homeScore']))
    away = to_int(first(row, ['as_', 'away_score', 'awayScore']))
    return (home, away) in {(20, 0), (0, 20)}

def _team_side(row: dict[str, Any], team: str) -> Optional[str]:
    if str(first(row, ['ht', 'home_team', 'homeTeam']) or '').strip() == team:
        return 'home'
    if str(first(row, ['at', 'away_team', 'awayTeam']) or '').strip() == team:
        return 'away'
    return None

def _raw_stat(row: dict[str, Any], side: str, metric: str, quarter: Optional[int]=None) -> Optional[float]:
    prefix = 'h' if side == 'home' else 'a'
    codes = {'FGA': 'fga', 'FGM': 'fgm', '2PA': '2pa', '2PM': '2pm', '3PA': '3pa', '3PM': '3pm', 'FTA': 'fta', 'FTM': 'ftm', 'ORB': 'orb', 'DRB': 'drb', 'TO': 'tov', 'FOULS': 'fls'}
    suffix = str(quarter) if quarter else 'm'
    value = row.get(f'{prefix}{codes[metric]}{suffix}')
    return to_number(value)

def canonical_game(row: dict[str, Any], perspective_team: Optional[str]=None, config: Optional[dict[str, Any]]=None) -> dict[str, Any]:
    home_team = str(first(row, ['ht', 'home_team', 'homeTeam']) or '')
    away_team = str(first(row, ['at', 'away_team', 'awayTeam']) or '')
    home_score = to_number(first(row, ['hs', 'home_score', 'homeScore']))
    away_score = to_number(first(row, ['as_', 'away_score', 'awayScore']))
    quarters: list[dict[str, Optional[float]]] = []
    for number in range(1, 5):
        home = to_number(first(row, [f'q{number}h', f'home_q{number}']))
        away = to_number(first(row, [f'q{number}a', f'away_q{number}']))
        total = to_number(first(row, [f'q{number}t', f'q{number}_total']))
        if total is None and home is not None and (away is not None):
            total = home + away
        quarters.append({'home': home, 'away': away, 'total': total})
    total = to_number(first(row, ['tot', 'total', 'match_total']))
    if total is None and home_score is not None and (away_score is not None):
        total = home_score + away_score
    side = _team_side(row, perspective_team) if perspective_team else None
    team_score = home_score if side == 'home' else away_score if side == 'away' else None
    opponent_score = away_score if side == 'home' else home_score if side == 'away' else None
    team_quarters = [quarter.get(side) if side else None for quarter in quarters]
    opponent_side = 'away' if side == 'home' else 'home' if side == 'away' else None
    opponent_quarters = [quarter.get(opponent_side) if opponent_side else None for quarter in quarters]
    stats: dict[str, dict[str, Optional[float]]] = {'home': {}, 'away': {}}
    quarter_stats: dict[str, list[dict[str, Optional[float]]]] = {'home': [], 'away': []}
    for game_side in ('home', 'away'):
        for metric in ('FGA', 'FGM', '2PA', '2PM', '3PA', '3PM', 'FTA', 'FTM', 'ORB', 'DRB', 'TO', 'FOULS'):
            stats[game_side][metric] = _raw_stat(row, game_side, metric)
        for number in range(1, 5):
            quarter_stats[game_side].append({metric: _raw_stat(row, game_side, metric, number) for metric in ('FGA', 'FGM', '2PA', '2PM', '3PA', '3PM', 'FTA', 'FTM', 'ORB', 'DRB', 'TO', 'FOULS')})
    game_format = _format_info(row, str(first(row, ['tour', 'tournament']) or ''), config) if config else {
        'quarters': 4,
        'quarter_minutes': None,
        'regulation_minutes': None,
        'competition_type': '',
        'format_key': 'UNKNOWN',
        'source': 'unavailable',
        'warnings': ['FORMAT_NOT_RESOLVED'],
    }
    return {'id': str(first(row, ['mid', 'match_id', 'id']) or ''), 'date': first(row, ['dt', 'date', 'start_time']), 'status': first(row, ['st', 'status']), 'tournament': first(row, ['tour', 'tournament']), 'home_team': home_team, 'away_team': away_team, 'home_score': home_score, 'away_score': away_score, 'total': total, 'quarters': quarters, 'h1_total': sum((q['total'] for q in quarters[:2] if q['total'] is not None)) if all((q['total'] is not None for q in quarters[:2])) else None, 'h2_total': sum((q['total'] for q in quarters[2:] if q['total'] is not None)) if all((q['total'] is not None for q in quarters[2:])) else None, 'perspective_team': perspective_team, 'perspective_side': side, 'team_score': team_score, 'opponent_score': opponent_score, 'team_quarters': team_quarters, 'opponent_quarters': opponent_quarters, 'stats': stats, 'quarter_stats': quarter_stats, 'format': game_format, 'raw': row}

def _filter_history(rows: list[dict[str, Any]], current_id: str, team: Optional[str], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    counters = {'current': 0, 'technical': 0, 'duplicate': 0}
    for row in rows or []:
        row_id = str(first(row, ['mid', 'match_id', 'id']) or '')
        if current_id and row_id == current_id:
            counters['current'] += 1
            continue
        if _technical(row):
            counters['technical'] += 1
            continue
        key = _game_key(row)
        if key in seen:
            counters['duplicate'] += 1
            continue
        seen.add(key)
        output.append(canonical_game(row, team, config))
    return (output, counters)

def _apply_history_format_override(games: list[dict[str, Any]], override: dict[str, Any]) -> int:
    """Temporary fallback for legacy parsers; explicit per-game metadata remains preferred."""
    last_n = to_int(override.get('last_n_games')) or 0
    last_format = str(override.get('last_n_format') or '').strip()
    remaining_format = str(override.get('remaining_format') or '').strip()
    if last_n <= 0 or not last_format:
        return 0
    changed = 0
    newest_first = str(override.get('history_order') or 'newest_first').lower() != 'oldest_first'
    selected = set(range(min(last_n, len(games)))) if newest_first else set(range(max(0, len(games) - last_n), len(games)))
    for index, game in enumerate(games):
        key = last_format if index in selected else remaining_format
        if not key:
            continue
        match = re.search(r'(\d+)x(10|12)', key, flags=re.IGNORECASE)
        if not match:
            continue
        quarters, quarter_minutes = int(match.group(1)), int(match.group(2))
        game['format'] = {
            'quarters': quarters,
            'quarter_minutes': quarter_minutes,
            'regulation_minutes': quarters * quarter_minutes,
            'competition_type': key.rsplit('_', 1)[0],
            'format_key': key,
            'source': 'history_format_override',
            'warnings': ['LEGACY_FORMAT_OVERRIDE_USED'],
        }
        changed += 1
    return changed

def _live_stats(source: dict[str, Any], raw_main: dict[str, Any], side: str, aliases: dict[str, list[str]], actual_points: float) -> tuple[dict[str, Optional[float]], list[str]]:
    live = source.get('live_team_stats', {}).get(side, {}) or {}
    box_key = 'team_a_1h' if side == 'home' else 'team_b_1h'
    box = source.get('live_boxscore', {}).get(box_key, {}) or {}
    prefix = 'h' if side == 'home' else 'a'
    raw_codes = {'FGA': 'fgam', 'FGM': 'fgmm', '2PA': '2pam', '2PM': '2pmm', '3PA': '3pam', '3PM': '3pmm', 'FTA': 'ftam', 'FTM': 'ftmm', 'ORB': 'orbm', 'DRB': 'drbm', 'TO': 'tovm', 'FOULS': 'flsm'}
    result: dict[str, Optional[float]] = {'POINTS': actual_points}
    missing: list[str] = []
    for metric in ('FGA', 'FGM', '2PA', '2PM', '3PA', '3PM', 'FTA', 'FTM', 'ORB', 'DRB', 'TO', 'FOULS'):
        value = alias_value(live, metric, aliases)
        if value in (None, ''):
            value = alias_value(box, metric, aliases)
        if value in (None, ''):
            value = raw_main.get(prefix + raw_codes[metric])
        result[metric] = to_number(value)
        if result[metric] is None:
            missing.append(f'live_stats.{side}.{metric}')
    fga, fgm, three_made, fta, orb, turnovers = (result.get(key) for key in ('FGA', 'FGM', '3PM', 'FTA', 'ORB', 'TO'))
    result['Poss'] = fga - orb + turnovers + 0.44 * fta if None not in (fga, orb, turnovers, fta) else None
    result['eFG'] = (fgm + 0.5 * three_made) / fga if fga and fgm is not None and (three_made is not None) else None
    result['FTr'] = fta / fga if fga and fta is not None else None
    result['OffRtg'] = actual_points / result['Poss'] * 100 if result.get('Poss') else None
    result['TO_rate'] = turnovers / result['Poss'] if result.get('Poss') and turnovers is not None else None
    result['ORB_per_possession'] = orb / result['Poss'] if result.get('Poss') and orb is not None else None
    result['FTA_per_possession'] = fta / result['Poss'] if result.get('Poss') and fta is not None else None
    return (result, missing)

def adapt_match(source: dict[str, Any], config: dict[str, Any], strict: bool=False) -> dict[str, Any]:
    match = source.get('match', {}) or {}
    raw_data = source.get('raw_data', {}) or {}
    raw_main = raw_data.get('main_match', {}) or {}
    current_id = str(first(match, ['id', 'match_id']) or first(raw_main, ['mid', 'id']) or '')
    home_team = str(first(raw_main, ['ht', 'home_team']) or first(match, ['home_team', 'home']) or '')
    away_team = str(first(raw_main, ['at', 'away_team']) or first(match, ['away_team', 'away']) or '')
    if not home_team or not away_team:
        name = str(match.get('name') or '')
        split = re.split('\\s+vs\\s+', name, maxsplit=1, flags=re.IGNORECASE)
        if len(split) == 2:
            home_team, away_team = (home_team or split[0], away_team or split[1])
    tournament = str(first(match, ['tournament', 'league']) or first(raw_main, ['tour']) or '')
    format_mapping = deepcopy(match)
    if isinstance(source.get('rules'), dict) and not isinstance(format_mapping.get('rules'), dict):
        format_mapping['rules'] = deepcopy(source['rules'])
    match_format = _format_info(format_mapping, tournament, config)
    quarter_minutes = int(match_format['quarter_minutes'])
    quarter_seconds = quarter_minutes * 60
    full_seconds = int(match_format['regulation_minutes']) * 60
    home_score = to_number(match.get('score', {}).get('home'))
    away_score = to_number(match.get('score', {}).get('away'))
    if home_score is None:
        home_score = to_number(first(raw_main, ['hs', 'home_score'])) or 0.0
    if away_score is None:
        away_score = to_number(first(raw_main, ['as_', 'away_score'])) or 0.0
    explicit_stage = str(first(match, ['stage', 'status']) or first(raw_main, ['st']) or '')
    analysis_context = source.get('analysis_context', {}) if isinstance(source.get('analysis_context'), dict) else {}
    trigger_checkpoint = to_int(first(analysis_context, ['trigger_checkpoint', 'checkpoint']))
    elapsed_raw = first(match, ['match_minute_played', 'elapsed_minutes'])
    period_raw = first(match, ['period', 'quarter', 'current_quarter'])
    period_played_raw = first(match, ['period_minute_played', 'quarter_minute_played'])
    period_left_raw = first(match, ['period_minute_left', 'quarter_minute_left'])
    elapsed_minutes = to_number(elapsed_raw)
    period = to_int(period_raw)
    period_played = to_number(period_played_raw)
    period_left = to_number(period_left_raw)
    time_reliable = elapsed_raw not in (None, '') or (period is not None and (period_played_raw not in (None, '') or period_left_raw not in (None, '')))
    if elapsed_minutes is None and period and (period_played is not None):
        elapsed_minutes = (period - 1) * quarter_minutes + period_played
    if elapsed_minutes is None and period is None:
        # No numeric time fields in the payload at all (e.g. 'match' block empty) - fall
        # back to parsing the provider's textual status ("Live (N-а чверть M')",
        # "Перерва (після QN)", "Finished") so the match isn't misclassified as PRE_MATCH.
        status_clock = _parse_status_clock(explicit_stage, quarter_seconds, full_seconds)
        if status_clock is not None:
            status_elapsed_seconds, status_period, status_period_played_seconds = status_clock
            elapsed_minutes = status_elapsed_seconds / 60.0
            period = status_period
            if status_period_played_seconds is not None:
                period_played = status_period_played_seconds / 60.0
            time_reliable = True
    if elapsed_minutes is None:
        elapsed_minutes = 0.0
    elapsed_seconds = max(0, min(full_seconds, int(round(elapsed_minutes * 60))))
    if period is None and elapsed_seconds < full_seconds:
        period = min(4, elapsed_seconds // quarter_seconds + 1)
    if period_left is None and period:
        period_elapsed_seconds = max(0, elapsed_seconds - (period - 1) * quarter_seconds)
        period_left_seconds = max(0, quarter_seconds - period_elapsed_seconds)
    else:
        period_left_seconds = int(round((period_left or 0) * 60))
    stage = _stage(elapsed_seconds, full_seconds, quarter_seconds, explicit_stage)
    raw_game = canonical_game(raw_main, config=config)
    quarters: list[dict[str, Optional[float]]] = []
    match_quarters = match.get('quarters', {}) or {}
    for number in range(1, 5):
        q_source = match_quarters.get(f'q{number}') or match_quarters.get(f'q{number}_live') or {}
        raw_q = raw_game['quarters'][number - 1]
        q_home = to_number(q_source.get('home'))
        q_away = to_number(q_source.get('away'))
        if q_home is None:
            q_home = raw_q.get('home')
        if q_away is None:
            q_away = raw_q.get('away')
        q_total = q_home + q_away if q_home is not None and q_away is not None else raw_q.get('total')
        quarters.append({'home': q_home, 'away': q_away, 'total': q_total})
    team_a_history, count_a = _filter_history(raw_data.get('team_a_hist', []), current_id, home_team, config)
    team_b_history, count_b = _filter_history(raw_data.get('team_b_hist', []), current_id, away_team, config)
    h2h_history, count_h2h = _filter_history(raw_data.get('h2h_hist', []), current_id, None, config)
    format_override = source.get('history_format_override', {}) if isinstance(source.get('history_format_override'), dict) else {}
    override_count = sum(
        _apply_history_format_override(pool, format_override)
        for pool in (team_a_history, team_b_history, h2h_history)
    )
    current_regulation = int(match_format['regulation_minutes'])
    def split_format(pool: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        same: list[dict[str, Any]] = []
        cross: list[dict[str, Any]] = []
        for game in pool:
            game_regulation = to_int((game.get('format') or {}).get('regulation_minutes'))
            if game_regulation == current_regulation:
                same.append(game)
            else:
                cross.append(game)
        return same, cross
    team_a_same, team_a_cross = split_format(team_a_history)
    team_b_same, team_b_cross = split_format(team_b_history)
    h2h_same, h2h_cross = split_format(h2h_history)
    aliases = config.get('aliases', {})
    home_stats, missing_home = _live_stats(source, raw_main, 'home', aliases, home_score)
    away_stats, missing_away = _live_stats(source, raw_main, 'away', aliases, away_score)
    core = ('FGA', 'FTA', 'ORB', 'TO', 'Poss', 'eFG')
    found_home = sum((home_stats.get(metric) is not None for metric in core))
    found_away = sum((away_stats.get(metric) is not None for metric in core))
    if found_home == len(core) and found_away == len(core):
        stat_support = 'ON'
    elif found_home >= 3 and found_away >= 3:
        stat_support = 'LIMITED'
    else:
        stat_support = 'OFF'
    schema_errors: list[str] = []
    if not current_id:
        schema_errors.append('match.id')
    if not home_team:
        schema_errors.append('match.home_team/raw_data.main_match.ht')
    if not away_team:
        schema_errors.append('match.away_team/raw_data.main_match.at')
    explicit_upper = explicit_stage.upper()
    explicit_live_hint = any(token in explicit_upper for token in ('LIVE', 'Q1', 'Q2', 'Q3', 'Q4', 'ЧВЕРТ', 'QUARTER'))
    if explicit_live_hint and not time_reliable:
        schema_errors.append('match.live_time')
    if strict and schema_errors:
        raise ValueError('Schema errors: ' + ', '.join(schema_errors))
    exclusions = {'current': count_a['current'] + count_b['current'] + count_h2h['current'], 'technical': count_a['technical'] + count_b['technical'] + count_h2h['technical'], 'duplicate': count_a['duplicate'] + count_b['duplicate'] + count_h2h['duplicate']}
    parser_blocks = {
        key: deepcopy(source.get(key))
        for key in (
            'history_by_exact_line',
            'scenario_patterns_by_line',
            'checkpoint_matrices',
            'stat_conditioned_line_profiles',
            'quarter_result_profile',
            'stat_alignment',
            'history_zones',
            'stat_zones',
            'projections',
        )
        if source.get(key) is not None
    }
    return {
        'match_id': current_id,
        'name': match.get('name') or f'{home_team} vs {away_team}',
        'home_team': home_team,
        'away_team': away_team,
        'tournament': tournament,
        'explicit_stage': explicit_stage,
        'stage': stage,
        'trigger_checkpoint': trigger_checkpoint,
        'current_quarter': period,
        'quarter_minutes': quarter_minutes,
        'quarter_seconds': quarter_seconds,
        'full_game_seconds': full_seconds,
        'elapsed_game_seconds': elapsed_seconds,
        'remaining_game_seconds': full_seconds - elapsed_seconds,
        'quarter_seconds_remaining': period_left_seconds,
        'clock': f'{period_left_seconds // 60:02d}:{period_left_seconds % 60:02d}' if period is not None else None,
        'score': {
            'home': home_score,
            'away': away_score,
            'total': home_score + away_score,
            'margin_home': home_score - away_score,
        },
        'quarters': quarters,
        'series_context': deepcopy(match.get('series_context', {})),
        'format': match_format,
        'live_stats': {'home': home_stats, 'away': away_stats},
        'stat_support': stat_support,
        'history': {'team_a': team_a_same, 'team_b': team_b_same, 'h2h': h2h_same},
        'history_cross_format': {'team_a': team_a_cross, 'team_b': team_b_cross, 'h2h': h2h_cross},
        'raw_main': raw_main,
        'parser_blocks': parser_blocks,
        'data_gate': {
            'history_team_a_n': len(team_a_same),
            'history_team_b_n': len(team_b_same),
            'pooled_n': len(team_a_same) + len(team_b_same),
            'h2h_n': len(h2h_same),
            'cross_format_team_a_n': len(team_a_cross),
            'cross_format_team_b_n': len(team_b_cross),
            'cross_format_h2h_n': len(h2h_cross),
            'cross_format_exact_hits_used': False,
            'cross_format_normalized_baseline_allowed': bool(team_a_cross or team_b_cross),
            'history_format_override_games': override_count,
            'current_match_excluded': True,
            'current_games_excluded': exclusions['current'],
            'technical_games_excluded': exclusions['technical'],
            'duplicate_games_excluded': exclusions['duplicate'],
            'stats_found': stat_support != 'OFF',
            'stat_support': stat_support,
            'missing_fields': sorted(set(missing_home + missing_away)),
            'schema_errors': schema_errors,
            'time_reliable': time_reliable,
        },
    }

# ===== market_parser.py =====
SUPPORTED_BUCKETS = {'match_total', 'half_total', 'quarter_total', 'team_it', 'home_ind_total', 'away_ind_total'}

def _scope_text(row: dict[str, Any]) -> str:
    return str(row.get('scope') or row.get('segment') or row.get('period') or '').upper().replace(' ', '')

def _team_from_row(bucket: str, row: dict[str, Any], canonical: dict[str, Any]) -> Optional[str]:
    if bucket == 'home_ind_total':
        return canonical['home_team']
    if bucket == 'away_ind_total':
        return canonical['away_team']
    raw = row.get('team') or row.get('team_name') or row.get('participant')
    if raw in ('home', 'HOME', 'team_a', 'A'):
        return canonical['home_team']
    if raw in ('away', 'AWAY', 'team_b', 'B'):
        return canonical['away_team']
    return str(raw) if raw else None

def _market_type(bucket: str, scope: str, team: Optional[str]) -> tuple[Optional[str], str]:
    is_team = bucket in {'team_it', 'home_ind_total', 'away_ind_total'} or team is not None
    if bucket == 'match_total' and (not is_team):
        return ('MATCH_TOTAL', 'MATCH')
    if bucket == 'half_total' and (not is_team):
        if scope.startswith('H1') or scope in {'1H', 'FIRSTHALF'}:
            return ('H1_TOTAL', 'H1')
        if scope.startswith('H2') or scope in {'2H', 'SECONDHALF'}:
            return ('H2_TOTAL', 'H2')
    if bucket == 'quarter_total' and (not is_team):
        quarter = next((q for q in ('Q1', 'Q2', 'Q3', 'Q4') if q in scope), scope)
        return ('CURRENT_QUARTER_TOTAL', quarter)
    if is_team:
        if scope.startswith('H1') or scope in {'1H', 'FIRSTHALF'}:
            return ('TEAM_IT_H1', 'H1')
        if scope.startswith('H2') or scope in {'2H', 'SECONDHALF'}:
            return ('TEAM_IT_H2', 'H2')
        quarter = next((q for q in ('Q1', 'Q2', 'Q3', 'Q4') if q in scope), None)
        if quarter:
            return ('CURRENT_QUARTER_TEAM_IT', quarter)
        return ('TEAM_IT_MATCH', 'MATCH')
    return (None, scope or 'UNKNOWN')

def _current_quarter_issue(market_type: str, segment: str, canonical: dict[str, Any]) -> Optional[str]:
    if market_type not in {'CURRENT_QUARTER_TOTAL', 'CURRENT_QUARTER_TEAM_IT'}:
        return None
    current = canonical.get('current_quarter')
    target = int(segment[1:]) if segment.startswith('Q') and segment[1:].isdigit() else None
    if target is None:
        return 'UNKNOWN_QUARTER'
    if current is None:
        return 'NO_CURRENT_QUARTER'
    if target > current:
        return 'FUTURE_QUARTER'
    if target < current:
        return 'PAST_QUARTER'
    if canonical.get('clock') is None:
        return 'NO_EXACT_CURRENT_QUARTER_TIME'
    quarter_score = canonical.get('quarters', [])[target - 1]
    if quarter_score.get('total') is None:
        return 'NO_CURRENT_QUARTER_SCORE'
    return None

def parse_markets(source: dict[str, Any], canonical: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # math_script.py (the actual upstream producer) writes the enriched bookmaker
    # lines dict under the top-level key 'lines' (see its "lines": bookmaker_lines
    # assembly) - not 'bookmaker_lines'/'bookmaker_markets'/'markets'. Keep those
    # older names as fallbacks too in case other producers use them.
    containers = source.get('lines') or source.get('bookmaker_lines') or source.get('bookmaker_markets') or source.get('markets') or {}
    aliases = config.get('aliases', {})
    odds_min = float(config.get('odds_min', 1.44))
    odds_max = float(config.get('odds_max', 10.0))
    evaluations: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    sequence = 0
    for bucket, rows in containers.items():
        if not isinstance(rows, list):
            continue
        if bucket not in SUPPORTED_BUCKETS:
            for row in rows:
                audit.append({'bucket': bucket, 'supported': False, 'reason': 'UNSUPPORTED_MARKET', 'raw': row})
            continue
        for row in rows:
            if not isinstance(row, dict):
                audit.append({'bucket': bucket, 'supported': False, 'reason': 'INVALID_MARKET_ROW', 'raw': row})
                continue
            scope = _scope_text(row)
            team = _team_from_row(bucket, row, canonical)
            market_type, segment = _market_type(bucket, scope, team)
            line = to_number(alias_value(row, 'LINE', aliases))
            real_line = bool(row.get('is_real_bookmaker_line', True))
            bookmaker = str(row.get('bookmaker') or row.get('source') or 'unknown')
            current_issue = _current_quarter_issue(market_type or '', segment, canonical)
            base_reasons: list[str] = []
            if market_type is None:
                base_reasons.append('UNSUPPORTED_MARKET')
            if line is None:
                base_reasons.append('NO_LINE')
            if not real_line:
                base_reasons.append('SYNTHETIC_LINE')
            if current_issue:
                base_reasons.append(current_issue)
            over_odds = to_number(alias_value(row, 'OVER_ODDS', aliases))
            under_odds = to_number(alias_value(row, 'UNDER_ODDS', aliases))
            audit_row = {'source_id': row.get('id'), 'bucket': bucket, 'market_type': market_type, 'team': team, 'segment': segment, 'line': line, 'over_odds': over_odds, 'under_odds': under_odds, 'bookmaker': bookmaker, 'real_line': real_line, 'issues': list(base_reasons)}
            if over_odds is None:
                audit_row['issues'].append('NO_OVER_ODDS')
            if under_odds is None:
                audit_row['issues'].append('NO_UNDER_ODDS')
            audit.append(audit_row)
            for side, odds in (('OVER', over_odds), ('UNDER', under_odds)):
                reasons = list(base_reasons)
                if odds is None:
                    reasons.append('NO_ODDS')
                if odds is not None and odds < odds_min:
                    reasons.append('ODDS_BELOW_MINIMUM')
                if odds is not None and odds > odds_max:
                    reasons.append('ODDS_ABOVE_MAXIMUM')
                sequence += 1
                safe_line = 'na' if line is None else str(line).replace('.', '_')
                market_id = str(row.get('id') or f'{bucket}_{segment}_{safe_line}_{sequence}')
                evaluations.append({'market_id': f'{market_id}_{side.lower()}_{sequence}', 'source_market_id': row.get('id'), 'market_type': market_type or 'UNSUPPORTED', 'team': team, 'segment': segment, 'side': side, 'line': line, 'odds': odds, 'bookmaker': bookmaker, 'source_bucket': bucket, 'parser_issues': reasons, 'eligible_market': not reasons})
    return (evaluations, audit)

# ===== history_engine.py =====
def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

def settle(result: float, line: float, side: str) -> str:
    if result == line:
        return 'push'
    won = result > line if side.upper() == 'OVER' else result < line
    return 'win' if won else 'loss'

def smoothed_probability(wins: int, valid_n: int, alpha: float=1.0, beta: float=1.0) -> float:
    return (wins + alpha) / (valid_n + alpha + beta)

def _segment_value(game: dict[str, Any], market: dict[str, Any], team_name: Optional[str]=None, opponent_allowed: bool=False) -> Optional[float]:
    market_type = market['market_type']
    segment = market.get('segment', 'MATCH')
    if market_type == 'MATCH_TOTAL':
        return game.get('total')
    if market_type == 'H1_TOTAL':
        return game.get('h1_total')
    if market_type == 'H2_TOTAL':
        return game.get('h2_total')
    if market_type == 'CURRENT_QUARTER_TOTAL':
        if segment.startswith('Q') and segment[1:].isdigit():
            return game['quarters'][int(segment[1:]) - 1].get('total')
        return None
    if market_type.startswith('TEAM_IT') or market_type == 'CURRENT_QUARTER_TEAM_IT':
        if team_name:
            if game.get('home_team') == team_name:
                side = 'home'
            elif game.get('away_team') == team_name:
                side = 'away'
            else:
                return None
            if opponent_allowed:
                side = 'away' if side == 'home' else 'home'
            if segment == 'MATCH':
                return game.get('home_score') if side == 'home' else game.get('away_score')
            if segment == 'H1':
                values = [game['quarters'][i].get(side) for i in (0, 1)]
                return sum(values) if all((value is not None for value in values)) else None
            if segment == 'H2':
                values = [game['quarters'][i].get(side) for i in (2, 3)]
                return sum(values) if all((value is not None for value in values)) else None
            if segment.startswith('Q') and segment[1:].isdigit():
                return game['quarters'][int(segment[1:]) - 1].get(side)
        return None
    return None

def exact_breakdown(values: list[Optional[float]], line: float, side: str, alpha: float, beta: float) -> dict[str, Any]:
    valid = [float(value) for value in values if value is not None]
    results = [settle(value, line, side) for value in valid]
    wins = results.count('win')
    losses = results.count('loss')
    pushes = results.count('push')
    n = len(valid)
    return {'wins': wins, 'losses': losses, 'pushes': pushes, 'n': n, 'valid_n': n, 'raw_pct': wins / n if n else None, 'raw_hit_pct': wins / n if n else None, 'p_smoothed': smoothed_probability(wins, n, alpha, beta) if n else None, 'values': valid}

def _distribution(values: list[float], line: float, side: str, min_normal_n: int, alpha: float, beta: float) -> dict[str, Any]:
    if not values:
        return {'n': 0, 'available': False, 'p_distribution': None}
    exact = exact_breakdown(values, line, side, alpha, beta)
    mean = statistics.fmean(values)
    median = statistics.median(values)
    standard_deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    empirical = exact['p_smoothed']
    normal_probability = None
    if len(values) >= min_normal_n and standard_deviation > 0:
        z = (line - mean) / standard_deviation
        normal_probability = 1.0 - normal_cdf(z) if side == 'OVER' else normal_cdf(z)
    available = [probability for probability in (empirical, normal_probability) if probability is not None]
    probability = sum(available) / len(available)
    return {'n': len(values), 'available': True, 'mean': mean, 'median': median, 'standard_deviation': standard_deviation, 'empirical_percentile_line': sum((value <= line for value in values)) / len(values), 'normal_cdf_probability': normal_probability, 'p_distribution': probability}

def _weighted_available(components: dict[str, Optional[float]], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    active = {key: float(weights[key]) for key, value in components.items() if value is not None and key in weights and (weights[key] > 0)}
    total = sum(active.values())
    if not active or total <= 0:
        return (0.5, {})
    normalized = {key: weight / total for key, weight in active.items()}
    return (sum((float(components[key]) * normalized[key] for key in normalized)), normalized)

def calculate_total_history(market: dict[str, Any], canonical: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    alpha = float(config['smoothing']['alpha'])
    beta = float(config['smoothing']['beta'])
    line = float(market['line'])
    side = market['side']
    team_a_values = [_segment_value(game, market) for game in canonical['history']['team_a']]
    team_b_values = [_segment_value(game, market) for game in canonical['history']['team_b']]
    h2h_values = [_segment_value(game, market) for game in canonical['history']['h2h']]
    team_a = exact_breakdown(team_a_values, line, side, alpha, beta)
    team_b = exact_breakdown(team_b_values, line, side, alpha, beta)
    pooled_values = [value for value in team_a_values + team_b_values if value is not None]
    pooled = exact_breakdown(pooled_values, line, side, alpha, beta)
    h2h = exact_breakdown(h2h_values, line, side, alpha, beta)
    pooled_probability = pooled['p_smoothed'] if pooled['p_smoothed'] is not None else 0.5
    h2h_k = float(config['credibility']['h2h_k'])
    h2h_credibility = h2h['n'] / (h2h['n'] + h2h_k) if h2h['n'] else 0.0
    h2h_probability = h2h['p_smoothed'] if h2h['p_smoothed'] is not None else pooled_probability
    h2h['credibility'] = h2h_credibility
    h2h['p_shrunk'] = h2h_credibility * h2h_probability + (1 - h2h_credibility) * pooled_probability
    last5_values = [value for value in team_a_values[:5] + team_b_values[:5] if value is not None]
    last5 = exact_breakdown(last5_values, line, side, alpha, beta)
    form_k = float(config['credibility']['form_k'])
    form_credibility = last5['n'] / (last5['n'] + form_k) if last5['n'] else 0.0
    form_probability = last5['p_smoothed'] if last5['p_smoothed'] is not None else pooled_probability
    last5['credibility'] = form_credibility
    last5['p_shrunk'] = form_credibility * form_probability + (1 - form_credibility) * pooled_probability
    distribution = _distribution(pooled_values, line, side, int(config['credibility']['normal_min_sample']), alpha, beta)
    components = {'exact': pooled['p_smoothed'], 'form': last5.get('p_shrunk') if last5['n'] else None, 'h2h': h2h.get('p_shrunk') if h2h['n'] else None, 'distribution': distribution.get('p_distribution')}
    p_hist, normalized = _weighted_available(components, config['history_weights'])
    for block in (team_a, team_b, pooled, h2h, last5):
        block.pop('values', None)
    return {'team_a': team_a, 'team_b': team_b, 'pooled': pooled, 'h2h': h2h, 'last5': last5, 'distribution': distribution, 'components': components, 'component_weights': normalized, 'p_hist': p_hist}

def _current_team_score(canonical: dict[str, Any], team: str, segment: str) -> float:
    side = 'home' if team == canonical['home_team'] else 'away'
    if segment == 'MATCH':
        return float(canonical['score'][side])
    if segment == 'H1':
        return sum((float(q.get(side) or 0) for q in canonical['quarters'][:2]))
    if segment == 'H2':
        return sum((float(q.get(side) or 0) for q in canonical['quarters'][2:]))
    if segment.startswith('Q') and segment[1:].isdigit():
        return float(canonical['quarters'][int(segment[1:]) - 1].get(side) or 0)
    return 0.0

def calculate_team_it_history(market: dict[str, Any], canonical: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    alpha = float(config['smoothing']['alpha'])
    beta = float(config['smoothing']['beta'])
    team = market['team']
    opponent = canonical['away_team'] if team == canonical['home_team'] else canonical['home_team']
    own_pool = canonical['history']['team_a'] if team == canonical['home_team'] else canonical['history']['team_b']
    opponent_pool = canonical['history']['team_b'] if team == canonical['home_team'] else canonical['history']['team_a']
    own_values = [_segment_value(game, market, team) for game in own_pool]
    allowed_values = [_segment_value(game, market, opponent, opponent_allowed=True) for game in opponent_pool]
    h2h_values = [_segment_value(game, market, team) for game in canonical['history']['h2h']]
    own = exact_breakdown(own_values, float(market['line']), market['side'], alpha, beta)
    allowed = exact_breakdown(allowed_values, float(market['line']), market['side'], alpha, beta)
    h2h = exact_breakdown(h2h_values, float(market['line']), market['side'], alpha, beta)
    weights = config['team_it']
    components = {'own_scored': own['p_smoothed'], 'opponent_allowed': allowed['p_smoothed'], 'h2h_it': h2h['p_smoothed']}
    configured = {'own_scored': float(weights['own_weight']), 'opponent_allowed': float(weights['opponent_allowed_weight']), 'h2h_it': float(weights['h2h_weight'])}
    p_hist, normalized = _weighted_available(components, configured)
    current = _current_team_score(canonical, team, market.get('segment', 'MATCH'))
    required = max(0.0, float(market['line']) - current)
    if market.get('segment') == 'MATCH':
        remaining_minutes = canonical['remaining_game_seconds'] / 60
    elif market.get('segment') == 'H1':
        remaining_minutes = max(0.0, canonical['full_game_seconds'] / 2 - canonical['elapsed_game_seconds']) / 60
    elif market.get('segment') == 'H2':
        half_start = canonical['full_game_seconds'] / 2
        elapsed_half = max(0.0, canonical['elapsed_game_seconds'] - half_start)
        remaining_minutes = max(0.0, half_start - elapsed_half) / 60
    else:
        remaining_minutes = canonical['quarter_seconds_remaining'] / 60
    required_ppm = required / remaining_minutes if remaining_minutes > 0 else None
    live_side = 'home' if team == canonical['home_team'] else 'away'
    poss = canonical['live_stats'][live_side].get('Poss')
    elapsed_minutes = canonical['elapsed_game_seconds'] / 60
    possessions_per_minute = poss / elapsed_minutes if poss and elapsed_minutes > 0 else None
    remaining_possessions = possessions_per_minute * remaining_minutes if possessions_per_minute else None
    required_ppp = required / remaining_possessions if remaining_possessions and remaining_possessions > 0 else None
    weakest_values = [value for key, value in components.items() if key != 'h2h_it' and value is not None]
    weakest = min(weakest_values) if weakest_values else None
    for block in (own, allowed, h2h):
        block.pop('values', None)
    return {'team_a': own if team == canonical['home_team'] else {}, 'team_b': own if team == canonical['away_team'] else {}, 'pooled': {}, 'h2h': h2h, 'last5': {}, 'distribution': {}, 'own_scored': own, 'opponent_allowed': allowed, 'h2h_it': h2h, 'opponent': opponent, 'components': components, 'component_weights': normalized, 'weakest_gate': weakest, 'required_live': required, 'remaining_minutes': remaining_minutes, 'required_points_per_minute': required_ppm, 'required_points_per_possession': required_ppp, 'p_hist_IT': p_hist, 'p_hist': p_hist}

def calculate_history(market: dict[str, Any], canonical: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if market['market_type'].startswith('TEAM_IT') or market['market_type'] == 'CURRENT_QUARTER_TEAM_IT':
        return calculate_team_it_history(market, canonical, config)
    return calculate_total_history(market, canonical, config)

def segment_value(game: dict[str, Any], market: dict[str, Any], team_name: Optional[str]=None) -> Optional[float]:
    return _segment_value(game, market, team_name)

# ===== scenario_engine.py =====
PatternMatcher = Callable[[dict[str, Any]], bool]

def _team_state(canonical: dict[str, Any], team: str) -> dict[str, Any]:
    side = 'home' if team == canonical['home_team'] else 'away'
    opponent = 'away' if side == 'home' else 'home'
    team_q = [quarter.get(side) for quarter in canonical['quarters']]
    opp_q = [quarter.get(opponent) for quarter in canonical['quarters']]
    elapsed = canonical['elapsed_game_seconds']
    q_seconds = canonical['quarter_seconds']

    # A checkpoint job is usually queued when the provider already shows the first
    # minute of the next quarter (Q2/Q3/Q4). In that snapshot elapsed % q_seconds
    # is no longer zero, so relying only on exact clock boundaries silently disables
    # PATTERN_13..15 after Q1/HT/Q3. The queue source is authoritative here.
    trigger_checkpoint = to_int(canonical.get('trigger_checkpoint'))
    checkpoint_boundary = trigger_checkpoint in (1, 2, 3)
    if checkpoint_boundary:
        completed = int(trigger_checkpoint)
        boundary = True
    else:
        completed = min(4, elapsed // q_seconds)
        boundary = elapsed % q_seconds == 0

    # Do not contaminate an after-quarter scenario with points already scored in
    # the next quarter. When the completed-quarter boxscore is available, rebuild
    # the checkpoint score strictly from Q1..Qn; otherwise preserve the live-score
    # fallback used by the previous implementation.
    checkpoint_team_values = team_q[:int(completed)]
    checkpoint_opp_values = opp_q[:int(completed)]
    checkpoint_boxscore_complete = (
        int(completed) > 0
        and all(value is not None for value in checkpoint_team_values + checkpoint_opp_values)
    )
    if boundary and checkpoint_boxscore_complete:
        score = float(sum(checkpoint_team_values))
        opponent_score = float(sum(checkpoint_opp_values))
        total = score + opponent_score
        checkpoint_score_source = 'COMPLETED_QUARTERS'
    else:
        score = canonical['score'][side]
        opponent_score = canonical['score'][opponent]
        total = canonical['score']['total']
        checkpoint_score_source = 'LIVE_SCORE_FALLBACK'

    return {
        'side': side,
        'team_q': team_q,
        'opp_q': opp_q,
        'completed_quarters': int(completed),
        'at_boundary': boundary,
        'current_quarter': canonical.get('current_quarter'),
        'score': score,
        'opponent_score': opponent_score,
        'total': total,
        'checkpoint_score_source': checkpoint_score_source,
    }

def _game_margin(game: dict[str, Any], after_quarters: int) -> Optional[float]:
    team_values = game['team_quarters'][:after_quarters]
    opponent_values = game['opponent_quarters'][:after_quarters]
    if not all((value is not None for value in team_values + opponent_values)):
        return None
    return sum(team_values) - sum(opponent_values)

def _game_total(game: dict[str, Any], after_quarters: int) -> Optional[float]:
    values = [quarter.get('total') for quarter in game['quarters'][:after_quarters]]
    return sum(values) if all((value is not None for value in values)) else None

def _bucket(value: float, size: float) -> tuple[float, float]:
    low = value // size * size
    return (low, low + size)

def _within_bucket(value: Optional[float], bounds: tuple[float, float]) -> bool:
    return value is not None and bounds[0] <= value < bounds[1]

def _active_patterns(canonical: dict[str, Any], team: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state = _team_state(canonical, team)
    team_q, opp_q = (state['team_q'], state['opp_q'])
    active: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    def add(pattern_id: str, name: str, group: str, condition: bool, matcher: PatternMatcher, reason: str='CURRENT_CONDITION_FALSE') -> None:
        record = {'pattern_id': pattern_id, 'name': name, 'pattern_group': group, 'team': team, 'matcher': matcher}
        if condition:
            active.append(record)
        else:
            rejected.append({key: value for key, value in record.items() if key != 'matcher'} | {'rejection_reason': reason})
    completed_indices = range(state['completed_quarters'])
    won_any = any((team_q[i] is not None and opp_q[i] is not None and (team_q[i] > opp_q[i]) for i in completed_indices))
    scored_21 = any((team_q[i] is not None and team_q[i] >= 21 for i in completed_indices))
    add('PATTERN_01', 'WON_AT_LEAST_ONE_QUARTER', 'quarter_strength', won_any, lambda game: any((a is not None and b is not None and (a > b) for a, b in zip(game['team_quarters'], game['opponent_quarters']))))
    add('PATTERN_02', 'SCORED_21_PLUS_IN_ANY_QUARTER', 'quarter_strength', scored_21, lambda game: any((value is not None and value >= 21 for value in game['team_quarters'])))
    q1_known = team_q[0] is not None and opp_q[0] is not None and (canonical['elapsed_game_seconds'] >= canonical['quarter_seconds'])
    add('PATTERN_03', 'SCORED_18_PLUS_IN_Q1', 'quarter_strength', q1_known and team_q[0] >= 18, lambda game: game['team_quarters'][0] is not None and game['team_quarters'][0] >= 18)
    add('PATTERN_04', 'WON_Q1', 'quarter_result', q1_known and team_q[0] > opp_q[0], lambda game: None not in (game['team_quarters'][0], game['opponent_quarters'][0]) and game['team_quarters'][0] > game['opponent_quarters'][0])
    add('PATTERN_05', 'WON_Q1_AND_SCORED_18_PLUS', 'quarter_result', q1_known and team_q[0] > opp_q[0] and (team_q[0] >= 18), lambda game: None not in (game['team_quarters'][0], game['opponent_quarters'][0]) and game['team_quarters'][0] > game['opponent_quarters'][0] and (game['team_quarters'][0] >= 18))
    add('PATTERN_06', 'LOST_Q1', 'quarter_result', q1_known and team_q[0] < opp_q[0], lambda game: None not in (game['team_quarters'][0], game['opponent_quarters'][0]) and game['team_quarters'][0] < game['opponent_quarters'][0])
    add('PATTERN_07', 'LED_AFTER_Q1', 'score_state', q1_known and team_q[0] > opp_q[0], lambda game: (_game_margin(game, 1) or 0) > 0)
    add('PATTERN_08', 'TRAILED_AFTER_Q1', 'score_state', q1_known and team_q[0] < opp_q[0], lambda game: (_game_margin(game, 1) or 0) < 0)
    ht_known = canonical['elapsed_game_seconds'] >= canonical['quarter_seconds'] * 2 and all((value is not None for value in team_q[:2] + opp_q[:2]))
    current_ht_margin = sum(team_q[:2]) - sum(opp_q[:2]) if ht_known else 0
    add('PATTERN_09', 'LED_AT_HT', 'score_state', ht_known and current_ht_margin > 0, lambda game: (_game_margin(game, 2) or 0) > 0)
    add('PATTERN_10', 'TRAILED_AT_HT', 'score_state', ht_known and current_ht_margin < 0, lambda game: (_game_margin(game, 2) or 0) < 0)
    if q1_known:
        bounds = _bucket(team_q[0] - opp_q[0], float(config['patterns']['margin_bucket_size']))
        add('PATTERN_11', 'Q1_MARGIN_BUCKET', 'margin_state', True, lambda game, b=bounds: _within_bucket(_game_margin(game, 1), b))
    else:
        rejected.append({'pattern_id': 'PATTERN_11', 'name': 'Q1_MARGIN_BUCKET', 'pattern_group': 'margin_state', 'team': team, 'rejection_reason': 'Q1_NOT_COMPLETE'})
    if ht_known:
        bounds = _bucket(current_ht_margin, float(config['patterns']['margin_bucket_size']))
        add('PATTERN_12', 'HT_MARGIN_BUCKET', 'margin_state', True, lambda game, b=bounds: _within_bucket(_game_margin(game, 2), b))
    else:
        rejected.append({'pattern_id': 'PATTERN_12', 'name': 'HT_MARGIN_BUCKET', 'pattern_group': 'margin_state', 'team': team, 'rejection_reason': 'HT_NOT_AVAILABLE'})
    if state['at_boundary'] and state['completed_quarters'] in (1, 2, 3):
        checkpoint = state['completed_quarters']
        margin_bounds = _bucket(state['score'] - state['opponent_score'], float(config['patterns']['margin_bucket_size']))
        total_bounds = _bucket(state['total'], float(config['patterns']['total_bucket_size']))
        score_bounds = _bucket(state['score'], float(config['patterns']['team_score_bucket_size']))
        add('PATTERN_13', 'CURRENT_MARGIN_BUCKET', 'margin_state', True, lambda game, n=checkpoint, b=margin_bounds: _within_bucket(_game_margin(game, n), b))
        add('PATTERN_14', 'CURRENT_TOTAL_BUCKET', 'total_state', True, lambda game, n=checkpoint, b=total_bounds: _within_bucket(_game_total(game, n), b))
        add('PATTERN_15', 'CURRENT_TEAM_SCORE_BUCKET', 'total_state', True, lambda game, n=checkpoint, b=score_bounds: _within_bucket(sum(game['team_quarters'][:n]) if all((value is not None for value in game['team_quarters'][:n])) else None, b))
        add('PATTERN_16', 'SAME_STAGE', 'time_state', True, lambda game: True)
        add('PATTERN_17', 'SAME_QUARTER_NUMBER', 'time_state', True, lambda game: True)
    else:
        for pattern_id, name, group in (('PATTERN_13', 'CURRENT_MARGIN_BUCKET', 'margin_state'), ('PATTERN_14', 'CURRENT_TOTAL_BUCKET', 'total_state'), ('PATTERN_15', 'CURRENT_TEAM_SCORE_BUCKET', 'total_state'), ('PATTERN_16', 'SAME_STAGE', 'time_state'), ('PATTERN_17', 'SAME_QUARTER_NUMBER', 'time_state'), ('PATTERN_18', 'SAME_MINUTE_BUCKET', 'time_state')):
            rejected.append({'pattern_id': pattern_id, 'name': name, 'pattern_group': group, 'team': team, 'rejection_reason': 'HISTORICAL_CHECKPOINT_NOT_AVAILABLE'})
    for pattern_id, name in (('PATTERN_19', 'FAVORITE_LEADS'), ('PATTERN_20', 'FAVORITE_TRAILS')):
        rejected.append({'pattern_id': pattern_id, 'name': name, 'pattern_group': 'favorite_state', 'team': team, 'rejection_reason': 'HISTORICAL_CLOSING_HANDICAP_NOT_AVAILABLE'})
    return (active, rejected)

def calculate_scenario(market: dict[str, Any], canonical: dict[str, Any], history: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    teams = [market['team']] if market.get('team') else [canonical['home_team'], canonical['away_team']]
    patterns_found: list[dict[str, Any]] = []
    patterns_rejected: list[dict[str, Any]] = []
    alpha = float(config['smoothing']['alpha'])
    beta = float(config['smoothing']['beta'])
    pattern_k = float(config['credibility']['pattern_k'])
    minimum = int(config['credibility']['pattern_min_sample'])
    p_hist = float(history['p_hist'])
    for team in teams:
        active, inactive = _active_patterns(canonical, team, config)
        patterns_rejected.extend(inactive)
        pool = canonical['history']['team_a'] if team == canonical['home_team'] else canonical['history']['team_b']
        for pattern in active:
            matched = [game for game in pool if pattern['matcher'](game)]
            values = [segment_value(game, market, team if market.get('team') else None) for game in matched]
            valid_outcomes = [float(value) for value in values if value is not None]
            breakdown = exact_breakdown(values, float(market['line']), market['side'], alpha, beta)
            credibility = breakdown['n'] / (breakdown['n'] + pattern_k) if breakdown['n'] else 0.0
            smoothed = breakdown['p_smoothed'] if breakdown['p_smoothed'] is not None else p_hist
            shrunk = credibility * smoothed + (1 - credibility) * p_hist
            sample_quality = min(1.0, breakdown['n'] / 20.0)
            specificity = float(config['patterns']['specificity'].get(pattern['pattern_id'], 0.7))
            distance_quality = 1.0
            rank = credibility * sample_quality * specificity * distance_quality
            result = {
                'pattern_id': pattern['pattern_id'],
                'name': pattern['name'],
                'pattern_group': pattern['pattern_group'],
                'team': team,
                'matched_games': breakdown['n'],
                'market_hits': breakdown['wins'],
                'market_losses': breakdown['losses'],
                'pushes': breakdown['pushes'],
                'raw_hit_pct': breakdown['raw_pct'],
                'smoothed_probability': breakdown['p_smoothed'],
                'credibility': credibility,
                'shrunk_probability': shrunk,
                'sample_quality': sample_quality,
                'specificity': specificity,
                'distance_match_quality': distance_quality,
                'pattern_rank': rank,
                'outcome_mean': statistics.fmean(valid_outcomes) if valid_outcomes else None,
                'outcome_median': statistics.median(valid_outcomes) if valid_outcomes else None,
                'outcome_standard_deviation': statistics.stdev(valid_outcomes) if len(valid_outcomes) > 1 else None,
                'used_in_scenario': False,
                'rejection_reason': None,
            }
            if breakdown['n'] < minimum:
                result['rejection_reason'] = 'SAMPLE_BELOW_PATTERN_MINIMUM'
                patterns_rejected.append(result)
            else:
                patterns_found.append(result)
    best_by_group: dict[str, dict[str, Any]] = {}
    for pattern in patterns_found:
        group = pattern['pattern_group']
        if group not in best_by_group or pattern['pattern_rank'] > best_by_group[group]['pattern_rank']:
            best_by_group[group] = pattern
    patterns_used = list(best_by_group.values())
    for pattern in patterns_used:
        pattern['used_in_scenario'] = True
    for pattern in patterns_found:
        if pattern not in patterns_used:
            rejected = dict(pattern)
            rejected['rejection_reason'] = 'DOUBLE_COUNT_GROUP_LOWER_RANK'
            patterns_rejected.append(rejected)
    independence = float(config['patterns'].get('independence_factor', 0.9))
    for pattern in patterns_used:
        pattern['pattern_weight'] = pattern['credibility'] * pattern['sample_quality'] * pattern['specificity'] * independence * pattern['distance_match_quality']
    weight_sum = sum((pattern['pattern_weight'] for pattern in patterns_used))
    if weight_sum > 0:
        raw = sum((pattern['pattern_weight'] * pattern['shrunk_probability'] for pattern in patterns_used)) / weight_sum
        effective_sample = sum((pattern['pattern_weight'] * pattern['matched_games'] for pattern in patterns_used))
        scenario_k = float(config['credibility']['scenario_k'])
        credibility = effective_sample / (effective_sample + scenario_k)
        probability = credibility * raw + (1 - credibility) * p_hist
        support = 'ON'
    else:
        raw = p_hist
        effective_sample = 0.0
        credibility = 0.0
        probability = p_hist
        support = 'OFF'
    outcome_items = [
        (float(pattern['outcome_median']), float(pattern.get('pattern_weight') or 0.0))
        for pattern in patterns_used
        if pattern.get('outcome_median') is not None and float(pattern.get('pattern_weight') or 0.0) > 0
    ]
    outcome_weight = sum(weight for _, weight in outcome_items)
    outcome_center = (
        sum(value * weight for value, weight in outcome_items) / outcome_weight
        if outcome_weight > 0
        else None
    )
    return {
        'patterns_found': patterns_found,
        'patterns_used': patterns_used,
        'patterns_rejected': patterns_rejected,
        'p_scenario_raw': raw,
        'effective_sample': effective_sample,
        'scenario_credibility': credibility,
        'p_scenario': probability,
        'scenario_support': support,
        'outcome_center': outcome_center,
        'outcome_center_source': 'matched_pattern_outcome_medians' if outcome_center is not None else None,
    }

def choose_best_per_group(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for pattern in patterns:
        group = pattern['pattern_group']
        if group not in best or pattern['pattern_rank'] > best[group]['pattern_rank']:
            best[group] = pattern
    return list(best.values())

# ===== live_projection_engine.py =====
def safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator

def calculate_team_metrics(stats: dict[str, Any], points: float, elapsed_minutes: float) -> dict[str, Optional[float]]:
    fga = stats.get('FGA')
    fgm = stats.get('FGM')
    two_pa = stats.get('2PA')
    two_pm = stats.get('2PM')
    three_pa = stats.get('3PA')
    three_pm = stats.get('3PM')
    fta = stats.get('FTA')
    ftm = stats.get('FTM')
    orb = stats.get('ORB')
    turnovers = stats.get('TO')
    poss = fga - orb + turnovers + 0.44 * fta if None not in (fga, orb, turnovers, fta) else stats.get('Poss')
    return {'Poss': poss, 'eFG': safe_div(fgm + 0.5 * three_pm if fgm is not None and three_pm is not None else None, fga), 'FTr': safe_div(fta, fga), 'OffRtg': points / poss * 100 if poss else None, '2P%': safe_div(two_pm, two_pa), '3P%': safe_div(three_pm, three_pa), 'FT%': safe_div(ftm, fta), 'TO_rate': safe_div(turnovers, poss), 'ORB_per_possession': safe_div(orb, poss), 'FTA_per_possession': safe_div(fta, poss), 'FGA_per_minute': safe_div(fga, elapsed_minutes), 'Poss_per_minute': safe_div(poss, elapsed_minutes)}

def _segment_clock(market: dict[str, Any], canonical: dict[str, Any]) -> dict[str, float]:
    market_type = market['market_type']
    segment = market.get('segment', 'MATCH')
    elapsed_game = float(canonical['elapsed_game_seconds'])
    full_game = float(canonical['full_game_seconds'])
    quarter = float(canonical['quarter_seconds'])
    side = 'home' if market.get('team') == canonical['home_team'] else 'away' if market.get('team') else None
    if market_type in {'MATCH_TOTAL', 'TEAM_IT_MATCH'}:
        full = full_game
        elapsed = elapsed_game
        current = float(canonical['score'][side]) if side else float(canonical['score']['total'])
    elif segment == 'H1':
        full = full_game / 2
        elapsed = min(elapsed_game, full)
        quarters = canonical['quarters'][:2]
        current = sum((float(q.get(side) or 0) for q in quarters)) if side else sum((float(q.get('total') or 0) for q in quarters))
    elif segment == 'H2':
        full = full_game / 2
        elapsed = max(0.0, min(full, elapsed_game - full_game / 2))
        quarters = canonical['quarters'][2:]
        current = sum((float(q.get(side) or 0) for q in quarters)) if side else sum((float(q.get('total') or 0) for q in quarters))
    elif segment.startswith('Q') and segment[1:].isdigit():
        target = int(segment[1:])
        full = quarter
        if target == canonical.get('current_quarter'):
            elapsed = max(0.0, quarter - float(canonical['quarter_seconds_remaining']))
        elif target < (canonical.get('current_quarter') or 0):
            elapsed = quarter
        else:
            elapsed = 0.0
        q = canonical['quarters'][target - 1]
        current = float(q.get(side) or 0) if side else float(q.get('total') or 0)
    else:
        full, elapsed, current = (full_game, elapsed_game, float(canonical['score']['total']))
    return {'full_seconds': full, 'elapsed_seconds': elapsed, 'remaining_seconds': max(0.0, full - elapsed), 'current_points': current}

def _history_values(market: dict[str, Any], canonical: dict[str, Any]) -> list[float]:
    values: list[float] = []
    team = market.get('team')
    pools = [canonical['history']['team_a'], canonical['history']['team_b']] if not team else [canonical['history']['team_a'] if team == canonical['home_team'] else canonical['history']['team_b']]
    for pool in pools:
        for game in pool:
            value = segment_value(game, market, team)
            if value is not None:
                values.append(float(value))
    return values

def _game_possessions(game: dict[str, Any], side: str) -> Optional[float]:
    stats = game.get('stats', {}).get(side, {})
    fields = [stats.get('FGA'), stats.get('ORB'), stats.get('TO'), stats.get('FTA')]
    if any((value is None for value in fields)):
        return None
    return fields[0] - fields[1] + fields[2] + 0.44 * fields[3]

def _historical_pace_and_ppp(canonical: dict[str, Any], team: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    pool = canonical['history']['team_a'] if team == canonical['home_team'] else canonical['history']['team_b']
    opponent_pool = canonical['history']['team_b'] if team == canonical['home_team'] else canonical['history']['team_a']
    cross_pool = canonical.get('history_cross_format', {}).get('team_a' if team == canonical['home_team'] else 'team_b', [])
    cross_opponent_pool = canonical.get('history_cross_format', {}).get('team_b' if team == canonical['home_team'] else 'team_a', [])

    def collect(games: list[dict[str, Any]], allowed_mode: bool=False) -> tuple[list[float], list[float]]:
        paces: list[float] = []
        rates: list[float] = []
        for game in games:
            side = game.get('perspective_side')
            if not side:
                continue
            other = 'away' if side == 'home' else 'home'
            team_poss = _game_possessions(game, side)
            opp_poss = _game_possessions(game, other)
            game_minutes = to_number((game.get('format') or {}).get('regulation_minutes')) or canonical['full_game_seconds'] / 60
            if team_poss and opp_poss and game_minutes:
                paces.append((team_poss + opp_poss) / 2 / game_minutes)
            target_poss = _game_possessions(game, other if allowed_mode else side)
            target_score = game.get('opponent_score') if allowed_mode else game.get('team_score')
            if target_poss and target_score is not None:
                rates.append(float(target_score) / target_poss)
        return paces, rates

    same_paces, same_offense = collect(pool)
    same_allowed_paces, same_allowed = collect(opponent_pool, allowed_mode=True)
    cross_paces, cross_offense = collect(cross_pool)
    cross_allowed_paces, cross_allowed = collect(cross_opponent_pool, allowed_mode=True)

    def conservative_blend(same: list[float], cross: list[float]) -> Optional[float]:
        same_value = statistics.median(same) if same else None
        cross_value = statistics.median(cross) if cross else None
        if same_value is not None and cross_value is not None:
            return 0.75 * same_value + 0.25 * cross_value
        if same_value is not None:
            return same_value
        if cross_value is not None:
            return cross_value
        return None

    return (
        conservative_blend(same_paces, cross_paces),
        conservative_blend(same_offense, cross_offense),
        conservative_blend(same_allowed, cross_allowed),
    )

def _previous_quarter_pace(market: dict[str, Any], canonical: dict[str, Any], clock: dict[str, float]) -> Optional[float]:
    current = canonical.get('current_quarter') or 1
    side = 'home' if market.get('team') == canonical['home_team'] else 'away' if market.get('team') else None
    index = current - 2
    if index < 0 or index >= len(canonical['quarters']):
        return None
    q = canonical['quarters'][index]
    points = q.get(side) if side else q.get('total')
    if points is None:
        return None
    return float(points) / canonical['quarter_seconds']

def _trimmed_weighted_mean(items: list[tuple[str, float, float]]) -> tuple[float, set[str]]:
    included = list(items)
    excluded: set[str] = set()
    if len(included) >= 5:
        ordered = sorted(included, key=lambda item: item[1])
        excluded.update({ordered[0][0], ordered[-1][0]})
        included = ordered[1:-1]
    total_weight = sum((weight for _, _, weight in included))
    if total_weight <= 0:
        return (statistics.median((value for _, value, _ in included)), excluded)
    return (sum((value * weight for _, value, weight in included)) / total_weight, excluded)

def _stage_sigma(market_type: str, stage: str, config: dict[str, Any]) -> float:
    sigma_key = market_type
    if market_type in {'TEAM_IT_H1', 'TEAM_IT_H2'}:
        sigma_key = 'TEAM_IT_HALF'
    elif market_type == 'CURRENT_QUARTER_TOTAL':
        sigma_key = 'QUARTER_TOTAL'
    elif market_type == 'CURRENT_QUARTER_TEAM_IT':
        sigma_key = 'QUARTER_TEAM_IT'
    settings = config['sigma'].get(sigma_key, {'default': 10.0})
    return float(settings.get(stage, settings.get('default', 10.0)))

def _parser_projection_components(market: dict[str, Any], canonical: dict[str, Any], clock: dict[str, float]) -> dict[str, dict[str, Any]]:
    blocks = canonical.get('parser_blocks', {})
    projections = blocks.get('projections') if isinstance(blocks.get('projections'), dict) else {}
    conditioned = blocks.get('stat_conditioned_line_profiles') if isinstance(blocks.get('stat_conditioned_line_profiles'), dict) else {}
    live_meta = conditioned.get('live_calibrated') if isinstance(conditioned.get('live_calibrated'), dict) else {}
    elapsed_minutes = canonical['elapsed_game_seconds'] / 60
    parser_elapsed = to_number(live_meta.get('min_played'))
    snapshot_ok = parser_elapsed is None or abs(parser_elapsed - elapsed_minutes) <= 1.5
    team_side = 'home' if market.get('team') == canonical['home_team'] else 'away' if market.get('team') else None
    result: dict[str, dict[str, Any]] = {}

    def add(name: str, value: Any, reason: Optional[str]=None) -> None:
        numeric = to_number(value)
        if numeric is not None and numeric < clock['current_points']:
            reason = 'PARSER_PROJECTION_BELOW_CURRENT_SCORE'
        result[name] = {
            'value': numeric,
            'available': numeric is not None and reason is None,
            'exclusion_reason': reason if reason else None,
        }

    market_type = market['market_type']
    if market_type in {'MATCH_TOTAL', 'TEAM_IT_MATCH'}:
        live = projections.get('live_calibrated') if isinstance(projections.get('live_calibrated'), dict) else {}
        segment = projections.get('segment_projection') if isinstance(projections.get('segment_projection'), dict) else {}
        pre = projections.get('pre_match_stat') if isinstance(projections.get('pre_match_stat'), dict) else {}
        suffix = f'{team_side}_final' if team_side else 'total'
        live_value = live.get(suffix)
        segment_value_ = segment.get(suffix)
        pre_value = pre.get(suffix)
        live_reason = None if snapshot_ok and live.get('valid', True) else 'PARSER_PROJECTION_STALE_OR_INVALID'
        add('projection_parser_live_calibrated', live_value, live_reason)
        divergence_reason = None
        if to_number(live_value) is not None and to_number(segment_value_) is not None:
            divergence = abs(float(segment_value_) - float(live_value))
            if divergence > max(25.0, abs(float(live_value)) * 0.18):
                divergence_reason = 'PARSER_SEGMENT_DIVERGES_FROM_LIVE_CALIBRATED'
        if not snapshot_ok:
            divergence_reason = 'PARSER_PROJECTION_STALE_OR_INVALID'
        add('projection_parser_segment', segment_value_, divergence_reason)
        add('projection_parser_pre_match', pre_value)
    elif market_type in {'CURRENT_QUARTER_TOTAL', 'CURRENT_QUARTER_TEAM_IT'}:
        segment_key = str(market.get('segment') or '').lower()
        quarter_projection = projections.get(segment_key) if isinstance(projections.get(segment_key), dict) else {}
        if team_side:
            key = 'team_a_center' if team_side == 'home' else 'team_b_center'
        else:
            key = 'total_center'
        add('projection_parser_current_quarter', quarter_projection.get(key))
    return result

def calculate_live_projection(market: dict[str, Any], canonical: dict[str, Any], history: dict[str, Any], scenario: dict[str, Any], config: dict[str, Any], stat: Optional[dict[str, Any]]=None) -> dict[str, Any]:
    clock = _segment_clock(market, canonical)
    elapsed_minutes = canonical['elapsed_game_seconds'] / 60
    team_metrics = {side: calculate_team_metrics(canonical['live_stats'][side], canonical['score'][side], elapsed_minutes) for side in ('home', 'away')}
    poss_values = [team_metrics[side]['Poss'] for side in ('home', 'away')]
    game_possessions = sum(poss_values) / 2 if all((value is not None for value in poss_values)) else None
    combined_ppp = None
    if all((value not in (None, 0) for value in poss_values)):
        combined_ppp = canonical['score']['home'] / poss_values[0] + canonical['score']['away'] / poss_values[1]
    simple = clock['current_points'] / clock['elapsed_seconds'] * clock['full_seconds'] if clock['elapsed_seconds'] > 0 else None
    values = _history_values(market, canonical)
    baseline = statistics.median(values) if values else None
    historical_rate = baseline / clock['full_seconds'] if baseline is not None and clock['full_seconds'] else None
    history_projection = clock['current_points'] + historical_rate * clock['remaining_seconds'] if historical_rate is not None else None
    scenario_projection = None
    scenario_projection_method = None
    scenario_center = to_number(scenario.get('outcome_center'))
    if history_projection is not None and scenario_center is not None and clock['full_seconds']:
        scenario_rate = scenario_center / clock['full_seconds']
        scenario_rate_projection = clock['current_points'] + scenario_rate * clock['remaining_seconds']
        scenario_credibility = max(0.0, min(0.65, float(scenario.get('scenario_credibility') or 0.0)))
        scenario_projection = scenario_credibility * scenario_rate_projection + (1 - scenario_credibility) * history_projection
        scenario_projection_method = 'MATCHED_PATTERN_OUTCOME_DISTRIBUTION'
    elif history_projection is not None:
        delta = (scenario['p_scenario'] - history['p_hist']) * float(config['projection']['scenario_projection_span'])
        direction = 1 if market['side'] == 'OVER' else -1
        scenario_projection = history_projection + direction * delta
        scenario_projection_method = 'PROBABILITY_DELTA_FALLBACK'
    current_rate = clock['current_points'] / clock['elapsed_seconds'] if clock['elapsed_seconds'] > 0 else None
    previous_rate = _previous_quarter_pace(market, canonical, clock)
    segment_rates = [(current_rate, 0.45), (previous_rate, 0.25), (historical_rate, 0.3)]
    available_rates = [(value, weight) for value, weight in segment_rates if value is not None]
    rate_weight = sum((weight for _, weight in available_rates))
    blended_rate = sum((value * weight for value, weight in available_rates)) / rate_weight if rate_weight else None
    segment_projection = clock['current_points'] + blended_rate * clock['remaining_seconds'] if blended_rate is not None else None
    stat_adjusted = None
    stat_details: dict[str, Any] = {'team_metrics': team_metrics, 'game_possessions': game_possessions, 'combined_ppp': combined_ppp}
    if game_possessions is not None and elapsed_minutes > 0:
        remaining_minutes = clock['remaining_seconds'] / 60
        current_pace = game_possessions / elapsed_minutes
        home_pace, home_offense, home_allowed = _historical_pace_and_ppp(canonical, canonical['home_team'])
        away_pace, away_offense, away_allowed = _historical_pace_and_ppp(canonical, canonical['away_team'])
        historical_paces = [value for value in (home_pace, away_pace) if value is not None]
        historical_pace = statistics.median(historical_paces) if historical_paces else current_pace
        scenario_pace = historical_pace
        pace_weights = config['projection']['regression']
        blended_future_pace = current_pace * pace_weights['current_pace'] + historical_pace * pace_weights['history_pace'] + scenario_pace * pace_weights['scenario_pace']
        future_possessions = blended_future_pace * remaining_minutes
        regressed: dict[str, float] = {}
        for side, offense, opponent_allowed in (('home', home_offense, away_allowed), ('away', away_offense, home_allowed)):
            current_ppp = safe_div(canonical['score'][side], team_metrics[side]['Poss'])
            base_values = {'current': current_ppp, 'offense': offense, 'allowed': opponent_allowed, 'scenario': offense}
            weights = {'current': pace_weights['current_ppp'], 'offense': pace_weights['historical_offense_ppp'], 'allowed': pace_weights['opponent_allowed_ppp'], 'scenario': pace_weights['scenario_ppp']}
            available = [(base_values[key], weights[key]) for key in base_values if base_values[key] is not None]
            total = sum((weight for _, weight in available))
            regressed[side] = sum((value * weight for value, weight in available)) / total if total else 1.0
        if market.get('team'):
            side = 'home' if market['team'] == canonical['home_team'] else 'away'
            stat_adjusted = clock['current_points'] + future_possessions * regressed[side]
        else:
            stat_adjusted = clock['current_points'] + future_possessions * (regressed['home'] + regressed['away'])
        adjustment_rate = 0.0
        adjustment_events: list[dict[str, Any]] = []
        indicators = (stat or {}).get('indicators', {})
        adjustments = config['projection'].get('adjustments', {})
        adjustment_rules = [
            ('EFG_VERY_HIGH_NO_VOLUME', bool(indicators.get('score_or_efg_high') and indicators.get('volume_low')), 'efg_very_high_no_volume'),
            ('LOW_EFG_HIGH_VOLUME_BOUNCE', bool(indicators.get('score_or_efg_low') and indicators.get('volume_high')), 'low_efg_high_volume_bounce'),
            ('FTR_HIGH', bool(indicators.get('fta_high')), 'ftr_high'),
            ('ORB_HIGH', bool(indicators.get('orb_high')), 'orb_high'),
            ('TO_HIGH', bool(indicators.get('to_high')), 'to_high'),
            ('OPPONENT_ALLOWS', bool(indicators.get('opponent_allows')), 'opponent_allows'),
            ('OPPONENT_SUPPRESSES', bool(indicators.get('opponent_suppresses')), 'opponent_suppresses'),
        ]
        for rule_id, active, config_key in adjustment_rules:
            if not active:
                continue
            delta = float(adjustments.get(config_key, 0.0))
            adjustment_rate += delta
            adjustment_events.append({'rule_id': rule_id, 'delta': delta})
        adjustment_rate = max(-0.08, min(0.08, adjustment_rate))
        if stat_adjusted is not None:
            future_points = max(0.0, stat_adjusted - clock['current_points'])
            stat_adjusted = clock['current_points'] + future_points * (1 + adjustment_rate)
        stat_details.update({'current_pace': current_pace, 'historical_pace': historical_pace, 'scenario_pace': scenario_pace, 'blended_future_pace': blended_future_pace, 'future_possessions': future_possessions, 'regressed_ppp': regressed, 'adjustment_rate': adjustment_rate, 'adjustment_events': adjustment_events})
    parser_components = _parser_projection_components(market, canonical, clock)
    parser_available_values = [
        item['value'] for item in parser_components.values()
        if item.get('available') and item.get('value') is not None
    ]
    control_values = [value for value in (history_projection, scenario_projection, stat_adjusted, segment_projection) if value is not None]
    control_values.extend(parser_available_values[:1])
    control = statistics.median(control_values) if control_values else simple
    configured_weights = config['projection']['weights']
    component_values = {'projection_simple': simple, 'projection_segment': segment_projection, 'projection_history': history_projection, 'projection_scenario': scenario_projection, 'projection_stat_adjusted': stat_adjusted, 'projection_control': control}
    component_weights = {'projection_simple': float(config['projection']['simple_information_weight']), 'projection_segment': float(configured_weights['segment']), 'projection_history': float(configured_weights['history']), 'projection_scenario': float(configured_weights['scenario']), 'projection_stat_adjusted': float(configured_weights['stat_adjusted']), 'projection_control': float(configured_weights['control'])}
    parser_weights = {
        'projection_parser_live_calibrated': 0.20,
        'projection_parser_segment': 0.08,
        'projection_parser_pre_match': 0.08,
        'projection_parser_current_quarter': 0.18,
    }
    for key, item in parser_components.items():
        component_values[key] = item.get('value') if item.get('available') else None
        component_weights[key] = parser_weights.get(key, 0.08)
    items = [(key, float(value), component_weights[key]) for key, value in component_values.items() if value is not None]
    line = float(market['line'])
    if items:
        projection_used, trimmed = _trimmed_weighted_mean(items)
    else:
        projection_used, trimmed = (line, set())
    components: dict[str, dict[str, Any]] = {}
    for key, value in component_values.items():
        parser_exclusion = parser_components.get(key, {}).get('exclusion_reason')
        components[key] = {'value': parser_components.get(key, {}).get('value', value), 'weight': component_weights[key], 'available': value is not None, 'included': value is not None and key not in trimmed, 'exclusion_reason': 'TRIMMED_EXTREME' if key in trimmed else parser_exclusion or ('UNAVAILABLE' if value is None else None)}
    line_edge = projection_used - line if market['side'] == 'OVER' else line - projection_used
    sigma = _stage_sigma(market['market_type'], canonical['stage'], config)
    z_score = line_edge / sigma
    p_live = normal_cdf(z_score)
    return {'clock': canonical.get('clock'), 'elapsed_seconds': clock['elapsed_seconds'], 'remaining_seconds': clock['remaining_seconds'], 'elapsed_game_seconds': canonical['elapsed_game_seconds'], 'remaining_game_seconds': canonical['remaining_game_seconds'], 'current_points': clock['current_points'], 'components': components, 'projection_simple': simple, 'projection_segment': segment_projection, 'projection_history': history_projection, 'projection_scenario': scenario_projection, 'scenario_projection_method': scenario_projection_method, 'projection_stat_adjusted': stat_adjusted, 'projection_control': control, 'projection_used': projection_used, 'Projection_used': projection_used, 'line': line, 'line_edge': line_edge, 'line_edge_over': projection_used - line, 'line_edge_under': line - projection_used, 'sigma': sigma, 'z_score': z_score, 'p_live': p_live, 'stat_projection_details': stat_details}

# ===== stat_gate_engine.py =====
METRICS = ('scored', 'allowed', 'period_total', 'FGA', 'Poss', '2PA', '3PA', 'FTA', 'FTr', 'ORB', 'TO', 'fouls', 'eFG', 'OffRtg', 'allowed_FGA', 'allowed_eFG', 'allowed_FTA', 'allowed_ORB', 'allowed_Poss', 'allowed_OffRtg', 'forced_TO')

def _zone(value: Optional[float], thresholds: Optional[dict[str, Any]]) -> Optional[str]:
    if value is None or not thresholds:
        return None
    if value <= thresholds['p25']:
        return 'LOW'
    if value >= thresholds['p90']:
        return 'VERY_HIGH'
    if value >= thresholds['p75']:
        return 'HIGH'
    return 'MID'

def _side_for_team(game: dict[str, Any], team: str) -> Optional[str]:
    if game.get('home_team') == team:
        return 'home'
    if game.get('away_team') == team:
        return 'away'
    return None

def _aggregate_stats(game: dict[str, Any], side: str, scope: str) -> dict[str, Optional[float]]:
    if scope == 'MATCH':
        return dict(game.get('stats', {}).get(side, {}))
    indices = [0, 1] if scope == 'H1' else [2, 3] if scope == 'H2' else [int(scope[1:]) - 1] if scope.startswith('Q') and scope[1:].isdigit() else []
    rows = game.get('quarter_stats', {}).get(side, [])
    output: dict[str, Optional[float]] = {}
    for metric in ('FGA', 'FGM', '2PA', '2PM', '3PA', '3PM', 'FTA', 'FTM', 'ORB', 'DRB', 'TO', 'FOULS'):
        values = [rows[index].get(metric) for index in indices if index < len(rows)]
        output[metric] = sum(values) if values and all((value is not None for value in values)) else None
    return output

def _score_for_scope(game: dict[str, Any], side: str, scope: str) -> Optional[float]:
    if scope == 'MATCH':
        return game.get('home_score') if side == 'home' else game.get('away_score')
    indices = [0, 1] if scope == 'H1' else [2, 3] if scope == 'H2' else [int(scope[1:]) - 1] if scope.startswith('Q') and scope[1:].isdigit() else []
    values = [game['quarters'][index].get(side) for index in indices]
    return sum(values) if values and all((value is not None for value in values)) else None

def _metric_value(game: dict[str, Any], team: str, scope: str, metric: str) -> Optional[float]:
    side = _side_for_team(game, team)
    if not side:
        return None
    opponent = 'away' if side == 'home' else 'home'
    scored = _score_for_scope(game, side, scope)
    allowed = _score_for_scope(game, opponent, scope)
    stats = _aggregate_stats(game, side, scope)
    opp_stats = _aggregate_stats(game, opponent, scope)
    full_minutes = 40.0 if scope == 'MATCH' else 20.0 if scope in {'H1', 'H2'} else 10.0
    team_metrics = calculate_team_metrics(stats, scored or 0.0, full_minutes)
    opp_metrics = calculate_team_metrics(opp_stats, allowed or 0.0, full_minutes)
    mapping = {'scored': scored, 'allowed': allowed, 'period_total': scored + allowed if scored is not None and allowed is not None else None, 'FGA': stats.get('FGA'), 'Poss': team_metrics.get('Poss'), '2PA': stats.get('2PA'), '3PA': stats.get('3PA'), 'FTA': stats.get('FTA'), 'FTr': team_metrics.get('FTr'), 'ORB': stats.get('ORB'), 'TO': stats.get('TO'), 'fouls': stats.get('FOULS'), 'eFG': team_metrics.get('eFG'), 'OffRtg': team_metrics.get('OffRtg'), 'allowed_FGA': opp_stats.get('FGA'), 'allowed_eFG': opp_metrics.get('eFG'), 'allowed_FTA': opp_stats.get('FTA'), 'allowed_ORB': opp_stats.get('ORB'), 'allowed_Poss': opp_metrics.get('Poss'), 'allowed_OffRtg': opp_metrics.get('OffRtg'), 'forced_TO': opp_stats.get('TO')}
    return mapping.get(metric)

class ZoneIndex:

    def __init__(self, zones_data: Optional[dict[str, Any]]) -> None:
        self._index: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in (zones_data or {}).get('team_relative_zone_thresholds', []):
            if isinstance(row, dict) and all((key in row for key in ('team', 'scope', 'metric'))):
                self._index[str(row['team']), str(row['scope']).upper(), str(row['metric'])] = row

    def get(self, team: str, scope: str, metric: str) -> Optional[dict[str, Any]]:
        return self._index.get((team, scope.upper(), metric))

def _fallback_thresholds(canonical: dict[str, Any], team: str, scope: str, metric: str) -> Optional[dict[str, Any]]:
    pool = canonical['history']['team_a'] if team == canonical['home_team'] else canonical['history']['team_b']
    values = [_metric_value(game, team, scope, metric) for game in pool]
    valid = [float(value) for value in values if value is not None]
    if not valid:
        return None
    return {'team': team, 'scope': scope, 'metric': metric, 'n': len(valid), 'mean': sum(valid) / len(valid), 'p25': percentile(valid, 0.25), 'p50': percentile(valid, 0.5), 'p75': percentile(valid, 0.75), 'p90': percentile(valid, 0.9), 'source': 'match_file_last35_fallback'}

def _current_raw_stats(canonical: dict[str, Any], side: str, scope: str) -> dict[str, Optional[float]]:
    if scope in {'MATCH', 'H1'}:
        return dict(canonical['live_stats'][side])
    raw = canonical.get('raw_main', {})
    prefix = 'h' if side == 'home' else 'a'
    codes = {'FGA': 'fga', 'FGM': 'fgm', '2PA': '2pa', '2PM': '2pm', '3PA': '3pa', '3PM': '3pm', 'FTA': 'fta', 'FTM': 'ftm', 'ORB': 'orb', 'DRB': 'drb', 'TO': 'tov', 'FOULS': 'fls'}
    indices = [2, 3] if scope == 'H2' else [int(scope[1:])] if scope.startswith('Q') and scope[1:].isdigit() else []
    result: dict[str, Optional[float]] = {}
    for metric, code in codes.items():
        values = [to_number(raw.get(f'{prefix}{code}{index}')) for index in indices]
        result[metric] = sum(values) if values and all((value is not None for value in values)) else None
    return result

def _current_score(canonical: dict[str, Any], side: str, scope: str) -> float:
    if scope == 'MATCH':
        return float(canonical['score'][side])
    indices = [0, 1] if scope == 'H1' else [2, 3] if scope == 'H2' else [int(scope[1:]) - 1] if scope.startswith('Q') and scope[1:].isdigit() else []
    return sum((float(canonical['quarters'][index].get(side) or 0) for index in indices))

def _current_metric_map(canonical: dict[str, Any], side: str, scope: str) -> dict[str, Optional[float]]:
    opponent = 'away' if side == 'home' else 'home'
    stats = _current_raw_stats(canonical, side, scope)
    opp_stats = _current_raw_stats(canonical, opponent, scope)
    scored = _current_score(canonical, side, scope)
    allowed = _current_score(canonical, opponent, scope)
    elapsed_minutes = max(1 / 60, canonical['elapsed_game_seconds'] / 60)
    metrics = calculate_team_metrics(stats, scored, elapsed_minutes)
    opp_metrics = calculate_team_metrics(opp_stats, allowed, elapsed_minutes)
    return {'scored': scored, 'allowed': allowed, 'period_total': scored + allowed, 'FGA': stats.get('FGA'), 'Poss': metrics.get('Poss'), '2PA': stats.get('2PA'), '3PA': stats.get('3PA'), 'FTA': stats.get('FTA'), 'FTr': metrics.get('FTr'), 'ORB': stats.get('ORB'), 'TO': stats.get('TO'), 'fouls': stats.get('FOULS'), 'eFG': metrics.get('eFG'), 'OffRtg': metrics.get('OffRtg'), 'allowed_FGA': opp_stats.get('FGA'), 'allowed_eFG': opp_metrics.get('eFG'), 'allowed_FTA': opp_stats.get('FTA'), 'allowed_ORB': opp_stats.get('ORB'), 'allowed_Poss': opp_metrics.get('Poss'), 'allowed_OffRtg': opp_metrics.get('OffRtg'), 'forced_TO': opp_stats.get('TO')}

def _is_high(zone: Optional[str]) -> bool:
    return zone in {'HIGH', 'VERY_HIGH'}

def _is_low(zone: Optional[str]) -> bool:
    return zone == 'LOW'

def classify_fake_profiles(flags: dict[str, bool]) -> tuple[bool, bool]:
    fake_over = flags.get('score_or_efg_high', False) and flags.get('volume_low', False) and flags.get('fta_low', False) and (not flags.get('orb_high', False))
    fake_under = flags.get('score_or_efg_low', False) and flags.get('volume_high', False) and (flags.get('orb_high', False) or flags.get('fta_high', False))
    return (fake_over, fake_under)

def calculate_stat_gate(market: dict[str, Any], canonical: dict[str, Any], zones_data: Optional[dict[str, Any]]) -> dict[str, Any]:
    scope = market.get('segment') or 'MATCH'
    if scope not in {'MATCH', 'H1', 'H2', 'Q1', 'Q2', 'Q3', 'Q4'}:
        scope = 'MATCH'
    index = ZoneIndex(zones_data)
    comparisons: dict[str, list[dict[str, Any]]] = {'team_a': [], 'team_b': []}
    zone_maps: dict[str, dict[str, Optional[str]]] = {'team_a': {}, 'team_b': {}}
    for label, team, side in (('team_a', canonical['home_team'], 'home'), ('team_b', canonical['away_team'], 'away')):
        current = _current_metric_map(canonical, side, scope)
        for metric in METRICS:
            thresholds = index.get(team, scope, metric) or _fallback_thresholds(canonical, team, scope, metric)
            value = current.get(metric)
            zone = _zone(value, thresholds)
            zone_maps[label][metric] = zone
            comparisons[label].append({'metric': metric, 'current_value': value, 'p25': thresholds.get('p25') if thresholds else None, 'p50': thresholds.get('p50') if thresholds else None, 'p75': thresholds.get('p75') if thresholds else None, 'p90': thresholds.get('p90') if thresholds else None, 'n': thresholds.get('n') if thresholds else 0, 'zone': zone, 'source': thresholds.get('source', 'compact_json') if thresholds else 'missing'})
    maps = [zone_maps['team_a'], zone_maps['team_b']]
    volume_high = any((_is_high(mapping.get('FGA')) or _is_high(mapping.get('Poss')) for mapping in maps))
    volume_low = all((_is_low(mapping.get('FGA')) or _is_low(mapping.get('Poss')) for mapping in maps))
    fta_high = any((_is_high(mapping.get('FTA')) or _is_high(mapping.get('FTr')) for mapping in maps))
    fta_low = all((_is_low(mapping.get('FTA')) or _is_low(mapping.get('FTr')) for mapping in maps))
    orb_high = any((_is_high(mapping.get('ORB')) for mapping in maps))
    orb_low = all((_is_low(mapping.get('ORB')) for mapping in maps))
    to_high = any((_is_high(mapping.get('TO')) for mapping in maps))
    to_not_high = all((not _is_high(mapping.get('TO')) for mapping in maps if mapping.get('TO') is not None))
    efg_low = any((_is_low(mapping.get('eFG')) for mapping in maps))
    efg_not_low = all((not _is_low(mapping.get('eFG')) for mapping in maps if mapping.get('eFG') is not None))
    opponent_allows = any((_is_high(mapping.get('allowed_FGA')) or _is_high(mapping.get('allowed_eFG')) or _is_high(mapping.get('allowed_Poss')) for mapping in maps))
    opponent_suppresses = any((_is_low(mapping.get('allowed_FGA')) or _is_low(mapping.get('allowed_eFG')) or _is_low(mapping.get('allowed_Poss')) for mapping in maps))
    score_or_efg_high = any((_is_high(mapping.get('scored')) or _is_high(mapping.get('eFG')) for mapping in maps))
    score_or_efg_low = any((_is_low(mapping.get('scored')) or _is_low(mapping.get('eFG')) for mapping in maps))
    flags = {'score_or_efg_high': score_or_efg_high, 'score_or_efg_low': score_or_efg_low, 'volume_high': volume_high, 'volume_low': volume_low, 'fta_high': fta_high, 'fta_low': fta_low, 'orb_high': orb_high, 'orb_low': orb_low, 'to_high': to_high, 'to_not_high': to_not_high, 'efg_low': efg_low, 'efg_not_low': efg_not_low, 'opponent_allows': opponent_allows, 'opponent_suppresses': opponent_suppresses}
    fake_over, fake_under = classify_fake_profiles(flags)
    over_channels = [name for name, active in {'FGA_OR_POSS_HIGH': volume_high, 'FTA_OR_FTR_HIGH': fta_high, 'ORB_ACTIVE': orb_high, 'TO_NOT_HIGH': to_not_high, 'EFG_NOT_LOW': efg_not_low, 'OPPONENT_ALLOWS': opponent_allows}.items() if active]
    under_channels = [name for name, active in {'FGA_OR_POSS_LOW': volume_low, 'FTA_OR_FTR_LOW': fta_low, 'ORB_LOW': orb_low, 'TO_HIGH_OR_EMPTY': to_high, 'EFG_LOW_WITHOUT_VOLUME': efg_low and (not volume_high), 'OPPONENT_SUPPRESSES': opponent_suppresses}.items() if active]
    stat_support = canonical['stat_support']
    if market['side'] == 'OVER':
        confirmed = len(over_channels) >= 3 and (not fake_over)
        against = len(under_channels) >= 3 and (not fake_under)
    else:
        confirmed = len(under_channels) >= 3 and (not fake_under)
        against = len(over_channels) >= 3 and (not fake_over)
    status = 'OFF' if stat_support == 'OFF' else 'CONFIRMED' if confirmed else 'AGAINST' if against else 'LIMITED'
    return {'scope': scope, 'team_a': comparisons['team_a'], 'team_b': comparisons['team_b'], 'zones': zone_maps, 'over_positive_channels': over_channels, 'under_positive_channels': under_channels, 'over_gate_score': len(over_channels), 'under_gate_score': len(under_channels), 'over_gate_status': 'CONFIRMED' if len(over_channels) >= 3 and (not fake_over) else 'NOT_CONFIRMED', 'under_gate_status': 'CONFIRMED' if len(under_channels) >= 3 and (not fake_under) else 'NOT_CONFIRMED', 'fake_over': fake_over, 'fake_under': fake_under, 'overheat_status': 'ON' if fake_over else 'OFF', 'bounce_risk': 'ON' if fake_under else 'OFF', 'real_over': len(over_channels) >= 3 and (not fake_over), 'real_under': len(under_channels) >= 3 and (not fake_under), 'indicators': flags, 'stat_support': stat_support, 'stat_gate_status': status}

# ===== q4_context_engine.py =====
def weighted_harmonic_mean(values: dict[str, Optional[float]], weights: dict[str, float], epsilon: float=1e-06) -> Optional[float]:
    active = [(float(weights[key]), float(value)) for key, value in values.items() if key in weights and value is not None and (weights[key] > 0)]
    if not active:
        return None
    numerator = sum((weight for weight, _ in active))
    denominator = sum((weight / max(value, epsilon) for weight, value in active))
    return numerator / denominator if denominator else None

def _quarter_sum(raw: dict[str, Any], code: str, quarters: list[int]) -> Optional[float]:
    values: list[float] = []
    for quarter in quarters:
        home = to_number(raw.get(f'h{code}{quarter}'))
        away = to_number(raw.get(f'a{code}{quarter}'))
        if home is None or away is None:
            return None
        values.append(home + away)
    return sum(values)

def _quarter_total(canonical: dict[str, Any], number: int) -> Optional[float]:
    if number < 1 or number > len(canonical['quarters']):
        return None
    return canonical['quarters'][number - 1].get('total')

def is_q4_context_market(market: dict[str, Any], canonical: dict[str, Any]) -> bool:
    if market['market_type'] in {'CURRENT_QUARTER_TOTAL', 'CURRENT_QUARTER_TEAM_IT'} and market.get('segment') == 'Q4':
        return True
    return market['market_type'] in {'MATCH_TOTAL', 'TEAM_IT_MATCH'} and canonical['stage'] in {'AFTER_3Q', 'Q4_CONFIRMATION'}

def calculate_q4_context(market: dict[str, Any], canonical: dict[str, Any], history: dict[str, Any], scenario: dict[str, Any], live: dict[str, Any], stat: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    applicable = is_q4_context_market(market, canonical)
    if not applicable:
        return {'applicable': False, 'status': 'OFF'}
    exact_time = canonical.get('clock') is not None and canonical.get('current_quarter') == 4
    raw = canonical.get('raw_main', {})
    pre_fouls = _quarter_sum(raw, 'fls', [1, 2, 3])
    pre_fta = _quarter_sum(raw, 'fta', [1, 2, 3])
    pre_fga = _quarter_sum(raw, 'fga', [1, 2, 3])
    q3_fouls = _quarter_sum(raw, 'fls', [3])
    q3_fta = _quarter_sum(raw, 'fta', [3])
    q3_total = _quarter_total(canonical, 3)
    total_after_3q_values = [_quarter_total(canonical, number) for number in (1, 2, 3)]
    total_after_3q = sum(total_after_3q_values) if all((value is not None for value in total_after_3q_values)) else None
    q3_home = sum((float(canonical['quarters'][i].get('home') or 0) for i in range(3)))
    q3_away = sum((float(canonical['quarters'][i].get('away') or 0) for i in range(3)))
    abs_margin_3q = abs(q3_home - q3_away) if total_after_3q is not None else None
    live_margin = abs(float(canonical['score']['margin_home']))
    q4_total = _quarter_total(canonical, 4) or 0.0
    q4_minutes_left = canonical.get('quarter_seconds_remaining', 0) / 60
    pre_ftr = pre_fta / pre_fga if pre_fta is not None and pre_fga else None
    thresholds = deepcopy(config['q4']['thresholds'])
    duration_factor = float(canonical.get('quarter_minutes') or 10) / 10.0
    for key in ('pre_fouls_low', 'pre_fouls_high', 'pre_fouls_very_high', 'q3_fouls_high', 'pre_fta_high', 'pre_fta_low', 'q3_fta_high', 'total_after_3q_high'):
        thresholds[key] = float(thresholds[key]) * duration_factor
    playoff = bool((canonical.get('series_context') or {}).get('is_playoff'))
    must_win = bool((canonical.get('series_context') or {}).get('must_win'))
    chase_margin = abs_margin_3q is not None and thresholds['chase_margin_low'] <= abs_margin_3q <= thresholds['chase_margin_high'] or thresholds['chase_margin_low'] - 2 <= live_margin <= thresholds['chase_margin_high']
    foul_tail = 0.0
    foul_tail += 0.22 if chase_margin else 0.0
    foul_tail += 0.18 if pre_fouls is not None and pre_fouls >= thresholds['pre_fouls_high'] else 0.0
    foul_tail += 0.2 if pre_fta is not None and pre_fta >= thresholds['pre_fta_high'] else 0.0
    foul_tail += 0.15 if q3_fouls is not None and q3_fouls >= thresholds['q3_fouls_high'] else 0.0
    foul_tail += 0.1 if q3_fta is not None and q3_fta >= thresholds['q3_fta_high'] else 0.0
    foul_tail += 0.1 if total_after_3q is not None and total_after_3q >= thresholds['total_after_3q_high'] else 0.0
    foul_tail += 0.05 if playoff else 0.0
    indicators = stat.get('indicators', {})
    volume_low = bool(indicators.get('volume_low'))
    orb_low = bool(indicators.get('orb_low'))
    to_high = bool(indicators.get('to_high'))
    orb_high = bool(indicators.get('orb_high'))
    to_not_high = bool(indicators.get('to_not_high'))
    fta_high = bool(indicators.get('fta_high'))
    efg_not_low = bool(indicators.get('efg_not_low'))
    no_chase = not chase_margin and (not playoff) and (not must_win)
    dry = 0.0
    dry += 0.22 if pre_fouls is not None and pre_fouls <= thresholds['pre_fouls_low'] else 0.0
    dry += 0.18 if pre_fta is not None and pre_fta < thresholds['pre_fta_low'] else 0.0
    dry += 0.18 if abs_margin_3q is not None and abs_margin_3q >= thresholds['blowout_margin'] else 0.0
    dry += 0.15 if volume_low else 0.0
    dry += 0.12 if orb_low else 0.0
    dry += 0.1 if to_high else 0.0
    dry += 0.05 if no_chase else 0.0
    q4_three_pa = _quarter_sum(raw, '3pa', [4])
    three_pa_chase = bool(q4_three_pa is not None and q4_three_pa >= 8 * duration_factor and chase_margin)
    bonus_path = pre_fouls is not None and pre_fouls >= thresholds['pre_fouls_high'] or (pre_fta is not None and pre_fta >= thresholds['pre_fta_high'])
    leader_ft_path = bool(chase_margin and fta_high)
    kill_chase = 0.0
    kill_chase += 0.2 if 4 <= live_margin <= 10 else 0.0
    kill_chase += 0.18 if three_pa_chase else 0.0
    kill_chase += 0.17 if bonus_path else 0.0
    kill_chase += 0.15 if orb_high else 0.0
    kill_chase += 0.15 if to_not_high else 0.0
    kill_chase += 0.1 if leader_ft_path else 0.0
    kill_chase += 0.05 if playoff else 0.0
    volume = 0.0
    volume += 0.25 if indicators.get('volume_high') else 0.0
    volume += 0.2 if fta_high else 0.0
    volume += 0.2 if orb_high else 0.0
    volume += 0.2 if to_not_high else 0.0
    volume += 0.15 if efg_not_low else 0.0
    epsilon = float(config['q4'].get('epsilon', 1e-06))
    under_values = {'hist': history['p_hist'], 'scenario': scenario['p_scenario'], 'live': live['p_live'], 'dry': dry, 'no_foul_tail': 1 - foul_tail, 'no_kill_chase': 1 - kill_chase}
    over_values = {'hist': history['p_hist'], 'scenario': scenario['p_scenario'], 'live': live['p_live'], 'foul_tail': foul_tail, 'kill_chase': kill_chase, 'volume': volume}
    under_gate = weighted_harmonic_mean(under_values, config['q4']['under_weights'], epsilon)
    over_gate = weighted_harmonic_mean(over_values, config['q4']['over_weights'], epsilon)
    line_edge_over = live['line_edge_over']
    over_boost = 0.05 if foul_tail >= 0.7 and line_edge_over >= 4 else 0.03 if foul_tail >= 0.55 and line_edge_over >= 0 else 0.0
    line_edge_bonus = 0.05 if line_edge_over >= 7 else 0.03 if line_edge_over >= 4 else 0.0
    context_gate = under_gate if market['side'] == 'UNDER' else over_gate
    mandatory_missing = []
    if not exact_time:
        mandatory_missing.append('exact_q4_time_or_start')
    if total_after_3q is None:
        mandatory_missing.append('total_after_3q')
    if abs_margin_3q is None:
        mandatory_missing.append('abs_margin_3q')
    return {'applicable': True, 'status': 'ON' if not mandatory_missing else 'MISSING_CONTEXT', 'exact_time': exact_time, 'mandatory_missing': mandatory_missing, 'duration_factor_vs_4x10': duration_factor, 'duration_adjusted_thresholds': thresholds, 'pre_q4_fouls_total': pre_fouls, 'pre_q4_fta_total': pre_fta, 'pre_q4_ftr': pre_ftr, 'q3_fouls_total': q3_fouls, 'q3_fta_total': q3_fta, 'q3_total': q3_total, 'total_after_3q': total_after_3q, 'abs_margin_3q': abs_margin_3q, 'q4_current_total': q4_total, 'q4_minutes_left': q4_minutes_left, 'live_margin_q4': live_margin, 'foul_tail_score': foul_tail, 'dry_score': dry, 'kill_chase_score': kill_chase, 'volume_score': volume, 'under_gate_h': under_gate, 'over_gate_h': over_gate, 'context_gate': context_gate, 'over_boost': over_boost, 'line_edge_bonus': line_edge_bonus, 'bonus_path': bonus_path, 'three_pa_chase': three_pa_chase, 'leader_ft_path': leader_ft_path, 'chase_margin': chase_margin}

# ===== super_basket_calculator.py =====
def _normalize_weights(weights: dict[str, float]) -> tuple[dict[str, float], bool]:
    total = sum((float(value) for value in weights.values()))
    if total <= 0:
        return ({'hist': 1.0, 'scenario': 0.0, 'live': 0.0}, True)
    normalized = {key: float(value) / total for key, value in weights.items()}
    return (normalized, abs(total - 1.0) > 1e-12)

def _cap(rule_id: str, cap: float, reason: str, inputs: Optional[dict[str, Any]]=None) -> dict[str, Any]:
    return {'rule_id': rule_id, 'cap': float(cap), 'reason': reason, 'inputs': inputs or {}}

def _blocker(rule_id: str, reason: str, inputs: Optional[dict[str, Any]]=None) -> dict[str, Any]:
    return {'rule_id': rule_id, 'reason': reason, 'inputs': inputs or {}}

def _trace_step(step: str, applied: bool, formula: str, inputs: dict[str, Any], before: Optional[float], after: Optional[float], reason_codes: Optional[list[str]]=None) -> dict[str, Any]:
    return {
        'step': step,
        'applied': bool(applied),
        'formula': formula,
        'inputs': inputs,
        'probability_before': before,
        'probability_after': after,
        'reason_codes': reason_codes or [],
    }

def _verdict(probability: float, blockers: list[dict[str, Any]], strong_clean: bool) -> str:
    if blockers or probability < 0.68:
        return 'PASS'
    if probability < 0.75:
        return 'RISK PLAY'
    if probability < 0.8:
        return 'LIVE PLAY'
    return 'PLAY' if strong_clean else 'LIVE PLAY'

def _router(market: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    market_type = market['market_type']
    stage = canonical['stage']
    current = canonical.get('current_quarter')
    status, reason, cap = ('ALLOW', 'SUPPORTED_BY_STAGE_ROUTER', None)
    hard_block = False
    trigger_checkpoint = canonical.get('trigger_checkpoint')
    in_q2_window = canonical['quarter_seconds'] <= canonical['elapsed_game_seconds'] < canonical['full_game_seconds'] / 2
    if trigger_checkpoint == 1 and market_type not in {'H1_TOTAL', 'TEAM_IT_H1'}:
        # Absolute checkpoint-level protection. The Q1 job may start parsing a
        # little later (already in Q2 or even at HT), so stage inference alone
        # is not sufficient. A job triggered after Q1 may only emit first-half
        # total / first-half team-IT; full-match total and TEAM_IT_MATCH are
        # always blocked for this job.
        status, reason, hard_block = ('BLOCK', 'CHECKPOINT1_ONLY_H1_TOTAL_AND_H1_TEAM_IT', True)
    elif market_type in {'MATCH_TOTAL', 'TEAM_IT_MATCH'} and (current == 2 or in_q2_window):
        # Checkpoint #1 (stage_monitor.js) opens the analysis window right after
        # Q1 ends, i.e. during Q2 — the whole Q2 window, until half-time (which
        # is Checkpoint #2). In that window a full-match total/team-IT signal is
        # too early and noisy; only the half-scoped markets (H1_TOTAL/TEAM_IT_H1)
        # are allowed to fire here.
        # NOTE: gated on elapsed game time (in_q2_window), not only on the
        # provider's raw `current_quarter` field. Some feeds keep `period` at 1
        # during the break right after Q1 ends (score/time already reflect a
        # finished Q1) until Q2 officially tips off, which let full-match total
        # signals slip through the old `current == 2`-only check for that
        # window.
        status, reason, hard_block = ('BLOCK', 'MATCH_TOTAL_BLOCKED_DURING_Q2_AFTER_CHECKPOINT1_ONLY_HALF_MARKETS', True)
    elif market_type == 'H1_TOTAL' or market_type == 'TEAM_IT_H1':
        if canonical['elapsed_game_seconds'] >= canonical['full_game_seconds'] / 2:
            status, reason, hard_block = ('BLOCK', 'H1_ALREADY_COMPLETE', True)
        elif current == 1:
            status, reason = ('DOWNGRADE', 'H1_BEFORE_Q1_COMPLETION')
    elif market_type == 'H2_TOTAL' or market_type == 'TEAM_IT_H2':
        if canonical['elapsed_game_seconds'] < canonical['full_game_seconds'] / 2:
            status, reason, hard_block = ('BLOCK', 'FUTURE_H2_BEFORE_HT', True)
    elif market_type in {'CURRENT_QUARTER_TOTAL', 'CURRENT_QUARTER_TEAM_IT'}:
        if market.get('segment') in {'Q2', 'Q3'}:
            status, reason, cap = ('DOWNGRADE', 'Q2_Q3_STANDALONE_NO_CLEAN_PLAY', 0.74)
        elif market.get('segment') == 'Q4':
            status, reason = ('CONTEXT_GATE', 'Q4_REQUIRES_CONTEXT_GATE')
    elif market_type in {'MATCH_TOTAL', 'TEAM_IT_MATCH'} and stage == 'AFTER_3Q':
        status, reason = ('PRIORITY', 'AFTER_3Q_PRIORITY')
    elif market_type in {'MATCH_TOTAL', 'TEAM_IT_MATCH', 'H2_TOTAL', 'TEAM_IT_H2'} and stage == 'HT':
        status, reason = ('PRIORITY', 'HT_PRIORITY')
    return {'status': status, 'reason': reason, 'cap': cap, 'hard_block': hard_block}

def _strong_edge_threshold(market_type: str, config: dict[str, Any]) -> float:
    return float(config['strong_live_edge'].get(market_type, 7.0))

def _empty_evaluation(market: dict[str, Any], blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {**market, 'history': {'p_hist': 0.5}, 'scenario': {'p_scenario': 0.5, 'scenario_support': 'OFF', 'patterns_found': [], 'patterns_used': [], 'patterns_rejected': []}, 'live': {'projection_used': None, 'p_live': 0.5}, 'stat_comparison': {'stat_gate_status': 'OFF', 'fake_over': False, 'fake_under': False}, 'q4_context': {'applicable': False, 'status': 'OFF'}, 'weights': {'original': {}, 'normalized': {}, 'normalization_applied': False}, 'p_raw': 0.5, 'router': {'status': 'BLOCK', 'reason': 'INVALID_MARKET'}, 'caps': [], 'blockers': blockers, 'hard_conflict': True, 'p_final': 0.5, 'verdict': 'PASS', 'p_trace': [_trace_step('INVALID_MARKET', True, 'hard_block', {'blockers': blockers}, None, 0.5, [item['rule_id'] for item in blockers])]}

def _dedupe_markets(markets: list[dict[str, Any]], odds_min: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for market in markets:
        key = (
            market.get('market_type'),
            market.get('team'),
            market.get('segment'),
            market.get('side'),
            to_number(market.get('line')),
        )
        grouped.setdefault(key, []).append(market)
    unique: list[dict[str, Any]] = []
    for key, offers in grouped.items():
        ordered = sorted(offers, key=lambda item: float(item.get('odds') or 0.0), reverse=True)
        selected = deepcopy(ordered[0])
        selected['offers'] = [
            {
                'market_id': item.get('market_id'),
                'bookmaker': item.get('bookmaker'),
                'odds': item.get('odds'),
                'source_market_id': item.get('source_market_id'),
            }
            for item in ordered
        ]
        selected['duplicate_offer_count'] = max(0, len(ordered) - 1)
        issues = [issue for issue in selected.get('parser_issues', []) if issue != 'ODDS_BELOW_MINIMUM']
        if selected.get('odds') is None or float(selected['odds']) < odds_min:
            issues.append('ODDS_BELOW_MINIMUM')
        selected['parser_issues'] = sorted(set(issues))
        selected['eligible_market'] = not selected['parser_issues']
        stable_key = '|'.join(str(part) for part in key)
        selected['math_market_key'] = hashlib.sha256(stable_key.encode('utf-8')).hexdigest()[:16]
        unique.append(selected)
    unique.sort(key=lambda item: (str(item.get('market_type')), str(item.get('team')), str(item.get('segment')), float(item.get('line') or 0), str(item.get('side'))))
    return unique, {
        'offer_sides_before_deduplication': len(markets),
        'unique_market_sides': len(unique),
        'duplicate_offers_removed': len(markets) - len(unique),
    }

class SuperBasketCalculator:

    def __init__(self, config: dict[str, Any], zones_data: Optional[dict[str, Any]]=None) -> None:
        self.config = deepcopy(config)
        self.zones_data = zones_data or {}

    @classmethod
    def from_files(cls, config_path: str | Path, zones_path: Optional[str | Path]=None) -> 'SuperBasketCalculator':
        with open(config_path, 'r', encoding='utf-8') as handle:
            config = json.load(handle)
        zones: dict[str, Any] = {}
        if zones_path:
            with open(zones_path, 'r', encoding='utf-8') as handle:
                zones = json.load(handle)
        return cls(config, zones)

    def evaluate_market(self, market: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
        initial_blockers = [_blocker(issue, issue.replace('_', ' ').title()) for issue in market.get('parser_issues', [])]
        if market.get('line') is None or market.get('odds') is None or market.get('market_type') == 'UNSUPPORTED':
            return _empty_evaluation(market, initial_blockers)
        history = calculate_history(market, canonical, self.config)
        scenario = calculate_scenario(market, canonical, history, self.config)
        stat = calculate_stat_gate(market, canonical, self.zones_data)
        live = calculate_live_projection(market, canonical, history, scenario, self.config, stat)
        q4 = calculate_q4_context(market, canonical, history, scenario, live, stat, self.config)
        stage_key = canonical['stage']
        original_weights = deepcopy(self.config['stage_weights'].get(stage_key, self.config['stage_weights']['EARLY_LIVE']))
        normalized_weights, normalization_applied = _normalize_weights(original_weights)
        p_raw = normalized_weights['hist'] * history['p_hist'] + normalized_weights['scenario'] * scenario['p_scenario'] + normalized_weights['live'] * live['p_live']
        router = _router(market, canonical)
        caps: list[dict[str, Any]] = []
        blockers = list(initial_blockers)
        if canonical['data_gate']['schema_errors']:
            blockers.append(_blocker('SCHEMA_ERROR', 'Required canonical fields are missing', {'paths': canonical['data_gate']['schema_errors']}))
        if router.get('cap') is not None:
            caps.append(_cap('PRODUCTION_ROUTER_DOWNGRADE', router['cap'], router['reason']))
        if router.get('hard_block'):
            blockers.append(_blocker('PRODUCTION_ROUTER_BLOCK', router['reason']))
        if market.get('segment') == 'Q3' and market['market_type'] in {'CURRENT_QUARTER_TOTAL', 'CURRENT_QUARTER_TEAM_IT'}:
            if p_raw < 0.80:
                blockers.append(_blocker('Q3_EXCEPTIONAL_PROBABILITY_BELOW_80', 'Standalone Q3 requires model probability of at least 80%', {'p_raw': p_raw}))
            if stat.get('stat_support') != 'ON':
                blockers.append(_blocker('Q3_EXCEPTIONAL_STATS_REQUIRED', 'Standalone Q3 requires complete live statistics'))
        same_format_n = int(canonical['data_gate'].get('pooled_n') or 0)
        if same_format_n == 0:
            caps.append(_cap('NO_SAME_FORMAT_HISTORY', 0.67, 'No same-duration history is available'))
            blockers.append(_blocker('NO_SAME_FORMAT_HISTORY', 'Exact-line history from a different game duration cannot be used'))
        elif same_format_n < 20:
            caps.append(_cap('SMALL_SAME_FORMAT_SAMPLE', self.config['caps']['small_sample'], 'Same-format history sample is below 20 games', {'same_format_pooled_n': same_format_n}))
        live_mode = canonical['stage'] != 'PRE_MATCH'
        if live_mode and stat['stat_support'] == 'OFF':
            # A physically consistent score/clock fallback remains eligible, but
            # it must never be promoted to a clean PLAY without live statistics.
            caps.append(_cap('NO_STATS_FALLBACK', self.config['caps']['stat_off'], 'Live statistics unavailable; RISK PLAY only'))
        elif live_mode and stat['stat_support'] == 'LIMITED':
            caps.append(_cap('STAT_SUPPORT_LIMITED', self.config['caps']['stat_limited'], 'Incomplete live statistics'))
        if stat['stat_gate_status'] == 'AGAINST':
            blockers.append(_blocker('STAT_GATE_DIRECTLY_AGAINST', 'Team-relative stat channels oppose this side', {'side': market['side']}))
        if market['side'] == 'OVER' and stat['fake_over']:
            caps.append(_cap('FAKE_OVER', self.config['caps']['fake_over'], 'High score/efficiency is not supported by volume'))
        if market['side'] == 'UNDER' and stat['fake_under']:
            caps.append(_cap('FAKE_UNDER', self.config['caps']['fake_under'], 'Low score has high-volume bounce risk'))
        strong_edge = _strong_edge_threshold(market['market_type'], self.config)
        if history['p_hist'] >= 0.9 and live['line_edge'] <= -strong_edge:
            blockers.append(_blocker('STRONG_HISTORY_LIVE_CONFLICT', 'History side is blocked by a strong opposite live projection', {'p_hist': history['p_hist'], 'line_edge': live['line_edge'], 'threshold': strong_edge}))
        if market['market_type'].startswith('TEAM_IT') or market['market_type'] == 'CURRENT_QUARTER_TEAM_IT':
            opponent_allowed = history.get('opponent_allowed', {})
            weakest = history.get('weakest_gate')
            if not opponent_allowed or opponent_allowed.get('n', 0) == 0:
                blockers.append(_blocker('TEAM_IT_NO_OPPONENT_ALLOWED', 'Opponent allowed history is mandatory'))
            if weakest is None:
                blockers.append(_blocker('TEAM_IT_WEAKEST_MISSING', 'Own scored/opponent allowed gate is unavailable'))
            elif weakest < 0.7:
                caps.append(_cap('TEAM_IT_WEAKEST_BELOW_70', self.config['caps']['team_it_weak'], 'Weakest Team IT gate below 70%', {'weakest': weakest}))
                blockers.append(_blocker('TEAM_IT_WEAKEST_BLOCK', 'Weakest Team IT gate below 70%', {'weakest': weakest}))
            elif weakest < 0.75:
                caps.append(_cap('TEAM_IT_WEAKEST_70_74', self.config['caps']['team_it_70_74'], 'Weakest Team IT gate is 70-74%', {'weakest': weakest}))
            elif weakest < 0.8:
                caps.append(_cap('TEAM_IT_WEAKEST_75_79', self.config['caps']['team_it_75_79'], 'Weakest Team IT gate is 75-79%', {'weakest': weakest}))
            required_ppm = history.get('required_points_per_minute')
            if market['side'] == 'OVER' and required_ppm is not None and (required_ppm > float(self.config['team_it']['unrealistic_points_per_minute'])):
                blockers.append(_blocker('TEAM_IT_REQUIRED_LIVE_UNREALISTIC', 'Required scoring rate is mathematically unrealistic', {'required_points_per_minute': required_ppm}))
        context_probability = p_raw
        if q4.get('applicable'):
            if q4.get('mandatory_missing'):
                blockers.append(_blocker('Q4_MISSING_MANDATORY_CONTEXT', 'Q4 exact time/score context is incomplete', {'missing': q4['mandatory_missing']}))
            if market['side'] == 'UNDER':
                gate = q4.get('under_gate_h')
                if gate is not None:
                    gate_adjusted = gate + 0.03 if q4['dry_score'] >= 0.7 and live['line_edge_under'] > 0 else gate
                    context_probability = min(p_raw, gate_adjusted)
                if q4['dry_score'] < 0.55:
                    blockers.append(_blocker('Q4_UNDER_NO_DRY', 'Q4 Under requires DryScore at least 0.55', {'dry_score': q4['dry_score']}))
                elif q4['dry_score'] < 0.7 and live['line_edge_under'] < strong_edge:
                    blockers.append(_blocker('Q4_UNDER_MEDIUM_DRY_NO_STRONG_EDGE', 'DryScore 0.55-0.69 requires a strong projection edge below the line', {'dry_score': q4['dry_score'], 'line_edge_under': live['line_edge_under'], 'required_edge': strong_edge}))
                if q4['foul_tail_score'] >= 0.7 or q4['kill_chase_score'] >= 0.65:
                    caps.append(_cap('Q4_UNDER_DANGER', self.config['caps']['q4_danger'], 'Foul-tail or kill/chase risk blocks clean Under', {'foul_tail': q4['foul_tail_score'], 'kill_chase': q4['kill_chase_score']}))
            else:
                gate = q4.get('over_gate_h')
                if gate is not None:
                    context_probability = min(p_raw + q4['over_boost'], gate + q4['line_edge_bonus'])
                if live['p_live'] < 0.6 or live['projection_used'] < market['line'] or stat['indicators'].get('to_high') or (not stat['indicators'].get('efg_not_low')):
                    blockers.append(_blocker('Q4_OVER_CONFIRMATION_FAILED', 'Q4 Over needs P_live >=60%, projection above line, TO not high and eFG not low'))
        active_cap = min((item['cap'] for item in caps), default=1.0)
        p_final = max(0.0, min(1.0, context_probability, active_cap))
        alignment = history['p_hist'] >= 0.5 and scenario['p_scenario'] >= 0.5 and (live['p_live'] >= 0.5)
        sample_sufficient = canonical['data_gate']['pooled_n'] >= 20
        strong_clean = not blockers and (not caps) and alignment and (stat['stat_gate_status'] == 'CONFIRMED') and sample_sufficient
        verdict = _verdict(p_final, blockers, strong_clean)
        p_trace = [
            _trace_step('P_HIST', True, 'weighted exact + form + H2H + distribution (or Team IT formula)', history.get('components', {'team_it': history.get('component_weights')}), None, history['p_hist']),
            _trace_step('P_SCENARIO', scenario.get('scenario_support') == 'ON', 'independent matched-pattern groups with sample shrinkage', {'effective_sample': scenario.get('effective_sample'), 'patterns_used': [item.get('pattern_id') for item in scenario.get('patterns_used', [])], 'outcome_center': scenario.get('outcome_center')}, None, scenario['p_scenario']),
            _trace_step('P_LIVE', canonical['stage'] != 'PRE_MATCH', 'Phi(line edge / sigma) from conservative multi-component projection', {'projection_used': live.get('projection_used'), 'line': market['line'], 'line_edge': live.get('line_edge'), 'sigma': live.get('sigma'), 'scenario_projection_method': live.get('scenario_projection_method')}, None, live['p_live']),
            _trace_step('STAGE_WEIGHTS', True, 'w_hist*P_hist + w_scenario*P_scenario + w_live*P_live', {'stage': stage_key, 'weights': normalized_weights, 'normalization_applied': normalization_applied}, None, p_raw),
            _trace_step('PRODUCTION_ROUTER', router['status'] != 'ALLOW', 'router may allow, cap, prioritize or block the market', router, p_raw, p_raw, [router['reason']]),
            _trace_step('STAT_GATE', stat.get('stat_gate_status') != 'OFF', 'team-relative 3-of-5 confirmation gate', {'support': stat.get('stat_support'), 'status': stat.get('stat_gate_status'), 'over_score': stat.get('over_gate_score'), 'under_score': stat.get('under_gate_score')}, p_raw, p_raw, [f"STAT_{stat.get('stat_gate_status')}"]),
            _trace_step('FAKE_PROFILE', bool((market['side'] == 'OVER' and stat.get('fake_over')) or (market['side'] == 'UNDER' and stat.get('fake_under'))), 'fake over/under applies cap only to the evaluated side', {'fake_over': stat.get('fake_over'), 'fake_under': stat.get('fake_under'), 'evaluated_side': market['side']}, p_raw, p_raw, [item['rule_id'] for item in caps if item['rule_id'].startswith('FAKE_')]),
            _trace_step('LIVE_HISTORY_CONFLICT', any(item['rule_id'] == 'STRONG_HISTORY_LIVE_CONFLICT' for item in blockers), 'strong opposite live edge blocks the history side', {'p_hist': history['p_hist'], 'line_edge': live.get('line_edge'), 'required_edge': strong_edge}, p_raw, p_raw, [item['rule_id'] for item in blockers if 'CONFLICT' in item['rule_id']]),
            _trace_step('FORMAT_AND_SAMPLE_GATE', same_format_n < 20, 'exact-line hits use same regulation duration only; cross-format games are normalized baseline only', {'format': canonical.get('format'), 'same_format_pooled_n': same_format_n, 'cross_format_team_a_n': canonical['data_gate'].get('cross_format_team_a_n'), 'cross_format_team_b_n': canonical['data_gate'].get('cross_format_team_b_n')}, p_raw, p_raw, [item['rule_id'] for item in caps if 'SAMPLE' in item['rule_id'] or 'FORMAT' in item['rule_id']]),
            _trace_step('TEAM_IT_GATE', market['market_type'].startswith('TEAM_IT') or market['market_type'] == 'CURRENT_QUARTER_TEAM_IT', '0.50 own + 0.35 opponent allowed + 0.15 H2H; weakest gate controls cap/block', {'weakest_gate': history.get('weakest_gate'), 'required_points_per_minute': history.get('required_points_per_minute')}, p_raw, p_raw, [item['rule_id'] for item in caps + blockers if item['rule_id'].startswith('TEAM_IT_')]),
            _trace_step('Q4_CONTEXT', bool(q4.get('applicable')), 'harmonic context gate after P_raw', {'status': q4.get('status'), 'foul_tail': q4.get('foul_tail_score'), 'dry': q4.get('dry_score'), 'kill_chase': q4.get('kill_chase_score'), 'volume': q4.get('volume_score'), 'context_gate': q4.get('context_gate')}, p_raw, context_probability, [item['rule_id'] for item in caps + blockers if item['rule_id'].startswith('Q4_')]),
            _trace_step('ACTIVE_CAPS', bool(caps), 'P_capped = min(P_context, all active caps)', {'active_cap': active_cap, 'caps': caps}, context_probability, p_final, [item['rule_id'] for item in caps]),
            _trace_step('HARD_BLOCKERS', bool(blockers), 'any hard blocker forces PASS without inventing a replacement market', {'blockers': blockers}, p_final, p_final, [item['rule_id'] for item in blockers]),
            _trace_step('P_FINAL_RULE', True, 'clamp(P_context, active caps); blockers control verdict', {'strong_clean': strong_clean}, p_final, p_final, [verdict]),
        ]
        return {**market, 'history': history, 'scenario': scenario, 'live': live, 'stat_comparison': stat, 'q4_context': q4, 'weights': {'original': original_weights, 'normalized': normalized_weights, 'normalization_applied': normalization_applied}, 'p_raw': p_raw, 'router': router, 'caps': caps, 'blockers': blockers, 'hard_conflict': bool(blockers), 'p_final': p_final, 'verdict': verdict, 'p_trace': p_trace, 'strong_requirements': {'aligned': alignment, 'stat_confirmation': stat['stat_gate_status'] == 'CONFIRMED', 'sample_sufficient': sample_sufficient, 'clean': strong_clean}}

    def calculate(self, source: dict[str, Any], dispatch_threshold: Optional[float]=None, strict_schema: bool=False) -> dict[str, Any]:
        canonical = adapt_match(source, self.config, strict_schema)
        markets, audit = parse_markets(source, canonical, self.config)
        canonical['data_gate']['lines_found'] = sum((row.get('line') is not None for row in audit))
        markets, dedupe_summary = _dedupe_markets(markets, float(self.config['odds_min']))
        evaluations = [self.evaluate_market(market, canonical) for market in markets]
        threshold = float(dispatch_threshold if dispatch_threshold is not None else self.config.get('dispatch_threshold', 0.7))
        candidates = [evaluation for evaluation in evaluations if evaluation['p_final'] >= threshold and (not evaluation['blockers']) and (evaluation.get('odds') is not None) and (evaluation['odds'] >= float(self.config['odds_min']))]
        candidates.sort(key=lambda item: (item['p_final'], item.get('odds') or 0), reverse=True)
        best = candidates[0] if candidates else None
        payload_candidates = []
        for item in candidates:
            payload_candidates.append({'market_id': item['market_id'], 'market_type': item['market_type'], 'team': item.get('team'), 'segment': item['segment'], 'side': item['side'], 'line': item['line'], 'odds': item['odds'], 'team_a_hits_n': [item['history'].get('team_a', {}).get('wins'), item['history'].get('team_a', {}).get('n')], 'team_b_hits_n': [item['history'].get('team_b', {}).get('wins'), item['history'].get('team_b', {}).get('n')], 'h2h_hits_n': [item['history'].get('h2h', {}).get('wins'), item['history'].get('h2h', {}).get('n')], 'p_hist': item['history']['p_hist'], 'patterns': item['scenario']['patterns_used'], 'p_scenario': item['scenario']['p_scenario'], 'projection_used': item['live']['projection_used'], 'p_live': item['live']['p_live'], 'stat_zones': item['stat_comparison'].get('zones'), 'fake_over': item['stat_comparison'].get('fake_over'), 'fake_under': item['stat_comparison'].get('fake_under'), 'p_raw': item['p_raw'], 'caps': item['caps'], 'blockers': item['blockers'], 'p_final': item['p_final'], 'verdict': item['verdict']})
        unhashed = deepcopy(source)
        unhashed.pop('super_basket_calculation', None)
        unhashed.pop('super_basket_system', None)
        snapshot_hash = hashlib.sha256(json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        calculation = {'engine_version': str(self.config.get('engine_version', '5.0')), 'calculated_at': source.get('meta', {}).get('generated_at') or source.get('generated_at'), 'input_snapshot_hash': snapshot_hash, 'canonical_snapshot': {'match_id': canonical['match_id'], 'name': canonical['name'], 'stage': canonical['stage'], 'explicit_stage': canonical['explicit_stage'], 'trigger_checkpoint': canonical.get('trigger_checkpoint'), 'current_quarter': canonical['current_quarter'], 'clock': canonical['clock'], 'score': canonical['score'], 'elapsed_game_seconds': canonical['elapsed_game_seconds'], 'remaining_game_seconds': canonical['remaining_game_seconds'], 'stat_support': canonical['stat_support'], 'format': canonical.get('format')}, 'data_gate': canonical['data_gate'], 'market_audit': dedupe_summary, 'markets_detected': audit, 'market_evaluations': evaluations, 'candidates': candidates, 'best_candidate': best, 'gpt_dispatch': {'threshold': threshold, 'eligible': bool(payload_candidates), 'candidate_count': len(payload_candidates), 'payload': {'match_id': canonical['match_id'], 'stage': canonical['stage'], 'candidates': payload_candidates}}}
        output = deepcopy(source)
        output['super_basket_calculation'] = calculation
        return output

def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)

def save_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{os.getpid()}.tmp')
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)

# ===== EMBEDDED CONFIG AND SIMPLE INTEGRATION API =====
DEFAULT_CONFIG = json.loads(r"""{
  "engine_version": "5.0",
  "calibration_status": "calibration_default_not_backtested",
  "odds_min": 1.44,
  "odds_max": 10.0,
  "dispatch_threshold": 0.68,
  "smoothing": {"alpha": 1.0, "beta": 1.0},
  "credibility": {
    "h2h_k": 8.0,
    "form_k": 8.0,
    "pattern_k": 12.0,
    "scenario_k": 18.0,
    "pattern_min_sample": 5,
    "normal_min_sample": 10
  },
  "history_weights": {
    "exact": 0.50,
    "form": 0.15,
    "h2h": 0.10,
    "distribution": 0.25
  },
  "stage_weights": {
    "PRE_MATCH": {"hist": 0.65, "scenario": 0.35, "live": 0.00},
    "EARLY_LIVE": {"hist": 0.32, "scenario": 0.23, "live": 0.45},
    "HT": {"hist": 0.27, "scenario": 0.20, "live": 0.53},
    "AFTER_3Q": {"hist": 0.16, "scenario": 0.17, "live": 0.67},
    "Q4_CONFIRMATION": {"hist": 0.10, "scenario": 0.15, "live": 0.75},
    "CURRENT_Q1_Q3": {"hist": 0.23, "scenario": 0.23, "live": 0.54}
  },
  "sigma": {
    "MATCH_TOTAL": {"PRE_MATCH": 16.0, "EARLY_LIVE": 15.0, "HT": 12.0, "AFTER_3Q": 8.0, "Q4_CONFIRMATION": 7.0, "default": 14.0},
    "TEAM_IT_MATCH": {"PRE_MATCH": 9.0, "EARLY_LIVE": 8.0, "HT": 7.0, "AFTER_3Q": 5.0, "Q4_CONFIRMATION": 4.5, "default": 8.0},
    "H1_TOTAL": {"EARLY_LIVE": 9.0, "CURRENT_Q1_Q3": 8.0, "default": 10.0},
    "H2_TOTAL": {"HT": 10.0, "EARLY_LIVE": 10.0, "default": 10.0},
    "TEAM_IT_HALF": {"HT": 7.0, "CURRENT_Q1_Q3": 6.0, "default": 7.0},
    "QUARTER_TOTAL": {"CURRENT_Q1_Q3": 8.0, "Q4_CONFIRMATION": 7.0, "default": 8.0},
    "QUARTER_TEAM_IT": {"CURRENT_Q1_Q3": 5.0, "Q4_CONFIRMATION": 4.5, "default": 5.0}
  },
  "strong_live_edge": {
    "MATCH_TOTAL": 10.0,
    "H1_TOTAL": 7.0,
    "H2_TOTAL": 7.0,
    "CURRENT_QUARTER_TOTAL": 5.0,
    "TEAM_IT_MATCH": 7.0,
    "TEAM_IT_H1": 4.5,
    "TEAM_IT_H2": 4.5,
    "CURRENT_QUARTER_TEAM_IT": 4.5
  },
  "projection": {
    "minimum_segment_seconds": 120,
    "scenario_projection_span": 20.0,
    "simple_information_weight": 0.05,
    "weights": {
      "history": 0.25,
      "scenario": 0.15,
      "segment": 0.25,
      "stat_adjusted": 0.25,
      "control": 0.10
    },
    "regression": {
      "current_pace": 0.45,
      "history_pace": 0.40,
      "scenario_pace": 0.15,
      "current_ppp": 0.30,
      "historical_offense_ppp": 0.30,
      "opponent_allowed_ppp": 0.30,
      "scenario_ppp": 0.10
    },
    "adjustments": {
      "efg_very_high_no_volume": -0.04,
      "low_efg_high_volume_bounce": 0.03,
      "ftr_high": 0.025,
      "orb_high": 0.02,
      "to_high": -0.03,
      "opponent_allows": 0.02,
      "opponent_suppresses": -0.02
    }
  },
  "caps": {
    "stat_limited": 0.79,
    "stat_off": 0.79,
    "fake_over": 0.74,
    "fake_under": 0.74,
    "small_sample": 0.74,
    "team_it_weak": 0.55,
    "team_it_70_74": 0.74,
    "team_it_75_79": 0.79,
    "q4_danger": 0.68
  },
  "team_it": {
    "own_weight": 0.50,
    "opponent_allowed_weight": 0.35,
    "h2h_weight": 0.15,
    "unrealistic_points_per_minute": 4.0
  },
  "patterns": {
    "margin_bucket_size": 5,
    "total_bucket_size": 5,
    "team_score_bucket_size": 5,
    "specificity": {
      "PATTERN_01": 0.70, "PATTERN_02": 0.75, "PATTERN_03": 0.80,
      "PATTERN_04": 0.75, "PATTERN_05": 1.00, "PATTERN_06": 0.75,
      "PATTERN_07": 0.70, "PATTERN_08": 0.70, "PATTERN_09": 0.80,
      "PATTERN_10": 0.80, "PATTERN_11": 0.80, "PATTERN_12": 0.85,
      "PATTERN_13": 0.85, "PATTERN_14": 0.85, "PATTERN_15": 0.85,
      "PATTERN_16": 0.70, "PATTERN_17": 0.70, "PATTERN_18": 0.70,
      "PATTERN_19": 0.80, "PATTERN_20": 0.80
    },
    "independence_factor": 0.90
  },
  "match_format": {
    "default_quarters": 4,
    "default_quarter_minutes": 10,
    "ten_minute_league_patterns": ["SUMMER LEAGUE", "WNBA", "EUROLEAGUE", "FIBA"],
    "twelve_minute_league_patterns": ["\\bNBA\\b", "NBA G LEAGUE"]
  },
  "q4": {
    "epsilon": 0.000001,
    "thresholds": {
      "pre_fouls_low": 26,
      "pre_fouls_high": 34,
      "pre_fouls_very_high": 38,
      "q3_fouls_high": 12,
      "pre_fta_high": 35,
      "pre_fta_low": 24,
      "q3_fta_high": 13,
      "total_after_3q_high": 137,
      "close_margin": 5,
      "chase_margin_low": 6,
      "chase_margin_high": 10,
      "blowout_margin": 21
    },
    "under_weights": {"hist": 0.18, "scenario": 0.18, "live": 0.24, "dry": 0.18, "no_foul_tail": 0.12, "no_kill_chase": 0.10},
    "over_weights": {"hist": 0.16, "scenario": 0.16, "live": 0.28, "foul_tail": 0.14, "kill_chase": 0.14, "volume": 0.12}
  },
  "aliases": {
    "FGA": ["FGA", "fga", "field_goal_attempts", "shots_attempted", "fieldGoalsAttempted"],
    "FGM": ["FGM", "fgm", "field_goals_made", "fieldGoalsMade"],
    "2PA": ["2PA", "two_point_attempts", "twoPointsAttempted"],
    "2PM": ["2PM", "two_point_made", "twoPointsMade"],
    "3PA": ["3PA", "three_point_attempts", "threePointsAttempted"],
    "3PM": ["3PM", "three_point_made", "threePointsMade"],
    "FTA": ["FTA", "fta", "free_throw_attempts", "freeThrowsAttempted"],
    "FTM": ["FTM", "ftm", "free_throws_made", "freeThrowsMade"],
    "ORB": ["ORB", "orb", "offensive_rebounds", "offensiveRebounds"],
    "DRB": ["DRB", "drb", "defensive_rebounds", "defensiveRebounds"],
    "TO": ["TO", "TOV", "to", "turnovers"],
    "FOULS": ["fouls", "FOULS", "PF", "personal_fouls"],
    "CLOCK": ["clock", "time", "time_remaining", "seconds_remaining", "quarter_clock"],
    "OVER_ODDS": ["over_odd", "overOdd", "over_odds", "overOdds"],
    "UNDER_ODDS": ["under_odd", "underOdd", "under_odds", "underOdds"],
    "LINE": ["line", "total", "value", "handicap"]
  }
}
""")


# ===== VPS ORCHESTRATION, AUDIT, LEARNING, GPT AND TELEGRAM =====
SYSTEM_VERSION = '5.0.0'

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

# Кожен виклик process_vps_match_file() (тобто кожен чекпоінт кожного матчу)
# дописує один JSON-рядок сюди — незалежно від того, PLAY це, RISK чи PASS.
# Шлях налаштовується через VERDICT_LOG_FILE (в docker-compose природно
# покласти поруч з SUPER_BASKET_DB, напр. /app/state/verdicts.log), щоб файл
# лежав у volume 'state' і переживав рестарт контейнера.
VERDICT_LOG_FILE = os.getenv('VERDICT_LOG_FILE', 'verdicts.log')

def append_verdict_log(entry: dict[str, Any], path: str | Path | None = None) -> None:
    """Дописує один JSON-рядок (JSON Lines) з підсумком чекпоінта.

    Ніколи не кидає виняток назовні — збій запису логу не повинен зривати сам
    аналіз/сигнал; помилка лише друкується в stderr.
    """
    target = Path(path or VERDICT_LOG_FILE).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as exc:
        print(f'WARNING: could not write verdict log to {target}: {exc}', file=sys.stderr)

def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}

def normalized_action(probability: float, blockers: list[dict[str, Any]], mode: str) -> tuple[str, str, str]:
    if blockers:
        return 'PASS', 'PASS', '0%'
    if mode.upper() == 'STRICT':
        if probability < 0.75:
            return 'PASS', 'TRIGGER ONLY', '0%'
    elif probability < 0.68:
        return 'PASS', 'PASS', '0%'
    if probability < 0.75:
        return 'RISK', 'RISK PLAY', '10-15% live-limit'
    if probability < 0.80:
        return 'PLAY', 'LIVE PLAY', '20-25% live-limit'
    return 'PLAY', 'PLAY', '30-35% live-limit'

def _precomputed_line_reconciliation(source: dict[str, Any], evaluation: Optional[dict[str, Any]], data_gate: dict[str, Any]) -> dict[str, Any]:
    if not evaluation:
        return {'status': 'NO_MARKET_SELECTED'}
    if data_gate.get('cross_format_team_a_n') or data_gate.get('cross_format_team_b_n'):
        return {
            'status': 'NOT_COMPARABLE_PRECOMPUTED_MIXES_FORMATS',
            'reason': 'P_hist was recomputed from same-duration raw history; parser table may contain 4x10 and 4x12 together',
        }
    root = source.get('history_by_exact_line')
    if not isinstance(root, dict):
        return {'status': 'BLOCK_NOT_PROVIDED'}
    market_type = evaluation.get('market_type')
    line = float(evaluation.get('line'))
    side_key = 'over_rate' if evaluation.get('side') == 'OVER' else 'under_rate'
    comparisons: dict[str, Any] = {}

    def find_array(rows: Any) -> Optional[dict[str, Any]]:
        if not isinstance(rows, list):
            return None
        return next((item for item in rows if to_number(item.get('line')) is not None and abs(float(item['line']) - line) < 1e-9), None)

    if market_type == 'MATCH_TOTAL':
        block = root.get('match_total', {})
        for parser_key, history_key in (('team_a', 'team_a'), ('team_b', 'team_b'), ('pooled70', 'pooled'), ('h2h', 'h2h')):
            row = find_array(block.get(parser_key) if isinstance(block, dict) else None)
            derived = evaluation.get('history', {}).get(history_key, {}).get('raw_pct')
            if row and derived is not None:
                comparisons[history_key] = {'parser': to_number(row.get(side_key)), 'recomputed': derived}
    elif market_type in {'H1_TOTAL', 'H2_TOTAL'}:
        block = root.get('half_total', {})
        row = block.get(str(line)) or block.get(f'{line:.1f}') if isinstance(block, dict) else None
        for parser_key, history_key in (('team_a', 'team_a'), ('team_b', 'team_b'), ('pooled70', 'pooled'), ('h2h', 'h2h')):
            parsed = row.get(parser_key) if isinstance(row, dict) else None
            derived = evaluation.get('history', {}).get(history_key, {}).get('raw_pct')
            if isinstance(parsed, dict) and derived is not None:
                comparisons[history_key] = {'parser': to_number(parsed.get(side_key)), 'recomputed': derived}
    elif market_type == 'CURRENT_QUARTER_TOTAL':
        block = root.get('quarter_total', {})
        for parser_key, history_key in (('team_a', 'team_a'), ('team_b', 'team_b'), ('pooled70', 'pooled')):
            row = find_array(block.get(parser_key) if isinstance(block, dict) else None)
            derived = evaluation.get('history', {}).get(history_key, {}).get('raw_pct')
            if row and derived is not None:
                comparisons[history_key] = {'parser': to_number(row.get(side_key)), 'recomputed': derived}
    elif market_type and ('TEAM_IT' in market_type):
        block = root.get('team_it', {})
        row = block.get(str(line)) or block.get(f'{line:.1f}') if isinstance(block, dict) else None
        team_key = 'team_a' if evaluation.get('team') == (source.get('match', {}) or {}).get('home_team') else 'team_b'
        parsed = row.get(f'{team_key}_{str(evaluation.get("side") or "").lower()}') if isinstance(row, dict) else None
        if isinstance(parsed, dict):
            comparisons['own_scored'] = {'parser': to_number(parsed.get('own_scored_rate')), 'recomputed': evaluation.get('history', {}).get('own_scored', {}).get('raw_pct')}
            comparisons['opponent_allowed'] = {'parser': to_number(parsed.get('opponent_allowed_rate')), 'recomputed': evaluation.get('history', {}).get('opponent_allowed', {}).get('raw_pct')}
    if not comparisons:
        return {'status': 'NO_EXACT_PRECOMPUTED_LINE', 'line': line}
    differences = [abs(float(item['parser']) - float(item['recomputed'])) for item in comparisons.values() if item.get('parser') is not None and item.get('recomputed') is not None]
    max_difference = max(differences, default=0.0)
    return {
        'status': 'MATCH' if max_difference <= 0.02 else 'DATA_CONFLICT',
        'tolerance': 0.02,
        'max_absolute_difference': max_difference,
        'comparisons': comparisons,
    }

def build_input_usage(source: dict[str, Any], calculation: dict[str, Any], evaluation: Optional[dict[str, Any]]) -> dict[str, Any]:
    data_gate = calculation['data_gate']
    blocks = {
        'match/raw_data.main_match': 'PRIMARY: score, time, teams and duration',
        'bookmaker_lines': 'PRIMARY: only real supported bookmaker lines',
        'raw_data.team_a_hist': 'PRIMARY: recomputed exact-line same-format history',
        'raw_data.team_b_hist': 'PRIMARY: recomputed exact-line same-format history',
        'raw_data.h2h_hist': 'PRIMARY: recomputed H2H with shrinkage',
        'history_by_exact_line': 'VALIDATION/FALLBACK: checked against raw history, never double-counted',
        'scenario_patterns_by_line': 'VALIDATION: raw historical pattern matches are recomputed',
        'checkpoint_matrices': 'VALIDATION: scenario condition support',
        'quarter_result_profile': 'VALIDATION: quarter scenario support',
        'stat_conditioned_line_profiles': 'USED: timestamp validation and live-calibrated projection component',
        'projections': 'USED: conservative projection components; stale/extreme values rejected',
        'stat_alignment/stat_zones': 'VALIDATION: stat-gate is recomputed from live stats and team-relative zones',
        'history_zones': 'IGNORED: old global zones are forbidden by the supplied rules',
        'line_evaluations/markets_evaluation/final_verdict': 'AUDIT ONLY: never fed back into P to avoid circular probability',
    }
    availability = {
        key: any(part in source for part in key.split('/'))
        for key in (
            'history_by_exact_line', 'scenario_patterns_by_line', 'checkpoint_matrices',
            'quarter_result_profile', 'stat_conditioned_line_profiles', 'projections',
            'stat_alignment', 'stat_zones', 'history_zones', 'line_evaluations',
            'markets_evaluation', 'final_verdict',
        )
    }
    reconciliation = _precomputed_line_reconciliation(source, evaluation, data_gate)
    return {
        'rules': blocks,
        'availability': availability,
        'exact_line_reconciliation': reconciliation,
        'data_conflict': reconciliation.get('status') == 'DATA_CONFLICT',
        'cross_format_policy': {
            'exact_line_hits': 'same regulation duration only',
            'different_duration_games': 'normalized pace/PPP baseline only, max 25% influence',
            'current_match_excluded': True,
        },
    }

class LearningStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS processed_snapshots (
            input_hash TEXT PRIMARY KEY,
            source_path TEXT,
            output_path TEXT,
            status TEXT NOT NULL,
            processed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            input_hash TEXT NOT NULL,
            match_id TEXT NOT NULL,
            match_name TEXT,
            stage TEXT NOT NULL,
            format_key TEXT NOT NULL,
            market_type TEXT NOT NULL,
            team TEXT,
            segment TEXT NOT NULL,
            side TEXT NOT NULL,
            line REAL NOT NULL,
            odds REAL NOT NULL,
            bookmaker TEXT,
            p_hist REAL,
            p_scenario REAL,
            p_live REAL,
            p_raw REAL,
            p_rule REAL NOT NULL,
            p_calibrated REAL NOT NULL,
            p_final REAL NOT NULL,
            deterministic_action TEXT NOT NULL,
            final_action TEXT NOT NULL,
            gpt_status TEXT,
            telegram_status TEXT,
            telegram_message_id TEXT,
            result TEXT,
            outcome_value REAL,
            profit_units REAL,
            created_at TEXT NOT NULL,
            settled_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_snapshot_market
        ON signals(input_hash, market_type, IFNULL(team, ''), segment, side, line);
        CREATE INDEX IF NOT EXISTS idx_signal_calibration
        ON signals(format_key, market_type, stage, side, result);
        ''')
        self.connection.commit()

    def calibration(self, evaluation: dict[str, Any], stage: str, format_key: str) -> dict[str, Any]:
        p_rule = float(evaluation['p_final'])
        scopes = [
            ('market_stage_side', 'market_type=? AND stage=? AND side=?', (evaluation['market_type'], stage, evaluation['side'])),
            ('market_side', 'market_type=? AND side=?', (evaluation['market_type'], evaluation['side'])),
            ('market', 'market_type=?', (evaluation['market_type'],)),
        ]
        selected: Optional[dict[str, Any]] = None
        for scope, clause, values in scopes:
            row = self.connection.execute(
                f'''SELECT COUNT(*) AS n,
                           SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                           AVG((p_rule - CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END) *
                               (p_rule - CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END)) AS brier
                    FROM signals
                    WHERE format_key=? AND result IN ('WIN','LOSS') AND {clause}''',
                (format_key, *values),
            ).fetchone()
            stats = {'scope': scope, 'samples': int(row['n'] or 0), 'wins': int(row['wins'] or 0), 'brier_score': row['brier']}
            if selected is None:
                selected = stats
            if stats['samples'] >= 50:
                selected = stats
                break
        assert selected is not None
        if selected['samples'] < 50:
            return {**selected, 'status': 'WAITING_FOR_50_SETTLED', 'weight': 0.0, 'posterior_hit_rate': None, 'p_rule': p_rule, 'p_calibrated': p_rule, 'delta': 0.0}
        alpha = beta = 2.0
        posterior = (selected['wins'] + alpha) / (selected['samples'] + alpha + beta)
        weight = min(0.20, 0.05 + 0.15 * min(1.0, (selected['samples'] - 50) / 200.0))
        raw_calibrated = (1 - weight) * p_rule + weight * posterior
        delta = max(-0.05, min(0.05, raw_calibrated - p_rule))
        active_cap = min((float(item['cap']) for item in evaluation.get('caps', [])), default=1.0)
        calibrated = max(0.01, min(0.99, p_rule + delta, active_cap))
        return {**selected, 'status': 'ACTIVE', 'weight': weight, 'posterior_hit_rate': posterior, 'p_rule': p_rule, 'p_calibrated': calibrated, 'delta': calibrated - p_rule}

    def get_signal(self, signal_id: str) -> Optional[dict[str, Any]]:
        row = self.connection.execute('SELECT * FROM signals WHERE signal_id=?', (signal_id,)).fetchone()
        return dict(row) if row else None

    def record_signal(self, decision: dict[str, Any], calculation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        market = decision['market']
        probabilities = decision['probabilities']
        snapshot = calculation['canonical_snapshot']
        values = (
            decision['signal_id'], calculation['input_snapshot_hash'], snapshot['match_id'], snapshot['name'], snapshot['stage'],
            snapshot.get('format', {}).get('format_key') or 'UNKNOWN', market['market_type'], market.get('team'), market['segment'], market['side'],
            market['line'], market['odds'], market.get('bookmaker'), probabilities['p_hist'], probabilities['p_scenario'], probabilities['p_live'],
            probabilities['p_raw'], probabilities['p_rule'], probabilities['p_calibrated'], probabilities['p_final'], decision['deterministic_action'],
            decision['action'], decision.get('gpt_status'), decision.get('telegram_status'), utc_now(),
        )
        existing = self.get_signal(decision['signal_id'])
        if existing:
            return existing, True
        self.connection.execute('''INSERT INTO signals (
            signal_id,input_hash,match_id,match_name,stage,format_key,market_type,team,segment,side,line,odds,bookmaker,
            p_hist,p_scenario,p_live,p_raw,p_rule,p_calibrated,p_final,deterministic_action,final_action,gpt_status,telegram_status,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', values)
        self.connection.commit()
        return self.get_signal(decision['signal_id']) or {}, False

    def update_delivery(self, signal_id: str, final_action: str, gpt_status: str, telegram_status: str, message_id: Optional[str]) -> None:
        self.connection.execute('''UPDATE signals SET final_action=?, gpt_status=?, telegram_status=?, telegram_message_id=? WHERE signal_id=?''', (final_action, gpt_status, telegram_status, message_id, signal_id))
        self.connection.commit()

    def mark_processed(self, input_hash: str, source_path: str, output_path: str, status: str) -> None:
        self.connection.execute('''INSERT INTO processed_snapshots(input_hash,source_path,output_path,status,processed_at)
            VALUES(?,?,?,?,?) ON CONFLICT(input_hash) DO UPDATE SET output_path=excluded.output_path,status=excluded.status,processed_at=excluded.processed_at''',
            (input_hash, source_path, output_path, status, utc_now()))
        self.connection.commit()

    def settle(self, signal_id: str, result: str, outcome_value: Optional[float]=None) -> dict[str, Any]:
        normalized = result.strip().upper()
        if normalized not in {'WIN', 'LOSS', 'PUSH'}:
            raise ValueError('result must be win, loss or push')
        signal = self.get_signal(signal_id)
        if not signal:
            raise ValueError(f'Unknown signal_id: {signal_id}')
        profit = float(signal['odds']) - 1.0 if normalized == 'WIN' else -1.0 if normalized == 'LOSS' else 0.0
        self.connection.execute('''UPDATE signals SET result=?, outcome_value=?, profit_units=?, settled_at=? WHERE signal_id=?''', (normalized, outcome_value, profit, utc_now(), signal_id))
        self.connection.commit()
        return self.get_signal(signal_id) or {}

    def report(self) -> dict[str, Any]:
        row = self.connection.execute('''SELECT COUNT(*) AS signals,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result='PUSH' THEN 1 ELSE 0 END) AS pushes,
            SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) AS unsettled,
            SUM(COALESCE(profit_units,0)) AS profit_units,
            AVG(CASE WHEN result IN ('WIN','LOSS') THEN
                (p_rule - CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END) *
                (p_rule - CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END) END) AS brier_score
            FROM signals''').fetchone()
        return {key: row[key] for key in row.keys()}

def apply_learning_to_evaluation(evaluation: dict[str, Any], store: LearningStore, calculation: dict[str, Any], mode: str) -> dict[str, Any]:
    item = deepcopy(evaluation)
    calibration = store.calibration(item, calculation['canonical_snapshot']['stage'], calculation['canonical_snapshot'].get('format', {}).get('format_key') or 'UNKNOWN')
    p_rule = float(item['p_final'])
    p_calibrated = float(calibration['p_calibrated'])
    action, status, stake = normalized_action(p_calibrated, item.get('blockers', []), mode)
    if action != 'PASS' and item.get('stat_comparison', {}).get('stat_support') == 'OFF':
        action, status, stake = 'RISK', 'RISK PLAY — NO-STATS FALLBACK', '10-15% live-limit'
    item['p_rule'] = p_rule
    item['p_calibrated'] = p_calibrated
    item['p_final_system'] = p_calibrated
    item['system_action'] = action
    item['system_status'] = status
    item['stake'] = stake
    item['calibration'] = calibration
    item.setdefault('p_trace', []).append(_trace_step(
        'CALIBRATION', calibration['status'] == 'ACTIVE',
        'Beta-Binomial empirical calibration after >=50 settled predictions; max weight 20%, max delta 5pp, caps reapplied',
        calibration, p_rule, p_calibrated, [calibration['status']],
    ))
    item['p_trace'].append(_trace_step(
        'P_FINAL', True, 'mode threshold + hard blockers after rule probability and calibration',
        {'mode': mode.upper(), 'action': action, 'status': status}, p_calibrated, p_calibrated,
        [action, status],
    ))
    return item

def _candidate_pool(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in evaluations
        if item.get('odds') is not None
        and float(item['odds']) >= float(DEFAULT_CONFIG['odds_min'])
        and not any(issue not in {'ODDS_BELOW_MINIMUM'} for issue in item.get('parser_issues', []))
    ]

def select_one_decision(evaluations: list[dict[str, Any]], mode: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    active = _candidate_pool(evaluations)
    eligible = [item for item in active if item['system_action'] != 'PASS']
    eligible.sort(key=lambda item: (float(item['p_final_system']), float(item.get('odds') or 0.0)), reverse=True)
    current_active = [item for item in active if not item.get('router', {}).get('hard_block')]
    current_active.sort(key=lambda item: (-len(item.get('blockers', [])), float(item['p_final_system']), float(item.get('odds') or 0.0)), reverse=True)
    active.sort(key=lambda item: (-len(item.get('blockers', [])), float(item['p_final_system']), float(item.get('odds') or 0.0)), reverse=True)
    closest = current_active[0] if current_active else active[0] if active else (evaluations[0] if evaluations else None)
    return (eligible[0] if eligible else None, closest)

def _reason_codes(evaluation: Optional[dict[str, Any]]) -> list[str]:
    if not evaluation:
        return ['NO_SUPPORTED_REAL_LINES']
    codes = [item['rule_id'] for item in evaluation.get('blockers', [])]
    codes.extend(item['rule_id'] for item in evaluation.get('caps', []))
    if not codes:
        codes.append('NO_HARD_CONFLICT')
    return list(dict.fromkeys(codes))

def deterministic_explanation(evaluation: Optional[dict[str, Any]], action: str, mode: str) -> tuple[str, str, str]:
    if not evaluation:
        return (
            'У JSON немає підтримуваної актуальної лінії букмекера з коефіцієнтом не нижче 1.44.',
            'Без реальної лінії система не створює ставку.',
            'Додати актуальну Match/H1/H2/Quarter Total або Team IT лінію з часом і рахунком.',
        )
    probability = float(evaluation.get('p_final_system', evaluation.get('p_final', 0.0)))
    live = evaluation.get('live', {})
    stat = evaluation.get('stat_comparison', {})
    history = evaluation.get('history', {})
    edge = float(live.get('line_edge') or 0.0)
    direction = 'вище' if evaluation.get('side') == 'OVER' else 'нижче'
    if action == 'PASS':
        codes = _reason_codes(evaluation)
        main = codes[0] if codes else 'P_FINAL_BELOW_THRESHOLD'
        explanation = f'Найкращий доступний варіант має P_final {probability:.1%}; рішення PASS через {main}.'
        risk = 'Наявні дані або гейти не дозволяють безпечно перетворити цей варіант на активну ставку.'
        trigger = f'Потрібні P_final не нижче {"75%" if mode.upper() == "STRICT" else "68%"}, відсутність hard blocker та stat-gate не проти.'
        return explanation, risk, trigger
    explanation = (
        f'P_final {probability:.1%}: проєкція {live.get("projection_used"):.1f} очка, '
        f'{direction} лінії {float(evaluation["line"]):.1f} на {abs(edge):.1f}; '
        f'P_hist {float(history.get("p_hist") or 0):.1%}, stat-gate {stat.get("stat_gate_status")}.'
    )
    if action == 'RISK':
        risk = 'Ймовірність перебуває в action-зоні 68–74%, тому це не clean PLAY і потрібен зменшений ліміт.'
    else:
        risk = 'Лінія та коефіцієнт можуть змінитися; сигнал чинний лише для вказаного snapshot.'
    trigger = 'Брати тільки якщо та сама лінія ще доступна, odds >=1.44 і рахунок/час не змінилися суттєво.'
    return explanation, risk, trigger

def build_decision(selected: Optional[dict[str, Any]], closest: Optional[dict[str, Any]], calculation: dict[str, Any], mode: str) -> dict[str, Any]:
    evaluation = selected or closest
    action = selected['system_action'] if selected else 'PASS'
    status = selected['system_status'] if selected else 'PASS'
    stake = selected['stake'] if selected else '0%'
    explanation, main_risk, trigger = deterministic_explanation(evaluation, action, mode)
    if evaluation:
        market = {
            'market_type': evaluation.get('market_type'),
            'team': evaluation.get('team'),
            'segment': evaluation.get('segment'),
            'side': evaluation.get('side'),
            'line': evaluation.get('line'),
            'odds': evaluation.get('odds'),
            'bookmaker': evaluation.get('bookmaker'),
            'offers': evaluation.get('offers', []),
        }
        probabilities = {
            'p_hist': evaluation.get('history', {}).get('p_hist'),
            'p_scenario': evaluation.get('scenario', {}).get('p_scenario'),
            'p_live': evaluation.get('live', {}).get('p_live'),
            'p_raw': evaluation.get('p_raw'),
            'p_rule': evaluation.get('p_rule', evaluation.get('p_final')),
            'p_calibrated': evaluation.get('p_calibrated', evaluation.get('p_final')),
            'p_final': evaluation.get('p_final_system', evaluation.get('p_final')),
        }
    else:
        market = None
        probabilities = {'p_hist': None, 'p_scenario': None, 'p_live': None, 'p_raw': None, 'p_rule': None, 'p_calibrated': None, 'p_final': None}
    signal_id = None
    if selected and market:
        key = '|'.join(str(value) for value in (
            calculation['input_snapshot_hash'], market['market_type'], market.get('team'), market['segment'], market['side'], market['line'],
        ))
        signal_id = 'SB-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:16].upper()
    return {
        'action': action,
        'deterministic_action': action,
        'status': status,
        'signal_id': signal_id,
        'market': market,
        'probabilities': probabilities,
        'stake': stake,
        'explanation_uk': explanation,
        'main_risk_uk': main_risk,
        'trigger_uk': trigger,
        'reason_codes': _reason_codes(evaluation),
        'caps': evaluation.get('caps', []) if evaluation else [],
        'blockers': evaluation.get('blockers', []) if evaluation else [],
        'p_trace': evaluation.get('p_trace', []) if evaluation else [],
        '_evaluation': evaluation,
    }

def gpt_review_decision(decision: dict[str, Any], calculation: dict[str, Any], *, api_key: Optional[str]=None, model: Optional[str]=None) -> dict[str, Any]:
    api_key = api_key or os.getenv('OPENAI_API_KEY')
    if not api_key:
        return {'status': 'SKIPPED_NO_API_KEY', 'approved': False, 'action': 'PASS', 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}
    try:
        from openai import OpenAI
        from pydantic import BaseModel, Field
    except ImportError as exc:
        return {'status': 'ERROR_OPENAI_PACKAGE_MISSING', 'approved': False, 'action': 'PASS', 'error': str(exc), 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}

    class GPTDecisionReview(BaseModel):
        approved: bool
        action: Literal['PLAY', 'RISK', 'PASS']
        explanation_uk: str = Field(min_length=1, max_length=500)
        main_risk_uk: str = Field(min_length=1, max_length=350)
        telegram_text_uk: str = Field(min_length=1, max_length=1000)

    compact = {
        'match': calculation['canonical_snapshot'],
        'decision': {key: value for key, value in decision.items() if key not in {'_evaluation', 'p_trace'}},
        'p_trace': decision.get('p_trace', []),
    }
    instructions = (
        'Ти контролер готового детермінованого баскетбольного сигналу. '
        'Не перераховуй P, не змінюй market/team/segment/side/line/odds і не вигадуй нову ставку. '
        'Ти можеш підтвердити дію, понизити PLAY до RISK або понизити будь-яку дію до PASS. '
        'Ніколи не підвищуй RISK до PLAY і PASS до активної дії. '
        'Якщо p_trace суперечливий, є hard blocker або дані stale — поверни PASS. Відповідай українською.'
    )
    try:
        client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)
        response = client.responses.parse(
            model=model or os.getenv('OPENAI_MODEL', 'gpt-5.6'),
            input=[
                {'role': 'system', 'content': instructions},
                {'role': 'user', 'content': json.dumps(compact, ensure_ascii=False, separators=(',', ':'))},
            ],
            text_format=GPTDecisionReview,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            return {'status': 'ERROR_EMPTY_STRUCTURED_OUTPUT', 'approved': False, 'action': 'PASS', 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}
        review = parsed.model_dump()
        ranks = {'PASS': 0, 'RISK': 1, 'PLAY': 2}
        deterministic = decision['deterministic_action']
        if ranks[review['action']] > ranks[deterministic]:
            review['action'] = deterministic
            review['approved'] = False
            review['status'] = 'DOWNGRADE_ENFORCEMENT_MODEL_ATTEMPTED_UPGRADE'
        else:
            review['status'] = 'APPROVED' if review['approved'] and review['action'] != 'PASS' else 'DOWNGRADED_TO_PASS' if review['action'] == 'PASS' else 'NOT_APPROVED'
        return review
    except Exception as exc:  # network/API failures fail closed by design
        return {'status': 'ERROR_GPT_REVIEW_FAILED', 'approved': False, 'action': 'PASS', 'error': f'{type(exc).__name__}: {exc}', 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}

def build_telegram_message(decision: dict[str, Any], calculation: dict[str, Any], review: dict[str, Any]) -> str:
    market = decision['market'] or {}
    probability = decision['probabilities'].get('p_final')
    icon = '🚨' if decision['action'] == 'PLAY' else '⚠️'
    explanation = review.get('explanation_uk') or decision['explanation_uk']
    risk = review.get('main_risk_uk') or decision['main_risk_uk']
    name = calculation['canonical_snapshot']['name']
    team = market.get('team')
    market_line = f'<b>Ринок:</b> {html.escape(str(market.get("market_type")))} / {html.escape(str(market.get("segment")))}'
    if team:
        market_line += f' ({html.escape(str(team))})'
    lines = [
        f'<b>{icon} {html.escape(decision["action"])}</b>',
        f'<b>Матч:</b> {html.escape(str(name))}',
        market_line,
        f'<b>Сторона:</b> {html.escape(str(market.get("side")))}',
        f'<b>Лінія:</b> {float(market.get("line")):.1f}',
        f'<b>Коефіцієнт:</b> {float(market.get("odds")):.2f} ({html.escape(str(market.get("bookmaker") or ""))})',
        f'<b>P_final:</b> {float(probability):.1%}',
        f'<b>Статус:</b> {html.escape(decision["status"])}',
        f'<b>Stake:</b> {html.escape(decision["stake"])}',
        f'<b>Пояснення:</b> {html.escape(explanation)}',
        f'<b>Головний ризик:</b> {html.escape(risk)}',
        '<i>Сигнал чинний лише для вказаних лінії, коефіцієнта, рахунку та часу.</i>',
        f'<code>{html.escape(str(decision.get("signal_id") or ""))}</code>',
    ]
    return '\n'.join(lines)

def _load_telegram_chat_ids(chats_file: Optional[str] = None) -> list[str]:
    """Read the {"offset":..., "chatIds":[...]} file the bot maintains and
    return the chat ids as strings, in order, de-duplicated."""
    path_value = chats_file or os.getenv('TELEGRAM_CHATS_FILE')
    if not path_value:
        return []
    path = Path(path_value).expanduser()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    raw_ids = data.get('chatIds') if isinstance(data, dict) else None
    if not isinstance(raw_ids, list):
        return []
    return list(dict.fromkeys(str(item) for item in raw_ids if item is not None))

def send_telegram_message(text_message: str, *, token: Optional[str]=None, chat_id: Optional[str]=None, chat_ids: Optional[list[str]]=None, chats_file: Optional[str]=None, retries: int=3) -> dict[str, Any]:
    token = token or os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        return {'status': 'SKIPPED_MISSING_TELEGRAM_CONFIG', 'sent': False, 'message_id': None}
    targets: list[str] = []
    if chat_id:
        targets.append(str(chat_id))
    for value in (chat_ids or _load_telegram_chat_ids(chats_file)):
        if str(value) not in targets:
            targets.append(str(value))
    if not targets:
        env_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if env_chat_id:
            targets.append(env_chat_id)
    if not targets:
        return {'status': 'SKIPPED_MISSING_TELEGRAM_CONFIG', 'sent': False, 'message_id': None}
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    per_chat: list[dict[str, Any]] = []
    for target in targets:
        payload = json.dumps({'chat_id': target, 'text': text_message[:4096], 'parse_mode': 'HTML', 'protect_content': True}).encode('utf-8')
        last_error = ''
        outcome: Optional[dict[str, Any]] = None
        for attempt in range(1, retries + 1):
            request = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = json.loads(response.read().decode('utf-8'))
                if body.get('ok'):
                    outcome = {'chat_id': target, 'status': 'SENT', 'sent': True, 'message_id': str((body.get('result') or {}).get('message_id')), 'attempts': attempt}
                    break
                last_error = str(body.get('description') or 'Telegram returned ok=false')
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = f'{type(exc).__name__}: {exc}'
            if attempt < retries:
                time.sleep(min(4, 2 ** (attempt - 1)))
        per_chat.append(outcome or {'chat_id': target, 'status': 'ERROR_TELEGRAM_SEND_FAILED', 'sent': False, 'message_id': None, 'attempts': retries, 'error': last_error})
    any_sent = any(item['sent'] for item in per_chat)
    first_message_id = next((item['message_id'] for item in per_chat if item['sent']), None)
    return {
        'status': 'SENT' if any_sent else 'ERROR_TELEGRAM_SEND_FAILED',
        'sent': any_sent,
        'message_id': first_message_id,
        'chats_attempted': len(targets),
        'chats_sent': sum(1 for item in per_chat if item['sent']),
        'per_chat': per_chat,
    }

def format_gate(calculation: dict[str, Any]) -> dict[str, Any]:
    snapshot_format = calculation['canonical_snapshot'].get('format', {})
    data = calculation['data_gate']
    warnings = list(snapshot_format.get('warnings', []))
    same_format_n = int(data.get('pooled_n') or 0)
    if same_format_n < 20:
        warnings.append('SAME_FORMAT_SAMPLE_BELOW_20')
    if same_format_n <= 8:
        warnings.append('VERY_SMALL_SAME_FORMAT_SAMPLE')
    if data.get('history_format_override_games'):
        warnings.append('LEGACY_HISTORY_FORMAT_OVERRIDE_USED')
    return {
        'current_format': snapshot_format.get('format_key'),
        'quarter_minutes': snapshot_format.get('quarter_minutes'),
        'regulation_minutes': snapshot_format.get('regulation_minutes'),
        'format_source': snapshot_format.get('source'),
        'same_format_history_team_a_n': data.get('history_team_a_n'),
        'same_format_history_team_b_n': data.get('history_team_b_n'),
        'same_format_pooled_n': same_format_n,
        'cross_format_history_team_a_n': data.get('cross_format_team_a_n'),
        'cross_format_history_team_b_n': data.get('cross_format_team_b_n'),
        'cross_format_exact_hits_used': False,
        'cross_format_normalized_baseline_used': bool(data.get('cross_format_normalized_baseline_allowed')),
        'small_sample_cap': DEFAULT_CONFIG['caps']['small_sample'] if same_format_n < 20 else None,
        'warnings': list(dict.fromkeys(warnings)),
    }

def process_vps_match_file(
    match_path: str | Path,
    *,
    output_path: str | Path | None = None,
    zones_path: str | Path | None = None,
    db_path: str | Path = 'super_basket.sqlite3',
    mode: str = 'ACTION',
    require_gpt: bool = True,
    enable_gpt: bool = True,
    enable_telegram: bool = True,
    dry_run: bool = False,
    strict_schema: bool = False,
    checkpoint: Optional[int] = None,
    gpt_reviewer: Optional[Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = None,
    telegram_sender: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    mode = mode.upper()
    if mode not in {'ACTION', 'STRICT'}:
        raise ValueError('mode must be ACTION or STRICT')
    source_path = Path(match_path).expanduser().resolve()
    source = load_json(source_path)
    if checkpoint is not None:
        checkpoint = int(checkpoint)
        if checkpoint not in {1, 2, 3}:
            raise ValueError('checkpoint must be 1, 2 or 3')
        context = source.get('analysis_context') if isinstance(source.get('analysis_context'), dict) else {}
        source['analysis_context'] = {**context, 'trigger_checkpoint': checkpoint}
    zones = load_json(Path(zones_path).expanduser().resolve()) if zones_path else {}
    core_result = SuperBasketCalculator(deepcopy(DEFAULT_CONFIG), zones).calculate(source, dispatch_threshold=0.68, strict_schema=strict_schema)
    calculation = core_result['super_basket_calculation']
    target = Path(output_path).expanduser().resolve() if output_path else source_path.with_name(source_path.stem + '_result.json')
    store = LearningStore(db_path)
    try:
        evaluations = [apply_learning_to_evaluation(item, store, calculation, mode) for item in calculation['market_evaluations']]
        calculation['market_evaluations'] = evaluations
        selected, closest = select_one_decision(evaluations, mode)
        decision = build_decision(selected, closest, calculation, mode)
        input_usage = build_input_usage(source, calculation, selected or closest)
        if selected and input_usage['data_conflict']:
            decision['action'] = 'PASS'
            decision['deterministic_action'] = 'PASS'
            decision['status'] = 'PASS'
            decision['stake'] = '0%'
            decision['reason_codes'].insert(0, 'DATA_CONFLICT_PRECOMPUTED_VS_RAW')
            decision['blockers'].append(_blocker('DATA_CONFLICT_PRECOMPUTED_VS_RAW', 'Parser table and recomputed raw history disagree above tolerance'))
            decision['explanation_uk'] = 'Рішення PASS: готова таблиця парсера не збігається з повторним розрахунком raw history.'
            decision['main_risk_uk'] = 'Неможливо визначити, яке джерело історії є актуальним.'
        deterministic_action = decision['action']
        existing_before_review = store.get_signal(decision['signal_id']) if decision.get('signal_id') else None
        duplicate_already_sent = bool(existing_before_review and existing_before_review.get('telegram_status') == 'SENT')
        review: dict[str, Any]
        if deterministic_action == 'PASS':
            review = {'status': 'SKIPPED_DETERMINISTIC_PASS', 'approved': False, 'action': 'PASS', 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}
        elif duplicate_already_sent:
            previous_action = existing_before_review.get('final_action') or deterministic_action
            review = {'status': 'SKIPPED_DUPLICATE_ALREADY_SENT', 'approved': previous_action in {'PLAY', 'RISK'}, 'action': previous_action, 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}
        elif dry_run:
            review = {'status': 'DRY_RUN_NOT_CALLED', 'approved': True, 'action': deterministic_action, 'explanation_uk': decision['explanation_uk'], 'main_risk_uk': decision['main_risk_uk'], 'telegram_text_uk': ''}
        elif not enable_gpt:
            if require_gpt:
                review = {'status': 'REQUIRED_BUT_DISABLED', 'approved': False, 'action': 'PASS', 'explanation_uk': '', 'main_risk_uk': '', 'telegram_text_uk': ''}
            else:
                review = {'status': 'BYPASSED_BY_CONFIGURATION', 'approved': True, 'action': deterministic_action, 'explanation_uk': decision['explanation_uk'], 'main_risk_uk': decision['main_risk_uk'], 'telegram_text_uk': ''}
        else:
            reviewer = gpt_reviewer or (lambda d, c: gpt_review_decision(d, c))
            review = reviewer(decision, calculation)
        ranks = {'PASS': 0, 'RISK': 1, 'PLAY': 2}
        reviewed_action = review.get('action', 'PASS')
        if ranks.get(reviewed_action, 0) > ranks.get(deterministic_action, 0):
            reviewed_action = deterministic_action
            review['status'] = 'MODEL_UPGRADE_BLOCKED'
            review['approved'] = False
        # GPT review is recorded for context (gpt_status / explanation text) but never
        # blocks or downgrades dispatch anymore — the deterministic action always stands,
        # so RISK/PLAY signals go out to Telegram regardless of GPT approval or failure.
        if deterministic_action != 'PASS':
            decision['action'] = deterministic_action
        decision['gpt_status'] = review.get('status')
        decision['telegram_status'] = 'NOT_ATTEMPTED'
        delivery = {'status': 'SKIPPED_PASS', 'sent': False, 'message_id': None}
        duplicate = False
        if selected and decision.get('signal_id'):
            existing, duplicate = store.record_signal(decision, calculation)
            already_sent = existing.get('telegram_status') == 'SENT'
            if decision['action'] in {'PLAY', 'RISK'} and already_sent:
                delivery = {'status': 'SKIPPED_DUPLICATE_ALREADY_SENT', 'sent': False, 'message_id': existing.get('telegram_message_id')}
            elif decision['action'] in {'PLAY', 'RISK'} and dry_run:
                delivery = {'status': 'DRY_RUN_NOT_SENT', 'sent': False, 'message_id': None}
            elif decision['action'] in {'PLAY', 'RISK'} and not enable_telegram:
                delivery = {'status': 'SKIPPED_TELEGRAM_DISABLED', 'sent': False, 'message_id': None}
            elif decision['action'] in {'PLAY', 'RISK'}:
                message = build_telegram_message(decision, calculation, review)
                sender = telegram_sender or (lambda value: send_telegram_message(value))
                delivery = sender(message)
            decision['telegram_status'] = delivery['status']
            store.update_delivery(decision['signal_id'], decision['action'], review.get('status', ''), delivery['status'], delivery.get('message_id'))
        market_audit = deepcopy(calculation.get('market_audit', {}))
        market_audit.update({
            'evaluated_unique_market_sides': len(evaluations),
            'system_eligible_count': sum(item['system_action'] != 'PASS' for item in evaluations),
            'one_signal_selected': selected is not None,
        })
        evaluation_for_output = decision.pop('_evaluation', None)
        decision_probability = decision['probabilities'].get('p_final')
        probability_text = f'{float(decision_probability):.1%}' if decision_probability is not None else 'n/a'
        system = {
            'version': SYSTEM_VERSION,
            'processed_at': utc_now(),
            'input_hash': calculation['input_snapshot_hash'],
            'mode': mode,
            'status': 'OK' if not input_usage['data_conflict'] else 'DATA_CONFLICT',
            'data_gate': calculation['data_gate'],
            'format_gate': format_gate(calculation),
            'input_usage': input_usage,
            'market_audit': market_audit,
            'decision': decision,
            'decision_text': f"{decision['action']} | {decision['status']} | P_final {probability_text}",
            'gpt_review': review,
            'telegram_delivery': {**delivery, 'duplicate_signal': duplicate},
            'learning': evaluation_for_output.get('calibration') if evaluation_for_output else {'status': 'NO_MARKET'},
        }
        core_result['super_basket_system'] = system
        snapshot = calculation['canonical_snapshot']
        snapshot_quarters = snapshot.get('quarters') or []
        completed_quarters = sum(
            1 for quarter in snapshot_quarters
            if isinstance(quarter, dict)
            and quarter.get('home') is not None
            and quarter.get('away') is not None
        )
        line_reason = next((
            code for code in decision['reason_codes']
            if code in {
                'NO_SUPPORTED_REAL_LINES',
                'NO_LINE',
                'NO_ODDS',
                'ODDS_BELOW_MINIMUM',
                'SYNTHETIC_LINE',
                'UNSUPPORTED_MARKET',
            }
        ), None)
        append_verdict_log({
            'timestamp':    system['processed_at'],          # utc_now(), напр. 2026-07-19T10:15:00+00:00
            'match_id':     snapshot['match_id'],
            'match_name':   snapshot['name'],
            'checkpoint':   snapshot['stage'],                # computed live stage
            'trigger_checkpoint': snapshot.get('trigger_checkpoint'), # 1/Q1, 2/HT, 3/Q3 queue source
            'explicit_stage': snapshot.get('explicit_stage'), # сирий статус з фіда, для звірки
            'verdict':      decision['action'],                # PASS / RISK / PLAY (фінальне рішення після GPT-гейту)
            'verdict_status': decision['status'],              # людський статус, напр. "RISK ENTRY — GPT DOWNGRADE"
            'deterministic_verdict': decision['deterministic_action'],  # рішення ДО GPT-огляду (чисті формули)
            'p_final':      decision['probabilities'].get('p_final'),
            'market':       decision.get('market'),
            'description':  decision['explanation_uk'],
            'main_risk':    decision['main_risk_uk'],
            'reason_codes': decision['reason_codes'],
            'input_hash':   calculation['input_snapshot_hash'],
            'gpt_status':   system['gpt_review']['status'],
            'telegram_status': system['telegram_delivery']['status'],
            'stage_context': {
                'current_quarter': snapshot.get('current_quarter'),
                'completed_quarters': completed_quarters,
                'clock': snapshot.get('clock'),
                'elapsed_game_seconds': snapshot.get('elapsed_game_seconds'),
                'remaining_game_seconds': snapshot.get('remaining_game_seconds'),
                'score': snapshot.get('score'),
                'quarters': snapshot_quarters,
                'time_reliable': calculation.get('data_gate', {}).get('time_reliable'),
            },
            'line_diagnostics': {
                'detected_market_sides': len(calculation.get('markets_detected') or []),
                'evaluated_market_sides': len(calculation.get('market_evaluations') or []),
                'eligible_candidates': len(calculation.get('candidates') or []),
                'offers_before_deduplication': market_audit.get('offer_sides_before_deduplication'),
                'unique_market_sides': market_audit.get('unique_market_sides'),
                'duplicate_offers_removed': market_audit.get('duplicate_offers_removed'),
                'selected_market_source': (decision.get('market') or {}).get('bookmaker'),
                'empty_or_rejected_reason': line_reason,
            },
            'probabilities': deepcopy(decision.get('probabilities') or {}),
            'gates': {
                'caps': deepcopy(decision.get('caps') or []),
                'blockers': deepcopy(decision.get('blockers') or []),
            },
            'files': {
                'source': str(source_path),
                'result': str(target),
            },
        })
        save_json(target, core_result)
        store.mark_processed(calculation['input_snapshot_hash'], str(source_path), str(target), system['status'])
        return core_result
    finally:
        store.close()

def _finished_match(canonical: dict[str, Any]) -> bool:
    status = str(canonical.get('explicit_stage') or '').upper()
    long_markers = {'FINAL', 'FINISHED', 'ENDED', 'ЗАВЕРШЕНО', 'КІНЕЦЬ'}
    return canonical['elapsed_game_seconds'] >= canonical['full_game_seconds'] or bool(re.search(r'\bFT\b', status)) or any(marker in status for marker in long_markers)

def _signal_outcome_value(signal: dict[str, Any], canonical: dict[str, Any]) -> Optional[float]:
    market_type = signal['market_type']
    segment = signal['segment']
    team = signal.get('team')
    team_side = 'home' if team == canonical['home_team'] else 'away' if team == canonical['away_team'] else None
    if market_type == 'MATCH_TOTAL':
        return float(canonical['score']['total'])
    if market_type == 'TEAM_IT_MATCH' and team_side:
        return float(canonical['score'][team_side])
    if segment in {'H1', 'H2'}:
        quarters = canonical['quarters'][:2] if segment == 'H1' else canonical['quarters'][2:]
    elif segment.startswith('Q') and segment[1:].isdigit():
        quarters = [canonical['quarters'][int(segment[1:]) - 1]]
    else:
        return None
    key = team_side if team_side else 'total'
    values = [to_number(quarter.get(key)) for quarter in quarters]
    return sum(float(value) for value in values) if values and all(value is not None for value in values) else None

def settle_finished_match_file(match_path: str | Path, db_path: str | Path) -> dict[str, Any]:
    source = load_json(Path(match_path).expanduser().resolve())
    canonical = adapt_match(source, deepcopy(DEFAULT_CONFIG))
    if not _finished_match(canonical):
        raise ValueError('Match is not marked finished; settlement refused')
    store = LearningStore(db_path)
    settled: list[dict[str, Any]] = []
    try:
        rows = store.connection.execute("SELECT * FROM signals WHERE match_id=? AND result IS NULL AND final_action IN ('PLAY','RISK')", (canonical['match_id'],)).fetchall()
        for row in rows:
            signal = dict(row)
            value = _signal_outcome_value(signal, canonical)
            if value is None:
                continue
            line = float(signal['line'])
            if abs(value - line) < 1e-9:
                result = 'PUSH'
            elif signal['side'] == 'OVER':
                result = 'WIN' if value > line else 'LOSS'
            else:
                result = 'WIN' if value < line else 'LOSS'
            settled.append(store.settle(signal['signal_id'], result, value))
        return {'match_id': canonical['match_id'], 'settled_count': len(settled), 'settled': settled, 'report': store.report()}
    finally:
        store.close()

def watch_inbox(
    inbox: str | Path,
    outbox: str | Path,
    *,
    zones_path: str | Path | None,
    db_path: str | Path,
    mode: str,
    require_gpt: bool,
    enable_gpt: bool,
    enable_telegram: bool,
    poll_seconds: float,
) -> None:
    inbox_path = Path(inbox).expanduser().resolve()
    outbox_path = Path(outbox).expanduser().resolve()
    inbox_path.mkdir(parents=True, exist_ok=True)
    outbox_path.mkdir(parents=True, exist_ok=True)
    signatures: dict[str, tuple[int, int]] = {}
    stable: dict[str, int] = {}
    processed: dict[str, tuple[int, int]] = {}
    print(f'WATCHING {inbox_path} -> {outbox_path}', flush=True)
    while True:
        for path in sorted(inbox_path.glob('*.json')):
            if path.name.endswith(('_result.json', '_calculated.json')):
                continue
            try:
                stat_result = path.stat()
                signature = (stat_result.st_size, stat_result.st_mtime_ns)
            except OSError:
                continue
            key = str(path)
            if signatures.get(key) == signature:
                stable[key] = stable.get(key, 0) + 1
            else:
                signatures[key] = signature
                stable[key] = 0
            if stable[key] < 1 or processed.get(key) == signature:
                continue
            output = outbox_path / f'{path.stem}_result.json'
            try:
                result = process_vps_match_file(path, output_path=output, zones_path=zones_path, db_path=db_path, mode=mode, require_gpt=require_gpt, enable_gpt=enable_gpt, enable_telegram=enable_telegram)
                decision = result['super_basket_system']['decision']
                print(f"{utc_now()} {path.name}: {decision['action']} {decision['status']}", flush=True)
                processed[key] = signature
            except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
                print(f'{utc_now()} ERROR {path.name}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        time.sleep(max(0.5, poll_seconds))




def calculate_match_file(
    match_path: str | Path,
    *,
    zones_path: str | Path | None = None,
    output_path: str | Path | None = None,
    in_place: bool = True,
    dispatch_threshold: float | None = None,
    strict_schema: bool = False,
) -> dict[str, Any]:
    """Calculate one parser JSON and write the calculation block.

    `zones_path` is optional. If omitted, team-relative percentiles are derived
    from last35 inside the match file.
    """
    match_path = Path(match_path).expanduser().resolve()
    source = load_json(match_path)
    zones = load_json(Path(zones_path).expanduser().resolve()) if zones_path else {}
    result = SuperBasketCalculator(deepcopy(DEFAULT_CONFIG), zones).calculate(
        source,
        dispatch_threshold=dispatch_threshold,
        strict_schema=strict_schema,
    )
    target = match_path if in_place else Path(output_path).expanduser().resolve() if output_path else match_path.with_name(match_path.stem + '_calculated.json')
    save_json(target, result)
    return result


def _add_runtime_switches(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--mode', choices=['action', 'strict'], default=os.getenv('SUPER_BASKET_MODE', 'action').lower())
    parser.add_argument('--require-gpt', dest='require_gpt', action='store_true', default=env_bool('SUPER_BASKET_REQUIRE_GPT', True))
    parser.add_argument('--no-require-gpt', dest='require_gpt', action='store_false')
    parser.add_argument('--gpt', dest='enable_gpt', action='store_true', default=True)
    parser.add_argument('--no-gpt', dest='enable_gpt', action='store_false')
    parser.add_argument('--telegram', dest='enable_telegram', action='store_true', default=True)
    parser.add_argument('--no-telegram', dest='enable_telegram', action='store_false')

def _single_file_cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith('--') and '--match' in argv:
        argv.insert(0, 'run')
    parser = argparse.ArgumentParser(description='SUPER_BASKET VPS SYSTEM v5.0')
    subparsers = parser.add_subparsers(dest='command', required=True)

    run = subparsers.add_parser('run', help='Process one parser JSON')
    run.add_argument('--match', required=True)
    run.add_argument('--output')
    run.add_argument('--zones')
    run.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))
    run.add_argument('--dry-run', action='store_true', help='Calculate without external GPT/Telegram calls')
    run.add_argument('--strict-schema', action='store_true')
    run.add_argument('--checkpoint', type=int, choices=[1, 2, 3], help='Queue trigger checkpoint: 1=after Q1, 2=HT, 3=after Q3')
    _add_runtime_switches(run)

    watch = subparsers.add_parser('watch', help='Continuously process stable JSON files in an inbox')
    watch.add_argument('--inbox', required=True)
    watch.add_argument('--outbox', required=True)
    watch.add_argument('--zones')
    watch.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))
    watch.add_argument('--poll-seconds', type=float, default=2.0)
    _add_runtime_switches(watch)

    settle = subparsers.add_parser('settle', help='Settle one signal manually')
    settle.add_argument('--signal-id', required=True)
    settle.add_argument('--result', required=True, choices=['win', 'loss', 'push'])
    settle.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))

    settle_match = subparsers.add_parser('settle-match', help='Settle all active signals from a finished match JSON')
    settle_match.add_argument('--match', required=True)
    settle_match.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))

    report = subparsers.add_parser('report', help='Print SQLite learning/performance report')
    report.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))

    check = subparsers.add_parser('check-config', help='Check deployment configuration without printing secrets')
    check.add_argument('--db', default=os.getenv('SUPER_BASKET_DB', 'super_basket.sqlite3'))

    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            result = process_vps_match_file(
                args.match,
                output_path=args.output,
                zones_path=args.zones,
                db_path=args.db,
                mode=args.mode,
                require_gpt=args.require_gpt,
                enable_gpt=args.enable_gpt,
                enable_telegram=args.enable_telegram,
                dry_run=args.dry_run,
                strict_schema=args.strict_schema,
                checkpoint=args.checkpoint,
            )
            system = result['super_basket_system']
            summary = {
                'output_status': system['status'],
                'match_id': result['super_basket_calculation']['canonical_snapshot']['match_id'],
                'stage': result['super_basket_calculation']['canonical_snapshot']['stage'],
                'trigger_checkpoint': result['super_basket_calculation']['canonical_snapshot'].get('trigger_checkpoint'),
                'format': system['format_gate']['current_format'],
                'decision': deepcopy(system['decision']),
                'gpt_status': system['gpt_review']['status'],
                'telegram_status': system['telegram_delivery']['status'],
            }
            summary['decision'].pop('p_trace', None)
            summary['decision'].pop('caps', None)
            summary['decision'].pop('blockers', None)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.command == 'watch':
            watch_inbox(args.inbox, args.outbox, zones_path=args.zones, db_path=args.db, mode=args.mode, require_gpt=args.require_gpt, enable_gpt=args.enable_gpt, enable_telegram=args.enable_telegram, poll_seconds=args.poll_seconds)
        elif args.command == 'settle':
            store = LearningStore(args.db)
            try:
                settled = store.settle(args.signal_id, args.result)
                output = {'settled': settled, 'report': store.report()}
            finally:
                store.close()
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif args.command == 'settle-match':
            print(json.dumps(settle_finished_match_file(args.match, args.db), ensure_ascii=False, indent=2))
        elif args.command == 'report':
            store = LearningStore(args.db)
            try:
                print(json.dumps(store.report(), ensure_ascii=False, indent=2))
            finally:
                store.close()
        elif args.command == 'check-config':
            store = LearningStore(args.db)
            store.close()
            print(json.dumps({
                'python': sys.version.split()[0],
                'database_ready': True,
                'openai_api_key_set': bool(os.getenv('OPENAI_API_KEY')),
                'openai_model': os.getenv('OPENAI_MODEL', 'gpt-5.6'),
                'telegram_bot_token_set': bool(os.getenv('TELEGRAM_BOT_TOKEN')),
                'telegram_chat_id_set': bool(os.getenv('TELEGRAM_CHAT_ID')),
                'telegram_chats_file': os.getenv('TELEGRAM_CHATS_FILE'),
                'telegram_chats_file_chat_count': len(_load_telegram_chat_ids()),
                'require_gpt': env_bool('SUPER_BASKET_REQUIRE_GPT', True),
            }, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print('STOPPED', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_single_file_cli())
