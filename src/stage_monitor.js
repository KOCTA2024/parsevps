'use strict';

/**
 * stage_monitor.js
 *
 * Довгоживучий процес (запускати поруч з worker.js), який стежить за
 * РЕАЛЬНОЮ стадією кожного матчу з matches.json і сам вирішує, коли саме
 * пора запускати ланцюжок аналізу (парсер → math_script.py → OpenAI →
 * Telegram, все виконує сам worker.js — цей модуль лише кладе job у чергу).
 *
 * Три контрольні точки на матч:
 *   Checkpoint #1 (після 1-ї чверті) — старт вікна: kickoff + Q (NBA: +12 хв, default: +10 хв)
 *   Checkpoint #2 (half-time)        — старт вікна: kickoff + 2Q + коротка пауза
 *   Checkpoint #3 (після 3-ї чверті) — старт вікна: kickoff + 3Q + коротка пауза + half-time пауза
 * (точні хвилини рахуються кумулятивно в buildCheckpointOffsets(), з довжини
 * чверті й тривалості перерв — не "плоскими" 2×Q/3×Q, бо ті ігнорують самі
 * перерви між чвертями)
 *
 * Checkpoint #3 завжди відкриває вікно й завжди дає сигнал — навіть якщо
 * вердикт Checkpoint #2 (aiVerdict з результату відповідного job'а —
 * "PLAY"/"STRONG PLAY"/"PASS"/"CONFLICT"/"RISK ENTRY"/"PARSE_ERROR" від
 * openai_analyst.js, або "NO_DATA"/"ERROR" від worker.js) був "PLAY" чи
 * "STRONG PLAY". Раніше такий вердикт повністю пропускав Checkpoint #3 —
 * тепер це прибрано: Checkpoint #3 лише ЧЕКАЄ, поки Checkpoint #2
 * завершиться (щоб не поллити стадію паралельно з ним), а сам вердикт іде
 * лише в лог, на рішення "відкривати вікно чи ні" більше не впливає.
 *
 * Для кожної контрольної точки: починаючи з моменту старту вікна, раз на
 * хвилину питаємо стадію матчу (match_stage.js — легка перевірка, БЕЗ
 * повного парсингу df_hh/архівів, щоб ВПС встигав опрацьовувати всі матчі).
 * Щойно бачимо статус "break" — одразу ставимо задачу в чергу BullMQ
 * (delay: 0), і worker.js виконує звичний Step 1→2→3 ланцюжок.
 *
 * Виняток — Checkpoint #1 (після Q1) і Checkpoint #3 (після Q3): "break"
 * лишається ОСНОВНОЮ ознакою і тут (перевіряємо його першим на кожному
 * тіку), але фід ЧАСТО його не дає на цих межах (на відміну від half-time,
 * де break приходить стабільно) — лічильник liveMinute просто "зависає"
 * близько до довжини чверті (напр. лишається 10' і не оновлюється), або
 * стрибає одразу на менше значення, коли наступна чверть уже почалась і
 * перерву пропустили повністю. Якщо break за один тік не прийшов — в дію
 * вступає ФОЛБЕК: (a) скидання liveMinute на менше значення, або (b) те, що
 * liveMinute не змінюється STAGE_STALL_CONFIRM_MS поспіль (дефолт 2 хв,
 * як і сама коротка пауза після Q1/Q3) поблизу довжини чверті.
 *
 * Якщо перерва (або, для Checkpoint #1, старт Q2) так і не настала за
 * CHECK_WINDOW_MS від старту вікна — здаємось по цій контрольній точці
 * (лог warning) і чекаємо наступну.
 *
 * Catch-up при (пере)запуску / rescan: якщо на момент запуску деякі матчі
 * вже пройшли заплановане вікно чекпоінта, kickoff у фіді міг брехати в
 * ОБИДВА боки, і сліпа арифметика "kickoff+offset" однаково небезпечна в
 * обох випадках:
 *   - матч стартував ПІЗНІШЕ заявленого kickoff (типово — велика затримка
 *     старту) → "прострочений дедлайн" — фікція, матч ще навіть не почався;
 *   - матч стартував РАНІШЕ / скрипт довго простояв → матч уже реально на
 *     пізнішій чверті, ніж припускає розклад.
 * Тому перед тим, як наздоганяти застарілий чекпоінт, stage_monitor спершу
 * робить ОДИН live-запит реального статусу матчу (handleStaleCheckpoint):
 *   - "not_started" → kickoff відставав "у мінус" → staleness-перевірка по
 *     часу ігнорується повністю, вікно відкривається негайно (ризику зловити
 *     не ту чверть немає — жодна ще не йшла);
 *   - "finished" → чекпоінт пропускається;
 *   - "live"/"break"/невдалий live-запит → матч реально йде → відкриваємо
 *     вікно "заднім числом" ЛИШЕ якщо реальний час ще НЕ переступив момент,
 *     коли мав би початись НАСТУПНИЙ чекпоінт (для #3 — орієнтовний час
 *     завершення матчу). Якщо переступив — точку вже фізично неможливо
 *     "наздогнати" правильно: freeze-prone-фолбек Checkpoint #1/#3
 *     (скидання/зависання liveMinute) або очікування "будь-якого break" для
 *     Checkpoint #2 з високою ймовірністю зловлять межу ІНШОЇ, пізнішої
 *     чверті — і надішлють сигнал під невірною міткою (напр. "Q1 checkpoint",
 *     хоча матч насправді вже на Q2/Q3). У цьому випадку вікно взагалі не
 *     відкривається — точка просто пропускається з логом, без жодного
 *     (потенційно помилкового) сигналу.
 *
 * Usage:  node src/stage_monitor.js
 */

import path from 'path';
import fs from 'fs';
import { Queue, QueueEvents } from 'bullmq';
import { fileURLToPath } from 'url';
import { slugify } from './utils/slugify.js';
import { matchStageChecker } from './match_stage.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Config ──────────────────────────────────────────────────────────────────

const REDIS_CONFIG = {
  host:     process.env.REDIS_HOST     || '127.0.0.1',
  port:     Number(process.env.REDIS_PORT) || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
};

const QUEUE_NAME   = 'match-analysis';
const MATCHES_FILE = process.env.MATCHES_FILE
  ? path.resolve(process.env.MATCHES_FILE)
  : path.resolve(__dirname, 'matches.json');

// Ліги з 12-хвилинними чвертями (NBA-подібні) → зсуви більші.
// ВАЖЛИВО: у фіді назви ліг кириличні ("США: Літня ліга НБА в Лас-Вегасі"),
// тож окрім латинської "NBA" обов'язково матчимо кириличну "НБА" — інакше
// летня ліга NBA непомітно потрапляє під дефолтні (менші) офсети.
const NBA_PATTERN = /\bNBA\b|НБА|12[\s-]?min/i;

const POLL_INTERVAL_MS   = Number(process.env.STAGE_POLL_INTERVAL_MS) || 60_000;      // 1 хв
// Було 15 хв — зарано здавались, якщо матч стартував із запізненням щодо
// kickoff у фіді (реальний Q1/пауза розтягувались довше вікна). Підняли дефолт.
// Було 25 хв. Тепер, коли час у 'not_started' більше не витрачає це вікно
// (див. NOT_STARTED_MAX_WAIT_MS нижче), цей таймаут рахується вже ВІД
// реального старту матчу — тож підняли трохи вище як запас на лаг фіда
// в детекції break/stall самого Q1↔Q2 чи Q3↔Q4 переходу.
const CHECK_WINDOW_MS    = Number(process.env.STAGE_CHECK_WINDOW_MS)  || 35 * 60_000; // скільки чекати перерву в межах чекпоінта
const RESCAN_INTERVAL_MS = Number(process.env.MATCHES_RESCAN_MS)      || 5 * 60_000;   // раз стільки перечитуємо matches.json

// Скільки максимум чекати ЗАВЕРШЕННЯ Checkpoint #1 (у будь-якому вигляді —
// job поставлено, матч фінішував, чи вікно просто сплило), перш ніж
// відкривати вікно Checkpoint #2. Без цього при catch-up-старті (коли
// kickoff у фіді відстає від реального старту матчу) обидва чекпоінти
// відкривались ОДНОЧАСНО і поллили стадію паралельно — плутанина в логах і
// зайве навантаження. Дефолт трохи більший за CHECK_WINDOW_MS, щоб
// Checkpoint #1 встиг природно завершитись сам, а не по цьому таймауту.
const CHECKPOINT1_WAIT_MS = Number(process.env.CHECKPOINT1_WAIT_MS) || (CHECK_WINDOW_MS + 5 * 60_000);

// Скільки чекати РЕЗУЛЬТАТ Checkpoint #2 (half-time), перш ніж вирішувати долю
// Checkpoint #3. Job у черзі проходить парсер → math_script → OpenAI (до
// OPENAI_TIMEOUT_MS ≈ 3 хв за замовчуванням в openai_analyst.js) + можливі
// ретраї (3 спроби з exponential backoff, worker.js/BullMQ), тому таймаут тут
// суттєво більший за сам поллінг стадії. Якщо не встигли — вважаємо вердикт
// невідомим, і Checkpoint #3 стартує як завжди (тобто НЕ пропускається).
const CHECKPOINT2_VERDICT_WAIT_MS = Number(process.env.CHECKPOINT2_VERDICT_WAIT_MS) || 10 * 60_000; // 10 хв

// Checkpoint #1 (після Q1) і Checkpoint #3 (після Q3) страждають від одного й
// того самого: фід ЧАСТО не репортить status="break" на цих межах — замість
// цього liveMinute просто "зависає" (напр. на 10' при 10-хвилинній чверті) і
// більше не оновлюється, поки не почнеться наступна чверть. Раніше цей фікс
// був лише для Checkpoint #1 (через скидання liveMinute на старті Q2), а
// Checkpoint #3 чекав ЛИШЕ "break" — і при зависанні лічильника просто
// вичерпував CHECK_WINDOW_MS і здавався, жодного job'а так і не ставлячи.
// break перевіряється як ОСНОВНА ознака на кожному тіку (як і завжди); якщо
// фід break не дав — liveMinute-фолбек (скидання/stall) підхоплює. Коротка
// пауза після Q1/Q3 ~2 хв (SHORT_BREAK_MIN), тому STAGE_STALL_CONFIRM_MS за
// замовчуванням теж 2 хв.
const STALL_CONFIRM_MS = Number(process.env.STAGE_STALL_CONFIRM_MS) || 2 * 60_000; // 2 хв

// Матчі часто стартують із затримкою відносно kickoff у фіді (іноді майже
// без затримки, іноді дуже суттєво) — тому CHECK_WINDOW_MS раніше "згоряв"
// вхолосту, поки матч ще навіть не почався (status: 'not_started'), і
// чекпоінт здавався, хоча реальний Q1/Q3 ще навіть не наступив. Тепер час,
// проведений у 'not_started', НЕ витрачає CHECK_WINDOW_MS (див. watchCheckpoint) —
// таймер вікна зсувається вперед, поки матч реально не стартував. Але щоб не
// чекати вічно матч, який скасували/перенесли, обмежуємо це окремим стелею.
const NOT_STARTED_MAX_WAIT_MS = Number(process.env.NOT_STARTED_MAX_WAIT_MS) || 3 * 60 * 60_000; // 3 год

// Вердикти Checkpoint #2 (job.aiVerdict, див. worker.js / openai_analyst.js).
// РАНІШЕ при цих вердиктах Checkpoint #3 повністю пропускався. Тепер
// Checkpoint #3 завжди відкриває вікно й завжди дає сигнал — цей набір
// лишається лише для логування ("який був вердикт #2"), на рішення
// відкривати вікно чи ні він більше не впливає.
const SKIP_CHECKPOINT3_VERDICTS = new Set(['PLAY', 'STRONG PLAY']);

// Скільки запасу давати оцінці "кінця матчу" для catch-up-перевірки
// Checkpoint #3 (останньої точки — "наступного" чекпоінта, з яким можна
// звірити реальний час, не існує). Оцінка = kickoff + офсет Q3 + довжина
// чверті (як орієнтир для Q4) + цей запас (овертайми, затримки фіду тощо).
const CATCHUP_STALE_BUFFER_MS = Number(process.env.CATCHUP_STALE_BUFFER_MS) || 20 * 60_000; // 20 хв

// Зсуви контрольних точок рахуємо КУМУЛЯТИВНО від реальної структури матчу
// (довжина чверті + перерви між чвертями), а не "плоскими" 2×Q / 3×Q як
// раніше. Флет-варіант ([10,20,30] / [12,24,36]) ігнорував самі перерви,
// через що вікно Checkpoint #2 (half-time) і особливо Checkpoint #3 (після
// Q3) відкривалось зарано — half-time break сам по собі ~15 хв, тобто до
// Checkpoint #3 реально минає набагато більше, ніж 3×Q.
//
// Значення нижче — орієнтовні дефолти, підлаштуй під те, що реально бачиш
// у логах (короткі паузи між чвертями зазвичай ~2 хв, half-time — ~15 хв,
// але в різних лігах/фідах може відрізнятись).
const QUARTER_MIN_DEFAULT = Number(process.env.QUARTER_MIN_DEFAULT) || 10; // довжина чверті, хв (не-NBA ліги)
const QUARTER_MIN_NBA     = Number(process.env.QUARTER_MIN_NBA)     || 12; // довжина чверті, хв (NBA)
const SHORT_BREAK_MIN     = Number(process.env.SHORT_BREAK_MIN)     || 2;  // пауза після Q1 і після Q3
const HALFTIME_BREAK_MIN  = Number(process.env.HALFTIME_BREAK_MIN)  || 15; // пауза після Q2 (half-time)

/**
 * [кінець Q1, кінець Q2/half-time, кінець Q3] у хвилинах від kickoff.
 *   кінець Q1  = Q
 *   half-time  = Q + SHORT_BREAK + Q                    (Q1 + пауза + Q2)
 *   кінець Q3  = half-time + HALFTIME_BREAK + Q          (+ half-time-пауза + Q3)
 */
function buildCheckpointOffsets(quarterMin) {
  const q1End    = quarterMin;
  const halftime = quarterMin + SHORT_BREAK_MIN + quarterMin;
  const q3End    = halftime + HALFTIME_BREAK_MIN + quarterMin;
  return [q1End, halftime, q3End];
}

// Зсуви контрольних точок у хвилинах від kickoff: [Q1-break, half-time, Q3-break]
const CHECKPOINTS_DEFAULT = buildCheckpointOffsets(QUARTER_MIN_DEFAULT);
const CHECKPOINTS_NBA     = buildCheckpointOffsets(QUARTER_MIN_NBA);

function offsetsFor(league) {
  return NBA_PATTERN.test(league || '') ? CHECKPOINTS_NBA : CHECKPOINTS_DEFAULT;
}

/**
 * sportId потрібен лише для назви фіда dc_<sportId>_<matchId>.
 * match_h2h_export.js визначає його з matchUrl ('basketball' → '5', інакше '1');
 * у воркер-режимі (без matchUrl) той самий парсер де-факто падає на '1'.
 * Тут робимо це явним і трохи розумнішим: якщо в матчі є власне поле sportId —
 * довіряємо йому; інакше дивимось на league; інакше — той самий дефолт '1',
 * що й у решті пайплайна, щоб фіди були узгоджені між скриптами.
 */
function sportIdFor(match) {
  if (match.sportId) return String(match.sportId);
  if (/баскет|basketball/i.test(match.league || '') || NBA_PATTERN.test(match.league || '')) return '5';
  return process.env.STAGE_DEFAULT_SPORT_ID || '1';
}

function buildDataFilename(match) {
  const home = slugify(match.home);
  const away = slugify(match.away);
  return `${home}_vs_${away}_${match.id}.json`;
}

function toMs(kickoff) {
  const ms = typeof kickoff === 'number'
    ? (kickoff < 1e12 ? kickoff * 1000 : kickoff) // секунди vs мілісекунди
    : new Date(kickoff).getTime();
  return isNaN(ms) ? null : ms;
}

function log(matchId, ...args) {
  console.log(`[stage-monitor][match:${matchId}]`, ...args);
}

// ─── State ───────────────────────────────────────────────────────────────────

// matchId(String) -> Set(індексів чекпоінтів 0..2), які вже заплановані —
// щоб повторне сканування matches.json не плодило дублікати таймерів.
const scheduledCheckpoints = new Map();

// matchId(String) -> { promise, resolve, settled } — сигнал ЗАВЕРШЕННЯ
// Checkpoint #1 (незалежно від результату: job поставлено / матч фінішував /
// вікно сплило). Потрібен, щоб Checkpoint #2 не стартував паралельно з
// Checkpoint #1 — раніше при catch-up-старті обидва відкривались одночасно.
const checkpoint1Waiters = new Map();

// matchId(String) -> { promise, resolve, settled } — очікування вердикту
// Checkpoint #2 (half-time), від якого залежить, чи запускати Checkpoint #3.
// Створюється лінькво (при першому зверненні) і живе, поки живий процес —
// цього достатньо, бо кожен матч проходить обидва чекпоінти один раз.
const checkpoint2Waiters = new Map();

let queue;
let queueEvents;

// ─── Очікування завершення Checkpoint #1 (щоб не стартувати #2 паралельно) ───

function getCheckpoint1Waiter(matchId) {
  const key = String(matchId);
  let w = checkpoint1Waiters.get(key);
  if (!w) {
    let resolveFn;
    const promise = new Promise((resolve) => { resolveFn = resolve; });
    w = { promise, resolve: resolveFn, settled: false };
    checkpoint1Waiters.set(key, w);
  }
  return w;
}

/**
 * Фіксує, що Checkpoint #1 для матчу закінчився (в будь-якому вигляді).
 * Викликається рівно один раз на матч — повторні виклики ігноруються.
 */
function settleCheckpoint1(matchId) {
  const w = getCheckpoint1Waiter(matchId);
  if (w.settled) return;
  w.settled = true;
  w.resolve();
}

// ─── Очікування вердикту Checkpoint #2 (для рішення по Checkpoint #3) ────────

function getCheckpoint2Waiter(matchId) {
  const key = String(matchId);
  let w = checkpoint2Waiters.get(key);
  if (!w) {
    let resolveFn;
    const promise = new Promise((resolve) => { resolveFn = resolve; });
    w = { promise, resolve: resolveFn, settled: false };
    checkpoint2Waiters.set(key, w);
  }
  return w;
}

/**
 * Фіксує підсумок Checkpoint #2 для матчу: verdict (string) якщо job
 * відпрацював, або null якщо вердикту не буде (перерви не було, матч уже
 * закінчився, job не вдалось поставити/дочекатись тощо). Викликається рівно
 * один раз на матч — повторні виклики ігноруються.
 */
function settleCheckpoint2(matchId, verdict) {
  const w = getCheckpoint2Waiter(matchId);
  if (w.settled) return;
  w.settled = true;
  w.resolve(verdict);
}

/**
 * Дочекатись завершення job'а Checkpoint #2 у черзі й дістати з нього
 * aiVerdict (те саме поле, яке worker.js кладе в result.aiVerdict —
 * "PLAY"/"STRONG PLAY"/"PASS"/"CONFLICT"/"RISK ENTRY"/"PARSE_ERROR" з
 * openai_analyst.js, або "NO_DATA"/"ERROR" від самого worker.js).
 * Якщо job не вдалось отримати, він зафейлився, або не встигли за
 * CHECKPOINT2_VERDICT_WAIT_MS — вважаємо вердикт невідомим (null), що для
 * Checkpoint #3 еквівалентно "не PLAY" (працює як завжди).
 */
async function resolveCheckpoint2FromJob(matchId, job) {
  if (!job) {
    settleCheckpoint2(matchId, null);
    return;
  }
  try {
    const result = await job.waitUntilFinished(queueEvents, CHECKPOINT2_VERDICT_WAIT_MS);
    log(matchId, `Checkpoint #2 job finished — aiVerdict: ${result?.aiVerdict ?? '(none)'}`);
    settleCheckpoint2(matchId, result?.aiVerdict ?? null);
  } catch (e) {
    log(matchId, `⚠ Could not get Checkpoint #2 verdict (job ${job.id}) within ${CHECKPOINT2_VERDICT_WAIT_MS / 60_000} min: ${e.message}. ` +
                 `Treating as unresolved → Checkpoint #3 will proceed normally.`);
    settleCheckpoint2(matchId, null);
  }
}

// ─── Постановка задачі в чергу ────────────────────────────────────────────────

async function enqueueAnalysis(match, checkpointIndex) {
  const { id, home, away, kickoff, league } = match;
  const homeSlug = slugify(home);
  const awaySlug = slugify(away);

  const jobPayload = {
    matchId:      String(id),
    home,
    away,
    homeSlug,
    awaySlug,
    league:       league || '',
    kickoff,
    dataFilename: buildDataFilename(match),
    lineFilename: `line_result_${String(id)}.json`,
    fireAt:       new Date().toISOString(),
    checkpoint:   checkpointIndex + 1, // 1|2|3 — лише для логів/дебагу, worker.js це поле не використовує
  };

  // Унікальний jobId на кожну з трьох перерв одного матчу — інакше BullMQ
  // сприйме 2-гу й 3-тю перерву того ж matchId як дублікат 1-ї (за jobId).
  const jobId = `${id}_break${checkpointIndex + 1}`;

  const triggerLabel = checkpointIndex === 0 ? 'Q2 start' : `Break #${checkpointIndex + 1}`;

  try {
    const job = await queue.add('analyse', jobPayload, { jobId, delay: 0 });
    log(id, `✓ ${triggerLabel} detected → queued analysis (jobId=${jobId})`);
    return job;
  } catch (err) {
    if (/Job already exists/.test(err.message)) {
      log(id, `↷ ${triggerLabel} already queued (jobId=${jobId}). Skipped (idempotent).`);
      try {
        return await queue.getJob(jobId);
      } catch (e2) {
        console.error(`[stage-monitor] ✗ Could not fetch existing job ${jobId}:`, e2.message);
        return null;
      }
    } else {
      console.error(`[stage-monitor] ✗ Failed to queue ${jobId}:`, err.message);
      return null;
    }
  }
}

// ─── Опитування однієї контрольної точки ─────────────────────────────────────

/**
 * Викликається рівно в момент відкриття вікна (kickoff + offset).
 * Раз на POLL_INTERVAL_MS питає стадію матчу, поки:
 *   - не побачить "break"     → ставить job і зупиняється;               (Checkpoint #2, #3)
 *   - не побачить скидання liveMinute (нова чверть)  → ставить job;      (Checkpoint #1, див. нижче)
 *   - не побачить "finished"  → матч уже завершився, чекпоінт більше не актуальний;
 *   - не вичерпає CHECK_WINDOW_MS → здається, лишає лог warning.
 *
 * Checkpoint #1 (після Q1) — особливий випадок: за спостереженнями фід
 * ВЗАГАЛІ не репортить status="break" між Q1 і Q2 (бачимо суцільний "live"
 * аж до кінця вікна), тож класичне очікування break тут ніколи не спрацює.
 * Єдина ознака, яку фід реально віддає, — це liveMinute, що рахує хвилини
 * В МЕЖАХ поточної чверті і СКИДАЄТЬСЯ на початку нової (напр. 10' → 1').
 * Тому для Checkpoint #1 замість "status === 'break'" перевіряємо, що
 * liveMinute на черговому тіку (раз на хвилину, як і раніше) став МЕНШИМ
 * за попередній — це і є ознака старту Q2.
 */
function watchCheckpoint(match, checkpointIndex, sportId, quarterMin) {
  const { id } = match;
  let windowOpenedAt = Date.now();
  const notStartedWaitStartedAt = Date.now(); // стеля для 'not_started' — див. NOT_STARTED_MAX_WAIT_MS
  let stopped = false;

  // Checkpoint #1 (після Q1) і Checkpoint #3 (після Q3) — фід ЧАСТО не
  // репортить status="break" на цих межах (на відміну від half-time, де
  // break приходить стабільно). Замість "break" тут потрібно розпізнавати
  // дві альтернативні ознаки:
  //   (a) liveMinute СКИНУВСЯ на менше значення — нова чверть уже почалась,
  //       перерву пропустили повністю (наздоганяючий тригер);
  //   (b) liveMinute "завис" (не змінюється) кілька тіків поспіль близько
  //       до довжини чверті — перерва, ймовірно, вже йде просто зараз, фід
  //       лише не позначив це статусом.
  // status === 'break' теж перевіряємо паралельно — фід іноді все ж таки
  // його присилає, і тоді реагуємо одразу, не чекаючи стабілізації stall'у.
  const isFreezeProneCheckpoint = checkpointIndex === 0 || checkpointIndex === 2;
  let prevLiveMinute = null;
  let stallSinceMs = null; // коли вперше побачили поточне (незмінне) значення liveMinute

  log(id, `▶ Checkpoint #${checkpointIndex + 1} window opened — polling every ${POLL_INTERVAL_MS / 1000}s ` +
          (isFreezeProneCheckpoint
            ? `(break — основна ознака; якщо фід її не дасть, фолбек — скидання/зависання liveMinute; ` +
              `giving up after ${CHECK_WINDOW_MS / 60_000} min)`
            : `(giving up after ${CHECK_WINDOW_MS / 60_000} min if no break seen)`));

  const timer = setInterval(async () => {
    if (stopped) return;

    let stage;
    try {
      stage = await matchStageChecker.checkStage(id, sportId);
    } catch (e) {
      log(id, `⚠ stage check failed (will retry next tick): ${e.message}`);
      return;
    }

    // Матч ще фізично не почався (kickoff у фіді збрехав "у мінус" — реальна
    // затримка старту). Час чекання тут НЕ має витрачати CHECK_WINDOW_MS —
    // інакше вікно згорає вхолосту ще до того, як реально настане Q1/Q3.
    // Зсуваємо windowOpenedAt вперед на кожному тіку, поки бачимо not_started;
    // реальний відлік CHECK_WINDOW_MS почнеться лише коли матч дійсно піде.
    // Обмежено окремою стелею (NOT_STARTED_MAX_WAIT_MS), щоб не висіти вічно
    // на скасованому/перенесеному матчі.
    if (stage.status === 'not_started') {
      if (Date.now() - notStartedWaitStartedAt > NOT_STARTED_MAX_WAIT_MS) {
        stopped = true;
        clearInterval(timer);
        log(id, `⏱ Checkpoint #${checkpointIndex + 1} — match still not_started after ` +
                `${NOT_STARTED_MAX_WAIT_MS / 60_000} min of waiting → giving up (likely postponed/cancelled).`);
        if (checkpointIndex === 0) settleCheckpoint1(id);
        if (checkpointIndex === 1) settleCheckpoint2(id, null);
        return;
      }
      log(id, `Checkpoint #${checkpointIndex + 1} — match hasn't started yet (delayed kickoff) — ` +
              `window clock paused, not counting this wait against CHECK_WINDOW_MS.`);
      windowOpenedAt = Date.now();
      return;
    }

    if (Date.now() - windowOpenedAt > CHECK_WINDOW_MS) {
      stopped = true;
      clearInterval(timer);
      log(id, `⏱ Checkpoint #${checkpointIndex + 1} window expired — no trigger detected. Giving up on this checkpoint.`);
      if (checkpointIndex === 0) settleCheckpoint1(id);
      if (checkpointIndex === 1) settleCheckpoint2(id, null);
      return;
    }

    log(id, `Checkpoint #${checkpointIndex + 1} — stage: ${stage.status}` +
            (stage.liveMinute !== null ? ` (${stage.liveMinute}')` : ''));

    if (stage.status === 'finished') {
      stopped = true;
      clearInterval(timer);
      log(id, `Match already finished — checkpoint #${checkpointIndex + 1} skipped.`);
      if (checkpointIndex === 0) settleCheckpoint1(id);
      if (checkpointIndex === 1) settleCheckpoint2(id, null);
      return;
    }

    if (isFreezeProneCheckpoint) {
      // 1) ОСНОВНА ознака — той самий явний "break", що й для Checkpoint #2.
      //    Перевіряємо його першим на кожному тіку: якщо фід його дав —
      //    реагуємо негайно, без огляду на стан liveMinute.
      if (stage.status === 'break') {
        stopped = true;
        clearInterval(timer);
        log(id, `✓ Checkpoint #${checkpointIndex + 1} тригер: явний break-статус — queueing analysis.`);
        await enqueueAnalysis(match, checkpointIndex);
        if (checkpointIndex === 0) settleCheckpoint1(id);
        return;
      }

      // 2) ФОЛБЕК — застосовується лише коли break не прийшов: скидання
      //    liveMinute (нова чверть уже почалась) або "зависання" лічильника
      //    близько до довжини чверті (перерва йде, фід просто не позначив).
      const prev = prevLiveMinute;

      const minuteReset =
        stage.status === 'live' &&
        stage.liveMinute !== null &&
        prev !== null &&
        stage.liveMinute < prev;

      if (stage.liveMinute !== null && stage.liveMinute === prev) {
        if (stallSinceMs === null) stallSinceMs = Date.now();
      } else {
        stallSinceMs = null;
      }
      const stalledLongEnough = stallSinceMs !== null && (Date.now() - stallSinceMs) >= STALL_CONFIRM_MS;
      const nearQuarterEnd = stage.liveMinute !== null && quarterMin != null && stage.liveMinute >= quarterMin - 1;
      const minuteStalled = stage.status === 'live' && stalledLongEnough && nearQuarterEnd;

      if (stage.liveMinute !== null) prevLiveMinute = stage.liveMinute;

      if (minuteReset || minuteStalled) {
        stopped = true;
        clearInterval(timer);
        const reason = minuteReset
          ? `скидання liveMinute (${prev}' → ${stage.liveMinute}')`
          : `liveMinute завис на ${stage.liveMinute}' ≥${STALL_CONFIRM_MS / 60_000} хв`;
        log(id, `✓ Checkpoint #${checkpointIndex + 1} тригер (фолбек): ${reason} — queueing analysis.`);
        await enqueueAnalysis(match, checkpointIndex);
        if (checkpointIndex === 0) settleCheckpoint1(id);
      }
      // інакше — break не було, лічильник ще росте / ще не близько до кінця чверті → чекаємо наступного тіку
      return;
    }

    if (stage.status === 'break') {
      stopped = true;
      clearInterval(timer);
      const job = await enqueueAnalysis(match, checkpointIndex);
      if (checkpointIndex === 1) {
        // Не блокуємо поточний тік — Checkpoint #3 (якщо вже чекає) сам
        // прокинеться, коли resolveCheckpoint2FromJob зафіксує вердикт.
        resolveCheckpoint2FromJob(id, job);
      }
    }
    // 'live' / 'not_started' / 'unknown' → просто чекаємо наступного тіку
  }, POLL_INTERVAL_MS);
}

// ─── Checkpoint #2: чекаємо завершення Checkpoint #1, перш ніж стартувати ────

/**
 * Викликається в момент, коли Checkpoint #2 мав би відкрити вікно за старим
 * розкладом (kickoff + offset). Перш ніж відкривати вікно, чекаємо, поки
 * Checkpoint #1 повністю не завершиться (job поставлено / матч фінішував /
 * вікно #1 сплило) — інакше обидва чекпоінти поллять стадію ПАРАЛЕЛЬНО, що й
 * трапляється при catch-up-старті (коли kickoff у фіді відстає від
 * реального старту матчу і всі три вікна відкриваються майже одночасно).
 * Обмежено CHECKPOINT1_WAIT_MS — якщо Checkpoint #1 з якоїсь причини не
 * завершився і за цей час, все одно відкриваємо Checkpoint #2 (не блокуємось
 * назавжди).
 */
function scheduleCheckpoint2(match, checkpointIndex, sportId, quarterMin) {
  const { id } = match;
  log(id, `⏳ Checkpoint #${checkpointIndex + 1} reached scheduled time — waiting for Checkpoint #1 to finish (avoids polling both in parallel)…`);

  const waiter = getCheckpoint1Waiter(id);
  let timeoutHandle;
  const timeoutPromise = new Promise((resolve) => {
    timeoutHandle = setTimeout(() => resolve('timeout'), CHECKPOINT1_WAIT_MS);
  });

  Promise.race([waiter.promise.then(() => 'done'), timeoutPromise]).then((result) => {
    clearTimeout(timeoutHandle);
    if (result === 'timeout') {
      log(id, `⚠ Checkpoint #1 still not finished after ${CHECKPOINT1_WAIT_MS / 60_000} min — proceeding with Checkpoint #2 anyway.`);
    } else {
      log(id, `▶ Checkpoint #1 finished — proceeding with Checkpoint #2.`);
    }
    watchCheckpoint(match, checkpointIndex, sportId, quarterMin);
  });
}

// ─── Checkpoint #3: чекаємо вердикт Checkpoint #2, перш ніж стартувати ───────

/**
 * Викликається в момент, коли Checkpoint #3 мав би відкрити вікно за старим
 * розкладом (kickoff + offset). Перш ніж відкривати вікно, чекаємо, поки
 * остаточно не вирішиться доля Checkpoint #2 (half-time):
 *   - job завершився / зафейлився / не встигли дочекатись → verdict відомий (або null)
 *   - вікно Checkpoint #2 сплило без перерви, або матч уже закінчився → verdict = null
 * Це чекання лишається виключно щоб не поллити стадію ПАРАЛЕЛЬНО з
 * Checkpoint #2. Сам вердикт більше НЕ впливає на те, чи відкривати вікно —
 * Checkpoint #3 завжди запускається (watchCheckpoint) і завжди дає сигнал,
 * незалежно від того, яким був вердикт #2 (в т.ч. "PLAY"/"STRONG PLAY").
 */
function scheduleCheckpoint3(match, checkpointIndex, sportId, quarterMin) {
  const { id } = match;
  log(id, `⏳ Checkpoint #${checkpointIndex + 1} reached scheduled time — waiting for Checkpoint #2 to finish before opening the window…`);

  const waiter = getCheckpoint2Waiter(id);
  waiter.promise.then((verdict) => {
    if (verdict && SKIP_CHECKPOINT3_VERDICTS.has(verdict)) {
      log(id, `ℹ Checkpoint #2 verdict was "${verdict}" — skip is disabled, Checkpoint #3 still proceeds.`);
    } else {
      log(id, `▶ Checkpoint #2 verdict was "${verdict ?? '(none/unresolved)'}" → proceeding with Checkpoint #3.`);
    }
    watchCheckpoint(match, checkpointIndex, sportId, quarterMin);
  });
}

// ─── Рішення по "простроченому" (за кikoff+offset) чекпоінту ────────────────

/**
 * Викликається, коли за розкладом (kickoffMs + offset) чекпоінт мав би вже
 * відкрити вікно, але цього не сталося (скрипт (пере)запустився пізно /
 * matches.json довго не оновлювався / kickoff у фіді неточний). Розв'язує
 * ДВІ протилежні проблеми відразу — тому перше, що робимо, це ОДИН live-запит
 * реального статусу матчу (той самий matchStageChecker.checkStage, що й у
 * watchCheckpoint):
 *
 *   1) kickoff у фіді "збрехав У МІНУС" (матч стартував ПІЗНІШЕ за заявлений
 *      kickoff, типово — велика затримка старту матчу). Тоді арифметика
 *      "дедлайн уже минув" — фікція: матч ще навіть не починався. Якщо тут
 *      сліпо застосувати перевірку "чи не занадто пізно наздоганяти" (по
 *      kickoff-часу), вона хибно вирішить "занадто пізно" і НАЗАВЖДИ пропустить
 *      чекпоінт, хоча насправді ще нічого не відбулось. Розпізнається через
 *      live-статус "not_started" — у цьому випадку staleness-перевірку просто
 *      ІГНОРУЄМО і відкриваємо вікно негайно (ризику зловити не ту чверть
 *      немає: жодна чверть ще не йшла).
 *
 *   2) kickoff у фіді "збрехав У ПЛЮС" (матч стартував РАНІШЕ, ніж заявлено,
 *      або скрипт довго простояв) — це протилежний випадок, коли матч уже
 *      реально прогресує ДАЛІ, ніж припускає розклад. Якщо тут наздоганяти
 *      #1/#3 через freeze-prone-фолбек (скидання/зависання liveMinute) або
 *      #2 через "будь-який break", легко зловити межу ІНШОЇ, пізнішої чверті —
 *      і надіслати сигнал під невірною міткою (напр. "Q1 checkpoint", хоча
 *      матч уже на Q2/Q3). Розпізнається через live-статус "live"/"break" —
 *      у цьому випадку ЗАСТОСОВУЄМО nextCheckpointStartMs-перевірку.
 *
 * Якщо сам live-запит не вдався (мережа/Playwright), падаємо назад на поле
 * match.status із matches.json — і про всяк випадок ПОВОДИМОСЬ ОБЕРЕЖНО:
 * не довіряємо "not_started"-шляху без свіжого підтвердження, тобто
 * застосовуємо staleness-перевірку (безпечніше пропустити сумнівну точку,
 * ніж ризикнути надіслати сигнал під невірною міткою).
 */
async function handleStaleCheckpoint(match, idx, minutes, checkpointDeadline, nextCheckpointStartMs, sportId, startCheckpoint) {
  const { id } = match;

  let liveStatus = null;
  try {
    liveStatus = await matchStageChecker.checkStage(id, sportId);
  } catch (e) {
    log(id, `⚠ Live status check failed for stale Checkpoint #${idx + 1} (${e.message}) — ` +
            `falling back to matches.json status + kickoff-time staleness check.`);
  }

  const status = liveStatus?.status ?? (match.status || 'unknown');

  if (status === 'finished') {
    log(id, `Checkpoint #${idx + 1} (+${minutes} min) window long expired and match is finished → skipping.`);
    if (idx === 0) settleCheckpoint1(id);
    if (idx === 1) settleCheckpoint2(id, null);
    return;
  }

  if (liveStatus?.status === 'not_started') {
    // Live-підтверджено: реальний матч ще не почався — kickoff у фіді
    // відставав "у мінус" (затримка старту). Прострочений дедлайн тут —
    // фікція, staleness-перевірку по kickoff-часу ігноруємо повністю.
    log(id, `⏰ Checkpoint #${idx + 1} (+${minutes} min) deadline looked expired by kickoff-time math, but a live ` +
            `check shows the match hasn't actually started yet (delayed kickoff) → opening the window now anyway, ` +
            `kickoff-time staleness check skipped (no risk of catching the wrong quarter — none has happened yet).`);
    startCheckpoint();
    return;
  }

  // status тут: 'live' / 'break' / 'unknown' (включно з випадком, коли сам
  // live-запит не вдався) — матч, найімовірніше, реально вже йде, тож
  // застосовуємо захист від "не тієї чверті".
  if (Date.now() > nextCheckpointStartMs) {
    log(id, `⏰ Checkpoint #${idx + 1} (+${minutes} min) is too stale to catch up safely — real time already passed ` +
            `the start of the next checkpoint / estimated match window (${new Date(nextCheckpointStartMs).toISOString()}), ` +
            `and the match is confirmed live/already had a break → match is likely on a later quarter already. ` +
            `Skipping WITHOUT opening a window, to avoid sending a signal under the wrong checkpoint label.`);
    if (idx === 0) settleCheckpoint1(id);
    if (idx === 1) settleCheckpoint2(id, null);
    return;
  }

  log(id, `⏰ Checkpoint #${idx + 1} (+${minutes} min) scheduled window already passed ` +
          `(deadline was ${new Date(checkpointDeadline).toISOString()}), but match is still in progress ` +
          `and hasn't reached the next checkpoint yet → opening catch-up window now instead of skipping silently.`);
  startCheckpoint();
}

// ─── Планування всіх трьох чекпоінтів матчу ──────────────────────────────────

function scheduleMatch(match) {
  const { id, kickoff, league } = match;
  if (!id || !kickoff) return;

  const kickoffMs = toMs(kickoff);
  if (kickoffMs === null) {
    console.warn(`[stage-monitor] Invalid kickoff for match ${id}: ${kickoff}`);
    return;
  }

  let doneSet = scheduledCheckpoints.get(String(id));
  if (!doneSet) {
    doneSet = new Set();
    scheduledCheckpoints.set(String(id), doneSet);
  }

  const offsets = offsetsFor(league);
  const quarterMin = NBA_PATTERN.test(league || '') ? QUARTER_MIN_NBA : QUARTER_MIN_DEFAULT;
  const sportId = sportIdFor(match);
  const now = Date.now();

  offsets.forEach((minutes, idx) => {
    if (doneSet.has(idx)) return; // вже заплановано під час попереднього rescan()

    const checkpointStart    = kickoffMs + minutes * 60_000;
    const checkpointDeadline = checkpointStart + CHECK_WINDOW_MS;

    // Для перевірки "чи не застаріла точка НАСТІЛЬКИ, що наздоганяти її вже
    // небезпечно" потрібен орієнтир — момент старту НАСТУПНОГО чекпоінта.
    // Для #3 (останньої точки) такого наступного офсету немає — оцінюємо
    // орієнтовний кінець матчу як kickoff + офсет Q3 + довжина чверті (Q4) +
    // запас (овертайми/затримки фіду).
    const nextCheckpointStartMs = idx + 1 < offsets.length
      ? kickoffMs + offsets[idx + 1] * 60_000
      : checkpointStart + quarterMin * 60_000 + CATCHUP_STALE_BUFFER_MS;

    const startCheckpoint = idx === 2
      ? () => scheduleCheckpoint3(match, idx, sportId, quarterMin) // #3 — спершу чекає завершення #2 (сигнал дає завжди)
      : idx === 1
        ? () => scheduleCheckpoint2(match, idx, sportId, quarterMin) // #2 — спершу чекає завершення #1, щоб не поллити паралельно
        : () => watchCheckpoint(match, idx, sportId, quarterMin);    // #1 — без змін

    if (now > checkpointDeadline) {
      doneSet.add(idx);
      // Fire-and-forget: рішення по застарілому чекпоінту вимагає одного
      // live-запиту статусу (див. handleStaleCheckpoint), тому асинхронне.
      // forEach не чекає на нього — інші чекпоінти/матчі плануються далі.
      handleStaleCheckpoint(match, idx, minutes, checkpointDeadline, nextCheckpointStartMs, sportId, startCheckpoint)
        .catch(e => console.error(`[stage-monitor] handleStaleCheckpoint failed for match ${id} checkpoint #${idx + 1}:`, e));
      return;
    }

    doneSet.add(idx);
    const delay = Math.max(0, checkpointStart - now);
    setTimeout(startCheckpoint, delay);
    log(id, `Scheduled checkpoint #${idx + 1} (+${minutes} min${NBA_PATTERN.test(league || '') ? ', NBA' : ''}) ` +
            `→ opens in ${(delay / 60_000).toFixed(1)} min`);
  });
}

// ─── Завантаження matches.json ────────────────────────────────────────────────

function loadMatches() {
  if (!fs.existsSync(MATCHES_FILE)) {
    console.warn(`[stage-monitor] matches.json not found at ${MATCHES_FILE}`);
    return [];
  }
  let matches;
  try {
    matches = JSON.parse(fs.readFileSync(MATCHES_FILE, 'utf8'));
  } catch (e) {
    console.error('[stage-monitor] Failed to parse matches.json:', e.message);
    return [];
  }
  // monitor.js пише { fetchedAt, fsign, count, matches: [...] } — той самий формат,
  // що вже підтримує orchestrator.js.
  if (matches && !Array.isArray(matches) && Array.isArray(matches.matches)) {
    matches = matches.matches;
  }
  return Array.isArray(matches) ? matches : [];
}

function rescan() {
  const matches = loadMatches();
  for (const match of matches) scheduleMatch(match);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  queue = new Queue(QUEUE_NAME, {
    connection: REDIS_CONFIG,
    defaultJobOptions: {
      attempts: 3,
      backoff: { type: 'exponential', delay: 60_000 },
      removeOnComplete: { age: 86_400 },
      removeOnFail:     { age: 7 * 86_400 },
    },
  });

  // Потрібен для job.waitUntilFinished() — дізнатись aiVerdict Checkpoint #2
  // (виконує його worker.js в окремому процесі), щоб вирішити долю Checkpoint #3.
  queueEvents = new QueueEvents(QUEUE_NAME, { connection: REDIS_CONFIG });
  await queueEvents.waitUntilReady();

  rescan();
  setInterval(rescan, RESCAN_INTERVAL_MS);

  console.log(
    `[stage-monitor] Started. Watching "${MATCHES_FILE}" | ` +
    `poll every ${POLL_INTERVAL_MS / 1000}s | check window ${CHECK_WINDOW_MS / 60_000} min | ` +
    `stall-confirm (Checkpoints #1/#3 fallback) ${STALL_CONFIRM_MS / 60_000} min | ` +
    `rescanning matches.json every ${RESCAN_INTERVAL_MS / 60_000} min | ` +
    `Checkpoint #2 waits up to ${CHECKPOINT1_WAIT_MS / 60_000} min for Checkpoint #1 to finish | ` +
    `Checkpoint #3 always runs (no skip on verdict) once Checkpoint #2 is done | ` +
    `stale catch-up checkpoints (real time already past the next one) are skipped, not fired ` +
    `(#3 stale buffer ${CATCHUP_STALE_BUFFER_MS / 60_000} min)`
  );
}

async function shutdown(signal) {
  console.log(`\n[stage-monitor] ${signal} — shutting down…`);
  try { await matchStageChecker.close(); } catch {}
  try { await queue?.close(); } catch {}
  try { await queueEvents?.close(); } catch {}
  console.log('[stage-monitor] Shutdown complete.');
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

main().catch(err => {
  console.error('[stage-monitor] Fatal error:', err);
  process.exit(1);
});