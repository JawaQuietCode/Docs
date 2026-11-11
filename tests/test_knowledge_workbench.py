import json
import math
import tempfile
import unittest
from pathlib import Path

from memory.knowledge import KnowledgeWorkbench


class KnowledgeWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = KnowledgeWorkbench(Path(self.tmp.name) / "knowledge.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_claims_keep_provenance_and_temporal_validity(self):
        claim = self.store.add_claim(
            "Kwee", "uses_runtime", "Python",
            claim_type="observed", source_uri="https://example.org/runtime",
            evidence="The dependency manifest includes Python.",
            valid_from="2025-01-01", valid_until="2026-01-01",
        )
        self.assertTrue(claim["ok"])
        current = self.store.search_claims("Kwee Python", as_of="2025-06-01")
        self.assertEqual(current[0]["id"], claim["id"])
        self.assertEqual(current[0]["source_uri"], "https://example.org/runtime")
        self.assertEqual(self.store.search_claims("Kwee Python", as_of="2026-02-01"), [])

    def test_reconciliation_flags_overlapping_conflicts_without_choosing_a_winner(self):
        first = self.store.add_claim(
            "service", "status", "healthy", confidence=0.99,
            source_uri="https://a.example/status", valid_from="2026-01-01",
        )
        second = self.store.add_claim(
            "service", "status", "degraded", confidence=0.45,
            source_uri="https://b.example/status", valid_from="2026-02-01",
        )
        result = self.store.reconcile("service", "status")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual({item["id"] for item in result["conflicts"][0]["claims"]}, {first["id"], second["id"]})
        self.assertEqual({item["status"] for item in result["conflicts"][0]["claims"]}, {"disputed"})
        self.assertIn("not automatically resolved", result["policy"])

    def test_non_overlapping_temporal_claims_are_not_conflicts(self):
        self.store.add_claim("service", "region", "east", valid_from="2024-01-01", valid_until="2025-01-01")
        self.store.add_claim("service", "region", "west", valid_from="2025-01-01")
        result = self.store.reconcile("service", "region")
        self.assertEqual(result["conflicts"], [])

    def test_revision_preserves_original_and_adds_supersession_link(self):
        prior = self.store.add_claim("agent", "default_model", "model-a", source_uri="https://example.org/a")
        revised = self.store.revise_claim(
            prior["id"], new_object="model-b", reason="Provider configuration changed",
            source_uri="https://example.org/b",
        )
        self.assertTrue(revised["ok"])
        self.assertEqual(self.store.get_claim(prior["id"])["status"], "superseded")
        self.assertEqual(self.store.get_claim(revised["id"])["object"], "model-b")
        self.assertEqual(self.store.get_claim(revised["id"])["supersedes_id"], prior["id"])
        self.assertEqual(self.store.claim_provenance(revised["id"])["relations"][0]["relation"], "supersedes")

    def test_revision_of_expired_claim_does_not_inherit_an_expired_end_date(self):
        prior = self.store.add_claim("service", "region", "east", valid_from="2024-01-01", valid_until="2025-01-01")
        revised = self.store.revise_claim(prior["id"], new_object="west", reason="Verified migration")
        self.assertTrue(revised["ok"], revised)
        current = self.store.get_claim(revised["id"])
        self.assertIsNone(current["valid_until"])

    def test_revision_closes_old_validity_interval_at_new_claim_start(self):
        prior = self.store.add_claim("service", "region", "east", valid_from="2024-01-01", valid_until="2030-01-01")
        revised = self.store.revise_claim(prior["id"], new_object="west", reason="Verified migration", valid_from="2026-11-09")
        self.assertTrue(revised["ok"], revised)
        old = self.store.get_claim(prior["id"])
        new = self.store.get_claim(revised["id"])
        self.assertEqual(old["valid_until"], new["valid_from"])
        self.assertLessEqual(old["valid_until_ts"], new["valid_from_ts"])

    def test_dependency_reconstruction_is_bounded_and_provenance_bearing(self):
        source = self.store.add_claim("RTK", "filters", "shell output", source_uri="https://example.org/rtk")
        derived = self.store.add_claim("Kwee", "uses", "RTK", source_uri="https://example.org/kwee")
        relation = self.store.link_claims(derived["id"], source["id"], "derived_from", "Implementation reference")
        self.assertTrue(relation["ok"])
        graph = self.store.reconstruct(derived["id"], max_depth=3)
        self.assertEqual(graph["root"]["id"], derived["id"])
        self.assertEqual([node["id"] for node in graph["dependencies"]], [source["id"]])
        self.assertEqual(graph["dependencies"][0]["source_uri"], "https://example.org/rtk")

    def test_research_question_hypothesis_alternatives_and_study_design(self):
        project = self.store.create_research("RTK evaluation", "Does RTK reduce model-visible command output bytes?")
        hypothesis = self.store.add_hypothesis(
            project["id"],
            "RTK lowers bytes returned by supported shell commands.",
            alternatives=["Commands are already short", "The output format differs by command"],
            predictions=["Median output bytes decrease for supported commands"],
            hypothesis_type="descriptive",
        )
        design = self.store.create_study_design(
            project["id"], hypothesis_id=hypothesis["id"],
            independent_variables=["RTK enabled"], dependent_variables=["output bytes"],
            confounders=["command family", "repository size"],
            controls=["same command", "same working tree"],
            design_type="paired benchmark", sampling_plan="Run each command with and without RTK",
            analysis_plan="Report paired byte differences and distribution; avoid inferring provider billing savings.",
        )
        self.assertTrue(design["ok"])
        report = self.store.research_report(project["id"])
        self.assertEqual(report["project"]["question"], "Does RTK reduce model-visible command output bytes?")
        self.assertEqual(report["hypotheses"][0]["status"], "candidate")
        self.assertEqual(report["designs"][0]["independent_variables"], ["RTK enabled"])

    def test_study_design_rejects_missing_outcome_variables(self):
        project = self.store.create_research("Test", "Is this question answerable?")
        result = self.store.create_study_design(
            project["id"], independent_variables=["treatment"], dependent_variables=[],
            design_type="randomized",
        )
        self.assertFalse(result["ok"])

    def test_welch_test_requires_explicit_design_assumption_attestation(self):
        result = self.store.welch_test([1, 2, 3, 4], [2, 3, 4, 5], assumptions_confirmed=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["inferential_executed"])
        self.assertEqual(result["mean_difference_a_minus_b"], -1.0)

    def test_welch_test_returns_effect_uncertainty_and_multiple_test_adjustment(self):
        # Known equal-variance example; Welch is used and does not assume equal variance.
        result = self.store.welch_test(
            [2, 3, 4, 5, 6], [1, 2, 3, 4, 5],
            assumptions_confirmed=True, comparisons_planned=2,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["inferential_executed"])
        self.assertAlmostEqual(result["mean_difference_a_minus_b"], 1.0)
        self.assertGreater(result["p_value_adjusted"], 0)
        self.assertGreater(result["confidence_interval"][1], result["confidence_interval"][0])
        self.assertEqual(result["multiplicity"]["method"], "Bonferroni")
        self.assertIn("caller-attested", result["assumption_status"])

    def test_welch_student_t_tail_math(self):
        p = self.store._two_sided_t_p_value(2.2281388519649385, 10)
        self.assertAlmostEqual(p, 0.05, places=5)


if __name__ == "__main__":
    unittest.main()
