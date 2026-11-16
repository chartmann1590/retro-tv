"""Flask/Jinja localization and browser-side gettext message lookup."""
import os
from functools import lru_cache
from pathlib import Path

from babel.messages.extract import extract_from_file
from flask import has_request_context, request
from flask_babel import Babel, gettext

SUPPORTED_LANGUAGES = ("en", "he")
JS_DIR = Path(__file__).resolve().parent / "static" / "js"


def select_locale():
    configured = os.environ.get("RETRO_TV_LANGUAGE", "").lower().replace("_", "-").split("-")[0]
    if configured in SUPPORTED_LANGUAGES:
        return configured
    if not has_request_context():
        return "en"
    return request.accept_languages.best_match(SUPPORTED_LANGUAGES) or "en"


def init_i18n(app):
    app.config["BABEL_DEFAULT_LOCALE"] = "en"
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
    Babel(app, locale_selector=select_locale)


@lru_cache(maxsize=1)
def js_message_ids():
    """Extract JS keys once using Babel's public message extraction API."""
    ids = set()
    for path in JS_DIR.glob("*.js"):
        for _, message, _, _ in extract_from_file(
            "javascript", path, keywords={"gettext": None}
        ):
            if isinstance(message, str) and message:
                ids.add(message)
    return tuple(sorted(ids))


def js_catalog():
    """Export translated browser strings using Flask-Babel's public gettext API."""
    return {message: translated for message in js_message_ids()
            if (translated := gettext(message)) != message}
