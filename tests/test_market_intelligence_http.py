"""Market Intelligence HTTP contracts: no DB, paid models or network."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.market_intelligence.http_api import handle_get, handle_post


PID = "a"*32


class FakeHandler:
    def __init__(self, body=None):
        self.responses = []
        self.headers = {"Content-Type": "application/json"}
        if body is not None:
            raw=json.dumps(body,ensure_ascii=False).encode("utf-8")
            self.headers["Content-Length"]=str(len(raw))
            self.rfile=io.BytesIO(raw)
    def json_response(self, status, payload):
        self.responses.append((status, payload))


class MarketHTTPTests(unittest.TestCase):
    def test_marketing_page_is_registered_in_local_app(self):
        # Regression for a stale/main server responding 404 to the direct UI.
        # This verifies the checkout, NOT the already running localhost server.
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        app=(root/"app.py").read_text(encoding="utf-8")
        for name in ("market-intelligence.html","market-intelligence.js",
                     "market-intelligence.css"):
            self.assertTrue((root/"site"/name).is_file())
            self.assertIn(f"'/"+name+f"': '"+name+"'",app)
        self.assertTrue((root/"START_MARKETING_QA.bat").is_file())
        self.assertTrue((root/"scripts/check_marketing_local_server.ps1").is_file())

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_seed_registration_contract(self, service):
        service.return_value.register.return_value={"ok":True,"registered":1}
        handler=FakeHandler({"videos":["https://www.tiktok.com/@author/video/1234567890123456789"],
                             "authors":[]})
        self.assertTrue(handle_post(handler, f"/api/projects/{PID}/market-intelligence/seeds"))
        self.assertEqual(handler.responses[0][0], 200)
        self.assertEqual(service.return_value.register.call_count,1)

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_freeze_never_accepts_untrusted_report(self, service):
        handler=FakeHandler({"market_hypotheses_verified":True})
        self.assertTrue(handle_post(handler, f"/api/projects/{PID}/market-intelligence/freeze"))
        self.assertEqual(handler.responses[0][0], 422)
        service.return_value.freeze.assert_not_called()

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_readonly_rtb_context(self, service):
        expected={"ok":True,"read_only":True, "opus_dispatched":False}
        service.return_value.rtb_context.return_value=expected
        sid="123e4567-e89b-12d3-a456-426614174000"
        handler=FakeHandler()
        url=SimpleNamespace(path=f"/api/projects/{PID}/market-intelligence/rtb-context/{sid}",query="")
        self.assertTrue(handle_get(handler,url))
        self.assertEqual(handler.responses[0], (200,expected))
        service.return_value.rtb_context.assert_called_once_with(PID,sid)

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_unknown_routes_do_not_touch_storage(self, service):
        handler=FakeHandler()
        url=SimpleNamespace(path="/api/projects/"+PID+"/other-module",query="")
        self.assertFalse(handle_get(handler,url))
        service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
