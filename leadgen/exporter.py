"""Export the leads table to CSV."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from leadgen.storage import DEFAULT_DB_PATH

COLUMNS = [
    "place_id",
    "name",
    "phone",
    "address",
    "category",
    "website",
    "rating",
    "rating_count",
    "business_status",
    "segment_tag",
    "sub_area",
    "date_found",
    "status",
    "enrichment_source",
    "enrichment_phone",
    "enrichment_website_found",
    "enrichment_checked_at",
]


def export_to_csv(
    output_path: str | Path, db_path: str | Path = DEFAULT_DB_PATH
) -> int:
    """Dump the leads table to CSV, sorted by date_found descending.

    Returns the number of rows written.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}. Run 'main.py run' first.")

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM leads ORDER BY date_found DESC"
        ).fetchall()
    finally:
        conn.close()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([row[col] for col in COLUMNS])

    return len(rows)
