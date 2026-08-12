#!/usr/bin/env python3
import importlib.util
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parent
PATH=ROOT/'super_basket_v15_5_history_override.py'
spec=importlib.util.spec_from_file_location('v155_test_target',PATH)
m=importlib.util.module_from_spec(spec)
sys.modules['v155_test_target']=m
spec.loader.exec_module(m)

def run(**kw):
    base=dict(p_final=.72,p_score=.68,edge=5.0,edge_z=.55,odds=1.85,
              p_history=.80,p_scenario=.68,p_residual=.55,real=True,stage='HT',
              market_type='MATCH_TOTAL',segment='MATCH',side='OVER',
              source_evaluation={},raw_match={},age_blocked=False)
    base.update(kw)
    return m.classify_candidate(**base)

assert run(edge=3.9)[0]=='PASS'
assert run()[0]=='RISK'
assert run(p_history=.59)[0]=='PASS'
assert run(p_scenario=.59)[0]=='PASS'
fake={'stat_comparison':{'fake_over':True}}
r=run(source_evaluation=fake,p_history=.91,p_scenario=.70)
assert r[0]=='RISK' and r[7]=='FAKE_OVER' and r[8]=='STRONG_90_PLUS'
r=run(source_evaluation=fake,p_history=.80,p_scenario=.70)
assert r[0]=='RISK' and r[8]=='SUPPORTED_75_PLUS'
assert run(source_evaluation=fake,p_history=.70,p_scenario=.70)[0]=='PASS'
assert run(age_blocked=True)[0]=='PASS'
assert run(market_type='CURRENT_QUARTER_TOTAL',segment='Q3',p_final=.90,p_score=.85,edge=5.0,edge_z=1.0)[0]=='RISK'
assert run(market_type='CURRENT_QUARTER_TOTAL',segment='Q3',p_final=.90,p_score=.85,edge=8.0,edge_z=1.0)[0]=='PLAY'
raw={'match':{'quarters':{'q1':{'home':26,'away':21},'q2':{'home':28,'away':22}}}}
item={'stat_comparison':{'indicators':{'score_or_efg_high':True,'volume_high':True},'over_gate_score':4}}
assert run(side='UNDER',source_evaluation=item,raw_match=raw,p_history=.95,p_scenario=.80)[0]=='PASS'
assert m.detected_youth_age({'match':{'name':'Spain U17 vs France U17'}})==17
assert m.detected_youth_age({'match':{'name':'Spain U18 vs France U18'}})==18
print('13/13 LOGIC TESTS PASS')
