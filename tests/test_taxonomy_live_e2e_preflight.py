"""Block 7 budget preflight never considers conditional/unknown pricing."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.taxonomy_live_e2e import priced_candidates

def m(model_id,pricing,context=20000,maxout=8000):
    return {"model_id":model_id,"selectable":True,
       "capabilities":{"max_output_tokens":maxout,"context_length":context},
       "pricing":pricing}
class PaidPreflight(unittest.TestCase):
    def test_unknown_override_is_filtered_before_sorting(self):
        bad=m("some/tiered",{"prompt":"0.000001","completion":"0.000002",
              "overrides":[{"condition":"long_context"}]})
        good=m("some/fixed",{"prompt":"0.000003","completion":"0.000005"})
        self.assertEqual([x["model_id"] for x in priced_candidates([bad,good])],
                         ["some/fixed"])
    def test_unknown_base_price_and_incompatible_context_fail_closed(self):
        models=[m("missing-rate",{"completion":"0.000002"}),
                m("no-context",{"prompt":"0.000001","completion":"0.000002"},context=3000),
                m("low-output",{"prompt":"0.000001","completion":"0.000002"},maxout=400)]
        self.assertEqual(priced_candidates(models),[])
    def test_previously_verified_model_preferred_when_fixed_pricing(self):
        models=[m("generic/other",{"prompt":"0.000001","completion":"0.000001"}),
                m("deepseek/deepseek-v4.1-flash",
                  {"prompt":"0.000002","completion":"0.000002"})]
        self.assertEqual(priced_candidates(models)[0]["model_id"],
                         "deepseek/deepseek-v4.1-flash")
