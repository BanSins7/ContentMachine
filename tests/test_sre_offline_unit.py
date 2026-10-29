"""S4 fake model deterministic semantics *only as plumbing tests*."""
import copy
import unittest
from uuid import uuid4

from intelligence.relevance.offline_fake import evaluate_fake
from intelligence.relevance.v1_contracts import SREContractError
from test_sre_contracts_v1 import content, K1, K2


class FakeClassifierTests(unittest.TestCase):
    def contexts(self):
        return [
            {"category_key": K1, "hypothesis": {
                "name": "Калории в кофе",
                "exclude": "Продажа спортивного инвентаря"}},
            {"category_key": K2, "hypothesis": {
                "name": "Заработок на видео",
                "exclude": "Калории в кофе"}},
        ]

    def test_match_no_match_and_uncertain_are_exactly_grounded(self):
        snap=content()
        labels=evaluate_fake(snap,self.contexts())["labels"]
        self.assertEqual([v["decision"] for v in labels],["match","no_match"])
        self.assertEqual(labels[0]["evidence"][0],
                         {"source":"title","quote":"Калории в кофе"})
        self.assertEqual(labels[1]["evidence"][0]["quote"],"Калории в кофе")
        snap["data"]["title"]="Совсем другая тема"
        result=evaluate_fake(snap,self.contexts())["labels"]
        self.assertEqual([v["decision"] for v in result],["uncertain","uncertain"])
        self.assertTrue(all(not item["evidence"] for item in result))

    def test_empty_duplicate_and_fabricated_categories_are_rejected(self):
        snap=content()
        with self.assertRaises(SREContractError):
            evaluate_fake(snap,[])
        bad=self.contexts()
        bad[1]["category_key"]=bad[0]["category_key"]
        with self.assertRaises(SREContractError):
            evaluate_fake(snap,bad)
        bad=self.contexts()
        bad[0]["category_key"]=str(uuid4())
        # Fake mode cannot claim real semantic certainty based on absent text.
        self.assertEqual(evaluate_fake(snap,bad)["labels"][0]["decision"],"match")

    def test_fake_inputs_are_not_mutated(self):
        snap=content()
        original=copy.deepcopy(snap)
        contexts=self.contexts()
        prior=copy.deepcopy(contexts)
        evaluate_fake(snap,contexts)
        self.assertEqual(snap,original)
        self.assertEqual(contexts,prior)


if __name__=="__main__":
    unittest.main()
