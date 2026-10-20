"""Free regression tests for the one-shot model matrix smoke."""
import unittest
from decimal import Decimal

from scripts.taxonomy_model_matrix_smoke import (
    PROFILE, INPUTS, MODEL_IDS, STRONG, PROJECT_CEILING,
    NEW_BATCH_CAP, PREVIOUS_CONFIRMED, choose_stage, grade,
)

class ModelMatrixSmokeTests(unittest.TestCase):
    def test_twelve_unique_approved_models(self):
        self.assertEqual(len(MODEL_IDS), 12)
        self.assertEqual(len(set(MODEL_IDS)), 12)
        self.assertTrue(STRONG.issubset(set(MODEL_IDS)))

    def test_key_capped_project_total(self):
        self.assertLess(PREVIOUS_CONFIRMED + NEW_BATCH_CAP, PROJECT_CEILING)

    def test_exact_same_case_for_all(self):
        self.assertIn("questionnaire", INPUTS)
        self.assertEqual(INPUTS["questionnaire"], PROFILE)
        self.assertNotIn("password", str(INPUTS).lower())

    def test_valid_evidence_scores_high(self):
        facts = [
            {"kind": "function", "text": "Ведёт дневник питания",
             "source_field": "product_functions",
             "source_quote": "Приложение сохраняет блюда в дневнике питания."},
            {"kind": "function", "text": "Оценивает калории по фото",
             "source_field": "product_functions",
             "source_quote": "Приложение оценивает калории по фотографии порции."},
            {"kind": "problem", "text": "Люди забывают перекусы",
             "source_field": "audience_problems",
             "source_quote": "Люди забывают записывать перекусы."},
        ]
        score, verified, excluded = grade({"raw_json": {"facts": facts}})
        self.assertEqual(score, 100)
        self.assertEqual(len(verified), 3)
        self.assertFalse(excluded)

    def test_fabricated_quote_cannot_pass(self):
        facts = [{"kind": "function", "text": "Диагностирует диабет",
                  "source_field": "product_functions",
                  "source_quote": "Приложение диагностирует диабет"}]
        score, valid, rejected = grade({"raw_json": {"facts": facts}})
        self.assertLess(score, 65)
        self.assertFalse(valid)
        self.assertEqual(len(rejected), 1)

    def test_snapshot_budget_never_exceeded(self):
        cap = {"max_output_tokens": 10000, "context_length": 65000,
               "reasoning_efforts": ["low", "medium", "high"],
               "supported_parameters": ["response_format"]}
        pricing = {"prompt": "0.0000001", "completion": "0.0000002"}
        result = choose_stage("openai/gpt-6.1-sol", cap, pricing, Decimal("0.01"))
        self.assertIsNotNone(result)
        stage, _snap, reserve = result
        self.assertEqual(stage["reasoning"]["effort"], "high")
        self.assertLessEqual(reserve, Decimal("0.01"))
        self.assertGreaterEqual(stage["max_output_tokens"], 1400)

    def test_expensive_model_skipped_instead_of_charged(self):
        cap = {"max_output_tokens": 10000, "context_length": 65000,
               "reasoning_efforts": ["high"], "supported_parameters": ["response_format"]}
        pricing = {"prompt": "0.00005", "completion": "0.0005"}
        self.assertIsNone(choose_stage("anthropic/claude-opus-5.5",
                                       cap, pricing, Decimal("0.30")))

if __name__ == "__main__":
    unittest.main()
