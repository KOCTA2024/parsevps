#!/usr/bin/env python3
"""
compress_result.py — сжимает выходной JSON math_script.py, убирая
подтверждённую избыточность, без потери информации:

1. logic.zone_summary — удаляется целиком. Это производный набор из
   logic.history_zones / conditional_history_zones, разложенный по 6
   пересекающимся порогам (100/95/90/85/80/78%), где каждый следующий
   порог — надмножество предыдущего. Вся эта информация уже есть в
   history_zones (там же лежит и сам *_zone label на каждую запись).

2. thresholds / zones внутри history_zones и conditional_history_zones —
   для каждой линии есть пара OVER/UNDER. UNDER полностью выводится из
   OVER: n_under == n_over, hits_under == n - hits_over,
   rate_under == 1 - rate_over. Оставляем только OVER.

3. raw_data.team_a_hist / team_b_hist / h2h_hist / raw_block — сырые
   строки матчей с ~390 полями статистики, большинство из которых
   пустые строки "" (нет детальной статистики по броскам/подборам для
   этой лиги). Пустые/None поля убираются из каждой записи — это valid
   sparse JSON, отсутствующее поле == "нет данных" так же, как и "".

Использование:
    python3 compress_result.py <input.json> [output.json]

Если output не указан — перезаписывает input.json (как это делает
сам math_script.py).
"""
import sys
import json
import copy


def strip_under_recursive(obj):
    """Рекурсивно убирает UNDER-записи из любых полей thresholds/zones."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (
                k in ("thresholds", "zones")
                and isinstance(v, list)
                and v
                and isinstance(v[0], dict)
                and "side" in v[0]
            ):
                obj[k] = [e for e in v if e.get("side") != "UNDER"]
            else:
                strip_under_recursive(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_under_recursive(item)


def strip_empty_fields(record: dict) -> dict:
    """Убирает пустые строки / None из плоской записи (raw_data rows)."""
    return {k: v for k, v in record.items() if v not in ("", None)}


def compress(data: dict) -> dict:
    d = copy.deepcopy(data)

    logic = d.get("logic", {})

    # 1. zone_summary — полностью дублирует history_zones/conditional_history_zones
    if "zone_summary" in logic:
        del logic["zone_summary"]

    # 2. UNDER-записи убираем из history_zones и conditional_history_zones
    if "history_zones" in logic:
        strip_under_recursive(logic["history_zones"])
    if "conditional_history_zones" in logic:
        strip_under_recursive(logic["conditional_history_zones"])

    # 3. Пустые поля в сырых записях матчей
    raw_data = d.get("raw_data", {})
    for key in ("team_a_hist", "team_b_hist", "h2h_hist", "raw_block"):
        if key in raw_data and isinstance(raw_data[key], list):
            raw_data[key] = [
                strip_empty_fields(r) if isinstance(r, dict) else r
                for r in raw_data[key]
            ]

    return d


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 compress_result.py <input.json> [output.json]", file=sys.stderr)
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else in_path

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    before_size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    compressed = compress(data)
    after_size = len(json.dumps(compressed, ensure_ascii=False, separators=(",", ":")))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(compressed, f, ensure_ascii=False, separators=(",", ":"))

    saved_pct = round(100 * (1 - after_size / before_size), 1)
    print(f"[compress_result] {before_size:,} -> {after_size:,} chars "
          f"(-{saved_pct}%)", file=sys.stderr)
    print(f"[compress_result] written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
