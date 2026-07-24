# Basketball Coursework Analytics v1.0

Нейтральна академічна система аналізу архівних parser snapshots. Вона прогнозує розподіл очок, що залишилися, підтримує кілька файлів одночасно та окремий checkpoint після кожної чверті.

## Склад пакета

- `coursework_basketball_analytics.py` — calculator і CLI.
- `tests/test_coursework_basketball_analytics.py` — 22 автоматичні тести.
- `COURSEWORK_BASKETBALL_ANALYTICS_TZ_UA.md` — технічне завдання.
- `TEST_REPORT_UA.md` — фактичний звіт перевірки.
- `CHANGELOG_UA.md` — зміни й межі реалізації.
- `fixtures/` — 5 наданих input JSON.
- `examples/` — 5 output JSON і validation summary.

## Вимоги

- Python 3.10+.
- Зовнішні Python-пакети не потрібні.
- Network не використовується.

## Перевірка

```bash
python -m py_compile coursework_basketball_analytics.py tests/test_coursework_basketball_analytics.py
python -m unittest -v tests/test_coursework_basketball_analytics.py
```

Очікувано: `Ran 22 tests ... OK`.

## Один archived snapshot

```bash
python coursework_basketball_analytics.py run \
  --input fixtures/Aris_vs_AEK_Athens_GMWxPqlg.json \
  --output out/aris_analytics.json \
  --db state/coursework.sqlite3
```

## Кілька файлів одночасно

```bash
python coursework_basketball_analytics.py batch \
  --input-dir fixtures \
  --outbox out \
  --db state/coursework.sqlite3 \
  --workers 4
```

Або явний список:

```bash
python coursework_basketball_analytics.py batch \
  --inputs fixtures/a.json fixtures/b.json fixtures/c.json \
  --outbox out \
  --db state/coursework.sqlite3 \
  --workers 4
```

## Parser checkpoint

Старий JSON залишається сумісним. Для точного checkpoint бажано додати:

```json
{
  "analysis_context": {
    "trigger_checkpoint": 2,
    "research_replay": true
  }
}
```

Допустимі checkpoint: `0, 1, 2, 3, 4`. Якщо поля немає, значення виводиться з часу матчу.

## Ідентичність і повтори

```text
input_hash = SHA256(спортивних розділів snapshot)
run_key = SHA256(match_id + checkpoint + input_hash)
output = <stem>_cpN_<hash12>_analytics.json
```

- Exact duplicate пропускається.
- Інший checkpoint обробляється незалежно.
- Змінений рахунок/час створює новий run.
- Зміни поза спортивною моделлю не створюють нового run.
- Один broken JSON не зупиняє інші файли batch.

## Що містить forecast

- поточну суму очок;
- список чвертей, що залишилися;
- `N` унікальних історичних матчів;
- mean/median/standard deviation;
- p10/p25/p75/p90;
- прогноз фінальної суми як `current + median remaining`;
- центральний діапазон p10–p90;
- sample IDs та причини виключень;
- `READY / REVIEW_REQUIRED / INSUFFICIENT_DATA` як оцінку достатності даних.

## Обмеження

- Це offline-інструмент для курсової й перевірки статистичних методів.
- Він не виконує автоматичний polling та не надсилає результати назовні.
- Original parser JSON не змінюється.
- Неповні дані не доповнюються вигаданими значеннями.
