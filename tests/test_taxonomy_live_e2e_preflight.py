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
    def test_full_size_quote_plan_uses_pinned_model_identity(self):
        from scripts.taxonomy_live_e2e import snapshot_for_quote,PROFILE
        from intelligence.taxonomy_builder.cost_estimator import estimate
        row={"model_id":"fixture/model","catalog_version_id":42,
             "capabilities":{"text_output":True,"supports_structured_outputs":True,
                "context_length":131072,"max_output_tokens":32768},
             "pricing":{"prompt":"0.0000001","completion":"0.0000002"},
             "verified_at":"2026-10-20T00:00:00Z"}
        snaps={role:snapshot_for_quote(row) for role in
               ("fact_extraction","taxonomy_generation","taxonomy_critic")}
        stages=[{"role":r,"model_id":row["model_id"],"catalog_version_id":42,
                 "reasoning":{"mode":"default"},"temperature":None,
                 "max_output_tokens":maximum} for r,maximum in
                (("fact_extraction",6000),("taxonomy_generation",20000),
                 ("taxonomy_critic",6000))]
        quote=estimate(PROFILE,[],{"stages":stages},snaps)
        self.assertEqual(len(quote["stages"]),3)
        self.assertEqual(snaps["taxonomy_generation"]["model_id"],"fixture/model")
        self.assertLessEqual(float(quote["high_usd"]),0.10)


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

    def test_long_form_realistic_reasoning_limits_are_not_tiny(self):
        from scripts.taxonomy_live_e2e import PROFILE
        self.assertGreater(len(PROFILE["product_functions"]),300)
        self.assertGreater(len(PROFILE["audience_problems"]),300)
        self.assertIn("нет медицинской диагностики",PROFILE["product_functions"])
