#!/usr/bin/env python3
"""SUPER BASKET v15 — exact-score-core signal advisor.

This is an orchestration layer for two existing, independently useful engines:

1. basketball_score_predictor_v4.py — the primary score/total projection core;
2. the latest SUPER BASKET v14.x file — parser normalization, bookmaker markets,
   historical exact-line probabilities, similar-state scenarios and zones.

v15.5 keeps the exact-score anchor from v15.4 and adds a history/scenario override for soft live conflicts. It keeps market identity guards, frozen thresholds and honest forward-test logging.  The exact-score model owns
projection and direction.  History/scenario may confirm, reduce confidence or
block a weak edge, but may not reverse a meaningful score-model edge.

The file uses Python's standard library only.  Keep it next to the two base
files, or pass their paths explicitly with --score-model and --advisor.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_MODULE_CACHE: dict[tuple[str, str], Any] = {}
from xml.etree import ElementTree as ET

VERSION = "15.5.4-EXACT-ANCHOR-Q4-H73-S73-PLUS-H90-S90-PLAY-DELTA-GE0"

# Independent Telegram INFO layer.  These constants are intentionally not
# referenced by score_candidates(), action selection, stake calculation or the
# existing telegram_message().  Therefore PLAY/RISK behaviour remains frozen.
INFO_Q4_HISTORY_MIN = 0.73
INFO_Q4_SCENARIO_MIN = 0.73
INFO_Q4_ABS_DELTA_MAX = 3.0
INFO_Q4_STAGE = "Q4_LIVE"
INFO_Q4_MARKETS = frozenset({"MATCH_TOTAL", "TEAM_IT_MATCH"})
INFO_Q4_TELEGRAM_MAX_CHARS = 3800

# 90/90 layer for every route-valid real line with both history and scenario
# at 90%+.  If the exact-score direction is neutral/favourable (directional
# delta >= 0), it may promote the candidate to PLAY.  If delta is negative,
# the independent INFO behaviour remains available.
INFO_90_HISTORY_MIN = 0.90
INFO_90_SCENARIO_MIN = 0.90
INFO_90_PLAY_DIRECTIONAL_DELTA_MIN = 0.0
INFO_90_TELEGRAM_MAX_CHARS = 3800

# Email delivery uses the same notification body as Telegram, but posts it to
# the Google Apps Script web-app over HTTPS (port 443).  The secret and target
# are intentionally read from environment variables, never hard-coded.
DEFAULT_EMAIL_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycby5M0tbCNdy0ZV5DnDGJe7rCMLVY2GUvs1Nv-HRbar8mKczUeqhJa8MmaojjWgYFTdkdA/exec"
DEFAULT_MATCH_FILES_PUBLIC_BASE_URL = "http://130.0.235.172:8080/match_files"
DEFAULT_SCORE_MODEL_NAMES = (
    "basketball_score_predictor_v4.py",
    "basketball_score_predictor.py",
)
DEFAULT_CALIBRATION_NAMES = (
    "v15_2_calibration_production_485.json",
    "v15_2_calibration.json",
)
DEFAULT_ADVISOR_NAMES = (
    "super_basket_v14_3_1_base.py",
    "super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py",
    "super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER(2).py",
    "super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER.py",
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def half_up(value: float) -> int:
    return int(math.floor(max(0.0, float(value)) + 0.5))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_module(path: str | Path, module_name: str):
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    cache_key = (str(source), module_name)
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Python module: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE[cache_key] = module
    return module


def resolve_sibling(explicit: Optional[str], names: Iterable[str], anchor: Path) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    search_roots = [anchor.parent, Path.cwd(), Path("/mnt/data")]
    for root in search_roots:
        for name in names:
            candidate = (root / name).resolve()
            if candidate.exists():
                return candidate
    raise FileNotFoundError(
        "Required base file not found. Checked: "
        + ", ".join(str(root / name) for root in search_roots for name in names)
    )


@dataclass
class ScoreCandidate:
    market_id: str
    market_type: str
    segment: str
    team: Optional[str]
    side: str
    line: float
    odds: Optional[float]
    bookmaker: str
    real_line: bool
    horizon: str
    projection_home: float
    projection_away: float
    raw_projection: float
    projection: float
    projection_bias: float
    calibration_scope: str
    calibration_n: int
    edge: float
    sigma: float
    edge_z: float
    p_score_line: float
    p_residual_direction: float
    p_score_calibrated: float
    p_history: float
    p_scenario: float
    p_final: float
    break_even: Optional[float]
    expected_value: Optional[float]
    action: str
    status: str
    stake: str
    blockers: list[str]
    confirmations: list[str]
    historical_zones: list[dict[str, Any]]
    soft_conflict: Optional[str]
    history_override: Optional[str]
    hot_continuation: bool
    source_evaluation: dict[str, Any]


STAGE_ROUTE = {
    "PRE_MATCH": {
        "segments": {"Q1", "Q2", "Q3", "Q4", "H1", "H2", "MATCH"},
        "description": "усі доступні загальні та індивідуальні лінії",
    },
    "Q1_LIVE": {
        "segments": {"Q1", "H1"},
        "description": "лише 1-ша чверть і 1-ша половина",
    },
    "POST_Q1": {
        "segments": {"Q2", "H1"},
        "description": "2-га чверть або 1-ша половина",
    },
    "Q2_LIVE": {
        "segments": {"Q2", "H1"},
        "description": "2-га чверть або 1-ша половина",
    },
    "HT": {
        "segments": {"Q3", "MATCH"},
        "description": "3-тя чверть або матч",
    },
    "Q3_LIVE": {
        "segments": {"Q3", "MATCH"},
        "description": "3-тя чверть або матч",
    },
    "POST_Q3": {
        "segments": {"Q4", "H2", "MATCH"},
        "description": "4-та чверть, 2-га половина або матч; для UNDER H2 має пріоритет над матчем",
    },
    "Q4_LIVE": {
        "segments": {"MATCH"},
        "description": "лише загальний або індивідуальний тотал матчу",
    },
}

MARKET_LABELS = {
    "MATCH_TOTAL": "Тотал матчу",
    "TEAM_IT_MATCH": "Індивідуальний тотал матчу",
    "H1_TOTAL": "Тотал 1-ї половини",
    "TEAM_IT_H1": "Індивідуальний тотал 1-ї половини",
    "H2_TOTAL": "Тотал 2-ї половини",
    "TEAM_IT_H2": "Індивідуальний тотал 2-ї половини",
    "CURRENT_QUARTER_TOTAL": "Тотал чверті",
    "CURRENT_QUARTER_TEAM_IT": "Індивідуальний тотал чверті",
}


def route_allows(stage: str, market_type: str, segment: str) -> bool:
    route = STAGE_ROUTE.get(stage)
    if not route:
        return False
    if segment not in route["segments"]:
        return False
    if stage == "Q4_LIVE" and market_type not in {"MATCH_TOTAL", "TEAM_IT_MATCH"}:
        return False
    return True


def horizon_for_market(market_type: str, segment: str) -> str:
    if market_type in {"MATCH_TOTAL", "TEAM_IT_MATCH"}:
        return "FINAL"
    if market_type in {"H1_TOTAL", "TEAM_IT_H1"}:
        return "H1"
    if market_type in {"H2_TOTAL", "TEAM_IT_H2"}:
        return "H2"
    if market_type in {"CURRENT_QUARTER_TOTAL", "CURRENT_QUARTER_TEAM_IT"}:
        return segment
    return segment


def forecast_horizon(score_forecast: Any, horizon: str) -> Optional[dict[str, Any]]:
    values = getattr(score_forecast, "horizons", {}) or {}
    key = "FINAL" if horizon in {"MATCH", "REGULATION_FT", "FINAL"} else horizon
    value = values.get(key)
    if not isinstance(value, dict) or value.get("status") != "PREDICTED":
        return None
    if num(value.get("home")) is None or num(value.get("away")) is None:
        return None
    return value


def calibration_for_horizon(score_forecast: Any, horizon: str) -> tuple[dict[str, Any], dict[str, Any]]:
    key = "FINAL" if horizon in {"MATCH", "REGULATION_FT", "FINAL"} else horizon
    cal = (getattr(score_forecast, "horizon_calibration", {}) or {}).get(key, {})
    direction = (getattr(score_forecast, "horizon_direction_calibration", {}) or {}).get(key, {})
    # Some predictor builds store the current quarter also under NEXT_QUARTER.
    if not cal:
        for storage_key, value in (getattr(score_forecast, "horizons", {}) or {}).items():
            if value.get("quarter") == key:
                cal = (getattr(score_forecast, "horizon_calibration", {}) or {}).get(storage_key, {})
                direction = (getattr(score_forecast, "horizon_direction_calibration", {}) or {}).get(storage_key, {})
                break
    return cal, direction


def empirical_sigma(calibration: dict[str, Any], is_team_market: bool) -> float:
    mae = num(calibration.get("team_mae"))
    p80 = num(calibration.get("both_teams_p80_error"))
    estimates: list[float] = []
    if mae is not None and mae > 0:
        estimates.append(mae / math.sqrt(2.0 / math.pi))
    if p80 is not None and p80 > 0:
        estimates.append(p80 / 1.2815515655446004)
    team_sigma = max(estimates) if estimates else 10.0
    sigma = team_sigma if is_team_market else team_sigma * math.sqrt(2.0)
    return clamp(sigma, 2.0 if is_team_market else 3.0, 32.0)


def load_predictive_calibration(path: Optional[str | Path], anchor: Optional[Path] = None) -> dict[str, Any]:
    """Load a point-in-time predictive residual calibration.

    The config stores residual means and predictive residual SDs.  It is kept
    separate from the score model so a frozen pre-cutoff calibration can be
    used for honest temporal validation and a refitted config for production.
    """
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    env = os.getenv("SUPER_BASKET_V15_CALIBRATION")
    if env:
        candidates.append(Path(env).expanduser())
    root = anchor or Path(__file__).resolve()
    for name in DEFAULT_CALIBRATION_NAMES:
        candidates.extend([root.parent / name, Path.cwd() / name, Path('/mnt/data') / name])
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.exists():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            payload["_path"] = str(candidate)
            return payload
    return {"schema": "FALLBACK_EMPIRICAL_SIGMA", "bias_strength": 0.0, "sigma_inflation": 1.0}


def _cal_row(config: dict[str, Any], group: str, key: str) -> Optional[dict[str, Any]]:
    value = (config.get(group) or {}).get(key)
    return value if isinstance(value, dict) else None


def predictive_calibration(
    config: dict[str, Any], *, stage: str, mode: str, horizon: str,
    is_team: bool, team_is_home: Optional[bool], fallback_sigma: float,
) -> dict[str, Any]:
    exact = _cal_row(config, "exact", f"{stage}|{mode}|{horizon}")
    fallback = _cal_row(config, "mode_horizon", f"{mode}|{horizon}") or _cal_row(config, "horizon", horizon)
    if not exact and not fallback:
        return {"bias": 0.0, "sigma": fallback_sigma, "scope": "LEGACY_FALLBACK", "n": 0}
    exact = exact or fallback or {}
    fallback = fallback or exact
    n = int(num(exact.get("n"), 0.0) or 0)
    shrink_k = max(1.0, num(config.get("shrink_k"), 20.0) or 20.0)
    weight = n / (n + shrink_k)
    if is_team:
        bias_key = "bias_home" if team_is_home else "bias_away"
        sigma_key = "sigma_team"
        floor = 2.5
    else:
        bias_key = "bias_total"
        sigma_key = "sigma_total"
        floor = 3.5
    exact_bias = num(exact.get(bias_key), 0.0) or 0.0
    fallback_bias = num(fallback.get(bias_key), 0.0) or 0.0
    raw_bias = weight * exact_bias + (1.0 - weight) * fallback_bias
    exact_sigma = max(floor, num(exact.get(sigma_key), fallback_sigma) or fallback_sigma)
    fallback_sigma_value = max(floor, num(fallback.get(sigma_key), fallback_sigma) or fallback_sigma)
    sigma = math.sqrt(max(floor * floor, weight * exact_sigma ** 2 + (1.0 - weight) * fallback_sigma_value ** 2))
    sigma *= max(1.0, num(config.get("sigma_inflation"), 1.2) or 1.2)
    bias = raw_bias * clamp(num(config.get("bias_strength"), 0.5) or 0.5, 0.0, 1.0)
    return {
        "bias": bias, "raw_bias": raw_bias, "sigma": sigma,
        "scope": "STAGE_MODE_HORIZON_SHRUNK" if exact else "MODE_HORIZON_FALLBACK",
        "n": n, "weight": weight,
    }


def residual_direction_probability(direction: dict[str, Any], side: str) -> float:
    if direction.get("status") != "AVAILABLE":
        return 0.5
    above = num(direction.get("above_share"), 0.5) or 0.5
    below = num(direction.get("below_share"), 0.5) or 0.5
    equal = num(direction.get("equal_share"), max(0.0, 1.0 - above - below)) or 0.0
    return clamp((above if side == "OVER" else below) + 0.5 * equal, 0.05, 0.95)


def projection_value(
    horizon: dict[str, Any], market_type: str, team: Optional[str], home_team: str, away_team: str
) -> Optional[float]:
    home = num(horizon.get("home"))
    away = num(horizon.get("away"))
    if home is None or away is None:
        return None
    if market_type in {"MATCH_TOTAL", "H1_TOTAL", "H2_TOTAL", "CURRENT_QUARTER_TOTAL"}:
        return home + away
    if market_type in {"TEAM_IT_MATCH", "TEAM_IT_H1", "TEAM_IT_H2", "CURRENT_QUARTER_TEAM_IT"}:
        if team == home_team:
            return home
        if team == away_team:
            return away
    return None


def source_probability(item: dict[str, Any], block: str, key: str, default: float = 0.5) -> float:
    value = num((item.get(block) or {}).get(key))
    return clamp(value if value is not None else default, 0.01, 0.99)


def real_line(item: dict[str, Any]) -> bool:
    if bool(item.get("is_reference_line")) or bool(item.get("is_model_line")):
        return False
    raw = item.get("raw_line_row") if isinstance(item.get("raw_line_row"), dict) else {}
    return bool(raw.get("is_real_bookmaker_line", True)) and num(item.get("line")) is not None


def market_viable(item: dict[str, Any]) -> bool:
    if num(item.get("line")) is None:
        return False
    if item.get("market_type") in {None, "UNSUPPORTED"}:
        return False
    # Never revive a line that the audited parser/router already identified as
    # structurally invalid. This is essential for low quarter-total clusters
    # that are actually team totals without a team identifier.
    if item.get("eligible_market") is False:
        return False
    router = item.get("router") if isinstance(item.get("router"), dict) else {}
    if str(router.get("status") or "").upper() == "BLOCK" and str(router.get("reason") or "").upper() == "INVALID_MARKET":
        return False
    hard_parser = {
        "NO_LINE", "UNSUPPORTED_MARKET", "UNKNOWN_QUARTER", "INVALID_QUARTER",
        "PAST_QUARTER", "FUTURE_QUARTER", "NO_CURRENT_QUARTER",
        "AMBIGUOUS_QUARTER_TOTAL_LOOKS_TEAM_IT_NO_TEAM_ID",
        "AMBIGUOUS_TEAM_TOTAL_NO_TEAM_ID", "TEAM_IT_NO_TEAM_ID",
    }
    return not hard_parser.intersection(set(item.get("parser_issues") or []))



def _stat_block(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("stat_comparison", "stat_gate", "stats_context"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _soft_live_conflict(item: dict[str, Any], side: str) -> Optional[str]:
    """Return a conflict that may be overridden by very strong history/scenario.

    Parser/schema/market identity errors are handled earlier and are never soft.
    FAKE profiles, stat-gate AGAINST and Q4 context are evidence conflicts rather
    than data-integrity failures, so v15.5 may downgrade them to RISK instead of
    blindly forcing PASS when the exact-score anchor and independent history agree.
    """
    stat = _stat_block(item)
    side = str(side or "").upper()
    if side == "OVER" and bool(stat.get("fake_over")):
        return "FAKE_OVER"
    if side == "UNDER" and bool(stat.get("fake_under")):
        return "FAKE_UNDER"
    status = str(stat.get("stat_gate_status") or "").upper()
    if status in {"AGAINST", "CONFLICT"}:
        return f"STAT_GATE_{status}"
    q4 = item.get("q4_context") if isinstance(item.get("q4_context"), dict) else {}
    q4_status = str(q4.get("context_gate") or q4.get("status") or "").upper()
    if q4_status in {"BLOCK", "AGAINST", "CONFLICT", "FAIL"}:
        return f"Q4_{q4_status}"
    return None


def _history_override_level(*, edge: float, p_history: float, p_scenario: float) -> Optional[str]:
    if edge < 4.0 or p_scenario < 0.60:
        return None
    if p_history >= 0.90:
        return "STRONG_90_PLUS"
    if p_history >= 0.75:
        return "SUPPORTED_75_PLUS"
    return None


def _parse_quarter_pair(value: Any) -> tuple[Optional[float], Optional[float]]:
    if isinstance(value, dict):
        h = num(value.get("home", value.get("h", value.get("team_a"))))
        a = num(value.get("away", value.get("a", value.get("team_b"))))
        return h, a
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return num(value[0]), num(value[1])
    if isinstance(value, str):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*[:\-]\s*(\d+(?:[.,]\d+)?)", value)
        if m:
            return num(m.group(1)), num(m.group(2))
    return None, None


def observed_quarters(raw: dict[str, Any]) -> list[tuple[Optional[float], Optional[float]]]:
    candidates: list[Any] = []
    match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    for owner in (match, meta, raw):
        for key in ("quarters", "periods", "quarter_scores"):
            if owner.get(key) is not None:
                candidates.append(owner.get(key))
    for source in candidates:
        rows=[]
        if isinstance(source, dict):
            for i in range(1,5):
                value = source.get(f"q{i}")
                if value is None:
                    value = source.get(str(i))
                rows.append(_parse_quarter_pair(value))
        elif isinstance(source, list):
            rows=[_parse_quarter_pair(v) for v in source[:4]]
        else:
            continue
        if any(h is not None and a is not None for h,a in rows):
            return rows + [(None,None)]*(4-len(rows))
    return [(None,None)]*4


def _hot_continuation_under(item: dict[str, Any], raw: Optional[dict[str, Any]]) -> bool:
    """Protect UNDER from the user's observed 2-0/3-0 hot continuation pattern."""
    if not isinstance(raw, dict):
        return False
    completed=[pair for pair in observed_quarters(raw) if pair[0] is not None and pair[1] is not None]
    winners=[]
    for h,a in completed:
        if h > a:
            winners.append("H")
        elif a > h:
            winners.append("A")
        else:
            winners.append("T")
    one_team_sweep = len(winners) >= 2 and winners[-1] != "T" and winners.count(winners[-1]) >= 2
    stat=_stat_block(item)
    ind=stat.get("indicators") if isinstance(stat.get("indicators"), dict) else {}
    over_score=int(num(stat.get("over_gate_score"), 0) or 0)
    hot_eff=bool(ind.get("score_or_efg_high"))
    hot_volume=bool(ind.get("volume_high") or ind.get("fta_high") or ind.get("orb_high"))
    over_live = over_score >= 3 or str(stat.get("over_gate_status") or "").upper() == "CONFIRMED"
    return bool(one_team_sweep and ((hot_eff and hot_volume) or over_live))


def _identity_text(raw: dict[str, Any]) -> str:
    pieces=[]
    def take(obj: Any, depth: int=0):
        if depth > 3 or not isinstance(obj, dict):
            return
        for key,value in obj.items():
            lk=str(key).lower()
            if lk in {"name","title","league","competition","tournament","home_team","away_team","home","away","team_a","team_b"}:
                if isinstance(value, (str,int,float)):
                    pieces.append(str(value))
                elif isinstance(value, dict):
                    for nk in ("name","title"):
                        if value.get(nk) is not None:
                            pieces.append(str(value.get(nk)))
            if lk in {"match","meta","event","competition","league","tournament"} and isinstance(value, dict):
                take(value, depth+1)
    take(raw)
    return " | ".join(pieces)


def detected_youth_age(raw: dict[str, Any]) -> Optional[int]:
    text=_identity_text(raw)
    ages=[]
    for pat in (r"(?i)(?:^|[^A-ZА-Я0-9])(?:U|Ю)\s*[-_ ]?(1[4-9]|2[0-3])(?:[^0-9]|$)",
                r"(?i)UNDER\s*[-_ ]?(1[4-9]|2[0-3])", r"(?i)ДО\s*[-_ ]?(1[4-9]|2[0-3])"):
        ages.extend(int(x) for x in re.findall(pat,text))
    return min(ages) if ages else None


def is_quarter_market(market_type: str, segment: str) -> bool:
    return market_type in {"CURRENT_QUARTER_TOTAL", "CURRENT_QUARTER_TEAM_IT"} or str(segment).upper() in {"Q1","Q2","Q3","Q4"}


def v15_probability(
    p_score_line: float,
    p_residual: float,
    p_history: float,
    p_scenario: float,
    edge_z: float,
) -> tuple[float, float]:
    # v15.2 treats the line probability as a posterior-predictive probability:
    # the residual mean has already shifted the projection and predictive SD
    # already includes process noise.  Residual direction is diagnostic only,
    # preventing the same calibration evidence from being counted twice.
    p_score_calibrated = p_score_line
    p_final = 0.80 * p_score_calibrated + 0.12 * p_history + 0.08 * p_scenario
    information = clamp(abs(edge_z) / 0.55, 0.35, 1.0)
    p_final = 0.5 + (p_final - 0.5) * information
    return clamp(p_score_calibrated, 0.01, 0.99), clamp(p_final, 0.01, 0.99)


def classify_candidate(
    *, p_final: float, p_score: float, edge: float, edge_z: float, odds: Optional[float],
    p_history: float, p_scenario: float, p_residual: float, real: bool,
    stage: str, market_type: str, segment: str, side: str,
    source_evaluation: Optional[dict[str, Any]] = None,
    raw_match: Optional[dict[str, Any]] = None,
    age_blocked: bool = False,
) -> tuple[str, str, str, list[str], list[str], Optional[float], Optional[float], Optional[str], Optional[str], bool]:
    """v15.5 exact-anchor final selector.

    Base score probabilities are unchanged.  This layer only decides whether a
    real bookmaker line is actionable.  Exact-score delta >=4 remains the
    default money gate, except the explicit 90/90 override: when both history
    and scenario are >=90% and directional delta >=0, the candidate may be
    promoted to PLAY without reversing the score-model direction.  FAKE/stat/Q4
    conflicts can be overridden only by strong independent historical support
    and then the result is capped at RISK.  HOT CONTINUATION remains a hard
    UNDER protection.
    """
    blockers: list[str] = []
    confirmations: list[str] = []
    break_even = 1.0 / odds if odds and odds > 1.0 else None
    ev = p_final * odds - 1.0 if odds and odds > 1.0 else None
    item = source_evaluation or {}
    soft_conflict = _soft_live_conflict(item, side)
    override = _history_override_level(edge=edge, p_history=p_history, p_scenario=p_scenario)
    hot_continuation = side == "UNDER" and _hot_continuation_under(item, raw_match)

    if p_history >= 0.90:
        confirmations.append("історія 90%+ підтримує напрямок")
    elif p_history >= 0.75:
        confirmations.append("сильна історія 75%+ підтримує напрямок")
    elif p_history >= 0.60:
        confirmations.append("історія підтримує напрямок")
    else:
        blockers.append("P_history нижче 60%")
    if p_scenario >= 0.60:
        confirmations.append("схожі сценарії >=60% підтримують напрямок")
    else:
        blockers.append("P_scenario нижче 60%")
    if p_residual >= 0.58:
        confirmations.append("історичний знак похибки підтримує напрямок")
    elif p_residual <= 0.42:
        blockers.append("історичний знак похибки частіше був протилежним")

    if age_blocked:
        blockers.append("U16/U17 тимчасово заблоковані; сигнали дозволені від U18")
        return "PASS", "PASS — ВІКОВИЙ ФІЛЬТР U18+", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
    if not real:
        blockers.append("немає реальної букмекерської лінії")
        return "PASS", "PASS — НЕМАЄ РЕАЛЬНОЇ ЛІНІЇ", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
    if odds is None:
        blockers.append("немає коефіцієнта")
        return "PASS", "PASS — НЕМАЄ КОЕФІЦІЄНТА", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
    if odds < 1.44:
        blockers.append("коефіцієнт нижче мінімуму 1.44")
        return "PASS", "PASS — НИЗЬКИЙ КОЕФІЦІЄНТ", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    # 90/90 override requested by the user.  `edge` is already directional:
    # OVER -> projection - line; UNDER -> line - projection.  Therefore
    # edge >= 0 means the score model is exactly on the line or points to the
    # same side as the 90/90 history+scenario signal.
    info_90_play = (
        p_history + 1e-12 >= INFO_90_HISTORY_MIN
        and p_scenario + 1e-12 >= INFO_90_SCENARIO_MIN
        and edge + 1e-12 >= INFO_90_PLAY_DIRECTIONAL_DELTA_MIN
    )
    if info_90_play:
        if hot_continuation:
            blockers.append("HOT CONTINUATION: 2-0/3-0 по чвертях + сильний over-live профіль; UNDER заблоковано")
            return "PASS", "PASS — HOT CONTINUATION ПРОТИ UNDER", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
        confirmations.append("HISTORY 90% + SCENARIO 90% і directional delta >= 0")
        if soft_conflict:
            blockers.append(f"live conflict: {soft_conflict}")
            confirmations.append("90/90 override дозволяє сигнал, але soft live conflict обмежує його рівнем RISK")
            return "RISK", "RISK — HISTORY/SCENARIO 90/90 + DELTA >= 0, SOFT CONFLICT", "1% session risk-budget", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
        return "PLAY", "PLAY — HISTORY/SCENARIO 90/90 + DELTA >= 0", "3% session risk-budget", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    if edge < 4.0:
        blockers.append(f"exact-score delta {edge:.1f} < 4.0")
        return "PASS", "PASS — DELTA МЕНШЕ 4", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
    if p_history < 0.60 or p_scenario < 0.60:
        return "PASS", "PASS — НЕМАЄ ПОДВІЙНОГО HISTORY+SCENARIO ПІДТВЕРДЖЕННЯ", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
    if hot_continuation:
        blockers.append("HOT CONTINUATION: 2-0/3-0 по чвертях + сильний over-live профіль; UNDER заблоковано")
        return "PASS", "PASS — HOT CONTINUATION ПРОТИ UNDER", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    if soft_conflict:
        blockers.append(f"live conflict: {soft_conflict}")
        if override is None:
            return "PASS", f"PASS — {soft_conflict} БЕЗ СИЛЬНОГО HISTORY OVERRIDE", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation
        confirmations.append(f"{override}: exact delta >=4 + history/scenario дозволяють не робити автоматичний PASS")

    surcharge = 0.0
    if stage == "Q1_LIVE" and market_type in {"H1_TOTAL", "TEAM_IT_H1"}:
        surcharge = 0.025
    elif stage == "Q2_LIVE" and market_type in {"H1_TOTAL", "TEAM_IT_H1"}:
        surcharge = 0.015

    quarter_cap_risk = is_quarter_market(market_type, segment) and edge < 7.0
    conflict_cap_risk = soft_conflict is not None

    # Keep v15.3 score/EV thresholds, but add user's exact-delta and H/S gates.
    if (
        not quarter_cap_risk and not conflict_cap_risk
        and p_final >= 0.84 + surcharge and p_score >= 0.76 and edge_z >= 0.85
        and ev is not None and ev >= 0.10 and odds >= 1.50
    ):
        return "PLAY", "MAIN PLAY — EXACT SCORE + HISTORY + SCENARIO", "5% session risk-budget", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    if (
        not quarter_cap_risk and not conflict_cap_risk
        and p_final >= 0.76 + surcharge and p_score >= 0.67 and edge_z >= 0.52
        and ev is not None and ev >= 0.035 and odds >= 1.48
    ):
        return "PLAY", "PLAY — EXACT SCORE + HISTORY + SCENARIO", "3% session risk-budget", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    # User rule: with delta >=4 and H/S >=60%, permit a controlled RISK even
    # when predictive p_final is below the old v15.3 risk gate.  A soft conflict
    # is never promoted above RISK.
    risk_floor_ok = p_score >= 0.55 and edge_z > 0 and ev is not None and ev > -0.08
    if risk_floor_ok:
        if quarter_cap_risk:
            confirmations.append("чверть з delta 4-6.9 обмежена рівнем RISK")
        if conflict_cap_risk:
            confirmations.append("soft live conflict обмежує сигнал рівнем RISK")
        return "RISK", "RISK — EXACT DELTA >=4 + HISTORY/SCENARIO >=60%", "1% session risk-budget", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation

    if p_score < 0.55:
        blockers.append("P_score нижче мінімального exact-anchor risk floor")
    if ev is not None and ev <= -0.08:
        blockers.append("ціна лінії занадто погана навіть для RISK")
    return "PASS", "PASS — EXACT НАПРЯМОК Є, АЛЕ ЯКОСТІ НЕДОСТАТНЬО", "0%", blockers, confirmations, break_even, ev, soft_conflict, override, hot_continuation


def candidate_rank(candidate: ScoreCandidate, stage: str) -> float:
    # Select the highest calibrated probability first.  v15.1 sometimes chose
    # any active PLAY/RISK even when another market in the same file had a
    # materially better score-core probability.
    post_q3_under_h2 = 0.01 if stage == "POST_Q3" and candidate.side == "UNDER" and candidate.segment == "H2" else 0.0
    return candidate.p_final + 0.03 * candidate.p_score_calibrated + 0.005 * clamp(candidate.edge_z, -2.0, 3.0) + post_q3_under_h2


def _zone_values(advisor_module: Any, canonical: dict[str, Any], item: dict[str, Any]) -> list[float]:
    market_type = str(item.get("market_type") or "")
    history = canonical.get("history") if isinstance(canonical.get("history"), dict) else {}
    values: list[float] = []
    segment_value_fn = getattr(advisor_module, "_segment_value", None)
    if not callable(segment_value_fn):
        return values
    if market_type in {"MATCH_TOTAL", "H1_TOTAL", "H2_TOTAL", "CURRENT_QUARTER_TOTAL"}:
        seen: set[str] = set()
        for pool_name in ("team_a", "team_b"):
            for game in history.get(pool_name) or []:
                game_id = str(game.get("id") or "")
                if game_id and game_id in seen:
                    continue
                if game_id:
                    seen.add(game_id)
                value = num(segment_value_fn(game, item))
                if value is not None:
                    values.append(float(value))
        return values
    if market_type in {"TEAM_IT_MATCH", "TEAM_IT_H1", "TEAM_IT_H2", "CURRENT_QUARTER_TEAM_IT"}:
        team = item.get("team")
        home = canonical.get("home_team")
        away = canonical.get("away_team")
        if team not in {home, away}:
            return values
        opponent = away if team == home else home
        own_pool = history.get("team_a") if team == home else history.get("team_b")
        opponent_pool = history.get("team_b") if team == home else history.get("team_a")
        for game in own_pool or []:
            value = num(segment_value_fn(game, item, team))
            if value is not None:
                values.append(float(value))
        for game in opponent_pool or []:
            value = num(segment_value_fn(game, item, opponent, opponent_allowed=True))
            if value is not None:
                values.append(float(value))
        return values
    return values


def _hit_rate(values: list[float], line: float, side: str) -> tuple[int, int, int, float]:
    wins = sum(value > line for value in values) if side == "OVER" else sum(value < line for value in values)
    pushes = sum(value == line for value in values)
    losses = len(values) - wins - pushes
    return wins, losses, pushes, wins / len(values) if values else 0.0


def _nearest_zone(values: list[float], side: str, target: float = 0.80) -> Optional[dict[str, Any]]:
    if len(values) < 10:
        return None
    if side == "OVER":
        candidate_lines = sorted({round(value - 0.5, 1) for value in values})
        eligible = []
        for line in candidate_lines:
            wins, losses, pushes, rate = _hit_rate(values, line, side)
            if rate >= target:
                eligible.append((line, wins, losses, pushes, rate))
        chosen = max(eligible, default=None, key=lambda row: row[0])
    else:
        candidate_lines = sorted({round(value + 0.5, 1) for value in values})
        eligible = []
        for line in candidate_lines:
            wins, losses, pushes, rate = _hit_rate(values, line, side)
            if rate >= target:
                eligible.append((line, wins, losses, pushes, rate))
        chosen = min(eligible, default=None, key=lambda row: row[0])
    if chosen is None:
        return None
    line, wins, losses, pushes, rate = chosen
    return {"available": True, "kind": "HISTORICAL_80_ZONE", "side": side, "line": line,
            "probability": rate, "hits": wins, "losses": losses, "pushes": pushes,
            "n": len(values), "target_probability": target, "validated_for_money": False,
            "method": "same-format empirical history; informational only"}


def historical_zones(advisor_module: Any, canonical: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    values = _zone_values(advisor_module, canonical, item)
    if not values:
        return [{"available": False, "side": side, "reason": "INSUFFICIENT_HISTORY", "n": 0}
                for side in ("OVER", "UNDER")]
    output: list[dict[str, Any]] = []
    current_line = float(item.get("line"))
    for side in ("OVER", "UNDER"):
        wins, losses, pushes, rate = _hit_rate(values, current_line, side)
        output.append({"available": True, "kind": "CURRENT_LINE_HISTORY", "side": side,
                       "line": current_line, "probability": rate, "hits": wins,
                       "losses": losses, "pushes": pushes, "n": len(values),
                       "validated_for_money": False, "method": "same-format empirical history"})
        zone = _nearest_zone(values, side, 0.80)
        if zone:
            output.append(zone)
    return output




def _market_integrity_audit(calculation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Detect upstream lines whose market identity was lost by the parser.

    The legacy parser occasionally writes team quarter totals into
    ``quarter_total`` and invents a Q scope.  v15.2.1 never guesses the team:
    ambiguous rows are blocked for money until the JSON carries an explicit
    team and market type.
    """
    evaluations = [row for row in (calculation.get("market_evaluations") or []) if isinstance(row, dict)]
    unique: dict[str, dict[str, Any]] = {}
    for row in evaluations:
        raw = row.get("raw_line_row") if isinstance(row.get("raw_line_row"), dict) else {}
        raw_id = str(raw.get("id") or row.get("market_id") or "")
        if not raw_id:
            continue
        unique.setdefault(raw_id, {
            "raw_id": raw_id,
            "market_type": str(row.get("market_type") or ""),
            "segment": str(row.get("segment") or ""),
            "line": num(row.get("line")),
            "raw": raw,
        })

    quarter = [row for row in unique.values() if row["market_type"] == "CURRENT_QUARTER_TOTAL" and row["line"] is not None]
    low = [row for row in quarter if float(row["line"]) < 30.0]
    normal = [row for row in quarter if float(row["line"]) >= 32.0]
    issues: dict[str, dict[str, Any]] = {}

    for row in low:
        line = float(row["line"])
        evidence: list[str] = []
        matched_totals: list[float] = []
        # Same-scope low/high split: classic lost team identity.
        for high in normal:
            high_line = float(high["line"])
            if row["segment"] == high["segment"] and high_line - line >= 8.0 and line <= 0.72 * high_line:
                evidence.append("LOW_AND_NORMAL_TOTAL_IN_SAME_QUARTER_SCOPE")
                matched_totals.append(high_line)
        # Cross-scope arithmetic check catches parser-created fake Q2/Q3 scopes:
        # two team totals add up to the real combined quarter total.
        for high in normal:
            high_line = float(high["line"])
            if abs(2.0 * line - high_line) <= 4.0:
                evidence.append("LOW_LINE_IS_APPROX_HALF_OF_COMBINED_QUARTER_TOTAL")
                matched_totals.append(high_line)
            for other in low:
                if other["raw_id"] == row["raw_id"]:
                    continue
                if abs(line + float(other["line"]) - high_line) <= 2.5:
                    evidence.append("TWO_LOW_LINES_SUM_TO_COMBINED_QUARTER_TOTAL")
                    matched_totals.append(high_line)
        if evidence:
            issues[row["raw_id"]] = {
                "code": "AMBIGUOUS_QUARTER_TOTAL_IS_LIKELY_TEAM_IT",
                "action": "BLOCK_MONEY_NO_TEAM_ID",
                "line": line,
                "reported_segment": row["segment"],
                "matched_combined_totals": sorted(set(matched_totals)),
                "evidence": sorted(set(evidence)),
                "required_json_fields": ["market_type", "segment", "team_side", "team_name", "raw_market_name"],
            }
    return issues


def _raw_market_identity_is_explicit(item: dict[str, Any]) -> bool:
    raw = item.get("raw_line_row") if isinstance(item.get("raw_line_row"), dict) else {}
    # Generated descriptions/_type are not source evidence.  These fields must
    # be copied from the bookmaker response before normalization.
    semantic_keys = (
        "raw_market_name", "raw_market_title", "market_name", "market_title",
        "raw_selection_name", "selection_name", "team", "team_name", "team_side",
        "source_market_id", "source_outcome_id",
    )
    return any(raw.get(key) not in (None, "") for key in semantic_keys)


def _item_integrity_issue(
    item: dict[str, Any], integrity: dict[str, dict[str, Any]],
    *, raw_projection: Optional[float] = None, line: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    raw = item.get("raw_line_row") if isinstance(item.get("raw_line_row"), dict) else {}
    raw_id = str(raw.get("id") or "")
    if raw_id and raw_id in integrity:
        return integrity[raw_id]
    market_type = str(item.get("market_type") or "")
    # A combined full-match line below the parser's own declared range, with a
    # gigantic model gap and no preserved source label, is not safe to bet.
    # It may be a team/period total stored in match_total.
    if market_type == "MATCH_TOTAL" and line is not None and raw_projection is not None:
        if float(line) < 120.0 and abs(float(raw_projection) - float(line)) >= 25.0 and not _raw_market_identity_is_explicit(item):
            return {
                "code": "LOW_MATCH_TOTAL_WITHOUT_SOURCE_MARKET_IDENTITY",
                "action": "BLOCK_MONEY_UNTIL_PARSER_FIX",
                "line": float(line),
                "raw_projection": float(raw_projection),
                "required_json_fields": ["raw_market_name", "raw_selection_name", "market_type", "team_side", "team_name"],
            }
    return None


def score_candidates(
    *, advisor_module: Any, legacy_result: dict[str, Any], score_forecast: Any,
    canonical_override: Optional[dict[str, Any]] = None,
    calibration_config: Optional[dict[str, Any]] = None,
    raw_match: Optional[dict[str, Any]] = None,
    age_blocked: bool = False,
) -> tuple[list[ScoreCandidate], list[dict[str, Any]]]:
    calculation = legacy_result.get("super_basket_calculation") or {}
    canonical = canonical_override or calculation.get("canonical_snapshot") or {}
    home_team = str(canonical.get("home_team") or getattr(score_forecast, "prematch_prior", {}).get("home_name") or "")
    away_team = str(canonical.get("away_team") or getattr(score_forecast, "prematch_prior", {}).get("away_name") or "")
    stage = str(getattr(score_forecast, "stage", "") or "")
    mode = str(getattr(score_forecast, "mode", "") or "")
    calibration_config = calibration_config or {}
    rejected: list[dict[str, Any]] = []
    output: list[ScoreCandidate] = []
    integrity = _market_integrity_audit(calculation)

    for item in calculation.get("market_evaluations") or []:
        if not isinstance(item, dict) or not market_viable(item):
            continue
        market_type = str(item.get("market_type") or "")
        segment = str(item.get("segment") or "")
        if not route_allows(stage, market_type, segment):
            rejected.append({"market_id": item.get("market_id"), "reason": "V15_STAGE_ROUTE", "stage": stage, "segment": segment})
            continue
        horizon_name = horizon_for_market(market_type, segment)
        horizon = forecast_horizon(score_forecast, horizon_name)
        if not horizon:
            rejected.append({"market_id": item.get("market_id"), "reason": "NO_SCORE_FORECAST_FOR_HORIZON", "horizon": horizon_name})
            continue
        raw_projection = projection_value(horizon, market_type, item.get("team"), home_team, away_team)
        line = num(item.get("line"))
        if raw_projection is None or line is None:
            continue
        integrity_issue = _item_integrity_issue(item, integrity, raw_projection=raw_projection, line=line)
        if integrity_issue:
            rejected.append({
                "market_id": item.get("market_id"),
                "reason": integrity_issue.get("code"),
                "market_integrity": integrity_issue,
                "line": line,
                "market_type": market_type,
                "segment": segment,
            })
            continue
        side = str(item.get("side") or "").upper()
        is_team = market_type in {"TEAM_IT_MATCH", "TEAM_IT_H1", "TEAM_IT_H2", "CURRENT_QUARTER_TEAM_IT"}
        calibration, direction = calibration_for_horizon(score_forecast, horizon_name)
        legacy_sigma = empirical_sigma(calibration, is_team)
        team_is_home = item.get("team") == home_team if is_team else None
        predictive = predictive_calibration(
            calibration_config, stage=stage, mode=mode, horizon=horizon_name,
            is_team=is_team, team_is_home=team_is_home, fallback_sigma=legacy_sigma,
        )
        projection = float(raw_projection) + float(predictive["bias"])
        sigma = float(predictive["sigma"])
        edge = projection - line if side == "OVER" else line - projection
        edge_z = edge / sigma
        p_score_line = clamp(normal_cdf(edge_z), 0.01, 0.99)
        p_residual = residual_direction_probability(direction, side)
        p_history = source_probability(item, "history", "p_hist")
        p_scenario = source_probability(item, "scenario", "p_scenario")
        p_score_calibrated, p_final = v15_probability(p_score_line, p_residual, p_history, p_scenario, edge_z)
        real = real_line(item)
        odds = num(item.get("odds"))
        action, status, stake, blockers, confirmations, break_even, ev, soft_conflict, history_override, hot_continuation = classify_candidate(
            p_final=p_final, p_score=p_score_calibrated, edge=edge, edge_z=edge_z, odds=odds,
            p_history=p_history, p_scenario=p_scenario, p_residual=p_residual,
            real=real, stage=stage, market_type=market_type, segment=segment, side=side,
            source_evaluation=item, raw_match=raw_match, age_blocked=age_blocked,
        )
        output.append(ScoreCandidate(
            market_id=str(item.get("market_id") or ""),
            market_type=market_type,
            segment=segment,
            team=item.get("team"),
            side=side,
            line=line,
            odds=odds,
            bookmaker=str(item.get("bookmaker") or "unknown"),
            real_line=real,
            horizon=horizon_name,
            projection_home=float(horizon["home"]),
            projection_away=float(horizon["away"]),
            raw_projection=float(raw_projection),
            projection=float(projection),
            projection_bias=float(predictive["bias"]),
            calibration_scope=str(predictive["scope"]),
            calibration_n=int(predictive["n"]),
            edge=float(edge),
            sigma=float(sigma),
            edge_z=float(edge_z),
            p_score_line=float(p_score_line),
            p_residual_direction=float(p_residual),
            p_score_calibrated=float(p_score_calibrated),
            p_history=float(p_history),
            p_scenario=float(p_scenario),
            p_final=float(p_final),
            break_even=break_even,
            expected_value=ev,
            action=action,
            status=status,
            stake=stake,
            blockers=blockers,
            confirmations=confirmations,
            historical_zones=historical_zones(advisor_module, canonical, item),
            soft_conflict=soft_conflict,
            history_override=history_override,
            hot_continuation=hot_continuation,
            source_evaluation=deepcopy(item),
        ))
    output.sort(key=lambda row: candidate_rank(row, stage), reverse=True)
    return output, rejected


def model_line_fallback(score_forecast: Any) -> dict[str, Any]:
    stage = str(getattr(score_forecast, "stage", "") or "")
    route = STAGE_ROUTE.get(stage, {"segments": {"MATCH"}, "description": "матч"})
    priority = {
        "Q1_LIVE": ["Q1", "H1"],
        "POST_Q1": ["Q2", "H1"],
        "Q2_LIVE": ["Q2", "H1"],
        "HT": ["Q3", "FINAL"],
        "Q3_LIVE": ["Q3", "FINAL"],
        "POST_Q3": ["Q4", "H2", "FINAL"],
        "Q4_LIVE": ["FINAL"],
        "PRE_MATCH": ["Q1", "H1", "FINAL"],
    }.get(stage, ["FINAL"])
    for horizon_name in priority:
        horizon = forecast_horizon(score_forecast, horizon_name)
        if not horizon:
            continue
        calibration, direction = calibration_for_horizon(score_forecast, horizon_name)
        sigma = empirical_sigma(calibration, False)
        projection = float(horizon["home"] + horizon["away"])
        over_support = residual_direction_probability(direction, "OVER")
        under_support = residual_direction_probability(direction, "UNDER")
        if over_support >= under_support + 0.04:
            side = "OVER"
        elif under_support >= over_support + 0.04:
            side = "UNDER"
        else:
            side = "FAIR_LINE"
        z60, z65, z70 = 0.253347103, 0.385320466, 0.524400513
        return {
            "action": "FORECAST",
            "status": "МОДЕЛЬНА ЛІНІЯ — ПОТРІБНА РЕАЛЬНА ЛІНІЯ ТА КОЕФІЦІЄНТ",
            "market_type": "MODEL_TOTAL",
            "segment": "MATCH" if horizon_name == "FINAL" else horizon_name,
            "horizon": horizon_name,
            "side": side,
            "projection_home": float(horizon["home"]),
            "projection_away": float(horizon["away"]),
            "projection": projection,
            "sigma": sigma,
            "entry": {
                "risk_over_max": round(projection - z60 * sigma, 1),
                "play_over_max": round(projection - z65 * sigma, 1),
                "strong_over_max": round(projection - z70 * sigma, 1),
                "risk_under_min": round(projection + z60 * sigma, 1),
                "play_under_min": round(projection + z65 * sigma, 1),
                "strong_under_min": round(projection + z70 * sigma, 1),
            },
            "residual_direction": {"over": over_support, "under": under_support},
            "route": route["description"],
            "stake": "0%",
        }
    return {
        "action": "PASS", "status": "НЕМАЄ ДОСТУПНОГО SCORE-HORIZON",
        "market_type": None, "segment": None, "side": None, "stake": "0%",
    }


def format_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{100.0 * value:.1f}%"


def stake_percent(stake: Any) -> float:
    """Return the recommended percent of bankroll from a stake label."""
    import re
    values = [float(value.replace(",", ".")) for value in re.findall(r"\d+(?:[.,]\d+)?", str(stake or ""))]
    if not values:
        return 0.0
    # A range is represented by its midpoint; v15 currently emits point values.
    return max(0.0, sum(values[:2]) / min(2, len(values)))


def resolve_bankroll(value: Optional[float] = None) -> Optional[float]:
    if value is not None:
        return max(0.0, float(value))
    env = os.getenv("SUPER_BASKET_BANKROLL") or os.getenv("SUPER_BASKET_BANKROLL_USDT")
    parsed = num(env)
    return max(0.0, parsed) if parsed is not None else None


def apply_budget(selected: Optional[dict[str, Any]], bankroll_usdt: Optional[float]) -> Optional[dict[str, Any]]:
    if selected is None:
        return None
    percent = stake_percent(selected.get("stake")) if selected.get("action") in {"PLAY", "RISK"} else 0.0
    bank = resolve_bankroll(bankroll_usdt)
    selected["stake_percent"] = percent
    selected["bankroll_usdt"] = bank
    selected["stake_amount_usdt"] = round(bank * percent / 100.0, 2) if bank is not None else None
    return selected


def budget_text(selected: dict[str, Any]) -> str:
    percent = float(selected.get("stake_percent") or 0.0)
    amount = selected.get("stake_amount_usdt")
    if amount is not None:
        return f"{percent:g}% від банку = {float(amount):.2f} USDT"
    return f"{percent:g}% від банку"


def top_signal_header(selected: dict[str, Any]) -> str:
    action = str(selected.get("action") or "PASS")
    budget = html.escape(budget_text(selected))
    status = str(selected.get("status") or "")
    if action == "PLAY" and "MAIN PLAY" in status:
        return f"<b>✅ MAIN PLAY — СТАВКА {budget}</b>"
    if action == "PLAY":
        return f"<b>✅ PLAY — СТАВКА {budget}</b>"
    if action == "RISK":
        return f"<b>✅ RISK — СТАВКА {budget}</b>"
    if action == "FORECAST":
        return "<b>⚪ ПРОГНОЗ — СТАВКА 0% / 0 USDT</b>"
    return "<b>⚪ РЕКОМЕНДАЦІЯ — СТАВКА 0% / 0 USDT</b>"


def format_zone(zone: dict[str, Any]) -> str:
    if not zone.get("available"):
        return f"{zone.get('side', 'ZONE')}: недостатньо даних"
    line = zone.get("line")
    probability = zone.get("probability")
    if probability is None:
        probability = zone.get("raw_probability") or zone.get("smoothed_probability")
    hits = zone.get("hits") or zone.get("wins")
    n = zone.get("n")
    side = zone.get("side") or "ZONE"
    return f"{side} {line if line is not None else '—'} — {format_pct(num(probability))}" + (f" ({hits}/{n})" if hits is not None and n else "")


def _public_match_file_url(path: str | Path) -> str:
    """Build a public /match_files URL using only the file name."""
    base = (os.getenv("MATCH_FILES_PUBLIC_BASE_URL") or DEFAULT_MATCH_FILES_PUBLIC_BASE_URL).rstrip("/")
    name = Path(path).name
    return f"{base}/{urllib.parse.quote(name)}"


def _file_links_footer(result: dict[str, Any]) -> str:
    links = result.get("file_links") if isinstance(result.get("file_links"), dict) else {}
    input_url = str(links.get("input_url") or "").strip()
    result_url = str(links.get("result_url") or "").strip()
    rows: list[str] = []
    if input_url:
        safe = html.escape(input_url, quote=True)
        rows.append(f'📄 Вхідний файл: <a href="{safe}">{safe}</a>')
    if result_url:
        safe = html.escape(result_url, quote=True)
        rows.append(f'📊 Файл результату: <a href="{safe}">{safe}</a>')
    if not rows:
        return ""
    return "\n".join(["<b>🔗 ФАЙЛИ</b>", *rows])


def _append_file_links(text: str, result: dict[str, Any]) -> str:
    footer = _file_links_footer(result)
    return text if not footer else text.rstrip() + "\n\n" + footer


def telegram_message(result: dict[str, Any]) -> str:
    forecast = result["score_forecast"]
    selected = result.get("selected") or {}
    stage = forecast.get("stage")
    route = STAGE_ROUTE.get(stage, {}).get("description", "немає маршруту")
    final_score = f"{half_up(forecast['final_home'])}:{half_up(forecast['final_away'])}"
    lines = [
        top_signal_header(selected),
        "",
        "<b>🏀 SUPER BASKET v15.5 — EXACT SCORE + HISTORY OVERRIDE</b>",
        html.escape(str(forecast.get("match_name") or "Матч")),
        f"Етап: <b>{html.escape(str(stage))}</b>",
        f"Маршрут: {html.escape(str(route))}",
        "",
    ]
    next_q = forecast.get("horizons", {}).get("NEXT_QUARTER", {}) if isinstance(forecast.get("horizons"), dict) else {}
    next_q_label = next_q.get("quarter") if isinstance(next_q, dict) else None
    next_q_score = None
    if isinstance(next_q, dict) and next_q.get("status") == "PREDICTED" and num(next_q.get("home")) is not None and num(next_q.get("away")) is not None:
        next_q_score = f"{half_up(next_q['home'])}:{half_up(next_q['away'])}"

    if selected and selected.get("market_type") not in {"MODEL_TOTAL", "NO_REAL_LINE", "AGE_BLOCK"}:
        team = f" — {html.escape(str(selected['team']))}" if selected.get("team") else ""
        odds = f" @ {float(selected['odds']):.2f}" if selected.get("odds") is not None else ""
        lines.extend([
            "<b>🔎 НАЙКРАЩА РЕКОМЕНДАЦІЯ</b>",
            f"<b>{html.escape(MARKET_LABELS.get(selected['market_type'], selected['market_type']))}{team}: "
            f"{html.escape(str(selected['side']))} {selected['line']}{odds}</b>",
            f"Статус: {html.escape(str(selected.get('status') or selected.get('action')))}",
            f"Ставка: <b>{html.escape(budget_text(selected))}</b>",
            "",
            f"🎯 Прогноз фінального рахунку: <b>{final_score}</b>",
            *(([f"🎯 Прогноз {html.escape(str(next_q_label))}: <b>{next_q_score}</b>"]) if next_q_score else []),
            f"Прогноз для ринку: {half_up(selected['projection_home'])}:{half_up(selected['projection_away'])} = <b>{selected['projection']:.1f}</b>",
            f"Сира score-проєкція / bias-корекція: {selected.get('raw_projection', selected['projection']):.1f} / {selected.get('projection_bias', 0.0):+.1f}",
            f"Predictive calibration: {html.escape(str(selected.get('calibration_scope') or 'fallback'))}, N={selected.get('calibration_n', 0)}",
            f"Edge від лінії у напрямку сигналу: <b>{selected['edge']:+.1f}</b>",
            f"Фактична калібрована σ: {selected['sigma']:.1f}; edge/σ: {selected['edge_z']:.2f}",
            "",
            "<b>📊 ОЦІНКА</b>",
            f"P_score по лінії: {format_pct(selected['p_score_line'])}",
            f"P_score після калібровки похибки: <b>{format_pct(selected['p_score_calibrated'])}</b>",
            f"P_scenario: {format_pct(selected['p_scenario'])}",
            f"P_history: {format_pct(selected['p_history'])}",
            f"Фінальна обережна оцінка: <b>{format_pct(selected['p_final'])}</b>",
        ])
        if selected.get("history_override"):
            lines.extend(["", "🛡 <b>History override:</b> " + html.escape(str(selected.get("history_override")))])
        if selected.get("soft_conflict"):
            lines.extend(["⚠️ <b>Live conflict:</b> " + html.escape(str(selected.get("soft_conflict"))) + " — не автоматичний PASS; максимум RISK при override."])
        if selected.get("confirmations"):
            lines.extend(["", "✅ Підтвердження: " + html.escape("; ".join(selected["confirmations"])) + "."])
        if selected.get("blockers"):
            lines.extend(["⚠️ Обмеження: " + html.escape("; ".join(selected["blockers"])) + "."])
        zones = selected.get("historical_zones") or []
        if zones:
            lines.extend(["", "<b>📍 ІСТОРИЧНІ ЗОНИ</b>"] + [html.escape(format_zone(zone)) for zone in zones[:4]])
    else:
        lines.extend([
            "<b>🔎 РЕКОМЕНДАЦІЯ</b>",
            "<b>PASS</b> — немає допустимої реальної букмекерської лінії/коефіцієнта або ринок не пройшов гейти.",
            f"🎯 Прогноз фінального рахунку: <b>{final_score}</b>",
        ])
        if next_q_score:
            lines.append(f"🎯 Прогноз {html.escape(str(next_q_label))}: <b>{next_q_score}</b>")
        lines.append("Синтетичний OVER/UNDER сигнал без реальної лінії не створюється.")
    lines.extend([
        "",
        "Exact-score є якорем напрямку. Базово потрібні real line, delta >=4 та P_history/P_scenario >=60%; виняток: HISTORY 90% + SCENARIO 90% при directional delta >=0 дає PLAY.",
        "FAKE/stat/Q4 conflict — це warning: strong history override може залишити максимум RISK; HOT CONTINUATION для UNDER залишається hard PASS.",
        "Після зміни рахунку, часу, статистики чи лінії потрібен новий розрахунок.",
    ])
    return _append_file_links("\n".join(lines), result)


def info_q4_enabled() -> bool:
    """Emergency kill switch for the independent INFO layer only."""
    raw = str(os.getenv("SUPER_BASKET_INFO_Q4_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def info_90_enabled() -> bool:
    """Emergency kill switch for the independent 90/90 INFO layer only."""
    raw = str(os.getenv("SUPER_BASKET_INFO_90_90_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _candidate_exact_line_history(candidate: ScoreCandidate) -> dict[str, Any]:
    """Return diagnostic exact-line history for display, never as a hard gate."""
    matches: list[dict[str, Any]] = []
    for zone in candidate.historical_zones or []:
        if not isinstance(zone, dict) or not zone.get("available"):
            continue
        if str(zone.get("kind") or "") != "CURRENT_LINE_HISTORY":
            continue
        if str(zone.get("side") or "").upper() != str(candidate.side or "").upper():
            continue
        zone_line = num(zone.get("line"))
        if zone_line is None or abs(zone_line - float(candidate.line)) > 1e-6:
            continue
        matches.append(zone)
    if not matches:
        return {"probability": None, "hits": None, "losses": None, "pushes": None, "n": None}
    matches.sort(key=lambda row: int(num(row.get("n"), 0.0) or 0), reverse=True)
    zone = matches[0]
    return {
        "probability": num(zone.get("probability", zone.get("raw_probability"))),
        "hits": int(num(zone.get("hits", zone.get("wins")), 0.0) or 0),
        "losses": int(num(zone.get("losses"), 0.0) or 0),
        "pushes": int(num(zone.get("pushes"), 0.0) or 0),
        "n": int(num(zone.get("n"), 0.0) or 0),
    }


def _info_q4_rank(candidate: ScoreCandidate, exact_history: dict[str, Any]) -> tuple[float, ...]:
    raw_delta = float(candidate.projection) - float(candidate.line)
    directional_edge = raw_delta if str(candidate.side).upper() == "OVER" else -raw_delta
    return (
        float(candidate.p_final),
        min(float(candidate.p_history), float(candidate.p_scenario)),
        float(exact_history.get("probability") or 0.0),
        min(directional_edge, 10.0),
        float(candidate.odds or 0.0),
    )


def collect_q4_history_scenario_info(
    candidates: Iterable[ScoreCandidate],
    *,
    stage: str,
    selected_action: str,
    age_blocked: bool = False,
) -> list[dict[str, Any]]:
    """Select the independent PASS→INFO cohort without touching PLAY/RISK.

    The gate is deliberately downstream of the frozen action calculation:
    - a file that already produced PLAY/RISK returns no INFO variants;
    - only candidate.action == PASS is eligible;
    - at most one candidate per market family is retained, ordered by P_final.
    """
    if not info_q4_enabled() or age_blocked:
        return []
    if str(selected_action or "").upper() in {"PLAY", "RISK"}:
        return []
    if str(stage or "") != INFO_Q4_STAGE:
        return []

    best_by_market: dict[str, tuple[ScoreCandidate, dict[str, Any]]] = {}
    for candidate in candidates:
        if str(candidate.action or "").upper() != "PASS":
            continue
        if not candidate.real_line or candidate.odds is None:
            continue
        if candidate.market_type not in INFO_Q4_MARKETS:
            continue
        if float(candidate.p_history) + 1e-12 < INFO_Q4_HISTORY_MIN:
            continue
        if float(candidate.p_scenario) + 1e-12 < INFO_Q4_SCENARIO_MIN:
            continue
        raw_delta = float(candidate.projection) - float(candidate.line)
        if abs(raw_delta) > INFO_Q4_ABS_DELTA_MAX + 1e-12:
            continue
        exact_history = _candidate_exact_line_history(candidate)
        previous = best_by_market.get(candidate.market_type)
        if previous is None or _info_q4_rank(candidate, exact_history) > _info_q4_rank(*previous):
            best_by_market[candidate.market_type] = (candidate, exact_history)

    market_order = {"MATCH_TOTAL": 1, "TEAM_IT_MATCH": 2}
    variants: list[dict[str, Any]] = []
    for market_type, (candidate, exact_history) in sorted(
        best_by_market.items(), key=lambda item: market_order.get(item[0], 99)
    ):
        raw_delta = float(candidate.projection) - float(candidate.line)
        directional_edge = raw_delta if str(candidate.side).upper() == "OVER" else -raw_delta
        variants.append({
            "market_id": candidate.market_id,
            "market_type": market_type,
            "market_label": MARKET_LABELS.get(market_type, market_type),
            "segment": candidate.segment,
            "team": candidate.team,
            "side": candidate.side,
            "line": float(candidate.line),
            "odds": float(candidate.odds),
            "bookmaker": candidate.bookmaker,
            "projection_home": float(candidate.projection_home),
            "projection_away": float(candidate.projection_away),
            "projection_total": float(candidate.projection_home + candidate.projection_away),
            "projection_market": float(candidate.projection),
            "raw_delta": raw_delta,
            "directional_edge": directional_edge,
            "candidate_edge": float(candidate.edge),
            "edge_z": float(candidate.edge_z),
            "p_history": float(candidate.p_history),
            "p_scenario": float(candidate.p_scenario),
            "p_final": float(candidate.p_final),
            "exact_history_probability": exact_history.get("probability"),
            "exact_history_hits": exact_history.get("hits"),
            "exact_history_losses": exact_history.get("losses"),
            "exact_history_pushes": exact_history.get("pushes"),
            "exact_history_n": exact_history.get("n"),
            "source_action": candidate.action,
            "status": "ЦІКАВИЙ ІСТОРИКО-СЦЕНАРНИЙ ВАРІАНТ — БЮДЖЕТ 0%",
            "informational_only": True,
        })
    return variants


def _candidate_info_identity(candidate: ScoreCandidate) -> str:
    """Stable identity used only to suppress an already-sent exact candidate."""
    market_id = str(candidate.market_id or "").strip()
    if market_id:
        return market_id
    return "|".join([
        str(candidate.market_type or ""),
        str(candidate.segment or ""),
        str(candidate.team or ""),
        str(candidate.side or "").upper(),
        f"{float(candidate.line):.6f}",
        str(candidate.bookmaker or ""),
    ])


def collect_history_scenario_90_info(
    candidates: Iterable[ScoreCandidate],
    *,
    excluded_market_ids: Iterable[str] = (),
    age_blocked: bool = False,
) -> list[dict[str, Any]]:
    """Return every independent 90/90 INFO line not already sent elsewhere.

    This selector intentionally has no stage, market, delta or candidate-action
    gate.  The upstream router still defines which candidates are legitimate.
    A real bookmaker line, real odds and U18+ remain mandatory.  Exact
    candidates already emitted as the main PLAY/RISK or Q4 INFO are excluded
    only to prevent duplicate Telegram messages.
    """
    if not info_90_enabled() or age_blocked:
        return []

    excluded = {str(value).strip() for value in excluded_market_ids if str(value).strip()}
    seen: set[str] = set()
    variants: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.real_line or candidate.odds is None:
            continue
        if float(candidate.p_history) + 1e-12 < INFO_90_HISTORY_MIN:
            continue
        if float(candidate.p_scenario) + 1e-12 < INFO_90_SCENARIO_MIN:
            continue
        identity = _candidate_info_identity(candidate)
        if identity in excluded or identity in seen:
            continue
        seen.add(identity)

        exact_history = _candidate_exact_line_history(candidate)
        raw_delta = float(candidate.projection) - float(candidate.line)
        directional_edge = raw_delta if str(candidate.side).upper() == "OVER" else -raw_delta
        variants.append({
            "market_id": identity,
            "market_type": candidate.market_type,
            "market_label": MARKET_LABELS.get(candidate.market_type, candidate.market_type),
            "segment": candidate.segment,
            "team": candidate.team,
            "side": candidate.side,
            "line": float(candidate.line),
            "odds": float(candidate.odds),
            "bookmaker": candidate.bookmaker,
            "projection_home": float(candidate.projection_home),
            "projection_away": float(candidate.projection_away),
            "projection_total": float(candidate.projection_home + candidate.projection_away),
            "projection_market": float(candidate.projection),
            "raw_delta": raw_delta,
            "directional_edge": directional_edge,
            "candidate_edge": float(candidate.edge),
            "edge_z": float(candidate.edge_z),
            "p_history": float(candidate.p_history),
            "p_scenario": float(candidate.p_scenario),
            "p_final": float(candidate.p_final),
            "exact_history_probability": exact_history.get("probability"),
            "exact_history_hits": exact_history.get("hits"),
            "exact_history_losses": exact_history.get("losses"),
            "exact_history_pushes": exact_history.get("pushes"),
            "exact_history_n": exact_history.get("n"),
            "source_action": candidate.action,
            "status": "ІСТОРІЯ 90% + СЦЕНАРІЙ 90% — INFO, БЮДЖЕТ 0%",
            "informational_only": True,
        })
    return variants


def _info_q4_variant_block(index: int, variant: dict[str, Any]) -> str:
    team = f" — {html.escape(str(variant['team']))}" if variant.get("team") else ""
    odds = f" @ {float(variant['odds']):.2f}" if variant.get("odds") is not None else ""
    history_probability = format_pct(num(variant.get("exact_history_probability")))
    history_n = int(variant.get("exact_history_n") or 0)
    history_hits = int(variant.get("exact_history_hits") or 0)
    history_sample = f" ({history_hits}/{history_n})" if history_n else ""
    return "\n".join([
        f"<b>{index}. {html.escape(str(variant.get('market_label') or variant.get('market_type')))}{team}</b>",
        f"<b>{html.escape(str(variant.get('side')))} {float(variant['line']):.1f}{odds}</b>",
        f"🎯 Прогнозований рахунок: <b>{float(variant['projection_home']):.1f} : "
        f"{float(variant['projection_away']):.1f}</b>",
        f"🧮 Прогноз ринку / лінія: <b>{float(variant['projection_market']):.1f}</b> / "
        f"{float(variant['line']):.1f}",
        f"Δ прогноз−лінія: <b>{float(variant['raw_delta']):+.1f}</b> | "
        f"Directional edge: <b>{float(variant['directional_edge']):+.1f}</b>",
        f"📊 P_history: <b>{format_pct(num(variant.get('p_history')))}</b> | "
        f"P_scenario: <b>{format_pct(num(variant.get('p_scenario')))}</b> | "
        f"P_final: {format_pct(num(variant.get('p_final')))}",
        f"📚 Exact-line history: <b>{history_probability}</b>{history_sample}",
        f"БК: {html.escape(str(variant.get('bookmaker') or '—'))}",
    ])


def q4_history_scenario_info_messages(result: dict[str, Any]) -> list[str]:
    variants = result.get("q4_history_scenario_info_variants") or []
    if not variants:
        return []
    forecast = result.get("score_forecast") or {}
    final_home = num(forecast.get("final_home"))
    final_away = num(forecast.get("final_away"))
    final_score = (
        f"{half_up(final_home)}:{half_up(final_away)}"
        if final_home is not None and final_away is not None else "—"
    )
    header = "\n".join([
        "<b>📚 ЦІКАВИЙ ІСТОРИКО-СЦЕНАРНИЙ ВАРІАНТ</b>",
        "<b>⚪ INFO 0% — НЕ PLAY І НЕ RISK</b>",
        html.escape(str(forecast.get("match_name") or "Матч")),
        f"Етап: <b>{html.escape(str(forecast.get('stage') or '—'))}</b>",
        f"🎯 Загальний прогноз рахунку: <b>{final_score}</b>",
        f"Умови: P_history/P_scenario ≥ 73%; |Δ| ≤ {INFO_Q4_ABS_DELTA_MAX:.1f}.",
    ])
    footer = "\n\n<i>Окремий PASS→INFO шар без бюджету. Чинні PLAY/RISK та їхні ставки не змінені.</i>"
    blocks = [_info_q4_variant_block(index, row) for index, row in enumerate(variants, 1)]
    messages: list[str] = []
    current: list[str] = []
    for block in blocks:
        proposed = header + "\n\n" + "\n\n".join(current + [block]) + footer
        if current and len(proposed) > INFO_Q4_TELEGRAM_MAX_CHARS:
            messages.append(_append_file_links(header + "\n\n" + "\n\n".join(current) + footer, result))
            current = [block]
        else:
            current.append(block)
    if current:
        messages.append(_append_file_links(header + "\n\n" + "\n\n".join(current) + footer, result))
    return messages


def history_scenario_90_info_messages(result: dict[str, Any]) -> list[str]:
    variants = result.get("history_scenario_90_info_variants") or []
    if not variants:
        return []
    forecast = result.get("score_forecast") or {}
    final_home = num(forecast.get("final_home"))
    final_away = num(forecast.get("final_away"))
    final_score = (
        f"{half_up(final_home)}:{half_up(final_away)}"
        if final_home is not None and final_away is not None else "—"
    )
    header = "\n".join([
        "<b>🔥 ІСТОРІЯ 90% + СЦЕНАРІЙ 90%</b>",
        "<b>🟣 INFO 0% — ДОДАТКОВИЙ ВАРІАНТ БЕЗ БЮДЖЕТУ</b>",
        html.escape(str(forecast.get("match_name") or "Матч")),
        f"Етап: <b>{html.escape(str(forecast.get('stage') or '—'))}</b>",
        f"🎯 Загальний прогноз рахунку: <b>{final_score}</b>",
        "Умови: P_history ≥ 90% і P_scenario ≥ 90%; без обмеження stage, market або Δ.",
    ])
    footer = (
        "\n\n<i>Окремий INFO-шар 0%. PLAY/RISK не змінені; "
        "лінії, уже надіслані main або Q4 INFO, не дублюються.</i>"
    )
    blocks = [_info_q4_variant_block(index, row) for index, row in enumerate(variants, 1)]
    messages: list[str] = []
    current: list[str] = []
    for block in blocks:
        proposed = header + "\n\n" + "\n\n".join(current + [block]) + footer
        if current and len(proposed) > INFO_90_TELEGRAM_MAX_CHARS:
            messages.append(_append_file_links(header + "\n\n" + "\n\n".join(current) + footer, result))
            current = [block]
        else:
            current.append(block)
    if current:
        messages.append(_append_file_links(header + "\n\n" + "\n\n".join(current) + footer, result))
    return messages


def _telegram_chat_ids(chats_file: Optional[str | Path] = None) -> tuple[list[str], str]:
    """Load recipients from the shared state volume, then fall back to env."""
    configured = chats_file or os.getenv("TELEGRAM_CHATS_FILE") or "/app/state/telegram_chats.json"
    path = Path(configured).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_ids = payload.get("chatIds") if isinstance(payload, dict) else None
        if isinstance(raw_ids, list):
            chat_ids = list(dict.fromkeys(
                str(value).strip() for value in raw_ids
                if str(value).strip()
            ))
            if chat_ids:
                return chat_ids, "file"
    except (OSError, ValueError, TypeError):
        pass

    raw_fallback = os.getenv("TELEGRAM_CHAT_IDS") or ""
    fallback_ids = [value.strip() for value in raw_fallback.split(",") if value.strip()]
    legacy_chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    if legacy_chat_id and str(legacy_chat_id).strip():
        fallback_ids.append(str(legacy_chat_id).strip())
    return list(dict.fromkeys(fallback_ids)), "env"


def send_telegram(text: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    chat_ids, recipients_source = _telegram_chat_ids()
    if not token:
        return {"status": "SKIPPED_MISSING_TELEGRAM_TOKEN"}
    if not chat_ids:
        return {"status": "SKIPPED_MISSING_TELEGRAM_CHATS", "recipients_source": recipients_source}

    message_ids: list[int] = []
    for chat_id in chat_ids:
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram rejected message: {payload}")
        message_id = (payload.get("result") or {}).get("message_id")
        if message_id is not None:
            message_ids.append(message_id)

    return {
        "status": "SENT",
        "recipients_source": recipients_source,
        "chats_sent": len(chat_ids),
        "message_ids": message_ids,
    }


def _plain_text_from_telegram_html(text: str) -> str:
    # The Telegram body uses only simple HTML formatting. Keep visible link text
    # and strip tags for the plain-text email alternative.
    without_tags = re.sub(r"<[^>]+>", "", text)
    return html.unescape(without_tags)


def _email_html_from_telegram_html(text: str) -> str:
    # Telegram's <b>/<i>/<a> markup is valid in email too; only newlines need
    # conversion so the mail visually matches the Telegram notification.
    return "<html><body>" + text.replace("\n", "<br>\n") + "</body></html>"


def _email_targets() -> tuple[list[str], str]:
    """Load email recipients from env, accepting a list or legacy single target.

    Preferred env:
      EMAIL_TARGETS - JSON array or comma/semicolon/newline/space-separated emails.

    Legacy env (still supported):
      EMAIL_TARGET / MAIL_TARGET - one email or the same delimited list format.
    """
    raw = os.getenv("EMAIL_TARGETS")
    source = "EMAIL_TARGETS"
    if raw is None or not raw.strip():
        raw = os.getenv("EMAIL_TARGET") or os.getenv("MAIL_TARGET") or ""
        source = "EMAIL_TARGET/MAIL_TARGET"
    raw = raw.strip()
    if not raw:
        return [], source

    values: list[str]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            values = [str(value).strip() for value in parsed]
        else:
            values = re.split(r"[,;\s]+", raw)
    else:
        values = re.split(r"[,;\s]+", raw)

    targets = list(dict.fromkeys(value for value in values if value))
    return targets, source


def _send_email_to_target(
    *, endpoint: str, secret: str, target: str, subject: str, text: str, html_text: str,
) -> dict[str, Any]:
    payload = {
        "secret": secret,
        "to": target,
        "subject": subject,
        "text": text,
        "html": html_text,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SUPER-BASKET-v15.5",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw_body = response.read().decode("utf-8", errors="replace").strip()
        response_payload: Any = None
        if raw_body:
            try:
                response_payload = json.loads(raw_body)
            except json.JSONDecodeError:
                response_payload = {"raw": raw_body[:1000]}
        if isinstance(response_payload, dict) and response_payload.get("ok") is False:
            return {
                "status": "ERROR_EMAIL_REJECTED",
                "sent": False,
                "target": target,
                "response": response_payload,
            }
        return {
            "status": "SENT",
            "sent": True,
            "target": target,
            "response": response_payload,
        }
    except Exception as error:
        # Email must never replace or break Telegram delivery.
        return {
            "status": "ERROR_EMAIL_SEND_FAILED",
            "sent": False,
            "target": target,
            "error": f"{type(error).__name__}: {error}",
        }


def send_email(text: str, subject: str = "SUPER BASKET notification") -> dict[str, Any]:
    """Send through Google Apps Script over HTTPS without touching SMTP.

    Required env:
      EMAIL_HASH    - the Apps Script secret/hash
      EMAIL_TARGETS - recipients as JSON array or comma/semicolon/space-separated list

    Backward compatible env:
      EMAIL_TARGET / MAIL_TARGET - one recipient or a delimited list

    Optional env:
      EMAIL_WEBHOOK_URL - override the deployed Apps Script /exec URL
    """
    secret = (os.getenv("EMAIL_HASH") or os.getenv("MAIL_HASH") or "").strip()
    targets, recipients_source = _email_targets()
    endpoint = (os.getenv("EMAIL_WEBHOOK_URL") or DEFAULT_EMAIL_WEBHOOK_URL).strip()
    if not secret:
        return {"status": "SKIPPED_MISSING_EMAIL_HASH", "sent": False}
    if not targets:
        return {
            "status": "SKIPPED_MISSING_EMAIL_TARGET",
            "sent": False,
            "recipients_source": recipients_source,
        }
    if not endpoint:
        return {"status": "SKIPPED_MISSING_EMAIL_WEBHOOK_URL", "sent": False}

    plain_text = _plain_text_from_telegram_html(text)
    html_text = _email_html_from_telegram_html(text)
    deliveries = [
        _send_email_to_target(
            endpoint=endpoint,
            secret=secret,
            target=target,
            subject=subject,
            text=plain_text,
            html_text=html_text,
        )
        for target in targets
    ]
    sent_count = sum(bool(item.get("sent")) for item in deliveries)
    if sent_count == len(deliveries):
        status = "SENT"
    elif sent_count:
        status = "PARTIAL"
    elif len({str(item.get("status") or "UNKNOWN") for item in deliveries}) == 1:
        status = str(deliveries[0].get("status") or "NOT_SENT")
    else:
        status = "NOT_SENT"

    return {
        "status": status,
        "sent": sent_count > 0,
        "recipients_source": recipients_source,
        "recipients_count": len(targets),
        "sent_count": sent_count,
        "targets": targets,
        "deliveries": deliveries,
    }


def _notification_subject(result: dict[str, Any], kind: str = "MAIN") -> str:
    forecast = result.get("score_forecast") or {}
    selected = result.get("selected") or {}
    match_name = str(forecast.get("match_name") or "Матч").strip()
    stage = str(forecast.get("stage") or "").strip()
    action = str(selected.get("action") or "").strip()
    parts = ["SUPER BASKET", kind]
    if action and kind == "MAIN":
        parts.append(action)
    if stage:
        parts.append(stage)
    parts.append(match_name)
    return " — ".join(parts)[:240]


def send_info_email_batch(messages: Iterable[str], subject: str) -> dict[str, Any]:
    deliveries: list[dict[str, Any]] = [send_email(message, subject) for message in messages]
    statuses = [str(item.get("status") or "UNKNOWN") for item in deliveries]
    if statuses and all(status == "SENT" for status in statuses):
        status = "SENT"
    elif not statuses:
        status = "SKIPPED_NO_MESSAGES"
    elif any(status == "SENT" for status in statuses):
        status = "PARTIAL"
    elif len(set(statuses)) == 1:
        status = statuses[0]
    else:
        status = "NOT_SENT"
    return {"status": status, "messages_count": len(deliveries), "deliveries": deliveries}


def send_info_telegram_batch(messages: Iterable[str]) -> dict[str, Any]:
    """Send INFO messages without allowing the optional layer to crash a job."""
    deliveries: list[dict[str, Any]] = []
    for message in messages:
        try:
            deliveries.append(send_telegram(message))
        except Exception as error:
            deliveries.append({
                "status": "ERROR",
                "error_type": type(error).__name__,
                "error": str(error),
            })
    statuses = [str(item.get("status") or "UNKNOWN") for item in deliveries]
    if statuses and all(status == "SENT" for status in statuses):
        status = "SENT"
    elif not statuses:
        status = "SKIPPED_NO_MESSAGES"
    elif any(status == "SENT" for status in statuses):
        status = "PARTIAL"
    elif len(set(statuses)) == 1:
        status = statuses[0]
    else:
        status = "NOT_SENT"
    return {"status": status, "messages_count": len(deliveries), "deliveries": deliveries}


def analyse_file(
    match_path: str | Path,
    *, score_model_path: str | Path, advisor_path: str | Path,
    output_path: Optional[str | Path] = None, zones_path: Optional[str | Path] = None,
    db_path: str | Path = "super_basket_v15.sqlite3", send: bool = False,
    simulations: int = 12000, bankroll_usdt: Optional[float] = None,
    calibration_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    match_path = Path(match_path).expanduser().resolve()
    raw = json.loads(match_path.read_text(encoding="utf-8"))
    advisor = load_module(advisor_path, "super_basket_v15_legacy_advisor")
    score_module = load_module(score_model_path, "super_basket_v15_score_model")

    embedded_calculation = raw.get("super_basket_calculation") if isinstance(raw.get("super_basket_calculation"), dict) else None
    if embedded_calculation and isinstance(embedded_calculation.get("market_evaluations"), list):
        # Parser/calculator result files already contain the audited market rows.
        # Reusing them makes replay deterministic and avoids recalculating the
        # same history/scenario blocks with a newer legacy wrapper.
        legacy_result = {"super_basket_calculation": embedded_calculation}
        calculation = embedded_calculation
        canonical_full = calculation.get("canonical_snapshot")
    else:
        with tempfile.TemporaryDirectory(prefix="super_basket_v15_") as temporary:
            legacy_output = Path(temporary) / "legacy_result.json"
            legacy_db = Path(temporary) / "legacy.sqlite3"
            legacy_result = advisor.process_vps_match_file(
                match_path,
                output_path=legacy_output,
                zones_path=zones_path,
                db_path=legacy_db,
                enable_telegram=False,
                dry_run=True,
            )
        calculation = legacy_result.get("super_basket_calculation") or {}
        canonical_full = None
        full_canonical_fn = getattr(advisor, "_v142_full_canonical", None)
        if callable(full_canonical_fn):
            try:
                canonical_full = full_canonical_fn(raw, calculation, False)
            except Exception:
                canonical_full = None
    engine = score_module.Engine(simulations=simulations)
    score_forecast_obj = engine.forecast(raw)
    if canonical_full is None:
        adapt_fn = getattr(advisor, "adapt_match", None)
        if callable(adapt_fn):
            try:
                canonical_full = adapt_fn(raw, deepcopy(getattr(advisor, "DEFAULT_CONFIG", {})), strict=False)
            except TypeError:
                canonical_full = adapt_fn(raw, deepcopy(getattr(advisor, "DEFAULT_CONFIG", {})))
            except Exception:
                canonical_full = None
    calibration_config = load_predictive_calibration(calibration_path, Path(__file__).resolve())
    youth_age = detected_youth_age(raw)
    age_blocked = youth_age is not None and youth_age < 18
    candidates, rejected = score_candidates(
        advisor_module=advisor, legacy_result=legacy_result, score_forecast=score_forecast_obj,
        canonical_override=canonical_full, calibration_config=calibration_config,
        raw_match=raw, age_blocked=age_blocked,
    )
    stage = str(score_forecast_obj.stage)
    real_candidates = [row for row in candidates if row.real_line and row.odds is not None]
    active = [row for row in real_candidates if row.action in {"PLAY", "RISK"}]
    selected_obj = active[0] if active else (real_candidates[0] if real_candidates else None)
    if age_blocked:
        selected = {
            "market_type": "AGE_BLOCK", "action": "PASS", "status": "PASS — U16/U17 НЕ АНАЛІЗУЄМО ДЛЯ СИГНАЛІВ",
            "stake": "0%", "line": None, "odds": None, "side": None, "team": None,
            "blockers": [f"визначено U{youth_age}; дозволені сигнали від U18"], "confirmations": [],
        }
    elif selected_obj is not None:
        selected = asdict(selected_obj)
    else:
        selected = {
            "market_type": "NO_REAL_LINE", "action": "PASS", "status": "PASS — НЕМАЄ ДОПУСТИМОЇ РЕАЛЬНОЇ ЛІНІЇ",
            "stake": "0%", "line": None, "odds": None, "side": None, "team": None,
            "blockers": ["синтетичні/model-only лінії не використовуються"], "confirmations": [],
        }
    model_line = None
    selected = apply_budget(selected, bankroll_usdt)
    q4_info_variants = collect_q4_history_scenario_info(
        candidates,
        stage=stage,
        selected_action=str(selected.get("action") or "PASS"),
        age_blocked=age_blocked,
    )
    info_90_excluded_ids = {
        str(row.get("market_id") or "").strip()
        for row in q4_info_variants
        if str(row.get("market_id") or "").strip()
    }
    if str(selected.get("action") or "").upper() in {"PLAY", "RISK"}:
        selected_market_id = str(selected.get("market_id") or "").strip()
        if selected_market_id:
            info_90_excluded_ids.add(selected_market_id)
    info_90_variants = collect_history_scenario_90_info(
        candidates,
        excluded_market_ids=info_90_excluded_ids,
        age_blocked=age_blocked,
    )

    score_forecast = asdict(score_forecast_obj)
    destination = Path(output_path).expanduser().resolve() if output_path else match_path.with_name(match_path.stem + "_v15_5_result.json")
    file_links = {
        "input_name": match_path.name,
        "input_url": _public_match_file_url(match_path),
        "result_name": destination.name,
        "result_url": _public_match_file_url(destination),
    }
    result = {
        "super_basket_v15": {
            "version": VERSION,
            "created_at": utc_now(),
            "input_file": str(match_path),
            "input_sha256": sha256_file(match_path),
            "score_model_file": str(Path(score_model_path).resolve()),
            "advisor_file": str(Path(advisor_path).resolve()),
            "policy": {
                "score_model_is_primary": True,
                "score_weight": 0.80,
                "scenario_weight": 0.08,
                "history_weight": 0.12,
                "predictive_calibration": calibration_config.get("name") or calibration_config.get("schema"),
                "calibration_path": calibration_config.get("_path"),
                "money_threshold_risk": 0.78,
                "money_threshold_play": 0.82,
                "market_identity_guard": "BLOCK_AMBIGUOUS_TEAM_OR_PERIOD_TOTALS",
                "history_scenario_cannot_reverse_meaningful_score_edge": True,
                "one_best_recommendation": True,
                "one_recommendation_for_every_input_file": True,
                "play_and_risk_show_percent_and_optional_usdt_amount": True,
                "zero_stake_recommendations_are_still_emitted": True,
                "missing_line_becomes_model_forecast_only": False,
                "real_bookmaker_line_required": True,
                "exact_delta_min_points_default": 4.0,
                "history_scenario_90_play_override_enabled": True,
                "history_scenario_90_play_override_directional_delta_min": INFO_90_PLAY_DIRECTIONAL_DELTA_MIN,
                "history_scenario_90_play_override_history_min": INFO_90_HISTORY_MIN,
                "history_scenario_90_play_override_scenario_min": INFO_90_SCENARIO_MIN,
                "risk_requires_history_and_scenario_min": 0.60,
                "u18_plus_only": True,
                "fake_stat_q4_are_soft_conflicts_with_history_override": True,
                "hot_continuation_under_is_hard_block": True,
                "post_q3_under_prefers_h2_to_avoid_ot_exposure": True,
                "detected_youth_age": youth_age,
                "age_blocked": age_blocked,
                "q4_pass_info_layer_enabled": info_q4_enabled(),
                "q4_pass_info_layer_is_independent": True,
                "q4_pass_info_layer_skips_files_with_play_or_risk": True,
                "q4_pass_info_history_min": INFO_Q4_HISTORY_MIN,
                "q4_pass_info_scenario_min": INFO_Q4_SCENARIO_MIN,
                "q4_pass_info_abs_delta_max": INFO_Q4_ABS_DELTA_MAX,
                "q4_pass_info_stage": INFO_Q4_STAGE,
                "q4_pass_info_markets": sorted(INFO_Q4_MARKETS),
                "q4_pass_info_budget_percent": 0.0,
                "history_scenario_90_info_layer_enabled": info_90_enabled(),
                "history_scenario_90_info_layer_is_independent": True,
                "history_scenario_90_info_history_min": INFO_90_HISTORY_MIN,
                "history_scenario_90_info_scenario_min": INFO_90_SCENARIO_MIN,
                "history_scenario_90_info_stage_gate": None,
                "history_scenario_90_info_market_gate": None,
                "history_scenario_90_info_delta_gate": "negative directional delta remains INFO; delta >= 0 may promote to PLAY",
                "history_scenario_90_info_requires_real_line_and_odds": True,
                "history_scenario_90_info_deduplicates_main_and_q4_info": True,
                "history_scenario_90_info_budget_percent": 0.0,
            },
        },
        "score_forecast": score_forecast,
        "selected": selected,
        "model_line": model_line,
        "file_links": file_links,
        "ranked_candidates": [asdict(row) for row in candidates],
        "q4_history_scenario_info_variants": q4_info_variants,
        "history_scenario_90_info_variants": info_90_variants,
        "rejected_by_v15_route": rejected,
        "legacy_context": {
            "version": ((legacy_result.get("super_basket_system") or {}).get("version")),
            "canonical_snapshot": (legacy_result.get("super_basket_calculation") or {}).get("canonical_snapshot"),
            "market_parse_audit": (legacy_result.get("super_basket_calculation") or {}).get("market_parse_audit"),
        },
    }
    message = telegram_message(result)
    if not send:
        delivery = {"status": "DISABLED"}
        email_delivery = {"status": "DISABLED"}
    elif selected.get("action") == "PASS":
        delivery = {"status": "SKIPPED_PASS_SIGNAL"}
        email_delivery = {"status": "SKIPPED_PASS_SIGNAL"}
    else:
        # Telegram remains the original channel. Email is an additional,
        # independent HTTPS delivery of exactly the same notification body.
        delivery = send_telegram(message)
        email_delivery = send_email(message, _notification_subject(result, "MAIN"))
    result["telegram"] = {"message": message, "delivery": delivery}
    result["email"] = {"message": message, "delivery": email_delivery}
    info_messages = q4_history_scenario_info_messages(result)
    if not info_q4_enabled():
        info_delivery = {"status": "SKIPPED_DISABLED_BY_ENV", "messages_count": 0}
        info_email_delivery = {"status": "SKIPPED_DISABLED_BY_ENV", "messages_count": 0}
    elif age_blocked:
        info_delivery = {"status": "SKIPPED_AGE_FILTER", "messages_count": 0}
        info_email_delivery = {"status": "SKIPPED_AGE_FILTER", "messages_count": 0}
    elif str(selected.get("action") or "").upper() in {"PLAY", "RISK"}:
        info_delivery = {"status": "SKIPPED_MAIN_PLAY_RISK", "messages_count": 0}
        info_email_delivery = {"status": "SKIPPED_MAIN_PLAY_RISK", "messages_count": 0}
    elif stage != INFO_Q4_STAGE:
        info_delivery = {"status": "SKIPPED_STAGE", "messages_count": 0}
        info_email_delivery = {"status": "SKIPPED_STAGE", "messages_count": 0}
    elif not info_messages:
        info_delivery = {"status": "SKIPPED_NO_QUALIFYING_PASS_INFO", "messages_count": 0}
        info_email_delivery = {"status": "SKIPPED_NO_QUALIFYING_PASS_INFO", "messages_count": 0}
    elif not send:
        info_delivery = {"status": "DISABLED", "messages_count": len(info_messages)}
        info_email_delivery = {"status": "DISABLED", "messages_count": len(info_messages)}
    else:
        info_delivery = send_info_telegram_batch(info_messages)
        info_email_delivery = send_info_email_batch(info_messages, _notification_subject(result, "Q4 INFO"))
    result["q4_history_scenario_info_telegram"] = {
        "informational_only": True,
        "budget_percent": 0.0,
        "rule": {
            "selected_action_required": "PASS",
            "stage": INFO_Q4_STAGE,
            "markets": sorted(INFO_Q4_MARKETS),
            "p_history_min": INFO_Q4_HISTORY_MIN,
            "p_scenario_min": INFO_Q4_SCENARIO_MIN,
            "abs_raw_delta_max": INFO_Q4_ABS_DELTA_MAX,
            "selection": "TOP1_PER_MARKET_TYPE_BY_P_FINAL",
        },
        "variants_count": len(q4_info_variants),
        "messages": info_messages,
        "delivery": info_delivery,
    }
    result["q4_history_scenario_info_email"] = {
        "informational_only": True,
        "messages_count": len(info_messages),
        "delivery": info_email_delivery,
    }

    info_90_messages = history_scenario_90_info_messages(result)
    if not info_90_enabled():
        info_90_delivery = {"status": "SKIPPED_DISABLED_BY_ENV", "messages_count": 0}
        info_90_email_delivery = {"status": "SKIPPED_DISABLED_BY_ENV", "messages_count": 0}
    elif age_blocked:
        info_90_delivery = {"status": "SKIPPED_AGE_FILTER", "messages_count": 0}
        info_90_email_delivery = {"status": "SKIPPED_AGE_FILTER", "messages_count": 0}
    elif not info_90_messages:
        info_90_delivery = {"status": "SKIPPED_NO_NEW_QUALIFYING_90_90_INFO", "messages_count": 0}
        info_90_email_delivery = {"status": "SKIPPED_NO_NEW_QUALIFYING_90_90_INFO", "messages_count": 0}
    elif not send:
        info_90_delivery = {"status": "DISABLED", "messages_count": len(info_90_messages)}
        info_90_email_delivery = {"status": "DISABLED", "messages_count": len(info_90_messages)}
    else:
        info_90_delivery = send_info_telegram_batch(info_90_messages)
        info_90_email_delivery = send_info_email_batch(info_90_messages, _notification_subject(result, "90/90 INFO"))
    result["history_scenario_90_info_telegram"] = {
        "informational_only": True,
        "budget_percent": 0.0,
        "rule": {
            "p_history_min": INFO_90_HISTORY_MIN,
            "p_scenario_min": INFO_90_SCENARIO_MIN,
            "stage_gate": None,
            "market_gate": None,
            "delta_gate": "INFO has no hard gate; qualifying 90/90 candidate with directional delta >= 0 is promoted to PLAY before delivery",
            "play_override_directional_delta_min": INFO_90_PLAY_DIRECTIONAL_DELTA_MIN,
            "real_line_and_odds_required": True,
            "u18_plus_required": True,
            "selection": "ALL_UNIQUE_ROUTE_VALID_REAL_LINES",
            "deduplicate_against": ["MAIN_PLAY_RISK", "Q4_HISTORY_SCENARIO_INFO"],
        },
        "excluded_market_ids": sorted(info_90_excluded_ids),
        "variants_count": len(info_90_variants),
        "messages": info_90_messages,
        "delivery": info_90_delivery,
    }
    result["history_scenario_90_info_email"] = {
        "informational_only": True,
        "messages_count": len(info_90_messages),
        "delivery": info_90_email_delivery,
    }

    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    record_v15_result(db_path, result)
    return result


def record_v15_result(db_path: str | Path, result: dict[str, Any]) -> None:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS v15_predictions("
            "id INTEGER PRIMARY KEY, input_sha256 TEXT UNIQUE, match_id TEXT, stage TEXT, "
            "action TEXT, market_type TEXT, segment TEXT, side TEXT, line REAL, odds REAL, "
            "projection REAL, p_score REAL, p_final REAL, payload TEXT, created_at TEXT)"
        )
        forecast = result.get("score_forecast") or {}
        selected = result.get("selected") or {}
        connection.execute(
            "INSERT OR IGNORE INTO v15_predictions("
            "input_sha256,match_id,stage,action,market_type,segment,side,line,odds,projection,p_score,p_final,payload,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result["super_basket_v15"]["input_sha256"],
                forecast.get("match_id"), forecast.get("stage"), selected.get("action"),
                selected.get("market_type"), selected.get("segment"), selected.get("side"),
                selected.get("line"), selected.get("odds"), selected.get("projection"),
                selected.get("p_score_calibrated"), selected.get("p_final"),
                json.dumps(result, ensure_ascii=False), utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


# --------------------------- XLSX / backtest ---------------------------

XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def excel_col_index(reference: str) -> int:
    letters = "".join(ch for ch in reference if ch.isalpha())
    value = 0
    for char in letters.upper():
        value = value * 26 + ord(char) - 64
    return value - 1


def read_xlsx_sheet(path: str | Path, sheet_name: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{XLSX_NS}si"):
                shared.append("".join(node.text or "" for node in si.iter(f"{XLSX_NS}t")))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall(f"{PKG_REL_NS}Relationship")}
        target = None
        sheets_node = workbook.find(f"{XLSX_NS}sheets")
        for sheet in list(sheets_node) if sheets_node is not None else []:
            if sheet.attrib.get("name") == sheet_name:
                target = rel_map[sheet.attrib[f"{REL_NS}id"]]
                break
        if target is None:
            raise KeyError(f"Sheet not found: {sheet_name}")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(archive.read(target))
        matrix: list[list[Any]] = []
        for row in root.findall(f".//{XLSX_NS}sheetData/{XLSX_NS}row"):
            values: dict[int, Any] = {}
            for cell in row.findall(f"{XLSX_NS}c"):
                index = excel_col_index(cell.attrib.get("r", "A1"))
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{XLSX_NS}v")
                inline = cell.find(f"{XLSX_NS}is")
                raw_value: Any = None
                if inline is not None:
                    raw_value = "".join(node.text or "" for node in inline.iter(f"{XLSX_NS}t"))
                elif value_node is not None:
                    text = value_node.text or ""
                    if cell_type == "s":
                        raw_value = shared[int(text)]
                    elif cell_type == "b":
                        raw_value = text == "1"
                    elif cell_type in {"str", "e"}:
                        raw_value = text
                    else:
                        try:
                            number = float(text)
                            raw_value = int(number) if number.is_integer() else number
                        except ValueError:
                            raw_value = text
                values[index] = raw_value
            width = max(values, default=-1) + 1
            matrix.append([values.get(index) for index in range(width)])
    if not matrix:
        return []
    headers = [str(value or "") for value in matrix[0]]
    return [
        {headers[index]: row[index] if index < len(row) else None for index in range(len(headers))}
        for row in matrix[1:] if any(value is not None for value in row)
    ]


def target_value(row: dict[str, Any], candidate: dict[str, Any], home_team: str, away_team: str) -> Optional[float]:
    market_type = candidate.get("market_type")
    segment = candidate.get("segment")
    team = candidate.get("team")
    prefix = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4", "H1": "H1", "H2": "H2", "MATCH": "regulation"}.get(segment)
    if not prefix:
        return None
    if market_type in {"MATCH_TOTAL", "H1_TOTAL", "H2_TOTAL", "CURRENT_QUARTER_TOTAL"}:
        return num(row.get(f"{prefix}_total"))
    if market_type in {"TEAM_IT_MATCH", "TEAM_IT_H1", "TEAM_IT_H2", "CURRENT_QUARTER_TEAM_IT"}:
        if team == home_team:
            return num(row.get(f"{prefix}_home"))
        if team == away_team:
            return num(row.get(f"{prefix}_away"))
    return None


def settle_candidate(actual: float, line: float, side: str) -> str:
    if actual == line:
        return "PUSH"
    if side == "OVER":
        return "WIN" if actual > line else "LOSS"
    return "WIN" if actual < line else "LOSS"


def backtest(
    *, targets_path: str | Path, snapshots_dir: str | Path,
    score_model_path: str | Path, advisor_path: str | Path,
    output_path: str | Path, simulations: int = 4000, bankroll_usdt: float = 100.0,
    calibration_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    rows = read_xlsx_sheet(targets_path, "TESTABLE_485")
    snapshots = Path(snapshots_dir).expanduser().resolve()
    results: list[dict[str, Any]] = []
    missing: list[str] = []
    stage_counts = Counter()
    action_counts = Counter()
    settlement_counts = Counter()
    by_market: dict[str, Counter] = defaultdict(Counter)
    by_action: dict[str, Counter] = defaultdict(Counter)
    profit_by_action: dict[str, float] = defaultdict(float)
    score_errors: dict[str, list[float]] = defaultdict(list)
    score_pm5: Counter = Counter()

    for row in rows:
        source_file = str(row.get("source_file") or "")
        source = snapshots / source_file
        if not source.exists():
            missing.append(source_file)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="v15_bt_") as temp:
                result = analyse_file(
                    source, score_model_path=score_model_path, advisor_path=advisor_path,
                    output_path=Path(temp) / "out.json", db_path=Path(temp) / "db.sqlite",
                    send=False, simulations=simulations, bankroll_usdt=bankroll_usdt, calibration_path=calibration_path,
                )
            forecast = result["score_forecast"]
            selected = result.get("selected") or {}
            stage_counts[forecast.get("stage")] += 1
            action_counts[selected.get("action")] += 1
            actual = target_value(row, selected, forecast.get("prematch_prior", {}).get("home_name", ""), forecast.get("prematch_prior", {}).get("away_name", ""))
            settlement = None
            if selected.get("line") is not None and selected.get("side") in {"OVER", "UNDER"} and actual is not None:
                settlement = settle_candidate(actual, float(selected["line"]), selected["side"])
                settlement_counts[settlement] += 1
                action_name = str(selected.get("action") or "UNKNOWN")
                by_market[selected.get("market_type")][settlement] += 1
                by_action[action_name][settlement] += 1
                stake_amount = float(selected.get("stake_amount_usdt") or 0.0)
                odds_value = num(selected.get("odds"))
                row_profit = 0.0
                if settlement == "WIN" and odds_value is not None:
                    row_profit = stake_amount * (odds_value - 1.0)
                elif settlement == "LOSS":
                    row_profit = -stake_amount
                profit_by_action[action_name] += row_profit
            horizon = selected.get("horizon")
            if horizon and actual is not None and selected.get("projection") is not None:
                score_errors[horizon].append(abs(float(selected["projection"]) - actual))
            actual_home = row.get(("regulation_home" if horizon == "FINAL" else f"{horizon}_home"))
            actual_away = row.get(("regulation_away" if horizon == "FINAL" else f"{horizon}_away"))
            if actual_home is not None and actual_away is not None and selected.get("projection_home") is not None:
                key = horizon or "UNKNOWN"
                score_pm5[(key, "n")] += 1
                if abs(float(selected["projection_home"]) - float(actual_home)) <= 5 and abs(float(selected["projection_away"]) - float(actual_away)) <= 5:
                    score_pm5[(key, "hit")] += 1
            results.append({
                "source_file": source_file, "game_id": row.get("game_id"), "stage": forecast.get("stage"),
                "action": selected.get("action"), "market_type": selected.get("market_type"), "segment": selected.get("segment"),
                "side": selected.get("side"), "line": selected.get("line"), "odds": selected.get("odds"),
                "match_name": forecast.get("match_name"),
                "projection": selected.get("projection"), "projection_home": selected.get("projection_home"),
                "projection_away": selected.get("projection_away"), "edge": selected.get("edge"),
                "p_score": selected.get("p_score_calibrated"), "p_scenario": selected.get("p_scenario"),
                "p_history": selected.get("p_history"), "p_final": selected.get("p_final"),
                "status": selected.get("status"), "stake_percent": selected.get("stake_percent"),
                "stake_amount_usdt": selected.get("stake_amount_usdt"),
                "actual": actual, "settlement": settlement,
                "profit_usdt": (row_profit if settlement is not None else 0.0),
            })
        except Exception as error:
            results.append({"source_file": source_file, "error": f"{type(error).__name__}: {error}"})

    evaluated = settlement_counts["WIN"] + settlement_counts["LOSS"]
    report = {
        "version": VERSION,
        "targets": len(rows),
        "snapshots_found": len(rows) - len(missing),
        "snapshots_missing": len(missing),
        "missing_examples": missing[:30],
        "stage_counts": dict(stage_counts),
        "action_counts": dict(action_counts),
        "settlements": dict(settlement_counts),
        "win_rate_ex_push": settlement_counts["WIN"] / evaluated if evaluated else None,
        "by_action": {
            key: {
                **dict(value),
                "evaluated": value["WIN"] + value["LOSS"],
                "win_rate_ex_push": (value["WIN"] / (value["WIN"] + value["LOSS"])) if (value["WIN"] + value["LOSS"]) else None,
                "profit_usdt": round(profit_by_action.get(key, 0.0), 2),
            } for key, value in by_action.items()
        },
        "staked_actions": {
            "wins": by_action["PLAY"]["WIN"] + by_action["RISK"]["WIN"],
            "losses": by_action["PLAY"]["LOSS"] + by_action["RISK"]["LOSS"],
            "pushes": by_action["PLAY"]["PUSH"] + by_action["RISK"]["PUSH"],
            "profit_usdt_on_reference_bank": round(profit_by_action.get("PLAY", 0.0) + profit_by_action.get("RISK", 0.0), 2),
        },
        "zero_stake_actions": {
            "wins": by_action["PASS"]["WIN"] + by_action["FORECAST"]["WIN"],
            "losses": by_action["PASS"]["LOSS"] + by_action["FORECAST"]["LOSS"],
            "pushes": by_action["PASS"]["PUSH"] + by_action["FORECAST"]["PUSH"],
        },
        "reference_bankroll_usdt": bankroll_usdt,
        "by_market": {key: dict(value) for key, value in by_market.items()},
        "score_total_mae_by_selected_horizon": {
            key: statistics.fmean(values) for key, values in score_errors.items() if values
        },
        "both_teams_within_pm5_by_horizon": {
            key: {"hits": score_pm5[(key, "hit")], "n": score_pm5[(key, "n")], "rate": score_pm5[(key, "hit")] / score_pm5[(key, "n")]}
            for key, kind in score_pm5 if kind == "n" and score_pm5[(key, "n")]
        },
        "rows": results,
    }
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def targets_audit(targets_path: str | Path) -> dict[str, Any]:
    rows = read_xlsx_sheet(targets_path, "TESTABLE_485")
    stages = Counter(str(row.get("resolved_stage")) for row in rows)
    modes = Counter(str(row.get("model_mode")) for row in rows)
    eligible_horizons = Counter()
    for row in rows:
        for horizon in str(row.get("available_test_horizons") or "").split("|"):
            if horizon.strip():
                eligible_horizons[horizon.strip()] += 1
    route_coverage = {}
    for stage, count in stages.items():
        permitted = STAGE_ROUTE.get(stage, {}).get("segments", set())
        route_coverage[stage] = {"rows": count, "v15_segments": sorted(permitted), "route_known": bool(permitted)}
    return {
        "version": VERSION,
        "rows": len(rows),
        "stages": dict(stages),
        "modes": dict(modes),
        "available_horizons": dict(eligible_horizons),
        "route_coverage": route_coverage,
        "warning": (
            "The XLSX contains targets, scores and stage labels, but not the parser JSON history, stats and bookmaker lines. "
            "A full signal replay requires the source JSON directory named by source_file."
        ),
    }


def cli(argv: Optional[list[str]] = None) -> int:
    anchor = Path(__file__).resolve()
    parser = argparse.ArgumentParser(description=f"SUPER BASKET {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Process one parser JSON and return one v15 recommendation")
    run.add_argument("--match", required=True)
    run.add_argument("--output")
    run.add_argument("--score-model")
    run.add_argument("--advisor")
    run.add_argument("--zones")
    run.add_argument("--db", default="super_basket_v15_5.sqlite3")
    run.add_argument("--simulations", type=int, default=12000)
    run.add_argument("--telegram", action="store_true")
    run.add_argument("--bankroll-usdt", type=float)
    run.add_argument("--calibration")

    watch = sub.add_parser("watch", help="Watch a directory for stable parser JSON files")
    watch.add_argument("--inbox", required=True)
    watch.add_argument("--outbox", required=True)
    watch.add_argument("--score-model")
    watch.add_argument("--advisor")
    watch.add_argument("--zones")
    watch.add_argument("--db", default="super_basket_v15_5.sqlite3")
    watch.add_argument("--simulations", type=int, default=12000)
    watch.add_argument("--poll", type=float, default=2.0)
    watch.add_argument("--telegram", action="store_true")
    watch.add_argument("--bankroll-usdt", type=float)
    watch.add_argument("--calibration")

    bt = sub.add_parser("backtest", help="Full replay using targets XLSX plus source JSON directory")
    bt.add_argument("--targets", required=True)
    bt.add_argument("--snapshots", required=True)
    bt.add_argument("--output", required=True)
    bt.add_argument("--score-model")
    bt.add_argument("--advisor")
    bt.add_argument("--simulations", type=int, default=4000)
    bt.add_argument("--bankroll-usdt", type=float, default=100.0)
    bt.add_argument("--calibration")

    audit = sub.add_parser("audit-targets", help="Audit stage/horizon coverage of the targets XLSX")
    audit.add_argument("--targets", required=True)
    audit.add_argument("--output")

    args = parser.parse_args(argv)
    try:
        if args.command == "audit-targets":
            report = targets_audit(args.targets)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
            print(text)
            return 0

        score_path = resolve_sibling(getattr(args, "score_model", None), DEFAULT_SCORE_MODEL_NAMES, anchor)
        advisor_path = resolve_sibling(getattr(args, "advisor", None), DEFAULT_ADVISOR_NAMES, anchor)

        if args.command == "run":
            result = analyse_file(
                args.match, score_model_path=score_path, advisor_path=advisor_path,
                output_path=args.output, zones_path=args.zones, db_path=args.db,
                send=args.telegram, simulations=args.simulations, bankroll_usdt=args.bankroll_usdt, calibration_path=args.calibration,
            )
            selected = result.get("selected") or {}
            print(json.dumps({
                "version": VERSION,
                "match_id": result["score_forecast"].get("match_id"),
                "stage": result["score_forecast"].get("stage"),
                "action": selected.get("action"),
                "market_type": selected.get("market_type"),
                "segment": selected.get("segment"),
                "side": selected.get("side"),
                "line": selected.get("line"),
                "projection": selected.get("projection"),
                "p_score": selected.get("p_score_calibrated"),
                "p_final": selected.get("p_final"),
                "telegram": result["telegram"]["delivery"],
                "email": result["email"]["delivery"],
                "q4_info_variants": result["q4_history_scenario_info_telegram"]["variants_count"],
                "q4_info_telegram": result["q4_history_scenario_info_telegram"]["delivery"],
                "q4_info_email": result["q4_history_scenario_info_email"]["delivery"],
                "info_90_90_variants": result["history_scenario_90_info_telegram"]["variants_count"],
                "info_90_90_telegram": result["history_scenario_90_info_telegram"]["delivery"],
                "info_90_90_email": result["history_scenario_90_info_email"]["delivery"],
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "watch":
            inbox = Path(args.inbox).expanduser().resolve()
            outbox = Path(args.outbox).expanduser().resolve()
            outbox.mkdir(parents=True, exist_ok=True)
            observed: dict[str, tuple[int, int]] = {}
            processed: dict[str, tuple[int, int]] = {}
            while True:
                for source in inbox.glob("*.json"):
                    if source.name.endswith(("_advisor_result.json", "_v15_3_result.json", "_v15_4_result.json", "_v15_5_result.json", "_score_forecast.json")):
                        continue
                    stat = source.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    key = str(source)
                    if stat.st_size <= 0:
                        continue
                    if observed.get(key) != signature:
                        observed[key] = signature
                        continue
                    if processed.get(key) == signature:
                        continue
                    try:
                        destination = outbox / f"{source.stem}_v15_5_result.json"
                        result = analyse_file(
                            source, score_model_path=score_path, advisor_path=advisor_path,
                            output_path=destination, zones_path=args.zones, db_path=args.db,
                            send=args.telegram, simulations=args.simulations, bankroll_usdt=args.bankroll_usdt, calibration_path=args.calibration,
                        )
                        print(json.dumps({"file": source.name, "selected": result.get("selected")}, ensure_ascii=False), flush=True)
                    except Exception as error:
                        print(f"ERROR {source}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                    finally:
                        processed[key] = signature
                time.sleep(max(0.5, args.poll))

        if args.command == "backtest":
            report = backtest(
                targets_path=args.targets, snapshots_dir=args.snapshots,
                score_model_path=score_path, advisor_path=advisor_path,
                output_path=args.output, simulations=args.simulations, bankroll_usdt=args.bankroll_usdt, calibration_path=args.calibration,
            )
            print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
            return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
