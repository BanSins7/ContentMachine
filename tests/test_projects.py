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

    def test_structured_taxonomy_fields_and_revisioned_update(self):
        created = projects.create_project({
            "name": "Calorie App", "niche": "Health & Fitness",
            "product_description": "Приложение для питания",
            "product_type": "mobile_app",
            "product_functions": "Считает калории по фото",
            "audience_problems": "Сложно вести учёт питания",
        })
        initial = projects.taxonomy_input(created["id"])
        self.assertEqual(initial["profile_revision"], 1)
        self.assertEqual(initial["profile"]["product_type"], "mobile_app")
        self.assertEqual(initial["missing_structured_fields"], [])
        self.assertTrue(initial["has_source_material"])
        updated = projects.update_project(created["id"], {
            "expected_revision": 1, "audience_problems": "Трудно считать калории каждый день",
        })
        self.assertEqual(updated["profile_revision"], 2)
        after = projects.taxonomy_input(created["id"])
        self.assertEqual(after["profile"]["product_functions"], "Считает калории по фото")
        self.assertNotEqual(after["profile_hash"], initial["profile_hash"])
        self.assertEqual(after["profile_revision"], 2)
        with self.assertRaises(projects.ProjectRevisionConflict):
            projects.update_project(created["id"], {
                "expected_revision": 1, "product_functions": "Некорректное устаревшее обновление",
            })
        self.assertEqual(projects.get_project(created["id"])["profile_revision"], 2)

    def test_legacy_profile_can_supply_taxonomy_input(self):
        data = projects.create_project({"product_description": "Только описание"})
        self.assertEqual(data["profile_revision"], 1)
        record_path = Path(self.temp.name) / data["id"] / "project.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        for field in ("product_type", "product_functions", "audience_problems",
                      "profile_revision", "updated_at"):
            record.pop(field, None)
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        payload = projects.taxonomy_input(data["id"])
        self.assertEqual(payload["profile_revision"], 1)
        self.assertEqual(payload["profile"]["product_type"], "")
        self.assertEqual(payload["profile"]["product_description"], "Только описание")
        self.assertEqual(len(payload["missing_structured_fields"]), 3)
        self.assertTrue(payload["has_source_material"])

    def test_optional_postgres_profile_revision_mirror(self):
        dsn = os.getenv("CM_TEST_PG_DSN")
        if not dsn:
            self.skipTest("CM_TEST_PG_DSN not provided")
        import psycopg
        from intelligence import migrate
        with psycopg.connect(dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)
        with mock.patch.dict(os.environ, {
            "CM_DB_ENABLED": "1", "CM_DB_DSN": dsn,
        }):
            created = projects.create_project({
                "name": "Mirror test", "product_type": "mobile_app",
                "product_functions": "Function v1",
            })
            self.assertEqual(created["profile_db_sync"], "synced")
            updated = projects.update_project(created["id"], {
                "expected_revision": 1, "product_functions": "Function v2",
            })
            self.assertEqual(updated["profile_db_sync"], "synced")
            with psycopg.connect(dsn) as conn:
                rows = conn.execute(
                    "SELECT revision,profile->'profile'->>'product_functions' "
                    "FROM project_profile_versions WHERE project_id=%s ORDER BY revision",
                    (created["id"],),
                ).fetchall()
                self.assertEqual(rows, [(1, "Function v1"), (2, "Function v2")])
                current = conn.execute(
                    "SELECT profile_revision FROM project_registry WHERE id=%s",
                    (created["id"],),
                ).fetchone()
                self.assertEqual(current[0], 2)
                conn.execute("DELETE FROM project_registry WHERE id=%s", (created["id"],))

    def test_invalid_product_type_and_unsafe_patch(self):
        with self.assertRaisesRegex(ValueError, "тип продукта"):
            projects.create_project({"product_type": "made_up"})
        project = projects.create_project({"name": "Original"})
        with self.assertRaisesRegex(ValueError, "Неподдерживаемые"):
            projects.update_project(project["id"], {
                "expected_revision": 1, "research_files": [],
            })
        with self.assertRaisesRegex(ValueError, "Название"):
            projects.update_project(project["id"], {
                "expected_revision": 1, "name": "",
            })
        self.assertEqual(projects.get_project(project["id"])["name"], "Original")

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

    def test_taxonomy_input_and_profile_edit_through_http(self):
        code, body = self.request("/api/projects", {
            "name": "App", "product_type": "mobile_app",
            "product_functions": "Сканирует еду",
            "audience_problems": "Сложно считать калории",
        })
        self.assertEqual(code, 201, body)
        ident = json.loads(body)["project"]["id"]
        code, body = self.request("/api/projects/"+ident+"/taxonomy-input")
        self.assertEqual(code, 200, body)
        payload = json.loads(body)
        self.assertEqual(payload["profile"]["product_functions"], "Сканирует еду")
        self.assertEqual(payload["profile_revision"], 1)
        code, body = self.request("/api/projects/"+ident+"/update", {
            "expected_revision": 1, "product_functions": "Распознаёт блюда",
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["project"]["profile_revision"], 2)
        code, body = self.request("/api/projects/"+ident+"/update", {
            "expected_revision": 1, "product_functions": "Устаревшее",
        })
        self.assertEqual(code, 409, body)
        code, body = self.request("/api/projects/"+ident+"/taxonomy-input")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["profile"]["product_functions"], "Распознаёт блюда")
        code, _ = self.request("/api/projects/not-a-uuid/taxonomy-input")
        self.assertEqual(code, 404)

    def test_download_uploaded_document_and_reject_other_paths(self):
        document=b"%PDF-1.4\\nfixture"
        encoded=base64.b64encode(document).decode("ascii")
        code,body=self.request("/api/projects",{
            "research_files":[{"name":"Исследование.pdf","data_base64":encoded}]
        })
        self.assertEqual(code,201)
        ident=json.loads(body)["project"]["id"]
        code,body=self.request("/api/projects/"+ident+"/files/0")
        self.assertEqual(code,200)
        self.assertEqual(body,document)
        code,_=self.request("/api/projects/"+ident+"/files/1")
        self.assertEqual(code,404)
        code,_=self.request("/api/projects/"+ident+"/files/../1")
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
