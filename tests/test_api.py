import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class TestValidation(unittest.TestCase):
    def test_exact_reel(self):
        self.assertEqual(app.canonical_reel('https://www.instagram.com/reel/DeffteyB6If/?foo=bar'), ('https://www.instagram.com/reel/DeffteyB6If/', 'DeffteyB6If'))
    def test_reject_spoof_host(self):
        with self.assertRaises(ValueError):
            app.canonical_reel('https://www.instagram.com.attacker.test/reel/DeffteyB6If/')
    def test_reject_http(self):
        with self.assertRaises(ValueError):
            app.canonical_reel('http://www.instagram.com/reel/DeffteyB6If/')
    def test_reject_profile_as_reel(self):
        with self.assertRaises(ValueError):
            app.canonical_reel('https://www.instagram.com/dr.velskaya/')
    def test_paths(self):
        self.assertIsNotNone(app.ACCOUNT_RE.fullmatch('dr.velskaya'))
        self.assertIsNone(app.ACCOUNT_RE.fullmatch('../secrets'))


class TestLocalHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, method='GET', data=None, headers=None):
        base = 'http://127.0.0.1:'+str(self.server.server_port)
        request = Request(base+path, data=json.dumps(data).encode() if data is not None else None,
                          headers={'Host':'localhost:8765',**(headers or {})}, method=method)
        try:
            with urlopen(request,timeout=5) as result: return result.status, result.read()
        except HTTPError as e: return e.code, e.read()

    def test_state(self):
        code, body = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertIn('library', json.loads(body))

    def test_static(self):
        code, body = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn(b'ContentMachine',body)

    def test_find_exact_reel(self):
        status, body = self.request('/api/find', 'POST', {'target': 'https://www.instagram.com/reel/DeffteyB6If/'}, {'X-ContentMachine':'1'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['urls'], ['https://www.instagram.com/reel/DeffteyB6If/'])

    def test_find_unsupported_url(self):
        status, _ = self.request('/api/find', 'POST', {'target': 'https://notinstagram.example/dr.velskaya/'}, {'X-ContentMachine':'1'})
        self.assertEqual(status, 400)

    def test_profile_reels_tab_pattern(self):
        self.assertIsNotNone(app.PROFILE_RE.fullmatch('https://www.instagram.com/dr.velskaya/reels/'))

    def test_reject_cross_site_mutation(self):
        code,_=self.request('/api/download','POST',{'urls':['https://www.instagram.com/reel/DeffteyB6If/']}, {'Origin':'https://evil.test','X-ContentMachine':'1'})
        self.assertEqual(code,403)

    def test_reject_bad_reel_before_queuing(self):
        code,body=self.request('/api/download','POST',{'urls':['https://evil.test/reel/DeffteyB6If/'],'engine':'local'}, {'X-ContentMachine':'1'})
        self.assertEqual(code,400)
        self.assertIn('error',json.loads(body))


if __name__ == '__main__': unittest.main()
