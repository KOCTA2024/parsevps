#!/usr/bin/env python3
"""Batch-run SUPER_BASKET over match JSON files and print results to stdout.

Selection rules:
- source directory: ./src/data by default;
- include *.json files;
- exclude files whose name starts with "line_";
- exclude files whose stem ends with "result" (for example *_q1_result.json).

The runner disables GPT and Telegram, uses a temporary SQLite database, writes
calculator output only to a temporary directory, and prints each complete
calculated JSON result to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all eligible JSON files from ./src/data through SUPER_BASKET."
    )
    parser.add_argument(
        "--data-dir",
        default="./src/data",
        help="Directory with input JSON files (default: ./src/data).",
    )
    parser.add_argument(
        "--calculator",
        help=(
            "Path to the calculator script. If omitted, common project paths "
            "are checked automatically."
        ),
    )
    parser.add_argument(
        "--zones",
        help="Optional path to 02_team_relative_stat_zones_COMPACT.json.",
    )
    parser.add_argument(
        "--mode",
        choices=("action", "strict"),
        default="action",
        help="Calculator mode (default: action).",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one compact JSON object per line instead of pretty output.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Read only files directly inside data-dir, without subdirectories.",
    )
    return parser.parse_args()


def resolve_calculator(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Calculator not found: {path}")
        return path

    candidates = (
        Path("./src/super_basket_vps_system_v5_6_1_fixed.py"),
        Path("./src/super_basket_vps_system.py"),
        Path("./super_basket_vps_system_v5_6_1_fixed.py"),
        Path("./super_basket_vps_system.py"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    checked = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Calculator script was not found. Checked:\n  - " + checked
        + "\nPass its path explicitly with --calculator."
    )


def eligible_json_files(data_dir: Path, recursive: bool) -> list[Path]:
    iterator = data_dir.rglob("*.json") if recursive else data_dir.glob("*.json")
    selected: list[Path] = []

    for path in iterator:
        if not path.is_file():
            continue
        name_lower = path.name.lower()
        stem_lower = path.stem.lower()
        if name_lower.startswith("line_"):
            continue
        if stem_lower.endswith("result"):
            continue
        selected.append(path.resolve())

    return sorted(selected, key=lambda item: str(item).lower())


def isolated_environment(temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()

    # Explicitly disable every external delivery/review path.
    env["SUPER_BASKET_REQUIRE_GPT"] = "false"
    env["OPENAI_API_KEY"] = ""
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""
    env["TELEGRAM_CHATS_FILE"] = ""

    # Keep test side effects outside the project and production state.
    env["SUPER_BASKET_DB"] = str(temp_dir / "batch_test.sqlite3")
    env["VERDICT_LOG_FILE"] = str(temp_dir / "verdicts.log")
    env["EXCEL_AUDIT_FILE"] = str(temp_dir / "audit.xlsx")
    env["SUPER_BASKET_EXCEL_AUDIT"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def print_payload(payload: dict[str, Any], jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    args = parse_args()

    try:
        data_dir = Path(args.data_dir).expanduser().resolve()
        if not data_dir.is_dir():
            raise NotADirectoryError(f"Data directory not found: {data_dir}")
        calculator = resolve_calculator(args.calculator)
        zones = Path(args.zones).expanduser().resolve() if args.zones else None
        if zones is not None and not zones.is_file():
            raise FileNotFoundError(f"Zones file not found: {zones}")
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    files = eligible_json_files(data_dir, recursive=not args.no_recursive)
    if not files:
        print(
            f"No eligible JSON files found in {data_dir}. "
            "Excluded: names starting with line_ and stems ending with result.",
            file=sys.stderr,
        )
        return 0

    succeeded = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="super_basket_batch_") as temp_name:
        temp_dir = Path(temp_name)
        env = isolated_environment(temp_dir)
        db_path = temp_dir / "batch_test.sqlite3"

        for index, match_path in enumerate(files, start=1):
            output_path = temp_dir / f"result_{index:05d}.json"
            command = [
                sys.executable,
                str(calculator),
                "run",
                "--match",
                str(match_path),
                "--output",
                str(output_path),
                "--db",
                str(db_path),
                "--mode",
                args.mode,
                "--dry-run",
                "--no-require-gpt",
                "--no-gpt",
                "--no-telegram",
            ]
            if zones is not None:
                command.extend(("--zones", str(zones)))

            completed = subprocess.run(
                command,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            if completed.returncode == 0 and output_path.is_file():
                try:
                    calculated = json.loads(output_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    completed = subprocess.CompletedProcess(
                        completed.args,
                        1,
                        completed.stdout,
                        completed.stderr + f"\nCould not read calculated JSON: {exc}",
                    )
                else:
                    succeeded += 1
                    payload = {
                        "batch": {
                            "index": index,
                            "total": len(files),
                            "input_file": str(match_path),
                            "calculator": str(calculator),
                            "returncode": completed.returncode,
                            "calculator_stdout": completed.stdout.strip(),
                            "calculator_stderr": completed.stderr.strip(),
                        },
                        "result": calculated,
                    }
                    if not args.jsonl:
                        print(
                            f"\n{'=' * 96}\n"
                            f"[{index}/{len(files)}] {match_path.name}\n"
                            f"{'=' * 96}",
                            flush=True,
                        )
                    print_payload(payload, args.jsonl)
                    continue

            failed += 1
            error_payload = {
                "batch": {
                    "index": index,
                    "total": len(files),
                    "input_file": str(match_path),
                    "calculator": str(calculator),
                    "returncode": completed.returncode,
                    "status": "ERROR",
                    "calculator_stdout": completed.stdout.strip(),
                    "calculator_stderr": completed.stderr.strip(),
                },
                "result": None,
            }
            if not args.jsonl:
                print(
                    f"\n{'!' * 96}\n"
                    f"[{index}/{len(files)}] ERROR: {match_path.name}\n"
                    f"{'!' * 96}",
                    flush=True,
                )
            print_payload(error_payload, args.jsonl)

    summary = {
        "batch_summary": {
            "data_dir": str(data_dir),
            "calculator": str(calculator),
            "eligible_files": len(files),
            "succeeded": succeeded,
            "failed": failed,
            "gpt_enabled": False,
            "telegram_enabled": False,
            "persistent_output_files_created": False,
            "production_database_modified": False,
        }
    }
    if not args.jsonl:
        print(f"\n{'-' * 96}\nBATCH SUMMARY\n{'-' * 96}", flush=True)
    print_payload(summary, args.jsonl)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
