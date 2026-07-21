import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'src' / 'super_basket_vps_system.py'
spec = importlib.util.spec_from_file_location('super_basket', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

base = {
    'stage': 'EARLY_LIVE',
    'current_quarter': 2,
    'quarter_seconds': 600,
    'elapsed_game_seconds': 660,
    'full_game_seconds': 2400,
    'trigger_checkpoint': 1,
}

def market(t):
    return {'market_type': t, 'segment': {
        'MATCH_TOTAL': 'MATCH',
        'TEAM_IT_MATCH': 'MATCH',
        'H1_TOTAL': 'H1',
        'TEAM_IT_H1': 'H1',
        'H2_TOTAL': 'H2',
        'TEAM_IT_H2': 'H2',
        'CURRENT_QUARTER_TOTAL': 'Q2',
    }.get(t, 'MATCH')}

blocked = ['MATCH_TOTAL', 'TEAM_IT_MATCH', 'H2_TOTAL', 'TEAM_IT_H2', 'CURRENT_QUARTER_TOTAL']
for t in blocked:
    result = mod._router(market(t), dict(base))
    assert result['hard_block'] is True, (t, result)
    assert result['reason'] == 'CHECKPOINT1_ONLY_H1_TOTAL_AND_H1_TEAM_IT', (t, result)
    print(f'PASS: checkpoint 1 blocks {t}')

for t in ['H1_TOTAL', 'TEAM_IT_H1']:
    result = mod._router(market(t), dict(base))
    assert result['hard_block'] is False, (t, result)
    print(f'PASS: checkpoint 1 permits {t}')

ht = dict(base, stage='HT', current_quarter=3, elapsed_game_seconds=1200, trigger_checkpoint=2)
for t in ['MATCH_TOTAL', 'TEAM_IT_MATCH', 'H2_TOTAL', 'TEAM_IT_H2']:
    result = mod._router(market(t), ht)
    assert result['hard_block'] is False, (t, result)
    print(f'PASS: checkpoint 2 does not apply Q1-only block to {t}')

print('\nAll Python checkpoint-router tests passed.')

source = {
    'lines': {'match_total': [{'scope': 'Match', 'line': 145.5, 'overOdd': 21, 'underOdd': 1.83}]},
}
canonical = {'home_team': 'A', 'away_team': 'B', 'current_quarter': 3}
markets, _ = mod.parse_markets(source, canonical, mod.DEFAULT_CONFIG)
bad = next(item for item in markets if item['side'] == 'OVER')
good = next(item for item in markets if item['side'] == 'UNDER')
assert 'ODDS_ABOVE_MAXIMUM' in bad['parser_issues'], bad
assert good['eligible_market'] is True, good
print('PASS: malformed odds above configured maximum are blocked')
