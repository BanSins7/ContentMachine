"""Project wizard storage + localhost HTTP tests; no remote providers involved."""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import projects


class TestProjectPersistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"CM_PROJECTS_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_all_fields_optional_and_survive_new_read(self):
        data = projects.create_project({})
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["niche"], "")
        self.assertEqual(data["files"], [])
        self.assertEqual(len(projects.list_projects()), 1)
        self.assertEqual(projects.public_project(projects.get_project(data["id"]))["id"], data["id"])
        self.assertTrue((Path(self.temp.name) / data["id"] / "project.json").exists())

    def test_nine_intake_fields_and_file_bytes_are_preserved(self):
        body = {key: key+" пример" for key in projects.FIELDS}
        body["name"] = "Новый проект"
        body["research_files"] = [
            {"name":"research.pdf", "data_base64":base64.b64encode(b"%PDF-1.4 test").decode("ascii")},
            {"name":"market.xlsx", "data_base64":base64.b64encode(b"mock spreadsheet").decode("ascii")},
        ]
        created = projects.create_project(body)
        stored = projects.get_project(created["id"])
        for key in projects.FIELDS:
            self.assertEqual(stored[key], body[key])
        self.assertEqual(created["name"], body["name"])
        self.assertEqual(len(created["files"]), 2)
        self.assertNotIn("stored_name", created["files"][0])
        disk = Path(self.temp.name) / created["id"] / "research"
        for item in stored["files"]:
            self.assertTrue((disk / item["stored_name"]).exists())
            self.assertEqual((disk / item["stored_name"]).stat().st_size, item["size"])

    def test_reject_invalid_base64_without_partial_project(self):
        with self.assertRaisesRegex(ValueError, "прочитать"):
            projects.create_project({"research_files":[{"name":"draft.pdf","data_base64":"broken!"}]})
        self.assertEqual(projects.list_projects(), [])

    def test_reject_unsafe_filename_and_unsupported_type(self):
        with self.assertRaisesRegex(ValueError, "расширение"):
            projects.create_project({"research_files":[{"name":"setup.exe","data_base64":"eA=="}]})
        project=projects.create_project({"research_files":[{"name":"../myreport.txt","data_base64":"eA=="}]})
        self.assertEqual(project["files"][0]["name"], "myreport.txt")
        self.assertEqual(len(projects.list_projects()), 1)

    def test_reject_large_fields_and_too_many_files(self):
        with self.assertRaises(ValueError):
            projects.create_project({"goal": "g" * 12001})
        with self.assertRaises(ValueError):
            projects.create_project({"research_files": [{}] * 11})

    def test_project_id_cannot_traverse_paths(self):
        projects.create_project({})
        self.assertIsNone(projects.get_project("../secrets"))
        self.assertIsNone(projects.get_project("A" * 32))


class TestProjectLocalHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=mock.patch.dict(os.environ, {"CM_PROJECTS_DIR":self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def request(self,path,payload=None,header=True,origin=None):
        headers={"Host":"localhost:8765"}
        if payload is not None and header:
            headers["X-ContentMachine"]="1"
        if origin: headers["Origin"]=origin
        request=Request(
            "http://127.0.0.1:"+str(self.server.server_port)+path,
            data=json.dumps(payload,ensure_ascii=False).encode("utf-8") if payload is not None else None,
            headers=headers,
            method="POST" if payload is not None else "GET")
        try:
            with urlopen(request,timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def test_create_read_and_list_through_http(self):
        code,body=self.request("/api/projects",{"name":"Мой продукт","niche":"Health & Fitness"})
        self.assertEqual(code,201,body)
        project=json.loads(body)["project"]
        self.assertEqual(project["niche"],"Health & Fitness")
        code,body=self.request("/api/projects")
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)["projects"][0]["id"],project["id"])
        code,body=self.request("/api/projects/"+project["id"])
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)["project"]["name"],"Мой продукт")
        code,body=self.request("/api/projects/invalid")
        self.assertEqual(code,404)

    def test_no_cross_site_or_unmarked_mutations(self):
        code,_=self.request("/api/projects",{"name":"bad"},header=False)
        self.assertEqual(code,403)
        code,_=self.request("/api/projects",{"name":"bad"},origin="https://evil.test")
        self.assertEqual(code,403)
        self.assertEqual(projects.list_projects(),[])

    def test_projects_js_is_served(self):
        code,body=self.request("/projects.js")
        self.assertEqual(code,200)
        self.assertIn(b"projectStepForm",body)
        code,body=self.request("/")
        self.assertEqual(code,200)
        self.assertIn(b"/projects.js",body)
        self.assertIn(b"projectSidebarList",body)


if __name__ == "__main__":
    unittest.main()
