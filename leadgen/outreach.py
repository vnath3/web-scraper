"""Turn scraped leads into personalised demo links + one-tap WhatsApp messages.

Nothing is sent automatically: the output is a CSV and a phone-friendly HTML
page where each lead has a "Send on WhatsApp" button (wa.me deep link with the
message prefilled). You review and send each one yourself.

All vertical-specific values (which demo to show, message text, site URL)
come from config — see config/outreach.yaml and `outreach.demo` in each
sources_*.yaml. No category/location strings are hardcoded here.
"""

from __future__ import annotations

import csv
import html
import re
import sqlite3
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote, urlencode

import yaml
from pydantic import BaseModel, Field, ValidationError

from leadgen.storage import DEFAULT_DB_PATH, init_db

DEFAULT_OUTREACH_CONFIG = Path(__file__).resolve().parent.parent / "config" / "outreach.yaml"


class OutreachSettings(BaseModel):
    site: str = Field(..., min_length=1)
    brand: str = Field(..., min_length=1)
    sender: str = Field(..., min_length=1)
    sender_city: str = ""
    default_limit: int = 20
    follow_up_days: int = 3
    messages: Dict[str, str]


def load_outreach_settings(path: str | Path = DEFAULT_OUTREACH_CONFIG) -> OutreachSettings:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Outreach config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    try:
        return OutreachSettings(**raw)
    except ValidationError as e:
        raise SystemExit(f"Invalid outreach config in {p}:\n{e}") from e


# ---------------------------------------------------------------- pure helpers

_CONTROL = re.compile(r"[\x00-\x1f<>{}\\]")


def clean_business_name(raw: Optional[str]) -> str:
    """Same rules as cleanBusinessName() on flowgridlabs.com — keep in step."""
    s = _CONTROL.sub("", raw or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


def display_name(raw: Optional[str]) -> str:
    """Google listing names often carry extras: 'Atharv Bakery (Mr. A. Shirsat)',
    'Glow Bakes - Best Cakes | Since 1990'. Keep the part a person would say."""
    s = raw or ""
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)  # drop bracketed parts
    s = re.split(r"\s+[-|–—]\s+|\s*\|\s*|,|_", s)[0]  # stop at separators
    cleaned = clean_business_name(s)
    return cleaned or clean_business_name(raw)


def normalize_indian_mobile(raw: Optional[str]) -> Optional[str]:
    """'094052 06776' / '+91 94052-06776' -> '919405206776'. Landlines -> None."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        local = digits
    elif len(digits) == 11 and digits.startswith("0"):
        local = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        local = digits[2:]
    else:
        return None
    return f"91{local}" if re.fullmatch(r"[6-9]\d{9}", local) else None


def demo_link(site: str, demo: str, name: str, ref: str) -> str:
    query = urlencode({"name": name, "ref": ref}, quote_via=quote)
    return f"{site.rstrip('/')}/demos/{demo}/?{query}"


def render_message(template: str, **values: str) -> str:
    return template.format(**values)


def whatsapp_url(phone: str, text: str) -> str:
    return f"https://wa.me/{phone}?text={quote(text, safe='')}"


@dataclass
class OutreachItem:
    place_id: str
    business: str
    listing_name: str
    phone: str
    sub_area: str
    rating: str
    segment_tag: str
    demo_url: str
    message: str
    whatsapp_url: str


@dataclass
class Selection:
    items: List[OutreachItem]
    skipped_no_mobile: List[str]


def build_outreach(
    rows: Iterable[Dict[str, object]],
    *,
    settings: OutreachSettings,
    demo: str,
    lang: str,
    limit: int,
    segment: Optional[str] = None,
    statuses: Sequence[str] = ("new",),
) -> Selection:
    """Pick leads to message and build their link + message. Pure: no I/O."""
    if lang not in settings.messages:
        raise SystemExit(f"No '{lang}' message in outreach config (have: {', '.join(settings.messages)})")
    template = settings.messages[lang]
    items: List[OutreachItem] = []
    skipped: List[str] = []
    seen_phones: set[str] = set()

    for row in rows:
        if len(items) >= limit:
            break
        status = str(row.get("status") or "new")
        if status not in statuses:
            continue
        if segment and str(row.get("segment_tag") or "") != segment:
            continue
        listing = str(row.get("name") or "")
        phone = normalize_indian_mobile(str(row.get("phone") or "")) or normalize_indian_mobile(
            str(row.get("enrichment_phone") or "")
        )
        if not phone:
            skipped.append(listing)
            continue
        if phone in seen_phones:  # same owner, several listings
            continue
        seen_phones.add(phone)

        business = display_name(listing)
        link = demo_link(settings.site, demo, business, f"outreach-{demo}")
        text = render_message(
            template,
            business=business,
            link=link,
            sender=settings.sender,
            brand=settings.brand,
            sender_city=settings.sender_city,
        )
        items.append(
            OutreachItem(
                place_id=str(row.get("place_id") or ""),
                business=business,
                listing_name=listing,
                phone=phone,
                sub_area=str(row.get("sub_area") or ""),
                rating=str(row.get("rating") or ""),
                segment_tag=str(row.get("segment_tag") or ""),
                demo_url=link,
                message=text,
                whatsapp_url=whatsapp_url(phone, text),
            )
        )
    return Selection(items=items, skipped_no_mobile=skipped)


# ---------------------------------------------------------------- I/O

def load_leads(category: str, db_path: str | Path = DEFAULT_DB_PATH) -> List[Dict[str, object]]:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}. Run 'main.py run' first.")
    init_db(db_path)  # applies additive migrations so optional columns exist
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT place_id, name, phone, enrichment_phone, sub_area, rating,
                   segment_tag, status
            FROM leads
            WHERE category = ?
            ORDER BY (segment_tag = 'no_digital_presence') DESC, rating DESC, date_found DESC
            """,
            (category,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def mark_contacted(
    place_ids: Sequence[str],
    follow_up_days: int,
    today: Optional[date] = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Set status='contacted' and a follow-up date — only for leads still 'new'."""
    if not place_ids:
        return 0
    follow_up = ((today or date.today()) + timedelta(days=follow_up_days)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.executemany(
            "UPDATE leads SET status = 'contacted', follow_up_date = ? WHERE place_id = ? AND status = 'new'",
            [(follow_up, pid) for pid in place_ids],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def write_csv(items: Sequence[OutreachItem], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(items[0]).keys()) if items else [f for f in OutreachItem.__dataclass_fields__]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for it in items:
            w.writerow(asdict(it))


def render_html(items: Sequence[OutreachItem], title: str) -> str:
    cards = []
    for i, it in enumerate(items, 1):
        meta = " · ".join(x for x in (it.sub_area, f"★ {it.rating}" if it.rating else "", it.segment_tag.replace("_", " ")) if x)
        cards.append(
            f"""<li class="card" id="c{i}">
  <div class="top"><span class="n">{i}</span><div><b>{html.escape(it.business)}</b>
  <small>{html.escape(meta)}</small><small>+{html.escape(it.phone)}</small></div></div>
  <details><summary>Message</summary><pre>{html.escape(it.message)}</pre></details>
  <div class="btns">
    <a class="send" href="{html.escape(it.whatsapp_url)}" target="_blank" rel="noopener" onclick="document.getElementById('c{i}').classList.add('sent')">Send on WhatsApp</a>
    <a class="demo" href="{html.escape(it.demo_url)}" target="_blank" rel="noopener">Preview demo</a>
  </div>
</li>"""
        )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6f8;color:#111}}
header{{position:sticky;top:0;background:#0e7490;color:#fff;padding:14px 16px}}
header b{{font-size:17px}} header small{{display:block;color:#e6f6f9}}
ol{{list-style:none;margin:0;padding:12px;display:grid;gap:12px;max-width:720px;margin:auto}}
.card{{min-width:0;overflow-wrap:anywhere;background:#fff;border:1px solid #dde3e8;border-radius:14px;padding:14px}}
.card.sent{{opacity:.45}} .card.sent .send::after{{content:" ✓"}}
.top{{display:flex;gap:12px}} .n{{flex:none;width:28px;height:28px;border-radius:50%;background:#e0f2f7;color:#0e7490;font-weight:700;display:grid;place-items:center}}
small{{display:block;color:#5b6670}}
details{{margin:10px 0}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.45;background:#f7f9fa;padding:10px;border-radius:8px;margin:8px 0 0}}
.btns{{display:flex;gap:8px}} .btns a{{flex:1;text-align:center;padding:12px;border-radius:10px;font-weight:600;text-decoration:none}}
.send{{background:#25d366;color:#fff}} .demo{{border:1px solid #cfd8de;color:#0e7490}}
</style></head><body>
<header><b>{html.escape(title)}</b><small>{len(items)} leads · tap "Send on WhatsApp", review the message, send. Sent cards fade.</small></header>
<ol>{''.join(cards)}</ol></body></html>"""


def write_html(items: Sequence[OutreachItem], path: str | Path, title: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(items, title), encoding="utf-8")
