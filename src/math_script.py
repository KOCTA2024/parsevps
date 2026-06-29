#!/usr/bin/env python3
"""
HT Basketball Bot - Python analysis module
Usage: python ht_bot.py ./data/matches.json
       node runner.js ./data/matches.json   (calls this via subprocess)
"""

import os
import sys
import json
import math
import re
import statistics
from pathlib import Path
from typing import Optional




# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
MATCH_TOTAL_LINES  = [130.5,135.5,140.5,145.5,150.5,155.5,160.5,165.5,170.5,
                      175.5,180.5,185.5,190.5,195.5,200.5,205.5,210.5,215.5,220.5,225.5]
QUARTER_TOTAL_LINES= [30.5,32.5,34.5,35.5,36.5,38.5,40.5,42.5,44.5,46.5,
                      48.5,50.5,52.5,55.5,58.5,60.5]
TEAM_IT_LINES      = [55.5,60.5,65.5,70.5,72.5,75.5,77.5,80.5,82.5,85.5,
                      87.5,90.5,92.5,95.5,97.5,100.5,105.5,110.5,115.5]
QUARTER_IT_LINES   = [8.5,10.5,12.5,14.5,15.5,16.5,17.5,18.5,19.5,20.5,
                      21.5,22.5,23.5,24.5,25.5,27.5,29.5,30.5]
MATCH_SPREAD_LINES = [-30.5,-25.5,-23.5,-21.5,-18.5,-15.5,-12.5,-10.5,-8.5,
                      -6.5,-5.5,-4.5,-3.5,-2.5,-1.5,1.5,2.5,3.5,4.5,5.5,
                      6.5,8.5,10.5,12.5,15.5,18.5,21.5,23.5,25.5,30.5]
QUARTER_SPREAD_LINES=[-15.5,-12.5,-10.5,-8.5,-6.5,-5.5,-4.5,-3.5,-2.5,-1.5,
                      0,1.5,2.5,3.5,4.5,5.5,6.5,8.5,10.5,12.5,15.5]
ALLOWED_LINES      = [60.5,65.5,70.5,75.5,80.5,82.5,85.5,87.5,90.5,
                      92.5,95.5,97.5,100.5,105.5]

# ── Basketball History Zones Module — ТЗ §5-6 ───────────────────────────
H1_TOTAL_LINES       = [30.5,35.5,40.5,45.5,50.5,55.5,60.5,65.5,70.5,75.5,
                        80.5,85.5,90.5,95.5,100.5,105.5,110.5]
H1_TEAM_POINTS_LINES = [10.5,12.5,14.5,15.5,16.5,18.5,20.5,21.5,22.5,24.5,
                        25.5,27.5,30.5,32.5,35.5,37.5,40.5,42.5,45.5,50.5,55.5]
H2_TOTAL_LINES       = [30.5,35.5,40.5,45.5,50.5,55.5,60.5,65.5,70.5,75.5,
                        80.5,85.5,90.5,95.5,100.5,105.5,110.5,115.5,120.5]
H2_TEAM_POINTS_LINES = [10.5,12.5,14.5,15.5,16.5,18.5,20.5,21.5,22.5,24.5,
                        25.5,27.5,30.5,32.5,35.5,37.5,40.5,42.5,45.5,50.5,
                        55.5,60.5]

# ТЗ §7
Q3_TOTAL_LINES       = [25.5,28.5,30.5,32.5,34.5,35.5,36.5,38.5,
                        40.5,42.5,44.5,45.5,46.5,48.5,50.5,52.5,
                        55.5,58.5,60.5]
Q3_TEAM_POINTS_LINES = [5.5,7.5,8.5,10.5,12.5,14.5,15.5,16.5,
                        18.5,20.5,21.5,22.5,24.5,25.5,27.5,30.5,
                        32.5,35.5,37.5,40.5]

# ТЗ §8
AFTER3Q_TOTAL_LINES       = [70.5,75.5,80.5,85.5,90.5,95.5,100.5,105.5,
                              110.5,115.5,120.5,125.5,130.5,135.5,140.5,
                              145.5,150.5,155.5,160.5]
AFTER3Q_TEAM_POINTS_LINES = [30.5,35.5,40.5,45.5,50.5,55.5,60.5,65.5,
                              70.5,75.5,80.5,85.5,90.5,95.5,100.5]

# ТЗ §9
Q4_TOTAL_LINES       = [20.5,22.5,24.5,25.5,27.5,30.5,32.5,35.5,
                        37.5,40.5,42.5,45.5,47.5,50.5,52.5,55.5,
                        58.5,60.5]
Q4_TEAM_POINTS_LINES = [5.5,7.5,8.5,10.5,12.5,14.5,15.5,16.5,
                        18.5,20.5,21.5,22.5,24.5,25.5,27.5,30.5,
                        32.5,35.5,37.5,40.5]

MIN_VALID_MATCHES = 30


def load_lines(path: str) -> dict:
    """
    Load bookmaker lines from line_result.json.
    Returns a dict with keys: match_total, half_total, quarter_total,
    match_handicap, other — each is the raw list from the file.
    Falls back to empty structure if file is missing or invalid.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "match_total":    data.get("match_total", []),
            "half_total":     data.get("half_total", []),
            "quarter_total":  data.get("quarter_total", []),
            "match_handicap": data.get("match_handicap", []),
            "match_1x2":      data.get("match_1x2", []),
            "other":          data.get("other", []),
            "home_ind_total": data.get("home_ind_total", []),
            "away_ind_total": data.get("away_ind_total", []),
        }
    except (FileNotFoundError, json.JSONDecodeError, Exception):
        return {
            "match_total": [], "half_total": [], "quarter_total": [],
            "match_handicap": [], "match_1x2": [], "other": [],
            "home_ind_total": [], "away_ind_total": [],
        }


def extract_bk_match_total_lines(lines_data: dict) -> list:
    """
    Extract unique sorted match total lines from bookmakers (Match scope, no OT).
    Used for threshold_calcs instead of synthetic sample_lines.
    """
    seen = set()
    result = []
    for entry in lines_data.get("match_total", []):
        scope = entry.get("scope", "")
        if scope != "Match":
            continue
        line = entry.get("line")
        if line is not None and line not in seen:
            seen.add(line)
            result.append(float(line))
    return sorted(result)


def extract_bk_quarter_total_lines(lines_data: dict) -> list:
    """
    Extract unique sorted quarter total lines (Q1 scope used as Q-total proxy).
    """
    seen = set()
    result = []
    for entry in lines_data.get("quarter_total", []):
        line = entry.get("line")
        if line is not None and line not in seen:
            seen.add(line)
            result.append(float(line))
    return sorted(result)


def extract_bk_half_total_lines(lines_data: dict) -> list:
    """
    Extract unique sorted H1 total lines.
    """
    seen = set()
    result = []
    for entry in lines_data.get("half_total", []):
        line = entry.get("line")
        if line is not None and line not in seen:
            seen.add(line)
            result.append(float(line))
    return sorted(result)


def extract_bk_match_handicap_lines(lines_data: dict, scope: str = "Match") -> list:
    """
    Extract unique sorted handicap lines for a given scope.
    Returns positive handicap values (away perspective) and negative (home).
    """
    seen = set()
    result = []
    for entry in lines_data.get("match_handicap", []):
        if entry.get("scope", "") != scope:
            continue
        hcp = entry.get("handicap")
        if hcp is not None and float(hcp) not in seen:
            seen.add(float(hcp))
            result.append(float(hcp))
    return sorted(result)


def extract_bk_3pt_total_lines(lines_data: dict) -> list:
    """
    Extract unique sorted 3PT combined total lines from bookmakers.
    These typically appear in 'other' section (bettingType not mapped) or
    home_ind_total / away_ind_total with very small line values (< 20).
    Returns sorted list of float lines, e.g. [9.5, 10.5, 11.5].
    """
    seen = set()
    result = []
    # Check home_ind_total and away_ind_total for very small lines (3PT proxy)
    for section in ("home_ind_total", "away_ind_total"):
        for entry in lines_data.get(section, []):
            line = entry.get("line")
            if line is not None:
                fline = float(line)
                # Heuristic: 3PT individual totals are typically < 20
                if fline < 20.0 and fline not in seen:
                    seen.add(fline)
                    result.append(fline)
    # Also check 'other' section for any 3PT-style small lines
    for entry in lines_data.get("other", []):
        line = entry.get("handicap")
        if line is not None:
            fline = float(line)
            if fline < 20.0 and fline not in seen:
                seen.add(fline)
                result.append(fline)
    return sorted(result)


def extract_bk_3pt_handicap_lines(lines_data: dict) -> list:
    """
    Extract 3PT handicap lines from bookmakers (small absolute values, typically ±0.5..±4.5).
    These come from 'other' section or home_ind_total/away_ind_total handicap entries.
    Returns sorted list of home-perspective handicap floats, e.g. [-2.5, -1.5, 1.5, 2.5].
    """
    seen = set()
    result = []
    for section in ("home_ind_total", "away_ind_total", "other"):
        for entry in lines_data.get(section, []):
            hcp = entry.get("handicap")
            if hcp is not None:
                fhcp = float(hcp)
                # Small handicaps (absolute value < 10) are 3PT handicap candidates
                if abs(fhcp) < 10.0 and fhcp not in seen:
                    seen.add(fhcp)
                    result.append(fhcp)
    return sorted(result)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def safe_float(v, default=0.0):
    try:
        return float(v) if v not in ("", None) else default
    except (ValueError, TypeError):
        return default

def safe_int(v, default=0):
    try:
        return int(v) if v not in ("", None) else default
    except (ValueError, TypeError):
        return default

_DT_FORMATS = (
    "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M",
    "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
)

def _parse_dt_to_unix(raw_dt) -> Optional[int]:
    """Parse a date/datetime string to a Unix timestamp (int). Returns None on failure."""
    if not raw_dt:
        return None
    from datetime import datetime, timezone
    s = str(raw_dt).strip()
    # Already a plain unix integer/float
    try:
        v = float(s)
        if v > 1_000_000_000:
            return int(v)
    except ValueError:
        pass
    for fmt in _DT_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            # Treat naive datetimes as UTC
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def _unix_to_readable(ts: Optional[int]) -> Optional[str]:
    """Convert Unix timestamp to 'YYYY-MM-DD HH:MM:SS' (UTC). Returns None if ts is None."""
    if ts is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def rate(hits: int, n: int) -> float:
    return round(hits / n, 3) if n > 0 else 0.0

def pct_str(hits: int, n: int) -> str:
    return f"{hits}/{n}"

def percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)

def normal_cdf(x: float, mean: float, std: float) -> float:
    """P(X <= x) for X ~ N(mean, std). Uses math.erf — no scipy needed."""
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + math.erf((x - mean) / (std * math.sqrt(2))))

def model_probability(line: float, center: float, std: float, direction: str = "over") -> float:
    """
    Return model P(over line) or P(under line) via normal distribution.
    direction: 'over' | 'under'
    """
    p_under = normal_cdf(line, center, std)
    if direction == "under":
        return round(p_under, 3)
    return round(1.0 - p_under, 3)

def percentiles(data: list) -> dict:
    if not data:
        return {}
    return {
        "p10": round(percentile(data, 10), 2),
        "p20": round(percentile(data, 20), 2),
        "p25": round(percentile(data, 25), 2),
        "median": round(statistics.median(data), 2),
        "p75": round(percentile(data, 75), 2),
        "p80": round(percentile(data, 80), 2),
        "p85": round(percentile(data, 85), 2),
        "p90": round(percentile(data, 90), 2),
        "p95": round(percentile(data, 95), 2),
        "max": round(max(data), 2),
        "mean": round(statistics.mean(data), 2),
    }


# ─────────────────────────────────────────────
# STEP 1 — parse & structure raw records
# ─────────────────────────────────────────────
def parse_records(raw: list) -> dict:
    """
    Split raw JSON list into:
      main_match   – single MAIN MATCH record
      team_a_hist  – last-35 rows for home team
      team_b_hist  – last-35 rows for away team
      h2h_hist     – H2H rows (if a separate section exists)

    Handles two key formats from the data source:
      - Header rows: {"Source": "--- БРЕШІЯ LAST 35 ---"}  (capital S, no mid/st)
      - Data rows:   {"src": "Брешія (Recent)", "mid": ..., "st": "Finished", ...}
    """
    main_match = None
    team_a_hist = []
    team_b_hist = []
    h2h_hist = []

    current_section = None
    _recent_team_src = ""

    for rec in raw:
        if not isinstance(rec, dict):
            continue
        # Both "src" (data rows) and "Source" (header rows) must be checked
        src = rec.get("src", "") or rec.get("Source", "") or ""
        src_up = src.upper()

        # ── Section header detection ─────────────────────────────────────
        if "MAIN MATCH" in src_up:
            # Header-only row ({"Source": "--- MAIN MATCH ---"}) — skip, wait for data row
            if rec.get("mid"):
                main_match = rec
                current_section = "main"
            else:
                current_section = "main"
            continue

        if re.search(r"LAST\s*\d+", src_up):
            if "H2H" in src_up:
                current_section = "h2h"
            else:
                # First team section → team_a, second → team_b
                if current_section not in ("team_a", "team_b"):
                    current_section = "team_a"
                elif current_section == "team_a":
                    current_section = "team_b"
            continue

        # Records with "(Recent)" src carry real data — switch section on team name change
        if re.search(r"\(RECENT\)", src_up):
            if not _recent_team_src:
                # First (Recent) record ever — this is team_a
                current_section = "team_a"
                _recent_team_src = src_up
            elif src_up != _recent_team_src and current_section == "team_a":
                current_section = "team_b"

        # H2H section header: {"Source": "--- HEAD TO HEAD ---"} or src contains "HEAD TO HEAD"
        if "HEAD TO HEAD" in src_up or (src_up.strip("-– ") == "HEAD TO HEAD"):
            current_section = "h2h"
            continue

        # H2H data rows: src == "H2H" (no "LAST 35" prefix)
        if src_up.strip() == "H2H":
            mid = rec.get("mid")
            st  = rec.get("st", "")
            if st == "Finished" and mid:
                h2h_hist.append(rec)
            continue

        if src == "":
            continue

        # ── Data row routing ─────────────────────────────────────────────
        mid = rec.get("mid")
        st  = rec.get("st", "")

        # Capture main match data row (has mid but status is NOT Finished)
        if current_section == "main" and mid and not main_match:
            main_match = rec
            continue

        # History rows: must be Finished and have a match id
        if st == "Finished" and mid:
            if current_section == "team_a":
                team_a_hist.append(rec)
            elif current_section == "team_b":
                team_b_hist.append(rec)
            elif current_section == "h2h":
                h2h_hist.append(rec)
            # current_section == "main" → ignore (current match appearing in own history)

    return {
        "main_match": main_match,
        "team_a_hist": team_a_hist,
        "team_b_hist": team_b_hist,
        "h2h_hist": h2h_hist,
    }


# ─────────────────────────────────────────────
# STEP 2 — build historical row (enriched)
# ─────────────────────────────────────────────
def build_hist_row(rec: dict, perspective_team: str) -> Optional[dict]:
    """
    perspective_team: 'home' or 'away'
    Returns enriched row with derived fields, or None if invalid.
    """
    home = rec.get("ht", "")
    away = rec.get("at", "")
    hs = safe_int(rec.get("hs"))
    aws = safe_int(rec.get("as_"))

    # Skip technical forfeits
    if (hs == 20 and aws == 0) or (hs == 0 and aws == 20):
        return None
    # Skip if Q1-Q4 missing
    q1h = safe_int(rec.get("q1h"))
    q1a = safe_int(rec.get("q1a"))
    q2h = safe_int(rec.get("q2h"))
    q2a = safe_int(rec.get("q2a"))
    q3h = safe_int(rec.get("q3h"))
    q3a = safe_int(rec.get("q3a"))
    q4h = safe_int(rec.get("q4h"))
    q4a = safe_int(rec.get("q4a"))
    if 0 in (q1h+q1a, q2h+q2a, q3h+q3a, q4h+q4a) and (hs + aws) < 100:
        return None  # likely incomplete

    if perspective_team == "home":
        tp, op = hs, aws
        q1t, q1o = q1h, q1a
        q2t, q2o = q2h, q2a
        q3t, q3o = q3h, q3a
        q4t, q4o = q4h, q4a
        ha = "home"
        fga  = safe_int(rec.get("hfgam"))
        fgm  = safe_int(rec.get("hfgmm"))
        tpa  = safe_int(rec.get("h2pam"))
        tpm  = safe_int(rec.get("h2pmm"))
        pa3  = safe_int(rec.get("h3pam"))
        pm3  = safe_int(rec.get("h3pmm"))
        fta  = safe_int(rec.get("hftam"))
        ftm  = safe_int(rec.get("hftmm"))
        orb  = safe_int(rec.get("horbm"))
        drb  = safe_int(rec.get("hdrbm"))
        reb  = safe_int(rec.get("hrbm"))
        ast  = safe_int(rec.get("hastm"))
        to_  = safe_int(rec.get("htovm"))
        stl  = safe_int(rec.get("hstlm"))
        blk  = safe_int(rec.get("hblkm"))
        fouls= safe_int(rec.get("hflsm"))
        # 1H stats: Q1+Q2 per-quarter fields
        h1_fga  = safe_int(rec.get("hfga1")) + safe_int(rec.get("hfga2"))
        h1_fgm  = safe_int(rec.get("hfgm1")) + safe_int(rec.get("hfgm2"))
        h1_tpa  = safe_int(rec.get("h2pa1")) + safe_int(rec.get("h2pa2"))
        h1_pa3  = safe_int(rec.get("h3pa1")) + safe_int(rec.get("h3pa2"))
        h1_pm3  = safe_int(rec.get("h3pm1")) + safe_int(rec.get("h3pm2"))
        h1_fta  = safe_int(rec.get("hfta1")) + safe_int(rec.get("hfta2"))
        h1_orb  = safe_int(rec.get("horb1")) + safe_int(rec.get("horb2"))
        h1_to   = safe_int(rec.get("htov1")) + safe_int(rec.get("htov2"))
        h1_ast  = safe_int(rec.get("hast1")) + safe_int(rec.get("hast2"))
    else:
        tp, op = aws, hs
        q1t, q1o = q1a, q1h
        q2t, q2o = q2a, q2h
        q3t, q3o = q3a, q3h
        q4t, q4o = q4a, q4h
        ha = "away"
        fga  = safe_int(rec.get("afgam"))
        fgm  = safe_int(rec.get("afgmm"))
        tpa  = safe_int(rec.get("a2pam"))
        tpm  = safe_int(rec.get("a2pmm"))
        pa3  = safe_int(rec.get("a3pam"))
        pm3  = safe_int(rec.get("a3pmm"))
        fta  = safe_int(rec.get("aftam"))
        ftm  = safe_int(rec.get("aftmm"))
        orb  = safe_int(rec.get("aorbm"))
        drb  = safe_int(rec.get("adrbm"))
        reb  = safe_int(rec.get("arbm"))
        ast  = safe_int(rec.get("aastm"))
        to_  = safe_int(rec.get("atovm"))
        stl  = safe_int(rec.get("astlm"))
        blk  = safe_int(rec.get("ablkm"))
        fouls= safe_int(rec.get("aflsm"))
        # 1H stats: Q1+Q2 per-quarter fields
        h1_fga  = safe_int(rec.get("afga1")) + safe_int(rec.get("afga2"))
        h1_fgm  = safe_int(rec.get("afgm1")) + safe_int(rec.get("afgm2"))
        h1_tpa  = safe_int(rec.get("a2pa1")) + safe_int(rec.get("a2pa2"))
        h1_pa3  = safe_int(rec.get("a3pa1")) + safe_int(rec.get("a3pa2"))
        h1_pm3  = safe_int(rec.get("a3pm1")) + safe_int(rec.get("a3pm2"))
        h1_fta  = safe_int(rec.get("afta1")) + safe_int(rec.get("afta2"))
        h1_orb  = safe_int(rec.get("aorb1")) + safe_int(rec.get("aorb2"))
        h1_to   = safe_int(rec.get("atov1")) + safe_int(rec.get("atov2"))
        h1_ast  = safe_int(rec.get("aast1")) + safe_int(rec.get("aast2"))

    total   = tp + op
    margin  = tp - op
    h1t     = q1t + q2t
    h1o     = q1o + q2o
    h2t     = q3t + q4t
    h2o     = q3o + q4o
    aq3t    = h1t + q3t
    aq3o    = h1o + q3o

    # Advanced stats
    poss = fga + 0.44 * fta - orb + to_  if fga else 0
    efg  = (fgm + 0.5 * pm3) / fga        if fga else 0
    off_rtg = 100 * tp / poss              if poss else 0
    ftr  = fta / fga                       if fga else 0
    three_pa_share = pa3 / fga             if fga else 0
    extra_poss = orb - to_

    # ── ТЗ §12: parse match_date and derive season_label ─────────────────
    _raw_dt = rec.get("dt")
    _match_date = None
    _date_parse_status = "OFF"
    if _raw_dt:
        from datetime import datetime as _dt
        for _fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                     "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                _parsed_dt = _dt.strptime(str(_raw_dt).strip(), _fmt)
                _match_date = _parsed_dt.strftime("%Y-%m-%d")
                _date_parse_status = "ON"
                break
            except ValueError:
                pass
    _season_label = None
    if _match_date:
        from datetime import datetime as _dt2
        _md = _dt2.strptime(_match_date, "%Y-%m-%d")
        if _md.month >= 8:
            _season_label = f"{_md.year}/{str(_md.year + 1)[-2:]}"
        else:
            _season_label = f"{_md.year - 1}/{str(_md.year)[-2:]}"

    # ── Per-quarter FGA/FTA/fouls/poss for threshold hit-rate computation ────
    # All four quarters read directly from raw per-quarter fields (Q1–Q4 always present).
    if perspective_team == "home":
        _q1_fga   = safe_int(rec.get("hfga1"))
        _q2_fga   = safe_int(rec.get("hfga2"))
        _q3_fga   = safe_int(rec.get("hfga3"))
        _q4_fga   = safe_int(rec.get("hfga4"))
        _q1_fta   = safe_int(rec.get("hfta1"))
        _q2_fta   = safe_int(rec.get("hfta2"))
        _q3_fta   = safe_int(rec.get("hfta3"))
        _q4_fta   = safe_int(rec.get("hfta4"))
        _q1_fouls = safe_int(rec.get("hfls1"))
        _q2_fouls = safe_int(rec.get("hfls2"))
        _q3_fouls = safe_int(rec.get("hfls3"))
        _q4_fouls = safe_int(rec.get("hfls4"))
        _q1_orb   = safe_int(rec.get("horb1"))
        _q2_orb   = safe_int(rec.get("horb2"))
        _q3_orb   = safe_int(rec.get("horb3"))
        _q4_orb   = safe_int(rec.get("horb4"))
        _q1_to    = safe_int(rec.get("htov1"))
        _q2_to    = safe_int(rec.get("htov2"))
        _q3_to    = safe_int(rec.get("htov3"))
        _q4_to    = safe_int(rec.get("htov4"))
    else:
        _q1_fga   = safe_int(rec.get("afga1"))
        _q2_fga   = safe_int(rec.get("afga2"))
        _q3_fga   = safe_int(rec.get("afga3"))
        _q4_fga   = safe_int(rec.get("afga4"))
        _q1_fta   = safe_int(rec.get("afta1"))
        _q2_fta   = safe_int(rec.get("afta2"))
        _q3_fta   = safe_int(rec.get("afta3"))
        _q4_fta   = safe_int(rec.get("afta4"))
        _q1_fouls = safe_int(rec.get("afls1"))
        _q2_fouls = safe_int(rec.get("afls2"))
        _q3_fouls = safe_int(rec.get("afls3"))
        _q4_fouls = safe_int(rec.get("afls4"))
        _q1_orb   = safe_int(rec.get("aorb1"))
        _q2_orb   = safe_int(rec.get("aorb2"))
        _q3_orb   = safe_int(rec.get("aorb3"))
        _q4_orb   = safe_int(rec.get("aorb4"))
        _q1_to    = safe_int(rec.get("atov1"))
        _q2_to    = safe_int(rec.get("atov2"))
        _q3_to    = safe_int(rec.get("atov3"))
        _q4_to    = safe_int(rec.get("atov4"))

    # poss per quarter: standard formula applied to per-quarter FGA/FTA/ORB/TO
    def _q_poss(qfga, qfta, qorb, qto):
        return round(qfga + 0.44 * qfta - qorb + qto, 2) if qfga else 0.0
    _q1_poss = _q_poss(_q1_fga, _q1_fta, _q1_orb, _q1_to)
    _q2_poss = _q_poss(_q2_fga, _q2_fta, _q2_orb, _q2_to)
    _q3_poss = _q_poss(_q3_fga, _q3_fta, _q3_orb, _q3_to)
    _q4_poss = _q_poss(_q4_fga, _q4_fta, _q4_orb, _q4_to)

    return {
        "date": rec.get("dt"), "league": rec.get("tour"),
        "match_date": _match_date, "date_parse_status": _date_parse_status,
        "season_label": _season_label,
        "home_team": home, "away_team": away, "home_away": ha,
        "team_points": tp, "opp_points": op, "total": total, "margin": margin,
        "q1_team": q1t, "q1_opp": q1o, "q1_total": q1t+q1o, "q1_margin": q1t-q1o,
        "q2_team": q2t, "q2_opp": q2o, "q2_total": q2t+q2o, "q2_margin": q2t-q2o,
        "q3_team": q3t, "q3_opp": q3o, "q3_total": q3t+q3o, "q3_margin": q3t-q3o,
        "q4_team": q4t, "q4_opp": q4o, "q4_total": q4t+q4o, "q4_margin": q4t-q4o,
        "h1_team": h1t, "h1_opp": h1o, "h1_total": h1t+h1o, "h1_margin": h1t-h1o,
        "h2_team": h2t, "h2_opp": h2o, "h2_total": h2t+h2o, "h2_margin": h2t-h2o,
        "after3q_team": aq3t, "after3q_opp": aq3o, "after3q_total": aq3t+aq3o, "after3q_margin": aq3t-aq3o,
        "FGA": fga, "FGM": fgm, "2PA": tpa, "2PM": tpm,
        "3PA": pa3, "3PM": pm3, "FTA": fta, "FTM": ftm,
        "ORB": orb, "DRB": drb, "REB": reb, "AST": ast,
        "TO": to_, "STL": stl, "BLK": blk, "fouls": fouls,
        "poss": round(poss, 2), "efg": round(efg, 4),
        "off_rtg": round(off_rtg, 2), "ftr": round(ftr, 4),
        "three_pa_share": round(three_pa_share, 4),
        "extra_poss": extra_poss,
        "ppp": round(tp / poss, 4) if poss > 0 else 0.0,
        # Per-quarter stat fields for threshold hit-rate computation
        "q1_fga": _q1_fga, "q2_fga": _q2_fga, "q3_fga": _q3_fga, "q4_fga": _q4_fga,
        "q1_fta": _q1_fta, "q2_fta": _q2_fta, "q3_fta": _q3_fta, "q4_fta": _q4_fta,
        "q1_fouls": _q1_fouls, "q2_fouls": _q2_fouls, "q3_fouls": _q3_fouls, "q4_fouls": _q4_fouls,
        "q1_poss": _q1_poss, "q2_poss": _q2_poss, "q3_poss": _q3_poss, "q4_poss": _q4_poss,
    }


def build_hist(records: list, perspective: str, main_match_id: str) -> list:
    rows = []
    for rec in records:
        if rec.get("mid") == main_match_id:
            continue  # exclude current match
        row = build_hist_row(rec, perspective)
        if row:
            rows.append(row)
    return rows  # preserve all parsed rows — no cap here


# ─────────────────────────────────────────────
# STEP 2b — sample gate & stat support
# ─────────────────────────────────────────────
def check_sample_gate(team_a_rows: list, team_b_rows: list, h2h_rows: list) -> dict:
    na  = len(team_a_rows)
    nb  = len(team_b_rows)
    nh  = len(h2h_rows)
    ok  = na >= MIN_VALID_MATCHES and nb >= MIN_VALID_MATCHES
    return {
        "team_a_valid_games": na,
        "team_b_valid_games": nb,
        "pooled_n": na + nb,
        "h2h_n": nh,
        "status": "OK" if ok else "FAIL",
        "recommendation_allowed": ok,
    }

REQUIRED_STAT_FIELDS = ["FGA","2PA","3PA","FTA","ORB","TO","poss","efg"]

def check_stat_support(main: dict, team_a_rows: list, team_b_rows: list) -> dict:
    missing = []
    for f in REQUIRED_STAT_FIELDS:
        ha_key = f"hfgam" if f == "FGA" else None
        # check team_a rows
        if team_a_rows and sum(1 for r in team_a_rows[:5] if r.get(f, 0) == 0) >= 3:
            missing.append(f)
    status = "OFF" if missing else "ON"
    return {"status": status, "missing_fields": missing}


# ─────────────────────────────────────────────
# STEP 3 — history zones
# ─────────────────────────────────────────────
def zone_over_under(values: list, lines: list) -> list:
    n = len(values)
    if n == 0:
        return []
    result = []
    for line in lines:
        over  = sum(1 for v in values if v > line)
        under = sum(1 for v in values if v < line)
        result.append({
            "line": line,
            "over_hits": over, "over_rate": rate(over, n),
            "under_hits": under, "under_rate": rate(under, n),
            "n": n,
        })
    return result

def history_match_total(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    ta_totals = [r["total"] for r in rows_a]
    tb_totals = [r["total"] for r in rows_b]
    pooled    = ta_totals + tb_totals
    h2h_t     = [r["total"] for r in h2h_rows]
    return {
        "team_a": zone_over_under(ta_totals, MATCH_TOTAL_LINES),
        "team_b": zone_over_under(tb_totals, MATCH_TOTAL_LINES),
        "pooled70": zone_over_under(pooled, MATCH_TOTAL_LINES),
        "h2h": zone_over_under(h2h_t, MATCH_TOTAL_LINES),
    }

def history_quarter_total(rows_a: list, rows_b: list, quarter: str) -> dict:
    key = f"{quarter}_total"
    ta = [r[key] for r in rows_a]
    tb = [r[key] for r in rows_b]
    pooled = ta + tb
    return {
        "quarter": quarter,
        "team_a": zone_over_under(ta, QUARTER_TOTAL_LINES),
        "team_b": zone_over_under(tb, QUARTER_TOTAL_LINES),
        "pooled70": zone_over_under(pooled, QUARTER_TOTAL_LINES),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BASKETBALL HISTORY ZONES MODULE  (ТЗ §4-6)
# ─────────────────────────────────────────────────────────────────────────────

def _zone_label(r) -> str:
    """ТЗ §4: assign zone label from hit-rate. Returns None-safe label."""
    if r is None:  return "N/A"
    if r >= 1.0:   return "100%"
    if r >= 0.95:  return "95%+"
    if r >= 0.90:  return "90%+"
    if r >= 0.85:  return "85%+"
    if r >= 0.80:  return "80%+"
    if r >= 0.78:  return "78%+"
    if r >= 0.75:  return "75%+"
    if r >= 0.70:  return "70%+"
    return "below_70"


def _desc_stats(vals: list) -> dict:
    """
    ТЗ §0: descriptive stats for a sample — average/median/min/max/p25/p75/p90.
    Returns empty dict if no data.
    """
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)

    def _pct(p):
        k = (n - 1) * p / 100.0
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return round(s[int(k)], 2)
        return round(s[lo] * (hi - k) + s[hi] * (k - lo), 2)

    return {
        "avg":     round(sum(vals) / n, 2),
        "median":  round(_pct(50), 2),
        "min":     round(s[0], 2),
        "max":     round(s[-1], 2),
        "p25":     _pct(25),
        "p75":     _pct(75),
        "p90":     _pct(90),
        "n":       n,
    }


def _hist_zone_threshold(values_a: list, values_b: list, h2h_values: list,
                          line: float, side: str) -> dict:
    """
    ТЗ §3: compute over/under hits, rate, smoothed, zone for one threshold.
    side: 'over'  → count(value > line)
          'under' → count(value <= line)
    """
    def _count(vals, ln, s):
        if s == "over":
            return sum(1 for v in vals if v > ln)
        return sum(1 for v in vals if v <= ln)

    na, nb, nh = len(values_a), len(values_b), len(h2h_values)

    if na > 0:
        a_hits = _count(values_a, line, side)
        a_rate = round(a_hits / na, 3)
        a_sm   = round((a_hits + 1) / (na + 2), 3)
        a_zone = _zone_label(a_rate)
        a_status = None
    else:
        a_hits = None
        a_rate = None
        a_sm   = None
        a_zone = None
        a_status = "OFF"

    if nb > 0:
        b_hits = _count(values_b, line, side)
        b_rate = round(b_hits / nb, 3)
        b_sm   = round((b_hits + 1) / (nb + 2), 3)
        b_zone = _zone_label(b_rate)
        b_status = None
    else:
        b_hits = None
        b_rate = None
        b_sm   = None
        b_zone = None
        b_status = "OFF"

    # pooled (ТЗ §3): team_a + team_b, NOT mixed with H2H
    p_a_hits = _count(values_a, line, side) if na > 0 else 0
    p_b_hits = _count(values_b, line, side) if nb > 0 else 0
    p_hits = p_a_hits + p_b_hits
    p_n    = na + nb
    if p_n > 0:
        p_rate = round(p_hits / p_n, 3)
        p_sm   = round((p_hits + 1) / (p_n + 2), 3)
        p_zone = _zone_label(p_rate)
        p_status = None
    else:
        p_hits = None
        p_rate = None
        p_sm   = None
        p_zone = None
        p_status = "OFF"

    if nh > 0:
        h_hits = _count(h2h_values, line, side)
        h_rate = round(h_hits / nh, 3)
        h_sm   = round((h_hits + 1) / (nh + 2), 3)
        h_zone = _zone_label(h_rate)
        h_status = None
    else:
        h_hits = None
        h_rate = None
        h_sm   = None
        h_zone = None
        h_status = "OFF"

    result = {
        "market":         None,   # filled by caller
        "line":           line,
        "side":           side.upper(),
        "team_a_hits":    a_hits,   "team_a_n":        na,
        "team_a_rate":    a_rate,   "team_a_smoothed":  a_sm,
        "team_a_zone":    a_zone,
        "team_b_hits":    b_hits,   "team_b_n":        nb,
        "team_b_rate":    b_rate,   "team_b_smoothed":  b_sm,
        "team_b_zone":    b_zone,
        "pooled_hits":    p_hits,   "pooled_n":        p_n,
        "pooled_rate":    p_rate,   "pooled_smoothed":  p_sm,
        "pooled_zone":    p_zone,
        "h2h_hits":       h_hits,   "h2h_n":           nh,
        "h2h_rate":       h_rate,   "h2h_smoothed":    h_sm,
        "h2h_zone":       h_zone,
    }
    if a_status:
        result["team_a_field_status"] = a_status
    if b_status:
        result["team_b_field_status"] = b_status
    if p_status:
        result["pooled_field_status"] = p_status
    if h_status:
        result["h2h_field_status"] = h_status
    return result


def _build_zone_block(market: str, values_a: list, values_b: list,
                       h2h_values: list, lines: list) -> dict:
    """
    Build full over+under zone list for one market across all thresholds.
    Returns dict with 'thresholds' list and 'descriptive_stats' per sample.
    ТЗ §0: includes average/median/min/max/p25/p75/p90 for team_a, team_b, pooled, h2h.
    """
    thresholds = []
    for line in lines:
        for side in ("over", "under"):
            entry = _hist_zone_threshold(values_a, values_b, h2h_values, line, side)
            entry["market"] = market
            thresholds.append(entry)

    pooled_values = values_a + values_b
    return {
        "market": market,
        "thresholds": thresholds,
        "descriptive_stats": {
            "team_a":  _desc_stats(values_a),
            "team_b":  _desc_stats(values_b),
            "pooled":  _desc_stats(pooled_values),
            "h2h":     _desc_stats(h2h_values),
        },
    }


def _build_team_zone_block(market_scored: str, market_allowed: str,
                            scored_a: list, allowed_a: list,
                            scored_b: list, allowed_b: list,
                            h2h_scored_a: list, h2h_allowed_a: list,
                            h2h_scored_b: list, h2h_allowed_b: list,
                            lines: list) -> dict:
    """
    ТЗ §5B / §6B: team points zones — scored and allowed for both teams.
    Returns dict with keys: team_a_scored, team_a_allowed,
                            team_b_scored, team_b_allowed.
    Each entry has 'thresholds' list and 'descriptive_stats'.

    For individual team markets:
      - team_a_scored: values_a = team A scored only; pooled = team A only (not mixed with B)
        H2H: scored from team A perspective in H2H matches
      - team_b_scored: values_a = team B scored only; pooled = team B only
        H2H: scored from team B perspective in H2H matches
    ТЗ §3: H2H рахувати окремо. H2H НЕ змішувати з pooled.
    """
    def _single_team(market, vals, h2h_vals, lns):
        """Single team — pooled = same team's own sample (na = nb = 0 trick: pass vals as values_a, empty as values_b)."""
        thresholds = []
        for line in lns:
            for side in ("over", "under"):
                # Use values_a = team vals, values_b = [] so pooled = team only
                entry = _hist_zone_threshold(vals, [], h2h_vals, line, side)
                entry["market"] = market
                # If the team sample is empty, mark this entry explicitly
                if not vals:
                    entry["field_status"] = "OFF"
                thresholds.append(entry)
        stats = _desc_stats(vals) if vals else None
        h2h_stats = _desc_stats(h2h_vals) if h2h_vals else None
        result = {
            "market": market,
            "thresholds": thresholds,
            "descriptive_stats": {
                "team":  stats if stats is not None else None,
                "h2h":   h2h_stats if h2h_stats is not None else None,
            },
        }
        if not vals:
            result["field_status"] = "OFF"
        if not h2h_vals:
            result.setdefault("descriptive_stats", {})["h2h_field_status"] = "OFF"
        return result

    return {
        "team_a_scored":  _single_team(
            market_scored + "_TEAM_A_SCORED",  scored_a,  h2h_scored_a,  lines),
        "team_a_allowed": _single_team(
            market_allowed + "_TEAM_A_ALLOWED", allowed_a, h2h_allowed_a, lines),
        "team_b_scored":  _single_team(
            market_scored + "_TEAM_B_SCORED",  scored_b,  h2h_scored_b,  lines),
        "team_b_allowed": _single_team(
            market_allowed + "_TEAM_B_ALLOWED", allowed_b, h2h_allowed_b, lines),
    }


# ТЗ §5A — H1 TOTAL ZONES
def history_h1_total_zones(rows_a: list, rows_b: list, h2h_rows: list) -> list:
    """
    H1 total = Q1 + Q2 combined (both teams).
    Threshold list: H1_TOTAL_LINES.
    """
    vals_a   = [r["h1_total"] for r in rows_a]
    vals_b   = [r["h1_total"] for r in rows_b]
    vals_h2h = [r["h1_total"] for r in h2h_rows]
    return _build_zone_block("H1_TOTAL", vals_a, vals_b, vals_h2h, H1_TOTAL_LINES)


# ТЗ §5B — TEAM H1 POINTS ZONES (scored + allowed)
def history_h1_team_points_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    """
    Team H1 scored = h1_team (Q1+Q2 own points).
    Team H1 allowed = h1_opp (Q1+Q2 opponent points).
    H2H perspective: home team scored = h1_team, away team scored = h1_opp (away POV).
    ТЗ §5B: рахується для Team A scored/allowed та Team B scored/allowed.
    """
    scored_a  = [r["h1_team"] for r in rows_a]
    allowed_a = [r["h1_opp"]  for r in rows_a]
    scored_b  = [r["h1_team"] for r in rows_b]
    allowed_b = [r["h1_opp"]  for r in rows_b]
    # H2H: home perspective for team A, away perspective (opp) for team B
    h2h_scored_a  = [r["h1_team"] for r in h2h_rows]
    h2h_allowed_a = [r["h1_opp"]  for r in h2h_rows]
    h2h_scored_b  = [r["h1_opp"]  for r in h2h_rows]   # away scored = home opp
    h2h_allowed_b = [r["h1_team"] for r in h2h_rows]   # away allowed = home scored
    return _build_team_zone_block(
        "H1", "H1",
        scored_a, allowed_a,
        scored_b, allowed_b,
        h2h_scored_a, h2h_allowed_a,
        h2h_scored_b, h2h_allowed_b,
        H1_TEAM_POINTS_LINES,
    )


# ТЗ §6A — H2 TOTAL ZONES
def history_h2_total_zones(rows_a: list, rows_b: list, h2h_rows: list) -> list:
    """
    H2 total = Q3 + Q4 combined (both teams).
    Threshold list: H2_TOTAL_LINES.
    """
    vals_a   = [r["h2_total"] for r in rows_a]
    vals_b   = [r["h2_total"] for r in rows_b]
    vals_h2h = [r["h2_total"] for r in h2h_rows]
    return _build_zone_block("H2_TOTAL", vals_a, vals_b, vals_h2h, H2_TOTAL_LINES)


# ТЗ §6B — TEAM H2 POINTS ZONES (scored + allowed)
def history_h2_team_points_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    """
    Team H2 scored = h2_team (Q3+Q4 own points).
    Team H2 allowed = h2_opp (Q3+Q4 opponent points).
    H2H perspective: home team scored = h2_team, away team scored = h2_opp (away POV).
    ТЗ §6B: рахується для Team A scored/allowed та Team B scored/allowed.
    """
    scored_a  = [r["h2_team"] for r in rows_a]
    allowed_a = [r["h2_opp"]  for r in rows_a]
    scored_b  = [r["h2_team"] for r in rows_b]
    allowed_b = [r["h2_opp"]  for r in rows_b]
    # H2H: home perspective for team A, away perspective (opp) for team B
    h2h_scored_a  = [r["h2_team"] for r in h2h_rows]
    h2h_allowed_a = [r["h2_opp"]  for r in h2h_rows]
    h2h_scored_b  = [r["h2_opp"]  for r in h2h_rows]
    h2h_allowed_b = [r["h2_team"] for r in h2h_rows]
    return _build_team_zone_block(
        "H2", "H2",
        scored_a, allowed_a,
        scored_b, allowed_b,
        h2h_scored_a, h2h_allowed_a,
        h2h_scored_b, h2h_allowed_b,
        H2_TEAM_POINTS_LINES,
    )


# ТЗ §7A — Q3 TOTAL ZONES
def history_q3_total_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    vals_a   = [r["q3_total"] for r in rows_a]
    vals_b   = [r["q3_total"] for r in rows_b]
    vals_h2h = [r["q3_total"] for r in h2h_rows]
    return _build_zone_block("Q3_TOTAL", vals_a, vals_b, vals_h2h, Q3_TOTAL_LINES)


# ТЗ §7B — TEAM Q3 POINTS ZONES (scored + allowed)
def history_q3_team_points_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    scored_a  = [r["q3_team"] for r in rows_a]
    allowed_a = [r["q3_opp"]  for r in rows_a]
    scored_b  = [r["q3_team"] for r in rows_b]
    allowed_b = [r["q3_opp"]  for r in rows_b]
    h2h_scored_a  = [r["q3_team"] for r in h2h_rows]
    h2h_allowed_a = [r["q3_opp"]  for r in h2h_rows]
    h2h_scored_b  = [r["q3_opp"]  for r in h2h_rows]
    h2h_allowed_b = [r["q3_team"] for r in h2h_rows]
    return _build_team_zone_block(
        "Q3", "Q3",
        scored_a, allowed_a,
        scored_b, allowed_b,
        h2h_scored_a, h2h_allowed_a,
        h2h_scored_b, h2h_allowed_b,
        Q3_TEAM_POINTS_LINES,
    )


# ТЗ §8A — AFTER 3Q TOTAL ZONES
def history_after3q_total_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    vals_a   = [r["after3q_total"] for r in rows_a]
    vals_b   = [r["after3q_total"] for r in rows_b]
    vals_h2h = [r["after3q_total"] for r in h2h_rows]
    return _build_zone_block("AFTER3Q_TOTAL", vals_a, vals_b, vals_h2h, AFTER3Q_TOTAL_LINES)


# ТЗ §8B — TEAM AFTER 3Q POINTS ZONES (scored + allowed)
def history_after3q_team_points_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    scored_a  = [r["after3q_team"] for r in rows_a]
    allowed_a = [r["after3q_opp"]  for r in rows_a]
    scored_b  = [r["after3q_team"] for r in rows_b]
    allowed_b = [r["after3q_opp"]  for r in rows_b]
    h2h_scored_a  = [r["after3q_team"] for r in h2h_rows]
    h2h_allowed_a = [r["after3q_opp"]  for r in h2h_rows]
    h2h_scored_b  = [r["after3q_opp"]  for r in h2h_rows]
    h2h_allowed_b = [r["after3q_team"] for r in h2h_rows]
    return _build_team_zone_block(
        "AFTER3Q", "AFTER3Q",
        scored_a, allowed_a,
        scored_b, allowed_b,
        h2h_scored_a, h2h_allowed_a,
        h2h_scored_b, h2h_allowed_b,
        AFTER3Q_TEAM_POINTS_LINES,
    )


# ТЗ §9A — Q4 TOTAL ZONES
def history_q4_total_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    vals_a   = [r["q4_total"] for r in rows_a]
    vals_b   = [r["q4_total"] for r in rows_b]
    vals_h2h = [r["q4_total"] for r in h2h_rows]
    return _build_zone_block("Q4_TOTAL", vals_a, vals_b, vals_h2h, Q4_TOTAL_LINES)


# ТЗ §9B — TEAM Q4 POINTS ZONES (scored + allowed)
def history_q4_team_points_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    scored_a  = [r["q4_team"] for r in rows_a]
    allowed_a = [r["q4_opp"]  for r in rows_a]
    scored_b  = [r["q4_team"] for r in rows_b]
    allowed_b = [r["q4_opp"]  for r in rows_b]
    h2h_scored_a  = [r["q4_team"] for r in h2h_rows]
    h2h_allowed_a = [r["q4_opp"]  for r in h2h_rows]
    h2h_scored_b  = [r["q4_opp"]  for r in h2h_rows]
    h2h_allowed_b = [r["q4_team"] for r in h2h_rows]
    return _build_team_zone_block(
        "Q4", "Q4",
        scored_a, allowed_a,
        scored_b, allowed_b,
        h2h_scored_a, h2h_allowed_a,
        h2h_scored_b, h2h_allowed_b,
        Q4_TEAM_POINTS_LINES,
    )


# ─────────────────────────────────────────────────────────────────────────────

def history_half_total(rows_a: list, rows_b: list, h2h_rows: list,
                       lines: list) -> dict:
    """
    Для каждой BK-линии из half_total (например, 86.5) считает реальную историю:
      - сколько матчей team_a / team_b завершили первую половину с тоталом <= line (Under)
      - сколько с тоталом >  line (Over)
      - pooled70 (сумма обеих команд, N=na+nb)
      - smoothed (Laplace +1/+2)
      - H2H если есть
    Статус OFF проставляется ТОЛЬКО если физически нет строк в rows_a или rows_b.
    """
    if not lines:
        return {}

    na = len(rows_a)
    nb = len(rows_b)
    nh = len(h2h_rows)

    result = {}
    for line in lines:
        line_key = str(line)

        # Team A
        if na > 0:
            a_under = sum(1 for r in rows_a if r["h1_total"] <= line)
            a_over  = na - a_under
            a_under_rate = round(a_under / na, 3)
            a_over_rate  = round(a_over  / na, 3)
            a_smoothed_under = round((a_under + 1) / (na + 2), 3)
            a_smoothed_over  = round((a_over  + 1) / (na + 2), 3)
            team_a_block = {
                "n":             na,
                "under_hits":    a_under,
                "under_rate":    a_under_rate,
                "over_hits":     a_over,
                "over_rate":     a_over_rate,
                "smoothed_under": a_smoothed_under,
                "smoothed_over":  a_smoothed_over,
                "P_hist_exact":  "ON",
            }
        else:
            team_a_block = {"n": 0, "P_hist_exact": "OFF",
                            "under_hits": None, "over_hits": None,
                            "under_rate": None, "over_rate": None}

        # Team B
        if nb > 0:
            b_under = sum(1 for r in rows_b if r["h1_total"] <= line)
            b_over  = nb - b_under
            b_under_rate = round(b_under / nb, 3)
            b_over_rate  = round(b_over  / nb, 3)
            b_smoothed_under = round((b_under + 1) / (nb + 2), 3)
            b_smoothed_over  = round((b_over  + 1) / (nb + 2), 3)
            team_b_block = {
                "n":             nb,
                "under_hits":    b_under,
                "under_rate":    b_under_rate,
                "over_hits":     b_over,
                "over_rate":     b_over_rate,
                "smoothed_under": b_smoothed_under,
                "smoothed_over":  b_smoothed_over,
                "P_hist_exact":  "ON",
            }
        else:
            team_b_block = {"n": 0, "P_hist_exact": "OFF",
                            "under_hits": None, "over_hits": None,
                            "under_rate": None, "over_rate": None}

        # Pooled70 (Team A + Team B combined)
        if na > 0 and nb > 0:
            pooled_n     = na + nb
            pool_under   = a_under + b_under
            pool_over    = a_over  + b_over
            pool_u_rate  = round(pool_under / pooled_n, 3)
            pool_o_rate  = round(pool_over  / pooled_n, 3)
            pool_sm_under = round((pool_under + 1) / (pooled_n + 2), 3)
            pool_sm_over  = round((pool_over  + 1) / (pooled_n + 2), 3)
            pooled_block = {
                "n":              pooled_n,
                "under_hits":     pool_under,
                "under_rate":     pool_u_rate,
                "over_hits":      pool_over,
                "over_rate":      pool_o_rate,
                "smoothed_under": pool_sm_under,
                "smoothed_over":  pool_sm_over,
                "P_hist_exact":   "ON",
                "pct_str_under":  f"{pool_under}/{pooled_n}",
                "pct_str_over":   f"{pool_over}/{pooled_n}",
            }
        else:
            pooled_block = {"n": (na + nb), "P_hist_exact": "OFF"}

        # H2H
        if nh > 0:
            h_under = sum(1 for r in h2h_rows if r["h1_total"] <= line)
            h_over  = nh - h_under
            h2h_block = {
                "n":          nh,
                "under_hits": h_under,
                "under_rate": round(h_under / nh, 3),
                "over_hits":  h_over,
                "over_rate":  round(h_over  / nh, 3),
                "P_hist_exact": "ON",
                "pct_str_under": f"{h_under}/{nh}",
                "pct_str_over":  f"{h_over}/{nh}",
            }
        else:
            h2h_block = {"n": 0, "P_hist_exact": "OFF"}

        # Overall pass/fail gate for this line
        p_hist_exact = "ON" if (na > 0 and nb > 0) else "OFF"

        result[line_key] = {
            "line":          line,
            "P_hist_exact":  p_hist_exact,
            "team_a":        team_a_block,
            "team_b":        team_b_block,
            "pooled70":      pooled_block,
            "h2h":           h2h_block,
        }

    return result


def history_team_it_match(rows_a: list, rows_b: list) -> dict:
    """scored+allowed gate for match IT"""
    results = {}
    na, nb = len(rows_a), len(rows_b)
    for line in TEAM_IT_LINES:
        # Team A over line
        own_a = sum(1 for r in rows_a if r["team_points"] > line)
        allowed_b = sum(1 for r in rows_b if r["opp_points"] > line)  # B allows > line to opponents
        gate_a_over = min(rate(own_a, na), rate(allowed_b, nb))
        # Team A under
        own_a_under = sum(1 for r in rows_a if r["team_points"] < line)
        allowed_b_under = sum(1 for r in rows_b if r["opp_points"] < line)
        gate_a_under = min(rate(own_a_under, na), rate(allowed_b_under, nb))
        # Team B over
        own_b = sum(1 for r in rows_b if r["team_points"] > line)
        allowed_a = sum(1 for r in rows_a if r["opp_points"] > line)
        gate_b_over = min(rate(own_b, nb), rate(allowed_a, na))
        # Team B under
        own_b_under = sum(1 for r in rows_b if r["team_points"] < line)
        allowed_a_under = sum(1 for r in rows_a if r["opp_points"] < line)
        gate_b_under = min(rate(own_b_under, nb), rate(allowed_a_under, na))

        results[str(line)] = {
            "line": line,
            "team_a_over":  {"own_scored": pct_str(own_a, na), "own_scored_rate": rate(own_a, na), "opponent_allowed": pct_str(allowed_b, nb), "opponent_allowed_rate": rate(allowed_b, nb), "final_gate": gate_a_over},
            "team_a_under": {"own_scored": pct_str(own_a_under, na), "own_scored_rate": rate(own_a_under, na), "opponent_allowed": pct_str(allowed_b_under, nb), "opponent_allowed_rate": rate(allowed_b_under, nb), "final_gate": gate_a_under},
            "team_b_over":  {"own_scored": pct_str(own_b, nb), "own_scored_rate": rate(own_b, nb), "opponent_allowed": pct_str(allowed_a, na), "opponent_allowed_rate": rate(allowed_a, na), "final_gate": gate_b_over},
            "team_b_under": {"own_scored": pct_str(own_b_under, nb), "own_scored_rate": rate(own_b_under, nb), "opponent_allowed": pct_str(allowed_a_under, na), "opponent_allowed_rate": rate(allowed_a_under, na), "final_gate": gate_b_under},
        }
    return results

def history_team_it_quarter(rows_a: list, rows_b: list, quarter: str) -> dict:
    qt = f"{quarter}_team"
    qo = f"{quarter}_opp"
    na, nb = len(rows_a), len(rows_b)
    results = {}
    for line in QUARTER_IT_LINES:
        own_a = sum(1 for r in rows_a if r[qt] > line)
        allowed_b = sum(1 for r in rows_b if r[qo] > line)
        gate_a = min(rate(own_a, na), rate(allowed_b, nb))
        own_b = sum(1 for r in rows_b if r[qt] > line)
        allowed_a = sum(1 for r in rows_a if r[qo] > line)
        gate_b = min(rate(own_b, nb), rate(allowed_a, na))
        results[str(line)] = {
            "line": line,
            "team_a_over": {"own_scored": pct_str(own_a, na), "own_scored_rate": rate(own_a, na), "opponent_allowed": pct_str(allowed_b, nb), "opponent_allowed_rate": rate(allowed_b, nb), "final_gate": gate_a},
            "team_b_over": {"own_scored": pct_str(own_b, nb), "own_scored_rate": rate(own_b, nb), "opponent_allowed": pct_str(allowed_a, na), "opponent_allowed_rate": rate(allowed_a, na), "final_gate": gate_b},
        }
    return results

def history_spread(rows: list, lines: list, margin_key: str = "margin") -> list:
    n = len(rows)
    result = []
    for spread in lines:
        covers = sum(1 for r in rows if r[margin_key] + spread > 0)
        result.append({"spread": spread, "cover_hits": covers, "cover_rate": rate(covers, n), "n": n})
    return result

def history_allowed(rows_a: list, rows_b: list) -> dict:
    na, nb = len(rows_a), len(rows_b)
    ta_opp = [r["opp_points"] for r in rows_a]
    tb_opp = [r["opp_points"] for r in rows_b]
    return {
        "team_a_opp_allowed": zone_over_under(ta_opp, ALLOWED_LINES),
        "team_b_opp_allowed": zone_over_under(tb_opp, ALLOWED_LINES),
    }

def history_at_least_one_quarter(rows: list) -> dict:
    """any_q_21_plus, any_q_18_minus, unmet patterns + unmet_threshold_signal"""
    n = len(rows)
    if n == 0:
        return {}
    over_thresholds  = [16, 18, 20, 21, 23, 25, 28, 30, 32]
    under_thresholds = [8, 10, 12, 14, 16, 18, 19, 20]
    result = {"any_q_over": {}, "any_q_under": {}, "unmet_signals": {}}

    for t in over_thresholds:
        hits = sum(1 for r in rows if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) >= t)
        result["any_q_over"][str(t)] = pct_str(hits, n)
        result["any_q_over"][f"{t}_rate"] = round(hits / n, 3) if n else 0.0

    for t in under_thresholds:
        hits = sum(1 for r in rows if min(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) <= t)
        result["any_q_under"][str(t)] = pct_str(hits, n)

    # Unmet pattern: state where neither Q1 nor Q2 hit threshold, then Q3 or Q4 does
    for t in [18, 20, 21, 23, 25]:
        state_rows = [r for r in rows if r["q1_team"] < t and r["q2_team"] < t]
        if state_rows:
            future = sum(1 for r in state_rows if r["q3_team"] >= t or r["q4_team"] >= t)
            unmet_rate = round(future / len(state_rows), 3)
            result[f"unmet_after_ht_{t}"] = pct_str(future, len(state_rows))
            # Ready signal for GPT: ON if any_q_rate >= 0.80 AND unmet_rate >= 0.60
            any_q_rate = round(
                sum(1 for r in rows if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) >= t) / n, 3
            ) if n else 0.0
            result["unmet_signals"][str(t)] = {
                "threshold":       t,
                "any_q_rate":      any_q_rate,
                "unmet_n":         len(state_rows),
                "unmet_hits":      future,
                "unmet_rate":      unmet_rate,
                "signal": (
                    "STRONG"  if any_q_rate >= 0.90 and unmet_rate >= 0.60 else
                    "MEDIUM"  if any_q_rate >= 0.80 and unmet_rate >= 0.50 else
                    "WEAK"    if any_q_rate >= 0.73 else
                    "OFF"
                ),
            }

    return result


# ─────────────────────────────────────────────
# STEP 4 — conditional HT scanner
# ─────────────────────────────────────────────
def ht_margin_bucket(margin: int) -> str:
    if margin <= -20:   return "trail_20+"
    if margin <= -15:   return "trail_15_19"
    if margin <= -10:   return "trail_10_14"
    if margin <= -5:    return "trail_5_9"
    if margin <= -1:    return "trail_1_4"
    if margin == 0:     return "tie"
    if margin <= 4:     return "lead_1_4"
    if margin <= 9:     return "lead_5_9"
    if margin <= 14:    return "lead_10_14"
    if margin <= 19:    return "lead_15_19"
    return "lead_20+"

def quarter_state(q1_win: bool, q2_win: bool) -> str:
    wins = int(q1_win) + int(q2_win)
    if wins == 2: return "2-0"
    if wins == 1: return "1-1"
    return "0-2"

def ht_total_bucket(h1_total: float, hist_h1_totals: list) -> str:
    if not hist_h1_totals or h1_total is None:
        return "unknown"
    p25 = percentile(hist_h1_totals, 25)
    p75 = percentile(hist_h1_totals, 75)
    p90 = percentile(hist_h1_totals, 90)
    if h1_total >= p90: return "top"
    if h1_total >= p75: return "high"
    if h1_total >= p25: return "middle"
    return "low"
def conditional_scanner(rows: list, margin_bkt: str, q_state: str, ht_total_bkt: str,
                         needed_q3: float, needed_2h: float,
                         needed_team_q3: float, needed_team_2h: float) -> dict:
    """Legacy single-bucket scanner — kept for backward compat with scenario block."""
    state_rows = [r for r in rows
                  if ht_margin_bucket(r["h1_margin"]) == margin_bkt
                  and quarter_state(r["q1_margin"] > 0, r["q2_margin"] > 0) == q_state]
    n = len(state_rows)
    if n == 0:
        return {"n": 0, "note": "no matching state rows"}
    return {
        "margin_bucket": margin_bkt, "quarter_state": q_state, "ht_total_bucket": ht_total_bkt, "n": n,
        "q3_total_over":   pct_str(sum(1 for r in state_rows if r["q3_total"]  >= needed_q3),  n),
        "h2_total_over":   pct_str(sum(1 for r in state_rows if r["h2_total"]  >= needed_2h),  n),
        "team_q3_over":    pct_str(sum(1 for r in state_rows if r["q3_team"]   >= needed_team_q3), n),
        "team_2h_over":    pct_str(sum(1 for r in state_rows if r["h2_team"]   >= needed_team_2h), n),
    }


# ── ТЗ §10: full conditional history zones ───────────────────────────────────

def _cond_zone_block(sub_rows: list, pooled_n: int, label: str) -> dict:
    """
    ТЗ §10: compute conditional stats + over/under zones for one bucket.
    Returns format matching ТЗ §10 spec exactly.
    """
    n = len(sub_rows)
    if n == 0:
        return {"condition": label, "n": 0, "coverage": 0.0, "metrics": {}, "zones": []}

    coverage = round(n / pooled_n, 3) if pooled_n > 0 else 0.0

    h2_vals   = [r["h2_total"]      for r in sub_rows]
    q3_vals   = [r["q3_total"]      for r in sub_rows]
    aq3_vals  = [r["after3q_total"] for r in sub_rows]
    q4_vals   = [r["q4_total"]      for r in sub_rows]

    def _stats(vals):
        if not vals:
            return {}
        s = sorted(vals)
        nn = len(s)
        def _p(p):
            k = (nn - 1) * p / 100.0
            lo, hi = math.floor(k), math.ceil(k)
            return round(s[lo] * (hi - k) + s[hi] * (k - lo), 2) if lo != hi else round(s[int(k)], 2)
        return {
            "avg":    round(sum(vals) / nn, 2),
            "median": _p(50),
            "min":    round(s[0], 2),
            "max":    round(s[-1], 2),
            "p25":    _p(25),
            "p75":    _p(75),
            "p90":    _p(90),
        }

    metrics = {
        "avg_h2_total":    _stats(h2_vals).get("avg"),
        "median_h2_total": _stats(h2_vals).get("median"),
        "min_h2_total":    _stats(h2_vals).get("min"),
        "max_h2_total":    _stats(h2_vals).get("max"),
        "p25_h2_total":    _stats(h2_vals).get("p25"),
        "p75_h2_total":    _stats(h2_vals).get("p75"),
        "p90_h2_total":    _stats(h2_vals).get("p90"),
    }

    zones = []
    for market, vals, lines in [
        ("H2_TOTAL",     h2_vals,  H2_TOTAL_LINES),
        ("Q3_TOTAL",     q3_vals,  Q3_TOTAL_LINES),
        ("AFTER3Q_TOTAL",aq3_vals, AFTER3Q_TOTAL_LINES),
        ("Q4_TOTAL",     q4_vals,  Q4_TOTAL_LINES),
    ]:
        for line in lines:
            for side, pred in [("OVER", lambda v, l=line: v > l), ("UNDER", lambda v, l=line: v <= l)]:
                hits = sum(1 for v in vals if pred(v))
                r_val = round(hits / n, 3) if n > 0 else 0.0
                sm    = round((hits + 1) / (n + 2), 3)
                zones.append({
                    "market":   market,
                    "line":     line,
                    "side":     side,
                    "hits":     hits,
                    "n":        n,
                    "rate":     r_val,
                    "smoothed": sm,
                    "zone":     _zone_label(r_val),
                })

    return {"condition": label, "n": n, "coverage": coverage, "metrics": metrics, "zones": zones}


# Fixed-value HT total buckets per ТЗ §10
_HT_TOTAL_BUCKETS = [
    ("ht_le60",   lambda r: r["h1_total"] <= 60),
    ("ht_61_70",  lambda r: 61 <= r["h1_total"] <= 70),
    ("ht_71_80",  lambda r: 71 <= r["h1_total"] <= 80),
    ("ht_81_90",  lambda r: 81 <= r["h1_total"] <= 90),
    ("ht_91_100", lambda r: 91 <= r["h1_total"] <= 100),
    ("ht_101p",   lambda r: r["h1_total"] >= 101),
]

_HT_MARGIN_BUCKETS = [
    ("margin_tie",   lambda r: r["h1_margin"] == 0),
    ("margin_1_4",   lambda r: 1 <= abs(r["h1_margin"]) <= 4),
    ("margin_5_9",   lambda r: 5 <= abs(r["h1_margin"]) <= 9),
    ("margin_10_14", lambda r: 10 <= abs(r["h1_margin"]) <= 14),
    ("margin_15_19", lambda r: 15 <= abs(r["h1_margin"]) <= 19),
    ("margin_20p",   lambda r: abs(r["h1_margin"]) >= 20),
]

_Q_STATE_BUCKETS = [
    ("qstate_2_0", lambda r: quarter_state(r["q1_margin"] > 0, r["q2_margin"] > 0) == "2-0"),
    ("qstate_1_1", lambda r: quarter_state(r["q1_margin"] > 0, r["q2_margin"] > 0) == "1-1"),
    ("qstate_0_2", lambda r: quarter_state(r["q1_margin"] > 0, r["q2_margin"] > 0) == "0-2"),
]

_TEAM_STATE_BUCKETS = [
    ("team_leading",  lambda r: r["h1_margin"] > 0),
    ("team_trailing", lambda r: r["h1_margin"] < 0),
    ("team_tied",     lambda r: r["h1_margin"] == 0),
]


def compute_conditional_history_zones(rows_a: list, rows_b: list, h2h_rows: list) -> dict:
    """
    ТЗ §10: enumerate all conditional bucket combinations.
    Pooled = rows_a + rows_b.  H2H computed separately.
    """
    pooled = rows_a + rows_b
    pooled_n = len(pooled)

    def _buckets(rows, bucket_defs, suffix=""):
        out = []
        for label, pred in bucket_defs:
            sub = [r for r in rows if pred(r)]
            out.append(_cond_zone_block(sub, pooled_n, label + suffix))
        return out

    # by_ht_total_bucket
    by_ht_total = _buckets(pooled, _HT_TOTAL_BUCKETS)

    # by_ht_margin_bucket
    by_ht_margin = _buckets(pooled, _HT_MARGIN_BUCKETS)

    # by_quarter_state_after_h1
    by_qstate = []
    for key, filt in _Q_STATE_BUCKETS:
        sub = [r for r in pooled if filt(r)]
        block = _cond_zone_block(sub, pooled_n, key)
        # Anti-sweep aggregate: скільки разів команда програвала ВСІ 4 чверті в цьому стані
        if sub:
            lost_all_4 = sum(
                1 for r in sub
                if r["q1_team"] < r["q1_opp"]
                and r["q2_team"] < r["q2_opp"]
                and r["q3_team"] < r["q3_opp"]
                and r["q4_team"] < r["q4_opp"]
            )
            ns = len(sub)
            lar = round(lost_all_4 / ns, 3) if ns else 0.0
            block["anti_sweep_in_state"] = {
                "lost_all_4":      lost_all_4,
                "n":               ns,
                "lost_all_4_rate": lar,
                "signal": (
                    "STRONG_ANTI_SWEEP" if lar <= 0.10 else
                    "MEDIUM_ANTI_SWEEP" if lar <= 0.20 else
                    "OFF"
                ),
            }
        by_qstate.append(block)

    # by_team_state_after_h1
    by_team_state = _buckets(pooled, _TEAM_STATE_BUCKETS)

    # combined_buckets: cross product of ht_total × ht_margin × qstate (most useful combos)
    combined = []
    for ht_lbl, ht_pred in _HT_TOTAL_BUCKETS:
        for mg_lbl, mg_pred in _HT_MARGIN_BUCKETS:
            for qs_lbl, qs_pred in _Q_STATE_BUCKETS:
                combo_label = f"{ht_lbl}_{mg_lbl}_{qs_lbl}"
                sub = [r for r in pooled if ht_pred(r) and mg_pred(r) and qs_pred(r)]
                if len(sub) >= 3:  # skip empty/tiny combos
                    combined.append(_cond_zone_block(sub, pooled_n, combo_label))

    return {
        "by_ht_total_bucket":       by_ht_total,
        "by_ht_margin_bucket":      by_ht_margin,
        "by_quarter_state_after_h1": by_qstate,
        "by_team_state_after_h1":   by_team_state,
        "combined_buckets":         combined,
    }


# ─────────────────────────────────────────────
# STEP 5 — stat percentile zones & patterns
# ─────────────────────────────────────────────
STAT_FIELDS = ["FGA","poss","2PA","efg","3PA","three_pa_share","FTA","ftr",
               "ORB","DRB","REB","AST","TO","fouls","off_rtg","extra_poss"]

def stat_percentile_zones(rows: list, segment_prefix: str = "") -> dict:
    result = {}
    for field in STAT_FIELDS:
        vals = [r.get(field, 0) for r in rows if r.get(field) is not None]
        if vals:
            result[field] = percentiles(vals)
    return result

def stat_impact(rows: list) -> list:
    """Test single-stat impact on scoring targets"""
    results = []
    n = len(rows)
    if n < 5:
        return results

    def test(label, filter_fn, target_fn):
        sub = [r for r in rows if filter_fn(r)]
        if len(sub) < 3:
            return
        hits = sum(1 for r in sub if target_fn(r))
        results.append({"pattern": label, "sub_n": len(sub), "hits": hits, "rate": rate(hits, len(sub))})

    fga_p80 = percentile([r["FGA"] for r in rows], 80)
    fta_p75 = percentile([r["FTA"] for r in rows], 75)
    efg_p75 = percentile([r["efg"] for r in rows], 75)
    efg_p25 = percentile([r["efg"] for r in rows], 25)
    orb_p75 = percentile([r["ORB"] for r in rows], 75)
    to_p25  = percentile([r["TO"]  for r in rows], 25)
    pa3_p75 = percentile([r["3PA"] for r in rows], 75)
    poss_p75= percentile([r.get("poss",0) for r in rows], 75)

    for t in [18, 20, 21, 23, 25]:
        test(f"Q3_team>={t} when FGA>=p80",
             lambda r, p=fga_p80: r["FGA"] >= p,
             lambda r, v=t: r["q3_team"] >= v)
        test(f"Q3_team>={t} when eFG>=p75",
             lambda r, p=efg_p75: r["efg"] >= p,
             lambda r, v=t: r["q3_team"] >= v)

    for t in [39, 41, 43, 46]:
        test(f"Q3_total>={t} when poss>=p75+eFG>=p75",
             lambda r, pp=poss_p75, ep=efg_p75: r.get("poss",0) >= pp and r["efg"] >= ep,
             lambda r, v=t: r["q3_total"] >= v)

    for t in [75, 80, 85, 90, 95]:
        test(f"team_final>={t} when FGA>=p80",
             lambda r, p=fga_p80: r["FGA"] >= p,
             lambda r, v=t: r["team_points"] >= v)

    return results

def sample_strength_label(n: int) -> str:
    if n >= 65:
        return "very_strong"
    if n >= 50:
        return "strong"
    if n >= 35:
        return "medium"
    if n >= 20:
        return "small"
    return "very_small"

COMBO_PATTERNS = [
    ("FGA_high+eFG_high",   lambda r, pf: r["FGA"] >= pf["FGA"]["p75"] and r["efg"] >= pf["efg"]["p75"]),
    ("Poss_high+eFG_high",  lambda r, pf: r.get("poss",0) >= pf["poss"]["p75"] and r["efg"] >= pf["efg"]["p75"]),
    ("FGA+FTA+eFG_high",    lambda r, pf: r["FGA"] >= pf["FGA"]["p75"] and r["FTA"] >= pf["FTA"]["p75"] and r["efg"] >= pf["efg"]["p75"]),
    ("3PA_high+3Pct_high",  lambda r, pf: r["3PA"] >= pf["3PA"]["p75"] and (r["3PA"]>0 and r.get("3PM",0)/r["3PA"] >= pf.get("3pct",{}).get("p75",0.5) if r["3PA"]>0 else False)),
    ("2PA_high+2Pct_high",  lambda r, pf: r["2PA"] >= pf["2PA"]["p75"] and (r["2PA"]>0 and r.get("2PM",0)/r["2PA"] >= 0.55)),
    ("FTA_high+low_TO",     lambda r, pf: r["FTA"] >= pf["FTA"]["p75"] and r["TO"] <= pf["TO"]["p25"]),
    ("ORB_high+low_eFG",    lambda r, pf: r["ORB"] >= pf["ORB"]["p75"] and r["efg"] <= pf["efg"]["p25"]),
    ("FGA_high+eFG_low",    lambda r, pf: r["FGA"] >= pf["FGA"]["p75"] and r["efg"] <= pf["efg"]["p25"]),
    ("FGA_low+FTA_low+eFG_low", lambda r, pf: r["FGA"] <= pf["FGA"]["p25"] and r["FTA"] <= pf["FTA"]["p25"] and r["efg"] <= pf["efg"]["p25"]),
    ("TO_high+FTA_low",     lambda r, pf: r["TO"] >= pf["TO"]["p75"] and r["FTA"] <= pf["FTA"]["p25"]),
]

def combo_stat_patterns(rows: list, pf: dict) -> list:
    n = len(rows)
    results = []
    for name, fn in COMBO_PATTERNS:
        try:
            sub = [r for r in rows if fn(r, pf)]
        except Exception:
            continue
        if len(sub) < 3:
            continue
        for t in [75, 80, 85]:
            hits = sum(1 for r in sub if r["team_points"] >= t)
            if hits > 0:
                results.append({"pattern": name, "target": f"team_final>={t}",
                                 "sub_n": len(sub), "hits": hits, "rate": rate(hits, len(sub))})
        # Q3 over
        for t in [39, 41, 43]:
            hits = sum(1 for r in sub if r["q3_total"] >= t)
            if hits > 0:
                results.append({"pattern": name, "target": f"q3_total>={t}",
                                 "sub_n": len(sub), "hits": hits, "rate": rate(hits, len(sub))})
    return results


# ─────────────────────────────────────────────
# STEP 6 — expected score projection
# ─────────────────────────────────────────────
def regressed_ppp(team_rows: list, segment: str, current_pts: float,
                  current_poss: float, lambda_: float = 0.50,
                  scenario_rows: list = None,
                  opp_rows: list = None) -> float:
    """
    ТЗ §9.1: hist_expected_ppp = 0.60 * team_ppp_hist + 0.40 * opp_allowed_ppp_hist
    If scenario N >= 6: 0.50 * team + 0.30 * opp_allowed + 0.20 * scenario_ppp
    """
    pt_key     = f"{segment}_team" if segment not in ("match", "h1") else "team_points"
    opp_pt_key = f"{segment}_opp"  if segment not in ("match", "h1") else "opp_points"
    poss_key   = "poss"

    pts_hist  = [r.get(pt_key, r.get("team_points", 0)) for r in team_rows]
    poss_hist = [r.get(poss_key, 0) for r in team_rows if r.get(poss_key, 0) > 0]
    if not pts_hist or not poss_hist:
        return 1.0
    avg_pts  = statistics.mean(pts_hist)
    avg_poss = statistics.mean(poss_hist)
    team_ppp_hist = avg_pts / avg_poss if avg_poss else 1.0

    # Opponent-allowed PPP (ТЗ §9.1: 0.40 weight)
    opp_allowed_ppp_hist = team_ppp_hist  # fallback if no opp rows
    if opp_rows:
        opp_pts_hist  = [r.get(opp_pt_key, r.get("opp_points", 0)) for r in opp_rows]
        opp_poss_hist = [r.get(poss_key, 0) for r in opp_rows if r.get(poss_key, 0) > 0]
        if opp_pts_hist and opp_poss_hist:
            opp_avg_pts  = statistics.mean(opp_pts_hist)
            opp_avg_poss = statistics.mean(opp_poss_hist)
            opp_allowed_ppp_hist = opp_avg_pts / opp_avg_poss if opp_avg_poss else team_ppp_hist

    # Scenario blend (ТЗ §9.1): if scenario N >= 6, use 3-channel blend
    scenario_ppp = None
    if scenario_rows and len(scenario_rows) >= 6:
        sc_pts  = [r.get(pt_key, r.get("team_points", 0)) for r in scenario_rows]
        sc_poss = [r.get(poss_key, 0) for r in scenario_rows if r.get(poss_key, 0) > 0]
        if sc_pts and sc_poss:
            scenario_ppp = statistics.mean(sc_pts) / statistics.mean(sc_poss)

    if scenario_ppp is not None:
        # 3-channel blend: 0.50 team + 0.30 opp_allowed + 0.20 scenario
        hist_expected_ppp = (0.50 * team_ppp_hist
                             + 0.30 * opp_allowed_ppp_hist
                             + 0.20 * scenario_ppp)
    else:
        # 2-channel blend: 0.60 team + 0.40 opp_allowed (ТЗ §9.1)
        hist_expected_ppp = 0.60 * team_ppp_hist + 0.40 * opp_allowed_ppp_hist

    current_ppp_live = current_pts / current_poss if current_poss > 0 else hist_expected_ppp
    return hist_expected_ppp + lambda_ * (current_ppp_live - hist_expected_ppp)

def expected_score_projection(main: dict, rows_a: list, rows_b: list,
                               scenario_rows_a: list, scenario_rows_b: list,
                               lines_data: dict = None) -> dict:
    hs = safe_int(main.get("hs"))
    aws= safe_int(main.get("as_"))
    q1h= safe_int(main.get("q1h")); q1a= safe_int(main.get("q1a"))
    q2h= safe_int(main.get("q2h")); q2a= safe_int(main.get("q2a"))
    h1_home = q1h + q2h; h1_away = q1a + q2a

    fga_h= safe_int(main.get("hfgam")); fta_h= safe_int(main.get("hftam"))
    orb_h= safe_int(main.get("horbm")); to_h= safe_int(main.get("htovm"))
    poss_h1_home = fga_h + 0.44*fta_h - orb_h + to_h if fga_h else 0

    fga_a= safe_int(main.get("afgam")); fta_a= safe_int(main.get("aftam"))
    orb_a= safe_int(main.get("aorbm")); to_a= safe_int(main.get("atovm"))
    poss_h1_away = fga_a + 0.44*fta_a - orb_a + to_a if fga_a else 0

    # Historical averages for H2
    h2_pts_a  = [r["h2_team"] for r in rows_a]
    h2_pts_b  = [r["h2_team"] for r in rows_b]
    avg_h2_a  = statistics.mean(h2_pts_a) if h2_pts_a else 40
    avg_h2_b  = statistics.mean(h2_pts_b) if h2_pts_b else 40
    q3_pts_a  = [r["q3_team"] for r in rows_a]
    q3_pts_b  = [r["q3_team"] for r in rows_b]
    avg_q3_a  = statistics.mean(q3_pts_a) if q3_pts_a else 20
    avg_q3_b  = statistics.mean(q3_pts_b) if q3_pts_b else 20

    rpp_a = regressed_ppp(rows_a, "h2", h1_home, poss_h1_home, opp_rows=rows_b)
    rpp_b = regressed_ppp(rows_b, "h2", h1_away, poss_h1_away, opp_rows=rows_a)

    # Blend: 60% hist, 40% opponent-allowed
    opp_allowed_h2_a = [r["h2_opp"] for r in rows_b]
    opp_allowed_h2_b = [r["h2_opp"] for r in rows_a]
    avg_opp_h2_a = statistics.mean(opp_allowed_h2_a) if opp_allowed_h2_a else avg_h2_a
    avg_opp_h2_b = statistics.mean(opp_allowed_h2_b) if opp_allowed_h2_b else avg_h2_b

    center_h2_a = round(0.60*avg_h2_a + 0.40*avg_opp_h2_a, 1)
    center_h2_b = round(0.60*avg_h2_b + 0.40*avg_opp_h2_b, 1)
    center_q3_a = round(0.60*avg_q3_a + 0.40*statistics.mean([r["q3_opp"] for r in rows_b] or [avg_q3_a]), 1)
    center_q3_b = round(0.60*avg_q3_b + 0.40*statistics.mean([r["q3_opp"] for r in rows_a] or [avg_q3_b]), 1)

    q3_total_center = round(center_q3_a + center_q3_b, 1)
    match_total_center = round(hs + aws + center_h2_a + center_h2_b, 1)
    margin_center = round((hs + center_h2_a) - (aws + center_h2_b), 1)

    # Std for range
    h2_std_a = statistics.stdev(h2_pts_a) if len(h2_pts_a) > 1 else 8
    h2_std_b = statistics.stdev(h2_pts_b) if len(h2_pts_b) > 1 else 8
    total_std = (h2_std_a**2 + h2_std_b**2)**0.5

    # Q3 std (combined team A + team B)
    q3_std_a = statistics.stdev(q3_pts_a) if len(q3_pts_a) > 1 else 5
    q3_std_b = statistics.stdev(q3_pts_b) if len(q3_pts_b) > 1 else 5
    q3_total_std = (q3_std_a**2 + q3_std_b**2)**0.5

    # model_probability: P(over/under line) via normal distribution.
    # Use real BK quarter_total lines if available, else fall back to QUARTER_TOTAL_LINES constant.
    bk_q_lines = extract_bk_quarter_total_lines(lines_data) if lines_data else []
    q_lines_to_use = bk_q_lines if bk_q_lines else QUARTER_TOTAL_LINES

    q3_model_probs = {}
    for line in q_lines_to_use:
        if abs(line - q3_total_center) <= 12:
            q3_model_probs[f"model_over_{str(line).replace('.', '_')}"] = model_probability(
                line, q3_total_center, q3_total_std, "over"
            )
            q3_model_probs[f"model_under_{str(line).replace('.', '_')}"] = model_probability(
                line, q3_total_center, q3_total_std, "under"
            )

    # Match: use real BK match total lines if available, else fall back to MATCH_TOTAL_LINES constant.
    bk_match_lines = extract_bk_match_total_lines(lines_data) if lines_data else []
    match_lines_to_use = bk_match_lines if bk_match_lines else MATCH_TOTAL_LINES

    match_model_probs = {}
    for line in match_lines_to_use:
        if abs(line - match_total_center) <= 20:
            match_model_probs[f"model_over_{str(line).replace('.', '_')}"] = model_probability(
                line, match_total_center, total_std, "over"
            )
            match_model_probs[f"model_under_{str(line).replace('.', '_')}"] = model_probability(
                line, match_total_center, total_std, "under"
            )

    return {
        "q3": {
            "team_a_center": center_q3_a,
            "team_b_center": center_q3_b,
            "total_center": q3_total_center,
            "total_std": round(q3_total_std, 2),
            "low": round(q3_total_center - 6, 1),
            "high": round(q3_total_center + 6, 1),
            **q3_model_probs,
        },
        "match": {
            "team_a_final_center": round(hs + center_h2_a, 1),
            "team_b_final_center": round(aws + center_h2_b, 1),
            "final_total_center": match_total_center,
            "final_margin_center": margin_center,
            "total_std": round(total_std, 2),
            "low": round(match_total_center - total_std, 1),
            "high": round(match_total_center + total_std, 1),
            **match_model_probs,
        },
    }


# ─────────────────────────────────────────────
# STEP 7 — strong pattern discovery (≥75%)
# ─────────────────────────────────────────────
def discover_strong_patterns(rows_a: list, rows_b: list) -> list:
    patterns = []
    for label, rows, side in [("Team_A", rows_a, "a"), ("Team_B", rows_b, "b")]:
        n = len(rows)
        if n < 10:
            continue
        pf = stat_percentile_zones(rows)
        combos = combo_stat_patterns(rows, pf)
        for c in combos:
            if c["rate"] >= 0.75:
                c["team"] = label
                patterns.append(c)
        single = stat_impact(rows)
        for s in single:
            if s["rate"] >= 0.75:
                s["team"] = label
                patterns.append(s)
    return sorted(patterns, key=lambda x: -x["rate"])


# ─────────────────────────────────────────────
# STEP 8 — HT live profile
# ─────────────────────────────────────────────
def ht_profile(main: dict, rows_a: list, rows_b: list) -> dict:
    def classify(side: str, rows: list) -> str:
        if side == "home":
            fga = safe_int(main.get("hfgam"))
            fgm = safe_int(main.get("hfgmm"))
            pm3 = safe_int(main.get("h3pmm"))
            pa3 = safe_int(main.get("h3pam"))
            fta = safe_int(main.get("hftam"))
            orb = safe_int(main.get("horbm"))
            to_ = safe_int(main.get("htovm"))
            pts = safe_int(main.get("hs"))
        else:
            fga = safe_int(main.get("afgam"))
            fgm = safe_int(main.get("afgmm"))
            pm3 = safe_int(main.get("a3pmm"))
            pa3 = safe_int(main.get("a3pam"))
            fta = safe_int(main.get("aftam"))
            orb = safe_int(main.get("aorbm"))
            to_ = safe_int(main.get("atovm"))
            pts = safe_int(main.get("as_"))

        poss = fga + 0.44*fta - orb + to_ if fga else 0
        efg  = (fgm + 0.5*pm3) / fga if fga else 0
        tpa_share = pa3 / fga if fga else 0

        if not rows:
            return "UNKNOWN"
        pf   = stat_percentile_zones(rows)
        avg_fga  = pf.get("FGA",{}).get("mean", fga)
        avg_poss = pf.get("poss",{}).get("mean", poss)
        avg_efg  = pf.get("efg",{}).get("mean", efg)
        avg_fta  = pf.get("FTA",{}).get("mean", fta)
        p90_efg  = pf.get("efg",{}).get("p90", 0.6)
        p25_efg  = pf.get("efg",{}).get("p25", 0.4)
        p75_fga  = pf.get("FGA",{}).get("p75", 60)
        p25_fga  = pf.get("FGA",{}).get("p25", 50)

        high_fga = fga >= p75_fga; low_fga = fga <= p25_fga
        high_efg = efg >= avg_efg * 1.05; low_efg = efg <= p25_efg
        very_high_efg = efg >= p90_efg
        high_fta = fta >= pf.get("FTA",{}).get("p75", 10)
        ok_to = to_ <= pf.get("TO",{}).get("p75", 15)

        # H1 total for this perspective
        h1_pts = pts  # full 1H
        avg_h1 = pf.get("poss",{}).get("mean", 80)  # proxy via hist h1

        if very_high_efg and not high_fga and not high_fta:
            return "FAKE_HIGH"
        if high_fga and (high_efg or high_fta) and ok_to:
            return "REAL_HIGH"
        if low_efg and (high_fga or high_fta) and ok_to:
            return "FAKE_LOW"
        if low_fga and low_efg and not ok_to:
            return "REAL_LOW"
        return "MIDDLE"

    return {
        "team_a": classify("home", rows_a),
        "team_b": classify("away", rows_b),
    }


# ─────────────────────────────────────────────
# STEP 8b — live override score
# ─────────────────────────────────────────────
def live_override_score(main: dict, rows: list, side: str) -> dict:
    if side == "home":
        fga = safe_int(main.get("hfgam")); fta = safe_int(main.get("hftam"))
        fgm = safe_int(main.get("hfgmm")); pm3 = safe_int(main.get("h3pmm"))
        pa3 = safe_int(main.get("h3pam"))
        orb = safe_int(main.get("horbm")); to_ = safe_int(main.get("htovm"))
        pts = safe_int(main.get("hs"))
    else:
        fga = safe_int(main.get("afgam")); fta = safe_int(main.get("aftam"))
        fgm = safe_int(main.get("afgmm")); pm3 = safe_int(main.get("a3pmm"))
        pa3 = safe_int(main.get("a3pam"))
        orb = safe_int(main.get("aorbm")); to_ = safe_int(main.get("atovm"))
        pts = safe_int(main.get("as_"))

    if not rows:
        return {"score": 0, "label": "OFF"}
    pf = stat_percentile_zones(rows)
    poss_live = fga + 0.44*fta - orb + to_ if fga else 0
    efg_live  = (fgm + 0.5*pm3) / fga if fga else 0
    tpa_share_live = pa3 / fga if fga else 0

    score = 0
    avg_poss = pf.get("poss",{}).get("mean", poss_live)
    avg_fga  = pf.get("FGA",{}).get("mean", fga)
    avg_fta  = pf.get("FTA",{}).get("mean", fta)
    avg_efg  = pf.get("efg",{}).get("mean", efg_live)
    avg_orb  = pf.get("ORB",{}).get("mean", orb)
    avg_tps  = pf.get("three_pa_share",{}).get("mean", tpa_share_live)
    avg_to   = pf.get("TO",{}).get("mean", to_)

    # Scale live to 2H assumption (live = 1H, project same pace)
    if poss_live >= avg_poss * 1.10:  score += 1
    if fga >= avg_fga * 1.10:         score += 1
    if fta >= avg_fta * 1.10:         score += 1
    if efg_live >= avg_efg:           score += 1
    if tpa_share_live >= avg_tps:     score += 1
    if orb >= avg_orb:                score += 1
    if to_ <= avg_to:                 score += 1

    if score <= 3:    label = "OFF"
    elif score <= 5:  label = "WEAK"
    elif score <= 7:  label = "MEDIUM"
    else:             label = "STRONG"

    # history_floor: minimum hit-rate across over gates (ТЗ §11 — hard rule for live override)
    it_gates = []
    for r in rows:
        for q in ["q3_team", "h2_team", "team_points"]:
            v = r.get(q, 0)
            if v > 0:
                it_gates.append(v)
    # Simplified: history_floor = rate of games where team scored > median
    if rows:
        med = statistics.median([r.get("team_points", 0) for r in rows])
        floor_hits = sum(1 for r in rows if r.get("team_points", 0) > med * 0.85)
        history_floor = round(floor_hits / len(rows), 3)
    else:
        history_floor = 0.0

    return {"score": score, "label": label, "history_floor": history_floor}


# ─────────────────────────────────────────────
# STEP 9 — threshold calculations
# ─────────────────────────────────────────────
def threshold_calcs(main: dict, lines_data: dict = None) -> dict:
    """
    ТЗ §4: Convert each real BK line into need/allowed/checkpoint thresholds.
    Uses lines from line_result.json (Match scope) when available,
    falls back to MATCH_TOTAL_LINES constant bracketed near expected range.
    """
    hs   = safe_int(main.get("hs"))
    aws  = safe_int(main.get("as_"))
    total= hs + aws

    q1h= safe_int(main.get("q1h")); q1a= safe_int(main.get("q1a"))
    q2h= safe_int(main.get("q2h")); q2a= safe_int(main.get("q2a"))
    h1_home= q1h+q2h; h1_away= q1a+q2a
    h1_total = h1_home + h1_away

    # ── Guard: finished match — BK lines from file are pre-match/frozen
    # snapshots and not meaningful for threshold calculation.
    # Fall back to synthetic lines bracketed around the final score.
    _match_is_finished = _is_match_finished(main.get("st", ""))
    if _match_is_finished:
        lines_data = None  # force synthetic fallback for all line extractions

    def for_match_line(line: float) -> dict:
        return {
            "line": line,
            "needed_over_2h":    math.floor(line) + 1 - total,
            "allowed_under_2h":  math.floor(line) - total,
            "team_a_it_need_2h_over": math.floor(line) + 1 - hs if line in TEAM_IT_LINES else None,
            "team_b_it_need_2h_over": math.floor(line) + 1 - aws if line in TEAM_IT_LINES else None,
        }

    def for_h1_line(line: float) -> dict:
        return {
            "line": line,
            "needed_over_h1":   math.floor(line) + 1 - h1_total,
            "allowed_under_h1": math.floor(line) - h1_total,
        }

    def for_q_line(line: float) -> dict:
        return {
            "line": line,
            "needed_over_q":   math.floor(line) + 1,
            "allowed_under_q": math.floor(line),
        }

    result = {}

    # Match total thresholds — real BK lines (Match scope)
    bk_match_lines = extract_bk_match_total_lines(lines_data) if lines_data else []
    if not bk_match_lines:
        # Fallback: bracket near expected 2H finish
        bk_match_lines = [l for l in MATCH_TOTAL_LINES if abs(l - total - 40) < 30]
    result["match_total"] = {str(l): for_match_line(l) for l in bk_match_lines}

    # H1 total thresholds — real BK lines (H1 scope)
    bk_h1_lines = extract_bk_half_total_lines(lines_data) if lines_data else []
    if bk_h1_lines:
        result["half_total"] = {str(l): for_h1_line(l) for l in bk_h1_lines}

    # Quarter total thresholds — real BK lines (Q scope)
    bk_q_lines = extract_bk_quarter_total_lines(lines_data) if lines_data else []
    if bk_q_lines:
        result["quarter_total"] = {str(l): for_q_line(l) for l in bk_q_lines}

    # Match handicap thresholds — real BK lines
    bk_hcp_lines = extract_bk_match_handicap_lines(lines_data, scope="Match") if lines_data else []
    if bk_hcp_lines:
        margin = hs - aws
        result["match_handicap"] = {
            str(hcp): {
                "handicap": hcp,
                "required_final_margin": abs(hcp) + 1 if hcp < 0 else -(abs(hcp) + 1),
                "required_remaining_margin": (abs(hcp) + 1 - margin) if hcp < 0 else (-(abs(hcp) + 1) - margin),
            }
            for hcp in bk_hcp_lines
        }

    return result


# ─────────────────────────────────────────────
# STEP 10 — candidate filter
# ─────────────────────────────────────────────
def candidate_filter(history_zones: dict, sample_gate: dict, stat_support: dict,
                     strong_patterns: list, projection: dict,
                     current_score_a: int = 0, current_score_b: int = 0) -> list:
    candidates = []
    it_zones = history_zones.get("team_it_match", {})
    for line_str, data in it_zones.items():
        line_val = float(line_str)
        for side in ["team_a_over", "team_b_over", "team_a_under", "team_b_under"]:
            g = data.get(side, {}).get("final_gate", 0)
            if g == 0:
                continue
            # Filter: skip IT Over lines already beaten by current live score
            if "over" in side:
                live_score = current_score_a if "_a_" in side else current_score_b
                if live_score > line_val:
                    continue  # line already passed — not a valid live market
            # Filter: skip IT Under lines already impossible given current live score
            if "under" in side:
                live_score = current_score_a if "_a_" in side else current_score_b
                if live_score >= line_val:
                    continue  # team already at/over the under ceiling
            blockers = []
            if not sample_gate["recommendation_allowed"]:
                blockers.append("SAMPLE_FAIL")
            if stat_support["status"] == "OFF":
                blockers.append("STAT_OFF")
            if g >= 0.85 and not blockers:
                status = "STRONG_CANDIDATE"
            elif g >= 0.79 and not blockers:
                status = "PLAY_CANDIDATE"
            elif g >= 0.73 and not blockers:
                status = "THIN_PLAY_CANDIDATE"
            elif blockers:
                status = "PASS_BLOCKER"
            else:
                status = "PASS"
            if status not in ("PASS", "PASS_BLOCKER"):
                candidates.append({
                    "market": f"{'Home' if '_a_' in side else 'Away'} IT {'Over' if 'over' in side else 'Under'} {line_str}",
                    "line": float(line_str),
                    "history_gate": g,
                    "blockers": blockers,
                    "pre_gpt_status": status,
                })
    # дедупликация по (market, line)
    seen_keys = set()
    deduped = []
    for c in candidates:
        key = (c["market"], c["line"])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(c)
    candidates = deduped
    return sorted(candidates, key=lambda x: -x["history_gate"])[:15]


# ─────────────────────────────────────────────
# STEP 11 — Quarter Threshold + Anti-Sweep + Allowed Ceiling Scanner
# ─────────────────────────────────────────────

def line_to_threshold(line: float) -> int:
    # Over 22.5 => 23, Over 82.5 => 83
    return int(line) + 1


def quarter_threshold_profile(team_games: list, threshold: int) -> dict:
    # team_games rows must have "q_pf": [q1, q2, q3, q4]
    n = len(team_games)
    if n == 0:
        return {"n": 0, "any_q_hits": 0, "any_q_rate": None,
                "no_q_hits": 0, "no_q_rate": None, "q_hits": {}}
    any_q_hits = sum(max(g["q_pf"]) >= threshold for g in team_games)
    no_q_hits = n - any_q_hits
    q_hits = {
        "Q1": sum(g["q_pf"][0] >= threshold for g in team_games),
        "Q2": sum(g["q_pf"][1] >= threshold for g in team_games),
        "Q3": sum(g["q_pf"][2] >= threshold for g in team_games),
        "Q4": sum(g["q_pf"][3] >= threshold for g in team_games),
    }
    return {
        "n": n,
        "any_q_hits": any_q_hits,
        "any_q_rate": round(any_q_hits / n, 3) if n else None,
        "no_q_hits": no_q_hits,
        "no_q_rate": round(no_q_hits / n, 3) if n else None,
        "q_hits": q_hits,
    }


def opponent_allowed_quarter_profile(opponent_games: list, threshold: int) -> dict:
    # opponent_games rows must have "q_pa": [allowed_q1, allowed_q2, allowed_q3, allowed_q4]
    n = len(opponent_games)
    if n == 0:
        return {"n": 0, "any_allowed_hits": 0, "any_allowed_rate": None,
                "no_allowed_hits": 0, "no_allowed_rate": None, "q_allowed_hits": {}}
    any_allowed_hits = sum(max(g["q_pa"]) >= threshold for g in opponent_games)
    no_allowed_hits = n - any_allowed_hits
    q_allowed_hits = {
        "Q1": sum(g["q_pa"][0] >= threshold for g in opponent_games),
        "Q2": sum(g["q_pa"][1] >= threshold for g in opponent_games),
        "Q3": sum(g["q_pa"][2] >= threshold for g in opponent_games),
        "Q4": sum(g["q_pa"][3] >= threshold for g in opponent_games),
    }
    return {
        "n": n,
        "any_allowed_hits": any_allowed_hits,
        "any_allowed_rate": round(any_allowed_hits / n, 3) if n else None,
        "no_allowed_hits": no_allowed_hits,
        "no_allowed_rate": round(no_allowed_hits / n, 3) if n else None,
        "q_allowed_hits": q_allowed_hits,
    }


def anti_sweep_profile(team_games: list, h2h_games: list = None) -> dict:
    # rows must have "q_pf": [q1,q2,q3,q4], "q_pa": [a1,a2,a3,a4]
    def calc(games):
        n = len(games)
        lost_all_4 = sum(
            all(pf < pa for pf, pa in zip(g["q_pf"], g["q_pa"]))
            for g in games
        )
        return {
            "n": n,
            "lost_all_4": lost_all_4,
            "lost_all_4_rate": round(lost_all_4 / n, 3) if n else None,
            "not_lost_all_4": n - lost_all_4,
            "not_lost_all_4_rate": round((n - lost_all_4) / n, 3) if n else None,
        }

    main_calc = calc(team_games)
    h2h_calc = calc(h2h_games) if h2h_games else None

    signal = "OFF"
    if main_calc["n"] >= 35 and main_calc["lost_all_4_rate"] is not None:
        if main_calc["lost_all_4_rate"] <= 0.10:
            signal = "STRONG_ANTI_SWEEP"
        elif main_calc["lost_all_4_rate"] <= 0.20:
            signal = "MEDIUM_ANTI_SWEEP"

    if (h2h_calc and h2h_calc["n"] > 0
            and h2h_calc["lost_all_4"] == 0
            and signal == "STRONG_ANTI_SWEEP"):
        signal = "STRONG_ANTI_SWEEP_H2H_CONFIRMED"

    return {"main": main_calc, "h2h": h2h_calc, "signal": signal}


def match_it_allowed_ceiling(team_games: list, opponent_games: list, line: float) -> dict:
    threshold = int(line) + 1
    ceiling = int(line)

    n_team = len(team_games)
    n_opp = len(opponent_games)

    own_over = sum(g["pf"] >= threshold for g in team_games)
    own_under = sum(g["pf"] <= ceiling for g in team_games)

    opp_allowed_over = sum(g["pa"] >= threshold for g in opponent_games)
    opp_allowed_under = sum(g["pa"] <= ceiling for g in opponent_games)

    allowed_under_rate = round(opp_allowed_under / n_opp, 3) if n_opp else None
    signal = "OFF"
    if n_opp >= 35 and allowed_under_rate is not None:
        if allowed_under_rate >= 0.95:
            signal = "STRONG_UNDER_ALLOWED_CEILING"
        elif allowed_under_rate >= 0.90:
            signal = "MEDIUM_UNDER_ALLOWED_CEILING"

    return {
        "threshold": threshold,
        "own_over": own_over,
        "own_over_rate": round(own_over / n_team, 3) if n_team else None,
        "own_under": own_under,
        "own_under_rate": round(own_under / n_team, 3) if n_team else None,
        "opponent_allowed_over": opp_allowed_over,
        "opponent_allowed_over_rate": round(opp_allowed_over / n_opp, 3) if n_opp else None,
        "opponent_allowed_under": opp_allowed_under,
        "opponent_allowed_under_rate": allowed_under_rate,
        "allowed_ceiling_signal": signal,
    }


def build_qpf_qpa_rows(rows: list) -> list:
    """Convert hist rows to {q_pf, q_pa, pf, pa} format for threshold/anti-sweep scanners."""
    result = []
    for r in rows:
        result.append({
            "q_pf": [safe_float(r.get("q1_team")), safe_float(r.get("q2_team")),
                     safe_float(r.get("q3_team")), safe_float(r.get("q4_team"))],
            "q_pa": [safe_float(r.get("q1_opp")),  safe_float(r.get("q2_opp")),
                     safe_float(r.get("q3_opp")),  safe_float(r.get("q4_opp"))],
            "pf": safe_float(r.get("team_points")),
            "pa": safe_float(r.get("opp_points")),
        })
    return result

def _build_live_stat_support(main: dict, stat_sp: dict) -> dict:
    """
    Сравниваем показатели текущего матча (H1) с историческими перцентилями.
    Сигнал ON если текущий показатель выше p75 или ниже p25 норм обеих команд.
    """
    def _pct(val, p25, p75):
        if val is None:
            return "OFF"
        if val >= p75:
            return "HIGH"
        if val <= p25:
            return "LOW"
        return "NORM"

    def _verdict(signals: list) -> str:
        vals = [s for s in signals if s != "OFF"]
        if not vals:
            return "OFF"
        if any(s in ("HIGH", "LOW") for s in vals):
            return "ON"
        return "NORM"

    # Текущие H1 показатели из main (уже строки, конвертируем)
    h_fga  = safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))  # суммарный FGA
    h_fta_a = safe_int(main.get("hftam"))
    h_fta_b = safe_int(main.get("aftam"))
    h_fls_a = safe_int(main.get("hflsm"))
    h_fls_b = safe_int(main.get("aflsm"))
    h_orb_a = safe_int(main.get("horbm"))
    h_orb_b = safe_int(main.get("aorbm"))
    h_tov_a = safe_int(main.get("htovm"))
    h_tov_b = safe_int(main.get("atovm"))

    # Исторические перцентили из stat_support (если есть)
    # Используем stat_sp который уже содержит нормы
    # Поскольку stat_zones считается отдельно — используем грубые нормы из stat_sp
    # FGA норма для H1 ~ половина от полного матча
    fga_p25 = 28.0
    fga_p75 = 36.0
    fta_p25 = 6.0
    fta_p75 = 14.0
    fouls_p25 = 8.0
    fouls_p75 = 13.0
    orb_p25 = 3.0
    orb_p75 = 7.0
    tov_p25 = 3.0
    tov_p75 = 7.0

    h1_fga_total = safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))
    fga_sig   = _pct(h1_fga_total, fga_p25 * 2, fga_p75 * 2)

    h_efg_a_raw = main.get("hfgpm", "")
    h_efg_b_raw = main.get("afgpm", "")
    # eFG из процентов — просто смотрим выше/ниже нормы 50%
    def _efg_sig(pct_str_val):
        try:
            v = float(str(pct_str_val).replace("%","")) / 100
            if v >= 0.58: return "HIGH"
            if v <= 0.44: return "LOW"
            return "NORM"
        except:
            return "OFF"

    efg_sig = "HIGH" if _efg_sig(h_efg_a_raw) in ("HIGH","LOW") or _efg_sig(h_efg_b_raw) in ("HIGH","LOW") else "NORM"

    fta_sig   = _pct(h_fta_a + h_fta_b, fta_p25 * 2, fta_p75 * 2)
    fouls_sig = _pct(h_fls_a + h_fls_b, fouls_p25 * 2, fouls_p75 * 2)
    orb_sig   = _pct(h_orb_a + h_orb_b, orb_p25 * 2, orb_p75 * 2)
    tov_sig   = _pct(h_tov_a + h_tov_b, tov_p25 * 2, tov_p75 * 2)
    pace_sig  = fga_sig  # pace proxy = суммарный FGA

    verdict = _verdict([fga_sig, efg_sig, fta_sig, fouls_sig, orb_sig, tov_sig])

    return {
        "fga_poss":        fga_sig,
        "efg_2p_3p":       efg_sig,
        "fta_fouls":       _pct(h_fta_a + h_fta_b + h_fls_a + h_fls_b, 28, 46),
        "orb_to_extraposs": _pct((h_orb_a + h_orb_b) - (h_tov_a + h_tov_b), -4, 4),
        "pace":            pace_sig,
        "verdict":         verdict,
    }


# ─────────────────────────────────────────────
# HELPERS — league quarter duration
# ─────────────────────────────────────────────
def quarter_duration_minutes(tour: str) -> int:
    """
    Return quarter duration in minutes for a given league/tournament string.
    NBA / G-League / Summer League: 12 min. FIBA (default): 10 min.
    """
    if not tour:
        return 10
    t = tour.upper()
    if any(k in t for k in ("NBA", "G LEAGUE", "G-LEAGUE", "GLEAGUE",
                             "SUMMER LEAGUE", "NBL", "BIG3")):
        return 12
    return 10  # FIBA default (Euroleague, Eurocup, national leagues, etc.)


def parse_minutes_played(st: str, tour: str) -> float:
    """
    Parse minutes played from status string.
    'Live (4-а чверть 4\')'       → 3 full quarters * q_dur + 4
    'Halftime' / 'HT'             → 2 * q_dur
    'Перерва (після Q2)'          → 2 * q_dur  (inter-quarter break after Q2)
    'Перерва (після Q1/Q3)'       → 1 * q_dur / 3 * q_dur
    'Finished'                    → 4 * q_dur
    Returns float minutes played (may be fractional from OT, ignored here).
    """
    q = quarter_duration_minutes(tour)
    if not st:
        return float(2 * q)  # default: halftime

    st_up = st.upper()

    if "FINISHED" in st_up:
        return float(4 * q)

    if "HALFTIME" in st_up or st_up.strip() in ("HT", "HALF TIME", "HALF-TIME"):
        return float(2 * q)

    # 0. Inter-quarter break: "Перерва (після Q2)", "Break (after Q3)", "Перерыв после Q1", etc.
    # Must come BEFORE generic quarter patterns to avoid mis-parsing the quarter number
    # as a live-quarter position (which would subtract 1 and return wrong minutes).
    m = re.search(
        r'(?:перерв[аи]|break|pause|перерыв)\s*[^Q\d]*(?:після|after|после|Q)\s*(?:Q\s*)?(\d+)',
        st, re.IGNORECASE
    )
    if m:
        quarter_num = int(m.group(1))
        # After Qn break means exactly n full quarters have been played
        return float(quarter_num * q)

    # 1. Ukrainian/Russian: "4-а чверть 5'", "1-й квартал 3'", "3-я чверть 2'", "4 четверть 5'"
    # Handle various suffixes (-а, -я, -й, -та, -ша, -га, -тя) and optional spaces/dots
    m = re.search(r'(\d+)[-–\s]*[а-яА-Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?\s*(\d+)[\'′]?', st, re.IGNORECASE)
    if m:
        quarter_num = int(m.group(1))
        minute_in_q = int(m.group(2))
        return float((quarter_num - 1) * q + minute_in_q)

    # 2. English: "Q4 5'", "Quarter 2 3'", "4th Quarter 5'", "Q 1 5'"
    m = re.search(r'(?:Q|Quarter|Qtr|Quar)\s*(\d+)\s*(\d+)[\'′]?', st, re.IGNORECASE)
    if m:
        quarter_num = int(m.group(1))
        minute_in_q = int(m.group(2))
        return float((quarter_num - 1) * q + minute_in_q)

    # 3. Quarter only: "4-а чверть", "Q3"
    m = re.search(r'(\d+)[-–\s]*[а-яА-Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?', st, re.IGNORECASE)
    if not m:
        m = re.search(r'(?:Q|Quarter|Qtr|Quar)\s*(\d+)', st, re.IGNORECASE)
    if m:
        quarter_num = int(m.group(1))
        return float((quarter_num - 1) * q)

    # Fallback to halftime if nothing else matches
    return float(2 * q)


def minutes_left(minutes_played: float, tour: str) -> float:
    """Total regulation minutes minus minutes played."""
    q = quarter_duration_minutes(tour)
    total = float(4 * q)
    return max(0.0, total - minutes_played)


# ─────────────────────────────────────────────
# PRE-MATCH STAT PROJECTION (ТЗ pre_match_stat_formula_columns)
# ─────────────────────────────────────────────
def pre_match_stat_projection(rows_a: list, rows_b: list) -> dict:
    """
    Builds pre-match stat forecast for Team A (home) and Team B (away).
    Uses 0.55 own / 0.45 opponent-allowed blend per ТЗ.
    rows_a / rows_b are enriched hist rows from build_hist_row().
    Available fields per row: FGA, 2PA, 2PM, 3PA, 3PM, FTA, FTM, ORB, DRB, TO, fouls
    and corresponding *_opp / *_opp counterparts via opp_points, but NOT
    per-stat opp fields — so we compute allowed from the opponent's own rows.
    """

    def _avg(rows, key):
        vals = [r.get(key, 0) for r in rows if r.get(key) is not None]
        return statistics.mean(vals) if vals else 0.0

    def _sum_ratio(rows, num_key, den_key):
        num = sum(r.get(num_key, 0) for r in rows)
        den = sum(r.get(den_key, 0) for r in rows)
        return num / den if den > 0 else 0.0

    if not rows_a or not rows_b:
        return {"status": "NO_DATA"}

    # ── Team A own averages ──────────────────────────────────────────────
    A_FGA_avg       = _avg(rows_a, "FGA")
    A_2P_Att_avg    = _avg(rows_a, "2PA")
    A_3P_Att_avg    = _avg(rows_a, "3PA")
    A_FT_Att_avg    = _avg(rows_a, "FTA")
    A_ORB_avg       = _avg(rows_a, "ORB")
    A_DRB_avg       = _avg(rows_a, "DRB")
    A_TO_avg        = _avg(rows_a, "TO")

    A_2P_pct        = _sum_ratio(rows_a, "2PM", "2PA")
    A_3P_pct        = _sum_ratio(rows_a, "3PM", "3PA")
    A_FT_pct        = _sum_ratio(rows_a, "FTM", "FTA")

    A_2PA_share     = A_2P_Att_avg / A_FGA_avg if A_FGA_avg > 0 else 0.0
    A_3PA_share     = A_3P_Att_avg / A_FGA_avg if A_FGA_avg > 0 else 0.0
    A_FTr           = A_FT_Att_avg / A_FGA_avg if A_FGA_avg > 0 else 0.0

    # ── Team B own averages ──────────────────────────────────────────────
    B_FGA_avg       = _avg(rows_b, "FGA")
    B_2P_Att_avg    = _avg(rows_b, "2PA")
    B_3P_Att_avg    = _avg(rows_b, "3PA")
    B_FT_Att_avg    = _avg(rows_b, "FTA")
    B_ORB_avg       = _avg(rows_b, "ORB")
    B_DRB_avg       = _avg(rows_b, "DRB")
    B_TO_avg        = _avg(rows_b, "TO")

    B_2P_pct        = _sum_ratio(rows_b, "2PM", "2PA")
    B_3P_pct        = _sum_ratio(rows_b, "3PM", "3PA")
    B_FT_pct        = _sum_ratio(rows_b, "FTM", "FTA")

    B_2PA_share     = B_2P_Att_avg / B_FGA_avg if B_FGA_avg > 0 else 0.0
    B_3PA_share     = B_3P_Att_avg / B_FGA_avg if B_FGA_avg > 0 else 0.0
    B_FTr           = B_FT_Att_avg / B_FGA_avg if B_FGA_avg > 0 else 0.0

    # ── Allowed averages (what each team gives up to opponents) ──────────
    # rows_a["opp_points"] exists, but per-stat allowed fields are read from
    # the *opponent* team's perspective rows — i.e. rows_b shows what B's
    # opponents scored / attempted, which equals what B "allowed".
    # In build_hist_row, for team_b row: FGA = team's own FGA, but
    # we need the FGA that B allowed = opponent's FGA in those rows.
    # Unfortunately build_hist_row doesn't store opp-stat breakdown.
    # We approximate allowed_2PA/3PA/FTA from the *other* team's own averages
    # as a league-average proxy, then blend 0.55/0.45.
    # For allowed FGA we use opp_points as proxy for pace: rows_b "opp" side.
    # Best available: use h1_fga fields stored in build_hist_row for rows_b
    # (h1_fga is from the perspective team's Q1+Q2, not full match).
    # Full-match allowed stats are NOT stored in build_hist_row.
    # → We use the opponent team's own averages as "allowed" proxy (common
    #   approach when allowed breakdowns are unavailable per-row).

    B_FGA_allowed_avg       = B_FGA_avg       # what B allows ≈ B's own pace (proxy)
    B_2P_Att_allowed_avg    = B_2P_Att_avg
    B_3P_Att_allowed_avg    = B_3P_Att_avg
    B_FT_Att_allowed_avg    = B_FT_Att_avg
    B_2PA_allowed_share     = B_2PA_share
    B_3PA_allowed_share     = B_3PA_share
    B_FTr_allowed           = B_FTr
    B_2P_pct_allowed        = B_2P_pct
    B_3P_pct_allowed        = B_3P_pct
    B_FT_pct_allowed        = B_FT_pct
    B_ORB_allowed_avg       = B_ORB_avg
    B_TO_allowed_avg        = B_TO_avg

    A_FGA_allowed_avg       = A_FGA_avg
    A_2P_Att_allowed_avg    = A_2P_Att_avg
    A_3P_Att_allowed_avg    = A_3P_Att_avg
    A_FT_Att_allowed_avg    = A_FT_Att_avg
    A_2PA_allowed_share     = A_2PA_share
    A_3PA_allowed_share     = A_3PA_share
    A_FTr_allowed           = A_FTr
    A_2P_pct_allowed        = A_2P_pct
    A_3P_pct_allowed        = A_3P_pct
    A_FT_pct_allowed        = A_FT_pct
    A_ORB_allowed_avg       = A_ORB_avg
    A_TO_allowed_avg        = A_TO_avg

    # ── Expected FGA (§4) ────────────────────────────────────────────────
    Pre_FGA_A = 0.55 * A_FGA_avg + 0.45 * B_FGA_allowed_avg
    Pre_FGA_B = 0.55 * B_FGA_avg + 0.45 * A_FGA_allowed_avg

    # ── Expected shares (§6) ─────────────────────────────────────────────
    Pre_2PA_share_A = 0.55 * A_2PA_share + 0.45 * B_2PA_allowed_share
    Pre_3PA_share_A = 0.55 * A_3PA_share + 0.45 * B_3PA_allowed_share
    Pre_2PA_share_B = 0.55 * B_2PA_share + 0.45 * A_2PA_allowed_share
    Pre_3PA_share_B = 0.55 * B_3PA_share + 0.45 * A_3PA_allowed_share

    # ── Expected FTr (§7) ────────────────────────────────────────────────
    Pre_FTr_A = 0.55 * A_FTr + 0.45 * B_FTr_allowed
    Pre_FTr_B = 0.55 * B_FTr + 0.45 * A_FTr_allowed

    # ── Expected attempts (§8) ───────────────────────────────────────────
    Pre_2PA_A = Pre_FGA_A * Pre_2PA_share_A
    Pre_3PA_A = Pre_FGA_A * Pre_3PA_share_A
    Pre_FTA_A = Pre_FGA_A * Pre_FTr_A

    Pre_2PA_B = Pre_FGA_B * Pre_2PA_share_B
    Pre_3PA_B = Pre_FGA_B * Pre_3PA_share_B
    Pre_FTA_B = Pre_FGA_B * Pre_FTr_B

    # ── Expected shooting % (§9) ─────────────────────────────────────────
    Pre_2P_pct_A = 0.55 * A_2P_pct + 0.45 * B_2P_pct_allowed
    Pre_3P_pct_A = 0.55 * A_3P_pct + 0.45 * B_3P_pct_allowed
    Pre_FT_pct_A = 0.55 * A_FT_pct + 0.45 * B_FT_pct_allowed

    Pre_2P_pct_B = 0.55 * B_2P_pct + 0.45 * A_2P_pct_allowed
    Pre_3P_pct_B = 0.55 * B_3P_pct + 0.45 * A_3P_pct_allowed
    Pre_FT_pct_B = 0.55 * B_FT_pct + 0.45 * A_FT_pct_allowed

    # ── PreStat points (§10) ─────────────────────────────────────────────
    PreStat_A = (2 * Pre_2PA_A * Pre_2P_pct_A
                 + 3 * Pre_3PA_A * Pre_3P_pct_A
                 + Pre_FTA_A * Pre_FT_pct_A)
    PreStat_B = (2 * Pre_2PA_B * Pre_2P_pct_B
                 + 3 * Pre_3PA_B * Pre_3P_pct_B
                 + Pre_FTA_B * Pre_FT_pct_B)

    # ── ORB bonus / TO drag (§11) ─────────────────────────────────────────
    # Baseline = mean of two teams
    orb_baseline = (A_ORB_avg + B_ORB_avg) / 2
    to_baseline  = (A_TO_avg  + B_TO_avg)  / 2

    Pre_ORB_bonus_A = max(0.0, A_ORB_avg - orb_baseline) * 0.7
    Pre_TO_drag_A   = max(0.0, A_TO_avg  - to_baseline)  * 0.8
    Pre_ORB_bonus_B = max(0.0, B_ORB_avg - orb_baseline) * 0.7
    Pre_TO_drag_B   = max(0.0, B_TO_avg  - to_baseline)  * 0.8

    # ── Final pre-match projection (§12) ─────────────────────────────────
    PreFinal_A = PreStat_A + Pre_ORB_bonus_A - Pre_TO_drag_A
    PreFinal_B = PreStat_B + Pre_ORB_bonus_B - Pre_TO_drag_B
    PreFinal_Total  = PreFinal_A + PreFinal_B
    PreFinal_Margin = PreFinal_A - PreFinal_B

    def r2(v): return round(v, 2)
    def r4(v): return round(v, 4)

    return {
        "team_a": {
            "Pre_FGA":        r2(Pre_FGA_A),
            "Pre_2PA":        r2(Pre_2PA_A),
            "Pre_3PA":        r2(Pre_3PA_A),
            "Pre_FTA":        r2(Pre_FTA_A),
            "Pre_2P_pct":     r4(Pre_2P_pct_A),
            "Pre_3P_pct":     r4(Pre_3P_pct_A),
            "Pre_FT_pct":     r4(Pre_FT_pct_A),
            "Pre_eFG":        r4((Pre_2PA_A * Pre_2P_pct_A + 1.5 * Pre_3PA_A * Pre_3P_pct_A) / Pre_FGA_A if Pre_FGA_A > 0 else 0),
            "Pre_ORB_bonus":  r2(Pre_ORB_bonus_A),
            "Pre_TO_drag":    r2(Pre_TO_drag_A),
            "PreStat":        r2(PreStat_A),
            "PreFinal":       r2(PreFinal_A),
            # raw averages for live calibration
            "_Pre_FGA_avg":   r2(A_FGA_avg),
            "_Pre_FTA_avg":   r2(A_FT_Att_avg),
            "_Pre_2PA_share": r4(A_2PA_share),
            "_Pre_3PA_share": r4(A_3PA_share),
            "_Pre_FTr":       r4(A_FTr),
            "_Pre_ORB_avg":   r2(A_ORB_avg),
            "_Pre_TO_avg":    r2(A_TO_avg),
            "_Pre_eFG":       r4(statistics.mean([r.get("efg", 0) for r in rows_a]) if rows_a else 0),
        },
        "team_b": {
            "Pre_FGA":        r2(Pre_FGA_B),
            "Pre_2PA":        r2(Pre_2PA_B),
            "Pre_3PA":        r2(Pre_3PA_B),
            "Pre_FTA":        r2(Pre_FTA_B),
            "Pre_2P_pct":     r4(Pre_2P_pct_B),
            "Pre_3P_pct":     r4(Pre_3P_pct_B),
            "Pre_FT_pct":     r4(Pre_FT_pct_B),
            "Pre_eFG":        r4((Pre_2PA_B * Pre_2P_pct_B + 1.5 * Pre_3PA_B * Pre_3P_pct_B) / Pre_FGA_B if Pre_FGA_B > 0 else 0),
            "Pre_ORB_bonus":  r2(Pre_ORB_bonus_B),
            "Pre_TO_drag":    r2(Pre_TO_drag_B),
            "PreStat":        r2(PreStat_B),
            "PreFinal":       r2(PreFinal_B),
            # raw averages for live calibration
            "_Pre_FGA_avg":   r2(B_FGA_avg),
            "_Pre_FTA_avg":   r2(B_FT_Att_avg),
            "_Pre_2PA_share": r4(B_2PA_share),
            "_Pre_3PA_share": r4(B_3PA_share),
            "_Pre_FTr":       r4(B_FTr),
            "_Pre_ORB_avg":   r2(B_ORB_avg),
            "_Pre_TO_avg":    r2(B_TO_avg),
            "_Pre_eFG":       r4(statistics.mean([r.get("efg", 0) for r in rows_b]) if rows_b else 0),
        },
        "PreFinal_Total":  r2(PreFinal_Total),
        "PreFinal_Margin": r2(PreFinal_Margin),
    }


# ─────────────────────────────────────────────
# LIVE CALIBRATED PROJECTION (ТЗ live_projection_formula_columns)
# ─────────────────────────────────────────────
def live_calibrated_projection(main: dict, pre_stat: dict) -> dict:
    """
    Builds live-calibrated score projection per ТЗ §2–§6.
    pre_stat is the output of pre_match_stat_projection().
    """
    if not pre_stat or pre_stat.get("status") == "NO_DATA":
        return {"status": "NO_DATA"}

    tour = main.get("tour", "")
    st   = main.get("st", "")

    # ── Guard: match already finished — live projection is meaningless ────
    if _is_match_finished(st):
        return {
            "status": "FINISHED",
            "note": (
                "Match is finished (stage=FT). live_calibrated values are NOT valid "
                "as a live projection — min_left=0, BK lines may be frozen pre-match "
                "snapshots. Use pre_match_stat + history for post-game reference only."
            ),
            "LiveCalibrated_Total": None,
            "LiveRaw_Total":        None,
            "home": {"LiveCalibrated": None, "LiveRaw": None},
            "away": {"LiveCalibrated": None, "LiveRaw": None},
            "min_played": None,
            "min_left":   0.0,
            "q_duration_min": quarter_duration_minutes(tour),
        }
    min_played = parse_minutes_played(st, tour)
    min_left   = minutes_left(min_played, tour)
    q_dur      = quarter_duration_minutes(tour)

    # ── LiveTrust from minute (ТЗ §6) ────────────────────────────────────
    def _live_trust(min_played: float) -> float:
        if min_played <= 5:   return 0.20
        if min_played <= 10:  return 0.30
        if min_played <= q_dur:      return 0.40  # end of Q1
        if min_played <= 2 * q_dur:  return 0.57  # halftime
        if min_played <= 3 * q_dur:  return 0.77  # end of Q3
        return 0.87  # Q4

    base_trust = _live_trust(min_played)

    def _build_side(side: str, pre: dict) -> dict:
        """side: 'home' or 'away'"""
        if side == "home":
            score    = safe_float(main.get("hs"))
            tpa      = safe_int(main.get("h2pam"))   # 2P_Att
            tpm      = safe_int(main.get("h2pmm"))   # 2P_Made
            pa3      = safe_int(main.get("h3pam"))   # 3P_Att
            pm3      = safe_int(main.get("h3pmm"))   # 3P_Made
            fta      = safe_int(main.get("hftam"))
            ftm      = safe_int(main.get("hftmm"))
            orb      = safe_int(main.get("horbm"))
            to_      = safe_int(main.get("htovm"))
        else:
            score    = safe_float(main.get("as_"))
            tpa      = safe_int(main.get("a2pam"))
            tpm      = safe_int(main.get("a2pmm"))
            pa3      = safe_int(main.get("a3pam"))
            pm3      = safe_int(main.get("a3pmm"))
            fta      = safe_int(main.get("aftam"))
            ftm      = safe_int(main.get("aftmm"))
            orb      = safe_int(main.get("aorbm"))
            to_      = safe_int(main.get("atovm"))

        # Pre-match reference values
        Pre_FGA      = pre["_Pre_FGA_avg"]
        Pre_FTA      = pre["_Pre_FTA_avg"]
        Pre_2PA_sh   = pre["_Pre_2PA_share"]
        Pre_3PA_sh   = pre["_Pre_3PA_share"]
        Pre_FTr      = pre["_Pre_FTr"]
        Pre_2P_pct   = pre["Pre_2P_pct"]
        Pre_3P_pct   = pre["Pre_3P_pct"]
        Pre_FT_pct   = pre["Pre_FT_pct"]
        Pre_ORB      = pre["_Pre_ORB_avg"]
        Pre_TO       = pre["_Pre_TO_avg"]
        Pre_eFG      = pre["_Pre_eFG"]
        PreStat      = pre["PreFinal"]

        # ── §2 Live metrics ──────────────────────────────────────────────
        fga_live = tpa + pa3                            # §2.1
        fgm_live = tpm + pm3

        live_2PA_share = tpa / fga_live if fga_live > 0 else Pre_2PA_sh   # §2.2
        live_3PA_share = pa3 / fga_live if fga_live > 0 else Pre_3PA_sh   # §2.3
        live_FTr       = fta / fga_live if fga_live > 0 else Pre_FTr       # §2.4

        live_2P_pct = tpm / tpa if tpa > 0 else Pre_2P_pct                # §2.5
        live_3P_pct = pm3 / pa3 if pa3 > 0 else Pre_3P_pct                # §2.6
        live_FT_pct = ftm / fta if fta > 0 else Pre_FT_pct                # §2.7

        poss_live = fga_live + 0.44 * fta - orb + to_ if fga_live > 0 else 0  # §2.8
        efg_live  = (fgm_live + 0.5 * pm3) / fga_live if fga_live > 0 else 0  # §2.9
        extra_poss_live = orb - to_                                            # §2.12

        # ── §4 Tempo adjustments ─────────────────────────────────────────
        if min_played > 0:
            FGA_per_min  = fga_live / min_played   # §4.1
            FTA_per_min  = fta / min_played         # §4.2
        else:
            FGA_per_min  = Pre_FGA / (4 * q_dur)
            FTA_per_min  = Pre_FTA / (4 * q_dur)

        Pre_FGA_per_min = Pre_FGA / (4 * q_dur)    # §4.3
        Pre_FTA_per_min = Pre_FTA / (4 * q_dur)    # §4.4

        # w_live / w_pre based on trust
        w_live = base_trust
        w_pre  = 1.0 - base_trust

        Adj_FGA_per_min = w_live * FGA_per_min + w_pre * Pre_FGA_per_min  # §4.5
        Adj_FTA_per_min = w_live * FTA_per_min + w_pre * Pre_FTA_per_min  # §4.6

        Rem_FGA = Adj_FGA_per_min * min_left                               # §4.7

        Adj_2PA_share = w_live * live_2PA_share + w_pre * Pre_2PA_sh      # §4.8
        Adj_3PA_share = w_live * live_3PA_share + w_pre * Pre_3PA_sh      # §4.9

        Rem_2PA = Rem_FGA * Adj_2PA_share                                  # §4.10
        Rem_3PA = Rem_FGA * Adj_3PA_share                                  # §4.11

        # §5.8 Foul_FT_factor — scale Rem_FTA
        if Pre_FTA_per_min > 0:
            Foul_FT_factor = FTA_per_min / Pre_FTA_per_min
        else:
            Foul_FT_factor = 1.0
        Rem_FTA = Adj_FTA_per_min * min_left                               # §4.12
        if Foul_FT_factor > 1.0:
            Rem_FTA = Rem_FTA * min(Foul_FT_factor, 1.35)

        Adj_2P_pct = w_live * live_2P_pct + w_pre * Pre_2P_pct            # §4.13
        Adj_3P_pct = w_live * live_3P_pct + w_pre * Pre_3P_pct            # §4.14
        Adj_FT_pct = w_live * live_FT_pct + w_pre * Pre_FT_pct            # §4.15

        # ── §5 Remaining points ──────────────────────────────────────────
        Pts_2P = 2 * Rem_2PA * Adj_2P_pct                                  # §5.1
        Pts_3P = 3 * Rem_3PA * Adj_3P_pct                                  # §5.2
        Pts_FT = Rem_FTA * Adj_FT_pct                                      # §5.3

        Expected_ORB_now = (Pre_ORB / (4 * q_dur)) * min_played            # §5.4
        ORB_bonus = max(0.0, orb - Expected_ORB_now) * 0.7                 # §5.5

        Expected_TO_now = (Pre_TO / (4 * q_dur)) * min_played              # §5.6
        TO_drag = max(0.0, to_ - Expected_TO_now) * 0.8                    # §5.7

        LiveRaw = score + Pts_2P + Pts_3P + Pts_FT + ORB_bonus - TO_drag  # §5.9

        # ── §6 Calibration ───────────────────────────────────────────────
        overheat = (efg_live - Pre_eFG) * 100  # in percentage points      # §6.1
        trust = base_trust
        if overheat >= 25:
            trust *= 0.50
        elif overheat >= 20:
            trust *= 0.60
        elif overheat >= 15:
            trust *= 0.70

        LiveCalibrated = PreStat + trust * (LiveRaw - PreStat)              # §6.2

        def r2(v): return round(float(v), 2)
        def r4(v): return round(float(v), 4)

        return {
            # Live raw metrics (§7 output)
            "LiveRaw":          r2(LiveRaw),
            "LiveCalibrated":   r2(LiveCalibrated),
            "eFG_live":         r4(efg_live),
            "Poss_live":        r2(poss_live),
            "FTr_live":         r4(live_FTr),
            "ExtraPoss_live":   r2(float(extra_poss_live)),
            "LiveTrust":        round(trust, 3),
            "Delta_LiveRaw_vs_PreStat":        r2(LiveRaw - PreStat),
            "Delta_LiveCalibrated_vs_PreStat": r2(LiveCalibrated - PreStat),
            # Detail
            "min_played":   round(min_played, 1),
            "min_left":     round(min_left, 1),
            "Rem_FGA":      r2(Rem_FGA),
            "Rem_2PA":      r2(Rem_2PA),
            "Rem_3PA":      r2(Rem_3PA),
            "Rem_FTA":      r2(Rem_FTA),
            "Adj_2P_pct":   r4(Adj_2P_pct),
            "Adj_3P_pct":   r4(Adj_3P_pct),
            "Adj_FT_pct":   r4(Adj_FT_pct),
            "Pts_2P":       r2(Pts_2P),
            "Pts_3P":       r2(Pts_3P),
            "Pts_FT":       r2(Pts_FT),
            "ORB_bonus":    r2(ORB_bonus),
            "TO_drag":      r2(TO_drag),
            "overheat_pts": round(overheat, 2),
            "Foul_FT_factor": round(Foul_FT_factor, 3),
        }

    side_a = _build_side("home", pre_stat["team_a"])
    side_b = _build_side("away", pre_stat["team_b"])

    LiveCalibrated_Total = side_a["LiveCalibrated"] + side_b["LiveCalibrated"]

    return {
        "home": side_a,
        "away": side_b,
        "LiveCalibrated_Total": round(LiveCalibrated_Total, 2),
        "LiveRaw_Total":        round(side_a["LiveRaw"] + side_b["LiveRaw"], 2),
        "status":               main.get("st", ""),
        "min_played":           round(min_played, 1),
        "min_left":             round(min_left, 1),
        "q_duration_min":       q_dur,
    }


# ─────────────────────────────────────────────
# STAGE DETECTION HELPER
# ─────────────────────────────────────────────
def _derive_stage(st: str) -> str:
    """
    Derive match stage label from the live status string.
    Examples: "Halftime" → "HT", "Live (4-а чверть 4')" → "Q4_live",
              "Перерва (після Q2)" → "HT", "Перерва (після Q1)" → "Q1_break",
              "Перерва (після Q3)" → "Q3_break",
              "Finished" → "FT", "" → "unknown"
    """
    import re as _re
    if not st:
        return "unknown"
    su = st.upper()
    if "FINISHED" in su:
        return "FT"
    if "HALFTIME" in su or su.strip() in ("HT", "HALF TIME", "HALF-TIME"):
        return "HT"
    # Inter-quarter break: "Перерва (після Q2)" etc.
    m = _re.search(
        r'(?:перерв[аи]|break|pause|перерыв)\s*[^Q\d]*(?:після|after|після|Q)\s*(?:Q\s*)?(\d+)',
        st, _re.IGNORECASE
    )
    if m:
        qn = int(m.group(1))
        return "HT" if qn == 2 else f"Q{qn}_break"
    # Live quarter detection: handles Ukrainian "4-а чверть", "3-й квартал", Russian "4 четверть" and English "Q4"
    m = _re.search(r'(\d+)[-–\s]*[а-яА-Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?|[Qq](\d+)', st, _re.IGNORECASE)
    if m:
        q = int(m.group(1) or m.group(2))
        return f"Q{q}_live"
    if "LIVE" in su:
        return "live"
    return st  # return raw if unrecognised


def _is_match_finished(st: str) -> bool:
    """
    Return True if the match status indicates the game is over (FT / Finished).
    Used to gate live_calibrated and BK line usage — a finished match has
    min_left=0 and lines frozen at their last update, so neither live-calibrated
    projections nor BK lines from line_result.json should be used as live signals.
    """
    if not st:
        return False
    su = st.upper().strip()
    return "FINISHED" in su or su == "FT"


# ─────────────────────────────────────────────
# MAIN PROCESSOR — single match
# ─────────────────────────────────────────────
def process_match(parsed: dict, lines_data: dict = None) -> dict:
    main   = parsed["main_match"]
    if not main:
        return {"error": "No MAIN MATCH found"}

    match_id = main.get("mid", "")
    home_team= main.get("ht", "Home")
    away_team= main.get("at", "Away")
    hs  = safe_int(main.get("hs"))
    aws = safe_int(main.get("as_"))
    _q1h_raw = main.get("q1h"); _q1a_raw = main.get("q1a")
    _q2h_raw = main.get("q2h"); _q2a_raw = main.get("q2a")
    q1h = safe_int(_q1h_raw); q1a = safe_int(_q1a_raw)
    q2h = safe_int(_q2h_raw); q2a = safe_int(_q2a_raw)
    _q1_known = _q1h_raw not in ("", None) and _q1a_raw not in ("", None)
    _q2_known = _q2h_raw not in ("", None) and _q2a_raw not in ("", None)
    h1_home = q1h+q2h if _q1_known and _q2_known else None
    h1_away = q1a+q2a if _q1_known and _q2_known else None
    margin_team_a = hs - aws

    # Determine perspective for each team based on their role
    rows_a_raw = parsed["team_a_hist"]
    rows_b_raw = parsed["team_b_hist"]
    h2h_raw    = parsed["h2h_hist"]

    # For team_a (home) history, perspective is whichever position they played
    rows_a = []
    for rec in rows_a_raw:
        if rec.get("mid") == match_id:
            continue
        if rec.get("ht") == home_team:
            row = build_hist_row(rec, "home")
        elif rec.get("at") == home_team:
            row = build_hist_row(rec, "away")
        else:
            row = build_hist_row(rec, "home")
        if row:
            rows_a.append(row)

    rows_b = []
    for rec in rows_b_raw:
        if rec.get("mid") == match_id:
            continue
        if rec.get("ht") == away_team:
            row = build_hist_row(rec, "home")
        elif rec.get("at") == away_team:
            row = build_hist_row(rec, "away")
        else:
            row = build_hist_row(rec, "home")
        if row:
            rows_b.append(row)

    h2h_rows = []
    for rec in h2h_raw:
        row = build_hist_row(rec, "home")
        if row:
            h2h_rows.append(row)

    # ── Idle period: days between last team match and current live match ──
    from datetime import datetime, timezone as _tz
    _live_ts = int(datetime.now(_tz.utc).timestamp())

    def _last_match_ts(raw_rows: list, skip_mid: str) -> Optional[int]:
        """Return Unix ts of the most recent finished match in raw_rows (excluding current)."""
        best_ts = None
        for rec in raw_rows:
            if rec.get("mid") == skip_mid:
                continue
            ts = _parse_dt_to_unix(rec.get("dt"))
            if ts is not None:
                if best_ts is None or ts > best_ts:
                    best_ts = ts
        return best_ts

    _last_ts_a = _last_match_ts(rows_a_raw, match_id)
    _last_ts_b = _last_match_ts(rows_b_raw, match_id)
    import sys as _sys
    _sys.stderr.write(f"[idle_debug] rows_a_raw={len(rows_a_raw)} last_ts_a={_last_ts_a} rows_b_raw={len(rows_b_raw)} last_ts_b={_last_ts_b}\n")
    if rows_a_raw:
        _sys.stderr.write(f"[idle_debug] a[0] mid={rows_a_raw[0].get('mid')!r} dt={rows_a_raw[0].get('dt')!r}\n")

    def _idle_days(live_ts, last_ts) -> Optional[float]:
        if live_ts is None or last_ts is None:
            return None
        diff = live_ts - last_ts
        return round(diff / 86400, 2)

    _idle_a = _idle_days(_live_ts, _last_ts_a)
    _idle_b = _idle_days(_live_ts, _last_ts_b)

    _idle_period = {
        "team_a_idle_days": _idle_a,
        "team_b_idle_days": _idle_b,
    }

    # Gates
    sample  = check_sample_gate(rows_a, rows_b, h2h_rows)
    basis_n = (
    sample["team_a_valid_games"] +
    sample["team_b_valid_games"] +
    sample["h2h_n"]
)

    sample_strength = sample_strength_label(basis_n)
    stat_sp = check_stat_support(main, rows_a, rows_b)

    # History zones
    hist_match_total = history_match_total(rows_a, rows_b, h2h_rows)
    hist_q3_total    = history_quarter_total(rows_a, rows_b, "q3")
    hist_q1_total    = history_quarter_total(rows_a, rows_b, "q1")
    hist_q2_total    = history_quarter_total(rows_a, rows_b, "q2")
    hist_q4_total    = history_quarter_total(rows_a, rows_b, "q4")
    hist_it_match    = history_team_it_match(rows_a, rows_b)
    hist_it_q3       = history_team_it_quarter(rows_a, rows_b, "q3")
    hist_spread_a    = history_spread(rows_a, MATCH_SPREAD_LINES)
    hist_spread_b    = history_spread(rows_b, MATCH_SPREAD_LINES)
    hist_spread_q3_a = history_spread(rows_a, QUARTER_SPREAD_LINES, "q3_margin")
    hist_allowed     = history_allowed(rows_a, rows_b)
    hist_atleast1q_a = history_at_least_one_quarter(rows_a)
    hist_atleast1q_b = history_at_least_one_quarter(rows_b)

    # ── 1H total history по BK-линиям (динамический расчёт) ─────────────────
    bk_h1_lines_for_hist = extract_bk_half_total_lines(lines_data) if lines_data else []
    hist_half_total = history_half_total(rows_a, rows_b, h2h_rows, bk_h1_lines_for_hist)

    # ── ТЗ §5A: H1 total zones (full threshold list) ─────────────────────────
    hist_h1_total_zones   = history_h1_total_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §5B: Team H1 points zones (scored + allowed) ─────────────────────
    hist_h1_team_pts      = history_h1_team_points_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §6A: H2 total zones ───────────────────────────────────────────────
    hist_h2_total_zones   = history_h2_total_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §6B: Team H2 points zones (scored + allowed) ─────────────────────
    hist_h2_team_pts      = history_h2_team_points_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §7A: Q3 total zones ───────────────────────────────────────────────
    hist_q3_total_zones   = history_q3_total_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §7B: Team Q3 points zones ─────────────────────────────────────────
    hist_q3_team_pts      = history_q3_team_points_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §8A: After 3Q total zones ─────────────────────────────────────────
    hist_after3q_total_zones  = history_after3q_total_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §8B: Team after 3Q points zones ───────────────────────────────────
    hist_after3q_team_pts     = history_after3q_team_points_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §9A: Q4 total zones ───────────────────────────────────────────────
    hist_q4_total_zones   = history_q4_total_zones(rows_a, rows_b, h2h_rows)
    # ── ТЗ §9B: Team Q4 points zones ─────────────────────────────────────────
    hist_q4_team_pts      = history_q4_team_points_zones(rows_a, rows_b, h2h_rows)

    # ── ТЗ §10: conditional history zones ────────────────────────────────
    cond_history_zones = compute_conditional_history_zones(rows_a, rows_b, h2h_rows)

    # ── ТЗ §11: zone_summary — collect entries ≥78% across all markets ───
    def _collect_zone_summary(block, min_zone_pct: float) -> list:
        """Return threshold entries where pooled_rate >= min_zone_pct."""
        out = []
        if isinstance(block, dict):
            thresholds = block.get("thresholds", [])
        elif isinstance(block, list):
            thresholds = block
        else:
            return out
        for e in thresholds:
            if (e.get("pooled_rate") or 0) >= min_zone_pct:
                out.append(e)
        return out

    def _collect_team_zone_summary(team_block: dict, min_zone_pct: float) -> list:
        out = []
        if not isinstance(team_block, dict):
            return out
        for sub in ("team_a_scored", "team_a_allowed", "team_b_scored", "team_b_allowed"):
            out.extend(_collect_zone_summary(team_block.get(sub, {}), min_zone_pct))
        return out

    def _zone_summary_for(total_block, team_block, pct) -> list:
        return _collect_zone_summary(total_block, pct) + _collect_team_zone_summary(team_block, pct)

    zone_summary = {}
    for prefix, t_blk, tp_blk in [
        ("h1",       hist_h1_total_zones,     hist_h1_team_pts),
        ("h2",       hist_h2_total_zones,     hist_h2_team_pts),
        ("q3",       hist_q3_total_zones,     hist_q3_team_pts),
        ("after_3q", hist_after3q_total_zones, hist_after3q_team_pts),
        ("q4",       hist_q4_total_zones,     hist_q4_team_pts),
    ]:
        for pct_label, pct_val in [
            ("100", 1.0), ("95", 0.95), ("90", 0.90), ("85", 0.85),
            ("80", 0.80), ("78", 0.78),
        ]:
            key = f"{prefix}_{pct_label}_zones"
            zone_summary[key] = _zone_summary_for(t_blk, tp_blk, pct_val)

    history_zones = {
        "match_total": hist_match_total,
        "q1_total": hist_q1_total,
        "q2_total": hist_q2_total,
        "q3_total": hist_q3_total,
        "q4_total": hist_q4_total,
        "team_it_match": hist_it_match,
        "team_it_q3": hist_it_q3,
        "spread_match_team_a": hist_spread_a,
        "spread_match_team_b": hist_spread_b,
        "spread_q3_team_a": hist_spread_q3_a,
        "allowed_points": hist_allowed,
        "at_least_one_quarter_a": hist_atleast1q_a,
        "at_least_one_quarter_b": hist_atleast1q_b,
        "half_total": hist_half_total,
        # ── ТЗ §5-9: Basketball History Zones Module ──────────────────────
        "h1_total_zones":        hist_h1_total_zones,
        "h1_team_points_zones":  hist_h1_team_pts,
        "h2_total_zones":        hist_h2_total_zones,
        "h2_team_points_zones":  hist_h2_team_pts,
        "q3_total_zones":        hist_q3_total_zones,
        "q3_team_points_zones":  hist_q3_team_pts,
        "after3q_total_zones":   hist_after3q_total_zones,
        "after3q_team_points_zones": hist_after3q_team_pts,
        "q4_total_zones":        hist_q4_total_zones,
        "q4_team_points_zones":  hist_q4_team_pts,
        # ── ТЗ §10-11 ─────────────────────────────────────────────────────
        "conditional_history_zones": cond_history_zones,
        "zone_summary": zone_summary,
    }

    # Conditional scanner
    current_margin_bucket = ht_margin_bucket(margin_team_a)
    current_q_state       = quarter_state(q1h > q1a, q2h > q2a)
    hist_h1_totals_a = [r["h1_total"] for r in rows_a]
    current_ht_bucket = ht_total_bucket(
        (h1_home + h1_away) if (h1_home is not None and h1_away is not None) else None,
        hist_h1_totals_a
    )
    q3_hist_a = [r["q3_total"] for r in rows_a]
    h2_hist_a = [r["h2_total"] for r in rows_a]

    # Use real BK Q-total line as scenario threshold if available (mid line); else hist average
    bk_q_lines = extract_bk_quarter_total_lines(lines_data) if lines_data else []
    if bk_q_lines:
        mid_idx = len(bk_q_lines) // 2
        needed_q3 = math.floor(bk_q_lines[mid_idx]) + 1
    else:
        needed_q3 = statistics.mean(q3_hist_a) if q3_hist_a else 40

    # Use real BK match total line to derive needed 2H (line - current_total); else hist average
    bk_match_lines = extract_bk_match_total_lines(lines_data) if lines_data else []
    current_total = hs + aws
    if bk_match_lines:
        mid_match_line = bk_match_lines[len(bk_match_lines) // 2]
        needed_2h = math.floor(mid_match_line) + 1 - current_total
    else:
        needed_2h = statistics.mean(h2_hist_a) if h2_hist_a else 80

    scenario_a = conditional_scanner(rows_a, current_margin_bucket, current_q_state,
                                      current_ht_bucket, needed_q3, needed_2h,
                                      needed_q3 / 2, needed_2h / 2)
    scenario_b = conditional_scanner(rows_b, current_margin_bucket, current_q_state,
                                      current_ht_bucket, needed_q3, needed_2h,
                                      needed_q3 / 2, needed_2h / 2)

    # Stat zones
    pf_a = stat_percentile_zones(rows_a)
    pf_b = stat_percentile_zones(rows_b)
    pf_pooled = stat_percentile_zones(rows_a + rows_b)
    stat_zones = {"team_a": pf_a, "team_b": pf_b, "pooled": pf_pooled}

    # Projection — pass lines_data so model_probability uses real BK lines
    projection = expected_score_projection(main, rows_a, rows_b, [], [], lines_data=lines_data)

    # HT profile & override
    profile = ht_profile(main, rows_a, rows_b)
    override_a = live_override_score(main, rows_a, "home")
    override_b = live_override_score(main, rows_b, "away")

    # Strong patterns
    strong_patterns = discover_strong_patterns(rows_a, rows_b)

    # Thresholds — pass lines_data so real BK lines are used
    thresholds = threshold_calcs(main, lines_data=lines_data)

    # Pre-match stat projection (ТЗ pre_match_stat_formula_columns)
    pre_match_stat = pre_match_stat_projection(rows_a, rows_b)

    # Live calibrated projection (ТЗ live_projection_formula_columns)
    live_calibrated = live_calibrated_projection(main, pre_match_stat)

    # ── Lines staleness warning ──────────────────────────────────────────
    _st = main.get("st", "")
    _match_finished = _is_match_finished(_st)
    _stage_label = _derive_stage(_st)
    lines_stale_warning = None
    if _match_finished:
        lines_stale_warning = (
            f"stage={_stage_label}: match is finished. "
            "BK lines from line_result.json are frozen snapshots — "
            "they reflect pre-match or early-live odds, NOT the current market. "
            "live_calibrated is disabled (status=FINISHED). "
            "Use pre_match_stat + history_zones for evaluation."
        )
    elif lines_data:
        # Check if ALL BK lines look like pre-match (opening == current for every entry)
        mt = lines_data.get("match_total", [])
        if mt:
            stale_count = sum(
                1 for e in mt
                if e.get("overOdd") is not None
                and e.get("overOpen") is not None
                and abs(float(e.get("overOdd", 0)) - float(e.get("overOpen", 0))) < 0.001
            )
            if stale_count == len(mt):
                lines_stale_warning = (
                    "All BK match_total lines have overOdd == overOpen — "
                    "lines appear to be pre-match snapshots with no live update. "
                    "live_calibrated may be unreliable; cross-check with pre_match_stat."
                )

    # Candidates
    candidates = candidate_filter(history_zones, sample, stat_sp, strong_patterns, projection,
                                   current_score_a=hs, current_score_b=aws)

    # Quarter threshold & anti-sweep.
    # threshold здесь = очки ОДНОЙ команды за квартал (team Q IT).
    # BK Q-total (42.5) — это сумма двух команд, не подходит.
    # Используем медиану QUARTER_IT_LINES (~22.5) или первого BK handicap Q1 если есть.
    # По ТЗ: Over 22.5 => threshold=23 (одна команда за квартал).
    bk_q1_hcp_lines = extract_bk_match_handicap_lines(lines_data, scope="Q1") if lines_data else []
    if bk_q1_hcp_lines:
        # берём абсолютное значение середины диапазона Q1 фор как прокси team IT за квартал
        _q_it_line = abs(bk_q1_hcp_lines[len(bk_q1_hcp_lines) // 2])
    elif candidates:
        _q_it_line = candidates[0]["line"] / 4  # грубый прокси: match IT / 4
    else:
        _q_it_line = 22.5  # типичный Q IT для евробаскета
    _threshold = line_to_threshold(_q_it_line)
    qpf_rows_a   = build_qpf_qpa_rows(rows_a)
    qpf_rows_b   = build_qpf_qpa_rows(rows_b)
    qpf_rows_h2h = build_qpf_qpa_rows(h2h_rows)
    _qt_a   = quarter_threshold_profile(qpf_rows_a, _threshold)
    _qt_b   = quarter_threshold_profile(qpf_rows_b, _threshold)
    _opp_a  = opponent_allowed_quarter_profile(qpf_rows_b, _threshold)
    _opp_b  = opponent_allowed_quarter_profile(qpf_rows_a, _threshold)
    _anti_a = anti_sweep_profile(qpf_rows_a, qpf_rows_h2h)
    _anti_b = anti_sweep_profile(qpf_rows_b, qpf_rows_h2h)

    # ── Quarter pattern analysis (sweep, at-least-one-quarter, etc.) ────
    quarter_patterns_a = quarter_pattern_analysis(rows_a, team_label="team_a")
    quarter_patterns_b = quarter_pattern_analysis(rows_b, team_label="team_b")
    quarter_patterns_h2h = quarter_pattern_analysis(h2h_rows, team_label="h2h") if h2h_rows else {"n": 0, "status": "NO_DATA"}

    # ── Similar scenario Q3/Q4 stat profile ──────────────────────────────
    _pooled_n = len(rows_a) + len(rows_b)
    similar_scenario = similar_scenario_q3_q4_stat_profile(
        main=main,
        rows_a=rows_a,
        rows_b=rows_b,
        stage=_derive_stage(main.get("st", "")),
        pooled_n=_pooled_n,
    )

    # ── Live protection calc ──────────────────────────────────────────────
    live_protection = live_protection_calc(
        main=main,
        pre_match_stat=pre_match_stat,
        live_calibrated=live_calibrated,
        history_zones=history_zones,
        lines_data=lines_data,
    )

    # ── Pre-match protection calc ─────────────────────────────────────────
    pre_match_protection = pre_match_protection_calc(
        main=main,
        pre_match_stat=pre_match_stat,
        history_zones=history_zones,
        lines_data=lines_data,
        rows_a=rows_a,
        rows_b=rows_b,
    )

    # ── Minute calculations for meta ────────────────────────────────────
    _tour_for_min = main.get("tour", "")
    _st_for_min   = main.get("st", "")
    _q_dur        = quarter_duration_minutes(_tour_for_min)
    _is_nba       = (_q_dur == 12)

    # Parse current minute from status string (supports OT via quarter_num > 4)
    def _parse_minutes_played_ot(st: str, q: int) -> float:
        """Like parse_minutes_played but supports OT quarters (num > 4)."""
        if not st:
            return float(2 * q)
        st_up = st.upper()
        if "FINISHED" in st_up:
            return float(4 * q)
        if "HALFTIME" in st_up or st_up.strip() in ("HT", "HALF TIME", "HALF-TIME"):
            return float(2 * q)
        # Inter-quarter break: "Перерва (після Q2)", "Break after Q3", etc.
        m = re.search(
            r'(?:перерв[аи]|break|pause|перерыв)\s*[^Q\d]*(?:після|after|після|Q)\s*(?:Q\s*)?(\d+)',
            st, re.IGNORECASE
        )
        if m:
            return float(int(m.group(1)) * q)
        # Ukrainian/Russian with OT support
        m = re.search(r'(\d+)[-–\s]*[а-яА-Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?\s*(\d+)[\'′]?', st, re.IGNORECASE)
        if m:
            qn = int(m.group(1)); qm = int(m.group(2))
            return float((qn - 1) * q + qm)
        m = re.search(r'(?:Q|Quarter|Qtr|Quar)\s*(\d+)\s*(\d+)[\'′]?', st, re.IGNORECASE)
        if m:
            qn = int(m.group(1)); qm = int(m.group(2))
            return float((qn - 1) * q + qm)
        m = re.search(r'(\d+)[-–\s]*[а-яА-Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?', st, re.IGNORECASE)
        if not m:
            m = re.search(r'(?:Q|Quarter|Qtr|Quar)\s*(\d+)', st, re.IGNORECASE)
        if m:
            qn = int(m.group(1))
            return float((qn - 1) * q)
        return float(2 * q)

    def _parse_quarter_num(st: str) -> int:
        """Extract current quarter number from status string (supports OT).
        For inter-quarter breaks ('Перерва після Q2') returns the completed quarter
        number so _n_completed = quarter_num (all those quarters are fully done)."""
        if not st:
            return 2
        st_up = st.upper()
        if "FINISHED" in st_up:
            return 4
        if "HALFTIME" in st_up or st_up.strip() in ("HT", "HALF TIME", "HALF-TIME"):
            return 2
        # Inter-quarter break: return completed quarter number directly
        m = re.search(
            r'(?:перерв[аи]|break|pause|перерыв)\s*[^Q\d]*(?:після|after|після|Q)\s*(?:Q\s*)?(\d+)',
            st, re.IGNORECASE
        )
        if m:
            return int(m.group(1))  # e.g. "після Q2" → 2
        m = re.search(r'(\d+)[-–\s]*[а-яА­Я]*\s*(?:чверть|квартал|четверть|чв|кв)\.?', st, re.IGNORECASE)
        if not m:
            m = re.search(r'(?:Q|Quarter|Qtr|Quar)\s*(\d+)', st, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 2

    _total_min_played = _parse_minutes_played_ot(_st_for_min, _q_dur)
    _q_num            = _parse_quarter_num(_st_for_min)
    # n = number of completed quarters (quarters already fully played)
    # For inter-quarter breaks _parse_quarter_num returns the completed quarter,
    # so _n_completed == _q_num (all those quarters are done, none in progress).
    # For OT: q_num > 4 means we are in an extra period.
    _st_for_min_up = _st_for_min.upper()
    _is_break = (
        re.search(r'(?:перерв[аи]|break|pause|перерыв)\s*[^Q\d]*(?:після|after|після|Q)\s*(?:Q\s*)?\d+',
                  _st_for_min, re.IGNORECASE) is not None
        or "HALFTIME" in _st_for_min_up
        or _st_for_min_up.strip() in ("HT", "HALF TIME", "HALF-TIME")
    )
    _n_completed      = _q_num if _is_break else max(0, _q_num - 1)
    # Total regulation time accounts for OT: n*q_dur for n quarters already completed
    # plus current quarter in progress; but total_min_left uses n_completed * q_dur as base
    _regulation_total = float(4 * _q_dur)  # standard regulation
    _total_min_played_f = round(_total_min_played, 1)
    _total_min_left_f   = round(max(0.0, _regulation_total - _total_min_played), 1)

    # Quarter minute = minutes played within the current quarter
    _quarter_min_played_f = round(_total_min_played - _n_completed * _q_dur, 1) if _q_num >= 1 else 0.0
    _quarter_min_left_f   = round(max(0.0, float(_q_dur) - _quarter_min_played_f), 1)

    # Build final JSON package
    return {
        "sample_strength": sample_strength,
        # Исходный блок meta сохранен полностью
        "meta": {
            "match": f"{home_team} vs {away_team}",
            "match_id": match_id,
            "tournament": main.get("tour"),
            "date": main.get("dt"),
            "stage": _derive_stage(main.get("st", "")),
            "score": f"{hs}-{aws}",
            "q1": f"{q1h}-{q1a}" if _q1_known else None,
            "q2": f"{q2h}-{q2a}" if _q2_known else None,
            "h1_total": (h1_home + h1_away) if (h1_home is not None and h1_away is not None) else None,
            "current_total": hs + aws,
            "margin_team_a": margin_team_a,
            "url": main.get("url", ""),
            # ── Minute tracking ──────────────────────────────────────────
            "quarter_min_played": _quarter_min_played_f,
            "quarter_min_left":   _quarter_min_left_f,
            "total_min_played":   _total_min_played_f,
            "total_min_left":     _total_min_left_f,
            # ── Idle period (days since last match per team) ──────────────
            "idle_period": _idle_period,
            # ── Season boundary ──────────────────────────────────────────
            "last_season_end_dt": main.get("last_season_end_dt"),
            "days_since_previous_season_end": (
                (datetime.now() - datetime.strptime(main["last_season_end_dt"], "%d.%m.%Y")).days
                if main.get("last_season_end_dt") else None
            ),
            "current_season_start_dt": main.get("current_season_start_dt"),
            "days_since_season_start": (
                (datetime.now() - datetime.strptime(main["current_season_start_dt"], "%d.%m.%Y")).days
                if main.get("current_season_start_dt") else None
            ),
        },
        "idle_period": _idle_period,
        "sample_gate": sample,
        "sample_strength": sample_strength,
        # live_stat_support (вычисляется из данных текущего матча vs исторических норм)
        "live_stat_support": _build_live_stat_support(main, stat_sp),
        
        # Оригинальный stat_support сохранен полностью, чтобы ничего не сломать в логике
        "stat_support": {**stat_sp,
            "team_a_1h": {
                "FGA": main.get("hfgam"), "FGM": main.get("hfgmm"),
                "FG%": main.get("hfgpm"), "3PA": main.get("h3pam"),
                "3PM": main.get("h3pmm"), "3P%": main.get("h3ppm"),
                "FTA": main.get("hftam"), "FTM": main.get("hftmm"),
                "FT%": main.get("hftpm"), "ORB": main.get("horbm"),
                "DRB": main.get("hdrbm"), "AST": main.get("hastm"),
                "TO":  main.get("htovm"), "STL": main.get("hstlm"),
                "BLK": main.get("hblkm"), "FOULS": main.get("hflsm"),
            },
            "team_b_1h": {
                "FGA": main.get("afgam"), "FGM": main.get("afgmm"),
                "FG%": main.get("afgpm"), "3PA": main.get("a3pam"),
                "3PM": main.get("a3pmm"), "3P%": main.get("a3ppm"),
                "FTA": main.get("aftam"), "FTM": main.get("aftmm"),
                "FT%": main.get("aftpm"), "ORB": main.get("aorbm"),
                "DRB": main.get("adrbm"), "AST": main.get("aastm"),
                "TO":  main.get("atovm"), "STL": main.get("astlm"),
                "BLK": main.get("ablkm"), "FOULS": main.get("aflsm"),
            },
            "ht_profile": profile,
            "live_override_score_a": override_a,
            "live_override_score_b": override_b,
        },
        
        "team_any_quarter_threshold": {
            "team_a": _qt_a,
            "team_b": _qt_b,
            "threshold": _threshold,
        },
        "opponent_allowed_any_quarter_threshold": {
            "team_a_opp_allowed": _opp_a,
            "team_b_opp_allowed": _opp_b,
            "threshold": _threshold,
        },
        "anti_sweep": {
            "team_a": _anti_a,
            "team_b": _anti_b,
        },
        "history_zones": history_zones,
        "stat_zones": stat_zones,
        "projection": projection,
        "scenario": {
            "current_margin_bucket": current_margin_bucket,
            "current_quarter_state": current_q_state,
            "current_ht_total_bucket": current_ht_bucket,
            "team_a": scenario_a,
            "team_b": scenario_b,
        },
        "strong_patterns": strong_patterns,
        "thresholds": thresholds,
        "pre_match_stat": pre_match_stat,
        "live_calibrated": live_calibrated,
        "candidates": candidates,
        "quarter_patterns": {
            "team_a": quarter_patterns_a,
            "team_b": quarter_patterns_b,
            "h2h": quarter_patterns_h2h,
        },
        "similar_scenario_q3_q4_stat_profile": similar_scenario,
        "live_protection": live_protection,
        "pre_match_protection": pre_match_protection,
        "pre_calculated_patterns": compute_pre_calculated_patterns(
            rows_a=rows_a,
            rows_b=rows_b,
            h2h_rows=h2h_rows,
            main=main,
            live_calibrated=live_calibrated,
            lines_data=lines_data,
        ),
        "blockers": (
            (["SAMPLE_FAIL"] if not sample["recommendation_allowed"] else []) +
            (["STAT_OFF"]    if stat_sp["status"] == "OFF" else []) +
            (["LINES_STALE"] if lines_stale_warning else [])
        ),
        "lines_stale_warning": lines_stale_warning,
        "final_signal": (
            "FINISHED_NO_LIVE"
            if _match_finished
            else (
                "SCENARIO_STRONG_BUT_NEEDS_LIVE_STAT"
                if stat_sp["status"] == "OFF" and sample["recommendation_allowed"]
                else ("SAMPLE_FAIL" if not sample["recommendation_allowed"] else "OK")
            )
        ),
        # ── ТЗ ДОРОБКА §2–6 ───────────────────────────────────────────
        "derived_live_stats": compute_derived_live_stats(main),
        "derived_pre_match_projection": compute_derived_pre_match_projection(
            rows_a, rows_b, pre_match_stat),
        "derived_live_projection": compute_derived_live_projection(
            main,
            compute_derived_pre_match_projection(rows_a, rows_b, pre_match_stat),
            live_calibrated,
        ),
        "derived_live_edge": compute_derived_live_edge(
            compute_derived_pre_match_projection(rows_a, rows_b, pre_match_stat),
            compute_derived_live_projection(
                main,
                compute_derived_pre_match_projection(rows_a, rows_b, pre_match_stat),
                live_calibrated,
            ),
        ),
        "line_profiles": compute_line_profiles(
            rows_a=rows_a,
            rows_b=rows_b,
            h2h_rows=h2h_rows,
            main=main,
            lines_data=lines_data,
            pre_proj=compute_derived_pre_match_projection(rows_a, rows_b, pre_match_stat),
            live_proj=compute_derived_live_projection(
                main,
                compute_derived_pre_match_projection(rows_a, rows_b, pre_match_stat),
                live_calibrated,
            ),
            user_live_lines=None,  # передається з-зовні якщо є
        ),
    }

# ═══════════════════════════════════════════════════════════════════════
# ADVANCED QUARTER PATTERNS — "скільки раз команда програвала 4-0"
#   та "скільки раз не кидала хоча б X в одній з четвертей"
# ═══════════════════════════════════════════════════════════════════════

def quarter_pattern_analysis(rows: list, team_label: str = "team") -> dict:
    """
    Розширений аналіз по четвертях для однієї команди:
    - Скільки разів програвала ВСІ 4 чверті (4-0 по чвертях)
    - Скільки разів хоча б в одній чверті набирала X+
    - Скільки разів НЕ набирала хоча б X в жодній чверті
    - Sweep (програла 4-0) та reverse sweep (виграла 4-0)
    - Паттерни типу "Q1 win + Q2 loss → що потім"
    """
    n = len(rows)
    if n == 0:
        return {"n": 0, "status": "NO_DATA"}

    def q_wins(r):
        return [
            r["q1_team"] > r["q1_opp"],
            r["q2_team"] > r["q2_opp"],
            r["q3_team"] > r["q3_opp"],
            r["q4_team"] > r["q4_opp"],
        ]

    # ── Sweep / reverse sweep ─────────────────────────────────────────
    lost_all_4   = sum(1 for r in rows if all(not w for w in q_wins(r)))
    won_all_4    = sum(1 for r in rows if all(w for w in q_wins(r)))
    lost_3_plus  = sum(1 for r in rows if sum(not w for w in q_wins(r)) >= 3)
    won_3_plus   = sum(1 for r in rows if sum(w for w in q_wins(r)) >= 3)

    # ── Хоча б X в одній чверті vs жодного X ─────────────────────────
    at_least_one_q = {}
    never_reached_q = {}
    thresholds = [15, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 30, 32]
    for t in thresholds:
        hit_at_least_once = sum(1 for r in rows if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) >= t)
        never_hit = n - hit_at_least_once
        at_least_one_q[str(t)] = {
            "hits": hit_at_least_once, "rate": rate(hit_at_least_once, n),
            "smoothed": round((hit_at_least_once + 1) / (n + 2), 3),
            "fraction": pct_str(hit_at_least_once, n),
        }
        never_reached_q[str(t)] = {
            "hits": never_hit, "rate": rate(never_hit, n),
            "smoothed": round((never_hit + 1) / (n + 2), 3),
            "fraction": pct_str(never_hit, n),
        }

    # ── Патерн: Q3 або Q4 хоча б X (без умов по Q1/Q2) ──────────────
    at_least_one_2h_q = {}
    for t in thresholds:
        hits = sum(1 for r in rows if r["q3_team"] >= t or r["q4_team"] >= t)
        at_least_one_2h_q[str(t)] = {"hits": hits, "rate": rate(hits, n), "fraction": pct_str(hits, n)}

    # ── Паттерн: якщо Q1+Q2 не досягли X — чи досягли Q3/Q4 ─────────
    recovery_after_ht = {}
    for t in [18, 19, 20, 21, 22, 23, 25]:
        ht_low = [r for r in rows if r["q1_team"] < t and r["q2_team"] < t]
        if ht_low:
            recovered = sum(1 for r in ht_low if r["q3_team"] >= t or r["q4_team"] >= t)
            recovery_after_ht[str(t)] = {
                "sample_ht_low": len(ht_low),
                "recovered": recovered,
                "rate": rate(recovered, len(ht_low)),
                "fraction": pct_str(recovered, len(ht_low)),
            }

    # ── Per-quarter stats ─────────────────────────────────────────────
    per_q_avg = {}
    for qk, ok in [("q1_team","q1_opp"), ("q2_team","q2_opp"), ("q3_team","q3_opp"), ("q4_team","q4_opp")]:
        label = qk.split("_")[0].upper()
        vals = [r[qk] for r in rows]
        ovals = [r[ok] for r in rows]
        per_q_avg[label] = {
            "avg_own":      round(statistics.mean(vals), 2) if vals else None,
            "avg_allowed":  round(statistics.mean(ovals), 2) if ovals else None,
            "win_rate":     rate(sum(1 for r in rows if r[qk] > r[ok]), n),
            "win_fraction": pct_str(sum(1 for r in rows if r[qk] > r[ok]), n),
        }

    # ── Consecutive quarter patterns ─────────────────────────────────
    # Програв Q3, виграв Q4 (comeback в 2H)
    q3loss_q4win = sum(1 for r in rows if r["q3_team"] < r["q3_opp"] and r["q4_team"] > r["q4_opp"])
    # Виграв Q3, програв Q4 (розтратив перевагу)
    q3win_q4loss = sum(1 for r in rows if r["q3_team"] > r["q3_opp"] and r["q4_team"] < r["q4_opp"])

    return {
        "n": n,
        "sweep_patterns": {
            "lost_all_4_quarters": {
                "hits": lost_all_4, "rate": rate(lost_all_4, n),
                "fraction": pct_str(lost_all_4, n),
                "note": "Програла ВСІ 4 чверті (4-0 по чвертях)",
            },
            "won_all_4_quarters": {
                "hits": won_all_4, "rate": rate(won_all_4, n),
                "fraction": pct_str(won_all_4, n),
                "note": "Виграла ВСІ 4 чверті",
            },
            "lost_3_or_more_quarters": {
                "hits": lost_3_plus, "rate": rate(lost_3_plus, n),
                "fraction": pct_str(lost_3_plus, n),
            },
            "won_3_or_more_quarters": {
                "hits": won_3_plus, "rate": rate(won_3_plus, n),
                "fraction": pct_str(won_3_plus, n),
            },
        },
        "at_least_one_quarter_X_plus": at_least_one_q,
        "never_reached_X_in_any_quarter": never_reached_q,
        "at_least_one_2h_quarter_X_plus": at_least_one_2h_q,
        "recovery_after_low_ht": recovery_after_ht,
        "per_quarter_profile": per_q_avg,
        "q3_q4_transitions": {
            "q3_loss_then_q4_win": {
                "hits": q3loss_q4win, "rate": rate(q3loss_q4win, n),
                "fraction": pct_str(q3loss_q4win, n),
            },
            "q3_win_then_q4_loss": {
                "hits": q3win_q4loss, "rate": rate(q3win_q4loss, n),
                "fraction": pct_str(q3win_q4loss, n),
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# SIMILAR SCENARIO Q3/Q4 STAT PROFILE  (ТЗ similar_scenario_q3_q4_stat_profile)
# ═══════════════════════════════════════════════════════════════════════

def _margin_bucket_label(margin: int) -> str:
    a = abs(margin)
    if a == 0:    return "tie"
    if a <= 4:    return "1-4"
    if a <= 9:    return "5-9"
    if a <= 14:   return "10-14"
    if a <= 19:   return "15-19"
    return "20+"

def _total_bucket_label(total: float, hist_totals: list) -> str:
    if not hist_totals:
        return "normal"
    p25 = percentile(hist_totals, 25)
    p75 = percentile(hist_totals, 75)
    p90 = percentile(hist_totals, 90)
    if total >= p90: return "very_high"
    if total >= p75: return "high"
    if total >= p25: return "normal"
    return "low"

def _quarter_win_state_full(row: dict) -> str:
    """'2-0' / '2-1' / '1-2' / '0-2' / '3-0' / '0-3' etc. based on q1..q3 margins."""
    wins = sum([
        row["q1_margin"] > 0,
        row["q2_margin"] > 0,
        row.get("q3_margin", 0) > 0,
    ])
    losses = 3 - wins
    return f"{wins}-{losses}"

def _lead_trail_state(margin: int) -> str:
    if margin >= 10:  return "blowout"
    if margin >= 1:   return "team_leading"
    if margin == 0:   return "close_game"
    if margin >= -9:  return "team_trailing"
    return "blowout_trail"

def _tempo_label_from_row(row: dict) -> str:
    """Estimate tempo label from a hist row using poss/efg/fta fields."""
    poss = row.get("poss", 0)
    efg  = row.get("efg", 0)
    ftr  = row.get("ftr", 0)
    if poss == 0:
        return "UNKNOWN"
    if poss >= 80 and efg >= 0.55:  return "REAL_HIGH"
    if poss >= 80 and efg < 0.50:   return "FAKE_HIGH"
    if poss <= 65 and efg >= 0.55:  return "FAKE_LOW"
    if poss <= 65 and efg < 0.48:   return "REAL_LOW"
    if ftr >= 0.35:                  return "FAKE_VOLUME"
    return "NORMAL"

def _smoothed_rate(hits: int, n: int, k: int = 10) -> float:
    if n == 0: return 0.0
    return round((hits + 1) / (n + 2), 3)

def _credibility(n: int, k: int = 10) -> float:
    return round(n / (n + k), 3)

def _pattern_score(hits: int, n: int, base_history: float, k: int = 10) -> float:
    cred = _credibility(n, k)
    smooth = _smoothed_rate(hits, n)
    return round(cred * smooth + (1 - cred) * base_history, 3)

def _sample_status(n: int) -> str:
    if n < 3:   return "informational_only"
    if n <= 5:  return "shrinkage_only"
    if n <= 9:  return "usable"
    return "strong_candidate"

def _build_segment_profile(rows: list, q_key_team: str, q_key_opp: str,
                            base_history_pts: float, pooled_n: int) -> dict:
    """
    Build own-scoring + allowed profile for one quarter segment (Q3 or Q4).
    q_key_team: 'q3_team', q_key_opp: 'q3_opp'
    """
    n = len(rows)
    if n == 0:
        return {"n": 0, "status": "NO_DATA"}

    pts_own    = [r[q_key_team] for r in rows]
    pts_allow  = [r[q_key_opp]  for r in rows]
    fga_vals   = [r.get("FGA", 0) for r in rows]
    fgm_vals   = [r.get("FGA", 0) - r.get("FGA", 0) * (1 - r.get("efg", 0.5)) for r in rows]  # approx
    p2a_vals   = [r.get("2PA", 0) for r in rows]
    p3a_vals   = [r.get("3PA", 0) for r in rows]
    fta_vals   = [r.get("FTA", 0) for r in rows]
    orb_vals   = [r.get("ORB", 0) for r in rows]
    to_vals    = [r.get("TO", 0)  for r in rows]
    poss_vals  = [r.get("poss", 0) for r in rows]
    efg_vals   = [r.get("efg", 0)  for r in rows]
    fouls_vals = [r.get("fouls", 0) for r in rows]

    def avg(lst): return round(statistics.mean(lst), 2) if lst else None
    def med(lst): return round(statistics.median(lst), 2) if lst else None
    def p25v(lst): return round(percentile(lst, 25), 2) if lst else None
    def p75v(lst): return round(percentile(lst, 75), 2) if lst else None

    # Hit rates: team points X+ in this quarter
    hit_rates_own = {}
    for t in [15, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 30]:
        h = sum(1 for v in pts_own if v >= t)
        hit_rates_own[str(t)] = {
            "hits": h, "rate": rate(h, n),
            "smoothed": _smoothed_rate(h, n),
            "fraction": pct_str(h, n),
            "credibility": _credibility(n),
            "pattern_score": _pattern_score(h, n, base_history_pts),
            "included_in_p_final": "YES" if n >= 10 and rate(h, n) >= 0.70 else
                                   ("SHRINKAGE" if 3 <= n <= 9 else "OFF"),
        }

    # Hit rates: opponent allowed X+ in this quarter
    hit_rates_allowed = {}
    for t in [15, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 30]:
        h = sum(1 for v in pts_allow if v >= t)
        hit_rates_allowed[str(t)] = {
            "hits": h, "rate": rate(h, n),
            "smoothed": _smoothed_rate(h, n),
            "fraction": pct_str(h, n),
        }

    # Quarter total (combined)
    q_totals = [pts_own[i] + pts_allow[i] for i in range(n)]
    hit_rates_qtotal = {}
    for t in [36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56]:
        h = sum(1 for v in q_totals if v >= t)
        hit_rates_qtotal[str(t)] = {
            "hits": h, "rate": rate(h, n),
            "smoothed": _smoothed_rate(h, n),
            "fraction": pct_str(h, n),
        }

    # ExtraPoss per row
    extra_poss_vals = [r.get("extra_poss", r.get("ORB", 0) - r.get("TO", 0)) for r in rows]

    # OffRtg per row (100 * pts / poss)
    off_rtg_vals = [100 * pts_own[i] / poss_vals[i] if poss_vals[i] > 0 else 0 for i in range(n)]

    coverage = round(n / pooled_n, 3) if pooled_n > 0 else None

    # tempo distribution
    tempo_counts = {}
    for r in rows:
        tl = _tempo_label_from_row(r)
        tempo_counts[tl] = tempo_counts.get(tl, 0) + 1

    conflict_check = {
        "own_scoring_gate": (
            "aligned" if n >= 6 and avg(pts_own) and avg(pts_own) >= 20 else
            "weak" if n >= 3 else "OFF"
        ),
        "opponent_allowed_gate": (
            "aligned" if n >= 6 and avg(pts_allow) and avg(pts_allow) >= 20 else
            "partial" if n >= 3 else "OFF"
        ),
        "live_stat_alignment": "requires_live_data",
        "verdict_impact": (
            "support_strong" if n >= 10 else
            "support_weak" if n >= 3 else "OFF"
        ),
    }

    return {
        "n": n,
        "sample_status": _sample_status(n),
        "coverage": coverage,
        # Own scoring profile
        "own_scoring": {
            "avg_points":   avg(pts_own),
            "median_points": med(pts_own),
            "p25_points":   p25v(pts_own),
            "p75_points":   p75v(pts_own),
            "min_points":   min(pts_own) if pts_own else None,
            "max_points":   max(pts_own) if pts_own else None,
            "avg_poss":     avg(poss_vals),
            "avg_fga":      avg(fga_vals),
            "avg_2pa":      avg(p2a_vals),
            "avg_3pa":      avg(p3a_vals),
            "avg_fta":      avg(fta_vals),
            "avg_orb":      avg(orb_vals),
            "avg_to":       avg(to_vals),
            "avg_fouls":    avg(fouls_vals),
            "avg_efg":      round(statistics.mean([e for e in efg_vals if e > 0]) * 100, 1) if any(efg_vals) else None,
            "avg_extra_poss": avg(extra_poss_vals),
            "avg_offrtg":   round(statistics.mean([v for v in off_rtg_vals if v > 0]), 1) if any(off_rtg_vals) else None,
        },
        # Opponent allowed profile
        "opponent_allowed": {
            "avg_points_allowed": avg(pts_allow),
            "median_points_allowed": med(pts_allow),
            "p25_allowed":  p25v(pts_allow),
            "p75_allowed":  p75v(pts_allow),
        },
        "tempo_distribution": tempo_counts,
        # Hit rates
        "hit_rates_own_pts": hit_rates_own,
        "hit_rates_allowed_pts": hit_rates_allowed,
        "hit_rates_quarter_total": hit_rates_qtotal,
        "conflict_check": conflict_check,
    }


def similar_scenario_q3_q4_stat_profile(
    main: dict,
    rows_a: list,
    rows_b: list,
    stage: str = None,
    pooled_n: int = None,
) -> dict:
    """
    ТЗ similar_scenario_q3_q4_stat_profile:
    Знаходить схожі сценарії в історії та розраховує Q3/Q4 профіль.
    """
    if not rows_a and not rows_b:
        return {"status": "NO_DATA"}

    # ── Визначаємо поточний стан матчу ────────────────────────────────
    hs  = safe_int(main.get("hs"))
    aws = safe_int(main.get("as_"))
    q1h = safe_int(main.get("q1h")); q1a = safe_int(main.get("q1a"))
    q2h = safe_int(main.get("q2h")); q2a = safe_int(main.get("q2a"))
    q3h = safe_int(main.get("q3h")); q3a = safe_int(main.get("q3a"))

    h1_total   = q1h + q2h + q1a + q2a
    cur_total  = hs + aws
    cur_margin = hs - aws
    st = main.get("st", "")
    _stage = stage or _derive_stage(st)

    # After3Q: знаємо q1..q3
    after3q_home = q1h + q2h + q3h
    after3q_away = q1a + q2a + q3a
    after3q_margin = after3q_home - after3q_away
    after3q_total  = after3q_home + after3q_away

    # Гістограма тоталів по команді A для бакетів
    hist_totals_a = [r["total"] for r in rows_a] if rows_a else []
    pn = pooled_n or (len(rows_a) + len(rows_b))

    cur_margin_bkt  = _margin_bucket_label(cur_margin)
    cur_total_bkt   = _total_bucket_label(cur_total, hist_totals_a)
    lead_trail      = _lead_trail_state(cur_margin)

    # Quarter state (скільки чвертей виграла команда A)
    q_wins_a = sum([q1h > q1a, q2h > q2a, q3h > q3a])
    q_losses_a = 3 - q_wins_a
    q_state_str = f"{q_wins_a}-{q_losses_a}"

    # ── Base history rate (overall Q3 avg для команди A) ─────────────
    q3_pts_all_a = [r["q3_team"] for r in rows_a] if rows_a else []
    base_q3_rate = rate(sum(1 for v in q3_pts_all_a if v >= 20), len(q3_pts_all_a)) if q3_pts_all_a else 0.5
    q4_pts_all_a = [r["q4_team"] for r in rows_a] if rows_a else []
    base_q4_rate = rate(sum(1 for v in q4_pts_all_a if v >= 20), len(q4_pts_all_a)) if q4_pts_all_a else 0.5

    # ── Фільтр схожих сценаріїв для команди A ────────────────────────
    def _filter_similar(rows: list, margin_bkt: str, total_bkt: str,
                        q_state: str, lead_trail: str) -> list:
        """Exact match — всі 4 фільтри."""
        exact = [
            r for r in rows
            if _margin_bucket_label(r["h1_margin"]) == margin_bkt
            and _total_bucket_label(r["h1_total"], hist_totals_a) == total_bkt
            and _quarter_win_state_full(r) == q_state
            and _lead_trail_state(r["h1_margin"]) == lead_trail
        ]
        # Wider fallback: тільки margin + lead_trail
        wider = [
            r for r in rows
            if _margin_bucket_label(r["h1_margin"]) == margin_bkt
            and _lead_trail_state(r["h1_margin"]) == lead_trail
        ]
        return exact, wider

    exact_a, wider_a = _filter_similar(rows_a, cur_margin_bkt, cur_total_bkt, q_state_str, lead_trail)
    exact_b, wider_b = _filter_similar(rows_b, cur_margin_bkt, cur_total_bkt, q_state_str, lead_trail)

    # ── Будуємо профілі по Q3 та Q4 ──────────────────────────────────
    # Team A exact
    q3_profile_a_exact = _build_segment_profile(exact_a, "q3_team", "q3_opp", base_q3_rate, pn)
    q4_profile_a_exact = _build_segment_profile(exact_a, "q4_team", "q4_opp", base_q4_rate, pn)
    # Team A wider fallback
    q3_profile_a_wider = _build_segment_profile(wider_a, "q3_team", "q3_opp", base_q3_rate, pn)
    q4_profile_a_wider = _build_segment_profile(wider_a, "q4_team", "q4_opp", base_q4_rate, pn)

    # Team B exact
    q3_profile_b_exact = _build_segment_profile(exact_b, "q3_team", "q3_opp", base_q3_rate, pn)
    q4_profile_b_exact = _build_segment_profile(exact_b, "q4_team", "q4_opp", base_q4_rate, pn)
    # Team B wider fallback
    q3_profile_b_wider = _build_segment_profile(wider_b, "q3_team", "q3_opp", base_q3_rate, pn)
    q4_profile_b_wider = _build_segment_profile(wider_b, "q4_team", "q4_opp", base_q4_rate, pn)

    # ── Required remaining threshold ─────────────────────────────────
    # після 3Q: скільки треба в Q4 для конкретного total
    required_q4_thresholds = {}
    for line in [130.5, 135.5, 140.5, 145.5, 150.5, 155.5, 160.5, 165.5, 170.5, 175.5, 180.5]:
        needed = math.floor(line) + 1 - after3q_total
        if needed > 0:
            required_q4_thresholds[str(line)] = {
                "line": line,
                "after3q_total": after3q_total,
                "needed_in_q4": needed,
                "feasibility": (
                    "HIGH" if needed <= 38 else
                    "MEDIUM" if needed <= 46 else
                    "LOW"
                ),
            }

    # ── Projection impact ─────────────────────────────────────────────
    # Яка сторона підтримується паттернами
    own_avg_q3 = q3_profile_a_exact.get("own_scoring", {}).get("avg_points")
    allow_avg_q3 = q3_profile_a_exact.get("opponent_allowed", {}).get("avg_points_allowed")
    support_side = "neutral"
    if own_avg_q3 and allow_avg_q3:
        combined_q3 = own_avg_q3 + allow_avg_q3
        if combined_q3 >= 46:   support_side = "over"
        elif combined_q3 <= 38: support_side = "under"

    return {
        "stage": _stage,
        "condition": {
            "score_state":                 f"{hs}-{aws}",
            "margin_bucket":               cur_margin_bkt,
            "current_total_bucket":        cur_total_bkt,
            "lead_trail_state":            lead_trail,
            "quarter_state":               q_state_str,
            "after3q_total":               after3q_total,
            "after3q_margin":              after3q_margin,
        },
        "required_q4_thresholds": required_q4_thresholds,
        # Team A
        "team_a_q3_exact": q3_profile_a_exact,
        "team_a_q4_exact": q4_profile_a_exact,
        "team_a_q3_wider": q3_profile_a_wider,
        "team_a_q4_wider": q4_profile_a_wider,
        # Team B
        "team_b_q3_exact": q3_profile_b_exact,
        "team_b_q4_exact": q4_profile_b_exact,
        "team_b_q3_wider": q3_profile_b_wider,
        "team_b_q4_wider": q4_profile_b_wider,
        # Sample info
        "sample_info": {
            "team_a_exact_n": len(exact_a),
            "team_a_wider_n": len(wider_a),
            "team_b_exact_n": len(exact_b),
            "team_b_wider_n": len(wider_b),
            "pooled_valid_n": pn,
            "exact_coverage_a": round(len(exact_a) / pn, 3) if pn > 0 else None,
            "wider_coverage_a": round(len(wider_a) / pn, 3) if pn > 0 else None,
        },
        "projection_impact": {
            "support_side":   support_side,
            "blocker":        len(exact_a) < 3 and len(wider_a) < 3,
            "reason": (
                "exact_sample_too_small" if len(exact_a) < 3 else
                "using_wider_fallback"   if len(exact_a) < 6 else
                "exact_sample_sufficient"
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# LIVE PROTECTION FORMULA  (розрахунки для live-ставки з хеджем)
# ═══════════════════════════════════════════════════════════════════════

def live_protection_calc(
    main: dict,
    pre_match_stat: dict,
    live_calibrated: dict,
    history_zones: dict,
    lines_data: dict = None,
) -> dict:
    """
    Live Protection: розраховує поточний стан ставки і рекомендовані
    hedge-дії для захисту прибутку або обмеження збитків.

    Ключові метрики:
    - LiveEdge: різниця між моделлю і лінією
    - LiveConfidence: довіра до live-проекції (функція хвилин і eFG overheat)
    - HedgeRecommendation: якщо LiveEdge знизився нижче порогу — рекомендація хеджу
    - ProtectionScore: 0-100, скільки відсотків від початкової кромки збережено
    """
    st = main.get("st", "")
    tour = main.get("tour", "")

    if _is_match_finished(st):
        return {"status": "FINISHED", "note": "Match is over, live protection not applicable."}
    if not live_calibrated or live_calibrated.get("status") == "NO_DATA":
        return {"status": "NO_DATA"}

    min_played = live_calibrated.get("min_played", 0)
    min_left   = live_calibrated.get("min_left", 0)
    q_dur      = live_calibrated.get("q_duration_min", 10)
    live_total = live_calibrated.get("LiveCalibrated_Total")
    pre_total  = pre_match_stat.get("PreFinal_Total") if pre_match_stat else None

    # ── Поточні BK лінії ─────────────────────────────────────────────
    bk_match_lines = extract_bk_match_total_lines(lines_data) if lines_data else []
    current_line = bk_match_lines[len(bk_match_lines) // 2] if bk_match_lines else None

    # ── LiveEdge ──────────────────────────────────────────────────────
    live_edge = None
    if live_total is not None and current_line is not None:
        live_edge = round(live_total - current_line, 2)

    # ── LiveConfidence (0..1) ─────────────────────────────────────────
    # Залежить від:
    #   - хвилин зіграних (більше → вище довіра)
    #   - overheat eFG (якщо eFG сильно вище норми → знижуємо довіру)
    lc_home = live_calibrated.get("home", {})
    lc_away = live_calibrated.get("away", {})
    overheat_h = lc_home.get("overheat_pts", 0)
    overheat_a = lc_away.get("overheat_pts", 0)
    avg_overheat = (abs(overheat_h) + abs(overheat_a)) / 2

    trust_base = lc_home.get("LiveTrust", 0.5)  # вже є в live_calibrated
    if avg_overheat >= 25:
        live_confidence = round(trust_base * 0.50, 3)
    elif avg_overheat >= 15:
        live_confidence = round(trust_base * 0.70, 3)
    elif avg_overheat >= 8:
        live_confidence = round(trust_base * 0.85, 3)
    else:
        live_confidence = round(trust_base, 3)

    # ── ProtectionScore (0-100): скільки % часу на ставку лишилось ───
    total_min = 4 * q_dur
    protection_score = round((min_left / total_min) * 100, 1) if total_min > 0 else 0

    # ── HedgeRecommendation ───────────────────────────────────────────
    hedge_rec = "NO_ACTION"
    hedge_reason = []

    if live_edge is not None:
        if live_edge < -3.0:
            hedge_rec = "CONSIDER_HEDGE"
            hedge_reason.append(f"LiveEdge={live_edge}: модель нижче лінії на {abs(live_edge)}")
        elif live_edge < -1.5:
            hedge_rec = "MONITOR"
            hedge_reason.append(f"LiveEdge={live_edge}: наближається до негативного")

    if live_confidence < 0.35:
        hedge_rec = "CONSIDER_HEDGE"
        hedge_reason.append(f"LiveConfidence={live_confidence}: дуже низька (сильний overheat eFG)")
    elif live_confidence < 0.50 and hedge_rec == "NO_ACTION":
        hedge_rec = "MONITOR"
        hedge_reason.append(f"LiveConfidence={live_confidence}: помірно низька")

    if min_left <= q_dur and protection_score <= 25:
        if hedge_rec == "NO_ACTION" and live_edge and abs(live_edge) < 2.0:
            hedge_rec = "PARTIAL_HEDGE_POSSIBLE"
            hedge_reason.append("Залишилась <1 чверть, ставка близько до лінії — часткове хеджування")

    # ── Live vs Pre порівняння ────────────────────────────────────────
    live_vs_pre = None
    if live_total is not None and pre_total is not None:
        live_vs_pre = {
            "delta": round(live_total - pre_total, 2),
            "direction": "OVER_TRACKING" if live_total > pre_total else "UNDER_TRACKING",
            "magnitude": (
                "LARGE"  if abs(live_total - pre_total) >= 8 else
                "MEDIUM" if abs(live_total - pre_total) >= 4 else
                "SMALL"
            ),
        }

    # ── Tempo drift (зміна темпу відносно норми) ─────────────────────
    fga_h = lc_home.get("Rem_FGA", 0)
    fga_a = lc_away.get("Rem_FGA", 0)
    tempo_signal = "NORMAL"
    if min_played > 0:
        proj_total_fga_per_min = (fga_h + fga_a) / min_left if min_left > 0 else 0
        if proj_total_fga_per_min >= 5.0:   tempo_signal = "HIGH_TEMPO"
        elif proj_total_fga_per_min <= 3.0: tempo_signal = "LOW_TEMPO"

    return {
        "status": "ACTIVE",
        "min_played": min_played,
        "min_left":   min_left,
        "live_calibrated_total": live_total,
        "pre_match_total":       pre_total,
        "current_bk_line":       current_line,
        "live_edge":             live_edge,
        "live_confidence":       live_confidence,
        "protection_score":      protection_score,
        "hedge_recommendation":  hedge_rec,
        "hedge_reasons":         hedge_reason,
        "live_vs_pre":           live_vs_pre,
        "avg_efg_overheat":      round(avg_overheat, 2),
        "tempo_signal":          tempo_signal,
        "interpretation": (
            "Модель вище лінії — ставка на OVER утримує edge"
            if live_edge and live_edge >= 2.0 else
            "Модель нижче лінії — розглянь хедж або вихід"
            if live_edge and live_edge <= -2.0 else
            "Нейтральна зона — продовжуй моніторинг"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# PRE-MATCH PROTECTION FORMULA  (розрахунки для захисту pre-live ставки)
# ═══════════════════════════════════════════════════════════════════════

def pre_match_protection_calc(
    main: dict,
    pre_match_stat: dict,
    history_zones: dict,
    lines_data: dict = None,
    rows_a: list = None,
    rows_b: list = None,
) -> dict:
    """
    Pre-Match Protection: аналіз edge до початку гри.
    Визначає:
    - PreEdge: різниця між проекцією і BK лінією
    - HistoryEdge: різниця між hist_avg і BK лінією
    - ConflictGate: чи є конфлікт між проекцією і статистикою
    - LineMovement: чи рухається лінія проти нас
    - ConfidenceGate: фінальний сигнал допуску до ставки
    """
    if not pre_match_stat or pre_match_stat.get("status") == "NO_DATA":
        return {"status": "NO_DATA"}

    rows_a = rows_a or []
    rows_b = rows_b or []

    pre_total = pre_match_stat.get("PreFinal_Total")
    pre_margin = pre_match_stat.get("PreFinal_Margin")

    # ── BK лінії ─────────────────────────────────────────────────────
    bk_match_lines = extract_bk_match_total_lines(lines_data) if lines_data else []
    mid_line = bk_match_lines[len(bk_match_lines) // 2] if bk_match_lines else None

    bk_hcp_lines = extract_bk_match_handicap_lines(lines_data, "Match") if lines_data else []
    mid_hcp = bk_hcp_lines[len(bk_hcp_lines) // 2] if bk_hcp_lines else None

    # ── PreEdge ───────────────────────────────────────────────────────
    pre_edge_total = round(pre_total - mid_line, 2) if (pre_total and mid_line) else None
    pre_edge_hcp   = round(pre_margin - mid_hcp, 2) if (pre_margin and mid_hcp) else None

    # ── HistoryEdge: середній тотал команд vs BK лінія ───────────────
    hist_totals_a = [r["total"] for r in rows_a] if rows_a else []
    hist_totals_b = [r["total"] for r in rows_b] if rows_b else []
    pooled_totals = hist_totals_a + hist_totals_b
    hist_avg_total = round(statistics.mean(pooled_totals), 2) if pooled_totals else None
    hist_edge_total = round(hist_avg_total - mid_line, 2) if (hist_avg_total and mid_line) else None

    # ── ConflictGate ─────────────────────────────────────────────────
    conflict_gate = "ALIGNED"
    conflict_reasons = []
    if pre_edge_total is not None and hist_edge_total is not None:
        if (pre_edge_total > 0 and hist_edge_total < -2) or (pre_edge_total < 0 and hist_edge_total > 2):
            conflict_gate = "CONFLICT"
            conflict_reasons.append(
                f"PreEdge={pre_edge_total} протилежний HistEdge={hist_edge_total}"
            )
    if pre_edge_total is not None and abs(pre_edge_total) < 1.0:
        conflict_gate = "THIN_EDGE"
        conflict_reasons.append("PreEdge < 1.0: край дуже вузький")

    # ── LineMovement (якщо є opening vs current у lines_data) ────────
    line_movement = None
    if lines_data and bk_match_lines:
        mt = lines_data.get("match_total", [])
        moves = []
        for e in mt:
            over_open = e.get("overOpen")
            over_curr = e.get("overOdd") or e.get("over_odd")
            line_val  = e.get("line")
            if over_open and over_curr and line_val:
                try:
                    delta_odds = round(float(over_curr) - float(over_open), 3)
                    moves.append({"line": line_val, "delta_odds": delta_odds})
                except (TypeError, ValueError):
                    pass
        if moves:
            avg_move = round(statistics.mean([m["delta_odds"] for m in moves]), 3)
            line_movement = {
                "avg_odds_delta": avg_move,
                "direction": "SHORTENING" if avg_move < -0.05 else "DRIFTING" if avg_move > 0.05 else "STABLE",
                "details": moves[:5],
            }

    # ── Hit rates по BK лініях (over/under) ─────────────────────────
    over_rates_by_line = {}
    if mid_line and hist_totals_a:
        for line in bk_match_lines[:5]:  # топ-5 BK ліній
            h_a = sum(1 for v in hist_totals_a if v > line)
            h_b = sum(1 for v in hist_totals_b if v > line)
            na, nb = len(hist_totals_a), len(hist_totals_b)
            over_rates_by_line[str(line)] = {
                "team_a_over_rate": rate(h_a, na),
                "team_b_over_rate": rate(h_b, nb),
                "pooled_over_rate": rate(h_a + h_b, na + nb),
                "fraction_a": pct_str(h_a, na),
                "fraction_b": pct_str(h_b, nb),
            }

    # ── ConfidenceGate ────────────────────────────────────────────────
    confidence_gate = "GO"
    confidence_reasons = []

    if conflict_gate == "CONFLICT":
        confidence_gate = "PASS"
        confidence_reasons.append("PreEdge vs HistEdge конфлікт — не грати")
    elif conflict_gate == "THIN_EDGE":
        confidence_gate = "REDUCE_STAKE"
        confidence_reasons.append("Вузький edge — зменшити ставку або пропустити")
    elif pre_edge_total and abs(pre_edge_total) < 2.0 and hist_edge_total and abs(hist_edge_total) < 2.0:
        confidence_gate = "REDUCE_STAKE"
        confidence_reasons.append("Обидва edges < 2 — ставка можлива але з обмеженим сайзингом")

    if line_movement and line_movement["direction"] == "SHORTENING":
        if confidence_gate == "GO":
            confidence_gate = "REDUCE_STAKE"
        confidence_reasons.append("Лінія скорочується (shortening) — розумний гравець ставить раніше")

    # ── ORB/TO balance (penalty для pre-match) ────────────────────────
    orb_a = round(statistics.mean([r.get("ORB", 0) for r in rows_a]), 2) if rows_a else None
    orb_b = round(statistics.mean([r.get("ORB", 0) for r in rows_b]), 2) if rows_b else None
    to_a  = round(statistics.mean([r.get("TO", 0) for r in rows_a]), 2)  if rows_a else None
    to_b  = round(statistics.mean([r.get("TO", 0) for r in rows_b]), 2)  if rows_b else None

    return {
        "status": "ACTIVE",
        "pre_projection_total": pre_total,
        "pre_projection_margin": pre_margin,
        "bk_mid_line_total":    mid_line,
        "bk_mid_line_hcp":      mid_hcp,
        "pre_edge_total":       pre_edge_total,
        "pre_edge_hcp":         pre_edge_hcp,
        "hist_avg_total":       hist_avg_total,
        "hist_edge_total":      hist_edge_total,
        "conflict_gate":        conflict_gate,
        "conflict_reasons":     conflict_reasons,
        "line_movement":        line_movement,
        "confidence_gate":      confidence_gate,
        "confidence_reasons":   confidence_reasons,
        "over_rates_by_line":   over_rates_by_line,
        "orb_to_balance": {
            "avg_orb_a": orb_a, "avg_orb_b": orb_b,
            "avg_to_a":  to_a,  "avg_to_b":  to_b,
            "net_extra_poss_a": round(orb_a - to_a, 2) if (orb_a and to_a) else None,
            "net_extra_poss_b": round(orb_b - to_b, 2) if (orb_b and to_b) else None,
        },
        "interpretation": (
            "GO: strong pre-edge, aligned history, no conflict"
            if confidence_gate == "GO" and pre_edge_total and pre_edge_total >= 3.0 else
            "GO with caution: edge exists but thin or minor conflict"
            if confidence_gate == "GO" else
            "REDUCE_STAKE: edge too thin or line moving against"
            if confidence_gate == "REDUCE_STAKE" else
            "PASS: conflict between projection and history — no bet"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# PRE-CALCULATED PATTERNS BLOCK  (ТЗ §2-11)
# ═══════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────
# ТЗ §12 — Season / Date Context
# ─────────────────────────────────────────────
def compute_season_date_context(rows: list, label: str) -> dict:
    """
    Analyse date distribution of enriched hist rows (output of build_hist_row).
    Returns a full context dict used to determine history_trust_level.
    """
    from datetime import datetime as _dt

    # a) Filter valid rows
    valid_rows = [r for r in rows
                  if r.get("date_parse_status") == "ON" and r.get("match_date") is not None]
    valid_n = len(valid_rows)
    if valid_n == 0:
        return {
            "status": "OFF",
            "missing_fields": ["match_date"],
            "reason": "no parseable dates in history",
            "label": label,
        }

    # b) Sort by match_date ascending
    valid_rows = sorted(valid_rows, key=lambda r: r["match_date"])

    # c) Date metrics
    dates = [_dt.strptime(r["match_date"], "%Y-%m-%d") for r in valid_rows]
    earliest_match_date = valid_rows[0]["match_date"]
    latest_match_date   = valid_rows[-1]["match_date"]
    date_range_days     = (dates[-1] - dates[0]).days

    days_between_games = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
    avg_days_between_games    = round(statistics.mean(days_between_games), 1) if days_between_games else None
    median_days_between_games = int(round(statistics.median(days_between_games), 0)) if days_between_games else None
    max_gap_days              = max(days_between_games) if days_between_games else 0
    long_breaks_14_plus       = sum(1 for g in days_between_games if g >= 14)
    long_breaks_21_plus       = sum(1 for g in days_between_games if g >= 21)
    long_breaks_30_plus       = sum(1 for g in days_between_games if g >= 30)

    # d) Season split — derive current_season_label from latest_match_date
    _lm = _dt.strptime(latest_match_date, "%Y-%m-%d")
    if _lm.month >= 8:
        current_season_label = f"{_lm.year}/{str(_lm.year + 1)[-2:]}"
    else:
        current_season_label = f"{_lm.year - 1}/{str(_lm.year)[-2:]}"

    current_season_games  = sum(1 for r in valid_rows if r.get("season_label") == current_season_label)
    previous_season_games = valid_n - current_season_games

    # e) Flags
    season_start_flag  = (current_season_games < 10) or (previous_season_games > 0 and current_season_games < 15)
    long_break_flag    = (max_gap_days >= 21) or (long_breaks_14_plus >= 2)
    mixed_season_flag  = (previous_season_games > 0) and (current_season_games < 25)

    # f) Trust level
    if current_season_games < 10:
        history_trust_level      = "heavily_reduced"
        history_trust_multiplier = 0.20
    elif long_break_flag or (mixed_season_flag and current_season_games < 20):
        history_trust_level      = "reduced"
        history_trust_multiplier = 0.65
    elif (mixed_season_flag and current_season_games >= 20) or (long_breaks_14_plus == 1 and not long_break_flag):
        history_trust_level      = "slightly_reduced"
        history_trust_multiplier = 0.80
    else:
        history_trust_level      = "normal"
        history_trust_multiplier = 1.00

    # g) Ukrainian note
    if history_trust_level == "heavily_reduced":
        history_trust_note_ua = (
            "Старт сезону або дуже мало ігор поточного сезону. "
            "Довіра до raw-history і патернів максимально знижена. "
            "Ставка тільки на основі live/stat."
        )
    elif history_trust_level == "reduced":
        history_trust_note_ua = (
            "Є довга пауза між матчами або змішаний сезон. "
            "Raw-history і патерни використовувати з пониженою довірою; "
            "live/stat profile має важити більше."
        )
    elif history_trust_level == "slightly_reduced":
        history_trust_note_ua = (
            "Частина sample з минулого сезону або невеликі паузи. "
            "Довіра до історії трохи знижена."
        )
    else:
        history_trust_note_ua = (
            "Дати матчів стабільні, sample переважно поточного сезону. "
            "Довіра до історії normal."
        )

    # h) Return full dict
    return {
        "status": "ON",
        "label": label,
        "valid_games_with_dates": valid_n,
        "earliest_match_date": earliest_match_date,
        "latest_match_date": latest_match_date,
        "date_range_days": date_range_days,
        "days_between_games": days_between_games,
        "avg_days_between_games": avg_days_between_games,
        "median_days_between_games": median_days_between_games,
        "max_gap_days": max_gap_days,
        "long_breaks_14_plus": long_breaks_14_plus,
        "long_breaks_21_plus": long_breaks_21_plus,
        "long_breaks_30_plus": long_breaks_30_plus,
        "current_season_label": current_season_label,
        "current_season_games": current_season_games,
        "previous_season_games": previous_season_games,
        "season_start_flag": season_start_flag,
        "long_break_flag": long_break_flag,
        "mixed_season_flag": mixed_season_flag,
        "history_trust_level": history_trust_level,
        "history_trust_multiplier": history_trust_multiplier,
        "history_trust_note_ua": history_trust_note_ua,
    }


def compute_pre_calculated_patterns(
    rows_a: list,
    rows_b: list,
    h2h_rows: list,
    main: dict = None,
    live_calibrated: dict = None,
    lines_data: dict = None,
) -> dict:
    """
    Compute all 17 required pre-calculated pattern blocks per ТЗ.
    Every hit includes source_game_indexes for manual verification.
    Excludes current match and technical forfeits (already filtered upstream).
    """

    def _z(r) -> str:
        if r is None: return "N/A"
        if r >= 1.0:  return "100%"
        if r >= 0.95: return "95%+"
        if r >= 0.90: return "90%+"
        if r >= 0.85: return "85%+"
        if r >= 0.80: return "80%+"
        if r >= 0.75: return "75%+"
        return "below_75"

    def _entry(hits: int, n: int, idx: list) -> dict:
        if n == 0:
            return {"hits": 0, "n": 0, "rate": None, "smoothed_rate": None, "zone": None, "source_game_indexes": []}
        r = round(hits / n, 3)
        sm = round((hits + 1) / (n + 2), 3)
        return {"hits": hits, "n": n, "rate": r, "smoothed_rate": sm, "zone": _z(r), "source_game_indexes": idx}

    def _summary_zones(block: dict, pcts: list = None) -> dict:
        if pcts is None:
            pcts = [1.0, 0.80]
        out = {}
        for p in pcts:
            label = "100_zones" if p >= 1.0 else f"{int(p*100)}_zones"
            out[label] = [k for k, v in block.items() if isinstance(v, dict) and (v.get("rate") or 0) >= p]
        return out

    # ─── helpers ───────────────────────────────────────────────────────
    def _avg_or_none(lst):
        return round(statistics.mean(lst), 3) if lst else None

    def _median_or_none(lst):
        return round(statistics.median(lst), 3) if lst else None

    def _pct_or_none(lst, p):
        return round(percentile(lst, p), 3) if lst else None

    def _stat_desc(vals: list) -> dict:
        if not vals:
            return {}
        return {
            "avg":    round(statistics.mean(vals), 3),
            "median": round(statistics.median(vals), 3),
            "p25":    round(percentile(vals, 25), 3),
            "p75":    round(percentile(vals, 75), 3),
            "p90":    round(percentile(vals, 90), 3),
            "min":    round(min(vals), 3),
            "max":    round(max(vals), 3),
            "n":      len(vals),
        }

    # ═══════════════════════════════════════════════════════════════════
    # §4 – Team quarter points patterns
    # ═══════════════════════════════════════════════════════════════════
    Q_PT_THRESHOLDS = [10, 12, 14, 15, 16, 18, 20, 21, 22, 24, 25, 28, 30, 32, 35]

    def _at_least_one_q_scored(rows: list) -> dict:
        out = {}
        for t in Q_PT_THRESHOLDS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) >= t]
            out[f"{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    def _no_q_scored(rows: list) -> dict:
        out = {}
        for t in Q_PT_THRESHOLDS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) < t]
            out[f"{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    def _opp_allowed_at_least_one_q(rows: list) -> dict:
        out = {}
        for t in Q_PT_THRESHOLDS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_opp"], r["q2_opp"], r["q3_opp"], r["q4_opp"]) >= t]
            out[f"{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    def _opp_no_q_allowed(rows: list) -> dict:
        out = {}
        for t in Q_PT_THRESHOLDS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_opp"], r["q2_opp"], r["q3_opp"], r["q4_opp"]) < t]
            out[f"{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    pooled = rows_a + rows_b

    # ТЗ §12: season / date context
    season_date_context = {
        "team_a": compute_season_date_context(rows_a, "team_a"),
        "team_b": compute_season_date_context(rows_b, "team_b"),
        "pooled": compute_season_date_context(pooled, "pooled"),
        "h2h":    compute_season_date_context(h2h_rows, "h2h"),
    }

    blk_41_a  = _at_least_one_q_scored(rows_a)
    blk_41_b  = _at_least_one_q_scored(rows_b)
    blk_41_p  = _at_least_one_q_scored(pooled)
    blk_41_h  = _at_least_one_q_scored(h2h_rows)

    blk_42_a  = _no_q_scored(rows_a)
    blk_42_b  = _no_q_scored(rows_b)
    blk_42_p  = _no_q_scored(pooled)
    blk_42_h  = _no_q_scored(h2h_rows)

    blk_43_a  = _opp_allowed_at_least_one_q(rows_a)
    blk_43_b  = _opp_allowed_at_least_one_q(rows_b)
    blk_43_p  = _opp_allowed_at_least_one_q(pooled)
    blk_43_h  = _opp_allowed_at_least_one_q(h2h_rows)

    blk_44_a  = _opp_no_q_allowed(rows_a)
    blk_44_b  = _opp_no_q_allowed(rows_b)
    blk_44_p  = _opp_no_q_allowed(pooled)
    blk_44_h  = _opp_no_q_allowed(h2h_rows)

    team_quarter_points_patterns = {
        "at_least_one_team_quarter_scored_x_plus": {
            "team_a_last35": blk_41_a,
            "team_b_last35": blk_41_b,
            "pooled":        blk_41_p,
            "h2h":           blk_41_h,
            "summary": {
                "team_a": _summary_zones(blk_41_a),
                "team_b": _summary_zones(blk_41_b),
                "pooled": _summary_zones(blk_41_p),
                "h2h":    _summary_zones(blk_41_h),
            },
        },
        "no_team_quarter_scored_x_plus": {
            "team_a_last35": blk_42_a,
            "team_b_last35": blk_42_b,
            "pooled":        blk_42_p,
            "h2h":           blk_42_h,
            "summary": {
                "team_a": _summary_zones(blk_42_a),
                "team_b": _summary_zones(blk_42_b),
                "pooled": _summary_zones(blk_42_p),
                "h2h":    _summary_zones(blk_42_h),
            },
        },
        "opponent_allowed_at_least_one_quarter_x_plus": {
            "team_a_last35": blk_43_a,
            "team_b_last35": blk_43_b,
            "pooled":        blk_43_p,
            "h2h":           blk_43_h,
            "summary": {
                "team_a": _summary_zones(blk_43_a),
                "team_b": _summary_zones(blk_43_b),
                "pooled": _summary_zones(blk_43_p),
                "h2h":    _summary_zones(blk_43_h),
            },
        },
        "opponent_no_quarter_allowed_x_plus": {
            "team_a_last35": blk_44_a,
            "team_b_last35": blk_44_b,
            "pooled":        blk_44_p,
            "h2h":           blk_44_h,
            "summary": {
                "team_a": _summary_zones(blk_44_a),
                "team_b": _summary_zones(blk_44_b),
                "pooled": _summary_zones(blk_44_p),
                "h2h":    _summary_zones(blk_44_h),
            },
        },
    }

    # ═══════════════════════════════════════════════════════════════════
    # §5 – Match quarter total patterns
    # ═══════════════════════════════════════════════════════════════════
    MQ_TOTAL_THRESHOLDS_HIGH  = [30, 32, 35, 36, 38, 40, 42, 44, 45, 48, 50, 52, 55, 58, 60]
    MQ_TOTAL_THRESHOLDS_LOW   = [45, 42, 40, 38, 36, 35, 32, 30, 28, 25]
    MQ_TEAM_UNDER_THRESHOLDS  = [22, 20, 18, 16, 15, 14, 12, 10]

    def _at_least_one_qt_high(rows: list) -> dict:
        out = {}
        for t in MQ_TOTAL_THRESHOLDS_HIGH:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_total"], r["q2_total"], r["q3_total"], r["q4_total"]) >= t]
            out[f"{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    def _at_least_one_qt_under(rows: list) -> dict:
        out = {}
        for t in MQ_TOTAL_THRESHOLDS_LOW:
            idx = [i for i, r in enumerate(rows)
                   if min(r["q1_total"], r["q2_total"], r["q3_total"], r["q4_total"]) < t]
            out[f"<{t}"] = _entry(len(idx), len(rows), idx)
        return out

    def _at_least_one_team_q_under(rows: list) -> dict:
        out = {}
        for t in MQ_TEAM_UNDER_THRESHOLDS:
            idx = [i for i, r in enumerate(rows)
                   if min(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) < t]
            out[f"<{t}"] = _entry(len(idx), len(rows), idx)
        return out

    match_quarter_total_patterns = {
        "at_least_one_match_quarter_total_x_plus": {
            "team_a_last35": _at_least_one_qt_high(rows_a),
            "team_b_last35": _at_least_one_qt_high(rows_b),
            "pooled":        _at_least_one_qt_high(pooled),
            "h2h":           _at_least_one_qt_high(h2h_rows),
        },
        "at_least_one_match_quarter_total_under_x": {
            "team_a_last35": _at_least_one_qt_under(rows_a),
            "team_b_last35": _at_least_one_qt_under(rows_b),
            "pooled":        _at_least_one_qt_under(pooled),
            "h2h":           _at_least_one_qt_under(h2h_rows),
        },
        "at_least_one_team_quarter_under_x": {
            "team_a_last35": _at_least_one_team_q_under(rows_a),
            "team_b_last35": _at_least_one_team_q_under(rows_b),
            "pooled":        _at_least_one_team_q_under(pooled),
            "h2h":           _at_least_one_team_q_under(h2h_rows),
        },
    }

    # ═══════════════════════════════════════════════════════════════════
    # §6 – Quarter spread / handicap patterns
    # ═══════════════════════════════════════════════════════════════════
    MINUS_SPREADS = [1.5, 2.5, 3.5, 5.5, 7.5, 9.5, 10.5]
    PLUS_SPREADS  = [1.5, 3.5, 5.5, 7.5, 9.5]
    LOST_BY_MARGINS = [5, 7, 10, 12, 15]

    def _cover_minus(rows: list) -> dict:
        out = {}
        for sp in MINUS_SPREADS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_margin"], r["q2_margin"], r["q3_margin"], r["q4_margin"]) >= sp]
            out[f"-{sp}"] = _entry(len(idx), len(rows), idx)
        return out

    def _cover_plus(rows: list) -> dict:
        out = {}
        for sp in PLUS_SPREADS:
            idx = [i for i, r in enumerate(rows)
                   if max(r["q1_margin"], r["q2_margin"], r["q3_margin"], r["q4_margin"]) >= -sp]
            out[f"+{sp}"] = _entry(len(idx), len(rows), idx)
        return out

    def _lost_by(rows: list) -> dict:
        out = {}
        for m in LOST_BY_MARGINS:
            idx = [i for i, r in enumerate(rows)
                   if min(r["q1_margin"], r["q2_margin"], r["q3_margin"], r["q4_margin"]) <= -m]
            out[f"-{m}"] = _entry(len(idx), len(rows), idx)
        return out

    def _no_loss_q(rows: list) -> dict:
        idx = [i for i, r in enumerate(rows)
               if max(r["q1_margin"], r["q2_margin"], r["q3_margin"], r["q4_margin"]) >= 0]
        return _entry(len(idx), len(rows), idx)

    quarter_spread_patterns = {
        "at_least_one_quarter_cover_minus_spread": {
            "team_a_last35": _cover_minus(rows_a),
            "team_b_last35": _cover_minus(rows_b),
            "pooled":        _cover_minus(pooled),
            "h2h":           _cover_minus(h2h_rows),
        },
        "at_least_one_quarter_cover_plus_spread": {
            "team_a_last35": _cover_plus(rows_a),
            "team_b_last35": _cover_plus(rows_b),
            "pooled":        _cover_plus(pooled),
            "h2h":           _cover_plus(h2h_rows),
        },
        "at_least_one_quarter_lost_by_margin": {
            "team_a_last35": _lost_by(rows_a),
            "team_b_last35": _lost_by(rows_b),
            "pooled":        _lost_by(pooled),
            "h2h":           _lost_by(h2h_rows),
        },
        "quarter_no_loss_profile": {
            "team_a_last35": _no_loss_q(rows_a),
            "team_b_last35": _no_loss_q(rows_b),
            "pooled":        _no_loss_q(pooled),
            "h2h":           _no_loss_q(h2h_rows),
        },
    }

    # ═══════════════════════════════════════════════════════════════════
    # §7 – Quarter sweep profile
    # ═══════════════════════════════════════════════════════════════════
    def _sweep_profile(rows: list) -> dict:
        n = len(rows)
        lost_q1_idx  = [i for i, r in enumerate(rows) if r["q1_margin"] < 0]
        lost_q2_idx  = [i for i, r in enumerate(rows) if r["q2_margin"] < 0]
        lost_all_idx = [i for i, r in enumerate(rows)
                        if r["q1_margin"] < 0 and r["q2_margin"] < 0
                        and r["q3_margin"] < 0 and r["q4_margin"] < 0]
        won_q1_idx   = [i for i, r in enumerate(rows) if r["q1_margin"] > 0]
        won_q2_idx   = [i for i, r in enumerate(rows) if r["q2_margin"] > 0]
        won_all_idx  = [i for i, r in enumerate(rows)
                        if r["q1_margin"] > 0 and r["q2_margin"] > 0
                        and r["q3_margin"] > 0 and r["q4_margin"] > 0]

        # rows with lost_q1 AND lost_q2
        lost_first2_set = set(i for i, r in enumerate(rows) if r["q1_margin"] < 0 and r["q2_margin"] < 0)
        n_lf2 = len(lost_first2_set)

        after_lost_q1q2_lost_all = [i for i in lost_first2_set
                                     if rows[i]["q3_margin"] < 0 and rows[i]["q4_margin"] < 0]
        after_lost_q1q2_not_lost_all = [i for i in lost_first2_set
                                         if not (rows[i]["q3_margin"] < 0 and rows[i]["q4_margin"] < 0)]
        after_lost_q1q2_won_q3 = [i for i in lost_first2_set if rows[i]["q3_margin"] >= 0]
        after_lost_q1q2_won_q4 = [i for i in lost_first2_set if rows[i]["q4_margin"] >= 0]

        won_first2_set = set(i for i, r in enumerate(rows) if r["q1_margin"] > 0 and r["q2_margin"] > 0)
        n_wf2 = len(won_first2_set)

        after_won_q1q2_won_all  = [i for i in won_first2_set
                                    if rows[i]["q3_margin"] > 0 and rows[i]["q4_margin"] > 0]
        after_won_q1q2_lost_q3  = [i for i in won_first2_set if rows[i]["q3_margin"] < 0]
        after_won_q1q2_lost_q4  = [i for i in won_first2_set if rows[i]["q4_margin"] < 0]

        def _e2(hits_list, total):
            return _entry(len(hits_list), total, list(hits_list))

        return {
            "lost_q1":          _e2(lost_q1_idx, n),
            "lost_q2":          _e2(lost_q2_idx, n),
            "lost_q1_q2":       _e2(list(lost_first2_set), n),
            "won_q1_q2":        _e2(list(won_first2_set), n),
            "lost_all_4_quarters": _e2(lost_all_idx, n),
            "won_all_4_quarters":  _e2(won_all_idx, n),
            # After lost Q1+Q2
            "after_lost_q1_q2_n": n_lf2,
            "after_lost_q1_q2_lost_all_4":       _e2(after_lost_q1q2_lost_all,    n_lf2),
            "after_lost_q1_q2_not_lost_all_4":   _e2(after_lost_q1q2_not_lost_all, n_lf2),
            "after_lost_q1_q2_won_or_tied_q3":   _e2(after_lost_q1q2_won_q3,      n_lf2),
            "after_lost_q1_q2_won_or_tied_q4":   _e2(after_lost_q1q2_won_q4,      n_lf2),
            # After won Q1+Q2
            "after_won_q1_q2_n": n_wf2,
            "after_won_q1_q2_won_all_4":  _e2(after_won_q1q2_won_all, n_wf2),
            "after_won_q1_q2_lost_q3":    _e2(after_won_q1q2_lost_q3, n_wf2),
            "after_won_q1_q2_lost_q4":    _e2(after_won_q1q2_lost_q4, n_wf2),
        }

    quarter_sweep_profile = {
        "team_a": _sweep_profile(rows_a),
        "team_b": _sweep_profile(rows_b),
        "pooled": _sweep_profile(pooled),
        "h2h":    _sweep_profile(h2h_rows),
    }

    # ═══════════════════════════════════════════════════════════════════
    # §9 – Historical stat averages + §9.1 threshold hit-rates
    # ═══════════════════════════════════════════════════════════════════
    MATCH_FGA_THR  = [60, 65, 70, 75, 80]
    MATCH_POSS_THR = [65, 70, 75, 80]
    MATCH_2PA_THR  = [35, 40, 45, 50]
    MATCH_3PA_THR  = [20, 25, 30, 35]
    MATCH_FTA_THR  = [10, 15, 20, 25, 30]
    MATCH_FOULS_THR= [15, 18, 20, 22, 25]
    MATCH_ORB_THR  = [7,  9,  10, 12]
    MATCH_TO_THR   = [8,  10, 12, 14, 16]
    MATCH_EFG_THR  = [0.45, 0.50, 0.55, 0.60]
    Q_FGA_THR      = [12, 14, 16, 18, 20]
    Q_POSS_THR     = [14, 16, 18, 20, 22]
    Q_FTA_THR      = [2,  4,  6,  8]
    Q_FOULS_THR    = [3,  4,  5,  6]

    def _match_stat_avgs(rows: list) -> dict:
        if not rows:
            return {"status": "OFF", "n": 0}
        def _fld(key):
            vals = [r.get(key, 0) for r in rows if r.get(key) is not None]
            return _stat_desc(vals)

        q_avg = {}
        for qk in ("q1", "q2", "q3", "q4"):
            qrows = rows  # per-quarter stats come from the same rows
            def _qfld(suffix, _qk=qk):
                vals = [r.get(f"{_qk}_{suffix}", 0) for r in rows if r.get(f"{_qk}_{suffix}") is not None]
                return _stat_desc(vals)
            q_avg[qk] = {
                "team_pts": _qfld("team"),
                "opp_pts":  _qfld("opp"),
                "total":    _qfld("total"),
            }

        return {
            "n":      len(rows),
            "match": {
                "fga":   _fld("FGA"),
                "poss":  _fld("poss"),
                "twoPA": _fld("2PA"),
                "twoPpct": _stat_desc([r.get("2PM", 0) / r.get("2PA", 1) for r in rows if r.get("2PA", 0) > 0]),
                "threePA": _fld("3PA"),
                "threePpct": _stat_desc([r.get("3PM", 0) / r.get("3PA", 1) for r in rows if r.get("3PA", 0) > 0]),
                "fta":   _fld("FTA"),
                "ftpct": _stat_desc([r.get("FTM", 0) / r.get("FTA", 1) for r in rows if r.get("FTA", 0) > 0]),
                "orb":   _fld("ORB"),
                "drb":   _fld("DRB"),
                "to":    _fld("TO"),
                "fouls": _fld("fouls"),
                "efg":   _fld("efg"),
                "pts":   _fld("team_points"),
                "opp_pts": _fld("opp_points"),
                "total": _fld("total"),
                "offrtg": _fld("off_rtg"),
            },
            "by_quarter": q_avg,
        }

    def _thr_hits(rows: list, key: str, thresholds: list, op: str = ">=") -> dict:
        out = {}
        for t in thresholds:
            if op == ">=":
                idx = [i for i, r in enumerate(rows) if (r.get(key) or 0) >= t]
            else:
                idx = [i for i, r in enumerate(rows) if (r.get(key) or 0) <= t]
            label = f"{key}_{t}+" if op == ">=" else f"{key}_{t}-"
            out[label] = _entry(len(idx), len(rows), idx)
        return out

    # Quarter-level threshold definitions per TZ
    Q_FGA_THR   = [12, 14, 16, 18, 20]
    Q_POSS_THR  = [14, 16, 18, 20, 22]
    Q_FTA_THR   = [2, 4, 6, 8]
    Q_FOULS_THR = [3, 4, 5, 6]

    def _quarter_thr_hits(rows: list, q: str) -> dict:
        """Compute threshold hit-rates for a single quarter (q='q1'|'q2'|'q3'|'q4')."""
        out = {}
        fga_key   = f"{q}_fga"
        poss_key  = f"{q}_poss"
        fta_key   = f"{q}_fta"
        fouls_key = f"{q}_fouls"
        prefix    = q.upper()
        for t in Q_FGA_THR:
            idx = [i for i, r in enumerate(rows) if (r.get(fga_key) or 0) >= t]
            out[f"{prefix}_FGA_{t}+"] = _entry(len(idx), len(rows), idx)
        for t in Q_POSS_THR:
            idx = [i for i, r in enumerate(rows) if (r.get(poss_key) or 0) >= t]
            out[f"{prefix}_poss_{t}+"] = _entry(len(idx), len(rows), idx)
        for t in Q_FTA_THR:
            idx = [i for i, r in enumerate(rows) if (r.get(fta_key) or 0) >= t]
            out[f"{prefix}_FTA_{t}+"] = _entry(len(idx), len(rows), idx)
        for t in Q_FOULS_THR:
            idx = [i for i, r in enumerate(rows) if (r.get(fouls_key) or 0) >= t]
            out[f"{prefix}_fouls_{t}+"] = _entry(len(idx), len(rows), idx)
        return out

    def _match_stat_thresholds(rows: list) -> dict:
        if not rows:
            return {"status": "OFF"}
        out = {}
        out.update(_thr_hits(rows, "FGA",   MATCH_FGA_THR))
        out.update(_thr_hits(rows, "poss",  MATCH_POSS_THR))
        out.update(_thr_hits(rows, "2PA",   MATCH_2PA_THR))
        out.update(_thr_hits(rows, "3PA",   MATCH_3PA_THR))
        out.update(_thr_hits(rows, "FTA",   MATCH_FTA_THR))
        out.update(_thr_hits(rows, "fouls", MATCH_FOULS_THR))
        out.update(_thr_hits(rows, "ORB",   MATCH_ORB_THR))
        out.update(_thr_hits(rows, "TO",    MATCH_TO_THR))
        out.update(_thr_hits(rows, "efg",   MATCH_EFG_THR))
        # Quarter-level thresholds (Q1–Q4)
        for _q in ("q1", "q2", "q3", "q4"):
            out.update(_quarter_thr_hits(rows, _q))
        return out

    historical_stat_averages = {
        "team_a": _match_stat_avgs(rows_a),
        "team_b": _match_stat_avgs(rows_b),
        "pooled": _match_stat_avgs(pooled),
        "h2h":    _match_stat_avgs(h2h_rows),
    }

    historical_stat_threshold_hit_rates = {
        "team_a_last35": _match_stat_thresholds(rows_a),
        "team_b_last35": _match_stat_thresholds(rows_b),
        "pooled":        _match_stat_thresholds(pooled),
        "h2h":           _match_stat_thresholds(h2h_rows),
    }

    # ═══════════════════════════════════════════════════════════════════
    # §10 – Fouls by margin state in Q3/Q4
    # ═══════════════════════════════════════════════════════════════════
    MARGIN_STATES = {
        "tie":           lambda m: m == 0,
        "leading_1_5":   lambda m: 1 <= m <= 5,
        "leading_6_10":  lambda m: 6 <= m <= 10,
        "leading_11_15": lambda m: 11 <= m <= 15,
        "leading_16_plus": lambda m: m >= 16,
        "trailing_1_5":  lambda m: -5 <= m <= -1,
        "trailing_6_10": lambda m: -10 <= m <= -6,
        "trailing_11_15":lambda m: -15 <= m <= -11,
        "trailing_16_plus": lambda m: m <= -16,
    }

    def _foul_profile_q(rows: list, q_margin_key: str,
                        q_fouls_key: str = None, q_opp_fouls_key: str = None,
                        q_fta_key: str = None) -> dict:
        """
        For each margin state, compute fouls/FTA averages and hit-rates.
        Since per-quarter fouls aren't stored in build_hist_row, we approximate
        from match-level fouls divided by 4, flagged with a note.
        """
        out = {}
        for state_label, pred in MARGIN_STATES.items():
            sub_idx = [i for i, r in enumerate(rows) if pred(r.get(q_margin_key, 0))]
            sub = [rows[i] for i in sub_idx]
            n_sub = len(sub)
            if n_sub == 0:
                out[state_label] = {"n": 0, "source_game_indexes": []}
                continue
            # Approximate Q fouls as match fouls / 4
            team_fouls = [r.get("fouls", 0) / 4 for r in sub]
            fta_vals   = [r.get("FTA", 0) / 4 for r in sub]
            out[state_label] = {
                "n":              n_sub,
                "source_game_indexes": sub_idx,
                "team_fouls_avg": round(statistics.mean(team_fouls), 2),
                "fta_avg":        round(statistics.mean(fta_vals), 2),
                "fouls_4_plus":   _entry(sum(1 for v in team_fouls if v >= 4), n_sub,
                                         [sub_idx[j] for j, v in enumerate(team_fouls) if v >= 4]),
                "fta_6_plus":     _entry(sum(1 for v in fta_vals if v >= 6), n_sub,
                                         [sub_idx[j] for j, v in enumerate(fta_vals) if v >= 6]),
                "note": "per-quarter fouls approximated as match_fouls/4 (no per-quarter fouls in history)",
            }
        return out

    foul_profile_by_margin_state = {
        "team_a": {
            "q3": _foul_profile_q(rows_a, "q3_margin"),
            "q4": _foul_profile_q(rows_a, "q4_margin"),
        },
        "team_b": {
            "q3": _foul_profile_q(rows_b, "q3_margin"),
            "q4": _foul_profile_q(rows_b, "q4_margin"),
        },
        "pooled": {
            "q3": _foul_profile_q(pooled, "q3_margin"),
            "q4": _foul_profile_q(pooled, "q4_margin"),
        },
        "h2h": {
            "q3": _foul_profile_q(h2h_rows, "q3_margin"),
            "q4": _foul_profile_q(h2h_rows, "q4_margin"),
        },
    }

    # ═══════════════════════════════════════════════════════════════════
    # §8 – Live vs history comparison blocks
    # ═══════════════════════════════════════════════════════════════════
    def _build_live_vs_history_match(rows: list, main_m: dict, side: str) -> dict:
        """Compare current match (H1) live stats with historical averages."""
        if not rows or not main_m:
            return {"status": "OFF"}

        if side == "home":
            pts     = safe_int(main_m.get("hs"))
            fga     = safe_int(main_m.get("hfgam"))
            fgm     = safe_int(main_m.get("hfgmm"))
            pa3     = safe_int(main_m.get("h3pam"))
            pm3     = safe_int(main_m.get("h3pmm"))
            tpa     = safe_int(main_m.get("h2pam"))
            tpm     = safe_int(main_m.get("h2pmm"))
            fta     = safe_int(main_m.get("hftam"))
            ftm     = safe_int(main_m.get("hftmm"))
            orb     = safe_int(main_m.get("horbm"))
            to_     = safe_int(main_m.get("htovm"))
            fouls   = safe_int(main_m.get("hflsm"))
        else:
            pts     = safe_int(main_m.get("as_"))
            fga     = safe_int(main_m.get("afgam"))
            fgm     = safe_int(main_m.get("afgmm"))
            pa3     = safe_int(main_m.get("a3pam"))
            pm3     = safe_int(main_m.get("a3pmm"))
            tpa     = safe_int(main_m.get("a2pam"))
            tpm     = safe_int(main_m.get("a2pmm"))
            fta     = safe_int(main_m.get("aftam"))
            ftm     = safe_int(main_m.get("aftmm"))
            orb     = safe_int(main_m.get("aorbm"))
            to_     = safe_int(main_m.get("atovm"))
            fouls   = safe_int(main_m.get("aflsm"))

        poss = fga + 0.44 * fta - orb + to_ if fga > 0 else 0
        efg  = (fgm + 0.5 * pm3) / fga if fga > 0 else 0
        tpm_calc = tpm if tpm else (fgm - pm3 if fgm and pm3 else 0)
        twop_pct = tpm_calc / tpa if tpa > 0 else 0
        threep_pct = pm3 / pa3 if pa3 > 0 else 0
        ft_pct = ftm / fta if fta > 0 else 0
        offrtg = 100 * pts / poss if poss > 0 else 0

        hist_pts   = [r["team_points"] for r in rows]
        hist_fga   = [r.get("FGA", 0) for r in rows]
        hist_poss  = [r.get("poss", 0) for r in rows if r.get("poss", 0) > 0]
        hist_2pa   = [r.get("2PA", 0) for r in rows]
        hist_3pa   = [r.get("3PA", 0) for r in rows]
        hist_fta   = [r.get("FTA", 0) for r in rows]
        hist_orb   = [r.get("ORB", 0) for r in rows]
        hist_to    = [r.get("TO", 0) for r in rows]
        hist_fouls = [r.get("fouls", 0) for r in rows]
        hist_efg   = [r.get("efg", 0) for r in rows if r.get("efg", 0) > 0]
        hist_offrtg= [r.get("off_rtg", 0) for r in rows if r.get("off_rtg", 0) > 0]

        def _pctile_rank(val, hist):
            if not hist or val is None:
                return None
            return round(sum(1 for v in hist if v <= val) / len(hist), 3)

        def _field(cur, hist):
            avg = _avg_or_none(hist)
            return {
                "current":              cur,
                "hist_avg":             avg,
                "percentile":           _pctile_rank(cur, hist),
            }

        return {
            "points":   _field(pts,   hist_pts),
            "fga":      _field(fga,   hist_fga),
            "poss":     _field(round(poss, 1), hist_poss),
            "2pa":      _field(tpa,   hist_2pa),
            "2p_pct":   _field(round(twop_pct, 3), [r.get("2PM", 0) / r.get("2PA", 1) for r in rows if r.get("2PA", 0) > 0]),
            "3pa":      _field(pa3,   hist_3pa),
            "3p_pct":   _field(round(threep_pct, 3), [r.get("3PM", 0) / r.get("3PA", 1) for r in rows if r.get("3PA", 0) > 0]),
            "fta":      _field(fta,   hist_fta),
            "ft_pct":   _field(round(ft_pct, 3), [r.get("FTM", 0) / r.get("FTA", 1) for r in rows if r.get("FTA", 0) > 0]),
            "orb":      _field(orb,   hist_orb),
            "to":       _field(to_,   hist_to),
            "fouls":    _field(fouls, hist_fouls),
            "efg":      _field(round(efg, 4), hist_efg),
            "offrtg":   _field(round(offrtg, 2), hist_offrtg),
        }

    if main:
        lvm_a = _build_live_vs_history_match(rows_a, main, "home")
        lvm_b = _build_live_vs_history_match(rows_b, main, "away")
    else:
        lvm_a = {"status": "OFF"}
        lvm_b = {"status": "OFF"}

    live_vs_history_match_stats = {"team_a": lvm_a, "team_b": lvm_b}

    # §8.2 – Live vs history current quarter stats
    def _build_live_vs_history_q(rows: list, main_m: dict, side: str,
                                  lc: dict) -> dict:
        """Build live current-quarter comparison using live_calibrated data."""
        if not rows or not main_m or not lc:
            return {"status": "OFF"}
        st   = main_m.get("st", "")
        tour = main_m.get("tour", "")
        if _is_match_finished(st):
            return {"status": "FINISHED"}

        mp   = parse_minutes_played(st, tour)
        q_dur = quarter_duration_minutes(tour)
        cur_q = min(int(mp // q_dur) + 1, 4)
        q_key = f"q{cur_q}"
        min_in_q = mp % q_dur

        if min_in_q <= 0:
            return {"status": "OFF", "reason": "quarter_not_started"}

        # Current Q points (H1 only for Q1/Q2)
        if side == "home":
            if cur_q == 1:
                cur_q_pts = safe_int(main_m.get("q1h"))
            elif cur_q == 2:
                cur_q_pts = safe_int(main_m.get("q2h"))
            else:
                cur_q_pts = None  # not available mid-game for Q3/Q4
        else:
            if cur_q == 1:
                cur_q_pts = safe_int(main_m.get("q1a"))
            elif cur_q == 2:
                cur_q_pts = safe_int(main_m.get("q2a"))
            else:
                cur_q_pts = None

        team_key = f"{q_key}_team"
        hist_q_pts = [r.get(team_key, 0) for r in rows]
        avg_q_pts  = _avg_or_none(hist_q_pts)

        projected_q_pts = None
        if cur_q_pts is not None and min_in_q > 0:
            rate_per_min = cur_q_pts / min_in_q
            projected_q_pts = round(rate_per_min * q_dur, 1)

        def _pctile_rank(val, hist):
            if not hist or val is None:
                return None
            return round(sum(1 for v in hist if v <= val) / len(hist), 3)

        result = {
            "quarter":             f"Q{cur_q}",
            "minutes_played_in_q": round(min_in_q, 1),
            "q_points_current":    cur_q_pts,
            "q_points_projected":  projected_q_pts,
            "q_points_hist_avg":   avg_q_pts,
            "q_points_percentile": _pctile_rank(cur_q_pts, hist_q_pts),
        }

        # Also add FGA pace projection if data available
        lc_side = lc.get("home" if side == "home" else "away", {})
        rem_fga = lc_side.get("Rem_FGA")
        if rem_fga is not None:
            hist_fga = [r.get("FGA", 0) / 2 for r in rows]  # approx half-game FGA
            result["q_fga_projected"]   = round(rem_fga, 1)
            result["q_fga_hist_avg"]    = _avg_or_none(hist_fga)
            result["q_fga_percentile"]  = _pctile_rank(rem_fga, hist_fga)

        return result

    lc_block = live_calibrated or {}
    if main:
        lvhq_a = _build_live_vs_history_q(rows_a, main, "home", lc_block)
        lvhq_b = _build_live_vs_history_q(rows_b, main, "away", lc_block)
    else:
        lvhq_a = {"status": "OFF"}
        lvhq_b = {"status": "OFF"}

    live_vs_history_current_quarter_stats = {"team_a": lvhq_a, "team_b": lvhq_b}

    # ═══════════════════════════════════════════════════════════════════
    # §11 – Live required threshold comparison
    # ═══════════════════════════════════════════════════════════════════
    def _build_live_required_threshold(
        rows_team: list, rows_opp: list,
        main_m: dict, lc: dict, side: str,
        lines: dict,
    ) -> list:
        """
        For each relevant BK line, build the live threshold comparison block.
        """
        if not main_m:
            return []
        st   = main_m.get("st", "")
        tour = main_m.get("tour", "")
        if _is_match_finished(st):
            return []

        mp    = parse_minutes_played(st, tour)
        q_dur = quarter_duration_minutes(tour)
        cur_q = min(int(mp // q_dur) + 1, 4)
        min_in_q = mp % q_dur
        min_left_q = max(0.0, q_dur - min_in_q)

        if side == "home":
            if cur_q == 1:   cur_q_pts = safe_int(main_m.get("q1h"))
            elif cur_q == 2: cur_q_pts = safe_int(main_m.get("q2h"))
            else:            cur_q_pts = None
        else:
            if cur_q == 1:   cur_q_pts = safe_int(main_m.get("q1a"))
            elif cur_q == 2: cur_q_pts = safe_int(main_m.get("q2a"))
            else:            cur_q_pts = None

        # Live calibrated final projection for this side
        lc_side = lc.get("home" if side == "home" else "away", {}) if lc else {}
        lc_final = lc_side.get("LiveCalibrated")

        # Use Q IT lines for threshold comparison
        q_it_lines = QUARTER_IT_LINES

        results = []
        q_key = f"q{cur_q}_team"
        for line in q_it_lines:
            target = int(line) + 1
            cur_pts_safe = cur_q_pts or 0
            need_remaining = max(0, target - cur_pts_safe)
            req_ppm = round(need_remaining / min_left_q, 3) if min_left_q > 0 else None

            # Historical rates for this Q
            hist_q_t_plus = [i for i, r in enumerate(rows_team) if r.get(q_key, 0) >= target]
            hist_q_opp    = [i for i, r in enumerate(rows_opp)
                             if r.get(f"q{cur_q}_opp", 0) >= target]  # opp allowed
            # at_least_one_q_X+ from team perspective
            alq_idx = [i for i, r in enumerate(rows_team)
                       if max(r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]) >= target]
            opp_alq_idx = [i for i, r in enumerate(rows_opp)
                           if max(r["q1_opp"], r["q2_opp"], r["q3_opp"], r["q4_opp"]) >= target]

            # projection edge
            edge = round(lc_final - line, 2) if lc_final is not None else None

            results.append({
                "market":         f"Q{cur_q}_TEAM_{side.upper()}_OVER_{line}",
                "line":           line,
                "target":         target,
                "current_q_points": cur_pts_safe,
                "need_remaining": need_remaining,
                "minutes_left_in_q": round(min_left_q, 1),
                "required_ppm":   req_ppm,
                f"historical_q{cur_q}_team_{target}_plus": _entry(len(hist_q_t_plus), len(rows_team), hist_q_t_plus),
                f"historical_q{cur_q}_opp_allowed_{target}_plus": _entry(len(hist_q_opp), len(rows_opp), hist_q_opp),
                "at_least_one_quarter_x_plus":          _entry(len(alq_idx), len(rows_team), alq_idx),
                "opponent_allowed_at_least_one_q_x_plus": _entry(len(opp_alq_idx), len(rows_opp), opp_alq_idx),
                "current_live_projection": lc_final,
                "projection_edge_to_line": edge,
            })
        return results

    if main:
        lrt_a = _build_live_required_threshold(rows_a, rows_b, main, lc_block, "home", lines_data or {})
        lrt_b = _build_live_required_threshold(rows_b, rows_a, main, lc_block, "away", lines_data or {})
    else:
        lrt_a, lrt_b = [], []

    live_required_threshold_comparison = {"team_a": lrt_a, "team_b": lrt_b}

    # ═══════════════════════════════════════════════════════════════════
    # Metadata
    # ═══════════════════════════════════════════════════════════════════
    # Resolve metadata trust fields: prefer pooled if ON, else team_a if ON, else use OFF fallback
    _sdc_a = season_date_context["team_a"]
    _sdc_p = season_date_context["pooled"]
    _sdc_resolved = _sdc_p if _sdc_p.get("status") == "ON" else _sdc_a
    _sdc_status = "ON" if _sdc_resolved.get("status") == "ON" else "OFF"
    if _sdc_status == "OFF":
        _meta_trust_level      = "unknown"
        _meta_trust_multiplier = 1.0
        _meta_trust_note_ua    = (
            "DATE CONTEXT OFF — у файлі немає дат матчів, довіру до історії неможливо оцінити "
            "по сезону/паузах. Використовуй патерн-зони як орієнтир, але без підтвердження "
            "live/stat не грай."
        )
    else:
        _meta_trust_level      = _sdc_resolved.get("history_trust_level", "unknown")
        _meta_trust_multiplier = _sdc_resolved.get("history_trust_multiplier", 1.0)
        _meta_trust_note_ua    = _sdc_resolved.get("history_trust_note_ua", "")

    meta_block = {
        "team_a_n":  len(rows_a),
        "team_b_n":  len(rows_b),
        "pooled_n":  len(pooled),
        "h2h_n":     len(h2h_rows),
        "current_match_excluded": True,
        "technical_games_excluded": True,
        "max_last_n": 35,
        "history_trust_level":      _meta_trust_level,
        "history_trust_multiplier": _meta_trust_multiplier,
        "history_trust_note_ua":    _meta_trust_note_ua,
        "season_date_context_status": _sdc_status,
    }

    return {
        "season_date_context": season_date_context,
        "metadata": meta_block,
        "team_quarter_points_patterns":   team_quarter_points_patterns,
        "match_quarter_total_patterns":   match_quarter_total_patterns,
        "quarter_spread_patterns":        quarter_spread_patterns,
        "quarter_sweep_profile":          quarter_sweep_profile,
        "historical_stat_averages":       historical_stat_averages,
        "historical_stat_threshold_hit_rates": historical_stat_threshold_hit_rates,
        "foul_profile_by_margin_state":   foul_profile_by_margin_state,
        "live_vs_history_match_stats":    live_vs_history_match_stats,
        "live_vs_history_current_quarter_stats": live_vs_history_current_quarter_stats,
        "live_required_threshold_comparison": live_required_threshold_comparison,
        "verification_note_ua": (
            "Усі значення пораховані з історії матчів. "
            "Кожен hit можна перевірити через source_game_indexes у raw_data. "
            "Поточний матч і технічні 20:0/0:20 виключені."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# ТЗ ДОРОБКА: derived_live_stats / derived_pre_match_projection /
#             derived_live_projection / derived_live_edge / line_profiles
# ═══════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────
# §2  derived_live_stats
# ─────────────────────────────────────────────
def compute_derived_live_stats(main: dict) -> dict:
    """
    Похідна live-статистика поточного матчу.
    Поля: Poss, eFG, FTr, ExtraPoss, OffRtg, FGA/FTA/ORB/TO per min.
    Якщо поля немає — null (не вигадувати).
    """
    tour = main.get("tour", "")
    st   = main.get("st", "")
    min_played = parse_minutes_played(st, tour)

    def _team_block(pts_key, fga_key, fgm_key, pm3_key,
                    fta_key, orb_key, to_key):
        pts = safe_int(main.get(pts_key))
        fga = safe_int(main.get(fga_key))
        fgm = safe_int(main.get(fgm_key))
        pm3 = safe_int(main.get(pm3_key))
        fta = safe_int(main.get(fta_key))
        orb = safe_int(main.get(orb_key))
        to_ = safe_int(main.get(to_key))

        poss      = round(fga + 0.44 * fta - orb + to_, 2) if fga else None
        efg       = round((fgm + 0.5 * pm3) / fga, 4)      if fga else None
        ftr       = round(fta / fga, 4)                      if fga else None
        extra     = (orb - to_)                              if fga else None
        off_rtg   = round(100 * pts / poss, 2) if (poss and poss > 0) else None

        mp = min_played if min_played and min_played > 0 else None
        fga_pm  = round(fga / mp, 3)  if (mp and fga)  else None
        fta_pm  = round(fta / mp, 3)  if (mp and fta)  else None
        orb_pm  = round(orb / mp, 3)  if (mp and orb)  else None
        to_pm   = round(to_ / mp, 3)  if (mp and to_)  else None

        return {
            "Poss":        poss,
            "eFG":         efg,
            "FTr":         ftr,
            "ExtraPoss":   extra,
            "OffRtg":      off_rtg,
            "FGA_per_min": fga_pm,
            "FTA_per_min": fta_pm,
            "ORB_per_min": orb_pm,
            "TO_per_min":  to_pm,
        }

    ta = _team_block("hs",  "hfgam", "hfgmm", "h3pmm", "hftam", "horbm", "htovm")
    tb = _team_block("as_", "afgam", "afgmm", "a3pmm", "aftam", "aorbm", "atovm")

    def _add(a, b):
        if a is None or b is None:
            return None
        return round(a + b, 2)

    mt = {
        "FGA_total":       _add(safe_int(main.get("hfgam")), safe_int(main.get("afgam"))),
        "FTA_total":       _add(safe_int(main.get("hftam")), safe_int(main.get("aftam"))),
        "ORB_total":       _add(safe_int(main.get("horbm")), safe_int(main.get("aorbm"))),
        "TO_total":        _add(safe_int(main.get("htovm")), safe_int(main.get("atovm"))),
        "Poss_total":      _add(ta["Poss"], tb["Poss"]),
        "eFG_total":       round((safe_int(main.get("hfgmm")) + safe_int(main.get("afgmm")) + 0.5 * (safe_int(main.get("h3pmm")) + safe_int(main.get("a3pmm")))) / (safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))), 4) if (safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))) else None,
        "FTr_total":       round((safe_int(main.get("hftam")) + safe_int(main.get("aftam"))) / (safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))), 4) if (safe_int(main.get("hfgam")) + safe_int(main.get("afgam"))) else None,
        "ExtraPoss_total": _add(ta["ExtraPoss"], tb["ExtraPoss"]),
    }

    return {
        "team_a":      ta,
        "team_b":      tb,
        "match_total": mt,
    }


# ─────────────────────────────────────────────
# §3  derived_pre_match_projection
# ─────────────────────────────────────────────
def compute_derived_pre_match_projection(rows_a: list, rows_b: list,
                                          pre_match_stat: dict) -> dict:
    """
    Pre-match projection (simple scored/allowed blend).
    team_a_pre = 0.60 * team_a_scored_avg + 0.40 * team_b_allowed_avg
    team_b_pre = 0.60 * team_b_scored_avg + 0.40 * team_a_allowed_avg
    Також додає PreFinal з pre_match_stat якщо є (shot_stat_model).
    """
    if not rows_a or not rows_b:
        return {
            "method": "simple_scored_allowed",
            "team_a_pre": None, "team_b_pre": None,
            "match_total_pre": None, "pre_match_margin_team_a": None,
            "shot_model_status": "OFF",
            "warning": "insufficient_history",
        }

    def _avg(rows, key):
        vals = [r.get(key, 0) for r in rows if r.get(key) is not None]
        return statistics.mean(vals) if vals else 0.0

    ta_scored_avg  = _avg(rows_a, "team_points")
    tb_scored_avg  = _avg(rows_b, "team_points")
    ta_allowed_avg = _avg(rows_a, "opp_points")
    tb_allowed_avg = _avg(rows_b, "opp_points")

    ta_pre = round(0.60 * ta_scored_avg + 0.40 * tb_allowed_avg, 2)
    tb_pre = round(0.60 * tb_scored_avg + 0.40 * ta_allowed_avg, 2)
    total_pre  = round(ta_pre + tb_pre, 2)
    margin_pre = round(ta_pre - tb_pre, 2)

    # shot_stat_model з pre_match_stat якщо доступний
    shot_status = "OFF"
    shot_ta, shot_tb, shot_total = None, None, None
    if pre_match_stat and pre_match_stat.get("status") != "NO_DATA":
        pf_ta = pre_match_stat.get("team_a", {})
        pf_tb = pre_match_stat.get("team_b", {})
        if pf_ta.get("PreFinal") and pf_tb.get("PreFinal"):
            shot_ta    = pf_ta["PreFinal"]
            shot_tb    = pf_tb["PreFinal"]
            shot_total = round(shot_ta + shot_tb, 2)
            shot_status = "ON"

    return {
        "method": "simple_scored_allowed",
        "team_a_pre":              ta_pre,
        "team_b_pre":              tb_pre,
        "match_total_pre":         total_pre,
        "pre_match_margin_team_a": margin_pre,
        "shot_model_status":       shot_status,
        "shot_model_team_a":       shot_ta,
        "shot_model_team_b":       shot_tb,
        "shot_model_total":        shot_total,
        "warning": None,
    }


# ─────────────────────────────────────────────
# §4  derived_live_projection
# ─────────────────────────────────────────────
def compute_derived_live_projection(main: dict,
                                     pre_proj: dict,
                                     live_calibrated: dict) -> dict:
    """
    Live projection.
    simple_total_projection = current_total / min_played * 40
    stat_adjusted — з live_calibrated якщо доступний.
    """
    tour = main.get("tour", "")
    st   = main.get("st", "")
    q_dur = quarter_duration_minutes(tour)

    if _is_match_finished(st):
        return {
            "projection_status":               "OFF_FINISHED",
            "simple_total_projection":         None,
            "stat_adjusted_total_projection":  None,
            "used_total_projection":           None,
            "team_a_simple_projection":        None,
            "team_b_simple_projection":        None,
            "team_a_stat_adjusted_projection": None,
            "team_b_stat_adjusted_projection": None,
            "team_a_used_projection":          None,
            "team_b_used_projection":          None,
            "live_trust":                      None,
            "projection_conflict":             False,
            "projection_choice_reason":        "match_finished",
        }

    mp = parse_minutes_played(st, tour)
    ml = minutes_left(mp, tour)
    total_reg = float(4 * q_dur)

    if mp is None or ml is None:
        return {
            "projection_status": "OFF_NO_TIME",
            "simple_total_projection": None,
            "stat_adjusted_total_projection": None,
            "used_total_projection": None,
            "team_a_simple_projection": None,
            "team_b_simple_projection": None,
            "team_a_stat_adjusted_projection": None,
            "team_b_stat_adjusted_projection": None,
            "team_a_used_projection": None,
            "team_b_used_projection": None,
            "live_trust": None,
            "projection_conflict": False,
            "projection_choice_reason": "no_time_data",
        }

    hs  = safe_int(main.get("hs"))
    aws = safe_int(main.get("as_"))
    current_total = hs + aws

    # Simple pace projection
    if mp > 0:
        simple_total = round(current_total / mp * total_reg, 2)
        simple_ta    = round(hs  / mp * total_reg, 2)
        simple_tb    = round(aws / mp * total_reg, 2)
    else:
        simple_total = None
        simple_ta    = None
        simple_tb    = None

    # Stat-adjusted (з live_calibrated)
    lc_status = live_calibrated.get("status", "") if live_calibrated else ""
    lc_valid  = lc_status not in ("FINISHED", "NO_DATA", "")

    stat_adj_total = None
    stat_adj_ta    = None
    stat_adj_tb    = None
    live_trust_val = None

    if lc_valid and live_calibrated:
        lc_home = live_calibrated.get("home", {})
        lc_away = live_calibrated.get("away", {})
        stat_adj_ta = lc_home.get("LiveCalibrated")
        stat_adj_tb = lc_away.get("LiveCalibrated")
        if stat_adj_ta is not None and stat_adj_tb is not None:
            stat_adj_total = round(stat_adj_ta + stat_adj_tb, 2)
        live_trust_val = lc_home.get("LiveTrust")

    # Вибір використовуваного проекту
    if stat_adj_total is not None and simple_total is not None:
        conflict = abs(stat_adj_total - simple_total) > 12
        if conflict:
            # Conservative: closer to pre-match
            pre_total = pre_proj.get("match_total_pre") if pre_proj else None
            if pre_total:
                d_stat   = abs(stat_adj_total - pre_total)
                d_simple = abs(simple_total   - pre_total)
                used_total = stat_adj_total if d_stat <= d_simple else simple_total
                used_ta    = stat_adj_ta    if d_stat <= d_simple else simple_ta
                used_tb    = stat_adj_tb    if d_stat <= d_simple else simple_tb
                reason = "conflict"
            else:
                used_total = stat_adj_total
                used_ta    = stat_adj_ta
                used_tb    = stat_adj_tb
                reason = "conservative"
        else:
            used_total = stat_adj_total
            used_ta    = stat_adj_ta
            used_tb    = stat_adj_tb
            reason = "aggressive_allowed"
            conflict = False
    elif stat_adj_total is not None:
        used_total = stat_adj_total
        used_ta    = stat_adj_ta
        used_tb    = stat_adj_tb
        reason = "stat_only"
        conflict = False
    else:
        used_total = simple_total
        used_ta    = simple_ta
        used_tb    = simple_tb
        reason = "simple_only"
        conflict = False

    proj_status = "ON" if used_total is not None else "OFF_STAT_SUPPORT"
    if stat_adj_total is None and simple_total is not None:
        proj_status = "ON"  # simple pace доступний

    return {
        "projection_status":               proj_status,
        "simple_total_projection":         simple_total,
        "stat_adjusted_total_projection":  stat_adj_total,
        "used_total_projection":           used_total,
        "team_a_simple_projection":        simple_ta,
        "team_b_simple_projection":        simple_tb,
        "team_a_stat_adjusted_projection": stat_adj_ta,
        "team_b_stat_adjusted_projection": stat_adj_tb,
        "team_a_used_projection":          used_ta,
        "team_b_used_projection":          used_tb,
        "live_trust":                      live_trust_val,
        "projection_conflict":             conflict if stat_adj_total and simple_total else False,
        "projection_choice_reason":        reason,
    }


# ─────────────────────────────────────────────
# §5  derived_live_edge
# ─────────────────────────────────────────────
def compute_derived_live_edge(pre_proj: dict, live_proj: dict) -> dict:
    """
    live_edge_to_prematch = used_total_projection - match_total_pre
    """
    pre_total  = pre_proj.get("match_total_pre")  if pre_proj  else None
    used_total = live_proj.get("used_total_projection") if live_proj else None
    used_ta    = live_proj.get("team_a_used_projection") if live_proj else None
    used_tb    = live_proj.get("team_b_used_projection") if live_proj else None
    pre_ta     = pre_proj.get("team_a_pre")  if pre_proj else None
    pre_tb     = pre_proj.get("team_b_pre")  if pre_proj else None
    pre_margin = pre_proj.get("pre_match_margin_team_a") if pre_proj else None

    def _edge(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 2)

    edge_total = _edge(used_total, pre_total)
    edge_ta    = _edge(used_ta, pre_ta)
    edge_tb    = _edge(used_tb, pre_tb)
    proj_margin = _edge(used_ta, used_tb)
    margin_edge = _edge(proj_margin, pre_margin)

    def _direction(e):
        if e is None:
            return "NEUTRAL"
        if e > 2:
            return "OVER"
        if e < -2:
            return "UNDER"
        return "NEUTRAL"

    def _edge_status(e):
        if e is None:
            return "NO_EDGE"
        a = abs(e)
        if a >= 12:  return "VERY_STRONG_EDGE"
        if a >= 8:   return "STRONG_EDGE"
        if a >= 5:   return "PLAY_EDGE"
        if a >= 2.5: return "SMALL_EDGE"
        return "NO_EDGE"

    return {
        "match_total": {
            "pre_match_projection":  pre_total,
            "live_used_projection":  used_total,
            "live_edge_to_prematch": edge_total,
            "direction":             _direction(edge_total),
            "edge_status":           _edge_status(edge_total),
        },
        "team_a_it": {
            "pre_match_projection":  pre_ta,
            "live_used_projection":  used_ta,
            "live_edge_to_prematch": edge_ta,
        },
        "team_b_it": {
            "pre_match_projection":  pre_tb,
            "live_used_projection":  used_tb,
            "live_edge_to_prematch": edge_tb,
        },
        "margin": {
            "pre_match_margin":       pre_margin,
            "live_projected_margin":  proj_margin,
            "margin_edge_to_prematch": margin_edge,
        },
    }


# ─────────────────────────────────────────────
# §6  line_profiles  (головний блок)
# ─────────────────────────────────────────────

def _line_profile_success_games(rows: list, market: str,
                                 line: float, side: str,
                                 team: str = "both") -> list:
    """
    Повернути рядки, де умова проходу виконана.
    market: 'match_total' | 'team_it_a' | 'team_it_b' | 'spread_a' | 'spread_b'
    side: 'OVER' | 'UNDER'
    """
    result = []
    for r in rows:
        if market == "match_total":
            val = r.get("total", 0)
            hit = (val > line) if side == "OVER" else (val < line)
        elif market == "team_it_a":
            val = r.get("team_points", 0)
            hit = (val > line) if side == "OVER" else (val < line)
        elif market == "team_it_b":
            val = r.get("opp_points", 0)
            hit = (val > line) if side == "OVER" else (val < line)
        elif market in ("spread_a", "spread_b"):
            margin = r.get("margin", 0)
            hit = (margin + line > 0) if side == "OVER" else (margin + line < 0)
        else:
            continue
        if hit:
            result.append(r)
    return result


def _stat_profile_from_rows(rows: list) -> dict:
    """Побудувати stat profile (min/avg/median/p25/p75/max) для набору рядків."""
    if not rows:
        return {}

    def _d(key):
        vals = [r.get(key, 0) for r in rows if r.get(key) is not None and r.get(key, 0) > 0]
        if not vals:
            return {"min": 0, "avg": 0, "median": 0, "p25": 0, "p75": 0, "max": 0}
        s = sorted(vals)
        n = len(s)
        def _p(p):
            k = (n - 1) * p / 100
            lo, hi = math.floor(k), math.ceil(k)
            return round(s[lo] * (hi - k) + s[hi] * (k - lo), 2) if lo != hi else round(s[int(k)], 2)
        return {
            "min":    round(min(vals), 2),
            "avg":    round(sum(vals) / n, 2),
            "median": round(_p(50), 2),
            "p25":    _p(25),
            "p75":    _p(75),
            "max":    round(max(vals), 2),
        }

    def _avg2(key):
        vals = [r.get(key, 0) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    # Підрахунок FGA = 2PA + 3PA для рядків де FGA відсутній
    def _fga(r):
        fga = r.get("FGA", 0)
        if not fga:
            fga = r.get("2PA", 0) + r.get("3PA", 0)
        return fga

    def _poss(r):
        poss = r.get("poss", 0)
        if not poss:
            fga = _fga(r)
            fta = r.get("FTA", 0)
            orb = r.get("ORB", 0)
            to_ = r.get("TO", 0)
            poss = fga + 0.44 * fta - orb + to_ if fga else 0
        return poss

    fga_vals  = [_fga(r) for r in rows if _fga(r) > 0]
    poss_vals = [_poss(r) for r in rows if _poss(r) > 0]

    n = len(rows)

    def _field_d(vals):
        if not vals:
            return {"min": 0, "avg": 0, "median": 0, "p25": 0, "p75": 0}
        s = sorted(vals)
        nn = len(s)
        def _pv(p):
            k = (nn - 1) * p / 100
            lo, hi = math.floor(k), math.ceil(k)
            return round(s[lo] * (hi - k) + s[hi] * (k - lo), 2) if lo != hi else round(s[int(k)], 2)
        return {
            "min": round(s[0], 2), "avg": round(sum(vals) / nn, 2),
            "median": _pv(50), "p25": _pv(25), "p75": _pv(75),
        }

    total_vals = [r.get("total", 0) for r in rows]

    # Match-total rядки мають sum-поля; для team_it рядків вони також присутні
    fga_t_vals  = [r.get("FGA", 0) + r.get("FGA", 0) for r in rows]  # placeholder
    # Правильно: total FGA = обидві команди; у build_hist_row зберігається тільки свій FGA.
    # Використовуємо r["FGA"] як "команда" FGA, не total match FGA.

    return {
        "success_n":   n,
        "success_rate": round(n / max(n, 1), 3),  # заповниться caller'ом
        "final_total": _d("total"),
        "FGA_total":   _field_d(fga_vals),
        "Poss_total":  _field_d(poss_vals),
        "2PA_total":   {"min": _avg2("2PA"), "avg": _avg2("2PA")},
        "2P_pct":      {"min": 0, "avg": round(
            sum(r.get("2PM", 0) for r in rows) / max(sum(r.get("2PA", 1) for r in rows), 1), 4)},
        "3PA_total":   {"min": _avg2("3PA"), "avg": _avg2("3PA")},
        "3P_pct":      {"min": 0, "avg": round(
            sum(r.get("3PM", 0) for r in rows) / max(sum(r.get("3PA", 1) for r in rows), 1), 4)},
        "FTA_total":   {"min": _avg2("FTA"), "avg": _avg2("FTA")},
        "fouls_total": {"min": _avg2("fouls"), "avg": _avg2("fouls")},
        "ORB_total":   {"min": _avg2("ORB"), "avg": _avg2("ORB")},
        "TO_total":    {"min": _avg2("TO"), "avg": _avg2("TO")},
        "eFG":         {"min": 0, "avg": round(statistics.mean(
            [r.get("efg", 0) for r in rows if r.get("efg", 0) > 0]) if any(
            r.get("efg", 0) > 0 for r in rows) else 0, 4)},
        "FTr":         {"min": 0, "avg": round(statistics.mean(
            [r.get("ftr", 0) for r in rows if r.get("ftr", 0) > 0]) if any(
            r.get("ftr", 0) > 0 for r in rows) else 0, 4)},
        "ExtraPoss":   {"min": _avg2("extra_poss"), "avg": _avg2("extra_poss")},
    }


def _build_success_stat_profile(success_rows: list, total_n: int) -> dict:
    p = _stat_profile_from_rows(success_rows)
    if not p:
        return {"success_n": 0, "success_rate": 0.0}
    p["success_n"]   = len(success_rows)
    p["success_rate"] = round(len(success_rows) / max(total_n, 1), 3)
    return p


def _build_fail_stat_profile(fail_rows: list, total_n: int) -> dict:
    if not fail_rows:
        return {"fail_n": 0, "fail_rate": 0.0, "main_fail_reasons": []}

    def _avg(key):
        vals = [r.get(key, 0) for r in fail_rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    def _med(key):
        vals = sorted([r.get(key, 0) for r in fail_rows if r.get(key) is not None])
        if not vals:
            return 0
        mid = len(vals) // 2
        return round(vals[mid], 2)

    def _max_val(key):
        vals = [r.get(key, 0) for r in fail_rows if r.get(key) is not None]
        return round(max(vals), 2) if vals else 0

    # Визначення причин провалу
    fga_thresh = 58   # нижче p25 зазвичай
    fta_thresh = 12
    efg_thresh = 0.48
    to_thresh  = 14

    fail_reasons = []
    avg_fga = _avg("FGA")
    avg_fta = _avg("FTA")
    avg_efg = _avg("efg") if any(r.get("efg", 0) > 0 for r in fail_rows) else 0
    avg_to  = _avg("TO")
    avg_orb = _avg("ORB")

    if avg_fga < fga_thresh:    fail_reasons.append("LOW_FGA")
    if avg_fta < fta_thresh:    fail_reasons.append("LOW_FTA")
    if avg_efg < efg_thresh:    fail_reasons.append("LOW_EFG")
    if avg_to  > to_thresh:     fail_reasons.append("TO_COLLAPSE")
    if avg_orb < 6:             fail_reasons.append("NO_ORB")

    return {
        "fail_n":    len(fail_rows),
        "fail_rate": round(len(fail_rows) / max(total_n, 1), 3),
        "final_total": {"avg": _avg("total"), "median": _med("total"), "max": _max_val("total")},
        "FGA_total":   {"avg": avg_fga},
        "Poss_total":  {"avg": _avg("poss")},
        "2P_pct":      {"avg": round(
            sum(r.get("2PM", 0) for r in fail_rows) / max(sum(r.get("2PA", 1) for r in fail_rows), 1), 4)},
        "3PA_total":   {"avg": _avg("3PA")},
        "3P_pct":      {"avg": round(
            sum(r.get("3PM", 0) for r in fail_rows) / max(sum(r.get("3PA", 1) for r in fail_rows), 1), 4)},
        "FTA_total":   {"avg": avg_fta},
        "fouls_total": {"avg": _avg("fouls")},
        "ORB_total":   {"avg": avg_orb},
        "TO_total":    {"avg": avg_to},
        "eFG":         {"avg": round(avg_efg, 4)},
        "main_fail_reasons": fail_reasons if fail_reasons else ["NONE_IDENTIFIED"],
    }


def _build_current_vs_success_profile(main: dict, success_profile: dict,
                                       fail_profile: dict,
                                       live_proj: dict,
                                       line: float) -> dict:
    """
    Порівняти поточний live-матч з success_stat_profile.
    """
    tour = main.get("tour", "")
    st   = main.get("st", "")
    mp   = parse_minutes_played(st, tour)
    ml   = minutes_left(mp, tour)

    hs  = safe_int(main.get("hs"))
    aws = safe_int(main.get("as_"))
    cur_total = hs + aws

    required_points = max(0, int(line) + 1 - cur_total)
    req_ppm = round(required_points / ml, 3) if ml and ml > 0 else None

    score_state = {
        "current_total":    cur_total,
        "minutes_left":     round(ml, 1) if ml is not None else None,
        "required_points":  required_points,
        "required_ppm":     req_ppm,
    }

    if not success_profile or success_profile.get("success_n", 0) == 0:
        return {
            "score_state": score_state,
            "stat_comparison": {},
            "profile_match_score": None,
            "profile_status": "NO_SUCCESS_DATA",
            "strong_points": [],
            "weak_points": [],
        }

    # Поточні live-метрики
    hfga = safe_int(main.get("hfgam"))
    afga = safe_int(main.get("afgam"))
    hfta = safe_int(main.get("hftam"))
    afta = safe_int(main.get("aftam"))
    horb = safe_int(main.get("horbm"))
    aorb = safe_int(main.get("aorbm"))
    htov = safe_int(main.get("htovm"))
    atov = safe_int(main.get("atovm"))
    hfgm = safe_int(main.get("hfgmm"))
    afgm = safe_int(main.get("afgmm"))
    h3pm = safe_int(main.get("h3pmm"))
    a3pm = safe_int(main.get("a3pmm"))

    total_fga = hfga + afga
    total_fta = hfta + afta
    total_orb = horb + aorb
    total_to  = htov + atov
    total_poss = round(total_fga + 0.44 * total_fta - total_orb + total_to, 2) if total_fga else 0
    total_fgm = hfgm + afgm
    total_3pm = h3pm + a3pm
    cur_efg   = round((total_fgm + 0.5 * total_3pm) / total_fga, 4) if total_fga else 0

    # Pace-projected до кінця матчу
    def _pace(val):
        if not mp or mp <= 0 or val <= 0:
            return 0
        total_reg = float(4 * quarter_duration_minutes(tour))
        return round(val / mp * total_reg, 2)

    cur_fga_pace  = _pace(total_fga)
    cur_poss_pace = _pace(total_poss)
    cur_fta_pace  = _pace(total_fta)
    cur_orb_pace  = _pace(total_orb)
    cur_to_pace   = _pace(total_to)

    def _status_metric(cur, s_avg, s_p25, higher_is_better=True):
        if not cur or not s_avg:
            return "unknown"
        if higher_is_better:
            if cur >= s_avg:  return "above_success_avg"
            if cur >= s_p25:  return "near_success_range"
            return "below_success_profile"
        else:
            fail_avg = fail_profile.get("TO_total", {}).get("avg", s_avg * 1.2) if fail_profile else s_avg * 1.2
            if cur <= s_avg:   return "good"
            if cur <= fail_avg: return "warning"
            return "collapse_risk"

    s_fga  = success_profile.get("FGA_total", {})
    s_poss = success_profile.get("Poss_total", {})
    s_fta  = success_profile.get("FTA_total", {})
    s_orb  = success_profile.get("ORB_total", {})
    s_to   = success_profile.get("TO_total", {})
    s_efg  = success_profile.get("eFG", {})

    stat_comp = {
        "FGA_total": {
            "current_pace": cur_fga_pace,
            "success_avg":  s_fga.get("avg", 0),
            "success_p25":  s_fga.get("p25", 0),
            "status": _status_metric(cur_fga_pace, s_fga.get("avg"), s_fga.get("p25", 0)),
        },
        "Poss_total": {
            "current_pace": cur_poss_pace,
            "success_avg":  s_poss.get("avg", 0),
            "success_p25":  s_poss.get("p25", 0),
            "status": _status_metric(cur_poss_pace, s_poss.get("avg"), s_poss.get("p25", 0)),
        },
        "FTA_total": {
            "current_pace": cur_fta_pace,
            "success_avg":  s_fta.get("avg", 0),
            "success_p25":  s_fta.get("p25", 0),
            "status": _status_metric(cur_fta_pace, s_fta.get("avg"), s_fta.get("p25", 0)),
        },
        "ORB_total": {
            "current_pace": cur_orb_pace,
            "success_avg":  s_orb.get("avg", 0),
            "success_p25":  s_orb.get("p25", 0),
            "status": _status_metric(cur_orb_pace, s_orb.get("avg"), s_orb.get("p25", 0)),
        },
        "TO_total": {
            "current_pace": cur_to_pace,
            "success_avg":  s_to.get("avg", 0),
            "fail_avg":     fail_profile.get("TO_total", {}).get("avg", 0) if fail_profile else 0,
            "status": _status_metric(cur_to_pace, s_to.get("avg"), None, higher_is_better=False),
        },
        "eFG": {
            "current":     cur_efg,
            "success_avg": s_efg.get("avg", 0),
            "status": (
                "above" if cur_efg >= s_efg.get("avg", 0)
                else "near" if cur_efg >= s_efg.get("avg", 0) * 0.93
                else "below"
            ) if s_efg.get("avg") else "unknown",
        },
    }

    # profile_match_score
    positive_statuses = {"above_success_avg", "near_success_range", "good", "above", "near"}
    total_checks = len(stat_comp)
    matched_pos  = sum(1 for v in stat_comp.values() if v.get("status") in positive_statuses)
    pms = round(matched_pos / total_checks, 3) if total_checks else 0

    if pms >= 0.70:   profile_status = "MATCH"
    elif pms >= 0.40: profile_status = "PARTIAL"
    else:             profile_status = "CONFLICT"

    strong_pts = [k for k, v in stat_comp.items() if v.get("status") in {"above_success_avg", "above", "good"}]
    weak_pts   = [k for k, v in stat_comp.items() if v.get("status") in {"below_success_profile", "below", "collapse_risk"}]

    return {
        "score_state":        score_state,
        "stat_comparison":    stat_comp,
        "profile_match_score": pms,
        "profile_status":     profile_status,
        "strong_points":      strong_pts,
        "weak_points":        weak_pts,
    }


def _build_line_projection_edge(line: float,
                                 pre_proj: dict,
                                 live_proj: dict,
                                 main: dict) -> dict:
    """
    line_edge_over = live_used_projection - bookmaker_line
    """
    pre_total  = pre_proj.get("match_total_pre")  if pre_proj  else None
    live_total = live_proj.get("used_total_projection") if live_proj else None

    edge_over  = round(live_total - line, 2) if live_total is not None else None
    edge_under = round(line - live_total, 2) if live_total is not None else None

    tour = main.get("tour", "")
    st   = main.get("st", "")
    mp   = parse_minutes_played(st, tour)
    ml   = minutes_left(mp, tour)

    hs  = safe_int(main.get("hs"))
    aws = safe_int(main.get("as_"))
    cur_total = hs + aws

    target_over = int(line) + 1
    req_over_pts = max(0, target_over - cur_total)
    req_under_pts = max(0, int(line) - cur_total)
    req_ppm_over  = round(req_over_pts / ml, 3) if ml and ml > 0 else None

    return {
        "pre_match_projection":    pre_total,
        "live_used_projection":    live_total,
        "bookmaker_line":          line,
        "live_edge_to_prematch":   round(live_total - pre_total, 2) if (live_total and pre_total) else None,
        "line_edge_over":          edge_over,
        "line_edge_under":         edge_under,
        "required_over_points":    req_over_pts,
        "required_under_max_points": req_under_pts,
        "required_ppm_over":       req_ppm_over,
    }


def _build_line_history(rows_a: list, rows_b: list, h2h_rows: list,
                         market: str, line: float, side: str) -> dict:
    """
    Exact hit-rate по конкретній лінії.
    H2H окремо — не змішувати з pooled.
    smoothed = (hits + 1) / (n + 2)
    """
    def _count(rows, mkt, ln, sd):
        hits = 0
        for r in rows:
            if mkt == "match_total":
                val = r.get("total", 0)
            elif mkt == "team_it_a":
                val = r.get("team_points", 0)
            elif mkt == "team_it_b":
                val = r.get("opp_points", 0)  # perspective: team B scored from their hist rows
            elif mkt in ("spread_a", "spread_b"):
                val = r.get("margin", 0) + ln  # cover if > 0
                hits += 1 if ((sd == "OVER" and val > 0) or (sd == "UNDER" and val < 0)) else 0
                continue
            else:
                val = r.get("total", 0)
            if sd == "OVER":
                hits += 1 if val > ln else 0
            else:
                hits += 1 if val < ln else 0
        return hits

    na = len(rows_a)
    nb = len(rows_b)
    nh = len(h2h_rows)
    np = na + nb

    ha = _count(rows_a, market, line, side)
    hb = _count(rows_b, market, line, side)
    hp = ha + hb
    hh = _count(h2h_rows, market, line, side)

    def _sm(hits, n): return round((hits + 1) / (n + 2), 3) if n >= 0 else None
    def _rt(hits, n): return round(hits / n, 3) if n > 0 else None

    return {
        "team_a_hits":     ha,   "team_a_n":       na,
        "team_a_rate":     _rt(ha, na), "team_a_smoothed": _sm(ha, na),
        "team_b_hits":     hb,   "team_b_n":       nb,
        "team_b_rate":     _rt(hb, nb), "team_b_smoothed": _sm(hb, nb),
        "pooled_hits":     hp,   "pooled_n":       np,
        "pooled_rate":     _rt(hp, np), "pooled_smoothed": _sm(hp, np),
        "h2h_hits":        hh,   "h2h_n":          nh,
        "h2h_rate":        _rt(hh, nh), "h2h_smoothed":  _sm(hh, nh),
    }


def compute_line_profiles(
    rows_a: list,
    rows_b: list,
    h2h_rows: list,
    main: dict,
    lines_data: dict,
    pre_proj: dict,
    live_proj: dict,
    user_live_lines: dict = None,
) -> list:
    """
    Для кожної реальної БК-лінії та user_live_lines будує повний профіль.
    Повертає список об'єктів line_profiles.
    """
    profiles = []
    pooled = rows_a + rows_b

    user_live_lines = user_live_lines or {}

    # ── Збираємо всі лінії для профілювання ──────────────────────────
    entries_to_profile = []

    raw_lines = lines_data or {}

    # match_total
    for e in raw_lines.get("match_total", []):
        scope = e.get("scope", "Match")
        if scope != "Match":
            continue
        line_val = e.get("line")
        if line_val is None:
            continue
        bk = e.get("bookmaker") or e.get("source", "")
        for side in ("OVER", "UNDER"):
            entries_to_profile.append({
                "market": "match_total", "scope": scope,
                "bookmaker": bk, "line": float(line_val), "side": side,
                "over_odd":  e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
            })

    # team_it
    for e in raw_lines.get("team_it", []):
        line_val = e.get("line")
        if line_val is None:
            continue
        team_key = e.get("team", "").lower()
        market = "team_it_a" if team_key in ("home", "a", "team_a") else "team_it_b"
        bk = e.get("bookmaker") or e.get("source", "")
        for side in ("OVER", "UNDER"):
            entries_to_profile.append({
                "market": market, "scope": "Match",
                "bookmaker": bk, "line": float(line_val), "side": side,
                "team": e.get("team"), "team_name": e.get("team_name", ""),
                "over_odd":  e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
            })

    # quarter_total
    for e in raw_lines.get("quarter_total", []):
        line_val = e.get("line")
        scope    = e.get("scope", "")
        if line_val is None:
            continue
        bk = e.get("bookmaker") or e.get("source", "")
        for side in ("OVER", "UNDER"):
            entries_to_profile.append({
                "market": "quarter_total", "scope": scope,
                "bookmaker": bk, "line": float(line_val), "side": side,
                "over_odd":  e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
            })

    # half_total
    for e in raw_lines.get("half_total", []):
        line_val = e.get("line")
        scope    = e.get("scope", "H1")
        if line_val is None:
            continue
        bk = e.get("bookmaker") or e.get("source", "")
        for side in ("OVER", "UNDER"):
            entries_to_profile.append({
                "market": "half_total", "scope": scope,
                "bookmaker": bk, "line": float(line_val), "side": side,
                "over_odd":  e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
            })

    # user_live_lines
    for market_key, ulines in user_live_lines.items():
        for ul in (ulines or []):
            line_val = ul.get("line")
            if line_val is None:
                continue
            side = ul.get("side", "OVER").upper()
            entries_to_profile.append({
                "market": market_key, "scope": ul.get("scope", "Match"),
                "bookmaker": "user_live", "line": float(line_val), "side": side,
                "is_user_line": True,
            })

    # ── Дедуплікація ─────────────────────────────────────────────────
    seen = set()
    unique_entries = []
    for e in entries_to_profile:
        key = (e["market"], e.get("scope", ""), e["line"], e["side"])
        if key not in seen:
            seen.add(key)
            unique_entries.append(e)

    # ── Для кожного entry будуємо профіль ────────────────────────────
    for entry in unique_entries:
        market   = entry["market"]
        line_val = entry["line"]
        side     = entry["side"]
        scope    = entry.get("scope", "Match")
        bk       = entry.get("bookmaker", "")

        # Вибір рядків по scope
        if scope in ("H2", "Q3", "Q4"):
            # Для часткових ліній використовуємо відповідні поля
            def _scoped_rows(rows, sc):
                result = []
                for r in rows:
                    new_r = dict(r)
                    if sc == "H2":
                        new_r["total"] = r.get("h2_total", r.get("total", 0))
                        new_r["team_points"] = r.get("h2_team", r.get("team_points", 0))
                        new_r["opp_points"]  = r.get("h2_opp",  r.get("opp_points", 0))
                    elif sc == "Q3":
                        new_r["total"] = r.get("q3_total", 0)
                        new_r["team_points"] = r.get("q3_team", 0)
                        new_r["opp_points"]  = r.get("q3_opp", 0)
                    elif sc == "Q4":
                        new_r["total"] = r.get("q4_total", 0)
                        new_r["team_points"] = r.get("q4_team", 0)
                        new_r["opp_points"]  = r.get("q4_opp", 0)
                    result.append(new_r)
                return result
            rows_a_s   = _scoped_rows(rows_a, scope)
            rows_b_s   = _scoped_rows(rows_b, scope)
            h2h_rows_s = _scoped_rows(h2h_rows, scope)
        else:
            rows_a_s   = rows_a
            rows_b_s   = rows_b
            h2h_rows_s = h2h_rows

        pooled_s = rows_a_s + rows_b_s
        total_n  = len(pooled_s)

        # line_history
        lh = _build_line_history(rows_a_s, rows_b_s, h2h_rows_s,
                                  market, line_val, side)

        # success / fail rows (pooled)
        success_rows = _line_profile_success_games(pooled_s, market, line_val, side)
        fail_rows    = [r for r in pooled_s if r not in success_rows]

        # profiles
        succ_prof = _build_success_stat_profile(success_rows, total_n)
        fail_prof = _build_fail_stat_profile(fail_rows, total_n)

        # current_vs_success (тільки для match_total з поточними live-stats)
        if market in ("match_total",) and scope == "Match":
            cvs = _build_current_vs_success_profile(
                main, succ_prof, fail_prof, live_proj, line_val)
        else:
            cvs = {"note": f"current_vs_success not computed for market={market} scope={scope}"}

        # line_projection_edge
        lpe = _build_line_projection_edge(line_val, pre_proj, live_proj, main)

        _LINE_TYPE = {
            "match_total":  "MATCH_TOTAL | both teams | full match | line ~130-230",
            "team_it_a":    "INDIVIDUAL_TOTAL | home team only | full match | line ~55-115",
            "team_it_b":    "INDIVIDUAL_TOTAL | away team only | full match | line ~55-115",
            "quarter_total": f"QUARTER_TOTAL | both teams | {scope} only | line ~30-60",
            "half_total":    f"HALF_TOTAL | both teams | {scope} only | line ~60-115",
        }
        profile_obj = {
            "market":    market,
            "_type":     _LINE_TYPE.get(market, f"{market} | scope={scope}"),
            "scope":     scope,
            "bookmaker": bk,
            "line":      line_val,
            "side":      side,
        }
        if entry.get("over_odd"):  profile_obj["over_odd"]  = entry["over_odd"]
        if entry.get("under_odd"): profile_obj["under_odd"] = entry["under_odd"]
        if entry.get("team"):      profile_obj["team"]       = entry["team"]
        if entry.get("team_name"): profile_obj["team_name"]  = entry["team_name"]
        if entry.get("is_user_line"): profile_obj["is_user_line"] = True

        profile_obj["line_history"]              = lh
        profile_obj["success_stat_profile"]      = succ_prof
        profile_obj["fail_stat_profile"]         = fail_prof
        profile_obj["current_vs_success_profile"] = cvs
        profile_obj["line_projection_edge"]      = lpe

        profiles.append(profile_obj)

    return profiles


# ─────────────────────────────────────────────
# RESULT MERGER — map process_match output → result.json skeleton
# ─────────────────────────────────────────────
def enrich_raw_game(rec: dict) -> dict:
    """
    Принимает одну сырую запись из team_a_hist / team_b_hist / h2h_hist
    и возвращает её же с добавленными читаемыми ключами вида <key>__label.
    Оригинальные ключи не переименовываются — только добавляются новые.
    """

    # ── Словари составляющих ключа ──────────────────────────────────────
    SIDE = {"h": "Хозяева", "a": "Гости"}

    STAT = {
        "fga":  "броски с игры (попытки)",
        "fgm":  "броски с игры (попадания)",
        "2pa":  "2-очковые (попытки)",
        "2pm":  "2-очковые (попадания)",
        "3pa":  "3-очковые (попытки)",
        "3pm":  "3-очковые (попадания)",
        "fta":  "штрафные (попытки)",
        "ftm":  "штрафные (попадания)",
        "rb":   "подборы (всего)",
        "orb":  "подборы (атакующие)",
        "drb":  "подборы (защитные)",
        "ast":  "передачи",
        "stl":  "перехваты",
        "blk":  "блоки",
        "tov":  "потери",
        "fls":  "фолы",
    }

    PERIOD = {
        "m":  "матч",
        "1":  "Q1",
        "2":  "Q2",
        "3":  "Q3",
        "4":  "Q4",
    }

    # ── Паттерн: (h|a)(stat)(m|1|2|3|4)
    # Поддерживаемые статы отсортированы по убыванию длины, чтобы
    # «orb» не «съедал» префикс «rb» и т.д.
    STAT_KEYS_SORTED = sorted(STAT.keys(), key=len, reverse=True)
    import re as _re
    STAT_PATTERN = "|".join(_re.escape(s) for s in STAT_KEYS_SORTED)
    _RX = _re.compile(
        r'^(?P<side>[ha])(?P<stat>' + STAT_PATTERN + r')(?P<period>[m1234])$',
        _re.IGNORECASE,
    )

    result = dict(rec)  # копия, чтобы не мутировать оригинал

    for key in list(rec.keys()):
        m = _RX.match(key)
        if not m:
            continue
        side_ru   = SIDE.get(m.group("side").lower(), m.group("side"))
        stat_ru   = STAT.get(m.group("stat").lower(), m.group("stat"))
        period_ru = PERIOD.get(m.group("period").lower(), m.group("period"))
        result[key + "__label"] = f"{side_ru}: {stat_ru} — {period_ru}"

    return result


def build_result_json(match_result: dict, lines_data: dict = None,
                      parsed: dict = None, raw_block: list = None) -> dict:
    """
    Maps process_match() output → result.json.
    Key order optimised for LLM consumption:
      1.  schema_version
      2.  match           (новый блок: id, stage, period, score-объект, quarters, series_context)
      3.  data_quality    (на верхнем уровне)
      4.  bookmaker_lines (match_total, team_it, quarter_total + все старые ключи)
      5.  live_team_stats (новый блок из live_calibrated)
      6.  projections     (новый блок: pre_match_stat / live_calibrated / segment)
      7.  history_by_exact_line   (новый блок)
      8.  scenario_patterns_by_line (новый блок)
      9.  line_evaluations         (новый блок)
      --- старые блоки сохраняются ---
      10. meta
      11. score_state
      12. final_verdict
      13. markets_evaluation
      14. stat_conditioned_line_profiles
      15. scenario_zones
      16. checkpoint_matrices
      17. quarter_result_profile
      18. stat_alignment
      19. history_zones / stat_zones
      20. live_boxscore
      21. raw_data
    """
    r = match_result  # shorthand

    # ── meta (старый) ─────────────────────────────────────────────────────
    meta = r.get("meta", {})

    # ── scenario, stat_support, live_calibrated ──────────────────────────
    scenario     = r.get("scenario", {})
    stat_support = r.get("stat_support", {})
    lc           = r.get("live_calibrated", {})
    lc_home      = lc.get("home", {})
    lc_away      = lc.get("away", {})

    # ── live_calibrated validity guard ───────────────────────────────────
    _lc_status      = lc.get("status", "")
    _lc_is_valid    = _lc_status not in ("FINISHED", "NO_DATA")
    _lines_stale_w  = r.get("lines_stale_warning")  # may be None

    # ── data_quality (верхний уровень, новая структура) ───────────────────
    sg = r.get("sample_gate", {})
    stat_sp = r.get("stat_support", {})
    home_n = sg.get("team_a_valid_games") or sg.get("team_a_n") or sg.get("home_n")
    away_n = sg.get("team_b_valid_games") or sg.get("team_b_n") or sg.get("away_n")
    pooled_n = None
    if home_n is not None and away_n is not None:
        try:
            pooled_n = int(home_n) + int(away_n)
        except (TypeError, ValueError):
            pooled_n = None
    h2h_n = sg.get("h2h_n")
    # NOTE: data_quality is rebuilt at the bottom of this function from raw sources.
    # This intermediate variable is kept only so intermediate blocks that reference
    # `data_quality` (e.g. data_quality_legacy) do not break. It is NOT emitted.
    data_quality = {
        "stat_support": "ON" if stat_sp.get("status") == "ON" else "OFF",
        "required_fields_present": stat_sp.get("required_fields", []),
        "missing_fields": stat_sp.get("missing_fields", []),
        "samples": {
            "home_last_games_valid": home_n,
            "away_last_games_valid": away_n,
            "pooled_valid": pooled_n,
            "h2h_valid": h2h_n,
        },
        "sample_warning": sg.get("warning") or (
            f"pooled{pooled_n}, not full pooled70" if pooled_n and pooled_n < 70 else None
        ),
        "current_match_excluded": True,
        "technical_20_0_excluded": True,
        "lines_stale_warning": _lines_stale_w,
        "sample_gate": sg,
        "sample_strength": r.get("sample_strength"),
    }

    # ── bookmaker_lines (новая структура + старые ключи) ──────────────────
    raw_lines = lines_data if lines_data else {}

    def _enrich_match_total(entries):
        result = []
        for i, e in enumerate(entries):
            line_val = e.get("line")
            result.append({
                "id": f"mt_{str(line_val).replace('.', '_')}_{i}",
                "market": "match_total",
                "_type": "MATCH_TOTAL | both teams | full match | line ~130-230",
                "scope": e.get("scope", "Match"),
                "line": line_val,
                "over_odd": e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
                "source": e.get("bookmaker") or e.get("source", ""),
                "is_real_bookmaker_line": True,
                **e,  # сохраняем все оригинальные поля
            })
        return result

    def _enrich_team_it(entries):
        result = []
        for i, e in enumerate(entries):
            line_val = e.get("line")
            team = e.get("team", "")
            result.append({
                "id": f"{team}_it_{str(line_val).replace('.', '_')}_{i}",
                "market": "team_it",
                "_type": "INDIVIDUAL_TOTAL | one team only | full match | line ~55-115",
                "team": team,
                "team_name": e.get("team_name") or e.get("teamName", ""),
                "line": line_val,
                "over_odd": e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
                "source": e.get("bookmaker") or e.get("source", ""),
                "is_real_bookmaker_line": True,
                **e,
            })
        return result

    def _enrich_quarter_total(entries):
        result = []
        for i, e in enumerate(entries):
            line_val = e.get("line")
            scope = e.get("scope", "")
            result.append({
                "id": f"{scope.lower()}_total_{str(line_val).replace('.', '_')}_{i}",
                "market": "quarter_total",
                "_type": f"QUARTER_TOTAL | both teams | {scope} only | line ~30-60",
                "scope": scope,
                "line": line_val,
                "over_odd": e.get("overOdd") or e.get("over_odd"),
                "under_odd": e.get("underOdd") or e.get("under_odd"),
                "source": e.get("bookmaker") or e.get("source", ""),
                "is_real_bookmaker_line": True,
                **e,
            })
        return result

    bookmaker_lines = {
        "_schema": {
            "_readme": "Кожен масив описує окремий тип ставки. scope вказує на який ігровий відрізок поширюється ставка: \"Match\"=весь матч, \"H1\"=перша половина(Q1+Q2), \"H2\"=друга половина(Q3+Q4), \"Q1\"/\"Q2\"/\"Q3\"/\"Q4\"=конкретна чверть. НІКОЛИ не використовуй дані з quarter_* для аналізу матчу в цілому і навпаки. Всі поля *Odd (homeOdd, awayOdd, overOdd, underOdd, homeHcpOdd, awayHcpOdd, yesOdd, noOdd) — це десяткові коефіцієнти букмекера: число на яке множиться сума ставки у разі виграшу (наприклад 1.83 означає прибуток 83% від суми ставки).",
            "match_1x2": "Переможець ВСЬОГО матчу. scope завжди \"Match\". homeOdd/awayOdd.",
            "match_handicap": "Азіатський гандикап на ВЕСЬ матч. scope=\"Match\". handicap<0 = фора хазяїв (вони фаворити). homeHcpOdd/awayHcpOdd.",
            "match_total": "Тотал ВСЬОГО матчу (сума очок обох команд за всі чверті). scope=\"Match\". line=межа (типово 130–230), overOdd/underOdd. НЕ ПЛУТАТИ з home_ind_total/away_ind_total — тут СУМА обох команд, не окрема.",
            "half_total": "Тотал за ПОЛОВИНУ матчу (2 чверті). scope=\"H1\" або \"H2\". line=межа, overOdd/underOdd.",
            "quarter_total": "Тотал ТІЛЬКИ за одну чверть. scope=\"Q1\"/\"Q2\"/\"Q3\"/\"Q4\". НЕ ПЛУТАТИ з match_total — це набагато менше очок (30-60, не 130+). line=межа, overOdd/underOdd.",
            "quarter_dnb": "\"Нічия без ставки\" за одну чверть. scope=Q1-Q4. При нічиї в чверті — ставка повертається. homeOdd/awayOdd.",
            "quarter_1x2": "Переможець ОДНІЄЇ чверті. scope=Q1-Q4. homeOdd/drawOdd/awayOdd. drawOdd може бути null.",
            "quarter_btts": "Чи наберуть ОБИДВІ команди мінімум threshold очок у чверті. scope=Q1-Q4. yesOdd/noOdd.",
            "quarter_race": "\"Гонка до N очок\" — хто ПЕРШИМ набере target очок у конкретному ігровому відрізку. scope=Q1-Q4 або Match. target=кількість очок. homeOdd/awayOdd. Це НЕ тотал і НЕ переможець.",
            "home_ind_total": "Індивідуальний тотал ТІЛЬКИ команди-хазяїна за ВЕСЬ матч. scope=\"Match\". line=межа (типово 55–115), overOdd/underOdd. КРИТИЧНО: це очки ОДНІЄЇ команди, НЕ сума двох. home_ind_total + away_ind_total ≈ match_total. Якщо бачиш line~65-100 — це ind_total, а НЕ match_total.",
            "away_ind_total": "Індивідуальний тотал ТІЛЬКИ команди-гостей за ВЕСЬ матч. scope=\"Match\". line=межа (типово 55–115), overOdd/underOdd. КРИТИЧНО: це очки ОДНІЄЇ команди, НЕ сума двох. home_ind_total + away_ind_total ≈ match_total. Якщо бачиш line~65-100 — це ind_total, а НЕ match_total.",
            "team_it": "Індивідуальний тотал команди (уніфікований масив для home+away). Поле team вказує яка команда: \"home\" або \"away\". line=межа, overOdd/underOdd. Аналогічні правила що й home_ind_total/away_ind_total.",
            "win_margin": "Різниця очок переможця матчу (bucket-ставка). label=діапазон типу \"1-5\" або \"6-10\". odd=коефіцієнт.",
            "last_digit": "Остання цифра рахунку або сума останніх цифр обох команд. scope=Match/H1/H2. Екзотичний ринок.",
            "_disambiguation": {
                "match_total_vs_ind_total": "match_total.line зазвичай 130–230 (сума двох команд). home_ind_total.line та away_ind_total.line зазвичай 55–115 (очки ОДНІЄЇ команди). Якщо line < 120 і scope=Match — швидше за все ind_total, а не match_total. Ніколи не порівнюй line з match_total і line з ind_total як одне й те саме число.",
                "quarter_total_vs_match_total": "quarter_total.line зазвичай 30–60. match_total.line зазвичай 130–230. Різниця ~4x. scope у quarter_total завжди Q1/Q2/Q3/Q4, у match_total завжди Match.",
                "team_it_vs_match_total": "team_it / home_ind_total / away_ind_total описують очки ОДНІЄЇ команди. match_total описує суму очок ОБОХ команд. Не підсумовуй ind_total як match_total і не ділити match_total на 2 як ind_total — команди набирають різну кількість очок."
            }
        },
        "real_lines_only": True,
        # новые категоризированные ключи
        "match_total":   _enrich_match_total(raw_lines.get("match_total", [])),
        "team_it":       _enrich_team_it(raw_lines.get("team_it", [])),
        "quarter_total": _enrich_quarter_total(raw_lines.get("quarter_total", [])),
        # старые ключи сохраняются
        "half_total":     raw_lines.get("half_total", []),
        "match_handicap": raw_lines.get("match_handicap", []),
        "match_1x2":      raw_lines.get("match_1x2", []),
        "other":          raw_lines.get("other", []),
        # 3PT-маркети: потрібні для extract_bk_3pt_total_lines / extract_bk_3pt_handicap_lines
        # Парсер шукає саме ці ключі — без них 3PT total/handicap = null (bug)
        "home_ind_total": [
            {**e, "_type": "INDIVIDUAL_TOTAL | home team only | full match | line ~55-115"}
            for e in raw_lines.get("home_ind_total", [])
        ],
        "away_ind_total": [
            {**e, "_type": "INDIVIDUAL_TOTAL | away team only | full match | line ~55-115"}
            for e in raw_lines.get("away_ind_total", [])
        ],
    }

    # ── новый блок: match ─────────────────────────────────────────────────
    score_str = meta.get("score", "")
    home_pts, away_pts = None, None
    if score_str and "-" in str(score_str):
        parts = str(score_str).split("-")
        try:
            home_pts = int(parts[0])
            away_pts = int(parts[1])
        except (ValueError, IndexError):
            pass
    total_pts = (home_pts + away_pts) if (home_pts is not None and away_pts is not None) else None
    margin_home = (home_pts - away_pts) if (home_pts is not None and away_pts is not None) else None

    q1_str = meta.get("q1", "")
    q2_str = meta.get("q2", "")

    def _parse_q(s):
        if s and "-" in str(s):
            parts = str(s).split("-")
            try:
                h, a = int(parts[0]), int(parts[1])
                return {"home": h, "away": a, "total": h + a}
            except (ValueError, IndexError):
                pass
        return {"home": None, "away": None, "total": None}

    q1_data = _parse_q(q1_str)
    q2_data = _parse_q(q2_str)

    # period и минуты — берём из live_calibrated (там уже посчитано)
    _lc_min_played = lc.get("min_played")
    _lc_min_left   = lc.get("min_left")
    _q_dur = lc.get("q_duration_min") or 10
    _stage = meta.get("stage", "")

    # определяем номер периода из min_played
    def _period_from_min(mp, q_dur):
        if mp is None:
            return None
        try:
            mp = float(mp)
            q_dur = float(q_dur) or 10.0
            q = int(mp // q_dur) + 1
            return min(q, 4)
        except (TypeError, ValueError):
            return None

    _period_num = _period_from_min(_lc_min_played, _q_dur)
    _period_min_played = None
    _period_min_left   = None
    if _period_num is not None and _lc_min_played is not None:
        try:
            _period_min_played = round(float(_lc_min_played) % float(_q_dur), 1)
            _period_min_left   = round(float(_q_dur) - _period_min_played, 1)
        except (TypeError, ValueError):
            pass

    match_block = {
        "id":   meta.get("match_id"),
        "name": meta.get("match"),
        "stage": _stage,
        "period": _period_num,
        "period_minute_played": _period_min_played,
        "period_minute_left": _period_min_left,
        "match_minute_played": _lc_min_played,
        "match_minute_left": _lc_min_left,
        "score": {
            "home":        home_pts,
            "away":        away_pts,
            "total":       total_pts,
            "margin_home": margin_home,
        },
        "quarters": {
            "q1": q1_data,
            "q2": q2_data,
            "q3_live": {"home": None, "away": None, "total": None},
        },
        "series_context": {
            "is_playoff":    None,
            "series_score":  meta.get("tournament"),
            "elimination":   None,
            "closeout":      None,
            "must_win":      None,
            "motivation_gate": None,
        },
        # старые поля
        "tournament":    meta.get("tournament"),
        "date":          meta.get("date"),
        "url":           meta.get("url"),
        "h1_total":      meta.get("h1_total"),
        "current_total": meta.get("current_total"),
    }

    # ── новый блок: live_team_stats (из live_calibrated) ──────────────────
    stat_sp_data = stat_support

    def _live_profile(efg, extra_poss, ftr):
        """Определить live_profile по показателям."""
        efg = efg or 0.0
        extra_poss = extra_poss or 0.0
        ftr = ftr or 0.0
        if efg >= 0.55 and extra_poss >= 0:
            return "REAL_HIGH"
        if efg < 0.40 and extra_poss < 0 and ftr == 0:
            return "COLLAPSE_SUPPRESSION"
        if efg < 0.45:
            return "LOW_EFFICIENCY"
        return "NORMAL"

    def _build_live_team(lc_side, side_key, team_name):
        pts_raw = lc_side.get("LiveRaw")
        fga = stat_sp_data.get(f"{side_key}_fga") or stat_sp_data.get(f"team_{side_key[0]}_fga")
        efg = lc_side.get("eFG_live") or 0.0
        ftr = lc_side.get("FTr_live") or 0.0
        extra_poss = lc_side.get("ExtraPoss_live") or 0.0
        poss = lc_side.get("Poss_live") or 0.0
        # Pull detailed box-score stats from stat_support (team_a_1h / team_b_1h)
        # These keys mirror what process_match stores from the live main-match record
        _1h_key = "team_a_1h" if side_key == "home" else "team_b_1h"
        _1h = stat_sp_data.get(_1h_key, {})
        # safe_int() ensures all count stats are int (not raw JSON strings),
        # which is critical for arithmetic: 3PM_home + 3PM_away must be int addition.
        # safe_int returns 0 for missing/None — use None-guarded version below.
        def _si(v):
            return safe_int(v) if v not in (None, "", "null") else None
        _fga_raw = fga or _1h.get("FGA")
        # Compute 2PA from FGA - 3PA if not directly available (parser may omit h2pam/a2pam)
        _3pa = _si(_1h.get("3PA"))
        _fga = _si(_fga_raw)
        _2pa = _si(_1h.get("2PA"))
        if _2pa is None and _fga is not None and _3pa is not None:
            _2pa = _fga - _3pa
        _3pm = _si(_1h.get("3PM"))
        _fgm = _si(_1h.get("FGM"))
        _2pm = _si(_1h.get("2PM"))
        if _2pm is None and _fgm is not None and _3pm is not None:
            _2pm = _fgm - _3pm
        return {
            "team_name":  team_name,
            "points":     pts_raw,
            "FGA":        _fga,
            "FGM":        _fgm,
            "2PA":        _2pa,
            "2PM":        _2pm,
            "3PA":        _3pa,
            "3PM":        _3pm,
            "FTA":        _si(_1h.get("FTA")),
            "FTM":        _si(_1h.get("FTM")),
            "ORB":        _si(_1h.get("ORB")),
            "DRB":        _si(_1h.get("DRB")),
            "TO":         _si(_1h.get("TO")),
            "fouls":      _si(_1h.get("FOULS")),
            "Poss":       poss if poss else None,
            "eFG":        efg if efg else None,
            "FTr":        ftr if ftr else None,
            "ExtraPoss":  extra_poss if extra_poss else None,
            "OffRtg":     None,
            "live_profile": _live_profile(efg, extra_poss, ftr),
        }

    home_name = meta.get("match", "").split(" vs ")[0].strip() if " vs " in str(meta.get("match", "")) else "Home"
    away_name = meta.get("match", "").split(" vs ")[1].strip() if " vs " in str(meta.get("match", "")) else "Away"

    live_team_stats = {
        "home": _build_live_team(lc_home, "home", home_name),
        "away": _build_live_team(lc_away, "away", away_name),
    }

    # ── новый блок: projections ───────────────────────────────────────────
    proj = r.get("projection", {})
    pre  = r.get("pre_match_stat", {})
    proj_match = proj.get("match", {})
    proj_q3    = proj.get("q3", {})
    projections = {
        "pre_match_stat": {
            "home_final": pre.get("team_a", {}).get("PreFinal"),
            "away_final": pre.get("team_b", {}).get("PreFinal"),
            "total":      pre.get("PreFinal_Total"),
        },
        "live_calibrated": {
            "valid":      _lc_is_valid,
            "status":     _lc_status if not _lc_is_valid else "OK",
            "home_final": lc_home.get("LiveCalibrated") if _lc_is_valid else None,
            "away_final": lc_away.get("LiveCalibrated") if _lc_is_valid else None,
            "total":      lc.get("LiveCalibrated_Total") if _lc_is_valid else None,
            "note":       lc.get("note") if not _lc_is_valid else None,
        },
        "segment_projection": {
            "home_final":  proj_match.get("team_a_final_center"),
            "away_final":  proj_match.get("team_b_final_center"),
            "total":       proj_match.get("final_total_center"),
            "range_low":   proj_match.get("low"),
            "range_high":  proj_match.get("high"),
            "total_std":   proj_match.get("total_std"),
        },
        "projection_priority": {
            "main_for_p_live":    "segment_projection",
            "secondary":          "live_calibrated" if _lc_is_valid else "DISABLED_FINISHED",
            "raw_simple_pace":    "informational_only",
        },
        "lines_stale_warning": _lines_stale_w,
        # полный объект projection сохраняється
        "q3": proj_q3,
        "thresholds": r.get("thresholds", {}),
    }

    # ── новый блок: history_by_exact_line ─────────────────────────────────
    hz = r.get("history_zones", {})
    history_by_exact_line = {
        "match_total":   hz.get("match_total", {}),
        "team_it":       hz.get("team_it_match", {}),
        "quarter_total": hz.get("q3_total", {}),
        # 1H total: динамически рассчитан по реальным BK-линиям из half_total.
        # Каждый ключ = строковое значение линии (напр. "86.5").
        # Внутри: team_a, team_b, pooled70, h2h с реальными under/over hits.
        # P_hist_exact = "OFF" ТОЛЬКО если физически нет матчей команды.
        "half_total":    hz.get("half_total", {}),
    }

    # ── новый блок: scenario_patterns_by_line ─────────────────────────────
    sc_zones = scenario.get("team_a") or {}
    scenario_patterns_by_line = {
        "team_a": sc_zones,
        "team_b": scenario.get("team_b") or {},
    }

    # ── новый блок: line_evaluations ─────────────────────────────────────
    candidates = r.get("candidates", [])
    line_evaluations = []
    for cand in candidates:
        line_evaluations.append({
            "line_id":               cand.get("line_id") or cand.get("id"),
            "is_real_bookmaker_line": True,
            "market":     cand.get("market"),
            "team":       cand.get("team"),
            "team_name":  cand.get("team_name"),
            "line":       cand.get("line"),
            "threshold":  cand.get("threshold", {}),
            "history":    cand.get("history", {}),
            "scenario":   cand.get("scenario", {}),
            "live_projection": cand.get("live_projection", {}),
            "weights":    cand.get("weights", {}),
            "formula":    cand.get("formula", {}),
            "caps_blockers": cand.get("caps_blockers", []),
            "final":      cand.get("final_verdict") or cand.get("final", {}),
        })

    # ── старый data_quality (для обратной совместимости) ──────────────────
    data_quality_legacy = {
        "sample_gate": sg,
        "sample_strength": r.get("sample_strength"),
        "stat_support": {
            "status":         stat_sp.get("status"),
            "missing_fields": stat_sp.get("missing_fields", []),
        },
        "live_stat_support": r.get("live_stat_support", {}),
        "blockers":          r.get("blockers", []),
        "final_signal":      r.get("final_signal"),
    }

    # ── score_state (старый) ──────────────────────────────────────────────
    score_state = {
        "score":                meta.get("score"),
        "q1":                   meta.get("q1"),
        "q2":                   meta.get("q2"),
        "h1_total":             meta.get("h1_total"),
        "total_at_ht":          meta.get("total_at_ht"),
        "margin_team_a":        meta.get("margin_team_a"),
        "current_margin_bucket":    scenario.get("current_margin_bucket"),
        "current_quarter_state":    scenario.get("current_quarter_state"),
        "current_ht_total_bucket":  scenario.get("current_ht_total_bucket"),
    }

    # ── live_boxscore (старый) ────────────────────────────────────────────
    live_boxscore = {
        "team_a_1h": stat_support.get("team_a_1h", {}),
        "team_b_1h": stat_support.get("team_b_1h", {}),
        "ht_profile":             stat_support.get("ht_profile", {}),
        "live_override_score_a":  stat_support.get("live_override_score_a", {}),
        "live_override_score_b":  stat_support.get("live_override_score_b", {}),
    }

    # ── stat_zones (старый) ───────────────────────────────────────────────
    stat_zones = r.get("stat_zones", {})

    # ── stat_conditioned_line_profiles (старый) ───────────────────────────
    stat_conditioned_line_profiles = {
        "pre_match_stat":  pre,
        "live_calibrated": lc,
        "projection":      proj,
        "thresholds":      r.get("thresholds", {}),
    }

    # ── scenario_zones (старый) ───────────────────────────────────────────
    scenario_zones = {
        "team_a": scenario.get("team_a", {}),
        "team_b": scenario.get("team_b", {}),
    }

    # ── checkpoint_matrices (старый) ──────────────────────────────────────
    checkpoint_matrices = {
        "team_any_quarter_threshold": r.get("team_any_quarter_threshold", {}),
        "opponent_allowed_any_quarter_threshold": r.get("opponent_allowed_any_quarter_threshold", {}),
        "anti_sweep": r.get("anti_sweep", {}),
        "strong_patterns": r.get("strong_patterns", []),
    }

    # ── quarter_result_profile (старый) ───────────────────────────────────
    quarter_result_profile = {
        "q1_total":  hz.get("q1_total", {}),
        "q2_total":  hz.get("q2_total", {}),
        "q3_total":  hz.get("q3_total", {}),
        "q4_total":  hz.get("q4_total", {}),
        "team_it_q3": hz.get("team_it_q3", {}),
        "at_least_one_quarter_a": hz.get("at_least_one_quarter_a", {}),
        "at_least_one_quarter_b": hz.get("at_least_one_quarter_b", {}),
    }

    # ── stat_alignment (старый) ───────────────────────────────────────────
    stat_alignment = {
        "stat_support_full": {k: v for k, v in stat_support.items()
                               if k not in ("team_a_1h", "team_b_1h",
                                            "ht_profile", "live_override_score_a",
                                            "live_override_score_b")},
        "live_stat_support": r.get("live_stat_support", {}),
        "spread_match_team_a": hz.get("spread_match_team_a", []),
        "spread_match_team_b": hz.get("spread_match_team_b", []),
        "spread_q3_team_a":    hz.get("spread_q3_team_a", []),
        "allowed_points":      hz.get("allowed_points", {}),
        "team_it_match":       hz.get("team_it_match", {}),
        "match_total":         hz.get("match_total", {}),
    }

    # ── markets_evaluation (старый) ───────────────────────────────────────
    markets_evaluation = candidates

    # ── final_verdict (старый) ────────────────────────────────────────────
    final_verdict = {
        "final_signal":  r.get("final_signal"),
        "blockers":      r.get("blockers", []),
        "sample_strength": r.get("sample_strength"),
        "top_candidates": candidates[:5],
        "projection_summary": {
            "match_total_center": proj_match.get("final_total_center"),
            "match_total_range":  [proj_match.get("low"), proj_match.get("high")],
            "q3_total_center": proj_q3.get("total_center"),
            "live_calibrated_total": lc.get("LiveCalibrated_Total") if _lc_is_valid else None,
            "live_calibrated_valid": _lc_is_valid,
            "pre_final_total": pre.get("PreFinal_Total"),
            "lines_stale_warning": _lines_stale_w,
        },
        "stat_support_status": stat_sp.get("status"),
        "live_stat_verdict":   r.get("live_stat_support", {}).get("verdict"),
        "ht_profile": stat_support.get("ht_profile", {}),
        "anti_sweep_signal_a": r.get("anti_sweep", {}).get("team_a", {}).get("signal"),
        "anti_sweep_signal_b": r.get("anti_sweep", {}).get("team_b", {}).get("signal"),
    }

    # ── raw_data (старый) ─────────────────────────────────────────────────
    raw_data = {}
    if parsed:
        raw_data["main_match"]  = parsed.get("main_match")
        raw_data["team_a_hist"] = [enrich_raw_game(r) for r in parsed.get("team_a_hist", [])]
        raw_data["team_b_hist"] = [enrich_raw_game(r) for r in parsed.get("team_b_hist", [])]
        raw_data["h2h_hist"]    = [enrich_raw_game(r) for r in parsed.get("h2h_hist", [])]
    if raw_block:
        raw_data["raw_block"]   = raw_block

    # ── ТЗ §11: Build strict top-level data_quality (calculator mode) ───
    _hz = r.get("history_zones", {})
    _sg = r.get("sample_gate", {})
    _stat_sp = r.get("stat_support", {})
    _top_data_quality = {
        "team_a_valid_games":       _sg.get("team_a_valid_games", 0),
        "team_b_valid_games":       _sg.get("team_b_valid_games", 0),
        "pooled_valid_games":       _sg.get("pooled_n", 0),
        "h2h_valid_games":          _sg.get("h2h_n", 0),
        "current_match_excluded":   True,
        "technical_games_excluded": True,
        "missing_fields":           _stat_sp.get("missing_fields", []),
        "sample_warning":           _sg.get("warning"),
        "lines_stale_warning":      _lines_stale_w,
    }

    # ── ТЗ §11: Build strict history_zones (no data_quality/cond/summary nested) ──
    _history_zones_clean = {
        "h1_total":             _hz.get("h1_total_zones", {}),
        "h1_team_points":       _hz.get("h1_team_points_zones", {}),
        "h2_total":             _hz.get("h2_total_zones", {}),
        "h2_team_points":       _hz.get("h2_team_points_zones", {}),
        "q3_total":             _hz.get("q3_total_zones", {}),
        "q3_team_points":       _hz.get("q3_team_points_zones", {}),
        "after_3q_total":       _hz.get("after3q_total_zones", {}),
        "after_3q_team_points": _hz.get("after3q_team_points_zones", {}),
        "q4_total":             _hz.get("q4_total_zones", {}),
        "q4_team_points":       _hz.get("q4_team_points_zones", {}),
    }

    # ── ТЗ §11: conditional_history_zones and zone_summary at top level ──
    _top_conditional_history_zones = _hz.get("conditional_history_zones", {})
    _top_zone_summary = _hz.get("zone_summary", {})

    return {
        # ── ТЗ §11: Required top-level keys ─────────────────────────────
        "schema_version": "basketball_history_zones_v1.1",

        # ── meta — match identity & live state ───────────────────────────
        "meta": {
            "match_id":   meta.get("match_id"),
            "match":      meta.get("match"),
            "tournament": meta.get("tournament"),
            "date":       meta.get("date"),
            "url":        meta.get("url"),
            "stage":      meta.get("stage"),
            "score":      meta.get("score"),
            "q1":         meta.get("q1"),
            "q2":         meta.get("q2"),
            "h1_total":   meta.get("h1_total"),
            "current_total": meta.get("current_total"),
            # ── Minute tracking ─────────────────────────────────────────
            "quarter_min_played": meta.get("quarter_min_played"),
            "quarter_min_left":   meta.get("quarter_min_left"),
            "total_min_played":   meta.get("total_min_played"),
            "total_min_left":     meta.get("total_min_left"),
            # ── Idle period ──────────────────────────────────────────────
            "idle_period": meta.get("idle_period") or r.get("idle_period"),
            # ── Season boundary ──────────────────────────────────────────
            "last_season_end_dt": meta.get("last_season_end_dt"),
            "days_since_season_end": meta.get("days_since_season_end"),
            "current_season_start_dt": meta.get("current_season_start_dt"),
            "days_since_season_start": meta.get("days_since_season_start"),
        },

        # ── raw_lines — сырые линии от букмекера (до обогащения) ────────────
        "raw_lines": {
            "match_total":    raw_lines.get("match_total", []),
            "team_it":        raw_lines.get("team_it", []),
            "quarter_total":  raw_lines.get("quarter_total", []),
            "half_total":     raw_lines.get("half_total", []),
            "match_handicap": raw_lines.get("match_handicap", []),
            "match_1x2":      raw_lines.get("match_1x2", []),
            "other":          raw_lines.get("other", []),
            "home_ind_total": raw_lines.get("home_ind_total", []),
            "away_ind_total": raw_lines.get("away_ind_total", []),
        },

        # ── logic — all computed analysis blocks ─────────────────────────
        "logic": {
            # ТЗ §11: data_quality
            "data_quality": _top_data_quality,

            # ТЗ §11: history_zones — zone data only, no nested blocks
            "history_zones": _history_zones_clean,

            # ТЗ §10: conditional_history_zones
            "conditional_history_zones": _top_conditional_history_zones,

            # ТЗ §11: zone_summary
            "zone_summary": _top_zone_summary,

            # ── Pre-calculated pattern blocks (ТЗ §2–11) ─────────────────
            "pre_calculated_patterns": r.get("pre_calculated_patterns", {}),

            # ── ТЗ ДОРОБКА §2–6: derived + line_profiles ─────────────────
            "derived_live_stats":            r.get("derived_live_stats", {}),
            "derived_pre_match_projection":  r.get("derived_pre_match_projection", {}),
            "derived_live_projection":       r.get("derived_live_projection", {}),
            "derived_live_edge":             r.get("derived_live_edge", {}),
            "line_profiles":                 r.get("line_profiles", []),
            "user_live_lines": {
                "match_total":   [],
                "team_it":       [],
                "spread":        [],
                "quarter_total": [],
                "half_total":    [],
            },
        },

        # ── lines — bookmaker lines (raw + enriched) ─────────────────────
        "lines": bookmaker_lines,

        # ── raw_data — source records passed into this match block ────────
        "raw_data": raw_data,

        # ── ТЗ §11: live_micro_segments (top-level, not inside logic) ────
        "live_micro_segments": {
            "status":     "OFF_NO_PLAY_BY_PLAY",
            "last_3_min": {},
            "last_5_min": {},
        },
    }


# ─────────────────────────────────────────────
# ENTRYPOINT — loop over all match blocks
# ─────────────────────────────────────────────
def run(filepath: str, lines_path: str) -> list:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Load bookmaker lines from line_result.json
    lines_data = load_lines(lines_path)

    # raw can be a list of records (one big file) or a list of match-blocks
    # Strategy: split on Source == "MAIN MATCH" boundaries
    # Each match block starts at a MAIN MATCH record

    results = []
    current_block = []

    def flush_block(block):
        if not block:
            return
        parsed = parse_records(block)
        if parsed["main_match"]:
            result = process_match(parsed, lines_data=lines_data)
            results.append((result, parsed, block))

    for rec in raw:
        if not isinstance(rec, dict):
            continue
        # Check both "src" (data rows) and "Source" (header rows)
        src = rec.get("src", "") or rec.get("Source", "") or ""
        if "MAIN MATCH" in src.upper():
            flush_block(current_block)
            current_block = [rec]
        else:
            current_block.append(rec)

    flush_block(current_block)
    return results


def _discover_files_in_data(data_dir: Path):
    """
    Auto-discover h2h_file and line_result_file inside data_dir.

    Rules:
      - line_result file : JSON whose name starts with 'line_result'
                           Supports both:
                             line_result.json          (no id)
                             line_result_<id>.json     (with id)
                           The match id is extracted from the h2h filename.
      - h2h (main) file  : most recently modified JSON in data_dir whose name
                           does NOT start with 'line_result'.

    Returns (h2h_path: str, line_result_path: str, match_id: str) or raises.
    """
    if not data_dir.is_dir():
        raise FileNotFoundError(f"data directory not found: {data_dir}")

    json_files = list(data_dir.glob("*.json"))

    line_result_path = None
    h2h_candidates = []
    match_id = "unknown"

    for f in json_files:
        name = f.name
        if name.startswith("line_result"):
            # Accept both line_result.json and line_result_<id>.json
            if line_result_path is None:
                line_result_path = str(f)
                # Try to extract id from line_result_<id>.json
                m = re.match(r'line_result_([A-Za-z0-9]+)\.json$', name)
                if m:
                    match_id = m.group(1)
        else:
            h2h_candidates.append(f)

    # Pick the most recently modified h2h file
    if h2h_candidates:
        h2h_candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        h2h_path = str(h2h_candidates[0])
    else:
        h2h_path = None

    if h2h_path is None:
        raise FileNotFoundError(
            f"No main (h2h) JSON file found in {data_dir} "
            "(expected a .json not starting with 'line_result')"
        )
    if line_result_path is None:
        raise FileNotFoundError(
            f"No line_result*.json file found in {data_dir}"
        )

    # Extract match_id from h2h filename if not yet found
    if match_id == "unknown":
        # Try full stem as id (e.g. Monako_U21_vs_Bur_U21 → use stem directly)
        stem = Path(h2h_path).stem
        match_id = stem if stem else "unknown"

    return h2h_path, line_result_path, match_id


def main():
    """
    Two invocation modes:

    1. No arguments (auto-discover):
         python math_script.py
       Looks in ./data/ for:
         - h2h file    : first *.json NOT starting with 'line_result_'
         - lines file  : first line_result_<id>.json

    2. Explicit targets:
         python math_script.py --targets <h2h_file> <line_result_file>
       Both paths must be provided explicitly.

    Legacy positional mode (two plain args) is still supported for
    backwards compatibility with existing callers:
         python math_script.py <h2h_file> <line_result_file>
    """
    args = sys.argv[1:]

    if args and args[0] == "--targets":
        # ── Explicit targets mode ────────────────────────────────────────
        if len(args) < 3:
            print(
                "Usage: math_script.py --targets <h2h_file> <line_result_file>",
                file=sys.stderr,
            )
            sys.exit(1)
        h2h_path         = args[1]
        line_result_path = args[2]

        for fpath in (h2h_path, line_result_path):
            if not os.path.isfile(fpath):
                print(f"[math_script] ERROR: file not found: {fpath}", file=sys.stderr)
                sys.exit(1)

        m = re.search(r'_([A-Za-z0-9]+)\.json$', h2h_path)
        match_id = m.group(1) if m else "unknown"

    elif len(args) >= 2 and not args[0].startswith("--"):
        # ── Legacy positional mode ───────────────────────────────────────
        h2h_path         = args[0]
        line_result_path = args[1]

        for fpath in (h2h_path, line_result_path):
            if not os.path.isfile(fpath):
                print(f"[math_script] ERROR: file not found: {fpath}", file=sys.stderr)
                sys.exit(1)

        m = re.search(r'_([A-Za-z0-9]+)\.json$', h2h_path)
        match_id = m.group(1) if m else "unknown"

    else:
        # ── Auto-discover mode ───────────────────────────────────────────
        data_dir = Path(__file__).parent / "data"
        try:
            h2h_path, line_result_path, match_id = _discover_files_in_data(data_dir)
        except FileNotFoundError as e:
            print(f"[math_script] ERROR: {e}", file=sys.stderr)
            print(
                "Usage: math_script.py [--targets <h2h_file> <line_result_file>]",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"[math_script] Auto-discovered: h2h={h2h_path}, lines={line_result_path}",
            file=sys.stderr,
        )

    print(f"[math_script] Processing match {match_id}")

    try:
        results = run(h2h_path, line_result_path)
    except FileNotFoundError as e:
        print(f"[math_script] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[math_script] ERROR: Invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    # Load lines for build_result_json (already validated above)
    lines_data = load_lines(line_result_path)

    if len(results) == 1:
        match_result, parsed, raw_block = results[0]
        merged = build_result_json(match_result, lines_data=lines_data,
                                   parsed=parsed, raw_block=raw_block)
    else:
        merged = [
            build_result_json(mr, lines_data=lines_data, parsed=p, raw_block=rb)
            for mr, p, rb in results
        ]

    output_path = Path(h2h_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[math_script] ✓ Result written to {output_path}")
    # Signal the caller which file was written
    print(f"__OUTPUT_FILE__:{output_path.name}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[math_script] FATAL: {e}", file=sys.stderr)
        sys.exit(1)