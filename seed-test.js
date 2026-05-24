#!/usr/bin/env node
/**
 * seed_test.js
 * Writes src/matches.json with kickoff times engineered so jobs fire
 * almost immediately — without touching cron or waiting for real games.
 *
 * Usage:  node seed_test.js [delay_seconds]
 *   delay_seconds: how many seconds from NOW the job should fire (default: 30)
 */

import fs from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

// 1. Конвертируем URL текущего файла (file:///home/...) в обычный системный путь
const __filename = fileURLToPath(import.meta.url);

// 2. Отрезаем имя файла, оставляя только путь к папке
const __dirname = path.dirname(__filename);

const FIRE_IN_SECONDS = Number(process.argv[2]) || 30;

// For NBA (24 min offset): kickoff must be (24*60 - FIRE_IN) seconds in the past
// For other (20 min offset): kickoff must be (20*60 - FIRE_IN) seconds in the past

const now = Date.now();

const matches = [
  {
    id:      'test-001',
    home:    'Сайгон Хит',
    away:    'Кант Ко Кетфиш',
    league:  'NBA',
    // kickoff set so: now + 24*60*1000 - kickoff = FIRE_IN * 1000
    kickoff: new Date(now - (24 * 60 * 1000) + (FIRE_IN_SECONDS * 1000)).toISOString(),
  },
  {
    id:      'test-002',
    home:    'FC Барселона',
    away:    'Реал Мадрид',
    league:  'La Liga',
    kickoff: new Date(now - (20 * 60 * 1000) + (FIRE_IN_SECONDS * 1000)).toISOString(),
  },
];

const outPath = path.join(__dirname, 'src', 'matches.json');
fs.writeFileSync(outPath, JSON.stringify(matches, null, 2));

console.log(`✓ Wrote ${matches.length} test matches to ${outPath}`);
console.log(`  Jobs will fire in ~${FIRE_IN_SECONDS} seconds after orchestrator runs.`);
matches.forEach(m =>
  console.log(`  [${m.id}] ${m.home} vs ${m.away} | kickoff: ${m.kickoff}`)
);