"""Lossless Pillow slide tests: ICC, unchanged pixels, deterministic layout."""
from __future__ import annotations

import base64
import io
import unittest

from PIL import Image, ImageCms

from carousel_images import ImageError, compose_slide


def data_url(image: Image.Image, image_format="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=image_format, **kwargs)
    mime = "jpeg" if image_format == "JPEG" else image_format.lower()
    return "data:image/" + mime + ";base64," + base64.b64encode(stream.getvalue()).decode("ascii"), stream.getvalue()


class PillowCarouselTests(unittest.TestCase):
    def setUp(self):
        self.profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.image = Image.new("RGB", (800, 1200), (23, 109, 174))
        self.payload = {
            "slide": {"layout": "text_top", "text_blocks": [
                {"type": "headline", "text": "Привет, мир!", "x": 50, "y": 18,
                 "font_size": 48, "font_weight": 400, "color": "#FFFFFF",
                 "align": "center"}
            ]},
            "width": 800, "height": 1200, "layout_width": 800,
        }

    def test_original_pixels_unchanged_outside_text_and_icc_preserved(self):
        self.payload["image"], original = data_url(self.image, icc_profile=self.profile)
        rendered, dims = compose_slide(self.payload)
        self.assertEqual(dims, (800, 1200))
        self.assertTrue(rendered.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(rendered)) as output:
            self.assertEqual(output.size, self.image.size)
            self.assertEqual(output.info.get("icc_profile"), self.profile)
            self.assertEqual(output.getpixel((1, 1)), self.image.getpixel((1, 1)))
            self.assertEqual(output.getpixel((700, 1100)), self.image.getpixel((700, 1100)))
            self.assertTrue(any(output.getpixel((x, y)) != (23, 109, 174)
                                for x in range(200, 600) for y in range(205, 275)))
        # User's uploaded source is never rewritten or mutated.
        self.assertEqual(self.image.getpixel((400, 220)), (23, 109, 174))
        self.assertIn(b"iCCP", rendered)
        self.assertIn(b"IDAT", original)

    def test_jpeg_source_is_decoded_once_and_exported_as_png(self):
        self.payload["image"], raw = data_url(self.image, "JPEG", icc_profile=self.profile, quality=92)
        rendered, dims = compose_slide(self.payload)
        with Image.open(io.BytesIO(raw)) as decoded, Image.open(io.BytesIO(rendered)) as output:
            self.assertEqual(dims, decoded.size)
            self.assertEqual(output.info.get("icc_profile"), self.profile)
            self.assertEqual(output.getpixel((0, 0)), decoded.getpixel((0, 0)))

    def test_resize_only_when_requested_dimensions_differ(self):
        self.payload["image"], _ = data_url(self.image)
        self.payload["width"] = 720
        self.payload["height"] = 1280
        png_data, dims = compose_slide(self.payload)
        self.assertEqual(dims, (720, 1280))
        with Image.open(io.BytesIO(png_data)) as output:
            self.assertEqual(output.size, (720, 1280))

    def test_rejects_invalid_image_and_unrequested_cta(self):
        self.payload["image"] = "file:///etc/passwd"
        with self.assertRaises(ImageError):
            compose_slide(self.payload)
        self.payload["image"], _ = data_url(self.image)
        self.payload["slide"]["cta"] = {"enabled": True}
        with self.assertRaises(ImageError) as error:
            compose_slide(self.payload)
        self.assertEqual(error.exception.code, "missing_cta")

    def test_plain_rgb_png_without_profile_remains_rgb(self):
        self.payload["image"], _ = data_url(self.image)
        rendered, _ = compose_slide(self.payload)
        with Image.open(io.BytesIO(rendered)) as output:
            self.assertEqual(output.mode, "RGB")
            self.assertEqual(output.getpixel((0, 0)), (23, 109, 174))


if __name__ == "__main__":
    unittest.main()
