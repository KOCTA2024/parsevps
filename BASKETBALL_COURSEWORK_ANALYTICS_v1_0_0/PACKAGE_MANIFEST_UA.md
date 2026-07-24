# Маніфест пакета

Версія: `1.0.0`  
Дата: `2026-07-21`

## Вміст

| Шлях | Роль |
|---|---|
| `coursework_basketball_analytics.py` | parser-compatible calculator і batch CLI |
| `tests/test_coursework_basketball_analytics.py` | 22 unit/integration tests |
| `COURSEWORK_BASKETBALL_ANALYTICS_TZ_UA.md` | фінальне безпечне ТЗ |
| `README_UA.md` | запуск та інтеграція |
| `TEST_REPORT_UA.md` | фактичні результати тестування |
| `CHANGELOG_UA.md` | зміни й межі |
| `fixtures/*.json` | 5 supplied inputs |
| `examples/*.json` | 5 results + validation summary |

## Контроль якості

- Python compilation: passed.
- Automated tests: `22/22 passed`.
- Supplied fixtures: `5 processed / 0 failed`.
- Exact rerun: `5 skipped`.
- Integrity: `5/5 passed`.
- External delivery: disabled.
- Runtime SQLite, WAL, cache і temporary files у пакет не включені.
- Original attachments не змінені.
