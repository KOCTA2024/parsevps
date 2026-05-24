'use strict';

/**
 * orchestrator.js
 * Reads src/matches.json and pushes idempotent delayed jobs into the BullMQ queue.
 *
 * Usage:  node src/orchestrator.js
 */
import path from 'path';
import fs from 'fs';                  // sync fs — needed for existsSync / readFileSync
import { Queue } from 'bullmq';
import { fileURLToPath } from 'url';
import { slugify } from './utils/slugify.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Config ──────────────────────────────────────────────────────────────────

const REDIS_CONFIG = {
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: Number(process.env.REDIS_PORT) || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
};

const QUEUE_NAME   = 'match-analysis';
const MATCHES_FILE = process.env.MATCHES_FILE
  ? path.resolve(process.env.MATCHES_FILE)
  : path.resolve(__dirname, 'matches.json');

// Leagues that use 12-minute quarters → target half-time ≈ kickoff + 24 min
const NBA_PATTERN  = /NBA|12[\s-]?min/i;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Build the expected output filename that math_script.py will look for.
 * Convention (adjust if your parser writes differently):
 *   data/<home_slug>_vs_<away_slug>_<matchId>.json
 */
function buildDataFilename(match) {
  const home = slugify(match.home);
  const away = slugify(match.away);
  return `${home}_vs_${away}_${match.id}.json`;
}

/**
 * Compute the BullMQ `delay` (ms from *now*) so the job fires at half-time.
 * kickoff is expected as an ISO-8601 string or Unix timestamp (ms/s).
 */
function computeDelay(kickoff, league) {
  const kickoffMs =
    typeof kickoff === 'number'
      ? kickoff < 1e12 ? kickoff * 1000 : kickoff   // handle seconds vs ms
      : new Date(kickoff).getTime();

  if (isNaN(kickoffMs)) {
    throw new Error(`Invalid kickoff value: ${kickoff}`);
  }

  const offsetMinutes = NBA_PATTERN.test(league || '') ? 24 : 20;
  const fireAt        = kickoffMs + offsetMinutes * 60 * 1000;
  const delayMs       = fireAt - Date.now();

  return { delayMs, fireAt: new Date(fireAt).toISOString(), offsetMinutes };
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  // 1. Load matches
  if (!fs.existsSync(MATCHES_FILE)) {
    console.error(`[orchestrator] matches.json not found at ${MATCHES_FILE}`);
    process.exit(1);
  }

  let matches;
  try {
    matches = JSON.parse(fs.readFileSync(MATCHES_FILE, 'utf8'));
  } catch (err) {
    console.error('[orchestrator] Failed to parse matches.json:', err.message);
    process.exit(1);
  }

  // monitor.js writes { fetchedAt, fsign, count, matches: [...] }
  if (matches && !Array.isArray(matches) && Array.isArray(matches.matches)) {
    matches = matches.matches;
  }

  if (!Array.isArray(matches) || matches.length === 0) {
    console.log('[orchestrator] No matches to schedule. Exiting.');
    process.exit(0);
  }

  // 2. Connect to queue
  const queue = new Queue(QUEUE_NAME, {
    connection: REDIS_CONFIG,
    defaultJobOptions: {
      attempts: 3,
      backoff: { type: 'exponential', delay: 60_000 },   // retry after 1 min, 2 min, 4 min
      removeOnComplete: { age: 86_400 },                  // keep completed jobs 24 h
      removeOnFail:     { age: 7 * 86_400 },              // keep failed jobs 7 days
    },
  });

  // 3. Seed jobs
  let seeded = 0;
  let skipped = 0;

  for (const match of matches) {
    const { id, home, away, kickoff, league } = match;

    if (!id || !kickoff) {
      console.warn('[orchestrator] Skipping match with missing id or kickoff:', match);
      skipped++;
      continue;
    }

    let delayMs, fireAt, offsetMinutes;
    try {
      ({ delayMs, fireAt, offsetMinutes } = computeDelay(kickoff, league));
    } catch (err) {
      console.warn(`[orchestrator] Skipping match ${id} – ${err.message}`);
      skipped++;
      continue;
    }

    if (delayMs < 0) {
      console.warn(
        `[orchestrator] Match ${id} half-time is in the past (${fireAt}). Skipping.`
      );
      skipped++;
      continue;
    }

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
      fireAt,
    };

    try {
      // jobId = matchId → BullMQ ignores duplicates with the same jobId
      await queue.add('analyse', jobPayload, {
        jobId: String(id),
        delay: delayMs,
      });

      console.log(
        `[orchestrator] ✓ Queued match ${id}` +
        ` (${home} vs ${away}) → fires at ${fireAt}` +
        ` (+${offsetMinutes} min offset)`
      );
      seeded++;
    } catch (err) {
      // BullMQ throws on duplicate jobId with a specific message; treat as skip
      if (/Job already exists/.test(err.message)) {
        console.log(`[orchestrator] ↷  Match ${id} already in queue. Skipped (idempotent).`);
        skipped++;
      } else {
        console.error(`[orchestrator] ✗ Failed to queue match ${id}:`, err.message);
        skipped++;
      }
    }
  }

  console.log(
    `\n[orchestrator] Done. Seeded: ${seeded}, Skipped: ${skipped}, Total: ${matches.length}`
  );

  await queue.close();
  process.exit(0);
}

main().catch(err => {
  console.error('[orchestrator] Fatal error:', err);
  process.exit(1);
});