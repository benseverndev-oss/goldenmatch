from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig, MappingSpec
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.mapping.name_similarity import name_similarity
from goldenflow.mapping.profile_similarity import profile_similarity


@dataclass
class ColumnMapping:
    source: str
    target: str
    confidence: float
    transform: str | None = None


class SchemaMapper:
    def __init__(
        self,
        auto_threshold: float = 0.9,
        suggest_threshold: float = 0.6,
    ):
        self.auto_threshold = auto_threshold
        self.suggest_threshold = suggest_threshold

    def map(
        self,
        source_df: pl.DataFrame,
        target_df: pl.DataFrame,
    ) -> list[ColumnMapping]:
        """Auto-map source columns to target columns."""
        source_profile = profile_dataframe(source_df)
        target_profile = profile_dataframe(target_df)

        target_profiles = {cp.name: cp for cp in target_profile.columns}
        source_profiles = {cp.name: cp for cp in source_profile.columns}

        mappings: list[ColumnMapping] = []
        used_targets: set[str] = set()

        for s_col in source_df.columns:
            best_match: ColumnMapping | None = None
            best_score = 0.0

            for t_col in target_df.columns:
                if t_col in used_targets:
                    continue

                # Pass 1: Name similarity
                n_score = name_similarity(s_col, t_col)

                # Pass 2: Profile similarity (when name is ambiguous)
                p_score = 0.0
                if s_col in source_profiles and t_col in target_profiles:
                    p_score = profile_similarity(
                        source_profiles[s_col], target_profiles[t_col]
                    )

                # Combined score: name-weighted
                combined = 0.7 * n_score + 0.3 * p_score

                if combined > best_score and combined >= self.suggest_threshold:
                    best_score = combined
                    best_match = ColumnMapping(
                        source=s_col,
                        target=t_col,
                        confidence=round(combined, 3),
                    )

            if best_match:
                mappings.append(best_match)
                used_targets.add(best_match.target)

        return mappings

    def to_config(self, mappings: list[ColumnMapping]) -> GoldenFlowConfig:
        """Convert mappings to a GoldenFlowConfig with MappingSpecs."""
        mapping_specs = [
            MappingSpec(
                source=m.source,
                target=m.target,
                transform=m.transform,
            )
            for m in mappings
        ]
        return GoldenFlowConfig(mappings=mapping_specs)
