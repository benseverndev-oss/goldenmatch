from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TransformSpec(BaseModel):
    column: str
    ops: list[str]


class SplitSpec(BaseModel):
    source: str
    target: list[str]
    method: str


class FilterSpec(BaseModel):
    column: str
    condition: str


class DedupSpec(BaseModel):
    columns: list[str]
    keep: Literal["first", "last"] = "first"


class MappingSpec(BaseModel):
    source: str
    target: str | list[str]
    transform: str | list[str] | None = None


class GoldenFlowConfig(BaseModel):
    source: str | None = None
    output: str | None = None
    transforms: list[TransformSpec] = []
    splits: list[SplitSpec] = []
    renames: dict[str, str] = {}
    drop: list[str] = []
    filters: list[FilterSpec] = []
    dedup: DedupSpec | None = None
    mappings: list[MappingSpec] = []
