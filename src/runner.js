#!/usr/bin/env node
/**
 * HT Basketball Bot — Node.js runner (ESM)
 * Usage:  node runner.js ./data/matches.json
 *         node runner.js ./data/matches.json --pretty
 *         node runner.js ./data/matches.json --output ./results/out.json
 *
 * Calls ht_bot.py via child_process, receives JSON on stdout.
 */

import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── CLI args ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
if (args.length === 0 || args[0] === "--help") {
  console.error("Usage: node runner.js <path_to_json> [--pretty] [--output <out_path>]");
  process.exit(1);
}

const inputFile  = args[0];
const pretty     = args.includes("--pretty");
const outIdx     = args.indexOf("--output");
const outputFile = outIdx !== -1 ? args[outIdx + 1] : null;

// ── Resolve python & script paths ───────────────────────────────────────────
const pythonCmd  = process.env.PYTHON_BIN ?? "python3";
const scriptPath = path.resolve(__dirname, "math_script.py");

if (!fs.existsSync(scriptPath)) {
  console.error(`ERROR: math_script.py not found at ${scriptPath}`);
  process.exit(1);
}
if (!fs.existsSync(inputFile)) {
  console.error(`ERROR: Input file not found: ${inputFile}`);
  process.exit(1);
}

// ── Run ─────────────────────────────────────────────────────────────────────
async function main() {
  console.error(`[runner] Starting math_script.py on: ${inputFile}`);

  const { stdout, stderr } = await execFileAsync(
    pythonCmd,
    [scriptPath, inputFile],
    { maxBuffer: 50 * 1024 * 1024 },  // 50 MB — large history files
  );

  if (stderr) process.stderr.write("[ht_bot] " + stderr);

  let results;
  try {
    results = JSON.parse(stdout);
  } catch (e) {
    console.error("[runner] Failed to parse JSON output from ht_bot.py:", e.message);
    process.exit(1);
  }

  // ── Print summary to stderr ──────────────────────────────────────────────
  console.error(`[runner] Processed ${results.length} match(es)`);
  for (const [i, r] of results.entries()) {
    const m     = r.meta        ?? {};
    const s     = r.sample_gate ?? {};
    const b     = (r.blockers   ?? []).join(", ") || "none";
    const cands = (r.candidates ?? []).length;
    console.error(
      `  [${i + 1}] ${m.match} | ${m.score} | ` +
      `sample=${s.status} (A=${s.team_a_valid_games},B=${s.team_b_valid_games}) | ` +
      `blockers=[${b}] | candidates=${cands}`,
    );
  }

  // ── Output ───────────────────────────────────────────────────────────────
  const out = pretty
    ? JSON.stringify(results, null, 2)
    : JSON.stringify(results);

  if (outputFile) {
    fs.mkdirSync(path.dirname(path.resolve(outputFile)), { recursive: true });
    fs.writeFileSync(outputFile, out, "utf-8");
    console.error(`[runner] Saved to: ${outputFile}`);
  } else {
    process.stdout.write(out + "\n");
  }
}

main().catch((err) => {
  console.error("[runner] Fatal:", err.message);
  process.exit(1);
});