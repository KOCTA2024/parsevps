'use strict';

/**
 * notifiers/telegram.js
 *
 * Підключення:
 *   1. Встанови змінну середовища з токеном бота (будь-яка з двох назв підійде):
 *        TELEGRAM_TOKEN=123456:ABC...
 *        TELEGRAM_KEY=123456:ABC...
 *
 *      CHAT_ID більше НЕ потрібен — бот сам розсилає повідомлення всім,
 *      хто хоч раз йому щось написав (наприклад /start). Список чатів
 *      зберігається в state/telegram_chats.json (том, який вже
 *      примонтований у сервіс worker як /app/state).
 *
 *   2. В worker.js розкоментуй:
 *        import { sendTelegram } from './notifiers/telegram.js';
 *        setNotifier(sendTelegram);
 *
 * Формат повідомлення:
 *   ⚽ Манчестер Юнайтед vs Ліверпуль
 *   🏆 Premier League
 *   📊 Вердикт: PLAY
 *   ✅ Total Over 215.5 — P_final: 81%
 *      "Pooled70 over = 58/70, live projection 218.3, P_live 79%..."
 *   🔗 Матч (якщо в result є поле matchUrl)
 *
 * Надсилається все, КРІМ вердикту ERROR (тобто PLAY / STRONG PLAY / PASS /
 * CONFLICT — все летить у Telegram).
 *
 * Для складніших повідомлень (таблиці, кнопки) — розшир formatMessage().
 */

import https from 'https';
import fs from 'fs';
import path from 'path';

const BOT_TOKEN = process.env.TELEGRAM_TOKEN || process.env.TELEGRAM_KEY;

const STATE_DIR   = process.env.TELEGRAM_STATE_DIR || '/app/state';
const CHATS_FILE  = path.join(STATE_DIR, 'telegram_chats.json');

// ─── Chat registry (хто писав боту) ───────────────────────────────────────────

function loadState() {
  try {
    const raw = fs.readFileSync(CHATS_FILE, 'utf8');
    const parsed = JSON.parse(raw);
    return {
      offset:  Number(parsed.offset) || 0,
      chatIds: Array.isArray(parsed.chatIds) ? parsed.chatIds : [],
    };
  } catch {
    return { offset: 0, chatIds: [] };
  }
}

function saveState(state) {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(CHATS_FILE, JSON.stringify(state, null, 2));
  } catch (e) {
    console.warn('[telegram] Could not persist chats file:', e.message);
  }
}

/**
 * Підтягує нові апдейти від Telegram і поповнює список відомих чатів.
 * Викликається перед кожною розсилкою — окремого long-polling процесу не потрібно.
 */
async function syncKnownChats() {
  const state = loadState();

  let updates;
  try {
    updates = await apiRequest('getUpdates', { offset: state.offset, timeout: 0 });
  } catch (e) {
    console.warn('[telegram] getUpdates failed:', e.message);
    return state.chatIds;
  }

  let changed = false;
  for (const update of updates) {
    state.offset = Math.max(state.offset, update.update_id + 1);
    const chat =
      update.message?.chat ||
      update.channel_post?.chat ||
      update.my_chat_member?.chat;

    if (chat && !state.chatIds.includes(chat.id)) {
      state.chatIds.push(chat.id);
      changed = true;
      const label = chat.title || chat.username || chat.first_name || chat.id;
      console.log(`[telegram] New chat registered: ${chat.id} (${label})`);
    }
  }

  if (changed || updates.length > 0) saveState(state);
  return state.chatIds;
}

// ─── Formatter ────────────────────────────────────────────────────────────────

function emoji(verdict) {
  if (!verdict) return '❓';
  const v = verdict.toUpperCase();
  if (v.includes('STRONG')) return '🔥';
  if (v.includes('PLAY'))   return '✅';
  if (v.includes('PASS'))   return '⏸';
  if (v.includes('CONFLICT')) return '⚠️';
  return '❓';
}

function formatMessage(result) {
  const { home, away, league, verdict, recommendations, summary, analysedAt, matchUrl } = result;

  const lines = [
    `🏀 <b>${home} vs ${away}</b>`,
    league ? `🏆 ${league}` : '',
    ``,
    `${emoji(verdict)} Вердикт: <b>${verdict}</b>`,
  ];

  if (Array.isArray(recommendations) && recommendations.length > 0) {
    for (const rec of recommendations) {
      const pct = rec.p_final != null
        ? ` — P_final: <b>${Math.round(rec.p_final * 100)}%</b>`
        : '';
      lines.push(`\n📌 ${rec.market} ${rec.line} <i>${rec.side}</i>${pct}`);
      if (rec.reasoning) {
        // Trim to keep TG message readable
        lines.push(`   <i>${rec.reasoning.slice(0, 200)}</i>`);
      }
    }
  }

  if (summary) {
    lines.push(`\n💬 ${summary}`);
  }

  if (matchUrl) {
    lines.push(`\n🔗 <a href="${matchUrl}">Матч</a>`);
  }

  const ts = analysedAt ? new Date(analysedAt).toLocaleString('uk-UA', { timeZone: 'Europe/Kyiv' }) : '';
  if (ts) lines.push(`\n🕐 ${ts}`);

  return lines.filter(l => l !== '').join('\n');
}

// ─── Sender ───────────────────────────────────────────────────────────────────

function apiRequest(method, payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const req = https.request(
      {
        hostname: 'api.telegram.org',
        path:     `/bot${BOT_TOKEN}/${method}`,
        method:   'POST',
        headers: {
          'Content-Type':   'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          try {
            const json = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if (!json.ok) reject(new Error(`Telegram error (${method}): ${json.description}`));
            else resolve(json.result);
          } catch (e) {
            reject(new Error(`Telegram parse error (${method}): ${e.message}`));
          }
        });
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ─── Public ───────────────────────────────────────────────────────────────────

/**
 * sendTelegram — notifier функція, передається в setNotifier().
 *
 * Мовчить (тільки логує попередження) якщо токен не налаштований,
 * щоб не падав worker при відсутності змінних.
 *
 * Надсилає повідомлення всім чатам, які коли-небудь писали боту.
 * Не надсилає нічого лише якщо verdict === ERROR (все інше — PLAY,
 * STRONG PLAY, PASS, CONFLICT — розсилається).
 */
export async function sendTelegram(result) {
  if (!BOT_TOKEN) {
    console.warn('[telegram] TELEGRAM_TOKEN/TELEGRAM_KEY not set — skipping notification.');
    return;
  }

  const verdict = (result.verdict ?? '').toUpperCase();
  if (!verdict || verdict.includes('ERROR')) {
    console.log(`[telegram] Verdict is "${verdict || 'empty'}" — no notification sent.`);
    return;
  }

  const chatIds = await syncKnownChats();
  if (chatIds.length === 0) {
    console.warn('[telegram] No known chats yet — nobody has messaged the bot. Skipping.');
    return;
  }

  const text = formatMessage(result);

  const outcomes = await Promise.allSettled(
    chatIds.map(chatId =>
      apiRequest('sendMessage', {
        chat_id: chatId,
        text,
        parse_mode: 'HTML',
        // Відключаємо preview посилань щоб не захаращувати чат
        disable_web_page_preview: true,
      })
    )
  );

  const failed = outcomes.filter(o => o.status === 'rejected');
  console.log(
    `[telegram] Notification sent for match ${result.matchId} — ` +
    `${outcomes.length - failed.length}/${outcomes.length} chat(s) ok.`
  );
  failed.forEach(f => console.warn('[telegram] send failed:', f.reason.message));
}