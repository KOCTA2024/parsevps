'use strict';

/**
 * notifiers/telegram.js
 *
 * Підключення:
 *   1. Встанови змінні середовища:
 *        TELEGRAM_BOT_TOKEN=123456:ABC...
 *        TELEGRAM_CHAT_ID=-100123456789   (канал/група/особистий чат)
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
 *
 * Для складніших повідомлень (таблиці, кнопки) — розшир formatMessage().
 */

import https from 'https';

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID   = process.env.TELEGRAM_CHAT_ID;

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
  const { home, away, league, verdict, recommendations, summary, analysedAt } = result;

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

  const ts = analysedAt ? new Date(analysedAt).toLocaleString('uk-UA', { timeZone: 'Europe/Kyiv' }) : '';
  if (ts) lines.push(`\n🕐 ${ts}`);

  return lines.filter(l => l !== '').join('\n');
}

// ─── Sender ───────────────────────────────────────────────────────────────────

function telegramRequest(payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const req = https.request(
      {
        hostname: 'api.telegram.org',
        path:     `/bot${BOT_TOKEN}/sendMessage`,
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
            if (!json.ok) reject(new Error(`Telegram error: ${json.description}`));
            else resolve(json);
          } catch (e) {
            reject(new Error(`Telegram parse error: ${e.message}`));
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
 * Мовчить (тільки логує попередження) якщо токен/чат не налаштовані,
 * щоб не падав worker при відсутності змінних.
 */
export async function sendTelegram(result) {
  if (!BOT_TOKEN || !CHAT_ID) {
    console.warn('[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification.');
    return;
  }

  // Надсилаємо тільки якщо є реальна рекомендація
  const verdict = (result.verdict ?? '').toUpperCase();
  if (!verdict.includes('PLAY')) {
    console.log(`[telegram] Verdict is ${verdict} — no notification sent.`);
    return;
  }

  const text = formatMessage(result);

  await telegramRequest({
    chat_id:    CHAT_ID,
    text,
    parse_mode: 'HTML',
    // Відключаємо preview посилань щоб не захаращувати чат
    disable_web_page_preview: true,
  });

  console.log(`[telegram] Notification sent for match ${result.matchId}`);
}