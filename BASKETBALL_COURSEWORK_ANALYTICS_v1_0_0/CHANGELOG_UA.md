# Basketball Coursework Analytics v1.0 — changelog

## Реалізовано

- Parser-compatible canonical adapter для наданих JSON.
- Паралельна обробка кількох archived snapshots.
- Checkpoint `0..4` після кожної чверті.
- Stable academic hash лише зі спортивних sections.
- Незалежні run keys для нового checkpoint або зміненого спортивного стану.
- Exact duplicate detection у batch і SQLite.
- WAL, busy timeout і один запис на run key.
- Failure isolation між файлами.
- Виключення current match, technical 20:0/0:20, duplicates та incomplete histories.
- Same-duration historical pool.
- Remaining-quarter plan, включно з partial current quarter.
- Distribution: N, mean, median, sigma, p10/p25/p75/p90, min/max.
- Formula `current total + median historical remaining`.
- Quarter profiles для майбутніх чвертей.
- Нейтральний data-readiness contract.
- Integrity report і explicit disabled external delivery.
- 22 unit/integration tests.

## Свідомо не включено

- автоматичний polling/watch;
- зовнішні повідомлення або network-виклики;
- комерційні та грошові поля;
- практичні рекомендації;
- заповнення відсутніх даних вигаданими числами.

## Сумісність

- Original parser inputs не змінюються.
- Підтримуються `match`, `rules`, `analysis_context`, `live_team_stats`, `raw_data`.
- `analysis_context.trigger_checkpoint` опційний; без нього checkpoint виводиться з часу.
- Output завжди створюється окремим JSON-файлом.
