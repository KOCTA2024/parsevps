# Технічне завдання: Basketball Coursework Analytics v1.0

## 1. Призначення

Створити parser-compatible систему для курсової роботи, яка аналізує архівні баскетбольні snapshots після кожної чверті та прогнозує статистичний розподіл очок, що залишилися до завершення матчу.

Система не формує практичних рекомендацій, не використовує комерційні поля, не виконує зовнішню доставку й не містить грошових дій.

## 2. Вхідні дані

Підтримати наявні JSON-блоки:

- `match` — ідентифікатор, команди, час, період, рахунок, чверті;
- `rules` — тривалість чверті, якщо вона задана явно;
- `analysis_context.trigger_checkpoint` — остання завершена чверть `0..4`;
- `live_team_stats` — спортивна статистика snapshot;
- `raw_data.main_match` — fallback для стану матчу;
- `raw_data.team_a_hist`, `team_b_hist`, `h2h_hist` — історія завершених матчів.

Усі інші розділи не входять до математичної моделі й не впливають на hash, forecast або readiness.

## 3. Canonical snapshot

Функція `canonical_snapshot()` повинна повертати:

```text
match_id
home_team / away_team
quarter_minutes / regulation_minutes
elapsed_minutes
current_quarter
current_quarter_minutes_left
checkpoint
score.home / score.away / score.total
quarters Q1..Q4
schema_errors
```

Пріоритет checkpoint:

1. `analysis_context.trigger_checkpoint`;
2. finished state → `4`;
3. `floor(elapsed_minutes / quarter_minutes)`, обмежене `0..3`.

## 4. Історичний пул

`build_unique_history_pool()` об’єднує три history-масиви та виключає:

- поточний `match_id`;
- технічні результати `20:0` і `0:20`;
- дублікати за stable game ID;
- записи без усіх чотирьох quarter totals;
- іншу regulation duration для основного forecast.

Інваріант:

```text
N == len(unique sample_game_ids)
```

## 5. Remaining-segment forecast

Для snapshot визначити список майбутніх чвертей:

| Checkpoint | Повні майбутні чверті |
|---:|---|
| 0 | Q1+Q2+Q3+Q4 |
| 1 | Q2+Q3+Q4 |
| 2 | Q3+Q4 |
| 3 | Q4 |
| 4 | немає |

Якщо поточна чверть уже почалась, історичне значення цієї чверті множиться на частку часу, що залишилась:

```text
remaining_ratio = current_quarter_minutes_left / quarter_minutes
historical_remaining_game =
    remaining_ratio × current_quarter_total
    + sum(full future quarter totals)
```

Основний прогноз:

```text
forecast_final_total = current_total + median(historical_remaining_game)
```

Поточний рахунок додається рівно один раз.

Показати distribution:

- `N`;
- mean;
- median;
- standard deviation;
- p10, p25, p75, p90;
- min, max.

Центральний діапазон:

```text
[current_total + p10_remaining, current_total + p90_remaining]
```

## 6. Data readiness

Дозволені лише нейтральні значення:

- `READY` — `N >= 20` і немає schema errors;
- `REVIEW_REQUIRED` — `8 <= N < 20` і немає schema errors;
- `INSUFFICIENT_DATA` — `N < 8` або є schema errors.

Readiness описує достатність даних, а не дію користувача.

## 7. Batch/checkpoint contract

Система одночасно обробляє кілька archived JSON через bounded worker pool.

```text
input_hash = SHA256(whitelisted sporting sections)
run_key = SHA256(match_id + checkpoint + input_hash)
output = <stem>_cp<N>_<hash12>_analytics.json
```

Правила:

1. Exact duplicate пропускається.
2. Зміна лише ігнорованих розділів не створює нового run.
3. Зміна спортивного стану створює новий run.
4. Новий checkpoint того самого матчу обробляється незалежно.
5. Помилка одного файла не скасовує інші.
6. Summary сортується детерміновано.
7. SQLite використовує WAL і `busy_timeout`.
8. Автоматичного polling/watch немає.

## 8. Output schema

```json
{
  "coursework_basketball_analytics": {
    "system_version": "1.0.0",
    "research_context": true,
    "input_hash": "...",
    "run_key": "...",
    "match": {},
    "checkpoint": 2,
    "snapshot_state": {},
    "data_readiness": "READY",
    "data_quality": {},
    "remaining_plan": {},
    "forecast": {},
    "quarter_profiles": {},
    "history_audit": {},
    "external_delivery": {"enabled": false},
    "integrity_report": {"all_passed": true}
  }
}
```

## 9. Обов’язкові тести

1. Parser canonicalization.
2. Checkpoint 0–4.
3. Partial-quarter remaining ratio.
4. Current total додається один раз.
5. Current/technical/duplicate history exclusions.
6. Cross-format separation.
7. Hash не залежить від ігнорованих sections.
8. Hash змінюється зі спортивним станом.
9. Readiness boundaries 8/20.
10. П’ять fixtures обробляються паралельно.
11. Exact rerun idempotency.
12. Different checkpoints are independent.
13. Changed state same checkpoint creates a new run.
14. Failure isolation.
15. Output не містить комерційних або грошових полів.
16. JSON не містить NaN/Infinity.
17. External delivery disabled.
18. SQLite rows unique by `run_key`.

## 10. Acceptance criteria

- `py_compile` успішний;
- усі tests зелені;
- 5/5 наданих fixtures оброблено без crash;
- incomplete fixture повертає `INSUFFICIENT_DATA`;
- exact rerun пропускається;
- output містить formula, N, percentiles, sample IDs та exclusions;
- original inputs не змінюються;
- network та external delivery відсутні.
