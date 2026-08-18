#!/usr/bin/env python3
"""Replay v15.5 production-result JSONs through both independent INFO selectors."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "super_basket_v15_5_history_override.py"
SPEC = importlib.util.spec_from_file_location("sb_v1553_replay", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {MODULE_PATH}")
sb = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sb
SPEC.loader.exec_module(sb)


def team_side(row: dict[str, Any], final: dict[str, Any]) -> str | None:
    source = row.get("source_evaluation") if isinstance(row.get("source_evaluation"), dict) else {}
    raw_line = source.get("raw_line_row") if isinstance(source.get("raw_line_row"), dict) else {}
    side = str(raw_line.get("team_side") or source.get("team_side") or "").lower()
    if side in {"home", "away"}:
        return side
    name = str((final.get("match") or {}).get("name") or "")
    home_name, _, away_name = name.partition(" vs ")
    team = str(row.get("team") or "")
    if team and team == home_name:
        return "home"
    if team and team == away_name:
        return "away"
    return None


def actual_value(row: dict[str, Any], final: dict[str, Any]) -> float | None:
    match = final.get("match") if isinstance(final.get("match"), dict) else {}
    score = match.get("score") if isinstance(match.get("score"), dict) else {}
    quarters = match.get("quarters") if isinstance(match.get("quarters"), dict) else {}
    market_type = str(row.get("market_type") or "")
    segment = str(row.get("segment") or "").upper()
    quarter_total = sum(
        sb.num((quarters.get(key) or {}).get("total"), 0.0) or 0.0
        for key in ("q1", "q2", "q3", "q4")
    )

    if market_type == "MATCH_TOTAL":
        score_total = sb.num(score.get("total"))
        return score_total if score_total is not None and score_total > 0 else quarter_total
    if market_type == "H1_TOTAL":
        value = sb.num(match.get("h1_total"))
        if value is not None:
            return value
        return sum(sb.num((quarters.get(key) or {}).get("total"), 0.0) or 0.0 for key in ("q1", "q2"))
    if market_type == "H2_TOTAL":
        return sum(sb.num((quarters.get(key) or {}).get("total"), 0.0) or 0.0 for key in ("q3", "q4"))
    if market_type in {"CURRENT_QUARTER_TOTAL", "QUARTER_TOTAL"} and segment in {"Q1", "Q2", "Q3", "Q4"}:
        return sb.num((quarters.get(segment.lower()) or {}).get("total"))

    side = team_side(row, final)
    if market_type == "TEAM_IT_MATCH" and side in {"home", "away"}:
        score_value = sb.num(score.get(side))
        if score_value is not None and score_value > 0:
            return score_value
        return sum(
            sb.num((quarters.get(key) or {}).get(side), 0.0) or 0.0
            for key in ("q1", "q2", "q3", "q4")
        )
    if market_type in {
        "TEAM_IT_H1", "TEAM_IT_H2", "TEAM_IT_QUARTER", "CURRENT_QUARTER_TEAM_IT"
    } and side in {"home", "away"}:
        if market_type == "TEAM_IT_H1":
            keys = ("q1", "q2")
        elif market_type == "TEAM_IT_H2":
            keys = ("q3", "q4")
        elif segment in {"Q1", "Q2", "Q3", "Q4"}:
            keys = (segment.lower(),)
        else:
            return None
        return sum(sb.num((quarters.get(key) or {}).get(side), 0.0) or 0.0 for key in keys)
    return None


def settle(row: dict[str, Any], final: dict[str, Any]) -> str:
    actual = actual_value(row, final)
    line = sb.num(row.get("line"))
    side = str(row.get("side") or "").upper()
    if actual is None or line is None or side not in {"OVER", "UNDER"}:
        return "UNSETTLED"
    if abs(actual - line) <= 1e-9:
        return "PUSH"
    won = actual > line if side == "OVER" else actual < line
    return "WIN" if won else "LOSS"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [str(row.get("outcome") or "UNSETTLED") for row in rows]
    wins = outcomes.count("WIN")
    losses = outcomes.count("LOSS")
    pushes = outcomes.count("PUSH")
    unsettled = outcomes.count("UNSETTLED")
    return {
        "lines": len(rows),
        "files": len({row["snapshot"] for row in rows}),
        "unique_matches": len({row["match_id"] for row in rows}),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "unsettled": unsettled,
        "wr": wins / (wins + losses) if wins + losses else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    matches_dir = Path(args.matches_dir).expanduser().resolve()

    final_by_match: dict[str, dict[str, Any]] = {}
    for path in matches_dir.rglob("*_result_checkpoint6.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        match_id = str((payload.get("match") or {}).get("id") or "")
        if match_id:
            final_by_match[match_id] = payload

    main_rows: list[dict[str, Any]] = []
    strict_rows: list[dict[str, Any]] = []
    raw_90_rows: list[dict[str, Any]] = []
    after_main_90_rows: list[dict[str, Any]] = []
    extra_90_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    candidate_files = 0

    with sb.tempfile.TemporaryDirectory(prefix="sb_v1553_replay_env_"):
        os.environ["SUPER_BASKET_INFO_Q4_ENABLED"] = "true"
        os.environ["SUPER_BASKET_INFO_90_90_ENABLED"] = "true"
        for path in sorted(matches_dir.rglob("*_v15_5_result.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                candidate_dicts = payload.get("ranked_candidates") or []
                if candidate_dicts:
                    candidate_files += 1
                candidates = [sb.ScoreCandidate(**row) for row in candidate_dicts]
                candidate_by_id = {str(row.market_id): source for row, source in zip(candidates, candidate_dicts)}
                forecast = payload.get("score_forecast") or {}
                match_id = str(forecast.get("match_id") or "")
                stage = str(forecast.get("stage") or "")
                selected = payload.get("selected") or {}
                selected_action = str(selected.get("action") or "PASS")
                policy = (payload.get("super_basket_v15") or {}).get("policy") or {}
                age_blocked = bool(policy.get("age_blocked")) or selected.get("market_type") == "AGE_BLOCK"
                final = final_by_match.get(match_id)
                if final is None and candidate_dicts:
                    errors.append(f"missing final: {path.name}")
                    continue

                if selected_action in {"PLAY", "RISK"}:
                    main_row = dict(selected)
                    main_rows.append({
                        "snapshot": path.name,
                        "match_id": match_id,
                        "market_id": str(main_row.get("market_id") or ""),
                        "market_type": main_row.get("market_type"),
                        "team": main_row.get("team"),
                        "side": main_row.get("side"),
                        "line": main_row.get("line"),
                        "actual": actual_value(main_row, final),
                        "outcome": settle(main_row, final),
                    })

                strict = sb.collect_q4_history_scenario_info(
                    candidates,
                    stage=stage,
                    selected_action=selected_action,
                    age_blocked=age_blocked,
                )
                excluded = {
                    str(row.get("market_id") or "") for row in strict if row.get("market_id")
                }
                main_excluded: set[str] = set()
                if selected_action in {"PLAY", "RISK"} and selected.get("market_id"):
                    main_excluded.add(str(selected["market_id"]))
                    excluded.update(main_excluded)

                raw_90 = sb.collect_history_scenario_90_info(candidates, age_blocked=age_blocked)
                after_main_90 = sb.collect_history_scenario_90_info(
                    candidates,
                    excluded_market_ids=main_excluded,
                    age_blocked=age_blocked,
                )
                extra_90 = sb.collect_history_scenario_90_info(
                    candidates,
                    excluded_market_ids=excluded,
                    age_blocked=age_blocked,
                )

                for target, variants in (
                    (strict_rows, strict),
                    (raw_90_rows, raw_90),
                    (after_main_90_rows, after_main_90),
                    (extra_90_rows, extra_90),
                ):
                    for variant in variants:
                        market_id = str(variant.get("market_id") or "")
                        source = candidate_by_id.get(market_id)
                        if source is None:
                            errors.append(f"candidate not found: {path.name} / {market_id}")
                            continue
                        target.append({
                            "snapshot": path.name,
                            "match_id": match_id,
                            "market_id": market_id,
                            "market_type": source.get("market_type"),
                            "team": source.get("team"),
                            "side": source.get("side"),
                            "line": source.get("line"),
                            "actual": actual_value(source, final),
                            "outcome": settle(source, final),
                        })
            except Exception as error:
                errors.append(f"{path.name}: {type(error).__name__}: {error}")

    combined = main_rows + strict_rows + extra_90_rows
    report = {
        "version": sb.VERSION,
        "production_result_files": len(list(matches_dir.rglob("*_v15_5_result.json"))),
        "files_with_candidates": candidate_files,
        "main_play_risk": summarize(main_rows),
        "q4_h73_s73_delta3": summarize(strict_rows),
        "all_h90_s90_before_dedup": summarize(raw_90_rows),
        "h90_s90_after_main_dedup_before_q4": summarize(after_main_90_rows),
        "new_h90_s90_after_main_q4_dedup": summarize(extra_90_rows),
        "combined_union": summarize(combined),
        "errors": errors,
        "debug": {
            "main_play_risk": main_rows,
            "q4_h73_s73_delta3": strict_rows,
            "all_h90_s90_before_dedup": raw_90_rows,
            "h90_s90_after_main_dedup_before_q4": after_main_90_rows,
            "new_h90_s90_after_main_q4_dedup": extra_90_rows,
        },
    }
    report["expected_counts_match"] = (
        report["production_result_files"] == 699
        and report["files_with_candidates"] == 151
        and report["main_play_risk"]["lines"] == 10
        and report["main_play_risk"]["wins"] == 9
        and report["main_play_risk"]["losses"] == 1
        and report["q4_h73_s73_delta3"]["lines"] == 18
        and report["q4_h73_s73_delta3"]["wins"] == 15
        and report["q4_h73_s73_delta3"]["losses"] == 3
        and report["all_h90_s90_before_dedup"]["lines"] == 15
        and report["all_h90_s90_before_dedup"]["wins"] == 14
        and report["all_h90_s90_before_dedup"]["losses"] == 1
        and report["h90_s90_after_main_dedup_before_q4"]["lines"] == 14
        and report["h90_s90_after_main_dedup_before_q4"]["wins"] == 13
        and report["h90_s90_after_main_dedup_before_q4"]["losses"] == 1
        and report["new_h90_s90_after_main_q4_dedup"]["lines"] == 10
        and report["new_h90_s90_after_main_q4_dedup"]["wins"] == 9
        and report["new_h90_s90_after_main_q4_dedup"]["losses"] == 1
        and report["combined_union"]["lines"] == 38
        and report["combined_union"]["wins"] == 33
        and report["combined_union"]["losses"] == 5
        and not errors
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["expected_counts_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
