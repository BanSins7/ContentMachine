"""One-time Bright Data settings are local, reusable and fail closed."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import brightdata_setup as setup


class BrightDataLocalSetupTests(unittest.TestCase):
    def test_reuses_existing_key_without_paste(self):
        with patch.dict(os.environ,{setup.API_KEY:"local_existing_valid_123456789"}):
            result=setup.install(input_secret=lambda:self.fail("must not prompt"))
        self.assertTrue(result["already_configured"])

    def test_writes_only_ignored_local_env_and_preserves_database(self):
        with TemporaryDirectory() as d:
            file=Path(d)/".env"
            file.write_text("CM_DB_ENABLED=1\nCM_BRIGHTDATA_API_TOKEN=oldtoken123456789\n"
                            "CM_BRIGHTDATA_METRICS_ENABLED=1\n",encoding="utf-8")
            setup._write_local_env(file,"replacement_token_123456789")
            result=file.read_text(encoding="utf-8")
            self.assertIn("CM_DB_ENABLED=1",result)
            self.assertIn("CM_BRIGHTDATA_API_TOKEN=replacement_token_123456789",result)
            self.assertIn("CM_BRIGHTDATA_METRICS_ENABLED=0",result)
            self.assertIn("CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED=0",result)
            self.assertNotIn("oldtoken",result)

    def test_rejects_bad_token_without_change(self):
        with TemporaryDirectory() as d:
            file=Path(d)/".env"
            file.write_text("CM_DB_ENABLED=1\n",encoding="utf-8")
            with self.assertRaises(ValueError):
                setup._write_local_env(file,"bad token containing spaces")
            self.assertEqual(file.read_text(),"CM_DB_ENABLED=1\n")

    def test_one_paste_if_no_existing_configuration(self):
        with TemporaryDirectory() as d:
            with patch.dict(os.environ,{setup.API_KEY:""}),patch.object(setup.config,"ENV_FILE",Path(d)/".env"):
                result=setup.install(input_secret=lambda:"new_local_token_123456789",environment={})
                self.assertFalse(result["already_configured"])
                self.assertTrue((Path(d)/".env").is_file())


if __name__=="__main__":
    unittest.main()
