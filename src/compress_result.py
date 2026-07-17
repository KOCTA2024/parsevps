#!/usr/bin/env python3
"""
compress_result.py — сжимает выходной JSON math_script.py, убирая
подтверждённую избыточность, без потери информации.

Шаги 1-3 — как раньше:
1. logic.zone_summary — удаляется целиком (дублирует history_zones).
2. UNDER-записи в thresholds/zones — выводятся из OVER, убираются.
3. Пустые поля в raw_data.*_hist — sparse JSON, отсутствие поля == "".

Шаг 4 (НОВОЕ) — избыточность внутри самих OVER-записей thresholds:
4a. entry["market"] — дублирует market родительского блока. Убираем.
4b. entry["*_smoothed"] — детерминированная функция от hits/n
    (smoothed = (hits+1)/(n+2)). Убираем, оставляем комментарий с формулой.
4c. entry["*_zone"] — детерминированная bucket-функция от rate
    (100/95/90/85/80/78/75/70%). Та же логика, что уже применена к
    zone_summary в шаге 1 — убираем по тем же основаниям.
4d. entry["*_n"] — размер выборки НЕ зависит от line/threshold, одно и то
    же значение повторяется в каждой записи thresholds одного блока.
    Выносим один раз в блок как "sample_sizes", убираем из entries.

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


_N_SUFFIXES = ("team_a_n", "team_b_n", "pooled_n", "h2h_n")
_SMOOTHED_SUFFIXES = (
    "team_a_smoothed", "team_b_smoothed", "pooled_smoothed", "h2h_smoothed",
)
_ZONE_SUFFIXES = ("team_a_zone", "team_b_zone", "pooled_zone", "h2h_zone")


def strip_redundant_threshold_fields(obj):
    """
    Рекурсивно находит любой словарь с ключом "thresholds" (список entries
    из _hist_zone_threshold) и убирает из каждой записи:
      - "market"      (дублирует market родительского блока)
      - "*_smoothed"  (= round((hits+1)/(n+2), 3), выводится из hits+n)
      - "*_zone"      (= bucket(rate), выводится из rate)
      - "*_n"         (не зависит от line — одинаков у всех entries блока;
                        выносится один раз в "sample_sizes" на уровень блока)
    """
    if isinstance(obj, dict):
        if "thresholds" in obj and isinstance(obj["thresholds"], list) and obj["thresholds"]:
            thresholds = obj["thresholds"]

            # Проверяем, что n-поля действительно константны в пределах
            # блока (так и должно быть — n не зависит от line). Если вдруг
            # нет — не трогаем, на всякий случай (защита от неизвестных
            # будущих форматов).
            sample_sizes = {}
            n_is_constant = True
            for suffix in _N_SUFFIXES:
                values = {e.get(suffix) for e in thresholds if suffix in e}
                if len(values) > 1:
                    n_is_constant = False
                    break
                if values:
                    sample_sizes[suffix] = next(iter(values))

            for entry in thresholds:
                entry.pop("market", None)
                for suffix in _SMOOTHED_SUFFIXES:
                    entry.pop(suffix, None)
                for suffix in _ZONE_SUFFIXES:
                    entry.pop(suffix, None)
                if n_is_constant:
                    for suffix in _N_SUFFIXES:
                        entry.pop(suffix, None)

            if n_is_constant and sample_sizes:
                obj["sample_sizes"] = sample_sizes

        for v in obj.values():
            strip_redundant_threshold_fields(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_redundant_threshold_fields(item)


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

    # 4. Избыточные поля внутри threshold-записей (market/smoothed/zone/n)
    if "history_zones" in logic:
        strip_redundant_threshold_fields(logic["history_zones"])
    if "conditional_history_zones" in logic:
        strip_redundant_threshold_fields(logic["conditional_history_zones"])

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