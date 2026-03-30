"""MapEngine — orchestrates schema extraction, scoring, and assignment."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from infermap.assignment import optimal_assign
from infermap.providers import extract_schema
from infermap.scorers import default_scorers
from infermap.scorers.alias import ALIASES, _ALIAS_LOOKUP
from infermap.types import FieldMapping, MapResult, SchemaInfo

logger = logging.getLogger("infermap")

# Minimum number of non-None scorer contributors required for a valid score
_MIN_CONTRIBUTORS = 2


class MapEngine:
    """Orchestrates the full field-mapping pipeline."""

    def __init__(
        self,
        min_confidence: float = 0.3,
        sample_size: int = 500,
        scorers=None,
        config_path: str | None = None,
    ) -> None:
        self.min_confidence = min_confidence
        self.sample_size = sample_size
        self.scorers = scorers if scorers is not None else default_scorers()
        if config_path is not None:
            self._apply_config(config_path)

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _apply_config(self, path: str) -> None:
        """Load YAML config and apply scorer weight overrides / alias additions."""
        cfg_path = Path(path)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}

        # Apply scorer overrides
        scorers_cfg: dict = cfg.get("scorers", {})
        if scorers_cfg:
            new_scorers = []
            for sc in self.scorers:
                sc_cfg = scorers_cfg.get(sc.name, {})
                if sc_cfg.get("enabled") is False:
                    continue
                if "weight" in sc_cfg:
                    sc.weight = float(sc_cfg["weight"])
                new_scorers.append(sc)
            self.scorers = new_scorers

        # Apply alias additions
        aliases_cfg: dict = cfg.get("aliases", {})
        if aliases_cfg:
            for canonical, alias_list in aliases_cfg.items():
                if canonical not in ALIASES:
                    ALIASES[canonical] = []
                for alias in alias_list:
                    alias_lower = alias.strip().lower()
                    if alias_lower not in ALIASES[canonical]:
                        ALIASES[canonical].append(alias_lower)
                    _ALIAS_LOOKUP[alias_lower] = canonical
                _ALIAS_LOOKUP[canonical] = canonical

    # ------------------------------------------------------------------
    # Core mapping
    # ------------------------------------------------------------------

    def map(
        self,
        source: Any,
        target: Any,
        required: list[str] | None = None,
        schema_file: str | None = None,
        **kwargs,
    ) -> MapResult:
        """Map fields from *source* to *target*.

        Parameters
        ----------
        source:
            Source data (CSV path, DataFrame, DB URI, schema YAML, …).
        target:
            Target data — same variety of inputs.
        required:
            Extra required target field names beyond those declared in schema.
        schema_file:
            Path to a schema YAML file whose aliases are merged into target fields.
        **kwargs:
            Forwarded to ``extract_schema``.
        """
        t0 = time.perf_counter()

        # 1. Extract schemas
        src_schema: SchemaInfo = extract_schema(source, sample_size=self.sample_size, **kwargs)
        tgt_schema: SchemaInfo = extract_schema(target, sample_size=self.sample_size, **kwargs)

        # 2. Merge required fields
        required_set: set[str] = set(tgt_schema.required_fields)
        if required:
            required_set.update(required)

        # 3. Merge schema_file aliases into target fields
        if schema_file is not None:
            sf_schema: SchemaInfo = extract_schema(schema_file, **kwargs)
            sf_by_name = {f.name: f for f in sf_schema.fields}
            for tgt_field in tgt_schema.fields:
                if tgt_field.name in sf_by_name:
                    sf_field = sf_by_name[tgt_field.name]
                    extra_aliases = sf_field.metadata.get("aliases", [])
                    existing = tgt_field.metadata.get("aliases", [])
                    merged = list(dict.fromkeys(existing + extra_aliases))
                    if merged:
                        tgt_field.metadata["aliases"] = merged
            # Also propagate required from schema_file
            required_set.update(sf_schema.required_fields)

        # 4. Build M x N score matrix
        src_fields = src_schema.fields
        tgt_fields = tgt_schema.fields
        M = len(src_fields)
        N = len(tgt_fields)

        score_matrix = np.zeros((M, N), dtype=float)
        # breakdown[i][j] = dict of scorer_name -> ScorerResult
        breakdown_matrix: list[list[dict]] = [[{} for _ in range(N)] for _ in range(M)]

        for i, src_field in enumerate(src_fields):
            for j, tgt_field in enumerate(tgt_fields):
                results: dict[str, Any] = {}
                for sc in self.scorers:
                    try:
                        result = sc.score(src_field, tgt_field)
                    except Exception as exc:
                        logger.warning(
                            "Scorer %s raised an exception for (%s, %s): %s",
                            sc.name,
                            src_field.name,
                            tgt_field.name,
                            exc,
                        )
                        result = None
                    if result is not None:
                        results[sc.name] = (result, sc.weight)

                # 5. Score combination: weighted average, min 2 contributors
                if len(results) < _MIN_CONTRIBUTORS:
                    combined = 0.0
                else:
                    total_weight = sum(w for _, (_, w) in enumerate(results.values()) if True)
                    # rebuild properly
                    total_weight = sum(w for (_, w) in results.values())
                    weighted_sum = sum(r.score * w for (r, w) in results.values())
                    combined = weighted_sum / total_weight if total_weight > 0 else 0.0

                score_matrix[i, j] = combined
                breakdown_matrix[i][j] = {name: r for name, (r, _) in results.items()}

        # 6. Optimal assignment
        assignments = optimal_assign(score_matrix, self.min_confidence)

        # 7. Build MapResult
        assigned_src = {r for r, _, _ in assignments}
        assigned_tgt = {c for _, c, _ in assignments}

        mappings: list[FieldMapping] = []
        for r, c, score in assignments:
            src_f = src_fields[r]
            tgt_f = tgt_fields[c]
            bd = breakdown_matrix[r][c]
            reasoning_parts = [f"{name}: {res.reasoning}" for name, res in bd.items()]
            reasoning = "; ".join(reasoning_parts)
            mappings.append(
                FieldMapping(
                    source=src_f.name,
                    target=tgt_f.name,
                    confidence=score,
                    breakdown=bd,
                    reasoning=reasoning,
                )
            )

        unmapped_source = [src_fields[i].name for i in range(M) if i not in assigned_src]
        unmapped_target = [tgt_fields[j].name for j in range(N) if j not in assigned_tgt]

        # 8. Warnings for required unmapped target fields
        warnings: list[str] = []
        mapped_targets = {m.target for m in mappings}
        for req_field in required_set:
            if req_field not in mapped_targets:
                # Find best candidate from score matrix
                tgt_idx = next(
                    (j for j, tf in enumerate(tgt_fields) if tf.name == req_field), None
                )
                best_candidate = None
                best_score = 0.0
                if tgt_idx is not None:
                    col_scores = score_matrix[:, tgt_idx]
                    best_src_idx = int(np.argmax(col_scores))
                    best_score = float(col_scores[best_src_idx])
                    if best_score > 0.0:
                        best_candidate = src_fields[best_src_idx].name

                if best_candidate:
                    warnings.append(
                        f"Required target field '{req_field}' is unmapped. "
                        f"Best candidate: '{best_candidate}' (score={best_score:.3f})"
                    )
                else:
                    warnings.append(
                        f"Required target field '{req_field}' is unmapped and no candidate found."
                    )

        # 9. Metadata with timing
        elapsed = time.perf_counter() - t0
        metadata = {
            "elapsed_seconds": round(elapsed, 4),
            "source_field_count": M,
            "target_field_count": N,
            "mapping_count": len(mappings),
            "min_confidence": self.min_confidence,
        }

        return MapResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
            warnings=warnings,
            metadata=metadata,
        )
