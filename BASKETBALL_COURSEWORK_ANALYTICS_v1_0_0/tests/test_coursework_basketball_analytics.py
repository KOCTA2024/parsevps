from __future__ import annotations

import copy
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
FIXTURE_DIR = ROOT / 'fixtures' if (ROOT / 'fixtures').exists() else WORKSPACE / 'upload'
sys.path.insert(0, str(ROOT))

import coursework_basketball_analytics as analytics  # noqa: E402


FIXTURES = [
    FIXTURE_DIR / 'KK_Split_vs_KK_Zabok_ABY9CUR1.json',
    FIXTURE_DIR / 'Atlanta_Dream_W_vs_Phoenix_Mercury_W_DOCX_ONLY_MISSING_HISTORY.json',
    FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json',
    FIXTURE_DIR / 'Hoventut_vs_Burgos_zXTEuz1D.json',
    FIXTURE_DIR / 'New_York_Liberty_W_vs_Dallas_Wings_W_AiEBDgEk.json',
]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


def all_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_strings(child)
    elif isinstance(value, str):
        yield value


class PureFunctionTests(unittest.TestCase):
    def test_number_parser_rejects_non_finite_values(self):
        self.assertEqual(analytics.to_number('12,5'), 12.5)
        self.assertIsNone(analytics.to_number('nan'))
        self.assertIsNone(analytics.to_number(float('inf')))

    def test_percentile_interpolates(self):
        self.assertEqual(analytics.percentile([10, 20, 30], 0.5), 20)
        self.assertEqual(analytics.percentile([10, 20], 0.5), 15)

    def test_canonical_snapshot_extracts_parser_state(self):
        source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
        snapshot = analytics.canonical_snapshot(source)
        self.assertEqual(snapshot['match_id'], 'GMWxPqlg')
        self.assertEqual(snapshot['checkpoint'], 2)
        self.assertEqual(snapshot['current_quarter'], 3)
        self.assertEqual(snapshot['schema_errors'], [])

    def test_remaining_plan_after_each_checkpoint(self):
        base = {
            'finished': False,
            'current_quarter': 1,
            'checkpoint': 0,
            'current_quarter_minutes_left': 10,
            'quarter_minutes': 10,
        }
        expected = {0: [1, 2, 3, 4], 1: [2, 3, 4], 2: [3, 4], 3: [4]}
        for checkpoint, quarters in expected.items():
            current = min(4, checkpoint + 1)
            plan = analytics.resolve_remaining_plan({
                **base,
                'checkpoint': checkpoint,
                'current_quarter': current,
            })
            self.assertEqual(plan['future_quarters'], quarters)

    def test_partial_quarter_uses_only_remaining_fraction(self):
        plan = analytics.resolve_remaining_plan({
            'finished': False,
            'checkpoint': 2,
            'current_quarter': 3,
            'current_quarter_minutes_left': 4,
            'quarter_minutes': 10,
        })
        pool = {'games': [{'game_id': 'g1', 'quarter_totals': [40, 40, 50, 60]}]}
        result = analytics.historical_remaining_values(pool, plan)
        self.assertAlmostEqual(result['values'][0], 80.0)
        self.assertTrue(result['approximation_used'])

    def test_forecast_adds_current_points_once(self):
        canonical = {'score': {'total': 100}, 'finished': False}
        remaining = {'values': [30, 40, 50], 'approximation_used': False}
        forecast = analytics.build_forecast(canonical, remaining)
        self.assertEqual(forecast['forecast_final_total_points'], 140)
        self.assertTrue(forecast['already_scored_points_added_once'])

    def test_finished_match_forecast_equals_observed_total(self):
        forecast = analytics.build_forecast(
            {'score': {'total': 155}, 'finished': True},
            {'values': [], 'approximation_used': False},
        )
        self.assertEqual(forecast['forecast_final_total_points'], 155)
        self.assertEqual(forecast['central_interval_p10_p90'], [155, 155])

    def test_history_pool_excludes_current_technical_and_duplicates(self):
        row = {'mid': 'g1', 'hs': 80, 'as_': 75, 'q1t': 40, 'q2t': 35, 'q3t': 38, 'q4t': 42}
        source = {
            'raw_data': {
                'team_a_hist': [row, copy.deepcopy(row), {'mid': 'current', 'hs': 70, 'as_': 60, 'q1t': 30, 'q2t': 30, 'q3t': 30, 'q4t': 40}],
                'team_b_hist': [{'mid': 'technical', 'hs': 20, 'as_': 0, 'q1t': 5, 'q2t': 5, 'q3t': 5, 'q4t': 5}],
                'h2h_hist': [],
            }
        }
        canonical = {'match_id': 'current', 'regulation_minutes': 40}
        pool = analytics.build_unique_history_pool(source, canonical)
        self.assertEqual(pool['n_same_format'], 1)
        self.assertEqual(pool['excluded']['duplicate'], 1)
        self.assertEqual(pool['excluded']['current_match'], 1)
        self.assertEqual(pool['excluded']['technical_result'], 1)

    def test_research_hash_ignores_non_sport_sections(self):
        source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
        changed = copy.deepcopy(source)
        changed['lines'] = {'arbitrary': [1, 2, 3]}
        changed['raw_lines'] = {'arbitrary': 'changed'}
        changed['bookmaker_lines'] = {'arbitrary': 999}
        self.assertEqual(analytics.academic_input_hash(source), analytics.academic_input_hash(changed))

    def test_research_hash_changes_when_sport_state_changes(self):
        source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
        changed = copy.deepcopy(source)
        changed['match']['score']['total'] += 1
        self.assertNotEqual(analytics.academic_input_hash(source), analytics.academic_input_hash(changed))

    def test_readiness_boundaries(self):
        self.assertEqual(analytics.readiness_for(7, []), 'INSUFFICIENT_DATA')
        self.assertEqual(analytics.readiness_for(8, []), 'REVIEW_REQUIRED')
        self.assertEqual(analytics.readiness_for(20, []), 'READY')
        self.assertEqual(analytics.readiness_for(30, ['match_id']), 'INSUFFICIENT_DATA')


class BatchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='coursework_analytics_')
        cls.root = Path(cls.temp.name)
        cls.outbox = cls.root / 'out'
        cls.db = cls.root / 'analytics.sqlite3'
        cls.summary = analytics.process_batch(
            FIXTURES,
            cls.outbox,
            db_path=cls.db,
            workers=4,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_five_files_are_processed_concurrently(self):
        self.assertEqual(self.summary['requested_count'], 5)
        self.assertEqual(self.summary['processed_count'], 5)
        self.assertEqual(self.summary['failed_count'], 0)
        self.assertEqual(self.summary['stored_run_count'], 5)

    def test_complete_and_incomplete_readiness(self):
        by_name = {Path(row['source']).name: row['data_readiness'] for row in self.summary['items']}
        incomplete = 'Atlanta_Dream_W_vs_Phoenix_Mercury_W_DOCX_ONLY_MISSING_HISTORY.json'
        self.assertEqual(by_name[incomplete], 'INSUFFICIENT_DATA')
        for name, value in by_name.items():
            if name != incomplete:
                self.assertIn(value, {'READY', 'REVIEW_REQUIRED'})

    def test_outputs_have_only_neutral_research_fields(self):
        forbidden_keys = {'odds', 'bookmaker', 'line', 'stake', 'bankroll', 'profit', 'payout'}
        forbidden_values = {'PLAY', 'RISK PLAY', 'PASS'}
        paths = list(self.outbox.glob('*_analytics.json'))
        self.assertEqual(len(paths), 5)
        for path in paths:
            result = read_json(path)
            self.assertTrue(forbidden_keys.isdisjoint(set(all_keys(result))), path.name)
            self.assertTrue(forbidden_values.isdisjoint(set(all_strings(result))), path.name)
            self.assertTrue(result[analytics.OUTPUT_KEY]['integrity_report']['all_passed'])
            self.assertFalse(result[analytics.OUTPUT_KEY]['external_delivery']['enabled'])

    def test_outputs_are_valid_finite_json(self):
        for path in self.outbox.glob('*_analytics.json'):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('NaN', text)
            self.assertNotIn('Infinity', text)
            json.loads(text)

    def test_exact_rerun_is_idempotent(self):
        rerun = analytics.process_batch(FIXTURES, self.outbox, db_path=self.db, workers=2)
        self.assertEqual(rerun['processed_count'], 0)
        self.assertEqual(rerun['skipped_duplicate_count'], 5)
        self.assertEqual(rerun['stored_run_count'], 5)

    def test_different_checkpoints_are_independent(self):
        with tempfile.TemporaryDirectory(prefix='coursework_checkpoints_') as directory:
            root = Path(directory)
            source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
            paths = []
            for checkpoint in (1, 2):
                value = copy.deepcopy(source)
                value['analysis_context'] = {'trigger_checkpoint': checkpoint, 'research_replay': True}
                path = root / f'q{checkpoint}.json'
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                paths.append(path)
            summary = analytics.process_batch(paths, root / 'out', db_path=root / 'db.sqlite3', workers=2)
            self.assertEqual(summary['processed_count'], 2)
            self.assertEqual({row['checkpoint'] for row in summary['items']}, {1, 2})
            self.assertEqual(len({row['run_key'] for row in summary['items']}), 2)

    def test_changed_sport_state_same_checkpoint_creates_new_run(self):
        with tempfile.TemporaryDirectory(prefix='coursework_changed_') as directory:
            root = Path(directory)
            source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
            changed = copy.deepcopy(source)
            changed['match']['score']['total'] += 1
            changed['match']['score']['home'] += 1
            paths = []
            for name, value in (('a.json', source), ('b.json', changed)):
                path = root / name
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                paths.append(path)
            summary = analytics.process_batch(paths, root / 'out', db_path=root / 'db.sqlite3', workers=2)
            self.assertEqual(summary['processed_count'], 2)
            self.assertEqual(len({row['run_key'] for row in summary['items']}), 2)

    def test_non_sport_change_is_duplicate_in_same_batch(self):
        with tempfile.TemporaryDirectory(prefix='coursework_metadata_') as directory:
            root = Path(directory)
            source = read_json(FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json')
            changed = copy.deepcopy(source)
            changed['lines'] = {'changed': True}
            paths = []
            for name, value in (('a.json', source), ('b.json', changed)):
                path = root / name
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                paths.append(path)
            summary = analytics.process_batch(paths, root / 'out', db_path=root / 'db.sqlite3', workers=2)
            self.assertEqual(summary['processed_count'], 1)
            self.assertEqual(summary['skipped_duplicate_count'], 1)

    def test_failure_is_isolated(self):
        with tempfile.TemporaryDirectory(prefix='coursework_failure_') as directory:
            root = Path(directory)
            bad = root / 'bad.json'
            bad.write_text('{invalid', encoding='utf-8')
            summary = analytics.process_batch(
                [FIXTURE_DIR / 'Aris_vs_AEK_Athens_GMWxPqlg.json', bad],
                root / 'out',
                db_path=root / 'db.sqlite3',
                workers=2,
            )
            self.assertEqual(summary['processed_count'], 1)
            self.assertEqual(summary['failed_count'], 1)

    def test_output_filenames_include_checkpoint_and_hash(self):
        for row in self.summary['items']:
            name = Path(row['output']).name
            self.assertIn(f"_cp{row['checkpoint']}_", name)
            self.assertIn(row['input_hash'][:12], name)

    def test_sqlite_contains_one_row_per_processed_run(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            count = connection.execute('SELECT COUNT(*) FROM processed_runs').fetchone()[0]
            distinct = connection.execute('SELECT COUNT(DISTINCT run_key) FROM processed_runs').fetchone()[0]
        self.assertEqual(count, 5)
        self.assertEqual(distinct, 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
