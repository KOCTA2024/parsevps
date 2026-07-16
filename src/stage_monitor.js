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
import { Queue } from 'bullmq';
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

let queue;

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
    await queue.add('analyse', jobPayload, { jobId, delay: 0 });
    log(id, `✓ Break #${checkpointIndex + 1} detected → queued analysis (jobId=${jobId})`);
  } catch (err) {
    if (/Job already exists/.test(err.message)) {
      log(id, `↷ Break #${checkpointIndex + 1} already queued (jobId=${jobId}). Skipped (idempotent).`);
    } else {
      console.error(`[stage-monitor] ✗ Failed to queue ${jobId}:`, err.message);
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
      return;
    }

    if (stage.status === 'break') {
      stopped = true;
      clearInterval(timer);
      await enqueueAnalysis(match, checkpointIndex);
    }
    // 'live' / 'not_started' / 'unknown' → просто чекаємо наступного тіку
  }, POLL_INTERVAL_MS);
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
    setTimeout(() => watchCheckpoint(match, idx, sportId), delay);
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

  rescan();
  setInterval(rescan, RESCAN_INTERVAL_MS);

  console.log(
    `[stage-monitor] Started. Watching "${MATCHES_FILE}" | ` +
    `poll every ${POLL_INTERVAL_MS / 1000}s | check window ${CHECK_WINDOW_MS / 60_000} min | ` +
    `rescanning matches.json every ${RESCAN_INTERVAL_MS / 60_000} min`
  );
}

async function shutdown(signal) {
  console.log(`\n[stage-monitor] ${signal} — shutting down…`);
  try { await matchStageChecker.close(); } catch {}
  try { await queue?.close(); } catch {}
  console.log('[stage-monitor] Shutdown complete.');
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

main().catch(err => {
  console.error('[stage-monitor] Fatal error:', err);
  process.exit(1);
});
