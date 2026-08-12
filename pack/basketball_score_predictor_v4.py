#!/usr/bin/env python3
"""Dual-mode, multi-horizon basketball score forecaster.

Academic score modelling only. Bookmaker lines, odds, stakes, legacy verdicts,
and legacy projections in input JSON are intentionally ignored.

The model has three observable-data paths:

* PRE_MATCH_MODE: immutable team/opponent score prior.
* STATS_MODE: separate pace and efficiency updates from possessions.
* SCORE_ONLY_MODE: conservative update from score, stage and elapsed time.

For ordinary four-quarter basketball it returns Q1-Q4, H1, H2, regulation FT,
and final-score horizons. It never treats an already completed segment as a
new prediction. Telegram messages contain an exact-score centre plus honest,
retrospectively measured error. They also report the historical sign of the
residual (actual total minus point forecast) as a calibration diagnostic, not
as a prospective recommendation. Python 3.10+, standard library only.

Quick VPS start::

    export TELEGRAM_BOT_TOKEN="..."
    export TELEGRAM_CHAT_ID="..."
    python3 basketball_score_predictor_v4.py --watch /path/to/json

No third-party Python packages are required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VERSION = "4.2.0"
SAFE_INPUT_KEYS = {
    "match", "meta", "raw_data", "live_team_stats", "live_boxscore",
    "rules", "data_quality",
}

# Retrospective calibration from the v4 multi-horizon backtest. Targets were
# independently verified against Flashscore: 106 final scores and 101 ordinary
# four-quarter games. Each tuple is (number of predictions, team MAE,
# 80th percentile of the maximum error across the two teams). The last value is
# therefore an empirical "both teams were within about +/-X" error boundary,
# not a guarantee for a new match.
STAGE_CALIBRATION = {
    ("PRE_MATCH_MODE", "PRE_MATCH", "Q1"): (85, 4.94, 9.1),
    ("PRE_MATCH_MODE", "PRE_MATCH", "H1"): (85, 6.74, 13.4),
    ("PRE_MATCH_MODE", "PRE_MATCH", "H2"): (85, 6.93, 13.7),
    ("PRE_MATCH_MODE", "PRE_MATCH", "FINAL"): (90, 9.53, 20.0),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "Q1"): (99, 4.28, 9.2),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "H1"): (99, 6.59, 13.5),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "FINAL"): (99, 10.09, 17.0),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "Q2"): (73, 4.42, 7.9),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "H1"): (73, 4.47, 8.0),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "FINAL"): (73, 8.66, 17.0),
    ("SCORE_ONLY_MODE", "HT", "Q3"): (68, 4.32, 9.8),
    ("SCORE_ONLY_MODE", "HT", "H2"): (68, 7.10, 15.0),
    ("SCORE_ONLY_MODE", "HT", "FINAL"): (70, 8.58, 15.0),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "Q4"): (64, 3.76, 8.0),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "H2"): (64, 3.75, 8.0),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "FINAL"): (64, 4.13, 8.0),
    ("STATS_MODE", "Q2_LIVE", "Q2"): (27, 4.43, 9.0),
    ("STATS_MODE", "Q2_LIVE", "H1"): (27, 4.43, 9.0),
    ("STATS_MODE", "Q2_LIVE", "FINAL"): (27, 8.74, 18.0),
    ("STATS_MODE", "HT", "Q3"): (29, 5.10, 9.7),
    ("STATS_MODE", "HT", "H2"): (29, 7.57, 18.0),
    ("STATS_MODE", "HT", "FINAL"): (29, 8.24, 18.0),
    ("STATS_MODE", "Q4_LIVE", "Q4"): (28, 3.66, 7.0),
    ("STATS_MODE", "Q4_LIVE", "H2"): (28, 3.62, 7.0),
    ("STATS_MODE", "Q4_LIVE", "FINAL"): (28, 3.62, 7.0),
}

# Fallbacks are used when a stage-specific cell had fewer than 20 observations.
MODE_CALIBRATION = {
    ("PRE_MATCH_MODE", "Q1"): (85, 4.94, 9.1),
    ("PRE_MATCH_MODE", "Q2"): (85, 4.72, 9.8),
    ("PRE_MATCH_MODE", "Q3"): (85, 4.60, 8.7),
    ("PRE_MATCH_MODE", "Q4"): (85, 4.82, 9.0),
    ("PRE_MATCH_MODE", "H1"): (85, 6.74, 13.4),
    ("PRE_MATCH_MODE", "H2"): (85, 6.93, 13.7),
    ("PRE_MATCH_MODE", "FINAL"): (90, 9.53, 20.0),
    ("SCORE_ONLY_MODE", "Q1"): (99, 4.28, 9.2),
    ("SCORE_ONLY_MODE", "Q2"): (172, 4.58, 9.1),
    ("SCORE_ONLY_MODE", "Q3"): (241, 4.38, 8.8),
    ("SCORE_ONLY_MODE", "Q4"): (306, 4.54, 9.0),
    ("SCORE_ONLY_MODE", "H1"): (172, 5.69, 10.7),
    ("SCORE_ONLY_MODE", "H2"): (306, 6.24, 12.6),
    ("SCORE_ONLY_MODE", "FINAL"): (308, 8.21, 16.0),
    ("STATS_MODE", "Q2"): (30, 4.20, 8.8),
    ("STATS_MODE", "Q3"): (59, 5.02, 9.8),
    ("STATS_MODE", "Q4"): (87, 3.99, 7.7),
    ("STATS_MODE", "H1"): (30, 4.43, 9.2),
    ("STATS_MODE", "H2"): (87, 6.20, 14.0),
    ("STATS_MODE", "FINAL"): (87, 6.90, 14.0),
}

# Directional residual calibration from the same independently verified
# backtest. Each tuple is:
#   (sample size, actual total above forecast, below forecast, exactly equal)
# It describes historical model bias only. It must not be interpreted as a
# guaranteed or independently validated probability for a new match.
STAGE_DIRECTION_CALIBRATION = {
    ("PRE_MATCH_MODE", "PRE_MATCH", "FINAL"): (90, 47, 41, 2),
    ("PRE_MATCH_MODE", "PRE_MATCH", "H1"): (85, 47, 38, 0),
    ("PRE_MATCH_MODE", "PRE_MATCH", "H2"): (85, 43, 42, 0),
    ("PRE_MATCH_MODE", "PRE_MATCH", "Q1"): (85, 37, 47, 1),
    ("PRE_MATCH_MODE", "PRE_MATCH", "Q2"): (85, 45, 40, 0),
    ("PRE_MATCH_MODE", "PRE_MATCH", "Q3"): (85, 44, 41, 0),
    ("PRE_MATCH_MODE", "PRE_MATCH", "Q4"): (85, 41, 43, 1),
    ("SCORE_ONLY_MODE", "HT", "FINAL"): (70, 28, 41, 1),
    ("SCORE_ONLY_MODE", "HT", "H2"): (68, 26, 41, 1),
    ("SCORE_ONLY_MODE", "HT", "Q3"): (68, 28, 40, 0),
    ("SCORE_ONLY_MODE", "HT", "Q4"): (68, 29, 39, 0),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "FINAL"): (99, 60, 34, 5),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "H1"): (99, 63, 34, 2),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "H2"): (99, 47, 51, 1),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "Q1"): (99, 66, 33, 0),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "Q2"): (99, 47, 52, 0),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "Q3"): (99, 50, 48, 1),
    ("SCORE_ONLY_MODE", "Q1_LIVE", "Q4"): (99, 46, 53, 0),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "FINAL"): (73, 41, 30, 2),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "H1"): (73, 50, 22, 1),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "H2"): (73, 34, 39, 0),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "Q2"): (73, 50, 22, 1),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "Q3"): (73, 36, 36, 1),
    ("SCORE_ONLY_MODE", "Q2_LIVE", "Q4"): (73, 36, 37, 0),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "FINAL"): (64, 46, 15, 3),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "H2"): (64, 45, 16, 3),
    ("SCORE_ONLY_MODE", "Q4_LIVE", "Q4"): (64, 45, 16, 3),
    ("STATS_MODE", "HT", "FINAL"): (29, 14, 14, 1),
    ("STATS_MODE", "HT", "H2"): (29, 14, 14, 1),
    ("STATS_MODE", "HT", "Q3"): (29, 17, 10, 2),
    ("STATS_MODE", "HT", "Q4"): (29, 13, 16, 0),
    ("STATS_MODE", "Q2_LIVE", "FINAL"): (27, 16, 9, 2),
    ("STATS_MODE", "Q2_LIVE", "H1"): (27, 22, 5, 0),
    ("STATS_MODE", "Q2_LIVE", "H2"): (27, 13, 13, 1),
    ("STATS_MODE", "Q2_LIVE", "Q2"): (27, 22, 5, 0),
    ("STATS_MODE", "Q2_LIVE", "Q3"): (27, 13, 14, 0),
    ("STATS_MODE", "Q2_LIVE", "Q4"): (27, 13, 14, 0),
    ("STATS_MODE", "Q4_LIVE", "FINAL"): (28, 17, 7, 4),
    ("STATS_MODE", "Q4_LIVE", "H2"): (28, 17, 7, 4),
    ("STATS_MODE", "Q4_LIVE", "Q4"): (28, 17, 7, 4),
}

MODE_DIRECTION_CALIBRATION = {
    ("PRE_MATCH_MODE", "FINAL"): (90, 47, 41, 2),
    ("PRE_MATCH_MODE", "H1"): (85, 47, 38, 0),
    ("PRE_MATCH_MODE", "H2"): (85, 43, 42, 0),
    ("PRE_MATCH_MODE", "Q1"): (85, 37, 47, 1),
    ("PRE_MATCH_MODE", "Q2"): (85, 45, 40, 0),
    ("PRE_MATCH_MODE", "Q3"): (85, 44, 41, 0),
    ("PRE_MATCH_MODE", "Q4"): (85, 41, 43, 1),
    ("SCORE_ONLY_MODE", "FINAL"): (308, 177, 120, 11),
    ("SCORE_ONLY_MODE", "H1"): (172, 113, 56, 3),
    ("SCORE_ONLY_MODE", "H2"): (306, 154, 147, 5),
    ("SCORE_ONLY_MODE", "Q1"): (99, 66, 33, 0),
    ("SCORE_ONLY_MODE", "Q2"): (172, 97, 74, 1),
    ("SCORE_ONLY_MODE", "Q3"): (241, 115, 124, 2),
    ("SCORE_ONLY_MODE", "Q4"): (306, 158, 145, 3),
    ("STATS_MODE", "FINAL"): (87, 49, 31, 7),
    ("STATS_MODE", "H1"): (30, 25, 5, 0),
    ("STATS_MODE", "H2"): (87, 46, 35, 6),
    ("STATS_MODE", "Q2"): (30, 22, 7, 1),
    ("STATS_MODE", "Q3"): (59, 31, 26, 2),
    ("STATS_MODE", "Q4"): (87, 45, 38, 4),
}

STAGE_LABELS_UA = {
    "PRE_MATCH": "ДО МАТЧУ",
    "Q1_LIVE": "1-ША ЧВЕРТЬ, LIVE",
    "POST_Q1": "ПІСЛЯ 1-Ї ЧВЕРТІ",
    "Q2_LIVE": "2-ГА ЧВЕРТЬ, LIVE",
    "HT": "ПЕРЕРВА",
    "Q3_LIVE": "3-ТЯ ЧВЕРТЬ, LIVE",
    "POST_Q3": "ПІСЛЯ 3-Ї ЧВЕРТІ",
    "Q4_LIVE": "4-ТА ЧВЕРТЬ, LIVE",
    "OT_LIVE": "ОВЕРТАЙМ, LIVE",
    "STAGE_AMBIGUOUS": "ЕТАП НЕ ВИЗНАЧЕНО",
}

MODE_LABELS_UA = {
    "PRE_MATCH_MODE": "до матчу",
    "STATS_MODE": "зі статистикою (Pace/PPP)",
    "SCORE_ONLY_MODE": "лише рахунок",
}

HORIZON_LABELS_UA = {
    "Q1": "1-ша чверть",
    "Q2": "2-га чверть",
    "Q3": "3-тя чверть",
    "Q4": "4-та чверть",
    "H1": "Перша половина",
    "H2": "Друга половина",
    "FINAL": "Фінальний рахунок",
}


def num(value: Any, default=None):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_pair(value: Any):
    if isinstance(value, dict):
        for home_key, away_key in (
            ("home", "away"), ("team_a", "team_b"), ("a", "b"),
            ("score_a", "score_b"),
        ):
            if home_key in value and away_key in value:
                return num(value[home_key]), num(value[away_key])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return num(value[0]), num(value[1])
    if isinstance(value, str):
        for separator in (":", "-", "–"):
            parts = value.split(separator)
            if len(parts) == 2 and num(parts[0]) is not None and num(parts[1]) is not None:
                return num(parts[0]), num(parts[1])
    return None, None


def get_path(data: dict, *paths: str, default=None):
    for path in paths:
        current: Any = data
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return default


def calibration_for(mode: str, stage: str, horizon: str):
    """Return transparent retrospective error metadata for one horizon."""
    values = STAGE_CALIBRATION.get((mode, stage, horizon))
    scope = "STAGE_SPECIFIC"
    if values is None:
        values = MODE_CALIBRATION.get((mode, horizon))
        scope = "MODE_FALLBACK"
    if values is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "fewer_than_20_comparable_predictions",
        }
    sample_size, mae_team, p80_both = values
    return {
        "status": "AVAILABLE",
        "scope": scope,
        "sample_size": sample_size,
        "team_mae": round(mae_team, 2),
        "both_teams_p80_error": round(p80_both, 1),
        "target_source": "FLASHScore_verified_backtest",
        "interpretation": (
            "Retrospective error only; the 80% boundary is not a guarantee "
            "for a new match."
        ),
    }


def direction_calibration_for(mode: str, stage: str, horizon: str):
    """Return the historical sign distribution of total-score residuals.

    A residual is ``actual total - point-forecast total``. These are observed
    backtest shares, not a forward-looking probability or a recommendation.
    """
    values = STAGE_DIRECTION_CALIBRATION.get((mode, stage, horizon))
    scope = "STAGE_SPECIFIC"
    if values is None:
        values = MODE_DIRECTION_CALIBRATION.get((mode, horizon))
        scope = "MODE_FALLBACK"
    if values is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "fewer_than_20_comparable_predictions",
        }
    sample_size, above_count, below_count, equal_count = values
    if sample_size < 20 or above_count + below_count + equal_count != sample_size:
        return {
            "status": "UNAVAILABLE",
            "reason": "invalid_or_small_direction_calibration_sample",
        }
    above_share = above_count / sample_size
    below_share = below_count / sample_size
    equal_share = equal_count / sample_size
    gap = abs(above_share - below_share)
    if gap < 0.10:
        dominant_direction = "BALANCED"
    elif above_share > below_share:
        dominant_direction = "ABOVE"
    else:
        dominant_direction = "BELOW"
    return {
        "status": "AVAILABLE",
        "scope": scope,
        "sample_size": sample_size,
        "above_count": above_count,
        "below_count": below_count,
        "equal_count": equal_count,
        "above_share": round(above_share, 4),
        "below_share": round(below_share, 4),
        "equal_share": round(equal_share, 4),
        "dominant_direction": dominant_direction,
        "metric": "actual_total_minus_point_forecast_total",
        "target_source": "FLASHScore_verified_backtest",
        "interpretation": (
            "Retrospective sign frequency only; not a prospective probability "
            "or recommendation for a new match."
        ),
    }


def quarter_scores(raw: dict):
    quarters = get_path(raw, "match.quarters", "meta.quarters", default={})
    result = []
    for index in range(1, 5):
        if isinstance(quarters, dict):
            value = quarters.get(f"q{index}")
        elif isinstance(quarters, list) and len(quarters) >= index:
            value = quarters[index - 1]
        else:
            value = None
        result.append(score_pair(value))
    return result


# Backwards-compatible alias used by earlier tests and external integrations.
quarters = quarter_scores


def identity(raw: dict):
    name = str(get_path(raw, "match.name", "meta.match", default="unknown_match"))
    match_id = str(get_path(raw, "match.id", "meta.match_id", default=""))
    if not match_id:
        match_id = hashlib.sha256(name.casefold().encode()).hexdigest()[:20]
    return match_id, name


def current_score(raw: dict):
    home, away = score_pair(get_path(raw, "match.score", "meta.score"))
    if home is not None and away is not None:
        return home, away
    values = [(home, away) for home, away in quarter_scores(raw) if home is not None and away is not None]
    return sum(home for home, _ in values), sum(away for _, away in values)


def resolve_stage(raw: dict):
    """Resolve the prediction moment without treating a live partial as final.

    The parser populates the current quarter with partial points. Counting all
    non-null quarter cells therefore mislabels Q1/Q2/Q4 live files as completed
    periods. Period number, elapsed clock, declared stage and score are used
    together instead.
    """
    declared = str(get_path(raw, "match.stage", "meta.stage", default="")).upper().replace(" ", "_")
    status = str(get_path(raw, "match.status", "meta.status", default="")).upper().replace(" ", "_")
    period = int(num(get_path(raw, "match.period", "meta.period"), 0) or 0)
    period_played = num(get_path(raw, "match.period_minute_played", "meta.period_minute_played"), 0.0) or 0.0
    period_left = num(get_path(raw, "match.period_minute_left", "meta.period_minute_left"))
    score_home, score_away = current_score(raw)
    combined = f"{declared} {status}"
    if any(token in combined for token in ("FINAL", "FINISHED", "FULL_TIME", "POST_Q4")):
        return "FINAL", []
    if any(token in declared for token in ("NOT_STARTED", "PRE_MATCH", "PREMATCH")):
        if score_home + score_away <= 0:
            return "PRE_MATCH", []
    if period <= 0 and score_home + score_away <= 0:
        return "PRE_MATCH", []
    if "HT" in declared or "HALF" in declared:
        return "HT", []
    if "Q3_BREAK" in declared or "AFTER_Q3" in declared or "POST_Q3" in declared:
        return "POST_Q3", []
    if "AFTER_Q1" in declared or "POST_Q1" in declared:
        return "POST_Q1", []
    if period == 1:
        return ("Q1_LIVE" if period_played > 0 or score_home + score_away > 0 else "PRE_MATCH"), []
    if period == 2:
        return ("Q2_LIVE" if period_played > 0 or (period_left is not None and period_left > 0) else "POST_Q1"), []
    if period == 3:
        return ("Q3_LIVE" if period_played > 0 else "HT"), []
    if period == 4:
        return ("Q4_LIVE" if period_played > 0 or (period_left is not None and period_left > 0) else "POST_Q3"), []
    if period > 4:
        return "OT_LIVE", ["overtime_live"]
    return "STAGE_AMBIGUOUS", ["insufficient_consistent_stage_evidence"]


def robust_median(values, default):
    cleaned = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if len(cleaned) >= 10:
        cut = max(1, int(len(cleaned) * 0.10))
        cleaned = cleaned[cut:-cut] or cleaned
    return statistics.median(cleaned) if cleaned else default


def recency_center(values, default):
    """Winsorised, recency-weighted centre; histories are newest first."""
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not cleaned:
        return float(default)
    ordered = sorted(cleaned)
    low = ordered[max(0, int(len(ordered) * 0.08) - 1)]
    high = ordered[min(len(ordered) - 1, int(len(ordered) * 0.92))]
    clipped = [clamp(value, low, high) for value in cleaned]
    weights = [0.965 ** index for index in range(len(clipped))]
    return sum(value * weight for value, weight in zip(clipped, weights)) / sum(weights)


def valid_history_row(row: dict):
    home = num(row.get("hs"))
    away = num(row.get("as_"))
    if home is None or away is None:
        return False
    if {round(home), round(away)} == {0, 20}:
        return False
    return home + away > 30


def oriented_history(rows, team_name):
    target = team_name.casefold().strip()
    output = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not valid_history_row(row):
            continue
        home_name = str(row.get("ht", "")).casefold().strip()
        away_name = str(row.get("at", "")).casefold().strip()
        if home_name == target:
            is_home = True
            scored, allowed = num(row.get("hs")), num(row.get("as_"))
            scored_quarters = [num(row.get(f"q{i}h")) for i in range(1, 5)]
            allowed_quarters = [num(row.get(f"q{i}a")) for i in range(1, 5)]
        elif away_name == target:
            is_home = False
            scored, allowed = num(row.get("as_")), num(row.get("hs"))
            scored_quarters = [num(row.get(f"q{i}a")) for i in range(1, 5)]
            allowed_quarters = [num(row.get(f"q{i}h")) for i in range(1, 5)]
        else:
            continue
        output.append({
            "scored": scored,
            "allowed": allowed,
            "scored_quarters": scored_quarters,
            "allowed_quarters": allowed_quarters,
            "is_home": is_home,
        })
    return output


def history_profile(rows, team_name, regulation, current_home):
    games = oriented_history(rows, team_name)
    fallback_total = 2.0 * regulation
    scored = robust_median([game["scored"] for game in games], fallback_total)
    allowed = robust_median([game["allowed"] for game in games], fallback_total)

    # Venue is supporting evidence only and is activated with a reasonable N.
    venue_games = [game for game in games if game["is_home"] == current_home]
    if len(venue_games) >= 6:
        venue_scored = robust_median([game["scored"] for game in venue_games], scored)
        venue_allowed = robust_median([game["allowed"] for game in venue_games], allowed)
        scored = 0.78 * scored + 0.22 * venue_scored
        allowed = 0.78 * allowed + 0.22 * venue_allowed

    quarter_fallback = fallback_total / 4.0
    scored_quarters = []
    allowed_quarters = []
    for quarter in range(4):
        q_scored = [game["scored_quarters"][quarter] for game in games]
        q_allowed = [game["allowed_quarters"][quarter] for game in games]
        scored_quarters.append(recency_center(q_scored, quarter_fallback))
        allowed_quarters.append(recency_center(q_allowed, quarter_fallback))
    return {
        "scored": scored,
        "allowed": allowed,
        "scored_quarters": scored_quarters,
        "allowed_quarters": allowed_quarters,
        "n": len(games),
        "quarter_n": sum(
            all(value is not None for value in game["scored_quarters"] + game["allowed_quarters"])
            for game in games
        ),
    }


def normalise_quarters(values, target_total):
    safe = [max(1.0, float(value)) for value in values]
    scale = target_total / sum(safe) if sum(safe) else 1.0
    return [value * scale for value in safe]


def prematch_prior(raw: dict):
    match_id, name = identity(raw)
    del match_id
    regulation = num(get_path(raw, "rules.regulation_minutes"), 40.0) or 40.0
    raw_data = get_path(raw, "raw_data", default={}) or {}
    main = raw_data.get("main_match", {}) if isinstance(raw_data, dict) else {}
    parts = name.split(" vs ", 1)
    home_name = str(main.get("ht") or (parts[0] if parts else "")).strip()
    away_name = str(main.get("at") or (parts[1] if len(parts) > 1 else "")).strip()
    home_profile = history_profile(raw_data.get("team_a_hist", []), home_name, regulation, True)
    away_profile = history_profile(raw_data.get("team_b_hist", []), away_name, regulation, False)

    # Structural prior retained from v3 for comparability of the FT backtest.
    home_total = 0.60 * home_profile["scored"] + 0.40 * away_profile["allowed"]
    away_total = 0.60 * away_profile["scored"] + 0.40 * home_profile["allowed"]
    home_quarters = [
        0.60 * home_profile["scored_quarters"][index]
        + 0.40 * away_profile["allowed_quarters"][index]
        for index in range(4)
    ]
    away_quarters = [
        0.60 * away_profile["scored_quarters"][index]
        + 0.40 * home_profile["allowed_quarters"][index]
        for index in range(4)
    ]
    home_quarters = normalise_quarters(home_quarters, home_total)
    away_quarters = normalise_quarters(away_quarters, away_total)
    return {
        "home_name": home_name,
        "away_name": away_name,
        "regulation_minutes": regulation,
        "home_total": home_total,
        "away_total": away_total,
        "home_quarters": home_quarters,
        "away_quarters": away_quarters,
        "home_history_n": home_profile["n"],
        "away_history_n": away_profile["n"],
        "home_quarter_history_n": home_profile["quarter_n"],
        "away_quarter_history_n": away_profile["quarter_n"],
    }


def stats_block(raw, side):
    stats = get_path(raw, "live_team_stats", default={}) or {}
    block = stats.get(side, {}) if isinstance(stats, dict) else {}
    aliases = {
        "fga": ("FGA", "fga"), "fgm": ("FGM", "fgm"),
        "fta": ("FTA", "fta"), "ftm": ("FTM", "ftm"),
        "orb": ("ORB", "orb"), "tov": ("TO", "TOV", "to", "tov"),
        "poss": ("Poss", "POSS", "poss"), "fouls": ("fouls", "FOULS"),
    }
    result = {}
    for target, keys in aliases.items():
        result[target] = next((num(block.get(key)) for key in keys if num(block.get(key)) is not None), None)
    if result["poss"] is None and all(result[key] is not None for key in ("fga", "fta", "orb", "tov")):
        result["poss"] = result["fga"] - result["orb"] + result["tov"] + 0.44 * result["fta"]
    return result


def is_special_format(raw: dict):
    name = str(get_path(raw, "match.name", "meta.match", default="")).upper()
    competition = str(get_path(raw, "match.tournament", "meta.tournament", default="")).upper()
    periods = int(num(get_path(raw, "rules.quarters"), 4) or 4)
    return periods != 4 or "3X3" in name + competition or "BIG3" in name + competition


def stage_position(raw: dict, stage: str, regulation: float):
    quarter_minutes = num(get_path(raw, "rules.quarter_minutes"), regulation / 4.0) or regulation / 4.0
    period = int(num(get_path(raw, "match.period", "meta.period"), 0) or 0)
    period_played = num(get_path(raw, "match.period_minute_played", "meta.period_minute_played"), 0.0) or 0.0
    elapsed = num(get_path(raw, "match.match_minute_played", "meta.total_min_played"))
    if elapsed is None:
        elapsed = max(0, period - 1) * quarter_minutes + period_played
    elapsed = clamp(float(elapsed), 0.0, regulation)
    if stage == "PRE_MATCH":
        current_index, completed = 0, 0
    elif stage == "Q1_LIVE":
        current_index, completed = 0, 0
    elif stage == "POST_Q1":
        current_index, completed = 1, 1
    elif stage == "Q2_LIVE":
        current_index, completed = 1, 1
    elif stage == "HT":
        current_index, completed = 2, 2
    elif stage == "Q3_LIVE":
        current_index, completed = 2, 2
    elif stage == "POST_Q3":
        current_index, completed = 3, 3
    elif stage == "Q4_LIVE":
        current_index, completed = 3, 3
    else:
        current_index = max(0, min(3, period - 1))
        completed = max(0, min(4, current_index))
    return {
        "quarter_minutes": quarter_minutes,
        "period": period,
        "period_played": clamp(period_played, 0.0, quarter_minutes),
        "elapsed": elapsed,
        "current_index": current_index,
        "completed": completed,
    }


@dataclass
class Forecast:
    match_id: str
    match_name: str
    stage: str
    mode: str
    format: str
    score_home: float
    score_away: float
    final_home: float
    final_away: float
    total: float
    margin: float
    intervals: dict
    top_exact_scores: list
    hit_probabilities: dict
    horizons: dict
    horizon_calibration: dict
    horizon_direction_calibration: dict
    prematch_prior: dict
    posterior: dict
    confidence: str
    warnings: list
    model_version: str = VERSION


class Engine:
    def __init__(self, simulations=12000, seed=20260803):
        self.n = simulations
        self.seed = seed

    def mode(self, raw):
        stage, _ = resolve_stage(raw)
        if stage == "PRE_MATCH":
            return "PRE_MATCH_MODE"
        home = stats_block(raw, "home")
        away = stats_block(raw, "away")
        if home["poss"] and away["poss"] and min(home["poss"], away["poss"]) >= 4:
            return "STATS_MODE"
        return "SCORE_ONLY_MODE"

    def endpoint(self, raw, prior, mode, stage, score_home, score_away, elapsed):
        regulation = prior["regulation_minutes"]
        prior_home_rate = prior["home_total"] / regulation
        prior_away_rate = prior["away_total"] / regulation
        remaining = max(0.0, regulation - elapsed)
        diagnostics = {
            "elapsed_minutes": round(elapsed, 3),
            "remaining_minutes": round(remaining, 3),
            "prior_home_rate": round(prior_home_rate, 5),
            "prior_away_rate": round(prior_away_rate, 5),
        }
        if elapsed < 3:
            live_home_rate = prior_home_rate
            live_away_rate = prior_away_rate
            live_weight = 0.0
        elif mode == "STATS_MODE":
            home_stats = stats_block(raw, "home")
            away_stats = stats_block(raw, "away")
            shared_possessions = robust_median(
                [home_stats["poss"], away_stats["poss"]],
                max(home_stats["poss"], away_stats["poss"]),
            )
            live_pace = shared_possessions * regulation / elapsed
            prior_pace = clamp((prior["home_total"] + prior["away_total"]) / 2.16, 55, 115)
            pace_weight = clamp(shared_possessions / (shared_possessions + 18), 0, 0.88)
            posterior_pace = (
                (1 - pace_weight) * prior_pace
                + pace_weight * clamp(live_pace, prior_pace * 0.72, prior_pace * 1.28)
            )
            prior_home_ppp = clamp(prior["home_total"] / prior_pace, 0.70, 1.35)
            prior_away_ppp = clamp(prior["away_total"] / prior_pace, 0.70, 1.35)
            efficiency_weight = clamp(shared_possessions / (shared_possessions + 38), 0, 0.66)
            posterior_home_ppp = (
                (1 - efficiency_weight) * prior_home_ppp
                + efficiency_weight * clamp(score_home / home_stats["poss"], prior_home_ppp * 0.72, prior_home_ppp * 1.28)
            )
            posterior_away_ppp = (
                (1 - efficiency_weight) * prior_away_ppp
                + efficiency_weight * clamp(score_away / away_stats["poss"], prior_away_ppp * 0.72, prior_away_ppp * 1.28)
            )
            live_home_rate = posterior_pace * posterior_home_ppp / regulation
            live_away_rate = posterior_pace * posterior_away_ppp / regulation
            live_weight = clamp(elapsed / (elapsed + 12.0), 0, 0.90)
            diagnostics.update({
                "observed_possessions": round(shared_possessions, 3),
                "prior_pace": round(prior_pace, 3),
                "live_pace": round(live_pace, 3),
                "posterior_pace": round(posterior_pace, 3),
                "pace_weight": round(pace_weight, 4),
                "efficiency_weight": round(efficiency_weight, 4),
                "posterior_home_ppp": round(posterior_home_ppp, 4),
                "posterior_away_ppp": round(posterior_away_ppp, 4),
            })
        else:
            live_home_rate = clamp(score_home / elapsed, prior_home_rate * 0.75, prior_home_rate * 1.25)
            live_away_rate = clamp(score_away / elapsed, prior_away_rate * 0.75, prior_away_rate * 1.25)
            live_weight = clamp(0.80 * elapsed / (elapsed + 10.0), 0, 0.78)
            margin = abs(score_home - score_away)
            if stage in ("POST_Q3", "Q4_LIVE") and margin >= 15:
                cooling = clamp((margin - 12) / 100, 0, 0.10)
                live_home_rate *= 1 - cooling
                live_away_rate *= 1 - cooling
                diagnostics["game_state_cooling"] = round(cooling, 4)

        posterior_home_rate = (1 - live_weight) * prior_home_rate + live_weight * live_home_rate
        posterior_away_rate = (1 - live_weight) * prior_away_rate + live_weight * live_away_rate
        mean_home = score_home + remaining * posterior_home_rate
        mean_away = score_away + remaining * posterior_away_rate
        if mode == "SCORE_ONLY_MODE" and elapsed >= 3:
            # Retained v3 calibration; explicitly labelled retrospective in reports.
            mean_home += 0.5
            mean_away += 0.5
        diagnostics.update({
            "live_weight": round(live_weight, 4),
            "posterior_home_rate": round(posterior_home_rate, 5),
            "posterior_away_rate": round(posterior_away_rate, 5),
            "endpoint_home": round(mean_home, 3),
            "endpoint_away": round(mean_away, 3),
        })
        return mean_home, mean_away, diagnostics

    @staticmethod
    def _horizon(home, away, status="PREDICTED"):
        return {
            "home": round(float(home), 1),
            "away": round(float(away), 1),
            "total": round(float(home + away), 1),
            "status": status,
        }

    def build_horizons(self, raw, prior, mode, stage, mean_home, mean_away):
        if is_special_format(raw):
            return {
                "FINAL": self._horizon(mean_home, mean_away),
                "REGULATION_FT": {"status": "DISABLED_SPECIAL_FORMAT"},
                "Q1": {"status": "DISABLED_SPECIAL_FORMAT"},
                "Q2": {"status": "DISABLED_SPECIAL_FORMAT"},
                "Q3": {"status": "DISABLED_SPECIAL_FORMAT"},
                "Q4": {"status": "DISABLED_SPECIAL_FORMAT"},
                "H1": {"status": "DISABLED_SPECIAL_FORMAT"},
                "H2": {"status": "DISABLED_SPECIAL_FORMAT"},
            }

        position = stage_position(raw, stage, prior["regulation_minutes"])
        current_index = position["current_index"]
        completed = position["completed"]
        quarter_minutes = position["quarter_minutes"]
        played = position["period_played"]
        score_home, score_away = current_score(raw)
        observed = quarter_scores(raw)
        endpoint_remaining_home = max(0.0, mean_home - score_home)
        endpoint_remaining_away = max(0.0, mean_away - score_away)

        def segment_weights(side):
            priors = prior[f"{side}_quarters"]
            weights = [0.0] * 4
            for index in range(completed, 4):
                fraction = 1.0
                if index == current_index and stage not in ("PRE_MATCH", "POST_Q1", "HT", "POST_Q3"):
                    fraction = max(0.0, 1.0 - played / quarter_minutes)
                    partial = observed[index][0 if side == "home" else 1]
                    if partial is not None and played >= 1.0:
                        expected_so_far = max(0.5, priors[index] * played / quarter_minutes)
                        ratio = clamp(partial / expected_so_far, 0.70, 1.30)
                        evidence_k = 6.0 if mode == "STATS_MODE" else 10.0
                        evidence_weight = played / (played + evidence_k)
                        fraction *= 1.0 + evidence_weight * (ratio - 1.0)
                weights[index] = max(0.01, priors[index] * fraction)
            return weights

        home_weights = segment_weights("home")
        away_weights = segment_weights("away")
        home_weight_total = sum(home_weights)
        away_weight_total = sum(away_weights)
        predicted_home = [None] * 4
        predicted_away = [None] * 4
        output = {}
        for index in range(4):
            key = f"Q{index + 1}"
            if index < completed:
                output[key] = {"status": "OBSERVED_NOT_SCORED_AS_PREDICTION"}
                continue
            add_home = endpoint_remaining_home * home_weights[index] / home_weight_total if home_weight_total else 0.0
            add_away = endpoint_remaining_away * away_weights[index] / away_weight_total if away_weight_total else 0.0
            partial_home, partial_away = observed[index]
            is_current_live = index == current_index and stage in ("Q1_LIVE", "Q2_LIVE", "Q3_LIVE", "Q4_LIVE")
            base_home = partial_home if is_current_live and partial_home is not None else 0.0
            base_away = partial_away if is_current_live and partial_away is not None else 0.0
            predicted_home[index] = base_home + add_home
            predicted_away[index] = base_away + add_away
            output[key] = self._horizon(predicted_home[index], predicted_away[index])

        # Halves are returned only while some part of that half remains unknown.
        if completed < 2:
            if stage == "Q2_LIVE":
                h1_home = score_home + max(0.0, predicted_home[1] - (observed[1][0] or 0.0))
                h1_away = score_away + max(0.0, predicted_away[1] - (observed[1][1] or 0.0))
            elif stage == "POST_Q1":
                h1_home = score_home + (predicted_home[1] or 0.0)
                h1_away = score_away + (predicted_away[1] or 0.0)
            else:
                h1_home = sum(value or 0.0 for value in predicted_home[:2])
                h1_away = sum(value or 0.0 for value in predicted_away[:2])
            output["H1"] = self._horizon(h1_home, h1_away)
        else:
            output["H1"] = {"status": "OBSERVED_NOT_SCORED_AS_PREDICTION"}

        if completed < 4 and stage not in ("PRE_MATCH", "Q1_LIVE", "POST_Q1", "Q2_LIVE"):
            if stage in ("Q4_LIVE",):
                h1_home = sum((observed[index][0] or 0.0) for index in range(2))
                h1_away = sum((observed[index][1] or 0.0) for index in range(2))
                h2_home = score_home - h1_home + max(0.0, predicted_home[3] - (observed[3][0] or 0.0))
                h2_away = score_away - h1_away + max(0.0, predicted_away[3] - (observed[3][1] or 0.0))
            elif stage == "POST_Q3":
                q3_home, q3_away = observed[2]
                h2_home = (q3_home or 0.0) + (predicted_home[3] or 0.0)
                h2_away = (q3_away or 0.0) + (predicted_away[3] or 0.0)
            else:
                h2_home = sum(value or 0.0 for value in predicted_home[2:4])
                h2_away = sum(value or 0.0 for value in predicted_away[2:4])
            output["H2"] = self._horizon(h2_home, h2_away)
        elif stage in ("PRE_MATCH", "Q1_LIVE", "POST_Q1", "Q2_LIVE"):
            h2_home = sum(value or 0.0 for value in predicted_home[2:4])
            h2_away = sum(value or 0.0 for value in predicted_away[2:4])
            output["H2"] = self._horizon(h2_home, h2_away)
        else:
            output["H2"] = {"status": "OBSERVED_NOT_SCORED_AS_PREDICTION"}

        output["REGULATION_FT"] = self._horizon(mean_home, mean_away)
        output["FINAL"] = self._horizon(mean_home, mean_away)

        future_quarters = [
            key for key in ("Q1", "Q2", "Q3", "Q4")
            if output.get(key, {}).get("status") == "PREDICTED"
        ]
        if future_quarters:
            output["NEXT_QUARTER"] = dict(output[future_quarters[0]])
            output["NEXT_QUARTER"]["quarter"] = future_quarters[0]
        return output

    def forecast(self, raw, saved_state=None):
        stage, flags = resolve_stage(raw)
        match_id, name = identity(raw)
        score_home, score_away = current_score(raw)
        mode = self.mode(raw)
        computed_prior = prematch_prior(raw)
        prior = computed_prior
        if isinstance(saved_state, dict) and saved_state.get("match_id") == match_id:
            candidate = saved_state.get("prematch_prior")
            if isinstance(candidate, dict) and candidate.get("home_total") and candidate.get("away_total"):
                prior = candidate
        regulation = prior["regulation_minutes"]
        position = stage_position(raw, stage, regulation)
        mean_home, mean_away, posterior = self.endpoint(
            raw, prior, mode, stage, score_home, score_away, position["elapsed"]
        )

        remaining = max(0.0, regulation - position["elapsed"])
        base_sd = max(2.5, 12.0 * math.sqrt(remaining / regulation if regulation else 0.0))
        if mode in ("SCORE_ONLY_MODE", "PRE_MATCH_MODE"):
            base_sd *= 1.20
        if flags or stage == "STAGE_AMBIGUOUS":
            base_sd *= 1.25
        random_seed = int(hashlib.sha256((match_id + str(self.seed)).encode()).hexdigest()[:12], 16)
        generator = random.Random(random_seed)
        simulations = []
        for _ in range(self.n):
            common = generator.gauss(0, base_sd * 0.55)
            home_noise = generator.gauss(0, base_sd * 0.75)
            away_noise = generator.gauss(0, base_sd * 0.75)
            simulated_home = max(score_home, round(mean_home + common + home_noise))
            simulated_away = max(score_away, round(mean_away + common + away_noise))
            simulations.append((int(simulated_home), int(simulated_away)))
        sorted_home = sorted(value[0] for value in simulations)
        sorted_away = sorted(value[1] for value in simulations)
        sorted_total = sorted(value[0] + value[1] for value in simulations)

        def percentile(values, probability):
            index = min(len(values) - 1, max(0, round((len(values) - 1) * probability)))
            return values[index]

        intervals = {}
        for label, low, high in (("50", 0.25, 0.75), ("80", 0.10, 0.90), ("90", 0.05, 0.95), ("95", 0.025, 0.975)):
            intervals[label] = {
                "home": [percentile(sorted_home, low), percentile(sorted_home, high)],
                "away": [percentile(sorted_away, low), percentile(sorted_away, high)],
                "total": [percentile(sorted_total, low), percentile(sorted_total, high)],
            }
        from collections import Counter
        top_scores = [
            {"score": f"{home}:{away}", "probability": round(count / self.n, 4)}
            for (home, away), count in Counter(simulations).most_common(10)
        ]
        final_home = round(statistics.median(sorted_home), 1)
        final_away = round(statistics.median(sorted_away), 1)
        hit_probabilities = {
            f"within_pm_{delta}": round(
                sum(abs(home - final_home) <= delta and abs(away - final_away) <= delta for home, away in simulations) / self.n,
                3,
            )
            for delta in (1, 3, 5, 6)
        }
        horizons = self.build_horizons(raw, prior, mode, stage, final_home, final_away)
        horizon_calibration = {}
        horizon_direction_calibration = {}
        for horizon, value in horizons.items():
            if value.get("status") != "PREDICTED":
                continue
            actual_horizon = value.get("quarter", horizon)
            if actual_horizon == "REGULATION_FT":
                actual_horizon = "FINAL"
            horizon_calibration[horizon] = calibration_for(
                mode, stage, actual_horizon
            )
            horizon_direction_calibration[horizon] = direction_calibration_for(
                mode, stage, actual_horizon
            )
        warnings = list(flags)
        if min(prior["home_history_n"], prior["away_history_n"]) < 20:
            warnings.append(f"small_team_history_n={min(prior['home_history_n'], prior['away_history_n'])}")
        if min(prior["home_quarter_history_n"], prior["away_quarter_history_n"]) < 20:
            warnings.append("quarter_history_below_20")
        if mode == "SCORE_ONLY_MODE":
            warnings.append("score_only_model_no_possession_stats")
        if is_special_format(raw):
            warnings.append("quarter_half_horizons_disabled_special_format")
        confidence = "LOW" if flags else (
            "HIGH" if mode == "STATS_MODE" and position["elapsed"] >= regulation / 2 else
            "MEDIUM" if mode == "STATS_MODE" else "LOW"
        )
        prior_public = {
            key: (round(value, 4) if isinstance(value, float) else value)
            for key, value in prior.items()
        }
        return Forecast(
            match_id=match_id,
            match_name=name,
            stage=stage,
            mode=mode,
            format="SPECIAL" if is_special_format(raw) else "FOUR_QUARTER",
            score_home=score_home,
            score_away=score_away,
            final_home=final_home,
            final_away=final_away,
            total=final_home + final_away,
            margin=final_home - final_away,
            intervals=intervals,
            top_exact_scores=top_scores,
            hit_probabilities=hit_probabilities,
            horizons=horizons,
            horizon_calibration=horizon_calibration,
            horizon_direction_calibration=horizon_direction_calibration,
            prematch_prior=prior_public,
            posterior=posterior,
            confidence=confidence,
            warnings=warnings,
        )


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS predictions("
            "id INTEGER PRIMARY KEY, input_sha256 TEXT UNIQUE, match_id TEXT, "
            "stage TEXT, mode TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "payload TEXT NOT NULL, actual_payload TEXT)"
        )
        # Safe in-place migration for a database created by v1-v3.
        prediction_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(predictions)").fetchall()
        }
        if "mode" not in prediction_columns:
            self.db.execute(
                "ALTER TABLE predictions ADD COLUMN mode TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN'"
            )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS match_state("
            "match_id TEXT PRIMARY KEY, prematch_prior TEXT NOT NULL, "
            "posterior TEXT NOT NULL, last_stage TEXT NOT NULL, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.commit()

    def get_state(self, match_id):
        row = self.db.execute(
            "SELECT prematch_prior, posterior, last_stage FROM match_state WHERE match_id=?",
            (match_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "match_id": match_id,
            "prematch_prior": json.loads(row[0]),
            "posterior": json.loads(row[1]),
            "last_stage": row[2],
        }

    def save(self, input_sha256, forecast):
        try:
            self.db.execute(
                "INSERT INTO predictions(input_sha256,match_id,stage,mode,payload) VALUES(?,?,?,?,?)",
                (
                    input_sha256, forecast.match_id, forecast.stage, forecast.mode,
                    json.dumps(asdict(forecast), ensure_ascii=False),
                ),
            )
            self.db.execute(
                "INSERT INTO match_state(match_id,prematch_prior,posterior,last_stage) VALUES(?,?,?,?) "
                "ON CONFLICT(match_id) DO UPDATE SET posterior=excluded.posterior, "
                "last_stage=excluded.last_stage, updated_at=CURRENT_TIMESTAMP",
                (
                    forecast.match_id,
                    json.dumps(forecast.prematch_prior, ensure_ascii=False),
                    json.dumps(forecast.posterior, ensure_ascii=False),
                    forecast.stage,
                ),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close_final(self, match_id, actual):
        self.db.execute(
            "UPDATE predictions SET actual_payload=? WHERE match_id=? AND actual_payload IS NULL",
            (json.dumps(actual, ensure_ascii=False), match_id),
        )
        self.db.commit()


def telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload
    )
    response = json.loads(urllib.request.urlopen(request, timeout=15).read())
    if not response.get("ok"):
        raise RuntimeError("Telegram API rejected the message")
    return True


def message_horizons(forecast):
    """Choose one stage-relevant quarter, one half and the final score."""
    selected = []
    next_quarter = forecast.horizons.get("NEXT_QUARTER", {})
    if next_quarter.get("status") == "PREDICTED":
        selected.append(("NEXT_QUARTER", next_quarter.get("quarter", "NEXT_QUARTER")))

    early_stages = {"PRE_MATCH", "Q1_LIVE", "POST_Q1", "Q2_LIVE"}
    half = "H1" if forecast.stage in early_stages else "H2"
    if forecast.horizons.get(half, {}).get("status") == "PREDICTED":
        selected.append((half, half))

    if forecast.horizons.get("FINAL", {}).get("status") == "PREDICTED":
        selected.append(("FINAL", "FINAL"))
    return selected


def friendly_warnings(forecast):
    output = []
    if "score_only_model_no_possession_stats" in forecast.warnings:
        output.append("немає повної статистики володінь; інтервал ширший")
    if any(str(value).startswith("small_team_history_n=") for value in forecast.warnings):
        output.append("історія однієї з команд менша за 20 матчів")
    if "quarter_history_below_20" in forecast.warnings:
        output.append("поквартальна історія неповна")
    if "quarter_half_horizons_disabled_special_format" in forecast.warnings:
        output.append("нестандартний формат: чверті й половини вимкнено")
    if forecast.stage == "STAGE_AMBIGUOUS":
        output.append("етап матчу визначено неоднозначно")
    return output


def display_score(value):
    """Conventional half-up rounding for non-negative basketball scores."""
    return int(math.floor(max(0.0, float(value)) + 0.5))


def format_message(forecast):
    interval = forecast.intervals["80"]
    confidence_ua = {"HIGH": "ВИСОКА", "MEDIUM": "СЕРЕДНЯ", "LOW": "НИЗЬКА"}
    lines = [
        "🏀 ПРОГНОЗ РАХУНКУ",
        forecast.match_name,
        "",
        f"Етап: {STAGE_LABELS_UA.get(forecast.stage, forecast.stage)}",
        f"Режим: {MODE_LABELS_UA.get(forecast.mode, forecast.mode)}",
        f"Поточний рахунок: {forecast.score_home:.0f}:{forecast.score_away:.0f}",
        f"Якість вхідних даних: {confidence_ua.get(forecast.confidence, forecast.confidence)}",
    ]

    for storage_key, actual_key in message_horizons(forecast):
        value = forecast.horizons[storage_key]
        label = HORIZON_LABELS_UA.get(actual_key, actual_key)
        calibration = forecast.horizon_calibration.get(storage_key, {})
        direction = forecast.horizon_direction_calibration.get(storage_key, {})
        display_home = display_score(value["home"])
        display_away = display_score(value["away"])
        display_total = display_home + display_away
        lines.extend([
            "",
            f"🔹 {label}",
            f"Точний прогноз: {display_home}:{display_away}",
            f"Сума очок: {display_total}",
        ])
        if calibration.get("status") == "AVAILABLE":
            p80 = math.ceil(calibration["both_teams_p80_error"])
            scope = (
                "для цього етапу"
                if calibration.get("scope") == "STAGE_SPECIFIC"
                else "для цього режиму"
            )
            lines.append(
                "Контрольна похибка: "
                f"MAE {calibration['team_mae']:.1f} очка/команду; "
                f"у 80% вибірки обидві команди були в межах ±{p80} "
                f"({scope}, n={calibration['sample_size']})"
            )
        else:
            lines.append("Контрольна похибка: недостатньо порівнюваних прогнозів")
        if direction.get("status") == "AVAILABLE":
            above = round(100 * direction["above_share"])
            below = round(100 * direction["below_share"])
            equal = max(0, 100 - above - below)
            scope = (
                "цей етап"
                if direction.get("scope") == "STAGE_SPECIFIC"
                else "цей режим"
            )
            lines.append(
                "Історичний знак похибки суми: "
                f"факт був ВИЩЕ у {above}%, НИЖЧЕ у {below}%, "
                f"РІВНО у {equal}% ({scope}, n={direction['sample_size']})"
            )
            dominant = direction.get("dominant_direction")
            if dominant == "ABOVE":
                lines.append("Ретроспективно частіший напрямок: ВИЩЕ.")
            elif dominant == "BELOW":
                lines.append("Ретроспективно частіший напрямок: НИЖЧЕ.")
            else:
                lines.append("Ретроспективно явної переваги ВИЩЕ/НИЖЧЕ немає.")
        else:
            lines.append(
                "Історичний знак похибки суми: недостатньо порівнюваних прогнозів"
            )

    home_name = str(forecast.prematch_prior.get("home_name") or "Команда 1")
    away_name = str(forecast.prematch_prior.get("away_name") or "Команда 2")
    lines.extend([
        "",
        "80% модельний інтервал фіналу:",
        f"{home_name}: {interval['home'][0]}–{interval['home'][1]}",
        f"{away_name}: {interval['away'][0]}–{interval['away'][1]}",
        f"Сума очок: {interval['total'][0]}–{interval['total'][1]}",
    ])
    warnings = friendly_warnings(forecast)
    if warnings:
        lines.extend(["", "⚠️ " + "; ".join(warnings) + "."])
    lines.extend([
        "",
        "Центральний рахунок — оцінка моделі, а не гарантія. "
        "Похибка ретроспективна й перевірена на історичній вибірці. "
        "Частки ВИЩЕ/НИЖЧЕ описують backtest, а не ймовірність нового матчу.",
    ])
    return "\n".join(lines)


def process(path, engine, store, output_directory, send=True, show_message=False):
    blob = Path(path).read_bytes()
    input_sha256 = hashlib.sha256(blob).hexdigest()
    raw = json.loads(blob)
    safe_raw = {key: value for key, value in raw.items() if key in SAFE_INPUT_KEYS}
    match_id, _ = identity(safe_raw)
    saved_state = store.get_state(match_id)
    forecast = engine.forecast(safe_raw, saved_state=saved_state)
    if forecast.stage == "FINAL":
        store.close_final(
            forecast.match_id,
            {"home": forecast.score_home, "away": forecast.score_away},
        )
        return None
    if not store.save(input_sha256, forecast):
        return None
    Path(output_directory).mkdir(parents=True, exist_ok=True)
    destination = Path(output_directory) / (Path(path).stem + "_score_forecast.json")
    destination.write_text(
        json.dumps(asdict(forecast), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    message = format_message(forecast)
    if show_message:
        print(message, flush=True)
    if send:
        telegram(message)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--watch")
    parser.add_argument("--db", default="data/predictions.sqlite")
    parser.add_argument("--output", default="output")
    parser.add_argument("--poll", type=float, default=3)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument(
        "--show-message", action="store_true",
        help="print the exact Telegram message to stdout",
    )
    parser.add_argument("--simulations", type=int, default=12000)
    arguments = parser.parse_args()
    Path(arguments.db).parent.mkdir(parents=True, exist_ok=True)
    engine = Engine(arguments.simulations)
    store = Store(arguments.db)
    if arguments.input:
        destination = process(
            arguments.input, engine, store, arguments.output,
            not arguments.no_telegram, arguments.show_message,
        )
        print(destination or "duplicate_or_final")
    elif arguments.watch:
        observed = {}
        processed = {}
        while True:
            for source in Path(arguments.watch).glob("*.json"):
                stat = source.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                source_key = str(source)
                if stat.st_size <= 0:
                    continue
                if observed.get(source_key) != signature:
                    observed[source_key] = signature
                    continue
                if processed.get(source_key) == signature:
                    continue
                try:
                    destination = process(
                        source, engine, store, arguments.output,
                        not arguments.no_telegram, arguments.show_message,
                    )
                    if destination:
                        print(destination, flush=True)
                except Exception as error:
                    print(f"ERROR {source}: {error}", flush=True)
                finally:
                    processed[source_key] = signature
            time.sleep(arguments.poll)
    else:
        parser.error("use --input FILE or --watch DIRECTORY")


if __name__ == "__main__":
    main()
