"""Tests for leadgen/outreach.py. Run: python -m unittest discover -s tests

Uses made-up categories ("bakery") on purpose — real config strings must
not appear in .py files (see scripts/verify_no_hardcoding.py).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from leadgen.outreach import (
    OutreachSettings,
    build_outreach,
    clean_business_name,
    demo_link,
    display_name,
    load_leads,
    load_outreach_settings,
    mark_contacted,
    normalize_indian_mobile,
    render_html,
    write_csv,
)
from leadgen.storage import init_db

SETTINGS = OutreachSettings(
    site="https://example.test/",
    brand="Brand",
    sender="Sam",
    sender_city="Townville",
    messages={"en": "Hi {business} — {sender} of {brand}, {sender_city}: {link}", "mr": "नमस्कार {business}: {link}"},
)


def row(pid, name, phone, status="new", segment="no_digital_presence", **kw):
    return {"place_id": pid, "name": name, "phone": phone, "status": status, "segment_tag": segment, "sub_area": "Old Town", "rating": 4.6, **kw}


class PhoneTests(unittest.TestCase):
    def test_mobiles(self):
        for raw in ("094052 06776", "+91 94052-06776", "9405206776", "919405206776"):
            self.assertEqual(normalize_indian_mobile(raw), "919405206776", raw)

    def test_rejects_landline_and_junk(self):
        for raw in ("0240 233 1234", "12345", "", None, "5405206776"):
            self.assertIsNone(normalize_indian_mobile(raw), raw)


class NameTests(unittest.TestCase):
    def test_display_name_drops_listing_extras(self):
        self.assertEqual(display_name("Atharv Bakery (Mr. A. Shirsat)"), "Atharv Bakery")
        self.assertEqual(display_name("Glow Bakes - Best Cakes | Since 1990"), "Glow Bakes")
        self.assertEqual(display_name("Sweet Spot, Old Town"), "Sweet Spot")
        self.assertEqual(display_name("SHELKE BAKERY_MR. SHELKE (EST 1990)"), "SHELKE BAKERY")
        self.assertEqual(display_name("(Only Brackets)"), "(Only Brackets)")

    def test_clean_matches_site_rules(self):
        self.assertEqual(clean_business_name("  A   <b>B</b> {x}\\ "), "A bB/b x")
        self.assertEqual(len(clean_business_name("y" * 80)), 40)


class LinkTests(unittest.TestCase):
    def test_demo_link_is_personalised_and_encoded(self):
        url = demo_link("https://example.test/", "bakery", "Raj & Sons", "outreach-bakery")
        u = urlparse(url)
        self.assertEqual(u.path, "/demos/bakery/")
        q = parse_qs(u.query)
        self.assertEqual(q["name"], ["Raj & Sons"])
        self.assertEqual(q["ref"], ["outreach-bakery"])


class BuildTests(unittest.TestCase):
    def test_builds_messages_and_whatsapp_links(self):
        sel = build_outreach([row("p1", "Atharv Bakery (Owner)", "094052 06776")], settings=SETTINGS, demo="bakery", lang="en", limit=5)
        self.assertEqual(len(sel.items), 1)
        it = sel.items[0]
        self.assertEqual(it.business, "Atharv Bakery")
        self.assertIn("Hi Atharv Bakery — Sam of Brand, Townville: https://example.test/demos/bakery/?name=Atharv%20Bakery", it.message)
        self.assertTrue(it.whatsapp_url.startswith("https://wa.me/919405206776?text="))
        self.assertEqual(unquote(it.whatsapp_url.split("text=")[1]), it.message)

    def test_marathi_template(self):
        sel = build_outreach([row("p1", "Atharv Bakery", "9405206776")], settings=SETTINGS, demo="bakery", lang="mr", limit=5)
        self.assertTrue(sel.items[0].message.startswith("नमस्कार Atharv Bakery"))

    def test_filters_status_segment_limit_and_duplicates(self):
        rows = [
            row("p1", "A", "9405206771"),
            row("p2", "B", "9405206772", status="contacted"),
            row("p3", "C", "9405206773", segment="has_website"),
            row("p4", "D", "0240 2331234"),  # landline
            row("p5", "E", "", enrichment_phone="9405206775"),  # enrichment fallback
            row("p6", "F", "9405206771"),  # same number as p1
            row("p7", "G", "9405206777"),
        ]
        sel = build_outreach(rows, settings=SETTINGS, demo="bakery", lang="en", limit=3, segment="no_digital_presence")
        self.assertEqual([i.place_id for i in sel.items], ["p1", "p5", "p7"])
        self.assertEqual(sel.skipped_no_mobile, ["D"])

    def test_unknown_language_is_a_clear_error(self):
        with self.assertRaises(SystemExit):
            build_outreach([], settings=SETTINGS, demo="bakery", lang="hi", limit=1)


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "leads.db"
        init_db(self.db)
        conn = sqlite3.connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
        base = {"place_id": None, "name": None, "phone": None, "category": "bakery", "status": "new", "segment_tag": "no_digital_presence", "sub_area": "Old Town", "rating": 4.5, "date_found": "2026-11-01"}
        for pid, name, phone, status in [("p1", "One", "9405206771", "new"), ("p2", "Two", "9405206772", "contacted")]:
            rec = {**base, "place_id": pid, "name": name, "phone": phone, "status": status}
            rec = {k: v for k, v in rec.items() if k in cols}
            conn.execute(f"INSERT INTO leads ({', '.join(rec)}) VALUES ({', '.join('?' * len(rec))})", list(rec.values()))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_mark_contacted(self):
        rows = load_leads("bakery", self.db)
        self.assertEqual({r["place_id"] for r in rows}, {"p1", "p2"})
        n = mark_contacted(["p1", "p2"], follow_up_days=3, today=date(2026, 11, 28), db_path=self.db)
        self.assertEqual(n, 1)  # p2 was already contacted — untouched
        conn = sqlite3.connect(self.db)
        status, fu = conn.execute("SELECT status, follow_up_date FROM leads WHERE place_id='p1'").fetchone()
        conn.close()
        self.assertEqual((status, fu), ("contacted", "2026-12-01"))


class OutputTests(unittest.TestCase):
    def test_html_and_csv(self):
        sel = build_outreach([row("p1", "Raj <Sweets>", "9405206776")], settings=SETTINGS, demo="bakery", lang="en", limit=1)
        page = render_html(sel.items, "Bakery list")
        self.assertIn("Send on WhatsApp", page)
        self.assertIn("Raj Sweets", page)
        self.assertNotIn("<Sweets>", page)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.csv"
            write_csv(sel.items, p)
            self.assertIn("whatsapp_url", p.read_text(encoding="utf-8").splitlines()[0])

    def test_real_outreach_config_loads(self):
        s = load_outreach_settings()
        self.assertIn("en", s.messages)
        self.assertIn("mr", s.messages)
        for key in ("{business}", "{link}"):
            self.assertIn(key, s.messages["en"])
            self.assertIn(key, s.messages["mr"])


if __name__ == "__main__":
    unittest.main()
