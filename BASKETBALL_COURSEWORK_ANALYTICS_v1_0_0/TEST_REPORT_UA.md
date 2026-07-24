# Basketball Coursework Analytics — звіт тестування

Дата: 2026-07-21

## Підсумок

| Перевірка | Результат |
|---|---:|
| Python compilation | PASS |
| Unit/integration tests | 22 PASS / 0 FAIL / 0 ERROR |
| Supplied fixture batch | 5 processed / 0 failed |
| Integrity reports | 5/5 passed |
| Exact duplicate rerun | 0 processed / 5 skipped |
| External delivery | disabled |

## Команди

```bash
python -m py_compile coursework_basketball_analytics.py tests/test_coursework_basketball_analytics.py
python -m unittest -v tests/test_coursework_basketball_analytics.py
```

## Покриття

1. Number parsing і захист від NaN/Infinity.
2. Percentile interpolation.
3. Canonical parser snapshot.
4. Remaining plan для checkpoint 0–3.
5. Partial-quarter time ratio.
6. Current points додаються один раз.
7. Finished state дорівнює observed total.
8. Current/technical/duplicate history exclusions.
9. Stable hash ігнорує неспортові sections.
10. Stable hash змінюється зі спортивним станом.
11. Readiness boundaries 8/20.
12. П’ять файлів у worker pool.
13. Complete/incomplete readiness.
14. Neutral output schema.
15. Finite valid JSON.
16. Exact rerun idempotency.
17. Незалежні checkpoint одного матчу.
18. Changed sport state creates a new run.
19. Ignored-section change is a duplicate.
20. Failure isolation.
21. Checkpoint/hash у filename.
22. SQLite unique run rows.

## Supplied fixtures

| Fixture | Checkpoint | Data readiness | Integrity |
|---|---:|---|---|
| Aris vs AEK Athens | 2 | READY | passed |
| Hoventut vs Burgos | 2 | READY | passed |
| KK Split vs KK Zabok | 2 | READY | passed |
| New York Liberty W vs Dallas Wings W | 2 | READY | passed |
| Atlanta Dream W vs Phoenix Mercury W — incomplete | 0 | INSUFFICIENT_DATA | passed |

Readiness оцінює лише повноту історичної вибірки. Для incomplete fixture система не вигадує відсутні значення.

## Batch facts

- `requested_count=5`;
- `worker_count=4`;
- `processed_count=5`;
- `failed_count=0`;
- `stored_run_count=5`;
- повторний запуск: `skipped_duplicate_count=5`.

## Відомі обмеження

- Частковий поточний період використовує пропорційне наближення історичного quarter total; у output це позначено `approximation_used=true`.
- Якщо історія не має всіх чотирьох totals, запис виключається й фіксується в audit.
- Valencia fixture, згаданий у старому ТЗ, у наданому наборі відсутній; його результат не вигадувався.
