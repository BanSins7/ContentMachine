"""FIX-18 QA-103/104: exercise the actual localhost HTTP authorization method."""
from __future__ import annotations

import inspect
import unittest

from app import HOST, PORT, Handler


def permit(headers, mutation=False):
    # Use the production bound method without opening a socket or performing POST.
    handler = object.__new__(Handler)
    handler.headers = headers
    return Handler.authorized(handler, mutation=mutation)


class QDPHostAuthorizationTests(unittest.TestCase):
    def test_qdp_post_requires_mutation_header(self):
        host = {"Host": f"{HOST}:{PORT}", "Origin": f"http://{HOST}:{PORT}"}
        self.assertTrue(permit(host))
        self.assertFalse(permit(host, mutation=True))
        self.assertFalse(permit({**host, "X-ContentMachine": "true"}, mutation=True))
        self.assertFalse(permit({**host, "X-ContentMachine": "0"}, mutation=True))
        self.assertTrue(permit({**host, "X-ContentMachine": "1"}, mutation=True))
        # The authorization check must precede any QDP mutation route.
        source = inspect.getsource(Handler.do_POST)
        self.assertLess(source.index("self.authorized(mutation=True)"),
                        source.index("qdp_post(self,route)"))

    def test_qdp_rejects_foreign_host_and_origin(self):
        good = {"Host": f"{HOST}:{PORT}"}
        self.assertTrue(permit(good))
        self.assertTrue(permit({"Host": f"localhost:{PORT}"}))
        for bad_host in ("example.com", f"{HOST}:1234", "attacker:8765",
                         "localhost", "localhost.evil:8765", ""):
            self.assertFalse(permit({"Host": bad_host}), bad_host)
        for bad_origin in ("https://example.com", "null", "http://evil.com",
                           "http://localhost.evil:8765",
                           f"https://{HOST}:{PORT}"):
            self.assertFalse(permit({**good, "Origin": bad_origin}), bad_origin)
        source = inspect.getsource(Handler.do_GET)
        self.assertLess(source.index("self.authorized()"),
                        source.index("qdp_get(self,url)"))


if __name__ == "__main__":
    unittest.main()
