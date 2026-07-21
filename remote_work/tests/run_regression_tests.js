'use strict';

import fs from 'fs';
import vm from 'vm';

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`Function ${name} not found`);
  const parenStart = source.indexOf('(', start);
  let parenDepth = 0;
  let brace = -1;
  for (let i = parenStart; i < source.length; i++) {
    if (source[i] === '(') parenDepth++;
    else if (source[i] === ')') {
      parenDepth--;
      if (parenDepth === 0) {
        brace = source.indexOf('{', i);
        break;
      }
    }
  }
  if (brace < 0) throw new Error(`Function ${name} opening brace not found`);
  let depth = 0;
  for (let i = brace; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`Function ${name} not closed`);
}

function assertEqual(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
  console.log(`PASS: ${label}`);
}

// Test the exact pure functions extracted from the patched files.
const root = new URL('../src/', import.meta.url);
const stageSource = fs.readFileSync(new URL('match_stage.js', root), 'utf8');
const stageMonitorSource = fs.readFileSync(new URL('stage_monitor.js', root), 'utf8');
const stageContext = {};
vm.createContext(stageContext);
vm.runInContext([
  extractFunction(stageSource, 'parseKV'),
  "const PERIOD_KEY_PAIRS = [['BA','BB'],['BC','BD'],['BE','BF'],['BG','BH']];",
  extractFunction(stageSource, 'hasScoreValue'),
  extractFunction(stageSource, 'parseQuarterProgress'),
].join('\n'), stageContext);

const progress = (raw, status, liveMinute = null) => vm.runInContext(
  `parseQuarterProgress(${JSON.stringify(raw)}, ${JSON.stringify({status, liveMinute})})`,
  stageContext,
);

assertEqual(progress('', 'not_started').completedQuarters, 0, 'not-started has zero completed quarters');
assertEqual(progress('BA÷21¬BB÷18', 'break').completedQuarters, 1, 'Q1 break is completedQ=1');
assertEqual(progress('BA÷21¬BB÷18¬BC÷4¬BD÷2', 'live', 1).completedQuarters, 1, 'active Q2 is completedQ=1');
assertEqual(progress('BA÷21¬BB÷18¬BC÷20¬BD÷19', 'break').completedQuarters, 2, 'halftime is completedQ=2');
assertEqual(progress('BA÷21¬BB÷18¬BC÷20¬BD÷19¬BE÷17¬BF÷22¬BG÷2¬BH÷0', 'live', 1).completedQuarters, 3, 'active Q4 is completedQ=3');

const classifierContext = {};
vm.createContext(classifierContext);
vm.runInContext(extractFunction(stageMonitorSource, 'checkpointProgressState'), classifierContext);
const classify = (completed, idx) => vm.runInContext(
  `checkpointProgressState(${JSON.stringify({completedQuarters: completed})}, ${idx})`,
  classifierContext,
);

// Delayed-start regression: all checkpoint windows may be open, but each must
// react only to its own actual quarter boundary.
assertEqual(classify(1, 0), 'TARGET', 'Q1 watcher fires at Q1 completion');
assertEqual(classify(1, 1), 'WAIT', 'HT watcher ignores Q1 break');
assertEqual(classify(1, 2), 'WAIT', 'Q3 watcher ignores Q1 break');
assertEqual(classify(2, 0), 'STALE', 'late Q1 watcher does not replay at halftime');
assertEqual(classify(2, 1), 'TARGET', 'HT watcher fires at Q2 completion');
assertEqual(classify(2, 2), 'WAIT', 'Q3 watcher ignores halftime');
assertEqual(classify(3, 2), 'TARGET', 'Q3 watcher fires in Q3 break/Q4');

console.log('\nAll JavaScript regression tests passed.');
