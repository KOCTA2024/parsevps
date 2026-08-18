SUPER BASKET v15.5.3 — Q4 H73/S73 + ДОДАТКОВИЙ ALL 90/90 INFO
================================================================

ЦЕ DROP-IN ОНОВЛЕННЯ SUPER BASKET v15.5.2.

Головний файл:
  super_basket_v15_5_history_override.py

Версія:
  15.5.3-EXACT-ANCHOR-Q4-H73-S73-PLUS-H90-S90-INFO


1. ЩО ЗАЛИШАЄТЬСЯ БЕЗ ЗМІН
--------------------------------

- score model і розрахунок прогнозованого рахунку;
- усі ranked_candidates та їхні числові поля;
- router і допустимі ринки;
- вибір selected;
- пороги, статуси й блокери PLAY/RISK;
- stake %, сума USDT та bankroll;
- головний Telegram-текст PLAY/RISK;
- U16/U17 block;
- вимога реальної букмекерської лінії та коефіцієнта;
- SQLite/dedup та імена result-файлів;
- попередній Q4 PASS→INFO H73/S73/|DELTA|<=3.

ВАЖЛИВО:
  - |DELTA|<=3 ніколи не застосовується до PLAY/RISK;
  - поріг 90/90 також не змінює PLAY/RISK;
  - обидва INFO-шари мають бюджет 0%.


2. ПОПЕРЕДНІЙ Q4 INFO — БЕЗ ЗМІН
----------------------------------

Він працює тільки коли selected.action = PASS і виконує старі умови:

- U18+;
- stage = Q4_LIVE;
- candidate.action = PASS;
- реальна лінія та коефіцієнт;
- MATCH_TOTAL або TEAM_IT_MATCH;
- P_history >= 73%;
- P_scenario >= 73%;
- abs(projection_market - bookmaker_line) <= 3.0;
- максимум один найкращий MATCH_TOTAL і один TEAM_IT_MATCH у snapshot.

PLAY/RISK-файли цей старий Q4 INFO, як і раніше, пропускають.


3. НОВИЙ ДОДАТКОВИЙ INFO 90/90
--------------------------------

Після повного старого розрахунку та після формування Q4 INFO система окремо
перевіряє всі route-valid кандидати поточного snapshot.

Лінія потрапляє в новий Telegram INFO, якщо:

- матч U18+;
- є реальна букмекерська лінія;
- є реальний коефіцієнт;
- P_history >= 90%;
- P_scenario >= 90%.

ДЛЯ ЦЬОГО НОВОГО ШАРУ НЕМАЄ:

- обмеження stage;
- обмеження market_type;
- обмеження delta;
- TOP1 по ринку;
- вимоги candidate.action = PASS.

Тобто надсилаються всі унікальні допустимі реальні лінії 90/90.

ЗАХИСТ ВІД ДУБЛІВ:

- якщо точна лінія вже пішла як головний PLAY/RISK — другий раз 90/90 не йде;
- якщо точна лінія вже пішла у Q4 H73/S73 INFO — другий раз 90/90 не йде;
- інші лінії 90/90 із того самого snapshot усе одно надсилаються.

Новий Telegram-текст:

- «ІСТОРІЯ 90% + СЦЕНАРІЙ 90%»;
- INFO 0%, без бюджету;
- матч і stage;
- загальний прогнозований рахунок;
- ринок, команда, OVER/UNDER, лінія та коефіцієнт;
- прогноз ринку;
- raw delta та directional edge;
- P_history, P_scenario, P_final;
- exact-line history hits/N;
- букмекер.


4. ВСТАНОВЛЕННЯ НА VPS
--------------------------------

1) Зробіть резервну копію чинних файлів у /app/src або вашій папці src.

2) Розпакуйте ZIP у чинний проєкт.

3) Замініть два файли:
   - super_basket_v15_5_history_override.py
   - worker.js

Файли нижче збережені без змін для цілісності пакета:
   - basketball_score_predictor_v4.py
   - super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py

4) НЕ ВИДАЛЯЙТЕ існуючі:
   - v15_2_calibration_production_485.json;
   - math_script.py;
   - match_h2h_export.js;
   - .env / Telegram token;
   - /app/state і telegram_chats.json;
   - SQLite базу.

5) Перезапустіть ваш чинний worker/container звичним способом.

Ім'я головного Python-файлу не змінилося, тому старий шлях
SUPER_BASKET_V15_SCRIPT продовжує працювати.


5. АВАРІЙНІ ВИМИКАЧІ INFO
--------------------------------

Обидва шари за замовчуванням увімкнені.

Вимкнути тільки старий Q4 INFO:
  SUPER_BASKET_INFO_Q4_ENABLED=false

Вимкнути тільки новий 90/90 INFO:
  SUPER_BASKET_INFO_90_90_ENABLED=false

Увімкнути назад:
  SUPER_BASKET_INFO_Q4_ENABLED=true
  SUPER_BASKET_INFO_90_90_ENABLED=true

Після зміни env перезапустіть worker/container.


6. НОВІ ПОЛЯ У RESULT JSON
--------------------------------

Попередні поля збережені:
  selected
  ranked_candidates
  score_forecast
  telegram
  q4_history_scenario_info_variants
  q4_history_scenario_info_telegram

Додані:
  history_scenario_90_info_variants
  history_scenario_90_info_telegram

worker.js тепер окремо логує:
  main Telegram
  Q4 INFO
  90/90 INFO


7. ПЕРЕВІРКА ПЕРЕД ЗАПУСКОМ
--------------------------------

  python3 -m py_compile super_basket_v15_5_history_override.py
  python3 super_basket_v15_5_history_override.py --help
  python3 verification/test_q4_info_layer.py
  node --check worker.js

SHA256 усіх файлів дивіться у SHA256SUMS.txt.


8. РЕТРОСПЕКТИВНИЙ REPLAY АРХІВУ
----------------------------------

Повний replay matches(2):

- 699 production-result snapshot-файлів;
- 151 файл із кандидатами;
- чинні PLAY/RISK: 10 ліній, 9 WIN / 1 LOSS;
- чинний Q4 INFO: 18 ліній, 15 WIN / 3 LOSS;
- усі 90/90 до дедуплікації: 15 ліній, 14 WIN / 1 LOSS;
- одна з цих 15 уже є головним RISK;
- після виключення main: 14 ліній, 13 WIN / 1 LOSS;
- ще 4 точні лінії вже є у Q4 INFO і не дублюються;
- фактично нових 90/90 для Telegram: 10 ліній у 4 snapshot-файлах;
- результат нових ліній: 9 WIN / 1 LOSS = 90.0%;
- об'єднано без дублів: 38 ліній, 33 WIN / 5 LOSS = 86.8%;
- унікальних матчів в об'єднаній вибірці: 19.

Це ретроспективний тест на невеликій і корельованій вибірці. Сусідні лінії
одного матчу не є незалежними ставками, а 90% не гарантує майбутній результат.

