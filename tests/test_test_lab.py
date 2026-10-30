"""Offline regression tests for saved test-lab scenarios and local HTTP routes."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import projects
import test_lab


class TestScenarioStorage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"CM_PROJECTS_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = projects.create_project({
            "name": "Отёк AI",
            "product_type": "mobile_app",
            "product_description": "Пример исходного контекста",
        })
        self.original = (self.path / self.project["id"] / "project.json").read_bytes()

    def test_create_and_reopen_without_cloning_or_changing_source(self):
        created = test_lab.create_scenario({
            "project_id": self.project["id"], "name": "Отёк AI · Тест SP",
            "target_stage": "search",
        })
        self.assertEqual(created["project_id"], self.project["id"])
        self.assertEqual(created["target_stage"], "search")
        self.assertEqual(created["revision"], 1)
        self.assertIsNone(created["last_opened_stage"])
        self.assertEqual(len(projects.list_projects()), 1)
        self.assertEqual((self.path / self.project["id"] / "project.json").read_bytes(), self.original)
        self.assertEqual(test_lab.list_scenarios()[0]["id"], created["id"])
        self.assertEqual(test_lab.get_scenario(created["id"])["project_name"], "Отёк AI")
        self.assertTrue((self.path / "_test_lab" / (created["id"] + ".json")).is_file())

    def test_step_boundary_expansion_and_navigation_bookmark(self):
        created = test_lab.create_scenario({"project_id": self.project["id"], "target_stage": "search"})
        with self.assertRaisesRegex(ValueError, "за пределы"):
            test_lab.update_scenario(created["id"], {"expected_revision": 1, "last_opened_stage": "collector"})
        opened = test_lab.update_scenario(created["id"], {
            "expected_revision": 1, "last_opened_stage": "search",
        })
        self.assertEqual(opened["last_opened_stage"], "search")
        expanded = test_lab.update_scenario(created["id"], {
            "expected_revision": 2, "target_stage": "collector",
        })
        self.assertEqual(expanded["target_stage"], "collector")
        next_stage = test_lab.update_scenario(created["id"], {
            "expected_revision": 3, "last_opened_stage": "collector",
        })
        self.assertEqual(next_stage["last_opened_stage"], "collector")
        self.assertIsNotNone(next_stage["last_opened_at"])
        reduced = test_lab.update_scenario(created["id"], {
            "expected_revision": 4, "target_stage": "search",
        })
        self.assertIsNone(reduced["last_opened_stage"])
        self.assertEqual(test_lab.get_scenario(created["id"])["target_stage"], "search")
        self.assertEqual((self.path / self.project["id"] / "project.json").read_bytes(), self.original)

    def test_reject_stale_revision_invalid_project_and_bad_fields(self):
        record = test_lab.create_scenario({"project_id": self.project["id"]})
        with self.assertRaises(test_lab.RevisionConflict):
            test_lab.update_scenario(record["id"], {"expected_revision": 2, "target_stage": "collector"})
        for payload in (
            {"project_id": "../somewhere"},
            {"project_id": "f"*32},
            {"project_id": self.project["id"], "target_stage": "unknown"},
            {"project_id": self.project["id"], "unapproved_param": "x"},
        ):
            with self.subTest(payload=payload), self.assertRaises((ValueError, FileNotFoundError)):
                test_lab.create_scenario(payload)
        self.assertIsNone(test_lab.get_scenario("../../passwd"))
        with self.assertRaises(ValueError):
            test_lab.update_scenario(record["id"], {
                "expected_revision": 1, "name": "Mutate source",
            })

    def test_scenarios_are_separate_and_missing_source_is_explicit(self):
        first = test_lab.create_scenario({"project_id": self.project["id"], "name": "SP"})
        second = test_lab.create_scenario({"project_id": self.project["id"], "name": "Collector", "target_stage": "collector"})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(test_lab.list_scenarios()), 2)
        (self.path / self.project["id"] / "project.json").unlink()
        missing = test_lab.get_scenario(first["id"])
        self.assertFalse(missing["project_available"])
        with self.assertRaises(FileNotFoundError):
            test_lab.update_scenario(first["id"], {"expected_revision": 1, "target_stage": "rtb"})


class TestScenarioHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"CM_PROJECTS_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = projects.create_project({"name": "Otek AI"})

    def request(self, path, payload=None, marked=True, origin=None):
        headers = {"Host": "localhost:8765"}
        if payload is not None and marked:
            headers["X-ContentMachine"] = "1"
        if origin:
            headers["Origin"] = origin
        req = Request(
            "http://127.0.0.1:" + str(self.server.server_port) + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def test_http_create_list_update_reload(self):
        code, raw = self.request("/api/test-lab", {
            "project_id": self.project["id"], "name": "SP regression",
            "target_stage": "search",
        })
        self.assertEqual(code, 201, raw)
        created = json.loads(raw)["scenario"]
        code, raw = self.request("/api/test-lab")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(raw)["scenarios"]), 1)
        self.assertEqual(json.loads(raw)["projects"][0]["id"], self.project["id"])
        code, raw = self.request("/api/test-lab/" + created["id"], {
            "expected_revision": 1, "target_stage": "collector",
        })
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw)["scenario"]["revision"], 2)
        code, raw = self.request("/api/test-lab/" + created["id"])
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["scenario"]["target_stage"], "collector")
        code, _ = self.request("/api/test-lab/" + created["id"], {
            "expected_revision": 1, "target_stage": "sre",
        })
        self.assertEqual(code, 409)

    def test_mutation_protection_assets_and_no_implicit_execution(self):
        body = {"project_id": self.project["id"]}
        self.assertEqual(self.request("/api/test-lab", body, marked=False)[0], 403)
        self.assertEqual(self.request("/api/test-lab", body, origin="https://attacker.test")[0], 403)
        self.assertEqual(test_lab.list_scenarios(), [])
        for path in ("/test-lab.js", "/test-lab.css"):
            code, content = self.request(path)
            self.assertEqual(code, 200)
            self.assertTrue(len(content) > 200)
        code, content = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b'testLabView', content)
        self.assertIn(b'/test-lab.js', content)
        self.assertEqual(self.request("/api/test-lab/invalid")[0], 404)


if __name__ == "__main__":
    unittest.main()
