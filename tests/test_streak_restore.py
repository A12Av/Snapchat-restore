import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streak_restore as sr  # noqa: E402

PROFILE = {"username": "my_snap", "email": "me+snap@example.com", "phone": "+1 555 123 4567"}


class FormUrlTest(unittest.TestCase):
    def test_blank_form(self):
        url = sr.form_url()
        self.assertEqual(url, "https://help.snapchat.com/hc/en-us/requests/new?co=true&ticket_form_id=149423")

    def test_prefilled_form(self):
        url = sr.form_url("en-gb", PROFILE, "alex_23")
        parts = urlsplit(url)
        self.assertEqual(parts.path, "/hc/en-gb/requests/new")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(query["ticket_form_id"], "149423")
        self.assertEqual(query["tf_24281229"], "my_snap")
        self.assertEqual(query["tf_24335325"], "me+snap@example.com")
        self.assertEqual(query["tf_24369716"], "+1 555 123 4567")
        self.assertEqual(query["tf_24369736"], "alex_23")
        self.assertEqual(query["tf_anonymous_requester_email"], "me+snap@example.com")
        self.assertNotIn(" ", url)


class InputTest(unittest.TestCase):
    def test_clean_username(self):
        self.assertEqual(sr.clean_username("@alex_23"), "alex_23")
        self.assertEqual(sr.clean_username(" sam.k, "), "sam.k")
        self.assertEqual(sr.clean_username("https://www.snapchat.com/add/jo_jo?share_id=x"), "jo_jo")
        self.assertEqual(sr.clean_username("snapchat.com/@some-one"), "some-one")

    def test_check_username(self):
        self.assertIsNone(sr.check_username("alex_23")[1])
        self.assertIsNotNone(sr.check_username("al")[1])
        self.assertIsNotNone(sr.check_username("bad name!")[1])

    def test_check_email(self):
        self.assertIsNone(sr.check_email("me@example.com")[1])
        self.assertIsNotNone(sr.check_email("me@example")[1])

    def test_check_phone(self):
        self.assertEqual(sr.check_phone(" +44  7700 900123 "), ("+44 7700 900123", None))
        self.assertIsNotNone(sr.check_phone("12345")[1])
        self.assertIsNotNone(sr.check_phone("call me")[1])

    def test_split_usernames(self):
        self.assertEqual(sr.split_usernames("a1b, c2d;e3f  g4h\n"), ["a1b", "c2d", "e3f", "g4h"])


class StoreTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            store = sr.Store(path)
            store.profile = dict(PROFILE)
            store.remember(["alex_23", "Sam.K"])
            store.remember(["sam.k", "jo_jo"])
            store.log("alex_23", "submitted", "link")
            store.settings["delay"] = 3
            store.save()

            again = sr.Store(path)
            self.assertIsNone(again.load())
            self.assertEqual(again.profile, PROFILE)
            self.assertEqual(again.saved, ["alex_23", "Sam.K", "jo_jo"])
            self.assertEqual(again.settings["delay"], 3)
            self.assertEqual(again.sent_counts(), (1, 1))

    def test_bad_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            path.write_text(json.dumps({"settings": {"delay": "soon", "method": "carrier-pigeon"}}))
            store = sr.Store(path)
            store.load()
            self.assertIsNone(store.profile)
            self.assertEqual(store.settings["delay"], sr.DEFAULT_SETTINGS["delay"])
            self.assertEqual(store.settings["method"], "link")

    def test_broken_file_is_kept_aside(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            path.write_text("{not json")
            store = sr.Store(path)
            self.assertIn("Starting fresh", store.load())
            self.assertTrue((Path(folder) / "data.json.broken").exists())


class LookTest(unittest.TestCase):
    def test_display_width(self):
        self.assertEqual(sr.dwidth("abc"), 3)
        self.assertEqual(sr.dwidth("\x1b[93mabc\x1b[0m"), 3)
        self.assertEqual(sr.dwidth("👻 hi"), 5)


if __name__ == "__main__":
    unittest.main()
