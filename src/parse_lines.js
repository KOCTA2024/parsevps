/**
 * parse_lines.js  (betking.com.ua edition)
 * ─────────────────────────────────────────────────────────────────────────────
 * Scrapes bookmaker lines for a live basketball match from betking.com.ua.
 *
 * Key constraints:
 *  • Reuses the existing Playwright BrowserContext from match_h2h_export.js —
 *    NO second Chromium launch.
 *  • Opens / closes a single new page per call.
 *  • Must finish in < 25 s on a single-core 1 GB VPS.
 *  • Output format is compatible with the original line_result_<matchId>.json.
 *
 * FIX: Content is rendered inside Shadow DOM of <swiper-slide> Web Components.
 *      All scraping now uses page.evaluate() with recursive Shadow DOM traversal.
 *
 * FIX 2: Team name matching — Cyrillic → Latin transliteration + alias dictionary
 *         so teams like "Ферро" → "ferro", "Хімнасія" → "gimnasia" are found.
 *         Also dumps all lobby card names when match is not found (debug).
 *
 * Call site in match_h2h_export.js:
 *   await fetchAndSaveLines(
 *     matchId, DATA_DIR, participants, lineFilename,
 *     homeName, awayName,
 *     mainContext, liveStatus, isPrematch
 *   );
 */

import fs   from 'fs';
import path from 'path';

// ─── Constants ────────────────────────────────────────────────────────────────

const LOBBY_URL   = 'https://betking.com.ua/sports-book/?page=sport&sportId=67';
const NAV_TIMEOUT = 120_000;

// ─── Cyrillic → Latin transliteration table ───────────────────────────────────
// Ukrainian/Russian sports names transliterated to match betking's Latin display.

const TRANSLIT_MAP = {
  'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','є':'ye','ж':'zh',
  'з':'z','и':'i','і':'i','ї':'yi','й':'y','к':'k','л':'l','м':'m','н':'n',
  'о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
  'ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'yu','я':'ya',
  // Russian extras
  'ё':'yo','э':'e','ъ':'','ы':'y',
};

/**
 * Convert a Cyrillic string to lowercase Latin.
 * "Ферро" → "ferro", "Хімнасія" → "khimnasiya"
 */
function transliterate(str) {
  return str.toLowerCase().split('').map(ch => TRANSLIT_MAP[ch] ?? ch).join('');
}

function normalizeTeamName(str) {
  return String(str ?? '')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[’'`]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// ─── Known alias dictionary ───────────────────────────────────────────────────
// Key: Cyrillic display name (lowercase). Values: array of Latin substrings
// that betking may use. Add new teams as you encounter them.
//
// Rule: any value that is a substring of the betking card name (or vice-versa)
// is considered a match.

const TEAM_ALIASES = {
  // Argentina LNB
  'ферро':        ['ferro', 'ferro carril', 'ferrocarril'],
  'хімнасія':     ['gimnasia', 'gimnasia lp', 'gimn', 'himnasia'],
  'пеньяроль':    ['penarol', 'peñarol', 'penyarol'],
  'сан мартін':   ['san martin'],
  'сан лоренсо':  ['san lorenzo'],
  'атлетіко':     ['atletico', 'atlético'],
  'бока хуніорс': ['boca', 'boca juniors'],
  'рівер плейт':  ['river', 'river plate'],
  'реал мадрид':  ['real madrid'],
  'барселона':    ['barcelona', 'barça'],
  'фенербахче':   ['fenerbahce', 'fenerbahçe'],
  // Add more as needed …
};

/**
 * Build all search variants for a team name:
 *   1. The original string (lowercased) — covers Latin names passed directly
 *   2. Transliterated version
 *   3. Dictionary aliases (if any)
 * Returns array of unique lowercase strings.
 */
function teamVariants(name) {
  const rawOrig = name.toLowerCase().trim();
  const orig   = normalizeTeamName(rawOrig);
  const translit = transliterate(orig);
  const aliases  = TEAM_ALIASES[rawOrig] ?? TEAM_ALIASES[orig] ?? [];
  return [...new Set([orig, translit, ...aliases.map(normalizeTeamName)].filter(Boolean))];
}

/**
 * Returns true if any variant from setA overlaps with any variant from setB
 * using substring containment (either direction).
 */
function variantsMatch(variantsA, variantsB) {
  for (const a of variantsA)
    for (const b of variantsB)
      if (a.includes(b) || b.includes(a)) return true;
  return false;
}

// ─── Market-selection by game moment ─────────────────────────────────────────
// Always returns empty Set → scrape ALL available markets regardless of game period.
// The downstream Python analysis decides which lines it uses.

function marketsForStatus(_liveStatus = '') {
  return new Set();   // empty = scrape everything, always
}

// ─── Market name → category ───────────────────────────────────────────────────

function inferScopeFromText(...parts) {
  const s = parts.filter(Boolean).join(' ').toLowerCase().replace(/[’`]/g, "'").trim();
  if (!s) return null;

  const quarter = n => (
    new RegExp(`\\bq${n}\\b`, 'i').test(s) ||
    new RegExp(`(?:^|\\D)${n}\\s*(?:-|–|—)?\\s*(?:а|я|га|та|тя|ша|ї|ої|го)?\\s*(?:чверт|quarter)`, 'i').test(s) ||
    new RegExp(`(?:чверт|quarter)\\w*\\s*${n}(?:\\D|$)`, 'i').test(s)
  );
  if ((s.includes('перш') && s.includes('чверт')) || /\b(?:first|1st)\s+quarter\b/i.test(s) || quarter(1)) return 'Q1';
  if ((s.includes('друг') && s.includes('чверт')) || /\b(?:second|2nd)\s+quarter\b/i.test(s) || quarter(2)) return 'Q2';
  if ((s.includes('трет') && s.includes('чверт')) || /\b(?:third|3rd)\s+quarter\b/i.test(s) || quarter(3)) return 'Q3';
  if ((s.includes('четверт') && s.includes('чверт')) || /\b(?:fourth|4th)\s+quarter\b/i.test(s) || quarter(4)) return 'Q4';

  const half = n => new RegExp(`(?:^|\\D)${n}\\s*(?:-|–|—)?\\s*(?:а|я|га|та|ї|ої|го)?\\s*(?:полов|half)`, 'i').test(s);
  if ((s.includes('перш') && s.includes('полов')) || /\bfirst\s+half\b/i.test(s) || half(1) || /\b1h\b/i.test(s)) return 'H1';
  if ((s.includes('друг') && s.includes('полов')) || /\bsecond\s+half\b/i.test(s) || half(2) || /\b2h\b/i.test(s)) return 'H2';
  return null;
}

function resolveTeamIdentity(text, homeName, awayName) {
  const normalized = normalizeTeamName(text);
  const homeNorm = normalizeTeamName(homeName);
  const awayNorm = normalizeTeamName(awayName);
  const homeVariants = teamVariants(homeName);
  const awayVariants = teamVariants(awayName);

  const score = (teamNorm, variants, opponentNorm) => {
    if (!normalized || !teamNorm) return 0;
    let value = 0;
    if (normalized.includes(teamNorm)) value = Math.max(value, 100 + teamNorm.length);
    for (const variant of variants) {
      if (variant.length >= 3 && normalized.includes(variant)) {
        value = Math.max(value, 60 + variant.length);
      }
    }
    const opponentWords = new Set(opponentNorm.split(' ').filter(w => w.length >= 4));
    for (const word of teamNorm.split(' ').filter(w => w.length >= 4)) {
      if (!normalized.includes(word)) continue;
      value += opponentWords.has(word) ? 1 : Math.min(20, word.length * 2);
    }
    return value;
  };

  const homeScore = score(homeNorm, homeVariants, awayNorm);
  const awayScore = score(awayNorm, awayVariants, homeNorm);
  if (homeScore <= 0 && awayScore <= 0) {
    return { team_side: null, team_name: null, ambiguous: false, homeScore, awayScore };
  }
  if (homeScore > awayScore + 2) {
    return { team_side: 'home', team_name: homeName, ambiguous: false, homeScore, awayScore };
  }
  if (awayScore > homeScore + 2) {
    return { team_side: 'away', team_name: awayName, ambiguous: false, homeScore, awayScore };
  }
  return { team_side: null, team_name: null, ambiguous: true, homeScore, awayScore };
}

function classifyMarket(name, context = {}) {
  const title = String(name ?? '').trim();
  const tabText = String(context.tabText ?? context.sourceTab ?? '').trim();
  const selectionText = String(context.selectionText ?? '').trim();
  const n = title.toLowerCase();
  const combined = `${title} ${tabText}`.toLowerCase().trim();
  // Scope is intentionally derived only from the market-group title or the
  // bookmaker tab metadata. Selection labels must never invent Q1/Q2/Q3/Q4.
  const scope = inferScopeFromText(title) || inferScopeFromText(tabText);
  // Team identity may legitimately be present in the market title OR in an
  // outcome label, so both are evidence for home/away ownership.
  const teamEvidence = `${title} ${selectionText}`.trim();
  const teamIdentity = context.homeName && context.awayName
    ? resolveTeamIdentity(teamEvidence, context.homeName, context.awayName)
    : { team_side: null, team_name: null, ambiguous: false };

  const hasTotal = n.includes('тотал') || /\btotal\b/i.test(n);
  const hasQuarter = scope?.startsWith('Q') || combined.includes('чверт') || /\bq[1-4]\b/i.test(combined);
  const hasHalf = scope?.startsWith('H') || combined.includes('полов') || /\b[12]h\b/i.test(combined);
  const qSuffix = scope?.startsWith('Q') ? scope.toLowerCase() : null;
  const hSuffix = scope?.startsWith('H') ? scope.toLowerCase() : null;

  if (n.includes('переможець') || n === 'п1п2' || n === 'п1 п2' || n === 'перемога') return 'match_1x2';

  if (n.includes('1x2') && hasQuarter && qSuffix) return `quarter_1x2_${qSuffix}`;
  if (n.includes('нічия без ставки') && hasQuarter && qSuffix) return `quarter_dnb_${qSuffix}`;

  if (n.includes('обидві команди наберуть')) {
    return qSuffix ? `quarter_btts_${qSuffix}` : 'quarter_btts';
  }
  if (n.includes('гонка до')) return qSuffix ? `quarter_race_${qSuffix}` : 'match_race';
  if (n.includes('перемога з різницею')) return 'win_margin';

  if (n.includes('остання цифра') || n.includes('сума останніх цифр')) {
    if (hSuffix) return `half_last_digit_${hSuffix}`;
    return 'last_digit';
  }

  if (n.includes('фора') && hasQuarter) return qSuffix ? `quarter_handicap_${qSuffix}` : null;
  if (n.includes('фора') && hasHalf) return hSuffix ? `half_handicap_${hSuffix}` : null;
  if (n.includes('фора')) return 'match_handicap';

  if (hasTotal) {
    const explicitTeamTotal =
      n.includes('індивідуальний тотал') ||
      n.includes('тотал очків') ||
      /\bteam\s+total\b/i.test(n) ||
      teamIdentity.team_side !== null;

    if (explicitTeamTotal) {
      if (!teamIdentity.team_side) {
        if (qSuffix) return `ambiguous_team_it_quarter_${qSuffix}`;
        if (hSuffix) return `ambiguous_team_it_half_${hSuffix}`;
        return 'ambiguous_team_it_match';
      }
      if (qSuffix) return `team_it_quarter_${qSuffix}`;
      if (hSuffix) return `team_it_half_${hSuffix}`;
      return 'team_it_match';
    }

    if (hasQuarter) return qSuffix ? `quarter_total_${qSuffix}` : null;
    if (hasHalf) return hSuffix ? `half_total_${hSuffix}` : null;
    return 'match_total';
  }

  return null;
}

// ─── Bet-label parser ─────────────────────────────────────────────────────────

function parseBetLabel(label, specialValue) {
  const l = label.trim();
  const sv = (specialValue ?? '').trim().replace(/,/g, '.').replace(/[−–—]/g, '-').replace(/\s+/g, '');

  // Over/under via "більше"/"менше"
  if (/більше/i.test(l)) {
    const m = (sv || l.replace(/,/g, '.')).match(/([\d.]+)/);
    return m ? { side: 'over',  line: parseFloat(m[1]) } : null;
  }
  if (/менше/i.test(l)) {
    const m = (sv || l.replace(/,/g, '.')).match(/([\d.]+)/);
    return m ? { side: 'under', line: parseFloat(m[1]) } : null;
  }

  // Handicap: specialValue holds "+6.5" / "-4.5", label holds team name
  if (sv && sv.match(/^[+-]?\d+\.?\d*$/)) {
    const v = parseFloat(sv);
    // We don't know home/away yet at this level — caller resolves by position
    return { side: 'raw_handicap', handicap: v };
  }

  // Fallback: plain handicap in label
  const hm = l.match(/^[Фф]?\s*([+-]?\d+\.?\d*)\s*$/);
  if (hm) {
    const v = parseFloat(hm[1]);
    return { side: v >= 0 ? 'home' : 'away', handicap: v };
  }

  if (/^п1$/i.test(l)) return { side: 'home' };
  if (/^п2$/i.test(l)) return { side: 'away' };

  return null;
}

// ─── Shadow DOM scraper (runs inside page.evaluate) ──────────────────────────

/**
 * Collects all market data from Shadow DOM roots on the page.
 * Returns array of { title, sliderValue, bets: [{label, specialValue, odd}] }
 * This function is serialised and sent to the browser — NO closures over outer scope.
 */
function collectMarketsFromShadowDOM() {
  // Recursively find all shadow roots, tracking visited roots to prevent duplicates.
  // Without the visited Set, nested shadow roots can be traversed multiple times
  // (once via their direct parent and again via an ancestor), causing market entries
  // to appear in the output more than once.
  function getAllShadowRoots(root, visited) {
    const roots = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node) {
      if (node.shadowRoot && !visited.has(node.shadowRoot)) {
        visited.add(node.shadowRoot);
        roots.push(node.shadowRoot);
        roots.push(...getAllShadowRoots(node.shadowRoot, visited));
      }
      node = walker.nextNode();
    }
    return roots;
  }

  const visited = new Set([document]);
  const allRoots = [document, ...getAllShadowRoots(document, visited)];
  const markets = [];

  for (const root of allRoots) {
    const marketBoxes = root.querySelectorAll('[class*="EventDetailsMarketBoxContainer-sc-"]');
    for (const box of marketBoxes) {
      const titleEl = box.querySelector('[class*="EventDetailsMarketName-sc-"]');
      if (!titleEl) continue;
      const title = titleEl.textContent.trim();
      const firstAttr = (element, names) => {
        for (const name of names) {
          const value = element?.getAttribute?.(name);
          if (value) return value;
        }
        return null;
      };
      const sourceMarketId = firstAttr(box, [
        'data-market-id', 'data-marketid', 'data-id', 'market-id', 'id',
      ]);

      // In prematch Betking renders the currently selected full-match total in
      // the slider thumb, while the Over/Under buttons may not carry that line.
      // Keep it with its own market box; the Node-side transformer decides when
      // it is appropriate to use it.
      const sliderEl = box.querySelector('[class*="EventDetailsSliderThumbLabel-sc-"]');
      const sliderValue = sliderEl?.textContent.trim() ?? '';

      const bets = [];
      const buttons = Array.from(box.querySelectorAll('button[class*="OddBoxButton-sc-"]'))
        // Some Betking builds nest similarly-named market containers. Without
        // this guard an outer box can absorb bets from all child markets.
        .filter(btn => btn.closest('[class*="EventDetailsMarketBoxContainer-sc-"]') === box);
      for (const btn of buttons) {
        // Label text (team name or більше/менше)
        const labelEl = btn.querySelector('[class*="OddLabel-sc-"]');
        // Special value (+6.5, -4.5, 175.5, etc.)
        const specialEl = btn.querySelector('[class*="OddSpecialValue-sc-"]');
        // Odd value
        const oddEl = btn.querySelector('[class*="OddValue-sc-"]');

        const label        = labelEl?.textContent.trim()  ?? '';
        const specialValue = specialEl?.textContent.trim() ?? '';
        const oddText      = oddEl?.textContent.trim().replace(',', '.') ?? '';
        const odd          = parseFloat(oddText) || null;
        const sourceOutcomeId = firstAttr(btn, [
          'data-outcome-id', 'data-selection-id', 'data-bet-id', 'data-id',
          'outcome-id', 'selection-id', 'id',
        ]);
        const rawSelectionName = [label, specialValue].filter(Boolean).join(' ').trim();

        if (label || specialValue) {
          bets.push({ label, specialValue, odd, rawSelectionName, sourceOutcomeId });
        }
      }

      if (bets.length > 0) markets.push({
        title,
        sliderValue,
        sourceMarketId,
        bets,
      });
    }
  }

  return markets;
}

// ─── Debug: dump all lobby cards (runs inside page.evaluate) ─────────────────

/**
 * Walks the entire Shadow DOM and collects all event card team names visible
 * in the lobby. Used when a match is not found to help diagnose naming mismatches.
 * Returns array of { home, away } objects.
 */
function collectAllLobbyCards() {
  function findInShadow(root) {
    const found = [];
    const cards = root.querySelectorAll('[class*="EventBoxContainer-sc-"]');
    for (const card of cards) {
      const names = Array.from(card.querySelectorAll('[class*="CompetitorName-sc-"]'))
        .map(e => e.textContent.trim());
      if (names.length >= 2)
        found.push({ home: names[0], away: names[1] });
      else if (names.length === 1)
        found.push({ home: names[0], away: '?' });
    }
    for (const el of root.querySelectorAll('*'))
      if (el.shadowRoot) found.push(...findInShadow(el.shadowRoot));
    return found;
  }
  return findInShadow(document);
}

// ─── Main scraper ─────────────────────────────────────────────────────────────

async function scrapeBetking(context, homeName, awayName, liveStatus = '', isPrematch = false) {
  const wantCategories = marketsForStatus(liveStatus);
  const scrapeAll      = wantCategories.size === 0;

  // Build multi-variant search arrays for robust matching
  const homeVariants = teamVariants(homeName);
  const awayVariants = teamVariants(awayName);

  console.log(`  [betking] homeVariants: ${JSON.stringify(homeVariants)}`);
  console.log(`  [betking] awayVariants: ${JSON.stringify(awayVariants)}`);

  // Keep simple norm strings for market-level matching (handicap labels etc.)
  const homeNorm = normalizeTeamName(homeName);
  const awayNorm = normalizeTeamName(awayName);

  const page = await context.newPage();
  page.setDefaultTimeout(NAV_TIMEOUT);

  let detailPage = page;

  try {
    // 1. Lobby
    console.log('  [betking] Navigating to lobby…');
    await page.goto(LOBBY_URL, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });

    // Wait for event cards (may be inside shadow DOM too — use evaluate)
    await page.waitForFunction(() => {
      function hasShadowMatch(root) {
        if (root.querySelector('[class*="EventBoxContainer-sc-"]')) return true;
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot && hasShadowMatch(el.shadowRoot)) return true;
        }
        return false;
      }
      return hasShadowMatch(document);
    }, { timeout: NAV_TIMEOUT });

    // Wait for the card list to stabilise — on a slow single-core VPS the JS bundle
    // keeps rendering cards for several seconds after the first card appears.
    // Poll until the count stops growing for two consecutive checks, with a generous
    // per-poll delay to give the CPU time to finish rendering between checks.
    {
      const POLL_INTERVAL = 1200;  // ms between checks — generous for 1-core VPS
      const STABLE_ROUNDS = 2;     // how many equal counts in a row = stable
      const MAX_POLLS     = 10;    // bail out after 10 × 1200 ms = 12 s max
      let prevCount  = -1;
      let stableHits = 0;
      for (let i = 0; i < MAX_POLLS; i++) {
        await page.waitForTimeout(POLL_INTERVAL);
        const count = await page.evaluate(() => {
          function countCards(root) {
            let n = root.querySelectorAll('[class*="EventBoxContainer-sc-"]').length;
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) n += countCards(el.shadowRoot);
            return n;
          }
          return countCards(document);
        });
        console.log(`  [betking] Cards count poll ${i + 1}: ${count}`);
        if (count > 0 && count === prevCount) {
          stableHits++;
          if (stableHits >= STABLE_ROUNDS) {
            console.log(`  [betking] Card list stable at ${count} cards`);
            break;
          }
        } else {
          stableHits = 0;
        }
        prevCount = count;
      }
    }

    // 2. Find and click event card by team names.
    //    betking uses virtual/lazy scroll — cards below the viewport are not in the DOM.
    //    Strategy: scroll down in steps, attempt to find the card after each step.
    console.log('  [betking] Looking for match card (with scroll)…');

    // ─── Card search helpers (serialised into browser via page.evaluate) ────────

    // Core search: collects ALL unique cards from DOM+shadowDOM (deduped by text),
    // then tries to find one matching the given keyword sets.
    // Returns { clicked, foundNames, strategy } or null.
    //
    // matchMode:
    //   'both'  — card must match at least one homeKw AND one awayKw
    //   'home'  — card must match at least one homeKw (awayKws ignored)
    //   'away'  — card must match at least one awayKw (homeKws ignored)
    //
    // In 'home'/'away' mode we also require uniqueness — if more than one card
    // matches the single keyword we skip it (ambiguous).

    async function tryFindCard(homeKws, awayKws, matchMode, strategyLabel, homeQuals = [], awayQuals = []) {
      return page.evaluate(
        ([hKws, aKws, mode, label, hQuals, aQuals]) => {
          function wordIn(nameOnPage, kws) {
            const normalize = value => value.toLowerCase()
              .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
              .replace(/[’'`]/g, '')
              .replace(/[^\p{L}\p{N}]+/gu, ' ')
              .replace(/\s+/g, ' ').trim();
            const n = normalize(nameOnPage);
            return kws.some(k => n.includes(k) || k.includes(n));
          }

          // Age/gender qualifiers (u17, u20, w, m, …) must ALSO be present on the
          // card side when we only matched on a bare team-name word. Without this,
          // "словенія" alone matches any Slovenia team regardless of age group —
          // e.g. it would happily click "Словенія U20" while looking for "Словенія U17 W".
          function qualifiersOk(nameOnPage, quals) {
            if (quals.length === 0) return true;
            const n = nameOnPage.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ');
            return quals.every(q => n.includes(q));
          }

          // Collect all cards from DOM + shadow DOM, dedupe by joined competitor text
          function collectCards(root, seen, out) {
            const cards = root.querySelectorAll('[class*="EventBoxContainer-sc-"]');
            for (const card of cards) {
              const nameEls = Array.from(card.querySelectorAll('[class*="CompetitorName-sc-"]'));
              if (nameEls.length < 2) continue;
              const key = nameEls.map(e => e.textContent.trim().toLowerCase()).join('|');
              if (seen.has(key)) continue;
              seen.add(key);
              out.push({ card, names: nameEls.map(e => e.textContent.trim()) });
            }
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) collectCards(el.shadowRoot, seen, out);
          }

          const seen = new Set();
          const entries = [];
          collectCards(document, seen, entries);

          // Filter by match mode
          const matches = entries.filter(({ names }) => {
            const ns = names.map(n => n.toLowerCase());
            if (mode === 'both')
              return ns.some(n => wordIn(n, hKws)) && ns.some(n => wordIn(n, aKws));
            if (mode === 'home')
              return ns.some(n => wordIn(n, hKws) && qualifiersOk(n, hQuals));
            if (mode === 'away')
              return ns.some(n => wordIn(n, aKws) && qualifiersOk(n, aQuals));
            return false;
          });

          // For single-team modes require exactly one match (avoid ambiguity)
          if (mode !== 'both' && matches.length !== 1) return null;
          if (matches.length === 0) return null;

          matches[0].card.click();
          return { clicked: true, foundNames: matches[0].names, strategy: label };
        },
        [homeKws, awayKws, matchMode, strategyLabel, homeQuals, awayQuals]
      );
    }

    // ─── Scroll helpers ───────────────────────────────────────────────────────

    async function getScrollTop() {
      return page.evaluate(() => {
        function findScrollable(root) {
          for (const el of root.querySelectorAll('*')) {
            if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {
              const st = getComputedStyle(el);
              if (st.overflow === 'auto' || st.overflow === 'scroll' ||
                  st.overflowY === 'auto' || st.overflowY === 'scroll') return el;
            }
            if (el.shadowRoot) { const r = findScrollable(el.shadowRoot); if (r) return r; }
          }
          return null;
        }
        const el = findScrollable(document);
        return el ? el.scrollTop : window.scrollY;
      });
    }

    async function scrollBy(px) {
      await page.evaluate((amount) => {
        function findScrollable(root) {
          for (const el of root.querySelectorAll('*')) {
            if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {
              const st = getComputedStyle(el);
              if (st.overflow === 'auto' || st.overflow === 'scroll' ||
                  st.overflowY === 'auto' || st.overflowY === 'scroll') return el;
            }
            if (el.shadowRoot) { const r = findScrollable(el.shadowRoot); if (r) return r; }
          }
          return null;
        }
        const el = findScrollable(document);
        if (el) el.scrollTop += amount;
        else window.scrollBy(0, amount);
      }, px);
    }

    const SCROLL_STEP  = 600;
    const SCROLL_PAUSE = 1000;  // generous for 1-core VPS
    const MAX_SCROLLS  = 20;

    // ─── Build keyword lists for fallback word-by-word search ────────────────
    // All variants expanded + individual words longer than 3 chars, deduplicated.
    // Words are tried longest-first so more specific words win over short ones.

    function significantWords(variants) {
      const words = new Set();
      for (const v of variants) {
        // the full variant itself
        words.add(v);
        // individual words within the variant
        for (const w of v.split(/[\s\-.,]+/))
          if (w.length > 3) words.add(w);
      }
      // sort longest first — more specific = better
      return [...words].sort((a, b) => b.length - a.length);
    }

    // Age-group / gender qualifiers (u16, u17, u20, w, m, …) get dropped by the
    // `length > 3` filter above, which lets a bare team-name word like "словенія"
    // match ANY age group for that country. Extract them separately so fallback
    // single-team strategies can still require them to match.
    function significantQualifiers(variants) {
      const quals = new Set();
      for (const v of variants)
        for (const w of v.split(/[\s\-.,]+/))
          if (/^u\d{1,2}$/.test(w) || /^[wm]$/.test(w)) quals.add(w);
      return [...quals];
    }

    const homeWords = significantWords(homeVariants);
    const awayWords = significantWords(awayVariants);
    const homeQualifiers = significantQualifiers(homeVariants);
    const awayQualifiers = significantQualifiers(awayVariants);

    console.log(`  [betking] homeWords: ${JSON.stringify(homeWords)}`);
    console.log(`  [betking] awayWords: ${JSON.stringify(awayWords)}`);
    console.log(`  [betking] homeQualifiers: ${JSON.stringify(homeQualifiers)}`);
    console.log(`  [betking] awayQualifiers: ${JSON.stringify(awayQualifiers)}`);

    // ─── Search pass — run after every scroll step ────────────────────────────
    // Returns clickResult or null.
    // Strategy order:
    //   1. Both teams full variants match (original behaviour)
    //   2. Each home word paired with each away word (word × word)
    //   3. Home word alone — unique card required
    //   4. Away word alone — unique card required

    async function searchPass() {
      // 1. Full match
      let r = await tryFindCard(homeVariants, awayVariants, 'both', 'full-variants');
      if (r) return r;

      // 2. Word × word
      for (const hw of homeWords) {
        for (const aw of awayWords) {
          r = await tryFindCard([hw], [aw], 'both', `word×word(${hw}×${aw})`);
          if (r) return r;
        }
      }

      // 3. Home word alone (unique) — must still match the age/gender qualifier
      for (const hw of homeWords) {
        r = await tryFindCard([hw], [], 'home', `home-word(${hw})`, homeQualifiers, []);
        if (r) return r;
      }

      // 4. Away word alone (unique) — must still match the age/gender qualifier
      for (const aw of awayWords) {
        r = await tryFindCard([], [aw], 'away', `away-word(${aw})`, [], awayQualifiers);
        if (r) return r;
      }

      return null;
    }

    // ─── Main find-and-click loop ─────────────────────────────────────────────

    console.log('  [betking] Looking for match card (with scroll)…');

    let clickResult = null;
    let scrollsDone = 0;

    // Subscribe before the click. Subscribing after card.click() has a race:
    // a fast popup can be created before waitForEvent() starts listening.
    // Start listening before the search/click so a very fast popup cannot be missed.
    // Keep the listener alive while the card is being searched for, but never make
    // same-page navigation wait for the full navigation timeout.
    const popupPromise = context.waitForEvent('page', { timeout: NAV_TIMEOUT }).catch(() => null);

    clickResult = await searchPass();

    while (!clickResult && scrollsDone < MAX_SCROLLS) {
      const topBefore = await getScrollTop();
      await scrollBy(SCROLL_STEP);
      await page.waitForTimeout(SCROLL_PAUSE);
      scrollsDone++;

      clickResult = await searchPass();

      if (!clickResult) {
        const topAfter = await getScrollTop();
        if (topAfter <= topBefore) {
          console.log(`  [betking] Reached bottom of list after ${scrollsDone} scrolls`);
          break;
        }
      }
    }

    if (!clickResult) {
      // ── DEBUG: dump all visible cards so we know exactly what betking calls them ──
      console.warn(`  [betking] ⚠ Match not found: "${homeName}" vs "${awayName}" (scrolled ${scrollsDone} times)`);
      console.warn(`  [betking] 🔍 Dumping all lobby cards for diagnosis:`);

      const allCards = await page.evaluate(collectAllLobbyCards);
      if (allCards.length === 0) {
        console.warn('  [betking]   (no cards found — page may not have loaded correctly)');
      } else {
        allCards.forEach((c, i) => {
          console.warn(`  [betking]   card[${i}]: "${c.home}" vs "${c.away}"`);
        });
      }
      console.warn(`  [betking] 💡 Check if the match is listed above. homeVariants=${JSON.stringify(homeVariants)} awayVariants=${JSON.stringify(awayVariants)}`);
      return null;
    }

    console.log(`  [betking] ✅ Match card clicked after ${scrollsDone} scrolls — found: ${JSON.stringify(clickResult.foundNames)} (strategy: ${clickResult.strategy})`);

    // 3. Wait for new tab or navigation to match detail
    console.log('  [betking] Waiting for detail page…');
    const newTab = await Promise.race([
      popupPromise,
      page.waitForTimeout(5_000).then(() => null),
    ]);
    detailPage = newTab ?? page;

    if (detailPage !== page) {
      await detailPage.waitForLoadState('domcontentloaded', { timeout: NAV_TIMEOUT }).catch(() => {});
    }

    // Wait until market boxes appear in shadow DOM
    await detailPage.waitForFunction(() => {
      function hasMarkets(root) {
        if (root.querySelector('[class*="EventDetailsMarketBoxContainer-sc-"]')) return true;
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot && hasMarkets(el.shadowRoot)) return true;
        }
        return false;
      }
      return hasMarkets(document);
    }, { timeout: NAV_TIMEOUT });

    console.log('  [betking] Match detail loaded — scraping markets…');

    // 4. Wait for tabs to render (Shadow DOM tabs may arrive later than market boxes,
    //    especially on slow single-core VPS or while Betking is repricing at HT).
    {
      const TAB_POLL_INTERVAL = 500;
      const TAB_POLL_MAX      = 30;   // up to 15 s; HT tab bar can disappear during repricing
      for (let i = 0; i < TAB_POLL_MAX; i++) {
        const tabCount = await detailPage.evaluate(() => {
          function countTabs(root) {
            let n = root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]').length;
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) n += countTabs(el.shadowRoot);
            return n;
          }
          return countTabs(document);
        });
        console.log(`  [betking] Tab poll ${i + 1}: ${tabCount} tab(s)`);
        if (tabCount > 0) break;
        await detailPage.waitForTimeout(TAB_POLL_INTERVAL);
      }
    }

    // Discover available tabs in shadow DOM
    const tabs = await detailPage.evaluate(() => {
      function findInShadow(root) {
        const found = [];
        root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]')
          .forEach((el, i) => found.push({ text: el.textContent.trim(), idx: found.length }));
        for (const el of root.querySelectorAll('*'))
          if (el.shadowRoot) {
            const sub = findInShadow(el.shadowRoot);
            sub.forEach(s => { s.idx = found.length; found.push(s); });
          }
        return found;
      }
      return findInShadow(document);
    });
    console.log(`  [betking] Tabs found: ${tabs.map(t => t.text).join(', ') || '(none)'}`);

    // Click a tab by its global index and wait for React re-render.
    // On slow VPS use a longer settle delay (1 s instead of 700 ms).
    async function clickTab(idx) {
      const clicked = await detailPage.evaluate((tabIdx) => {
        let count = 0;
        function clickInShadow(root) {
          for (const el of root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]')) {
            if (count++ === tabIdx) { el.click(); return true; }
          }
          for (const el of root.querySelectorAll('*'))
            if (el.shadowRoot && clickInShadow(el.shadowRoot)) return true;
          return false;
        }
        return clickInShadow(document);
      }, idx);
      if (!clicked) return false;

      // Wait for populated odds, not just a fixed delay. During HT repricing
      // the boxes remain mounted while every OddValue is temporarily empty.
      let previousSignature = null;
      let stableRounds = 0;
      for (let attempt = 0; attempt < 10; attempt++) {
        await detailPage.waitForTimeout(750);
        const state = await detailPage.evaluate(() => {
          function roots(root, out = [root]) {
            for (const el of root.querySelectorAll('*')) {
              if (el.shadowRoot) roots(el.shadowRoot, out);
            }
            return out;
          }
          const values = [];
          for (const root of roots(document)) {
            for (const el of root.querySelectorAll('[class*="OddValue-sc-"]')) {
              const value = el.textContent.trim();
              if (value) values.push(value);
            }
          }
          return { validOdds: values.length, signature: values.join('|') };
        });
        if (state.validOdds > 0 && state.signature === previousSignature) stableRounds++;
        else stableRounds = 0;
        previousSignature = state.signature;
        if (stableRounds >= 1) return true;
      }
      return false;
    }

    // Collect markets from all tabs, dedup by title.
    // IMPORTANT: specific period tabs (1-а половина, 2-га чверть, …) carry the
    // freshest odds. Generic tabs (Усі / Головні) may show stale/suspended lines
    // for markets that have already been updated in a specific tab.
    // Scrape specific tabs FIRST so their data wins the dedup, then fill in
    // anything remaining from the generic tabs.
    const marketIndex = new Map();
    const rawMarkets = [];

    function mergeMarkets(fresh, priority = 0, tabText = '') {
      for (const original of fresh) {
        const market = { ...original, sourceTab: tabText || original.sourceTab || null };
        const titleKey = market.title.toLowerCase().replace(/\s+/g, ' ').trim();
        const selectionText = (market.bets || [])
          .map(bet => bet.rawSelectionName || [bet.label, bet.specialValue].filter(Boolean).join(' '))
          .filter(Boolean)
          .join(' | ');
        const category = classifyMarket(market.title, {
          tabText: market.sourceTab,
          selectionText,
          homeName,
          awayName,
        });
        const scopeKey = inferScopeFromText(market.title) || inferScopeFromText(market.sourceTab) || 'Match';
        const teamKey = resolveTeamIdentity(`${market.title} ${selectionText}`, homeName, awayName).team_side || '-';
        // Every prematch slider point is a separate line of the same market.
        const sliderKey = isPrematch && category === 'match_total' && market.sliderValue
          ? `|slider:${String(market.sliderValue).replace(',', '.').trim()}`
          : '';
        // Scope and team identity are part of the key. This prevents identical
        // labels such as "Тотал" from Q1/Q2/Q3 tabs from overwriting each other.
        const key = `${scopeKey}|${teamKey}|${titleKey}|${market.sourceMarketId ?? ''}${sliderKey}`;
        const validOdds = market.bets.filter(bet => bet.odd !== null).length;
        const existing = marketIndex.get(key);
        if (!existing) {
          marketIndex.set(key, { index: rawMarkets.length, priority, validOdds });
          rawMarkets.push(market);
          continue;
        }
        if (priority > existing.priority ||
            (priority === existing.priority && validOdds > existing.validOdds)) {
          rawMarkets[existing.index] = market;
          marketIndex.set(key, { ...existing, priority, validOdds });
        }
      }
    }

    const TAB_SKIP    = ['конструктор'];
    const TAB_GENERIC = ['усі', 'головні', 'всі'];

    const genericTabs  = [];
    const specificTabs = [];
    for (const tab of tabs) {
      const tl = tab.text.toLowerCase();
      if (TAB_SKIP.some(s => tl.includes(s))) continue;
      if (TAB_GENERIC.some(s => tl.includes(s))) genericTabs.push(tab);
      else specificTabs.push(tab);
    }

    console.log(`  [betking] Specific tabs: ${specificTabs.map(t => t.text).join(', ') || '(none)'}`);
    console.log(`  [betking] Generic  tabs: ${genericTabs.map(t => t.text).join(', ')  || '(none)'}`);

    // Find slider controls in the active tab. The index is recalculated before
    // every drag because React can replace the DOM nodes after a value change.
    async function getSliderControls() {
      return detailPage.evaluate(() => {
        function roots(root, out = [root]) {
          for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) roots(el.shadowRoot, out);
          }
          return out;
        }
        const controls = [];
        const seen = new Set();
        for (const root of roots(document)) {
          for (const box of root.querySelectorAll('[class*="EventDetailsMarketBoxContainer-sc-"]')) {
            if (seen.has(box)) continue;
            seen.add(box);
            const input = box.querySelector('input[type="range"]');
            const track = box.querySelector('[class*="EventDetailsSliderTrack-sc-"]');
            if (!input || !track) continue;
            const title = box.querySelector('[class*="EventDetailsMarketName-sc-"]')?.textContent.trim() ?? '';
            controls.push({
              index: controls.length,
              title,
              min: Number(input.min),
              max: Number(input.max),
              value: Number(input.value),
            });
          }
        }
        return controls;
      });
    }

    async function dragSliderTo(index, targetValue) {
      const geometry = await detailPage.evaluate(([wantedIndex, wantedValue]) => {
        function roots(root, out = [root]) {
          for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) roots(el.shadowRoot, out);
          }
          return out;
        }
        const controls = [];
        const seen = new Set();
        for (const root of roots(document)) {
          for (const box of root.querySelectorAll('[class*="EventDetailsMarketBoxContainer-sc-"]')) {
            if (seen.has(box)) continue;
            seen.add(box);
            const input = box.querySelector('input[type="range"]');
            const track = box.querySelector('[class*="EventDetailsSliderTrack-sc-"]');
            // This is the visible draggable point from the supplied markup;
            // pointer events bubble from the circle to React Aria's container.
            const thumb = box.querySelector('[class*="EventDetailsSliderThumbCircle-sc-"]');
            if (input && track) controls.push({ input, track, thumb });
          }
        }
        const control = controls[wantedIndex];
        if (!control) return null;
        control.track.scrollIntoView({ block: 'center', inline: 'nearest' });
        const rect = control.track.getBoundingClientRect();
        const thumbRect = control.thumb?.getBoundingClientRect();
        const min = Number(control.input.min);
        const max = Number(control.input.max);
        const ratio = max > min ? (wantedValue - min) / (max - min) : 0;
        return {
          fromX: thumbRect ? thumbRect.left + thumbRect.width / 2 : rect.left,
          fromY: thumbRect ? thumbRect.top + thumbRect.height / 2 : rect.top + rect.height / 2,
          toX: rect.left + rect.width * Math.max(0, Math.min(1, ratio)),
          toY: rect.top + rect.height / 2,
        };
      }, [index, targetValue]);

      if (!geometry) return false;
      await detailPage.waitForTimeout(100);
      await detailPage.mouse.move(geometry.fromX, geometry.fromY);
      await detailPage.mouse.down();
      await detailPage.mouse.move(geometry.toX, geometry.toY, { steps: 8 });
      await detailPage.mouse.up();

      // Wait until React commits the discrete point, then allow odds to refresh.
      const reached = await detailPage.waitForFunction(([wantedIndex, wantedValue]) => {
        function roots(root, out = [root]) {
          for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) roots(el.shadowRoot, out);
          }
          return out;
        }
        const controls = [];
        const seen = new Set();
        for (const root of roots(document)) {
          for (const box of root.querySelectorAll('[class*="EventDetailsMarketBoxContainer-sc-"]')) {
            if (seen.has(box)) continue;
            seen.add(box);
            const input = box.querySelector('input[type="range"]');
            const track = box.querySelector('[class*="EventDetailsSliderTrack-sc-"]');
            if (input && track) controls.push(input);
          }
        }
        return Number(controls[wantedIndex]?.value) === wantedValue;
      }, [index, targetValue], { timeout: 3000 }).then(() => true).catch(() => false);
      await detailPage.waitForTimeout(650);
      return reached;
    }

    async function collectActiveTab(priority, tabText = '') {
      mergeMarkets(await detailPage.evaluate(collectMarketsFromShadowDOM), priority, tabText);
      if (!isPrematch) return;

      const controls = await getSliderControls();
      for (const control of controls) {
        // The alternate prematch extraction applies only to the full-match total.
        if (classifyMarket(control.title, { tabText, homeName, awayName }) !== 'match_total') continue;
        if (!Number.isFinite(control.min) || !Number.isFinite(control.max) || control.max < control.min) continue;
        console.log(`  [betking] Sweeping prematch slider "${control.title}": ${control.min}…${control.max}`);
        for (let point = control.min; point <= control.max; point++) {
          if (point === control.value) continue; // initial position already collected
          const reached = await dragSliderTo(control.index, point);
          if (!reached) {
            console.warn(`  [betking] ⚠ Slider point ${point} was not reached for "${control.title}"`);
            continue;
          }
          mergeMarkets(await detailPage.evaluate(collectMarketsFromShadowDOM), priority, tabText);
        }
      }
    }

    // 1. Specific tabs first (freshest data)
    for (const tab of specificTabs) {
      await clickTab(tab.idx);
      await collectActiveTab(2, tab.text);
    }

    // 2. Generic tabs — fill in anything not yet seen
    for (const tab of genericTabs) {
      await clickTab(tab.idx);
      await collectActiveTab(1, tab.text);
    }

    // 3. Fallback: if no tabs at all (or nothing collected), scrape whatever is
    //    currently rendered on the page. This keeps the VPS case working even when
    //    the tab bar never appears.
    if (rawMarkets.length === 0) {
      console.log('  [betking] No tabs found or empty result — scraping active view directly');
      await collectActiveTab(0, '');
    }

    // Skip locked/suspended buttons: filter bets with null odds out of each market
    // so positional parsers (match_1x2, quarter_dnb, quarter_race) get clean arrays.
    for (const m of rawMarkets) {
      m.bets = m.bets.filter(b => b.odd !== null);
    }

    console.log(`  [betking] Total unique markets after all tabs: ${rawMarkets.length}`);

    // 5. Transform into output structure
    const result = {
      match_1x2      : [],
      match_handicap : [],
      half_handicap  : [],   // handicap per half (H1/H2)
      quarter_handicap: [],  // handicap per quarter (Q1-Q4)
      match_total    : [],
      half_total     : [],
      quarter_total  : [],
      quarter_dnb    : [],
      quarter_1x2    : [],
      quarter_btts   : [],
      quarter_race   : [],
      win_margin     : [],
      last_digit     : [],
      home_ind_total : [],
      away_ind_total : [],
      market_outcomes: [],
      ambiguous_markets: [],
    };

    const marketFetchedAt = new Date().toISOString();

    function totalMarketRow({
      marketType, scope, teamSide = null, teamName = null, line,
      title, sourceTab = null, sourceMarketId = null,
      description = null, eligible = true, validationErrors = [],
    }) {
      return {
        ...(description ? { _description: description } : {}),
        market_type: marketType,
        scope,
        team_side: teamSide,
        team_name: teamName,
        line,
        overOdd: null,
        underOdd: null,
        raw_market_name: title || null,
        raw_selection_name: null,
        source_market_id: sourceMarketId || null,
        source_outcome_id: null,
        source: 'BETKING',
        fetchedAt: marketFetchedAt,
        eligible_market: Boolean(eligible),
        validation_errors: [...validationErrors],
        source_tab: sourceTab || null,
        raw_selections: [],
      };
    }

    function attachTotalSelection(row, bet, side) {
      if (side === 'over') row.overOdd = bet.odd;
      if (side === 'under') row.underOdd = bet.odd;
      const rawName = bet.rawSelectionName || [bet.label, bet.specialValue].filter(Boolean).join(' ').trim();
      const item = {
        side,
        raw_selection_name: rawName || null,
        source_outcome_id: bet.sourceOutcomeId || null,
        odd: bet.odd ?? null,
      };
      if (!row.raw_selections.some(x => x.side === item.side && x.raw_selection_name === item.raw_selection_name)) {
        row.raw_selections.push(item);
      }
      const names = [...new Set(row.raw_selections.map(x => x.raw_selection_name).filter(Boolean))];
      const ids = [...new Set(row.raw_selections.map(x => x.source_outcome_id).filter(Boolean))];
      row.raw_selection_name = names.length ? names.join(' | ') : null;
      row.source_outcome_id = ids.length ? ids.join('|') : null;
    }

    function registerCanonicalMarket(row) {
      if (!result.market_outcomes.includes(row)) result.market_outcomes.push(row);
      return row;
    }

    for (const { title, sliderValue, bets, sourceTab, sourceMarketId } of rawMarkets) {
      const selectionText = (bets || [])
        .map(bet => bet.rawSelectionName || [bet.label, bet.specialValue].filter(Boolean).join(' '))
        .filter(Boolean)
        .join(' | ');
      const cat = classifyMarket(title, {
        tabText: sourceTab,
        selectionText,
        homeName,
        awayName,
      });
      // Log every тотал-related market so misclassifications are always visible in logs
      if (title.toLowerCase().includes('тотал'))
        console.log(`  [betking] тотал market: "${title}" → cat="${cat ?? 'null'}"`);
      // Log every фора-related market
      if (title.toLowerCase().includes('фора'))
        console.log(`  [betking] фора  market: "${title}" → cat="${cat ?? 'null'}"`);
      if (!cat) {
        // Окремо логуємо ринки з "тотал"/"чверт"/"полов" — це майже завжди
        // нові формати назв betking, які треба додати в classifyMarket.
        const tl = title.toLowerCase();
        if (tl.includes('тотал') || tl.includes('чверт') || tl.includes('полов'))
          console.warn(`  [betking] ⚠ UNCLASSIFIED (тотал/чверт/полов): "${title}"`);
        else
          console.log(`  [betking] Unclassified market: "${title}"`);
        continue;
      }

      const baseCategory = cat.replace(/_q[1-4]$/, '').replace(/_h[12]$/, '');
      if (!scrapeAll && !wantCategories.has(baseCategory)) continue;

      // ── match_1x2 ──
      if (cat === 'match_1x2') {
        // Keep only one entry — prefer "Переможець", skip duplicates
        if (result.match_1x2.length === 0 && bets.length >= 2) {
          result.match_1x2.push({
            _description: 'Переможець ВСЬОГО МАТЧУ (не чверті). homeOdd = коефіцієнт на перемогу хазяїв, awayOdd = на перемогу гостей. Нічия не передбачена (баскетбол).',
            scope   : 'Match',
            homeOdd : bets[0]?.odd ?? null,
            awayOdd : bets[1]?.odd ?? null,
          });
        }

      // ── match_handicap ──
      } else if (cat === 'match_handicap') {
        // Buttons come in pairs: [home+X, away-X] or multiple lines
        // specialValue holds the handicap number, label holds team name
        const pairs = {};
        for (const bet of bets) {
          if (!bet.specialValue) continue;
          const hv = parseFloat(bet.specialValue);
          if (isNaN(hv)) continue;
          const key = Math.abs(hv).toFixed(1);
          if (!pairs[key]) pairs[key] = { home: null, away: null };

          const teamName     = normalizeTeamName(bet.label);
          const teamVariantsBk = [teamName];

          // Match handicap labels against both Cyrillic and Latin variants
          const isHome = variantsMatch(homeVariants, teamVariantsBk) ||
                         teamName.includes(homeNorm) || homeNorm.includes(teamName);
          const isAway = variantsMatch(awayVariants, teamVariantsBk) ||
                         teamName.includes(awayNorm) || awayNorm.includes(teamName);

          // home gets specialValue as-is (e.g. +6.5), away gets opposite sign
          if (isHome) pairs[key].home = { handicap: hv, odd: bet.odd };
          else if (isAway) pairs[key].away = { handicap: -hv, odd: bet.odd };
        }

        for (const { home, away } of Object.values(pairs)) {
          if (!home && !away) continue;
          result.match_handicap.push({
            _description: `Азіатський гандикап на ВЕСЬ МАТЧ. handicap — фора хазяїв (від'ємне = хазяї фаворити, наприклад -3.5 означає "хазяї мінус 3.5 очка"). homeHcpOdd — коефіцієнт якщо ставити на хазяїв з цією форою, awayHcpOdd — на гостей з протилежною форою (+3.5). НЕ стосується жодної окремої чверті.`,
            scope      : 'Match',
            handicap   : home?.handicap ?? (away ? away.handicap : null),
            homeHcpOdd : home?.odd ?? null,
            awayHcpOdd : away?.odd ?? null,
          });
        }

      // ── quarter handicap ──
      } else if (cat.startsWith('quarter_handicap_')) {
        const scopeMap = { quarter_handicap_q1:'Q1', quarter_handicap_q2:'Q2', quarter_handicap_q3:'Q3', quarter_handicap_q4:'Q4' };
        const scope = scopeMap[cat];
        const quarterLabelHcp = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
        const pairsQ = {};
        for (const bet of bets) {
          if (!bet.specialValue) continue;
          const hv = parseFloat(bet.specialValue);
          if (isNaN(hv)) continue;
          const key = Math.abs(hv).toFixed(1);
          if (!pairsQ[key]) pairsQ[key] = { home: null, away: null };
          const teamName = normalizeTeamName(bet.label);
          const isHome = variantsMatch(homeVariants, [teamName]) || teamName.includes(homeNorm) || homeNorm.includes(teamName);
          const isAway = variantsMatch(awayVariants, [teamName]) || teamName.includes(awayNorm) || awayNorm.includes(teamName);
          if (isHome) pairsQ[key].home = { handicap: hv, odd: bet.odd };
          else if (isAway) pairsQ[key].away = { handicap: -hv, odd: bet.odd };
        }
        for (const { home, away } of Object.values(pairsQ)) {
          if (!home && !away) continue;
          result.quarter_handicap.push({
            _description: `Азіатський гандикап ТІЛЬКИ за ${scope} (${quarterLabelHcp[scope]}). НЕ ПЛУТАТИ з match_handicap — це фора лише в межах однієї чверті. handicap — фора хазяїв у цій чверті. homeHcpOdd/awayHcpOdd.`,
            scope,
            handicap   : home?.handicap ?? (away ? away.handicap : null),
            homeHcpOdd : home?.odd ?? null,
            awayHcpOdd : away?.odd ?? null,
          });
        }

      // ── half handicap ──
      } else if (cat === 'half_handicap_h1' || cat === 'half_handicap_h2') {
        const scope = cat === 'half_handicap_h1' ? 'H1' : 'H2';
        const halfLabelHcp = { H1: 'перша половина (Q1+Q2)', H2: 'друга половина (Q3+Q4)' };
        const pairsH = {};
        for (const bet of bets) {
          if (!bet.specialValue) continue;
          const hv = parseFloat(bet.specialValue);
          if (isNaN(hv)) continue;
          const key = Math.abs(hv).toFixed(1);
          if (!pairsH[key]) pairsH[key] = { home: null, away: null };
          const teamName = normalizeTeamName(bet.label);
          const isHome = variantsMatch(homeVariants, [teamName]) || teamName.includes(homeNorm) || homeNorm.includes(teamName);
          const isAway = variantsMatch(awayVariants, [teamName]) || teamName.includes(awayNorm) || awayNorm.includes(teamName);
          if (isHome) pairsH[key].home = { handicap: hv, odd: bet.odd };
          else if (isAway) pairsH[key].away = { handicap: -hv, odd: bet.odd };
        }
        for (const { home, away } of Object.values(pairsH)) {
          if (!home && !away) continue;
          result.half_handicap.push({
            _description: `Азіатський гандикап за ${scope} (${halfLabelHcp[scope]}). НЕ ПЛУТАТИ з match_handicap — це фора лише за одну половину матчу. handicap — фора хазяїв у цій половині. homeHcpOdd/awayHcpOdd.`,
            scope,
            handicap   : home?.handicap ?? (away ? away.handicap : null),
            homeHcpOdd : home?.odd ?? null,
            awayHcpOdd : away?.odd ?? null,
          });
        }

      // ── match / half / quarter totals ──
      } else if (cat === 'match_total') {
        const prematchSliderLine = isPrematch
          ? parseFloat(String(sliderValue ?? '').replace(',', '.'))
          : NaN;
        if (isPrematch && Number.isFinite(prematchSliderLine)) {
          console.log(`  [betking] Prematch match_total slider line: ${prematchSliderLine} ("${title}")`);
        }
        for (const bet of bets) {
          const lineSource = Number.isFinite(prematchSliderLine)
            ? String(prematchSliderLine)
            : bet.specialValue;
          const p = parseBetLabel(bet.label, lineSource);
          if (!p || p.line == null) continue;
          const line = p.line;
          if (!line) continue;
          if (line < 40) {
            console.warn(`  [betking] ⚠ match_total sanity guard: rejected implausible line=${line} from "${title}"`);
            continue;
          }
          let e = result.match_total.find(x => x.scope === 'Match' && x.line === line);
          if (!e) {
            const rawTitle = String(title ?? '').trim();
            const genericRawName = /^(?:тотал|total)$/i.test(rawTitle);
            const missingRawName = !rawTitle;
            const unverifiableLowMatchTotal = line < 120 && (missingRawName || genericRawName);
            e = totalMarketRow({
              marketType: 'MATCH_TOTAL',
              scope: 'Match',
              line,
              title,
              sourceTab,
              sourceMarketId,
              description: `Тотал ВСЬОГО МАТЧУ. line=${line}.`,
              eligible: !unverifiableLowMatchTotal,
              validationErrors: unverifiableLowMatchTotal
                ? [missingRawName
                    ? 'MATCH_TOTAL_BELOW_120_WITHOUT_RAW_MARKET_NAME'
                    : 'MATCH_TOTAL_BELOW_120_WITH_GENERIC_RAW_MARKET_NAME']
                : [],
            });
            result.match_total.push(e);
            registerCanonicalMarket(e);
          }
          attachTotalSelection(e, bet, p.side);
        }

      } else if (cat === 'half_total_h1' || cat === 'half_total_h2') {
        const scope = cat === 'half_total_h1' ? 'H1' : 'H2';
        const marketType = scope === 'H1' ? 'H1_TOTAL' : 'H2_TOTAL';
        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = result.half_total.find(x => x.scope === scope && x.line === p.line);
          if (!e) {
            e = totalMarketRow({
              marketType,
              scope,
              line: p.line,
              title,
              sourceTab,
              sourceMarketId,
              description: `Тотал за ${scope}. line=${p.line}.`,
            });
            result.half_total.push(e);
            registerCanonicalMarket(e);
          }
          attachTotalSelection(e, bet, p.side);
        }

      } else if (cat.startsWith('quarter_total_')) {
        const scopeMap = {
          quarter_total_q1: 'Q1', quarter_total_q2: 'Q2',
          quarter_total_q3: 'Q3', quarter_total_q4: 'Q4',
        };
        const scope = scopeMap[cat];
        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = result.quarter_total.find(x => x.scope === scope && x.line === p.line);
          if (!e) {
            e = totalMarketRow({
              marketType: 'QUARTER_TOTAL',
              scope,
              line: p.line,
              title,
              sourceTab,
              sourceMarketId,
              description: `Загальний тотал ${scope} обох команд. line=${p.line}.`,
            });
            result.quarter_total.push(e);
            registerCanonicalMarket(e);
          }
          attachTotalSelection(e, bet, p.side);
        }

      // ── quarter draw-no-bet ──
      } else if (cat.startsWith('quarter_dnb_')) {
        const scopeMap = { quarter_dnb_q1:'Q1', quarter_dnb_q2:'Q2', quarter_dnb_q3:'Q3', quarter_dnb_q4:'Q4' };
        const scope = scopeMap[cat];
        if (bets.length >= 2) {
          // Buttons order: home team first, away team second (same pattern as match_1x2)
          const homeOdd = bets[0]?.odd ?? null;
          const awayOdd = bets[1]?.odd ?? null;
          if (!result.quarter_dnb.find(x => x.scope === scope)) {
            const quarterLabel = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
            result.quarter_dnb.push({
              _description: `"Нічия без ставки" за ${scope} (${quarterLabel[scope]}). Якщо чверть завершиться нічиєю — ставка повертається. homeOdd — коефіцієнт на перемогу хазяїв у цій чверті, awayOdd — на перемогу гостей. НЕ стосується результату матчу в цілому.`,
              scope, homeOdd, awayOdd,
            });
          }
        }

      // ── team individual totals (Match / H1 / H2 / Q1-Q4) ──
      } else if (
        cat === 'team_it_match' ||
        cat.startsWith('team_it_quarter_') ||
        cat.startsWith('team_it_half_') ||
        cat.startsWith('ambiguous_team_it_')
      ) {
        const identity = resolveTeamIdentity(`${title} ${selectionText}`, homeName, awayName);
        const scope = inferScopeFromText(title) || inferScopeFromText(sourceTab) || 'Match';
        const isAmbiguous = cat.startsWith('ambiguous_team_it_') || !identity.team_side;
        const marketType = isAmbiguous
          ? (scope.startsWith('Q') ? 'AMBIGUOUS_TEAM_IT_QUARTER' : 'AMBIGUOUS_TEAM_IT')
          : scope === 'Match'
            ? 'TEAM_IT_MATCH'
            : scope === 'H1'
              ? 'TEAM_IT_H1'
              : scope === 'H2'
                ? 'TEAM_IT_H2'
                : 'TEAM_IT_QUARTER';
        const target = identity.team_side === 'home'
          ? result.home_ind_total
          : identity.team_side === 'away'
            ? result.away_ind_total
            : result.ambiguous_markets;

        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = target.find(x =>
            x.market_type === marketType &&
            x.scope === scope &&
            x.team_side === identity.team_side &&
            x.line === p.line
          );
          if (!e) {
            e = totalMarketRow({
              marketType,
              scope,
              teamSide: identity.team_side,
              teamName: identity.team_name,
              line: p.line,
              title,
              sourceTab,
              sourceMarketId,
              description: isAmbiguous
                ? `Нерозпізнаний командний тотал ${scope}; грошове використання заборонено.`
                : `Індивідуальний тотал ${identity.team_name} за ${scope}. line=${p.line}.`,
              eligible: !isAmbiguous,
              validationErrors: isAmbiguous
                ? ['TEAM_IT_WITHOUT_UNAMBIGUOUS_TEAM_IDENTITY']
                : [],
            });
            target.push(e);
            registerCanonicalMarket(e);
          }
          attachTotalSelection(e, bet, p.side);
        }

      // ── quarter 1x2 (quarter winner) ──
      } else if (cat.startsWith('quarter_1x2_')) {
        const scopeMap = { quarter_1x2_q1:'Q1', quarter_1x2_q2:'Q2', quarter_1x2_q3:'Q3', quarter_1x2_q4:'Q4' };
        const scope = scopeMap[cat];
        const quarterLabel1x2 = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
        if (bets.length >= 2 && !result.quarter_1x2.find(x => x.scope === scope)) {
          result.quarter_1x2.push({
            _description: `Переможець ТІЛЬКИ ${scope} (${quarterLabel1x2[scope]}) — не матчу. homeOdd — хазяї виграють цю чверть, drawOdd — нічия в чверті (рідко, але можлива), awayOdd — гості виграють цю чверть.`,
            scope,
            title,
            homeOdd : bets[0]?.odd ?? null,
            drawOdd : bets.length >= 3 ? (bets[1]?.odd ?? null) : null,
            awayOdd : bets.length >= 3 ? (bets[2]?.odd ?? null) : (bets[1]?.odd ?? null),
          });
        }

      // ── quarter both-teams-score-N ──
      } else if (cat.startsWith('quarter_btts')) {
        const scopeMap = { quarter_btts_q1:'Q1', quarter_btts_q2:'Q2', quarter_btts_q3:'Q3', quarter_btts_q4:'Q4', quarter_btts:'Match' };
        const scope = scopeMap[cat] ?? 'Match';
        const thresholdM = title.match(/наберуть\s+(\d+)/i);
        const threshold = thresholdM ? parseInt(thresholdM[1]) : null;
        const yesOdd = bets[0]?.odd ?? null;
        const noOdd  = bets[1]?.odd ?? null;
        const quarterLabelBtts = { Q1: 'першій чверті', Q2: 'другій чверті', Q3: 'третій чверті', Q4: 'четвертій чверті', Match: 'матчі' };
        result.quarter_btts.push({
          _description: `Чи наберуть ОБИДВІ команди щонайменше ${threshold ?? '?'} очок у ${quarterLabelBtts[scope] ?? scope}. yesOdd — так (обидві наберуть), noOdd — ні (хоча б одна не набере). Це ставка на результативність обох команд, не на переможця.`,
          scope, title, threshold, yesOdd, noOdd,
        });

      // ── quarter race-to-N ──
      } else if (cat.startsWith('quarter_race') || cat === 'match_race') {
        const scopeMap = { quarter_race_q1:'Q1', quarter_race_q2:'Q2', quarter_race_q3:'Q3', quarter_race_q4:'Q4', match_race:'Match' };
        const scope = scopeMap[cat] ?? 'Match';
        const targetM = title.match(/до\s+(\d+)/i);
        const target  = targetM ? parseInt(targetM[1]) : null;
        const quarterLabelRace = { Q1: 'першій чверті', Q2: 'другій чверті', Q3: 'третій чверті', Q4: 'четвертій чверті', Match: 'матчі' };
        if (bets.length >= 2) {
          result.quarter_race.push({
            _description: `"Гонка до ${target ?? '?'} очок" у ${quarterLabelRace[scope] ?? scope} — яка команда ПЕРШОЮ набере ${target ?? '?'} очок саме в цьому ігровому відрізку. homeOdd — хазяї першими наберуть ${target ?? '?'}, awayOdd — гості. Це НЕ тотал і НЕ переможець чверті.`,
            scope, title, target,
            homeOdd : bets[0]?.odd ?? null,
            awayOdd : bets[1]?.odd ?? null,
          });
        }

      // ── win margin ──
      } else if (cat === 'win_margin') {
        for (const bet of bets) {
          if (bet.odd) result.win_margin.push({
            _description: `Перемога переможця матчу з різницею очок у діапазоні "${bet.label ?? bet.specialValue}". Наприклад "1-5" — переможець виграє на 1-5 очок. odd — коефіцієнт на цей діапазон.`,
            label: bet.label, specialValue: bet.specialValue, odd: bet.odd,
          });
        }

      // ── last digit / digit sum markets ──
      } else if (cat === 'last_digit' || cat.startsWith('half_last_digit')) {
        const scope = cat === 'half_last_digit_h1' ? 'H1' : cat === 'half_last_digit_h2' ? 'H2' : 'Match';
        const scopeText = scope === 'H1' ? 'першої половини' : scope === 'H2' ? 'другої половини' : 'матчу';
        for (const bet of bets) {
          if (bet.odd) result.last_digit.push({
            _description: `Ставка на останню цифру рахунку або суму останніх цифр обох команд ${scopeText}. label — конкретна цифра або діапазон на яку ставка. Екзотичний ринок, не стосується тоталів чи переможця.`,
            scope, title, label: bet.label, specialValue: bet.specialValue, odd: bet.odd,
          });
        }
      }
    }

    // ─── MARKET IDENTITY validation required by PARSER_MARKET_SCHEMA_FIX_UA ───
    for (const scope of ['Q1', 'Q2', 'Q3', 'Q4']) {
      const rows = result.quarter_total.filter(row => row.scope === scope);
      const lowRows = rows.filter(row => Number(row.line) < 30);
      const combinedRows = rows.filter(row => Number(row.line) >= 35 && Number(row.line) <= 60);
      if (lowRows.length && combinedRows.length) {
        for (const row of lowRows) {
          const pairEvidence = lowRows.some(other =>
            other !== row && combinedRows.some(combined =>
              Math.abs(Number(row.line) + Number(other.line) - Number(combined.line)) <= 2.5
            )
          );
          row.market_type = 'AMBIGUOUS_TEAM_IT_QUARTER';
          row.eligible_market = false;
          row.validation_errors = [...new Set([
            ...(row.validation_errors || []),
            pairEvidence
              ? 'LOW_QUARTER_LINES_SUM_TO_COMBINED_TOTAL_BUT_TEAM_IDENTITY_MISSING'
              : 'LOW_QUARTER_TOTAL_NEAR_COMBINED_TOTAL_LOOKS_LIKE_TEAM_IT',
          ])];
          if (!result.ambiguous_markets.includes(row)) result.ambiguous_markets.push(row);
        }
        const blocked = new Set(lowRows);
        result.quarter_total = result.quarter_total.filter(row => !blocked.has(row));
      }
    }

    for (const row of result.market_outcomes) {
      row.validation_errors = Array.isArray(row.validation_errors) ? row.validation_errors : [];
      if (row.market_type === 'QUARTER_TOTAL' && (row.team_side || row.team_name)) {
        row.eligible_market = false;
        row.validation_errors.push('QUARTER_TOTAL_MUST_NOT_HAVE_TEAM_IDENTITY');
      }
      if (String(row.market_type).startsWith('TEAM_IT_') && (!row.team_side || !row.team_name)) {
        row.eligible_market = false;
        row.validation_errors.push('TEAM_IT_REQUIRES_TEAM_SIDE_AND_TEAM_NAME');
      }
      if (row.market_type === 'MATCH_TOTAL' && Number(row.line) < 120 && !row.raw_market_name) {
        row.eligible_market = false;
        row.validation_errors.push('MATCH_TOTAL_BELOW_120_WITHOUT_RAW_MARKET_NAME');
      }
      row.validation_errors = [...new Set(row.validation_errors)];
      if (row.eligible_market === false && !result.ambiguous_markets.includes(row)) {
        result.ambiguous_markets.push(row);
      }
    }

    // The legacy v14 parser does not consume row.eligible_market from the raw
    // buckets. Therefore every blocked row must be physically removed from the
    // compatibility arrays and retained only in the canonical audit block.
    for (const key of ['match_total', 'half_total', 'quarter_total', 'home_ind_total', 'away_ind_total']) {
      result[key] = result[key].filter(row => row.eligible_market !== false);
    }

    // Sort totals by line value
    for (const key of ['match_total', 'half_total', 'quarter_total', 'quarter_dnb', 'home_ind_total', 'away_ind_total', 'ambiguous_markets'])
      result[key].sort((a, b) => String(a.scope ?? '').localeCompare(String(b.scope ?? '')) || (a.line ?? 0) - (b.line ?? 0));
    result.market_outcomes.sort((a, b) => String(a.scope ?? '').localeCompare(String(b.scope ?? '')) || (a.line ?? 0) - (b.line ?? 0));

    // Sort handicaps by handicap value
    for (const key of ['match_handicap', 'half_handicap', 'quarter_handicap'])
      result[key].sort((a, b) => (a.handicap ?? 0) - (b.handicap ?? 0));

    // Drop entries where ALL odds are null (both sides missing = no market)
    for (const key of ['match_total', 'half_total', 'quarter_total', 'home_ind_total', 'away_ind_total', 'ambiguous_markets'])
      result[key] = result[key].filter(x => x.overOdd !== null || x.underOdd !== null);
    result.market_outcomes = result.market_outcomes.filter(x => x.overOdd !== null || x.underOdd !== null);

    for (const key of ['match_handicap', 'half_handicap', 'quarter_handicap'])
      result[key] = result[key].filter(x => x.homeHcpOdd !== null || x.awayHcpOdd !== null);

    for (const key of ['match_1x2', 'quarter_1x2', 'quarter_dnb'])
      result[key] = result[key].filter(x => x.homeOdd !== null || x.awayOdd !== null);

    // Keep the complete ТЗ representation inside an object, not as another
    // top-level list bucket. Legacy v14 iterates top-level list buckets and would
    // otherwise emit UNSUPPORTED_MARKET audit noise for the canonical rows.
    const canonicalOutcomes = result.market_outcomes;
    const ambiguousOutcomes = result.ambiguous_markets;
    delete result.market_outcomes;
    delete result.ambiguous_markets;
    result.market_identity = {
      schema_version: '2026-08-03-market-identity-v1',
      outcomes: canonicalOutcomes,
      ambiguous_outcomes: ambiguousOutcomes,
    };

    // Remove empty legacy arrays. market_identity is an object and remains.
    for (const key of Object.keys(result))
      if (Array.isArray(result[key]) && result[key].length === 0) delete result[key];

    // ─── Schema description for downstream model ─────────────────────────────
    // Explains every key so the model can never confuse scope or market type.
    result._schema = {
      version: '2026-08-03-market-identity-v1',
      canonical_array: 'market_identity.outcomes',
      compatibility_arrays: [
        'match_total', 'half_total', 'quarter_total',
        'home_ind_total', 'away_ind_total',
      ],
      required_fields: [
        'market_type', 'scope', 'team_side', 'team_name', 'line',
        'overOdd', 'underOdd', 'raw_market_name', 'raw_selection_name',
        'source_market_id', 'source_outcome_id', 'source', 'fetchedAt',
        'eligible_market',
      ],
      market_types: {
        MATCH_TOTAL: 'Загальний тотал матчу; team_side/team_name мають бути null.',
        QUARTER_TOTAL: 'Загальний тотал конкретної чверті; team_side/team_name мають бути null.',
        TEAM_IT_MATCH: 'Індивідуальний тотал команди за матч.',
        TEAM_IT_QUARTER: 'Індивідуальний тотал команди за конкретну чверть.',
        TEAM_IT_H1: 'Індивідуальний тотал команди за першу половину.',
        TEAM_IT_H2: 'Індивідуальний тотал команди за другу половину.',
        H1_TOTAL: 'Сумарний тотал обох команд за першу половину; compatibility extension.',
        H2_TOTAL: 'Сумарний тотал обох команд за другу половину; compatibility extension.',
        AMBIGUOUS_TEAM_IT_QUARTER: 'Низька чвертна лінія схожа на Team IT, але команда не підтверджена; eligible_market=false.',
        AMBIGUOUS_TEAM_IT: 'Командний тотал без надійної team identity; eligible_market=false.',
      },
      scope_values: ['Match', 'H1', 'H2', 'Q1', 'Q2', 'Q3', 'Q4'],
      validation_rules: [
        'QUARTER_TOTAL не може містити team_side/team_name.',
        'TEAM_IT_* обов’язково має team_side і team_name.',
        'Scope визначається лише з raw_market_name або source_tab, не з порядку елементів.',
        'Лінія чверті <30 поруч із combined total 35-60 без team identity блокується.',
        'MATCH_TOTAL <120 без raw_market_name блокується.',
      ],
      notes: 'market_identity.outcomes є канонічним ТЗ-масивом. Старі bucket-масиви збережені без перейменування для math_script.py та v14 advisor; blocked rows у них не потрапляють.',
    };

    return result;

  } finally {
    if (detailPage !== page) await detailPage.close().catch(() => {});
    await page.close().catch(() => {});
  }
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * @param {string}  matchId
 * @param {string}  outputDir
 * @param {object}  participants   — { homeId, awayId } (compat only, unused)
 * @param {string|null} lineFilename
 * @param {string}  homeName       — home team display name (plain string)
 * @param {string}  awayName       — away team display name (plain string)
 * @param {import('playwright').BrowserContext} context
 * @param {string}  [liveStatus='']
 * @param {boolean} [isPrematch=false] — authoritative dc_ status is Not Started
 */
export async function fetchAndSaveLines(
  matchId,
  outputDir,
  participants = null,
  lineFilename  = null,
  homeName,
  awayName,
  context,
  liveStatus    = '',
  isPrematch    = false
) {
  lineFilename = lineFilename ?? `line_result_${matchId}.json`;

  const writeJsonAtomic = (target, value) => {
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), 'utf-8');
    fs.renameSync(temporary, target);
  };

  if (!homeName || !awayName) {
    const msg = `fetchAndSaveLines: homeName/awayName not provided (got "${homeName}" / "${awayName}") — skipping betking scrape`;
    console.warn(`  [betking] ⚠ ${msg}`);
    fs.mkdirSync(outputDir, { recursive: true });
    const err = { error: 'missing_team_names', matchId, source: 'betking' };
    writeJsonAtomic(path.join(outputDir, lineFilename), err);
    return err;
  }

  console.log(`\n--- Завантаження ліній (betking.com.ua)… ---`);
  console.log(`  Match: "${homeName}" vs "${awayName}"`);
  console.log(`  liveStatus: "${liveStatus}"`);
  console.log(`  isPrematch: ${isPrematch}`);

  let parsed = null;
  let attemptsUsed = 0;
  let lastError = null;
  const CORE_MARKET_KEYS = [
    'match_handicap', 'half_handicap', 'quarter_handicap',
    'match_total', 'half_total', 'quarter_total',
    'home_ind_total', 'away_ind_total',
  ];
  const coreMarketCount = value => CORE_MARKET_KEYS.reduce(
    (sum, key) => sum + (Array.isArray(value?.[key]) ? value[key].length : 0), 0
  );
  const MAX_SCRAPE_ATTEMPTS = 3;

  for (let attempt = 1; attempt <= MAX_SCRAPE_ATTEMPTS; attempt++) {
    attemptsUsed = attempt;
    try {
      const candidate = await scrapeBetking(context, homeName, awayName, liveStatus, isPrematch);
      // null means the match card itself was not found. Retrying the same lobby
      // immediately is expensive and does not address the HT repricing race.
      if (!candidate) {
        parsed = null;
        break;
      }
      parsed = candidate;
      const usable = coreMarketCount(parsed);
      if (usable > 0) break;
      if (attempt < MAX_SCRAPE_ATTEMPTS) {
        const delay = attempt * 2500;
        console.warn(`  [betking] ⚠ Match card opened but 0 core line markets were populated ` +
                     `(attempt ${attempt}/${MAX_SCRAPE_ATTEMPTS}); Betking may be repricing. Retrying in ${delay} ms…`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    } catch (e) {
      lastError = e;
      console.warn(`  [betking] ⚠ Помилка скрапінгу (attempt ${attempt}/${MAX_SCRAPE_ATTEMPTS}): ${e.message}`);
      console.warn(e.stack);
      if (attempt < MAX_SCRAPE_ATTEMPTS) {
        await new Promise(resolve => setTimeout(resolve, attempt * 2500));
      }
    }
  }

  fs.mkdirSync(outputDir, { recursive: true });
  const outPath = path.join(outputDir, lineFilename);

  if (!parsed) {
    const empty = { error: 'scrape_failed', errorMessage: lastError?.message ?? null, matchId, source: 'betking', homeName, awayName, attemptsUsed };
    // Keep the previous snapshot for diagnosis instead of silently destroying it.
    if (fs.existsSync(outPath)) fs.copyFileSync(outPath, `${outPath}.last_good`);
    writeJsonAtomic(outPath, empty);
    return empty;
  }

  parsed.meta = {
    source: 'betking',
    fetchedAt: new Date().toISOString(),
    matchId,
    isPrematch,
    attemptsUsed,
    coreMarketCount: coreMarketCount(parsed),
    retryExhaustedWithNoCoreMarkets: coreMarketCount(parsed) === 0,
  };
  writeJsonAtomic(outPath, parsed);
  console.log(`✅ Лінії збережено: ${outPath}`);

  const KEYS = ['match_1x2','match_handicap','half_handicap','quarter_handicap','match_total','half_total','quarter_total','quarter_dnb','quarter_1x2','quarter_btts','quarter_race','win_margin','last_digit','home_ind_total','away_ind_total'];
  for (const k of KEYS) if (parsed[k]) console.log(`  ${k}: ${parsed[k].length} рядків`);

  return parsed;
}
