"""Load and validate config/sources.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError


class EnrichmentConfig(BaseModel):
    enabled: bool = False
    source: Literal["justdial", "indiamart"] = "justdial"
    max_leads_per_run: int = 50


class ApiBudgetConfig(BaseModel):
    max_calls_per_month: int = 8000
    warn_at_percent: int = Field(80, ge=1, le=100)


class SourceConfig(BaseModel):
    category: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    sub_areas: List[str] = Field(..., min_length=1)
    radius_meters: int = 5000
    require_phone: bool = True
    enrichment: Optional[EnrichmentConfig] = None
    api_budget: Optional[ApiBudgetConfig] = None
    niche_keywords: Optional[Dict[str, List[str]]] = None


def load_config(path: str | Path) -> SourceConfig:
    """Load and validate a sources.yaml file. Raises SystemExit on failure."""
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    try:
        return SourceConfig(**raw)
    except ValidationError as e:
        raise SystemExit(f"Invalid config in {config_path}:\n{e}") from e
