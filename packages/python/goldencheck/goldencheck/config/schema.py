"""Pydantic models for goldencheck.yml configuration."""
from __future__ import annotations
from pydantic import BaseModel

class Settings(BaseModel):
    sample_size: int = 100_000
    severity_threshold: str = "warning"
    fail_on: str = "error"

class ColumnRule(BaseModel):
    type: str
    required: bool | None = None
    nullable: bool | None = None
    format: str | None = None
    unique: bool | None = None
    range: list[float] | None = None
    enum: list[str] | None = None
    outlier_stddev: float | None = None

class RelationRule(BaseModel):
    type: str
    columns: list[str]

class IgnoreEntry(BaseModel):
    column: str
    check: str

class GoldenCheckConfig(BaseModel):
    version: int = 1
    settings: Settings = Settings()
    columns: dict[str, ColumnRule] = {}
    relations: list[RelationRule] = []
    ignore: list[IgnoreEntry] = []
