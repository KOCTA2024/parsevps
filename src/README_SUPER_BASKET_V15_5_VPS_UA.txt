SUPER BASKET v15.5 — EXACT ANCHOR + HISTORY OVERRIDE
=====================================================

ЩО ЦЕ
v15.5 продовжує логіку v15.4: точний рахунок/проєкція є головним якорем напрямку,
а реальна букмекерська лінія, history, scenario та live context вирішують, чи є сигнал.
Базові файли score predictor, v14.2 advisor та calibration не змінені.

ГОЛОВНІ ПРАВИЛА ФІНАЛЬНОГО ВІДБОРУ
1. Сигнал тільки по реальній bookmaker line і з odds >= 1.44.
2. Exact-score delta до лінії має бути >= 4.0 очки у напрямку сигналу.
3. Для будь-якого RISK/PLAY: P_history >= 60% і P_scenario >= 60%.
4. U16/U17: жодного OVER/UNDER сигналу. Дозволено від U18.
5. Чверть з delta 4.0-6.9: максимум RISK. PLAY можливий тільки від delta >= 7.
6. FAKE OVER / FAKE UNDER / STAT_GATE_AGAINST / Q4 context conflict — SOFT CONFLICT:
   - без сильного history/scenario => PASS;
   - history >=75% + scenario >=60% + delta >=4 => HISTORY OVERRIDE, максимум RISK;
   - history >=90% + scenario >=60% + delta >=4 => STRONG_HISTORY_OVERRIDE, максимум RISK.
7. UNDER HOT CONTINUATION — hard PASS:
   коли є 2-0/3-0 тренд по чвертях однієї команди та сильний over-live profile
   (висока реалізація/обсяг/over gate). Сильна UNDER history це не обходить.
8. Без real line синтетичний/model-only OVER/UNDER сигнал у Telegram не створюється.
9. Telegram: одна рекомендація або PASS + точний прогноз матчу та наступної релевантної чверті.

ФАЙЛИ
- super_basket_v15_5_history_override.py — головний радник v15.5.
- basketball_score_predictor_v4.py — незмінене ядро exact score.
- super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py — незмінений history/scenario/stat/Q4 engine.
- v15_2_calibration_production_485.json — незмінена calibration.
- PARSER_MARKET_SCHEMA_FIX_UA.txt — schema notes.
- V15_5_TEST_LOG.txt — перевірки нової final-selection логіки.

ОДИН ФАЙЛ
python3 super_basket_v15_5_history_override.py run \
  --match /path/to/match.json \
  --score-model basketball_score_predictor_v4.py \
  --advisor "super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py" \
  --calibration v15_2_calibration_production_485.json \
  --bankroll-usdt 1000 \
  --simulations 12000 \
  --output /path/to/out/match_v15_5_result.json

WATCH
python3 super_basket_v15_5_history_override.py watch \
  --inbox /home/ubuntu/parsevps/src/data \
  --outbox /home/ubuntu/parsevps/app/state/v15_5 \
  --score-model basketball_score_predictor_v4.py \
  --advisor "super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py" \
  --calibration v15_2_calibration_production_485.json \
  --bankroll-usdt 1000 \
  --simulations 12000 \
  --telegram

ПЕРЕВІРКА
python3 -m py_compile super_basket_v15_5_history_override.py
python3 super_basket_v15_5_history_override.py --help

ВАЖЛИВО
Це зміна selection/gating layer. Вона не є доказом майбутнього win rate.
Перевіряти якість треба forward test на нових матчах без підглядання фінальних результатів.
