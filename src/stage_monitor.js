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
 *   Checkpoint #1 (після 1-ї чверті) — старт вікна: kickoff + 10 хв (NBA: +12 хв)
 *   Checkpoint #2 (half-time)        — старт вікна: kickoff + 20 хв (NBA: +24 хв)
 *   Checkpoint #3 (після 3-ї чверті) — старт вікна: kickoff + 30 хв (NBA: +36 хв)
 *
 * Checkpoint #3 має додаткову умову: перш ніж відкривати вікно, чекаємо
 * вердикт Checkpoint #2 (aiVerdict з результату відповідного job'а —
 * "PLAY"/"STRONG PLAY"/"PASS"/"CONFLICT"/"RISK ENTRY"/"PARSE_ERROR" від
 * openai_analyst.js, або "NO_DATA"/"ERROR" від worker.js). Якщо вердикт
 * "PLAY" або "STRONG PLAY" — Checkpoint #3 пропускається ПОВНІСТЮ (вікно
 * не відкривається, break не очікується). У будь-якому іншому випадку
 * (інший вердикт, невідомий вердикт, таймаут очікування, матч уже
 * закінчився або вікно Checkpoint #2 сплило без перерви) — Checkpoint #3
 * працює як завжди.
 *
 * Для кожної контрольної точки: починаючи з моменту старту вікна, раз на
 * хвилину питаємо стадію матчу (match_stage.js — легка перевірка, БЕЗ
 * повного парсингу df_hh/архівів, щоб ВПС встигав опрацьовувати всі матчі).
 * Щойно бачимо статус "break" — одразу ставимо задачу в чергу BullMQ
 * (delay: 0), і worker.js виконує звичний Step 1→2→3 ланцюжок.
 *
 * Якщо перерва так і не настала за CHECK_WINDOW_MS від старту вікна —
 * здаємось по цій контрольній точці (лог warning) і чекаємо наступну.
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
const NBA_PATTERN = /NBA|12[\s-]?min/i;

const POLL_INTERVAL_MS   = Number(process.env.STAGE_POLL_INTERVAL_MS) || 60_000;      // 1 хв
const CHECK_WINDOW_MS    = Number(process.env.STAGE_CHECK_WINDOW_MS)  || 15 * 60_000; // скільки чекати перерву в межах чекпоінта
const RESCAN_INTERVAL_MS = Number(process.env.MATCHES_RESCAN_MS)      || 5 * 60_000;   // раз стільки перечитуємо matches.json

// Скільки чекати РЕЗУЛЬТАТ Checkpoint #2 (half-time), перш ніж вирішувати долю
// Checkpoint #3. Job у черзі проходить парсер → math_script → OpenAI (до
// OPENAI_TIMEOUT_MS ≈ 3 хв за замовчуванням в openai_analyst.js) + можливі
// ретраї (3 спроби з exponential backoff, worker.js/BullMQ), тому таймаут тут
// суттєво більший за сам поллінг стадії. Якщо не встигли — вважаємо вердикт
// невідомим, і Checkpoint #3 стартує як завжди (тобто НЕ пропускається).
const CHECKPOINT2_VERDICT_WAIT_MS = Number(process.env.CHECKPOINT2_VERDICT_WAIT_MS) || 10 * 60_000; // 10 хв

// Вердикти Checkpoint #2 (job.aiVerdict, див. worker.js / openai_analyst.js),
// при яких Checkpoint #3 повністю пропускається.
const SKIP_CHECKPOINT3_VERDICTS = new Set(['PLAY', 'STRONG PLAY']);

// Зсуви контрольних точок у хвилинах від kickoff: [Q1-break, half-time, Q3-break]
const CHECKPOINTS_DEFAULT = [10, 20, 30];
const CHECKPOINTS_NBA     = [12, 24, 36];

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

// matchId(String) -> { promise, resolve, settled } — очікування вердикту
// Checkpoint #2 (half-time), від якого залежить, чи запускати Checkpoint #3.
// Створюється лінькво (при першому зверненні) і живе, поки живий процес —
// цього достатньо, бо кожен матч проходить обидва чекпоінти один раз.
const checkpoint2Waiters = new Map();

let queue;
let queueEvents;

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

  try {
    const job = await queue.add('analyse', jobPayload, { jobId, delay: 0 });
    log(id, `✓ Break #${checkpointIndex + 1} detected → queued analysis (jobId=${jobId})`);
    return job;
  } catch (err) {
    if (/Job already exists/.test(err.message)) {
      log(id, `↷ Break #${checkpointIndex + 1} already queued (jobId=${jobId}). Skipped (idempotent).`);
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
 *   - не побачить "break"     → ставить job і зупиняється;
 *   - не побачить "finished"  → матч уже завершився, чекпоінт більше не актуальний;
 *   - не вичерпає CHECK_WINDOW_MS → здається, лишає лог warning.
 */
function watchCheckpoint(match, checkpointIndex, sportId) {
  const { id } = match;
  const windowOpenedAt = Date.now();
  let stopped = false;

  log(id, `▶ Checkpoint #${checkpointIndex + 1} window opened — polling every ${POLL_INTERVAL_MS / 1000}s ` +
          `(giving up after ${CHECK_WINDOW_MS / 60_000} min if no break seen)`);

  const timer = setInterval(async () => {
    if (stopped) return;

    if (Date.now() - windowOpenedAt > CHECK_WINDOW_MS) {
      stopped = true;
      clearInterval(timer);
      log(id, `⏱ Checkpoint #${checkpointIndex + 1} window expired — no break detected. Giving up on this checkpoint.`);
      if (checkpointIndex === 1) settleCheckpoint2(id, null);
      return;
    }

    let stage;
    try {
      stage = await matchStageChecker.checkStage(id, sportId);
    } catch (e) {
      log(id, `⚠ stage check failed (will retry next tick): ${e.message}`);
      return;
    }

    log(id, `Checkpoint #${checkpointIndex + 1} — stage: ${stage.status}` +
            (stage.liveMinute !== null ? ` (${stage.liveMinute}')` : ''));

    if (stage.status === 'finished') {
      stopped = true;
      clearInterval(timer);
      log(id, `Match already finished — checkpoint #${checkpointIndex + 1} skipped.`);
      if (checkpointIndex === 1) settleCheckpoint2(id, null);
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

// ─── Checkpoint #3: чекаємо вердикт Checkpoint #2, перш ніж стартувати ───────

/**
 * Викликається в момент, коли Checkpoint #3 мав би відкрити вікно за старим
 * розкладом (kickoff + offset). Замість того, щоб відкривати вікно одразу,
 * спершу чекаємо, поки остаточно не вирішиться доля Checkpoint #2 (half-time):
 *   - job завершився / зафейлився / не встигли дочекатись → verdict відомий (або null)
 *   - вікно Checkpoint #2 сплило без перерви, або матч уже закінчився → verdict = null
 * Якщо verdict "PLAY" або "STRONG PLAY" → Checkpoint #3 пропускається повністю
 * (вікно не відкривається, break не очікується). Інакше — Checkpoint #3
 * стартує як завжди (watchCheckpoint), просто можливо трохи пізніше за
 * початково заплановані kickoff+offset хвилин.
 */
function scheduleCheckpoint3(match, checkpointIndex, sportId) {
  const { id } = match;
  log(id, `⏳ Checkpoint #${checkpointIndex + 1} reached scheduled time — waiting for Checkpoint #2 verdict before deciding…`);

  const waiter = getCheckpoint2Waiter(id);
  waiter.promise.then((verdict) => {
    if (verdict && SKIP_CHECKPOINT3_VERDICTS.has(verdict)) {
      log(id, `⏭ Checkpoint #2 verdict was "${verdict}" → skipping Checkpoint #3 entirely (window not opened).`);
      return;
    }
    log(id, `▶ Checkpoint #2 verdict was "${verdict ?? '(none/unresolved)'}" → proceeding with Checkpoint #3.`);
    watchCheckpoint(match, checkpointIndex, sportId);
  });
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
  const sportId = sportIdFor(match);
  const now = Date.now();

  offsets.forEach((minutes, idx) => {
    if (doneSet.has(idx)) return; // вже заплановано під час попереднього rescan()

    const checkpointStart    = kickoffMs + minutes * 60_000;
    const checkpointDeadline = checkpointStart + CHECK_WINDOW_MS;

    if (now > checkpointDeadline) {
      // Вікно вже давно минуло (наприклад, stage_monitor піднявся із запізненням) —
      // сенсу планувати немає, помічаємо як "оброблено", щоб не перевіряти знову.
      doneSet.add(idx);
      return;
    }

    doneSet.add(idx);
    const delay = Math.max(0, checkpointStart - now);
    const startCheckpoint = idx === 2
      ? () => scheduleCheckpoint3(match, idx, sportId) // #3 — спершу чекає вердикт #2, може пропустити вікно повністю
      : () => watchCheckpoint(match, idx, sportId);    // #1, #2 — без змін
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
    `rescanning matches.json every ${RESCAN_INTERVAL_MS / 60_000} min | ` +
    `Checkpoint #3 skipped when Checkpoint #2 verdict ∈ {${[...SKIP_CHECKPOINT3_VERDICTS].join(', ')}} ` +
    `(waiting up to ${CHECKPOINT2_VERDICT_WAIT_MS / 60_000} min for that verdict)`
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