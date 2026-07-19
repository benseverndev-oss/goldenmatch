"""Pydantic models for goldencheck.yml configuration."""
from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["GoldenCheckConfig", "ColumnRule", "Settings", "RelationRule", "IgnoreEntry"]

class Settings(BaseModel):
    sample_size: int = Field(
        default=100_000,
        description="Maximum number of rows sampled from the dataset when running checks, capping work on large tables.",
    )
    severity_threshold: str = Field(
        default="warning",
        description="Minimum severity level at which a finding is reported, so anything below this floor is suppressed from results.",
    )
    fail_on: str = Field(
        default="error",
        description="Severity level that causes the overall run to fail once a finding at or above it is produced.",
    )

class ColumnRule(BaseModel):
    type: str = Field(
        description="Expected data type for the column, used to flag values that do not conform to it.",
    )
    required: bool | None = Field(
        default=None,
        description="Whether the column must be present in the dataset, failing the check when it is missing.",
    )
    nullable: bool | None = Field(
        default=None,
        description="Whether null values are permitted in the column, flagging nulls when set to false.",
    )
    format: str | None = Field(
        default=None,
        description="Named format the column values must match, such as an email or date pattern, for validating string shape.",
    )
    unique: bool | None = Field(
        default=None,
        description="Whether every value in the column must be distinct, flagging duplicate values when set to true.",
    )
    range: list[float] | None = Field(
        default=None,
        description="Inclusive lower and upper numeric bounds that column values must fall within.",
    )
    enum: list[str] | None = Field(
        default=None,
        description="Closed set of allowed values for the column, flagging any value outside this list.",
    )
    outlier_stddev: float | None = Field(
        default=None,
        description="Number of standard deviations from the mean beyond which a numeric value is flagged as an outlier.",
    )

class RelationRule(BaseModel):
    type: str = Field(
        description="Kind of cross-column relationship to enforce, for example a uniqueness or foreign-key style constraint.",
    )
    columns: list[str] = Field(
        description="Columns that participate in the relationship, evaluated together as a group.",
    )

class IgnoreEntry(BaseModel):
    column: str = Field(
        description="Column whose check results should be suppressed from the report.",
    )
    check: str = Field(
        description="Specific check on that column to skip, silencing its findings for that column.",
    )

class GoldenCheckConfig(BaseModel):
    version: int = Field(
        default=1,
        description="Schema version of this configuration file, used to handle format changes across releases.",
    )
    settings: Settings = Field(
        default=Settings(),
        description="Global run settings such as sampling size and severity handling that apply to the whole check.",
    )
    columns: dict[str, ColumnRule] = Field(
        default={},
        description="Per-column validation rules keyed by column name.",
    )
    relations: list[RelationRule] = Field(
        default=[],
        description="Cross-column relationship rules applied across multiple columns.",
    )
    ignore: list[IgnoreEntry] = Field(
        default=[],
        description="List of column and check pairs whose findings should be excluded from results.",
    )
