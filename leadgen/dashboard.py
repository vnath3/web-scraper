"""Streamlit dashboard for browsing and triaging scraped leads.

Read-only against the leads table, with one explicit write path: updating
the `status` column from the grid. Does not touch the scraping/enrichment
pipeline — it only reads (and, for status, writes) data/leads.db.

Run with: streamlit run leadgen/dashboard.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import List

import pandas as pd
import streamlit as st

# `streamlit run` puts this file's own directory on sys.path, not the repo
# root, so the `leadgen` package isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen.config_loader import load_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _REPO_ROOT / "data" / "leads.db"
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "sources.yaml"

STATUS_OPTIONS = ["new", "contacted", "no_answer", "converted", "rejected"]
SEGMENT_TAGS = [
    "no_digital_presence",
    "reputation_angle",
    "visibility_angle",
    "general_outreach",
]

GRID_COLUMNS = [
    "name",
    "category",
    "phone",
    "address",
    "sub_area",
    "website",
    "rating",
    "segment_tag",
    "niche_tag",
    "status",
    "date_found",
    "enrichment_source",
    "enrichment_phone",
    "enrichment_website_found",
    "enrichment_checked_at",
]


def _read_only_connection() -> sqlite3.Connection:
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _load_leads() -> pd.DataFrame:
    with _read_only_connection() as conn:
        return pd.read_sql_query("SELECT * FROM leads", conn)


def _load_calls_this_month() -> int:
    month_prefix = pd.Timestamp.today().strftime("%Y-%m")
    with _read_only_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM api_call_log WHERE date LIKE ?",
            (f"{month_prefix}%",),
        ).fetchone()
        return int(row[0])


def _load_monthly_budget() -> int:
    try:
        config = load_config(DEFAULT_CONFIG_PATH)
    except SystemExit:
        return 8000
    if config.api_budget is None:
        return 8000
    return config.api_budget.max_calls_per_month


def _save_status_changes(changes: dict[str, str]) -> None:
    """Write only the status column for the given place_ids. No other column."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executemany(
            "UPDATE leads SET status = ? WHERE place_id = ?",
            [(status, place_id) for place_id, status in changes.items()],
        )
        conn.commit()
    finally:
        conn.close()


def _apply_filters(
    df: pd.DataFrame,
    sub_areas: List[str],
    segment_tags: List[str],
    niche_tags: List[str],
    statuses: List[str],
    has_website: str,
) -> pd.DataFrame:
    filtered = df
    if sub_areas:
        filtered = filtered[filtered["sub_area"].isin(sub_areas)]
    if segment_tags:
        filtered = filtered[filtered["segment_tag"].isin(segment_tags)]
    if niche_tags:
        filtered = filtered[filtered["niche_tag"].isin(niche_tags)]
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]
    if has_website == "Yes":
        filtered = filtered[filtered["website"].notna() & (filtered["website"] != "")]
    elif has_website == "No":
        filtered = filtered[filtered["website"].isna() | (filtered["website"] == "")]
    return filtered


def _render_summary(df: pd.DataFrame) -> None:
    cols = st.columns(1 + len(SEGMENT_TAGS))
    cols[0].metric("Total leads", len(df))
    for col, tag in zip(cols[1:], SEGMENT_TAGS):
        col.metric(tag, int((df["segment_tag"] == tag).sum()))

    status_counts = df["status"].fillna("new").value_counts()
    status_cols = st.columns(max(len(status_counts), 1))
    for col, (status_value, count) in zip(status_cols, status_counts.items()):
        col.metric(f"status: {status_value}", int(count))


def _render_category_tab(category: str, category_df: pd.DataFrame) -> None:
    _render_summary(category_df)

    display_df = category_df[["place_id", *GRID_COLUMNS]].set_index("place_id")

    edited_df = st.data_editor(
        display_df,
        key=f"editor_{category}",
        hide_index=True,
        use_container_width=True,
        row_height=32,
        column_config={
            "name": st.column_config.TextColumn("Name", width="medium", disabled=True),
            "category": st.column_config.TextColumn("Category", width="small", disabled=True),
            "phone": st.column_config.TextColumn("Phone", width="medium", disabled=True),
            "address": st.column_config.TextColumn("Address", width="large", disabled=True),
            "sub_area": st.column_config.TextColumn("Sub-area", width="small", disabled=True),
            "website": st.column_config.TextColumn("Website", width="large", disabled=True),
            "rating": st.column_config.NumberColumn("Rating", width="small", disabled=True),
            "segment_tag": st.column_config.TextColumn("Segment", width="medium", disabled=True),
            "niche_tag": st.column_config.TextColumn("Niche", width="medium", disabled=True),
            "status": st.column_config.SelectboxColumn(
                "Status", width="small", options=STATUS_OPTIONS, required=True
            ),
            "date_found": st.column_config.TextColumn("Date found", width="small", disabled=True),
            "enrichment_source": st.column_config.TextColumn(
                "Enrichment source", width="small", disabled=True
            ),
            "enrichment_phone": st.column_config.TextColumn(
                "Enrichment phone", width="medium", disabled=True
            ),
            "enrichment_website_found": st.column_config.TextColumn(
                "Enrichment website", width="large", disabled=True
            ),
            "enrichment_checked_at": st.column_config.TextColumn(
                "Enrichment checked at", width="medium", disabled=True
            ),
        },
    )

    save_col, export_col = st.columns([1, 1])

    with save_col:
        if st.button("Save status changes", key=f"save_{category}"):
            changes = {
                place_id: new_row["status"]
                for place_id, new_row in edited_df.iterrows()
                if new_row["status"] != display_df.loc[place_id, "status"]
            }
            if changes:
                _save_status_changes(changes)
                st.success(f"Saved {len(changes)} status change(s).")
                st.rerun()
            else:
                st.info("No status changes to save.")

    with export_col:
        csv_bytes = category_df[GRID_COLUMNS].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export this view to CSV",
            data=csv_bytes,
            file_name=f"leads_{category}.csv",
            mime="text/csv",
            key=f"export_{category}",
        )


def main() -> None:
    st.set_page_config(page_title="Lead Generation Dashboard", layout="wide")
    st.title("Lead Generation Dashboard")

    if not DB_PATH.exists():
        st.warning(
            f"No database found at {DB_PATH}. Run `python main.py run --config "
            f"config/sources.yaml` first to collect leads."
        )
        return

    calls_used = _load_calls_this_month()
    budget = _load_monthly_budget()
    st.caption(f"API calls this month: {calls_used:,} / {budget:,}")

    df = _load_leads()
    if df.empty:
        st.info("The leads table is empty. Run the scraper to populate it.")
        return

    st.sidebar.header("Filters")
    sub_areas = st.sidebar.multiselect(
        "Sub-area", sorted(df["sub_area"].dropna().unique().tolist())
    )
    segment_tags = st.sidebar.multiselect(
        "Segment tag", sorted(df["segment_tag"].dropna().unique().tolist())
    )
    niche_tags = st.sidebar.multiselect(
        "Niche", sorted(df["niche_tag"].dropna().unique().tolist())
    )
    statuses = st.sidebar.multiselect(
        "Status", sorted(df["status"].dropna().unique().tolist())
    )
    has_website = st.sidebar.radio("Has website", ["All", "Yes", "No"], horizontal=True)

    categories = sorted(df["category"].dropna().unique().tolist())
    tabs = st.tabs(categories)

    for tab, category in zip(tabs, categories):
        with tab:
            category_df = df[df["category"] == category]
            filtered_df = _apply_filters(
                category_df, sub_areas, segment_tags, niche_tags, statuses, has_website
            )
            _render_category_tab(category, filtered_df)


if __name__ == "__main__":
    main()
