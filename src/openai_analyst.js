'use strict';

/**
 * openai_analyst.js
 * Step 3 in the worker chain: reads match data files and runs OpenAI analysis.
 *
 * Public API:
 *   analyseMatch(jobData, dataFilePath, lineFilePath) → Promise<AnalysisResult>
 *
 * Env vars:
 *   OPENAI_API_KEY        — required
 *   OPENAI_MODEL          — default: gpt-4.5-preview
 *   OPENAI_MAX_TOKENS     — default: 4000
 *   OPENAI_TIMEOUT_MS     — default: 120000 (2 min)
 *   ANALYSIS_OUTPUT_DIR   — default: <APP_ROOT>/data
 *   PROMPT_FILE           — path to .docx prompt file
 *                           default: <APP_ROOT>/src/prompts/basketball_master_unified_prompt_v3_1_uk_fixed.docx
 */

import fs    from 'fs';
import path  from 'path';
import https from 'https';
import { fileURLToPath }    from 'url';
import { createRequire }    from 'module';

// mammoth is a CommonJS package — import via createRequire
const require  = createRequire(import.meta.url);
const mammoth  = require('mammoth');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT  = path.resolve(__dirname, '..');

// ─── Config ──────────────────────────────────────────────────────────────────

const OPENAI_API_KEY    = process.env.OPENAI_API_KEY;
const OPENAI_MODEL      = process.env.OPENAI_MODEL      || 'gpt-5.4';
const OPENAI_MAX_TOKENS       = Number(process.env.OPENAI_MAX_TOKENS)       || 32_000;
const OPENAI_REASONING_EFFORT = process.env.OPENAI_REASONING_EFFORT || 'low'; // none | low | medium | high | xhigh
const OPENAI_TIMEOUT_MS = Number(process.env.OPENAI_TIMEOUT_MS) || 180_000;
const OUTPUT_DIR        = process.env.ANALYSIS_OUTPUT_DIR
  ? path.resolve(process.env.ANALYSIS_OUTPUT_DIR)
  : path.join(APP_ROOT, 'data');

const PROMPT_FILE = process.env.PROMPT_FILE
  ? path.resolve(process.env.PROMPT_FILE)
  : path.join(
      APP_ROOT,
      'src', 'prompts',
      'basketball_master_unified_prompt_v3_1_uk_fixed.docx'
    );

// ─── JSON output instruction appended to the prompt ──────────────────────────
// Kept separate so the .docx stays clean and you can update it without
// touching this instruction block.

const JSON_OUTPUT_INSTRUCTION = `

---
ВАЖЛИВО: відповідь має бути ТІЛЬКИ у форматі JSON (без markdown-обгортки, без \`\`\`):
{
  "verdict": "PLAY|STRONG PLAY|PASS|CONFLICT",
  "recommendations": [
    {
      "market": "...",
      "line": "...",
      "side": "...",
      "p_final": 0.00,
      "reasoning": "..."
    }
  ],
  "data_quality": {
    "sample_a": null,
    "sample_b": null,
    "pooled": null,
    "h2h": null,
    "stat_support": "ON|OFF|unknown",
    "missing_fields": []
  },
  "live_projection": {
    "team_a": null,
    "team_b": null,
    "total": null,
    "margin": null
  },
  "p_final_table": [
    {
      "rank": 1,
      "market_side": "...",
      "p_hist": null,
      "p_scenario": null,
      "p_live_used": null,
      "weights": "...",
      "p_raw": null,
      "caps_blockers": "...",
      "p_final": null,
      "verdict": "PLAY|PASS"
    }
  ],
  "summary": "Короткий текст для читання людиною (1–5 речень)"
}`;

// ─── Load system prompt from .docx at startup ─────────────────────────────────

async function loadSystemPrompt() {
  if (!fs.existsSync(PROMPT_FILE)) {
    throw new Error(
      `[openai_analyst] Prompt file not found: ${PROMPT_FILE}\n` +
      `Put the .docx file there or set PROMPT_FILE env var.`
    );
  }

  const { value: text, messages } = await mammoth.extractRawText({ path: PROMPT_FILE });

  if (messages && messages.length > 0) {
    for (const m of messages) {
      if (m.type === 'error') {
        console.warn(`[openai_analyst] mammoth warning: ${m.message}`);
      }
    }
  }

  if (!text || text.trim().length < 100) {
    throw new Error(`[openai_analyst] Extracted prompt is suspiciously short. Check the .docx file.`);
  }

  return text.trim() + JSON_OUTPUT_INSTRUCTION;
}

// Cached at module level — loaded once on first analyseMatch() call
let _systemPromptCache = null;

async function getSystemPrompt() {
  if (!_systemPromptCache) {
    _systemPromptCache = await loadSystemPrompt();
    console.log(
      `[openai_analyst] Prompt loaded from ${PROMPT_FILE} ` +
      `(${_systemPromptCache.length} chars)`
    );
  }
  return _systemPromptCache;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function readFileSafe(filePath) {
  try {
    if (fs.existsSync(filePath)) return fs.readFileSync(filePath, 'utf8');
  } catch (_) {}
  return null;
}

function openaiRequest(payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);

    const req = https.request(
      {
        hostname: 'api.openai.com',
        path:     '/v1/chat/completions',
        method:   'POST',
        headers: {
          'Content-Type':   'application/json',
          'Authorization':  `Bearer ${OPENAI_API_KEY}`,
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          try {
            const text = Buffer.concat(chunks).toString('utf8');
            const json = JSON.parse(text);
            if (res.statusCode >= 400) {
              reject(new Error(`OpenAI API error ${res.statusCode}: ${json.error?.message ?? text}`));
            } else {
              resolve(json);
            }
          } catch (e) {
            reject(new Error(`OpenAI response parse error: ${e.message}`));
          }
        });
      }
    );

    const timer = setTimeout(
      () => req.destroy(new Error(`OpenAI request timed out after ${timeoutMs}ms`)),
      timeoutMs
    );

    req.on('error', e => { clearTimeout(timer); reject(e); });
    req.on('close', ()  => clearTimeout(timer));
    req.write(body);
    req.end();
  });
}

function parseModelReply(text) {
  const stripped = text
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  return JSON.parse(stripped);
}

// ─── Notification hook ────────────────────────────────────────────────────────

let _notifier = async (_result) => {};

export function setNotifier(fn) {
  _notifier = fn;
}

/**
 * Reload prompt cache (useful if you updated the .docx without restarting).
 */
export function reloadPrompt() {
  _systemPromptCache = null;
  console.log('[openai_analyst] Prompt cache cleared — will reload on next call.');
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * analyseMatch
 *
 * @param {object}   jobData      — job payload from worker
 * @param {string}   dataFilePath — path to data/<home>_vs_<away>_<id>.json
 * @param {string}   lineFilePath — path to data/line_result_<id>.json
 * @param {object}   [options]
 * @param {Function} [options.notifier] — per-call notifier override
 * @returns {Promise<object>}
 */
export async function analyseMatch(jobData, dataFilePath, lineFilePath, options = {}) {
  const { matchId, home, away, league, kickoff } = jobData;
  const notifier = options.notifier ?? _notifier;

  if (!OPENAI_API_KEY) throw new Error('[openai_analyst] OPENAI_API_KEY is not set.');

  // ── Load prompt ───────────────────────────────────────────────────────────
  const systemPrompt = await getSystemPrompt();

  // ── Assemble match context ────────────────────────────────────────────────
  const dataRaw = readFileSafe(dataFilePath);
  const lineRaw = readFileSafe(lineFilePath);

  if (!dataRaw && !lineRaw) {
    throw new Error(`[openai_analyst] Both data files missing for match ${matchId}.`);
  }

  const userMessage = [
    `Матч: ${home} vs ${away}`,
    `Ліга: ${league || 'невідома'}`,
    `Кік-оф: ${kickoff || 'невідомий'}`,
    '',
    dataRaw
      ? `=== Дані матчу (h2h / статистика) ===\n${dataRaw}`
      : '=== Дані матчу: файл відсутній ===',
    '',
    lineRaw
      ? `=== Результати математичного розрахунку (math_script) ===\n${lineRaw}`
      : '=== Результати math_script: файл відсутній ===',
  ].join('\n');

  // ── Call OpenAI ───────────────────────────────────────────────────────────
  const payload = {
    model:       OPENAI_MODEL,
    max_tokens:       OPENAI_MAX_TOKENS,
    reasoning: { effort: OPENAI_REASONING_EFFORT },
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user',   content: userMessage  },
    ],
  };

  let rawReply;
  try {
    const response = await openaiRequest(payload, OPENAI_TIMEOUT_MS);
    rawReply = response.choices?.[0]?.message?.content ?? '';
  } catch (err) {
    throw new Error(`[openai_analyst] OpenAI call failed for match ${matchId}: ${err.message}`);
  }

  // ── Parse reply ───────────────────────────────────────────────────────────
  let parsed;
  try {
    parsed = parseModelReply(rawReply);
  } catch (_) {
    parsed = {
      verdict:         'PARSE_ERROR',
      recommendations: [],
      summary:         rawReply.slice(0, 500),
      _raw:            rawReply,
    };
  }

  // ── Build & persist result ────────────────────────────────────────────────
  const result = {
    matchId,
    home,
    away,
    league:     league || '',
    kickoff:    kickoff || null,
    analysedAt: new Date().toISOString(),
    model:      OPENAI_MODEL,
    ...parsed,
  };

  const outPath = path.join(OUTPUT_DIR, `analysis_${matchId}.json`);
  try {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2), 'utf8');
  } catch (e) {
    console.error(`[openai_analyst] Could not write ${outPath}:`, e.message);
  }

  // ── Notify ────────────────────────────────────────────────────────────────
  try {
    await notifier(result);
  } catch (e) {
    console.error('[openai_analyst] Notifier threw:', e.message);
  }

  return result;
}