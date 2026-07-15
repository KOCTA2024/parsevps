'use strict';

/**
 * test_telegram.js
 *
 * Симулирует результат анализа матча (без запуска парсера/математики/OpenAI)
 * и напрямую вызывает sendTelegram(), чтобы проверить доставку уведомления.
 *
 * Запуск (важно — внутри контейнера воркера, чтобы был доступ к тем же
 * env-переменным TELEGRAM_TOKEN/TELEGRAM_KEY и тому же volume /app/state):
 *
 *   docker compose exec worker node src/test_telegram.js
 *
 * Положите файл в ту же папку, что и worker.js (src/), т.к. импорт идёт
 * относительным путём './notifiers/telegram.js' — как и в реальном worker.js.
 */

import { sendTelegram } from './notifiers/telegram.js';

// ── Фейковый результат — форма 1:1 как из openai_analyst.js ─────────────────
const fakeResult = {
  matchId:    'TEST_' + Date.now(),
  home:       'Test Team A',
  away:       'Test Team B',
  league:     'Test League',
  kickoff:    new Date(Date.now() + 3600_000).toISOString(),
  analysedAt: new Date().toISOString(),
  model:      'test-mode',
  verdict:    'PLAY',
  recommendations: [
    {
      market:    'match_total',
      line:      '215.5',
      side:      'Over',
      p_final:   0.81,
      reasoning: 'Це тестове повідомлення для перевірки доставки в Telegram.',
    },
  ],
  summary:  '🧪 Це тестовий сигнал з test_telegram.js — якщо бачиш це, доставка працює.',
  matchUrl: 'https://example.com/test-match',
};

console.log('[test] Sending fake result:', JSON.stringify(fakeResult, null, 2));

try {
  await sendTelegram(fakeResult);
  console.log('[test] sendTelegram() finished without throwing.');
  console.log('[test] Check the logs above for "Notification sent ... N/M chat(s) ok."');
} catch (e) {
  console.error('[test] sendTelegram() threw:', e);
  process.exit(1);
}