'use strict';

/**
 * match_stage.js
 *
 * Легка перевірка поточної стадії матчу — БЕЗ повного парсингу, який виконує
 * src/match_h2h_export.js (без df_hh, без архівів сезонів, без h2h/статистики).
 * Призначення: дати відповідь "матч зараз у перерві чи ні" якомога дешевше,
 * щоб можна було опитувати десятки матчів раз на хвилину, не кладучи ВПС.
 *
 * Механіка — та сама, що й в основному парсері:
 *   1. Playwright відкриває сторінку матчу й перехоплює заголовок x-fsign
 *      з мережевого запиту до flashscore.ninja (це "токен" для прямих
 *      HTTP-запитів до фідів, без подальшого рендерингу сторінки).
 *   2. Стадія матчу читається з фіда dc_<sportId>_<matchId> за полями DA/DI:
 *        DA=1  → матч ще не почався
 *        DA=2  → матч живий
 *        DA=3  → матч завершено
 *        DI=-1 → (при DA=2) годинник зупинено = ПЕРЕРВА
 *        DI>=0 → (при DA=2) матч іде, DI = поточна хвилина
 *
 * ВАЖЛИВО: на відміну від match_h2h_export.js, тут НЕ перевіряється наявність
 * рахунку (homeScore/awayScore) для відрізнення "перерва" від "ще не почалось",
 * бо stage_monitor.js починає опитування вже через 10+ хвилин після старту
 * матчу — на цей момент матч точно вже стартував, і DA=2+DI=-1 однозначно
 * означає перерву.
 *
 * fsign кешується на FSIGN_TTL_MS, щоб не відкривати сторінку в Playwright
 * на кожну хвилинну перевірку — рефрешиться лише коли протух або фід
 * повернув щось незрозуміле.
 */

import { chromium } from 'playwright';
import https from 'https';
import zlib from 'zlib';

const FSIGN_TTL_MS              = Number(process.env.STAGE_FSIGN_TTL_MS) || 5 * 60_000; // 5 хв
const FSIGN_CAPTURE_TIMEOUT_MS  = Number(process.env.STAGE_FSIGN_CAPTURE_TIMEOUT_MS) || 20_000;
const FEED_PREFIX_DEFAULT       = '35';

// ─── KV / feed helpers (той самий формат, що й у match_h2h_export.js) ───────

function parseKV(text) {
  const obj = {};
  if (!text) return obj;
  for (const pair of text.split('¬')) {
    const sep = pair.indexOf('÷');
    if (sep > 0) obj[pair.slice(0, sep)] = pair.slice(sep + 1);
  }
  return obj;
}

function fetchFeed(feedName, fsign, prefix = FEED_PREFIX_DEFAULT) {
  const url = `https://${prefix}.flashscore.ninja/${prefix}/x/feed/${feedName}`;
  const agent = new https.Agent({ keepAlive: false });
  return new Promise((resolve, reject) => {
    https.get(url, {
      agent,
      headers: {
        'x-fsign'        : fsign,
        'Referer'        : 'https://www.flashscore.ua/',
        'User-Agent'     : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept'         : '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin'         : 'https://www.flashscore.ua',
      },
    }, res => {
      const enc = res.headers['content-encoding'];
      let stream = res;
      if (enc === 'gzip')         stream = res.pipe(zlib.createGunzip());
      else if (enc === 'deflate') stream = res.pipe(zlib.createInflate());
      else if (enc === 'br')      stream = res.pipe(zlib.createBrotliDecompress());
      const chunks = [];
      stream.on('data',  c => chunks.push(c));
      stream.on('end',   () => resolve(Buffer.concat(chunks).toString('utf-8')));
      stream.on('error', reject);
    }).on('error', reject);
  });
}

/**
 * status: 'not_started' | 'live' | 'break' | 'finished' | 'unknown'
 */
function parseDaDi(rawDc) {
  if (!rawDc || rawDc.length < 3) return { status: 'unknown', liveMinute: null };
  const kv = parseKV(rawDc.split('~')[0]);

  const da = kv['DA'];
  if (da === '3') return { status: 'finished',     liveMinute: null };
  if (da === '1') return { status: 'not_started',  liveMinute: null };

  const di = kv['DI'];
  if (di === undefined) return { status: 'unknown', liveMinute: null };

  const diNum = Number(di);
  if (diNum === -1) return { status: 'break', liveMinute: null };
  return { status: 'live', liveMinute: diNum };
}

// ─── Stage checker ───────────────────────────────────────────────────────────

class MatchStageChecker {
  constructor() {
    this._browser    = null;
    this._context    = null;
    this._launching  = null;
    this._fsignCache = new Map(); // matchId -> { fsign, prefix, ts }
  }

  async _ensureContext() {
    if (this._context) return this._context;
    if (this._launching) return this._launching;

    this._launching = (async () => {
      this._browser = await chromium.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
          '--no-zygote',
          '--single-process',
          '--disable-extensions',
          '--blink-settings=imagesEnabled=false',
          '--disable-application-cache',
          '--disable-cache',
        ],
      });
      this._context = await this._browser.newContext();
      return this._context;
    })();

    const ctx = await this._launching;
    this._launching = null;
    return ctx;
  }

  /**
   * Відкриває легку сторінку матчу лише щоб перехопити x-fsign.
   * НЕ чекає на detailScore/DOM-статус, НЕ ходить за архівами/h2h —
   * це і робить перевірку набагато дешевшою за повний парсер.
   */
  async _captureFsign(matchId) {
    const context = await this._ensureContext();
    const page = await context.newPage();
    let fsign = '', prefix = FEED_PREFIX_DEFAULT;

    let resolveFsign;
    const fsignPromise = new Promise(res => { resolveFsign = res; });
    const handler = req => {
      const h = req.headers();
      if (h['x-fsign'] && req.url().includes('flashscore.ninja')) {
        const m = req.url().match(/https:\/\/(\d+)\.flashscore\.ninja/);
        resolveFsign({ fsign: h['x-fsign'], prefix: (m && m[1].length > 1) ? m[1] : FEED_PREFIX_DEFAULT });
      }
    };
    context.on('request', handler);

    try {
      await page.goto(`https://www.flashscore.ua/match/${matchId}/#/h2h/overall`, {
        waitUntil: 'domcontentloaded',
        timeout: 30_000,
      });

      const captured = await Promise.race([
        fsignPromise,
        new Promise((_, reject) => setTimeout(() => reject(new Error('fsign timeout')), FSIGN_CAPTURE_TIMEOUT_MS)),
      ]).catch(() => null);

      if (captured) { fsign = captured.fsign; prefix = captured.prefix; }
    } finally {
      context.off('request', handler);
      await page.close().catch(() => {});
    }

    return { fsign, prefix };
  }

  async _getFsign(matchId, { forceRefresh = false } = {}) {
    const cached = this._fsignCache.get(matchId);
    const isFresh = cached && (Date.now() - cached.ts) < FSIGN_TTL_MS;
    if (cached && isFresh && !forceRefresh) return cached;

    const captured = await this._captureFsign(matchId);
    if (!captured.fsign) {
      if (cached) return cached; // краще протухлий fsign, ніж жоден — спробуємо
      throw new Error(`Could not capture fsign for match ${matchId}`);
    }

    const entry = { ...captured, ts: Date.now() };
    this._fsignCache.set(matchId, entry);
    return entry;
  }

  /**
   * Повертає поточну стадію матчу.
   * @returns {Promise<{status: 'not_started'|'live'|'break'|'finished'|'unknown', liveMinute: number|null}>}
   */
  async checkStage(matchId, sportId = '5') {
    let { fsign, prefix } = await this._getFsign(matchId);
    let raw = await fetchFeed(`dc_${sportId}_${matchId}`, fsign, prefix).catch(() => '');
    let result = parseDaDi(raw);

    // Фід порожній/незрозумілий → fsign, ймовірно, протух. Пробуємо оновити один раз.
    if (result.status === 'unknown') {
      ({ fsign, prefix } = await this._getFsign(matchId, { forceRefresh: true }));
      raw = await fetchFeed(`dc_${sportId}_${matchId}`, fsign, prefix).catch(() => '');
      result = parseDaDi(raw);
    }

    return result;
  }

  async close() {
    try { await this._context?.close(); } catch {}
    try { await this._browser?.close(); } catch {}
    this._context = null;
    this._browser = null;
    this._fsignCache.clear();
  }
}

// Єдиний спільний інстанс на процес — щоб усі матчі перевірялись через
// один і той самий Chromium-браузер, а не плодили по процесу на матч.
export const matchStageChecker = new MatchStageChecker();
export { MatchStageChecker, parseDaDi };
