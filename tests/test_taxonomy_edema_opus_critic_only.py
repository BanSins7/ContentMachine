"""Free regression tests: Opus-only continuation uses immutable research v14."""
import unittest
from decimal import Decimal
from scripts.taxonomy_edema_opus_critic_only_once import (
    OPUS,ARTIFACT_ID,SOURCE_SHA256,USD_CEILING,MAX_ONE_REQUEST_RESERVE,
    EXTRA_SAFETY_MARGIN,MIN_OUTPUT_TOKENS,safe_provider_error)
from intelligence.taxonomy_builder.run_contracts import schema_for

class SingleCritic(unittest.TestCase):
    def test_exact_previous_source_artifact(self):
        self.assertEqual(ARTIFACT_ID,12278138025)
        self.assertEqual(SOURCE_SHA256,
            "055a98afd084c6e7292051d8f58bd1f86b7787937d38c64f901c342dcc11cbe2")
        self.assertEqual(OPUS,"anthropic/claude-opus-5.5")
    def test_critic_only_and_more_output_than_old_limit(self):
        self.assertGreaterEqual(MIN_OUTPUT_TOKENS,5000)
        self.assertGreater(MIN_OUTPUT_TOKENS,2000)
        self.assertEqual(schema_for("taxonomy_critic")["strict"],True)
        self.assertIn("findings",schema_for("taxonomy_critic")["schema"]["properties"])
    def test_one_request_max_cannot_exceed_old_one_dollar_key_rule(self):
        self.assertLess(USD_CEILING,Decimal("1"))
        self.assertLess(MAX_ONE_REQUEST_RESERVE,Decimal("0.5"))
        self.assertGreater(EXTRA_SAFETY_MARGIN,Decimal("0"))
    def test_http_error_keeps_google_anthropic_provider_raw_reason_no_user_id(self):
        sample=b'{"error":{"code":400,"message":"test","metadata":{"raw":"INVALID_ARGUMENT","provider_name":"Anthropic","provider_error_code":"400","is_byok":false}},"user_id":"sensitive"}'
        e=safe_provider_error(sample,"not-a-key")
        self.assertNotIn("user_id",e)
        self.assertEqual(e["error"]["metadata"]["raw"],"INVALID_ARGUMENT")
        self.assertNotIn("is_byok",e["error"]["metadata"])

if __name__=="__main__":
    unittest.main()
