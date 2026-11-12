from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"widget-test-data"
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"widget-test-data"
WEBP_BYTES = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"data"
GIF_BYTES = b"GIF89a" + b"widget-test-data"
BMP_BYTES = b"BM" + b"widget-test-data"


class WidgetImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_dir = app.WIDGET_IMAGE_DIR
        self.original_settings = app.WIDGET_SETTINGS_FILE
        self.original_limit = app.WIDGET_MAX_IMAGE_BYTES
        self.original_size = app.WIDGET_IDLE_SIZE
        self.original_visible = app.WIDGET_VISIBLE
        self.original_window = app.WIDGET_WINDOW
        app.WIDGET_IMAGE_DIR = self.root / "widget"
        app.WIDGET_IMAGE_DIR.mkdir(parents=True)
        app.WIDGET_SETTINGS_FILE = app.WIDGET_IMAGE_DIR / "settings.json"
        app.WIDGET_IDLE_SIZE = app.WIDGET_DEFAULT_SIZE
        app.WIDGET_VISIBLE = False
        app.WIDGET_WINDOW = None

    def tearDown(self):
        app.WIDGET_IMAGE_DIR = self.original_dir
        app.WIDGET_SETTINGS_FILE = self.original_settings
        app.WIDGET_MAX_IMAGE_BYTES = self.original_limit
        app.WIDGET_IDLE_SIZE = self.original_size
        app.WIDGET_VISIBLE = self.original_visible
        app.WIDGET_WINDOW = self.original_window
        self.temp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_detects_supported_image_signatures(self):
        self.assertEqual(app._detect_widget_image_suffix(PNG_BYTES), ".png")
        self.assertEqual(app._detect_widget_image_suffix(JPG_BYTES), ".jpg")
        self.assertEqual(app._detect_widget_image_suffix(WEBP_BYTES), ".webp")
        self.assertEqual(app._detect_widget_image_suffix(GIF_BYTES), ".gif")
        self.assertEqual(app._detect_widget_image_suffix(BMP_BYTES), ".bmp")
        self.assertIsNone(app._detect_widget_image_suffix(b"not an image"))

    def test_save_custom_image_and_overwrite_previous(self):
        jpg = self.write("custom.jpg", JPG_BYTES)
        first = app._save_widget_image(str(jpg))
        self.assertTrue(first["ok"])
        self.assertTrue(first["custom"])
        self.assertEqual(first["name"], "avatar.jpg")
        self.assertTrue(first["url"].startswith("/api/widget/avatar?v="))

        png = self.write("custom.png", PNG_BYTES)
        second = app._save_widget_image(str(png))
        self.assertTrue(second["ok"])
        self.assertEqual(second["name"], "avatar.png")
        saved = sorted(path.name for path in app.WIDGET_IMAGE_DIR.iterdir())
        self.assertEqual(saved, ["avatar.png"])

        info = app._widget_avatar_info()
        self.assertTrue(info["custom"])
        self.assertEqual(app._widget_avatar_path(), app.WIDGET_IMAGE_DIR / "avatar.png")

    def test_rejects_invalid_and_oversized_images(self):
        invalid = self.write("notes.txt", b"not an image")
        result = app._save_widget_image(str(invalid))
        self.assertFalse(result["ok"])
        self.assertIn("PNG", result["message"])

        app.WIDGET_MAX_IMAGE_BYTES = 8
        oversized = self.write("large.png", PNG_BYTES)
        result = app._save_widget_image(str(oversized))
        self.assertFalse(result["ok"])
        self.assertIn("12 MB", result["message"])
        self.assertFalse(any(app.WIDGET_IMAGE_DIR.iterdir()))

    def test_missing_file_is_reported(self):
        result = app._save_widget_image(str(self.root / "missing.png"))
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["message"])

    def test_falls_back_to_bundled_avatar(self):
        info = app._widget_avatar_info()
        self.assertTrue(info["ok"])
        self.assertFalse(info["custom"])
        self.assertEqual(info["name"], "widget-avatar.png")
        self.assertEqual(app._widget_avatar_path(), app.WEB_DIR / "widget-avatar.png")

    def test_widget_info_uses_desktop_icon_default_size(self):
        info = app._widget_info()
        self.assertEqual(info["size"], 64)
        self.assertEqual(info["size_default"], 64)
        self.assertEqual(info["size_min"], 44)
        self.assertEqual(info["size_max"], 160)
        self.assertFalse(info["close_to_widget"])

    def test_size_and_close_preference_are_persisted(self):
        result = app._apply_widget_settings({"size": 96, "close_to_widget": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["size"], 96)
        self.assertTrue(result["close_to_widget"])
        self.assertEqual(app.WIDGET_IDLE_SIZE, 96)

        reloaded = app._read_widget_settings()
        self.assertEqual(reloaded["size"], 96)
        self.assertTrue(reloaded["close_to_widget"])

        clamped = app._apply_widget_settings({"size": 999, "close_to_widget": "false"})
        self.assertEqual(clamped["size"], 160)
        self.assertFalse(clamped["close_to_widget"])

        low = app._apply_widget_settings({"size": 10})
        self.assertEqual(low["size"], 44)

    def test_invalid_settings_file_falls_back_to_defaults(self):
        app.WIDGET_SETTINGS_FILE.write_text("not json", encoding="utf-8")
        settings = app._read_widget_settings()
        self.assertEqual(settings["size"], 64)
        self.assertFalse(settings["close_to_widget"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
