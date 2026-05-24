/**
 * src/utils/slugify.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Shared slug builder used by orchestrator.js and match_h2h_export.js.
 *
 * Examples:
 *   slugify('Динамо Київ')    → 'Dynamo_Kyiv'
 *   slugify('Man. United')   → 'Man_United'
 *   slugify('PSG')           → 'PSG'
 */

/** Cyrillic → Latin transliteration table (Ukrainian + Russian). */
const TRANSLIT_MAP = {
  'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh',
  'з':'z','и':'y','і':'i','ї':'i','й':'j','к':'k','л':'l','м':'m','н':'n',
  'о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
  'ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'yu','я':'ya',
  'А':'A','Б':'B','В':'V','Г':'G','Ґ':'G','Д':'D','Е':'E','Є':'Ie','Ж':'Zh',
  'З':'Z','И':'Y','І':'I','Ї':'I','Й':'J','К':'K','Л':'L','М':'M','Н':'N',
  'О':'O','П':'P','Р':'R','С':'S','Т':'T','У':'U','Ф':'F','Х':'Kh','Ц':'Ts',
  'Ч':'Ch','Ш':'Sh','Щ':'Shch','Ь':'','Ю':'Yu','Я':'Ya',
};

/**
 * Convert a team name to a filesystem-safe ASCII slug.
 *
 * @param {string} value  — raw team name, e.g. "Динамо Київ" or "Man. United"
 * @returns {string}      — slug, e.g. "Dynamo_Kyiv" or "Man_United"
 */
export function slugify(value) {
  return String(value || 'team')
    // transliterate Cyrillic characters
    .split('').map(c => TRANSLIT_MAP[c] ?? c).join('')
    // Unicode normalise + strip combining diacritics
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    // replace anything that is not word-char or hyphen with underscore
    .replace(/[^\w\-]+/g, '_')
    // collapse multiple underscores
    .replace(/_+/g, '_')
    // trim leading/trailing underscores
    .replace(/^_+|_+$/g, '')
    // hard limit to keep filenames sane
    .slice(0, 60)
    || 'team';
}