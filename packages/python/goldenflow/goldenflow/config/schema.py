from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TransformSpec(BaseModel):
    column: str = Field(description="Name of the column the transform operations are applied to.")
    ops: list[str] = Field(
        description="Ordered list of transform-op names applied in sequence to the column."
    )


class SplitSpec(BaseModel):
    source: str = Field(description="Name of the column whose values are split into multiple columns.")
    target: list[str] = Field(description="Names of the output columns produced by the split.")
    method: str = Field(description="Splitting method that determines how the source value is divided.")


class FilterSpec(BaseModel):
    column: str = Field(description="Name of the column the filter condition is evaluated against.")
    condition: str = Field(description="Predicate expression that rows must satisfy to be kept.")


class DedupSpec(BaseModel):
    columns: list[str] = Field(
        description="Columns whose combined values define what counts as a duplicate row."
    )
    keep: Literal["first", "last"] = Field(
        default="first",
        description="Which duplicate to retain within each group, either the first or last occurrence.",
    )


class MappingSpec(BaseModel):
    source: str = Field(description="Name of the source column to read values from.")
    target: str | list[str] = Field(
        description="Name or names of the target column(s) the source values are written to."
    )
    transform: str | list[str] | None = Field(
        default=None,
        description="Optional transform op or ordered list of ops applied while mapping source to target.",
    )


class GoldenFlowConfig(BaseModel):
    source: str | None = Field(
        default=None, description="Path or identifier of the input dataset to read records from."
    )
    output: str | None = Field(
        default=None, description="Path or identifier where the transformed dataset is written."
    )
    transforms: list[TransformSpec] = Field(
        default_factory=list,
        description="Per-column transform specs that standardize values by applying ordered ops.",
    )
    splits: list[SplitSpec] = Field(
        default_factory=list,
        description="Split specs that break one column into several target columns.",
    )
    renames: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of existing column names to new names to rename them to.",
    )
    drop: list[str] = Field(
        default_factory=list,
        description="Names of columns to remove from the output.",
    )
    filters: list[FilterSpec] = Field(
        default_factory=list,
        description="Filter specs that keep only rows satisfying each condition.",
    )
    dedup: DedupSpec | None = Field(
        default=None,
        description="Optional deduplication spec that collapses duplicate rows by key columns.",
    )
    mappings: list[MappingSpec] = Field(
        default_factory=list,
        description="Mapping specs that copy source columns to targets with optional transforms.",
    )
