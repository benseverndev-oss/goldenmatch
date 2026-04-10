"""MapEngine — orchestrates schema extraction, scoring, and assignment."""
from __future__ import annotations

import copy
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from infermap.assignment import optimal_assign
from infermap.dictionaries import merge_domains
from infermap.providers import extract_schema
from infermap.scorers import default_scorers
from infermap.scorers.alias import ALIASES, _ALIAS_LOOKUP, AliasScorer
from infermap.calibration import Calibrator
from infermap.types import FieldMapping, MapResult, SchemaInfo

logger = logging.getLogger("infermap")

# Minimum number of non-None scorer contributors required for a valid score
_MIN_CONTRIBUTORS = 2


_DELIMITERS = ("_", "-", ".", " ")


def _common_affix_tokens(names: list[str], *, at_start: bool) -> str:
    """Find a common delimiter-bounded affix across *names*, else "".

    The affix must end (for prefix) or start (for suffix) at a delimiter
    boundary so we don't over-strip shared substrings like "em" from
    ["email", "employee"]. At least 2 names required; affix must be >= 2 chars.
    """
    if len(names) < 2:
        return ""
    # Find raw common substring at the chosen edge.
    if at_start:
        shortest = min(len(n) for n in names)
        i = 0
        while i < shortest and all(n[i] == names[0][i] for n in names):
            i += 1
        candidate = names[0][:i]
    else:
        shortest = min(len(n) for n in names)
        i = 0
        while i < shortest and all(n[-1 - i] == names[0][-1 - i] for n in names):
            i += 1
        candidate = names[0][-i:] if i > 0 else ""

    if len(candidate) < 2:
        return ""
    # Truncate candidate to the last delimiter boundary (prefix) or first
    # delimiter boundary (suffix) so we only strip whole tokens.
    if at_start:
        for pos in range(len(candidate) - 1, -1, -1):
            if candidate[pos] in _DELIMITERS:
                return candidate[: pos + 1]
        return ""
    else:
        for pos in range(len(candidate)):
            if candidate[pos] in _DELIMITERS:
                return candidate[pos:]
        return ""


def _default_scorers_with_domains(domains: list[str]) -> list:
    """Build the default scorer list with an AliasScorer that knows about
    the requested domains in addition to the generic defaults.

    `generic` is always included (prepended if the caller didn't ask for
    it) so users who pass `domains=["healthcare"]` still get common PII
    aliases like `email`/`phone`.
    """
    ordered: list[str] = []
    if "generic" not in domains:
        ordered.append("generic")
    ordered.extend(domains)
    merged_aliases = merge_domains(ordered)
    # Build the default list, then swap out the AliasScorer for one that
    # holds the merged dict. Keeps ordering stable with default_scorers().
    scorers = default_scorers()
    for i, sc in enumerate(scorers):
        if isinstance(sc, AliasScorer):
            scorers[i] = AliasScorer(aliases=merged_aliases)
            break
    return scorers


def _populate_canonical_names(schema: SchemaInfo) -> None:
    """Strip the common prefix + suffix (if any) from each field name and
    populate ``FieldInfo.canonical_name``. Mutates the schema in place.

    If stripping would leave a name empty (e.g. all fields are literally the
    prefix), canonical_name falls back to the original name.
    """
    names = [f.name for f in schema.fields]
    prefix = _common_affix_tokens(names, at_start=True)
    suffix = _common_affix_tokens(names, at_start=False)
    for f in schema.fields:
        canonical = f.name
        if prefix and canonical.startswith(prefix):
            canonical = canonical[len(prefix):]
        if suffix and canonical.endswith(suffix):
            canonical = canonical[: -len(suffix)]
        f.canonical_name = canonical if canonical else f.name


class MapEngine:
    """Orchestrates the full field-mapping pipeline."""

    def __init__(
        self,
        min_confidence: float = 0.2,
        sample_size: int = 500,
        scorers=None,
        config_path: str | None = None,
        return_score_matrix: bool = False,
        calibrator: Calibrator | None = None,
        domains: list[str] | None = None,
    ) -> None:
        self.min_confidence = min_confidence
        self.sample_size = sample_size
        # Domain dictionaries: when set, build a per-engine AliasScorer whose
        # lookup merges `generic` plus the requested domains. When None
        # (default), scorers use the module-level ALIASES so the existing
        # infermap.yaml-based alias extension path still works.
        self.domains = domains
        if scorers is not None:
            self.scorers = scorers
        elif domains is not None:
            self.scorers = _default_scorers_with_domains(domains)
        else:
            self.scorers = default_scorers()
        self.return_score_matrix = return_score_matrix
        # Optional post-assignment confidence calibrator. Applied AFTER
        # optimal_assign has picked mappings, so it never changes which
        # mappings are chosen — only the confidence attached to each.
        # `min_confidence` filtering happens during assignment on RAW scores;
        # calibration is about user-facing trust, not assignment behavior.
        self.calibrator = calibrator
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

        # Apply domain dictionaries from config (additive: merges into the
        # current AliasScorer's alias dict, or builds a per-engine one if
        # the default is still in use).
        domains_cfg = cfg.get("domains")
        if domains_cfg:
            if not isinstance(domains_cfg, list):
                raise ValueError("`domains` in config must be a list of strings")
            self.domains = list(domains_cfg)
            self.scorers = _default_scorers_with_domains(self.domains)

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
        # 1. Extract schemas
        src_schema: SchemaInfo = extract_schema(source, sample_size=self.sample_size, **kwargs)
        tgt_schema: SchemaInfo = extract_schema(target, sample_size=self.sample_size, **kwargs)

        # 2. Optional schema_file aliases (only applicable when paths are provided;
        # callers who already have SchemaInfo use map_schemas directly).
        sf_schema: SchemaInfo | None = None
        if schema_file is not None:
            sf_schema = extract_schema(schema_file, **kwargs)

        return self.map_schemas(
            src_schema,
            tgt_schema,
            required=required,
            schema_file_schema=sf_schema,
        )

    def map_schemas(
        self,
        src_schema: SchemaInfo,
        tgt_schema: SchemaInfo,
        required: list[str] | None = None,
        schema_file_schema: SchemaInfo | None = None,
    ) -> MapResult:
        """Map pre-extracted source and target schemas.

        Mirrors TypeScript's ``MapEngine.mapSchemas``. Use this when you already
        have ``SchemaInfo`` objects and don't need extraction. The benchmark runner
        uses this path to avoid round-tripping through ``extract_schema`` (which
        would re-infer dtypes from sample strings and cause drift between runners).
        """
        t0 = time.perf_counter()

        # 1. Merge required fields
        required_set: set[str] = set(tgt_schema.required_fields)
        if required:
            required_set.update(required)

        # 2. Merge schema_file aliases into target fields.
        # Deep-copy tgt_schema first so we don't mutate the caller's object
        # (we write into tgt_field.metadata["aliases"]).
        if schema_file_schema is not None:
            tgt_schema = copy.deepcopy(tgt_schema)
            sf_by_name = {f.name: f for f in schema_file_schema.fields}
            for tgt_field in tgt_schema.fields:
                if tgt_field.name in sf_by_name:
                    sf_field = sf_by_name[tgt_field.name]
                    extra_aliases = sf_field.metadata.get("aliases", [])
                    existing = tgt_field.metadata.get("aliases", [])
                    merged = list(dict.fromkeys(existing + extra_aliases))
                    if merged:
                        tgt_field.metadata["aliases"] = merged
            # Also propagate required from schema_file
            required_set.update(schema_file_schema.required_fields)

        # 2b. Populate canonical_name on each field (affix-stripped). Mutates
        # the schemas, so deep-copy first if we haven't already.
        if schema_file_schema is None:
            src_schema = copy.deepcopy(src_schema)
            tgt_schema = copy.deepcopy(tgt_schema)
        else:
            src_schema = copy.deepcopy(src_schema)
        _populate_canonical_names(src_schema)
        _populate_canonical_names(tgt_schema)

        # 3. Build M x N score matrix
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

                # 4. Score combination: weighted average, min 2 contributors
                if len(results) < _MIN_CONTRIBUTORS:
                    combined = 0.0
                else:
                    total_weight = sum(w for (_, w) in results.values())
                    weighted_sum = sum(r.score * w for (r, w) in results.values())
                    combined = weighted_sum / total_weight if total_weight > 0 else 0.0

                score_matrix[i, j] = combined
                breakdown_matrix[i][j] = {name: r for name, (r, _) in results.items()}

        # Optional: expose the full score matrix for MRR computation in the benchmark
        score_matrix_dict: dict[str, dict[str, float]] | None = None
        if self.return_score_matrix:
            score_matrix_dict = {
                src_fields[i].name: {
                    tgt_fields[j].name: float(score_matrix[i, j])
                    for j in range(N)
                }
                for i in range(M)
            }

        # 5. Optimal assignment
        assignments = optimal_assign(score_matrix, self.min_confidence)

        # 6. Build MapResult
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

        # 5b. Apply post-assignment calibration (opt-in).
        if self.calibrator is not None and mappings:
            raw = np.array([m.confidence for m in mappings], dtype=float)
            cal = self.calibrator.transform(raw)
            for m, c in zip(mappings, cal):
                m.confidence = float(c)

        unmapped_source = [src_fields[i].name for i in range(M) if i not in assigned_src]
        unmapped_target = [tgt_fields[j].name for j in range(N) if j not in assigned_tgt]

        # 7. Warnings for required unmapped target fields
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

        # 8. Metadata with timing
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
            score_matrix=score_matrix_dict,
        )
