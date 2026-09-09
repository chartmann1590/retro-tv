"""Pure-logic tests for livecontent.py card/narration building -- no network,
no ffmpeg/chromium/TTS. Run with venv/bin/python -m unittest discover -s tests -v."""
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import livecontent


class LiveContentTests(unittest.TestCase):
    def test_strip_tags_removes_markup_and_unescapes_entities(self):
        self.assertEqual(livecontent._strip_tags("<p>Troy, N.Y. &#8211; a story</p>"), "Troy, N.Y. – a story")
        self.assertEqual(livecontent._strip_tags(None), "")

    def test_weather_cards_cover_current_hourly_fiveday_radar(self):
        data = {
            "city": "Schenectady", "state": "NY", "radar": None,
            "forecast": [
                {"name": "Tonight", "isDaytime": False, "temperature": 59, "temperatureUnit": "F",
                 "shortForecast": "Mostly Cloudy", "detailedForecast": "Mostly cloudy, with a low around 59."},
                {"name": "Wednesday", "isDaytime": True, "temperature": 81, "temperatureUnit": "F", "shortForecast": "Rain"},
                {"name": "Thursday", "isDaytime": True, "temperature": 80, "temperatureUnit": "F", "shortForecast": "Sunny"},
                {"name": "Friday", "isDaytime": True, "temperature": 74, "temperatureUnit": "F", "shortForecast": "Sunny"},
                {"name": "Saturday", "isDaytime": True, "temperature": 78, "temperatureUnit": "F", "shortForecast": "Sunny"},
                {"name": "Sunday", "isDaytime": True, "temperature": 81, "temperatureUnit": "F", "shortForecast": "Rain"},
            ],
            "hourly": [
                {"startTime": f"2026-09-08T2{i}:00:00-04:00", "temperature": 66 - i, "shortForecast": "Mostly Cloudy"}
                for i in range(4)
            ],
        }
        cards = livecontent._weather_cards(data)
        titles = [c[0] for c in cards]
        self.assertEqual(titles, ["Current Conditions", "Hourly Forecast", "5-Day Forecast", "Live Radar"])
        self.assertIn("59 degrees", cards[0][2])
        self.assertIn("Schenectady", cards[0][2])
        self.assertIn("Wednesday", cards[2][2])
        self.assertEqual(len([p for p in data["forecast"] if p["isDaytime"]]), 5)
        for _, _, narration, body in cards:
            self.assertTrue(narration.strip())
            self.assertIn("<div", body)

    def test_weather_cards_handle_missing_radar_gracefully(self):
        data = {"city": "Schenectady", "state": "NY", "radar": None,
                "forecast": [{"name": "Tonight", "isDaytime": False, "temperature": 59,
                              "temperatureUnit": "F", "shortForecast": "Clear"}],
                "hourly": [{"startTime": "2026-09-08T22:00:00-04:00", "temperature": 60, "shortForecast": "Clear"}]}
        cards = livecontent._weather_cards(data)
        radar_title, radar_sub, radar_narration, radar_body = cards[-1]
        self.assertEqual(radar_title, "Live Radar")
        self.assertIn("unavailable", radar_body.lower())

    def test_news_cards_narration_includes_headline_and_summary(self):
        items = [
            {"title": "Local bridge reopens after repairs", "desc": "Crews finished ahead of schedule.", "image": None},
            {"title": "Second story", "desc": "Some details here.", "image": b"\xff\xd8fakejpeg"},
        ]
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "LIVE_CONTENT_DIR", tmp):
            os.makedirs(os.path.join(tmp, "news"))
            cards = livecontent._news_cards(items)
        self.assertEqual(len(cards), 2)
        self.assertIn("Local bridge reopens", cards[0][2])
        self.assertIn("Crews finished", cards[0][2])
        # no image -> no <img> tag in the card body; with image -> tag present
        self.assertNotIn("<img", cards[0][3])
        self.assertIn("<img", cards[1][3])


if __name__ == "__main__":
    unittest.main()
