from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "source" / "super_basket_v15_5_history_override.py"
if not MODULE_PATH.exists():
    MODULE_PATH = ROOT / "super_basket_v15_5_history_override.py"
SPEC = importlib.util.spec_from_file_location("sb_v1552_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {MODULE_PATH}")
sb = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sb
SPEC.loader.exec_module(sb)


def candidate(
    *,
    market_type: str = "MATCH_TOTAL",
    team: str | None = None,
    side: str = "OVER",
    line: float = 150.0,
    projection: float = 153.0,
    p_history: float = 0.73,
    p_scenario: float = 0.73,
    p_final: float = 0.50,
    action: str = "PASS",
    odds: float | None = 1.80,
) -> sb.ScoreCandidate:
    exact_probability = 0.80
    return sb.ScoreCandidate(
        market_id=f"{market_type}-{team}-{side}-{line}-{p_final}",
        market_type=market_type,
        segment="MATCH",
        team=team,
        side=side,
        line=line,
        odds=odds,
        bookmaker="TEST_BK",
        real_line=True,
        horizon="FINAL",
        projection_home=80.0,
        projection_away=73.0,
        raw_projection=projection,
        projection=projection,
        projection_bias=0.0,
        calibration_scope="TEST",
        calibration_n=100,
        edge=(projection - line) if side == "OVER" else (line - projection),
        sigma=12.0,
        edge_z=0.25,
        p_score_line=0.55,
        p_residual_direction=0.50,
        p_score_calibrated=0.55,
        p_history=p_history,
        p_scenario=p_scenario,
        p_final=p_final,
        break_even=0.55,
        expected_value=0.0,
        action=action,
        status="PASS — TEST",
        stake="0%",
        blockers=["TEST"],
        confirmations=[],
        historical_zones=[{
            "available": True,
            "kind": "CURRENT_LINE_HISTORY",
            "side": side,
            "line": line,
            "probability": exact_probability,
            "hits": 56,
            "losses": 14,
            "pushes": 0,
            "n": 70,
        }],
        soft_conflict=None,
        history_override=None,
        hot_continuation=False,
        source_evaluation={},
    )


class Q4InfoLayerTests(unittest.TestCase):
    def collect(self, rows, *, stage="Q4_LIVE", selected_action="PASS"):
        with patch.dict(os.environ, {"SUPER_BASKET_INFO_Q4_ENABLED": "true"}, clear=False):
            return sb.collect_q4_history_scenario_info(
                rows, stage=stage, selected_action=selected_action, age_blocked=False,
            )

    def test_inclusive_73_and_delta_3_qualify(self):
        rows = self.collect([candidate()])
        self.assertEqual(1, len(rows))
        self.assertEqual(3.0, rows[0]["raw_delta"])
        self.assertEqual(0.73, rows[0]["p_history"])
        self.assertEqual(0.73, rows[0]["p_scenario"])

    def test_play_or_risk_file_is_always_skipped(self):
        qualifying = candidate()
        self.assertEqual([], self.collect([qualifying], selected_action="PLAY"))
        self.assertEqual([], self.collect([qualifying], selected_action="RISK"))

    def test_only_pass_candidates_are_eligible(self):
        self.assertEqual([], self.collect([candidate(action="PLAY")]))
        self.assertEqual([], self.collect([candidate(action="RISK")]))

    def test_stage_market_probability_and_delta_gates(self):
        self.assertEqual([], self.collect([candidate()], stage="HT"))
        self.assertEqual([], self.collect([candidate(market_type="H1_TOTAL")]))
        self.assertEqual([], self.collect([candidate(p_history=0.729999)]))
        self.assertEqual([], self.collect([candidate(p_scenario=0.729999)]))
        self.assertEqual([], self.collect([candidate(projection=153.0001)]))

    def test_one_best_per_market_type_by_p_final(self):
        rows = self.collect([
            candidate(line=149.0, projection=151.0, p_final=0.51),
            candidate(line=150.0, projection=152.0, p_final=0.59),
            candidate(
                market_type="TEAM_IT_MATCH", team="Away", side="UNDER",
                line=75.0, projection=73.0, p_final=0.54,
            ),
        ])
        self.assertEqual(2, len(rows))
        by_market = {row["market_type"]: row for row in rows}
        self.assertEqual(150.0, by_market["MATCH_TOTAL"]["line"])
        self.assertEqual(75.0, by_market["TEAM_IT_MATCH"]["line"])

    def test_info_message_contains_required_fields_and_zero_budget(self):
        variants = self.collect([candidate()])
        result = {
            "score_forecast": {
                "match_name": "Home vs Away",
                "stage": "Q4_LIVE",
                "final_home": 80.0,
                "final_away": 73.0,
            },
            "q4_history_scenario_info_variants": variants,
        }
        messages = sb.q4_history_scenario_info_messages(result)
        self.assertEqual(1, len(messages))
        message = messages[0]
        for expected in (
            "INFO 0%", "НЕ PLAY І НЕ RISK", "Прогнозований рахунок",
            "Δ прогноз−лінія", "Directional edge", "P_history",
            "P_scenario", "P_final", "Exact-line history",
        ):
            self.assertIn(expected, message)
        self.assertLessEqual(len(message), sb.INFO_Q4_TELEGRAM_MAX_CHARS)

    def test_kill_switch_disables_only_info_collector(self):
        with patch.dict(os.environ, {"SUPER_BASKET_INFO_Q4_ENABLED": "false"}, clear=False):
            self.assertEqual([], sb.collect_q4_history_scenario_info(
                [candidate()], stage="Q4_LIVE", selected_action="PASS", age_blocked=False,
            ))


class HistoryScenario9090InfoLayerTests(unittest.TestCase):
    def collect(self, rows, *, excluded=(), age_blocked=False):
        with patch.dict(os.environ, {"SUPER_BASKET_INFO_90_90_ENABLED": "true"}, clear=False):
            return sb.collect_history_scenario_90_info(
                rows,
                excluded_market_ids=excluded,
                age_blocked=age_blocked,
            )

    def test_inclusive_90_90_has_no_stage_market_action_or_delta_gate(self):
        row = candidate(
            market_type="H1_TOTAL",
            side="UNDER",
            line=100.0,
            projection=150.0,
            p_history=0.90,
            p_scenario=0.90,
            action="PLAY",
        )
        variants = self.collect([row])
        self.assertEqual(1, len(variants))
        self.assertEqual("H1_TOTAL", variants[0]["market_type"])
        self.assertEqual("PLAY", variants[0]["source_action"])
        self.assertEqual(50.0, variants[0]["raw_delta"])

    def test_both_probabilities_must_reach_90(self):
        self.assertEqual([], self.collect([candidate(p_history=0.899999, p_scenario=0.95)]))
        self.assertEqual([], self.collect([candidate(p_history=0.95, p_scenario=0.899999)]))

    def test_returns_all_unique_lines_and_excludes_already_sent_market_id(self):
        first = candidate(line=150.0, projection=160.0, p_history=0.95, p_scenario=0.94)
        second = candidate(line=151.0, projection=160.0, p_history=0.91, p_scenario=0.92)
        variants = self.collect([first, first, second], excluded=[first.market_id])
        self.assertEqual(1, len(variants))
        self.assertEqual(second.market_id, variants[0]["market_id"])

    def test_real_odds_age_and_kill_switch_are_safety_gates(self):
        no_odds = candidate(p_history=0.95, p_scenario=0.95, odds=None)
        self.assertEqual([], self.collect([no_odds]))
        self.assertEqual([], self.collect([
            candidate(p_history=0.95, p_scenario=0.95)
        ], age_blocked=True))
        with patch.dict(os.environ, {"SUPER_BASKET_INFO_90_90_ENABLED": "false"}, clear=False):
            self.assertEqual([], sb.collect_history_scenario_90_info([
                candidate(p_history=0.95, p_scenario=0.95)
            ]))

    def test_90_90_message_contains_full_info_and_zero_budget(self):
        variants = self.collect([
            candidate(p_history=0.95, p_scenario=0.94, line=150.0, projection=160.0)
        ])
        result = {
            "score_forecast": {
                "match_name": "Home vs Away",
                "stage": "HT",
                "final_home": 84.0,
                "final_away": 76.0,
            },
            "history_scenario_90_info_variants": variants,
        }
        messages = sb.history_scenario_90_info_messages(result)
        self.assertEqual(1, len(messages))
        message = messages[0]
        for expected in (
            "ІСТОРІЯ 90% + СЦЕНАРІЙ 90%", "INFO 0%", "Прогнозований рахунок",
            "Δ прогноз−лінія", "Directional edge", "P_history", "P_scenario",
            "P_final", "Exact-line history", "без обмеження stage, market або Δ",
        ):
            self.assertIn(expected, message)
        self.assertLessEqual(len(message), sb.INFO_90_TELEGRAM_MAX_CHARS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
