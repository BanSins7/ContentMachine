"""Guard against breaking local Carousel Studio photo previews with restrictive CSP.

The browser creates blob: URLs for uploaded JPG/PNG/WebP and for rendered slide
previews.  Restrict scripts/network as before, but permit blob: as an image src.
Static AST test avoids importing app.py and starting external worker dependencies.
"""
import ast
import unittest
from pathlib import Path


class CarouselPhotoCSPTests(unittest.TestCase):
    def test_http_response_csp_allows_local_blob_images(self):
        app_file = Path(__file__).resolve().parents[1] / "app.py"
        syntax = ast.parse(app_file.read_text(encoding="utf-8"), filename=str(app_file))
        handler = next(node for node in syntax.body
                       if isinstance(node, ast.ClassDef) and node.name == "Handler")
        respond = next(node for node in handler.body
                       if isinstance(node, ast.FunctionDef) and node.name == "respond")
        values = []
        for node in ast.walk(respond):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "send_header" or len(node.args) < 2:
                continue
            try:
                header = ast.literal_eval(node.args[0])
                value = ast.literal_eval(node.args[1])
            except (ValueError, TypeError):
                continue
            if header == "Content-Security-Policy":
                values.append(value)
        self.assertEqual(len(values), 1, "Expected exactly one CSP header in Handler.respond")
        policy = values[0]
        directives = dict((parts[0], parts[1:]) for item in policy.split(";")
                          if (parts := item.strip().split()))
        self.assertIn("blob:", directives["img-src"],
                      "Uploaded photos and generated carousel previews rely on URL.createObjectURL")
        self.assertIn("data:", directives["img-src"])
        self.assertEqual(directives["script-src"], ["'self'"],
                         "Do not weaken script-src to fix image loading")
        self.assertEqual(directives["connect-src"], ["'self'"])


if __name__ == "__main__":
    unittest.main()
