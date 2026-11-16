"""Basic localization integration checks."""
import os
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from flask import Flask, render_template
from flask_babel import get_locale
from i18n import init_i18n, js_catalog, js_message_ids, select_locale


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        init_i18n(self.app)

    def test_default_language(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.app.test_request_context("/", headers={"Accept-Language": "fr"}):
                self.assertEqual(str(get_locale()), "en")

    def test_browser_language(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.app.test_request_context("/", headers={"Accept-Language": "he-IL,he;q=0.9,en;q=0.5"}):
                self.assertEqual(str(get_locale()), "he")

    def test_forced_language(self):
        with patch.dict(os.environ, {"RETRO_TV_LANGUAGE": "he-IL"}):
            with self.app.test_request_context("/"):
                self.assertEqual(str(get_locale()), "he")

    def render_base(self, language):
        with patch.dict(os.environ, {"RETRO_TV_LANGUAGE": language}):
            with self.app.test_request_context("/"):
                return render_template(
                    "base.html",
                    current_language=str(get_locale()),
                    js_translations=js_catalog(),
                    config_timezone="America/New_York",
                )

    def test_english_template_keeps_ltr_and_navigation(self):
        html = self.render_base("en")
        self.assertIn('<html lang="en" dir="ltr">', html)
        self.assertIn('>TV Guide</a>', html)
        self.assertIn('>Sports</a>', html)
        self.assertIn('window.TV_TRANSLATIONS=', html)

    def test_hebrew_template_sets_rtl(self):
        html = self.render_base("he")
        self.assertIn('<html lang="he" dir="rtl">', html)
        self.assertIn('window.TV_LANGUAGE="he"', html)

    def test_locale_without_request(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(select_locale(), "en")

    def test_browser_message_extraction(self):
        keys = js_message_ids()
        self.assertIn("The receiver could not complete that request.", keys)
        self.assertIn("Tuned to CH %(channel)s", keys)

    def test_catalog_metadata_and_extraction_coverage(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "translations" / "he" / "LC_MESSAGES" / "messages.po").open("rb") as source:
            hebrew = read_po(source, locale="he")
        headers = dict(hebrew.mime_headers)
        self.assertEqual(headers.get("Language"), "he")
        self.assertEqual(headers.get("Content-Type", "").lower(), "text/plain; charset=utf-8")
        with (root / "translations" / "messages.pot").open("rb") as source:
            template = read_po(source)
        ids = {message.id for message in template if isinstance(message.id, str)}
        missing = set(js_message_ids()) - ids
        self.assertEqual(missing, set(), f"Missing JavaScript strings in POT: {sorted(missing)}")

    def test_real_hebrew_translation_is_exported(self):
        message = "Tuned to CH %(channel)s"
        translated = "עובר לערוץ %(channel)s"
        with TemporaryDirectory() as tmp:
            catalog = Catalog(locale="he", domain="messages")
            catalog.add(message, translated)
            directory = Path(tmp) / "translations" / "he" / "LC_MESSAGES"
            directory.mkdir(parents=True)
            with (directory / "messages.mo").open("wb") as output:
                write_mo(output, catalog)
            app = Flask("i18n-integration", root_path=tmp)
            init_i18n(app)
            with patch.dict(os.environ, {"RETRO_TV_LANGUAGE": "he"}):
                with app.test_request_context("/"):
                    self.assertEqual(js_catalog().get(message), translated)

    def test_js_catalog(self):
        with self.app.test_request_context("/"):
            self.assertIsInstance(js_catalog(), dict)


if __name__ == "__main__":
    unittest.main()
