"""SQLite storage for scraped leads."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    place_id TEXT PRIMARY KEY,
    name TEXT,
    phone TEXT,
    address TEXT,
    category TEXT,
    website TEXT,
    rating REAL,
    rating_count INTEGER,
    business_status TEXT,
    segment_tag TEXT,
    sub_area TEXT,
    date_found TEXT,
    status TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS api_call_log (
    date TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, endpoint)
);

CREATE TABLE IF NOT EXISTS scraped_subareas (
    category TEXT NOT NULL,
    sub_area TEXT NOT NULL,
    last_scraped_at TEXT NOT NULL,
    PRIMARY KEY (category, sub_area)
);
"""

# Columns added after the initial release. Applied via ALTER TABLE so
# existing databases are migrated in place rather than dropped/recreated.
ENRICHMENT_COLUMNS = {
    "enrichment_source": "TEXT",
    "enrichment_phone": "TEXT",
    "enrichment_website_found": "TEXT",
    "enrichment_checked_at": "TEXT",
}

NICHE_COLUMNS = {
    "google_types": "TEXT",  # raw Places `types` array, stored comma-separated
    "niche_tag": "TEXT",
}

# Minimal outcome-tracking: one free-form note (overwritten, not a log) and
# one next-contact date. Deliberately not a CRM — see DECISIONS.md.
OUTCOME_COLUMNS = {
    "notes": "TEXT",
    "follow_up_date": "TEXT",
}


def _migrate_columns(conn: sqlite3.Connection, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for column, col_type in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {col_type}")


@contextmanager
def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate_columns(conn, ENRICHMENT_COLUMNS)
        _migrate_columns(conn, NICHE_COLUMNS)
        _migrate_columns(conn, OUTCOME_COLUMNS)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path):
        pass


def insert_lead(lead: Dict[str, Any], db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """Insert a qualifying lead. Returns True if a new row was inserted."""
    google_types = lead.get("google_types")
    if isinstance(google_types, list):
        google_types = ",".join(google_types)

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO leads (
                place_id, name, phone, address, category, website,
                rating, rating_count, business_status, segment_tag,
                sub_area, date_found, status, google_types, niche_tag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (
                lead.get("place_id"),
                lead.get("name"),
                lead.get("phone"),
                lead.get("address"),
                lead.get("category"),
                lead.get("website"),
                lead.get("rating"),
                lead.get("rating_count"),
                lead.get("business_status"),
                lead.get("segment_tag"),
                lead.get("sub_area"),
                lead.get("date_found", date.today().isoformat()),
                google_types,
                lead.get("niche_tag"),
            ),
        )
        return cursor.rowcount > 0


def log_api_call(endpoint: str, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Increment today's call count for the given endpoint."""
    today = date.today().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO api_call_log (date, endpoint, count) VALUES (?, ?, 1)
            ON CONFLICT(date, endpoint) DO UPDATE SET count = count + 1
            """,
            (today, endpoint),
        )


def get_calls_this_month(
    endpoint: Optional[str] = None, db_path: str | Path = DEFAULT_DB_PATH
) -> int:
    """Sum api_call_log counts for the current calendar month.

    If endpoint is None, sums across all endpoints.
    """
    month_prefix = date.today().isoformat()[:7]  # "YYYY-MM"
    with _connect(db_path) as conn:
        if endpoint is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM api_call_log WHERE date LIKE ?",
                (f"{month_prefix}%",),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM api_call_log "
                "WHERE date LIKE ? AND endpoint = ?",
                (f"{month_prefix}%", endpoint),
            ).fetchone()
        return row[0]


def get_subarea_last_scraped(
    category: str, sub_area: str, db_path: str | Path = DEFAULT_DB_PATH
) -> Optional[str]:
    """Return the last_scraped_at timestamp for (category, sub_area), or None."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_scraped_at FROM scraped_subareas WHERE category = ? AND sub_area = ?",
            (category, sub_area),
        ).fetchone()
        return row[0] if row else None


def mark_subarea_scraped(
    category: str,
    sub_area: str,
    scraped_at: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Record that (category, sub_area) was scraped, upserting the timestamp."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO scraped_subareas (category, sub_area, last_scraped_at)
            VALUES (?, ?, ?)
            ON CONFLICT(category, sub_area) DO UPDATE SET last_scraped_at = excluded.last_scraped_at
            """,
            (category, sub_area, scraped_at),
        )


def get_new_leads_since(
    since_date: str | date | datetime, db_path: str | Path = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Return leads with date_found >= since_date (ISO date string or date/datetime)."""
    if isinstance(since_date, (date, datetime)):
        since_date = since_date.isoformat()[:10]

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM leads WHERE date_found >= ? ORDER BY date_found DESC",
            (since_date,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_leads_needing_enrichment(
    limit: int, db_path: str | Path = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Return unenriched no_digital_presence leads, oldest first, up to limit."""
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE segment_tag = 'no_digital_presence'
              AND enrichment_checked_at IS NULL
            ORDER BY date_found ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def count_leads_needing_enrichment(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Count of no_digital_presence leads not yet checked (for skip reporting)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM leads
            WHERE segment_tag = 'no_digital_presence'
              AND enrichment_checked_at IS NULL
            """
        ).fetchone()
        return row[0]


def get_leads_missing_niche_tag(
    category: str, db_path: str | Path = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Return leads for a category with no niche_tag set yet (for backfill)."""
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM leads WHERE category = ? AND niche_tag IS NULL",
            (category,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_niche_tag(
    place_id: str, niche_tag: str, db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    """Set niche_tag for a single lead (used by the backfill script)."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE leads SET niche_tag = ? WHERE place_id = ?",
            (niche_tag, place_id),
        )


def update_notes(
    place_id: str, notes: Optional[str], db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    """Overwrite the free-form notes field for a single lead."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE leads SET notes = ? WHERE place_id = ?",
            (notes, place_id),
        )


def update_follow_up_date(
    place_id: str, follow_up_date: Optional[str], db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    """Set (or clear, if None) the next-contact date for a single lead."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE leads SET follow_up_date = ? WHERE place_id = ?",
            (follow_up_date, place_id),
        )


def get_leads_due_for_followup(
    as_of_date: str | date, db_path: str | Path = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Leads with follow_up_date <= as_of_date, excluding converted/rejected."""
    if isinstance(as_of_date, date):
        as_of_date = as_of_date.isoformat()

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE follow_up_date IS NOT NULL
              AND follow_up_date <= ?
              AND status NOT IN ('converted', 'rejected')
            ORDER BY follow_up_date ASC
            """,
            (as_of_date,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_enrichment(
    place_id: str,
    source: str,
    phone: Optional[str],
    website_found: Optional[str],
    checked_at: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Record the outcome of an enrichment lookup for a lead."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE leads
            SET enrichment_source = ?,
                enrichment_phone = ?,
                enrichment_website_found = ?,
                enrichment_checked_at = ?
            WHERE place_id = ?
            """,
            (source, phone, website_found, checked_at, place_id),
        )
