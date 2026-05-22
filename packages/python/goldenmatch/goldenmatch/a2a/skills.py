"""Skill dispatch for the A2A protocol server.

Routes incoming skill requests to the appropriate AgentSession methods
or GoldenMatch API functions.
"""

from __future__ import annotations

from typing import Any

import yaml

from goldenmatch.core.agent import (
    AgentSession,
    _decision_to_config,
    profile_for_agent,
    select_strategy,
)


def dispatch_skill(skill_id: str, params: dict) -> dict:
    """Dispatch an A2A skill request to the appropriate handler.

    Parameters
    ----------
    skill_id : str
        One of: analyze_data, configure, deduplicate, match, explain,
        review, compare_strategies, pprl, quality, transform.
    params : dict
        Skill-specific parameters.

    Returns
    -------
    dict
        Result payload.

    Raises
    ------
    ValueError
        If *skill_id* is not recognised.
    """
    session = AgentSession()

    if skill_id == "analyze_data":
        return session.analyze(params["file_path"])

    if skill_id == "autoconfig":
        # v1.7-v1.12: AutoConfigController via AgentSession. Returns committed
        # config + telemetry blob (stop_reason, health, decisions, NE fields).
        excl = params.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            return session.autoconfigure(params["file_path"])
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)

    if skill_id == "controller_telemetry":
        # Stateless A2A dispatch (one AgentSession per request) means we
        # can't recall telemetry from a prior request. Mirrors MCP's note —
        # callers should use the inline telemetry returned by autoconfig /
        # deduplicate / match instead.
        return {
            "available": False,
            "note": (
                "controller_telemetry is per-session and A2A dispatch is "
                "stateless. Telemetry is returned inline by autoconfig, "
                "deduplicate, and match skills."
            ),
        }

    if skill_id == "deduplicate":
        excl = params.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            result = session.deduplicate(
                params["file_path"],
                config=params.get("config"),
            )
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        serialised = _serialise_result(result)
        # Plumb telemetry onto the wire result so callers see stop_reason /
        # decisions / NE alongside the dedupe output, not as a separate call.
        serialised["telemetry"] = session.last_telemetry or {"available": False, "source": None}
        return serialised

    if skill_id == "match":
        excl = params.get("exclude_columns") or None
        _excl_token = None
        if excl:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(excl))
        try:
            result = session.match_sources(
                params["file_a"],
                params["file_b"],
                config=params.get("config"),
            )
        finally:
            if _excl_token is not None:
                from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        serialised = _serialise_result(result)
        serialised["telemetry"] = session.last_telemetry or {"available": False, "source": None}
        return serialised

    if skill_id == "compare_strategies":
        return session.compare_strategies(
            params["file_path"],
            ground_truth=params.get("ground_truth"),
        )

    if skill_id == "explain":
        import polars as pl

        from goldenmatch import explain_pair_df

        record_a = pl.DataFrame([params["record_a"]])
        record_b = pl.DataFrame([params["record_b"]])
        mk_cfg = params["matchkey"]
        explanation = explain_pair_df(record_a, record_b, mk_cfg)
        return {"explanation": explanation}

    if skill_id == "review":
        from goldenmatch.core.review_queue import ReviewQueue

        queue = ReviewQueue(backend="memory")
        return {"pending": queue.list_pending()}

    if skill_id == "configure":
        import polars as pl

        _analysis = session.analyze(params["file_path"])
        decision = select_strategy(
            profile_for_agent(
                pl.read_csv(params["file_path"], encoding="utf8-lossy", ignore_errors=True)
            )
        )
        cfg = _decision_to_config(decision)
        return {"config_yaml": yaml.dump(cfg.model_dump(), default_flow_style=False)}

    if skill_id == "pprl":
        from goldenmatch import pprl_link

        result = pprl_link(
            params["file_a"],
            params["file_b"],
            fields=params.get("fields", []),
        )
        return _serialise_result({"result": result})

    if skill_id == "quality":
        import polars as pl

        from goldenmatch.config.schemas import QualityConfig
        from goldenmatch.core.quality import _goldencheck_available, run_quality_check

        if not _goldencheck_available():
            return {"error": "goldencheck not installed. pip install goldenmatch[quality]"}

        file_path = params.get("file_path")
        if not file_path:
            return {"error": "Missing required parameter: file_path"}

        try:
            df = pl.read_csv(file_path, encoding="utf8-lossy", ignore_errors=True)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as exc:
            return {"error": f"Could not read CSV '{file_path}': {exc}"}

        fix_mode = params.get("fix_mode", "safe")
        domain = params.get("domain")
        qc = QualityConfig(mode="silent", fix_mode=fix_mode, domain=domain)
        fixed_df, fixes = run_quality_check(df, qc)

        output_path = params.get("output_path")
        write_error = None
        if output_path:
            try:
                fixed_df.write_csv(output_path)
            except Exception as exc:
                write_error = f"Results computed but failed to write to '{output_path}': {exc}"
                output_path = None

        result = {
            "total_records": fixed_df.height,
            "fixes_applied": len(fixes),
            "fixes": fixes,
            "output_path": output_path,
        }
        if write_error:
            result["write_error"] = write_error
        return result

    if skill_id == "transform":
        import polars as pl

        from goldenmatch.config.schemas import TransformConfig
        from goldenmatch.core.transform import _goldenflow_available, run_transform

        if not _goldenflow_available():
            return {"error": "goldenflow not installed. pip install goldenmatch[transform]"}

        file_path = params.get("file_path")
        if not file_path:
            return {"error": "Missing required parameter: file_path"}

        try:
            df = pl.read_csv(file_path, encoding="utf8-lossy", ignore_errors=True)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as exc:
            return {"error": f"Could not read CSV '{file_path}': {exc}"}

        tc = TransformConfig(mode="silent")
        transformed_df, fixes = run_transform(df, tc, strict=True)

        output_path = params.get("output_path")
        write_error = None
        if output_path:
            try:
                transformed_df.write_csv(output_path)
            except Exception as exc:
                write_error = f"Results computed but failed to write to '{output_path}': {exc}"
                output_path = None

        result = {
            "total_records": transformed_df.height,
            "transforms_applied": len(fixes),
            "transforms": fixes,
            "output_path": output_path,
        }
        if write_error:
            result["write_error"] = write_error
        return result

    if skill_id in {
        "identity_resolve", "identity_list", "identity_history",
        "identity_conflicts", "identity_merge", "identity_split",
    }:
        from goldenmatch.mcp.identity_tools import _dispatch as _identity_dispatch
        # Reuse MCP dispatch since the contract is identical (JSON in/out).
        return _identity_dispatch(skill_id, params)

    if skill_id == "add_correction":
        # v1.19.x Phase 3 (#437 surface sync). Pair-level OR field-level.
        # Inlined dispatch (rather than delegating to MCP) so this branch is
        # independent of the Phase 1 PR landing order.
        import uuid as _uuid
        from datetime import datetime as _dt

        from goldenmatch.core.memory.store import (
            Correction as _CorrectionDC,
        )
        from goldenmatch.core.memory.store import (
            MemoryStore as _MemoryStore,
        )
        from goldenmatch.core.memory.store import (
            _canon_pair,
        )

        dataset = params.get("dataset")
        if not dataset:
            return {"error": "dataset is required"}
        decision = params.get("decision")
        if decision not in ("approve", "reject", "field_correct"):
            return {"error": f"Invalid decision: {decision!r}"}
        path = params.get("path") or ".goldenmatch/memory.db"

        if decision == "field_correct":
            field_name = params.get("field_name")
            corrected_value = params.get("corrected_value")
            if not field_name:
                return {"error": "field_correct requires field_name"}
            if corrected_value is None:
                return {"error": "field_correct requires corrected_value"}
            cid = params.get("cluster_id", params.get("id_a", 0))
            try:
                cid = int(cid)
            except (TypeError, ValueError) as exc:
                return {"error": f"cluster_id must be an integer: {exc}"}
            correction = _CorrectionDC(
                id=str(_uuid.uuid4()),
                id_a=cid,
                id_b=0,
                decision=decision,
                source="agent",
                trust=0.5,
                field_hash="",
                record_hash="",
                original_score=0.0,
                matchkey_name=params.get("matchkey_name"),
                reason=params.get("reason"),
                dataset=dataset,
                created_at=_dt.now(),
                field_name=field_name,
                original_value=params.get("original_value"),
                corrected_value=corrected_value,
            )
        else:
            try:
                id_a = int(params["id_a"])
                id_b = int(params["id_b"])
            except (KeyError, TypeError, ValueError) as exc:
                return {"error": f"id_a / id_b must be integers: {exc}"}
            ca, cb = _canon_pair(id_a, id_b)
            correction = _CorrectionDC(
                id=str(_uuid.uuid4()),
                id_a=ca,
                id_b=cb,
                decision=decision,
                source="agent",
                trust=0.5,
                field_hash="",
                record_hash="",
                original_score=0.0,
                matchkey_name=params.get("matchkey_name"),
                reason=params.get("reason"),
                dataset=dataset,
                created_at=_dt.now(),
            )

        with _MemoryStore(backend="sqlite", path=path) as store:
            store.add_correction(correction)
        result: dict = {
            "status": "ok",
            "id": correction.id,
            "decision": decision,
            "source": "agent",
            "trust": 0.5,
            "dataset": dataset,
        }
        if decision == "field_correct":
            result["cluster_id"] = correction.id_a
            result["field_name"] = correction.field_name
            result["corrected_value"] = correction.corrected_value
        else:
            result["id_a"] = correction.id_a
            result["id_b"] = correction.id_b
        return result

    raise ValueError(f"Unknown skill: {skill_id}")


def _serialise_result(obj: Any) -> dict:
    """Best-effort serialisation of pipeline results to JSON-safe dict."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            try:
                import polars as pl

                if isinstance(v, pl.DataFrame):
                    out[k] = {"rows": v.height, "columns": v.columns}
                    continue
            except ImportError:
                pass
            if isinstance(v, dict):
                out[k] = _serialise_result(v)
            elif isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
            elif isinstance(v, list):
                out[k] = str(v)[:500]
            else:
                out[k] = str(v)[:500]
        return out
    return {"value": str(obj)[:500]}
