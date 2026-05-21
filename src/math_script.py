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

MIN_VALID_MATCHES = 20
PARSEDATAFILE = sorted(
    [
        entry for entry in os.scandir(os.path.join(os.path.dirname(__file__), "data"))
        if entry.is_file() and entry.name != "line_result.json"
    ],
    key=lambda e: e.stat().st_mtime,
    reverse=True,
)[0].path
print(PARSEDATAFILE)
LINES_FILE = os.path.join(os.path.dirname(__file__), "data", "line_result.json")


def load_lines(path: str = LINES_FILE) -> dict:
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
        }
    except (FileNotFoundError, json.JSONDecodeError, Exception):
        return {
            "match_total": [], "half_total": [], "quarter_total": [],
            "match_handicap": [], "match_1x2": [], "other": [],
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

    for rec in raw:
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

    return {
        "date": rec.get("dt"), "league": rec.get("tour"),
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
    """any_q_21_plus, any_q_18_minus, unmet patterns"""
    n = len(rows)
    if n == 0:
        return {}
    over_thresholds = [16,18,20,21,23,25,28,30,32]
    under_thresholds = [8,10,12,14,16,18,19,20]
    result = {"any_q_over": {}, "any_q_under": {}}
    for t in over_thresholds:
        hits = sum(1 for r in rows if max(r["q1_team"],r["q2_team"],r["q3_team"],r["q4_team"]) >= t)
        result["any_q_over"][str(t)] = pct_str(hits, n)
    for t in under_thresholds:
        hits = sum(1 for r in rows if min(r["q1_team"],r["q2_team"],r["q3_team"],r["q4_team"]) <= t)
        result["any_q_under"][str(t)] = pct_str(hits, n)

    # Unmet pattern: state where neither Q1 nor Q2 hit threshold, then Q3 or Q4 does
    for t in [18, 20, 21, 23, 25]:
        state_rows = [r for r in rows if r["q1_team"] < t and r["q2_team"] < t]
        if state_rows:
            future = sum(1 for r in state_rows if r["q3_team"] >= t or r["q4_team"] >= t)
            result[f"unmet_after_ht_{t}"] = pct_str(future, len(state_rows))
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
    if not hist_h1_totals:
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
            "q_pf": [r["q1_team"], r["q2_team"], r["q3_team"], r["q4_team"]],
            "q_pa": [r["q1_opp"],  r["q2_opp"],  r["q3_opp"],  r["q4_opp"]],
            "pf": r["team_points"],
            "pa": r["opp_points"],
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
    'Live (4-а чверть 4\')'  → 3 full quarters * q_dur + 4
    'Halftime' / 'HT'       → 2 * q_dur
    'Finished'              → 4 * q_dur
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
    q1h = safe_int(main.get("q1h")); q1a = safe_int(main.get("q1a"))
    q2h = safe_int(main.get("q2h")); q2a = safe_int(main.get("q2a"))
    h1_home = q1h+q2h; h1_away = q1a+q2a
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
    }

    # Conditional scanner
    current_margin_bucket = ht_margin_bucket(margin_team_a)
    current_q_state       = quarter_state(q1h > q1a, q2h > q2a)
    hist_h1_totals_a = [r["h1_total"] for r in rows_a]
    current_ht_bucket = ht_total_bucket(h1_home + h1_away, hist_h1_totals_a)
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
            "q1": f"{q1h}-{q1a}",
            "q2": f"{q2h}-{q2a}",
            "h1_total": h1_home + h1_away,
            "current_total": hs + aws,
            "margin_team_a": margin_team_a,
            "url": main.get("url", ""),
        },
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
    }

# ─────────────────────────────────────────────
# RESULT MERGER — map process_match output → result.json skeleton
# ─────────────────────────────────────────────
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
        # старые поля сохраняются
        "sample_gate": sg,
        "sample_strength": r.get("sample_strength"),
        "live_stat_support": r.get("live_stat_support", {}),
        "blockers": r.get("blockers", []),
        "final_signal": r.get("final_signal"),
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
        "home_ind_total": raw_lines.get("home_ind_total", []),
        "away_ind_total": raw_lines.get("away_ind_total", []),
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
        raw_data["team_a_hist"] = parsed.get("team_a_hist", [])
        raw_data["team_b_hist"] = parsed.get("team_b_hist", [])
        raw_data["h2h_hist"]    = parsed.get("h2h_hist", [])
    if raw_block:
        raw_data["raw_block"]   = raw_block

    return {
        # ══ НОВАЯ СТРУКТУРА (schema v3) ══════════════════════════════════
        "schema_version": "basketball_line_eval_v3.0",

        # ── 1. Контекст матча (новый расширенный блок) ───────────────────
        "match": match_block,

        # ── 2. Качество данных (верхний уровень) ─────────────────────────
        "data_quality": data_quality,

        # ── 3. Линии букмекеров (новая структура + старые ключи) ─────────
        "bookmaker_lines": bookmaker_lines,

        # ── 4. Живая статистика команд ───────────────────────────────────
        "live_team_stats": live_team_stats,

        # ── 5. Проекции ──────────────────────────────────────────────────
        "projections": projections,

        # ── 6. История по точным линиям ──────────────────────────────────
        "history_by_exact_line": history_by_exact_line,

        # ── 7. Сценарные паттерны по линиям ─────────────────────────────
        "scenario_patterns_by_line": scenario_patterns_by_line,

        # ── 8. Оценки линий (главный вывод по ставкам) ───────────────────
        "line_evaluations": line_evaluations,

        # ══ СТАРАЯ СТРУКТУРА (обратная совместимость) ════════════════════

        # ── 9. meta (старый) ─────────────────────────────────────────────
        "meta": meta,

        # ── 10. Линии букмекеров (старый плоский формат) ─────────────────
        # уже включено в bookmaker_lines выше

        # ── 11. Текущее состояние матча ───────────────────────────────────
        "score_state": score_state,

        # ── 12. Итоговый вердикт ─────────────────────────────────────────
        "final_verdict": final_verdict,

        # ── 13. Рынки — кандидаты ────────────────────────────────────────
        "markets_evaluation": markets_evaluation,

        # ── 14. Формулы: проекции, пороги, калибровка ────────────────────
        "stat_conditioned_line_profiles": stat_conditioned_line_profiles,

        # ── 15. Сценарии и чекпоинты ──────────────────────────────────────
        "scenario_zones": scenario_zones,
        "checkpoint_matrices": checkpoint_matrices,

        # ── 16. Профиль по четвертям ──────────────────────────────────────
        "quarter_result_profile": quarter_result_profile,

        # ── 17. Стат-выравнивание ─────────────────────────────────────────
        "stat_alignment": stat_alignment,

        # ── 18. Исторические зоны и перцентили ────────────────────────────
        "history_zones": hz,
        "stat_zones": stat_zones,

        # ── 19. Бокссcore текущего матча ──────────────────────────────────
        "live_boxscore": live_boxscore,

        # ── 20. Сырые данные ──────────────────────────────────────────────
        "raw_data": raw_data,
    }


# ─────────────────────────────────────────────
# ENTRYPOINT — loop over all match blocks
# ─────────────────────────────────────────────
def run(filepath: str, lines_path: str = LINES_FILE) -> list:
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
        # Check both "src" (data rows) and "Source" (header rows)
        src = rec.get("src", "") or rec.get("Source", "") or ""
        if "MAIN MATCH" in src.upper():
            flush_block(current_block)
            current_block = [rec]
        else:
            current_block.append(rec)

    flush_block(current_block)
    return results


def main():
    filepath = PARSEDATAFILE
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        print(f"ERROR: Expected a JSON array at top level, got {type(raw).__name__}", file=sys.stderr)
        sys.exit(1)
    sources = [r.get("src", "") for r in raw[:10]]
    print(sources)
    try:
        results = run(filepath)
        
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    # Output to stdout as JSON (pipe-friendly for Node.js or other callers)
    results_out = [mr for mr, _, _ in results]
    print(json.dumps(results_out, ensure_ascii=False, indent=2))

    # ── Merge into result.json skeleton ──────────────────────────────────
    lines_data = load_lines()

    if len(results) == 1:
        match_result, parsed, raw_block = results[0]
        merged = build_result_json(match_result, lines_data=lines_data,
                                   parsed=parsed, raw_block=raw_block)
    else:
        merged = [
            build_result_json(mr, lines_data=lines_data, parsed=p, raw_block=rb)
            for mr, p, rb in results
        ]

    # ── Overwrite the source file (preserve its original name) ─────────
    # Reuse the exact filename from PARSEDATAFILE so the bash script can
    # always reference it by its existing name — no rename, no duplicate,
    # no spaces-vs-underscores mismatch.
    output_path = os.path.abspath(filepath)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    filename = os.path.basename(output_path)
    print(f"[result written → {output_path}]", file=sys.stderr)
    # Signal the bash script which file to scp
    print(f"__OUTPUT_FILE__:{filename}", file=sys.stderr)


if __name__ == "__main__":
    main()