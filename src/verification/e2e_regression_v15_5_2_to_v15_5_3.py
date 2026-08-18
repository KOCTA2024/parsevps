#!/usr/bin/env python3
"""End-to-end regression: v15.5.2 baseline versus v15.5.3 INFO-only extension."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run_one(
    script: Path,
    match: Path,
    output: Path,
    db: Path,
    score_model: Path,
    advisor: Path,
    simulations: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(script),
        "run",
        "--match", str(match),
        "--output", str(output),
        "--db", str(db),
        "--score-model", str(score_model),
        "--advisor", str(advisor),
        "--simulations", str(simulations),
        "--bankroll-usdt", "100",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(
            f"exit={completed.returncode}; stdout={completed.stdout[-1000:]}; "
            f"stderr={completed.stderr[-1000:]}"
        )
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-script", required=True)
    parser.add_argument("--new-script", required=True)
    parser.add_argument("--matches-dir", required=True)
    parser.add_argument("--replay-report", required=True)
    parser.add_argument("--score-model", required=True)
    parser.add_argument("--advisor", required=True)
    parser.add_argument("--simulations", type=int, default=1200)
    parser.add_argument("--output")
    args = parser.parse_args()

    baseline_script = Path(args.baseline_script).resolve()
    new_script = Path(args.new_script).resolve()
    matches_dir = Path(args.matches_dir).resolve()
    replay = json.loads(Path(args.replay_report).read_text(encoding="utf-8"))
    score_model = Path(args.score_model).resolve()
    advisor = Path(args.advisor).resolve()

    debug = replay.get("debug") or {}
    snapshot_names = sorted({
        str(row["snapshot"])
        for key in (
            "main_play_risk",
            "q4_h73_s73_delta3",
            "new_h90_s90_after_main_q4_dedup",
        )
        for row in (debug.get(key) or [])
    })
    controls: list[Path] = []
    missing: list[str] = []
    for snapshot in snapshot_names:
        calculated_name = snapshot.replace("_v15_5_result.json", "_result.json")
        found = list(matches_dir.rglob(calculated_name))
        if len(found) != 1:
            missing.append(f"{calculated_name}: found {len(found)}")
        else:
            controls.append(found[0])

    checks = {
        "score_forecast": 0,
        "selected": 0,
        "ranked_candidates": 0,
        "rejected_by_v15_route": 0,
        "main_telegram_message": 0,
        "q4_info_variants": 0,
        "q4_info_messages": 0,
        "q4_info_delivery": 0,
    }
    errors: list[str] = list(missing)
    new_90_variants = 0
    new_90_messages = 0
    duplicate_90_market_ids: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sb_v1553_e2e_") as temporary:
        temp = Path(temporary)
        for index, match in enumerate(controls, 1):
            try:
                baseline = run_one(
                    baseline_script,
                    match,
                    temp / f"baseline_{index}.json",
                    temp / "baseline.sqlite3",
                    score_model,
                    advisor,
                    args.simulations,
                )
                new = run_one(
                    new_script,
                    match,
                    temp / f"new_{index}.json",
                    temp / "new.sqlite3",
                    score_model,
                    advisor,
                    args.simulations,
                )

                comparisons = {
                    "score_forecast": baseline.get("score_forecast") == new.get("score_forecast"),
                    "selected": baseline.get("selected") == new.get("selected"),
                    "ranked_candidates": baseline.get("ranked_candidates") == new.get("ranked_candidates"),
                    "rejected_by_v15_route": baseline.get("rejected_by_v15_route") == new.get("rejected_by_v15_route"),
                    "main_telegram_message": (
                        (baseline.get("telegram") or {}).get("message")
                        == (new.get("telegram") or {}).get("message")
                    ),
                    "q4_info_variants": (
                        baseline.get("q4_history_scenario_info_variants")
                        == new.get("q4_history_scenario_info_variants")
                    ),
                    "q4_info_messages": (
                        ((baseline.get("q4_history_scenario_info_telegram") or {}).get("messages"))
                        == ((new.get("q4_history_scenario_info_telegram") or {}).get("messages"))
                    ),
                    "q4_info_delivery": (
                        ((baseline.get("q4_history_scenario_info_telegram") or {}).get("delivery"))
                        == ((new.get("q4_history_scenario_info_telegram") or {}).get("delivery"))
                    ),
                }
                for key, matched in comparisons.items():
                    if matched:
                        checks[key] += 1
                    else:
                        errors.append(f"{match.name}: mismatch {key}")

                info_90 = new.get("history_scenario_90_info_telegram") or {}
                variants_90 = new.get("history_scenario_90_info_variants") or []
                new_90_variants += len(variants_90)
                new_90_messages += len(info_90.get("messages") or [])
                sent_elsewhere = {
                    str(row.get("market_id"))
                    for row in (new.get("q4_history_scenario_info_variants") or [])
                    if row.get("market_id")
                }
                selected = new.get("selected") or {}
                if str(selected.get("action") or "").upper() in {"PLAY", "RISK"} and selected.get("market_id"):
                    sent_elsewhere.add(str(selected["market_id"]))
                overlap = sent_elsewhere.intersection(
                    str(row.get("market_id")) for row in variants_90 if row.get("market_id")
                )
                duplicate_90_market_ids.extend(sorted(overlap))
            except Exception as error:
                errors.append(f"{match.name}: {type(error).__name__}: {error}")

    control_count = len(controls)
    report = {
        "baseline_version": "15.5.2-EXACT-ANCHOR-Q4-H73-S73-INFO",
        "new_version": "15.5.3-EXACT-ANCHOR-Q4-H73-S73-PLUS-H90-S90-INFO",
        "simulations": args.simulations,
        "control_files": control_count,
        "checks_equal": checks,
        "new_90_90_variants": new_90_variants,
        "new_90_90_messages": new_90_messages,
        "duplicate_90_market_ids": duplicate_90_market_ids,
        "errors": errors,
    }
    report["passed"] = (
        control_count == 23
        and all(value == control_count for value in checks.values())
        and new_90_variants == 10
        and new_90_messages == 4
        and not duplicate_90_market_ids
        and not errors
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
