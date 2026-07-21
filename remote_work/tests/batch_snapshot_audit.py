#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

root = Path('/app')
engine_path = root / 'src/super_basket_vps_system.py'
spec = importlib.util.spec_from_file_location('super_basket', engine_path)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)

data_dir = root / 'src/data'
zones_path = root / 'src/02_team_relative_stat_zones_COMPACT.json'
files = sorted(p for p in data_dir.glob('*.json') if not p.stem.endswith('_result') and not p.name.startswith(('analysis_', 'line_result_')))
counts = Counter()
stages = Counter()
reasons = Counter()
rows = []

with tempfile.TemporaryDirectory(prefix='sb-audit-') as temp:
    db = Path(temp) / 'audit.sqlite3'
    for path in files:
        try:
            source = json.loads(path.read_text(encoding='utf-8'))
            context = source.get('analysis_context') if isinstance(source.get('analysis_context'), dict) else {}
            checkpoint = context.get('trigger_checkpoint')
            result = engine.process_vps_match_file(
                path,
                output_path=Path(temp) / (path.stem + '_result.json'),
                zones_path=zones_path,
                db_path=db,
                dry_run=True,
                enable_gpt=False,
                require_gpt=False,
                enable_telegram=False,
                checkpoint=int(checkpoint) if checkpoint in (1, 2, 3, '1', '2', '3') else None,
            )
            system = result['super_basket_system']
            calc = result['super_basket_calculation']
            decision = system['decision']
            action = decision['action']
            stage = calc['canonical_snapshot']['stage']
            counts[action] += 1
            stages[stage] += 1
            for reason in decision.get('reason_codes', []):
                reasons[reason] += 1
            rows.append({
                'file': path.name,
                'match_id': calc['canonical_snapshot']['match_id'],
                'stage': stage,
                'stats': calc['canonical_snapshot']['stat_support'],
                'action': action,
                'status': decision['status'],
                'p_final': decision['probabilities'].get('p_final'),
                'market': decision.get('market'),
                'reasons': decision.get('reason_codes', []),
            })
        except Exception as exc:
            counts['ERROR'] += 1
            rows.append({'file': path.name, 'action': 'ERROR', 'error': f'{type(exc).__name__}: {exc}'})

report = {
    'input_files': len(files),
    'actions': dict(counts),
    'stages': dict(stages),
    'top_reasons': reasons.most_common(20),
    'active_signals': [row for row in rows if row['action'] in {'RISK', 'PLAY'}],
    'errors': [row for row in rows if row['action'] == 'ERROR'],
    'rows': rows,
}
target = Path('/tmp/super_basket_snapshot_audit.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: report[key] for key in ('input_files', 'actions', 'stages', 'top_reasons', 'active_signals', 'errors')}, ensure_ascii=False, indent=2))
