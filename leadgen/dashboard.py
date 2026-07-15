"""Streamlit dashboard for browsing and triaging scraped leads.

Read-only against the leads table, with one explicit write path: updating
the `status` column from the grid. Does not touch the scraping/enrichment
pipeline — it only reads (and, for status, writes) data/leads.db.

Run with: streamlit run leadgen/dashboard.py
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import yaml
from pydantic import ValidationError

# `streamlit run` puts this file's own directory on sys.path, not the repo
# root, so the `leadgen` package isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen.config_loader import SourceConfig, load_config  # noqa: E402
from leadgen.storage import get_subarea_last_scraped  # noqa: E402
from main import run_stream  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _REPO_ROOT / "data" / "leads.db"
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "sources.yaml"
CONFIG_DIR = _REPO_ROOT / "config"

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


def _slugify_category(category: str) -> str:
    slug = category.strip().lower()
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def _parse_sub_areas(raw: str) -> List[str]:
    parts = re.split(r"[\n,]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _list_config_files() -> List[str]:
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.name for p in CONFIG_DIR.glob("*.yaml"))


def _estimate_subareas_to_run(config: SourceConfig, force_rescrape: bool) -> int:
    """Lower-bound estimate of sub-areas this run will actually hit."""
    if force_rescrape or not DB_PATH.exists():
        return len(config.sub_areas)
    return sum(
        1
        for sub_area in config.sub_areas
        if get_subarea_last_scraped(config.category, sub_area) is None
    )


def _handle_save_config(
    category: str,
    location: str,
    sub_areas_raw: str,
    radius_meters: int,
    require_phone: bool,
    niche_keywords: Dict[str, List[str]],
) -> None:
    errors = []
    category_slug = _slugify_category(category)
    sub_areas = _parse_sub_areas(sub_areas_raw)

    if not category.strip():
        errors.append("Category is required.")
    elif not category_slug:
        errors.append(
            "Category must contain at least one letter or digit to generate a filename."
        )
    if not location.strip():
        errors.append("Location is required.")
    if not sub_areas:
        errors.append("At least one sub-area is required.")

    if errors:
        for error in errors:
            st.error(error)
        return

    filename = f"sources_{category_slug}.yaml"
    config_path = CONFIG_DIR / filename

    if config_path.exists() and not st.session_state.get("new_cfg_overwrite", False):
        st.error(
            f"config/{filename} already exists. Check 'Overwrite existing config' "
            f"below to replace it, then click Save Config again."
        )
        return

    raw_config: Dict[str, object] = {
        "category": category.strip(),
        "location": location.strip(),
        "sub_areas": sub_areas,
        "radius_meters": int(radius_meters),
        "require_phone": bool(require_phone),
    }
    if niche_keywords:
        raw_config["niche_keywords"] = niche_keywords

    try:
        validated = SourceConfig(**raw_config)
    except ValidationError as exc:
        st.error("Config failed validation — nothing was written:")
        st.code(str(exc))
        return

    output: Dict[str, object] = {
        "category": validated.category,
        "location": validated.location,
        "sub_areas": validated.sub_areas,
        "radius_meters": validated.radius_meters,
        "require_phone": validated.require_phone,
    }
    if validated.niche_keywords:
        output["niche_keywords"] = validated.niche_keywords

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(output, f, sort_keys=False, allow_unicode=True)

    st.success(f"Saved config/{filename}")
    st.session_state["last_saved_config"] = filename


def _execute_run(config_path: str, force_rescrape: bool) -> None:
    st.write("**Run output:**")
    log_lines: List[str] = []
    log_placeholder = st.empty()

    try:
        for line in run_stream(config_path, force_rescrape=force_rescrape):
            log_lines.append(line)
            log_placeholder.code("\n".join(log_lines))
    except Exception as exc:  # noqa: BLE001 - surface any failure in the UI, don't crash the app
        st.error(f"Run failed: {exc}")
        return

    st.success(
        "Run finished. Click below to refresh the dashboard tabs above — a new "
        "category tab appears automatically if this was a new vertical."
    )
    if st.button("Refresh dashboard now", key="refresh_after_run"):
        st.rerun()


def _render_run_scraper_section(selected_filename: str) -> None:
    config_path = CONFIG_DIR / selected_filename
    try:
        config = load_config(config_path)
    except SystemExit as exc:
        st.error(f"Could not load config/{selected_filename}: {exc}")
        return

    calls_used = _load_calls_this_month() if DB_PATH.exists() else 0
    force_rescrape = st.checkbox(
        "Force rescrape (ignore previously-scraped sub-areas)",
        key=f"force_rescrape_{selected_filename}",
    )

    near_limit = False
    if config.api_budget:
        max_calls = config.api_budget.max_calls_per_month
        subareas_to_run = _estimate_subareas_to_run(config, force_rescrape)
        projected_min = calls_used + subareas_to_run
        st.write(
            f"API budget: **{calls_used:,} / {max_calls:,}** calls used this month. "
            f"This run will hit at least {subareas_to_run} sub-area(s) "
            f"(1+ call each; each qualifying place adds one more Place Details "
            f"call on top) — projected minimum usage: **{projected_min:,} / {max_calls:,}**."
        )
        warn_threshold = max_calls * config.api_budget.warn_at_percent / 100
        near_limit = projected_min >= warn_threshold
    else:
        st.write(f"API calls this month: {calls_used:,} (no api_budget configured for this config).")

    confirmed = True
    if near_limit:
        st.warning(
            "Projected usage is close to (or over) this config's monthly budget. "
            "Confirm you want to proceed."
        )
        confirmed = st.checkbox("I understand, run anyway", key=f"confirm_run_{selected_filename}")

    if st.button(
        "Run Scraper",
        key=f"run_scraper_{selected_filename}",
        disabled=near_limit and not confirmed,
    ):
        _execute_run(str(config_path), force_rescrape)


def _render_new_config_tab() -> None:
    st.header("Create a new scrape config")

    category = st.text_input(
        "Category", key="new_cfg_category", placeholder='e.g. "dentist", "hardware store"'
    )
    location = st.text_input(
        "Location (city)", key="new_cfg_location", placeholder="e.g. Chhatrapati Sambhaji Nagar"
    )
    sub_areas_raw = st.text_area(
        "Sub-areas (one per line, or comma-separated)", key="new_cfg_subareas"
    )
    radius_meters = st.number_input(
        "Radius (meters)", min_value=100, value=5000, step=500, key="new_cfg_radius"
    )
    require_phone = st.checkbox(
        "Require phone number to qualify", value=True, key="new_cfg_require_phone"
    )

    niche_keywords: Dict[str, List[str]] = {}
    with st.expander("Advanced: niche keywords (optional)"):
        if "niche_row_count" not in st.session_state:
            st.session_state["niche_row_count"] = 1
        for i in range(st.session_state["niche_row_count"]):
            col1, col2 = st.columns([1, 2])
            with col1:
                niche_name = st.text_input(f"Niche name #{i + 1}", key=f"niche_name_{i}")
            with col2:
                niche_kw_raw = st.text_input(
                    f"Keywords #{i + 1} (comma-separated)", key=f"niche_kw_{i}"
                )
            if niche_name.strip() and niche_kw_raw.strip():
                keywords = [k.strip() for k in niche_kw_raw.split(",") if k.strip()]
                if keywords:
                    niche_keywords[niche_name.strip()] = keywords
        if st.button("Add another niche", key="add_niche_row"):
            st.session_state["niche_row_count"] += 1
            st.rerun()

    category_slug = _slugify_category(category)
    filename = f"sources_{category_slug}.yaml" if category_slug else None

    if filename:
        st.caption(f"Will save to: `config/{filename}`")
    else:
        st.caption("Enter a category to see the target filename.")

    if filename and (CONFIG_DIR / filename).exists():
        st.warning(f"config/{filename} already exists.")
        st.checkbox("Overwrite existing config", key="new_cfg_overwrite")

    if st.button("Save Config", key="save_new_config"):
        _handle_save_config(
            category, location, sub_areas_raw, radius_meters, require_phone, niche_keywords
        )

    st.divider()
    st.subheader("Run a saved config")

    all_configs = _list_config_files()
    if not all_configs:
        st.info("No config files found in config/ yet — save one above first.")
        return

    default_index = 0
    last_saved = st.session_state.get("last_saved_config")
    if last_saved and last_saved in all_configs:
        default_index = all_configs.index(last_saved)

    selected_config = st.selectbox(
        "Config to run", all_configs, index=default_index, key="run_config_select"
    )
    if selected_config:
        _render_run_scraper_section(selected_config)


def main() -> None:
    st.set_page_config(page_title="Lead Generation Dashboard", layout="wide")
    st.title("Lead Generation Dashboard")

    db_exists = DB_PATH.exists()
    if db_exists:
        calls_used = _load_calls_this_month()
        budget = _load_monthly_budget()
        st.caption(f"API calls this month: {calls_used:,} / {budget:,}")
    else:
        st.caption("No database yet — create and run a config below to get started.")

    df = _load_leads() if db_exists else pd.DataFrame()

    categories: List[str] = []
    sub_areas: List[str] = []
    segment_tags: List[str] = []
    niche_tags: List[str] = []
    statuses: List[str] = []
    has_website = "All"

    if not df.empty:
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
    else:
        st.info("No leads yet. Use the '+ New Scrape Config' tab below to get started.")

    tabs = st.tabs(categories + ["+ New Scrape Config"])

    for tab, category in zip(tabs[:-1], categories):
        with tab:
            category_df = df[df["category"] == category]
            filtered_df = _apply_filters(
                category_df, sub_areas, segment_tags, niche_tags, statuses, has_website
            )
            _render_category_tab(category, filtered_df)

    with tabs[-1]:
        _render_new_config_tab()


if __name__ == "__main__":
    main()
