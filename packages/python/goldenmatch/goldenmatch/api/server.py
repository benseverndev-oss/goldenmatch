"""GoldenMatch REST API server — local HTTP server for real-time matching.

Usage:
    goldenmatch serve --file customers.csv --config config.yaml --port 8080

Endpoints:
    GET  /health              Health check
    GET  /stats               Current dataset stats
    POST /match               Match a single record against loaded data
    POST /match/batch         Match multiple records
    POST /explain             Explain why two records match
    GET  /clusters            List all clusters
    GET  /clusters/<id>       Get cluster detail
    GET  /reviews             Review queue (borderline pairs for steward review)
    GET  /reviews/decisions   List completed review decisions
    POST /reviews/decide      Approve or reject a pair (steward action)
    POST /shatter             Break a cluster into singletons
    POST /unmerge             Pull a record out of its cluster
    POST /certify-recall      Estimate match recall without ground truth
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)


class MatchServer:
    """Holds the loaded data and engine for the API."""

    def __init__(self, engine, config: GoldenMatchConfig):
        self.engine = engine
        self.config = config
        self.result = None
        self._rows: list[dict] = []
        self._id_to_idx: dict[int, int] = {}
        self._review_queue: list[dict] = []  # pending reviews
        self._review_decisions: list[dict] = []  # completed reviews
        # Optional learning memory plumbing — when set, /reviews/decide writes a
        # Correction with empty hashes (REST receives raw decisions, no df).
        self._memory_store: Any = None
        self._memory_dataset: str | None = None
        # v1.7-v1.12: last AutoConfigController telemetry blob, populated by
        # POST /autoconfig. None before the first call.
        self._last_telemetry: dict | None = None

    def initialize(self) -> None:
        """Run initial matching and cache results."""
        self.result = self.engine.run_full(self.config)
        self._rows = self.engine.data.to_dicts()
        self._id_to_idx = {
            row["__row_id__"]: i for i, row in enumerate(self._rows)
        }
        logger.info(
            "Server initialized: %d records, %d clusters",
            len(self._rows),
            len([c for c in self.result.clusters.values() if c["size"] > 1]),
        )

    def get_stats(self) -> dict:
        if not self.result:
            return {"status": "not_initialized"}
        s = self.result.stats
        return {
            "total_records": s.total_records,
            "total_clusters": s.total_clusters,
            "singleton_count": s.singleton_count,
            "match_rate": round(s.match_rate, 2),
            "avg_cluster_size": round(s.avg_cluster_size, 2),
            "max_cluster_size": s.max_cluster_size,
        }

    def match_record(self, record: dict, top_k: int = 5) -> list[dict]:
        """Match a single record against loaded data."""
        from goldenmatch.core.explainer import explain_pair

        matchkeys = self.config.get_matchkeys()
        results = []

        for mk in matchkeys:
            if mk.type != "weighted":
                continue
            for row in self._rows:
                exp = explain_pair(record, row, mk.fields, mk.threshold or 0.80)
                if exp.is_match:
                    clean_row = {k: v for k, v in row.items() if not k.startswith("__")}
                    results.append({
                        "record": clean_row,
                        "score": round(exp.total_score, 4),
                        "row_id": row.get("__row_id__"),
                    })

        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def explain_pair(self, record_a: dict, record_b: dict) -> dict:
        """Explain why two records match."""
        from goldenmatch.core.explainer import explain_pair as _explain

        matchkeys = self.config.get_matchkeys()
        fields = []
        threshold = 0.80
        for mk in matchkeys:
            if mk.type == "weighted":
                fields = mk.fields
                threshold = mk.threshold or 0.80
                break

        exp = _explain(record_a, record_b, fields, threshold)
        return {
            "total_score": round(exp.total_score, 4),
            "threshold": exp.threshold,
            "is_match": exp.is_match,
            "top_contributor": exp.top_contributor,
            "weakest_field": exp.weakest_field,
            "fields": [
                {
                    "field": f.field_name,
                    "scorer": f.scorer,
                    "value_a": f.value_a,
                    "value_b": f.value_b,
                    "score": round(f.score, 4),
                    "weight": f.weight,
                    "contribution": round(f.contribution, 4),
                    "diff_type": f.diff_type,
                }
                for f in exp.fields
            ],
        }

    def get_clusters(self, min_size: int = 2) -> list[dict]:
        """List clusters."""
        if not self.result:
            return []
        clusters = []
        for cid, info in self.result.clusters.items():
            if info["size"] >= min_size:
                clusters.append({
                    "cluster_id": cid,
                    "size": info["size"],
                    "oversized": info.get("oversized", False),
                })
        clusters.sort(key=lambda x: -x["size"])
        return clusters

    def get_cluster_detail(self, cluster_id: int) -> dict | None:
        """Get cluster members."""
        if not self.result:
            return None
        info = self.result.clusters.get(cluster_id)
        if not info:
            return None

        members = []
        for mid in info["members"]:
            idx = self._id_to_idx.get(mid)
            if idx is not None:
                clean = {k: v for k, v in self._rows[idx].items() if not k.startswith("__")}
                members.append({"row_id": mid, **clean})

        return {
            "cluster_id": cluster_id,
            "size": info["size"],
            "members": members,
        }

    def build_review_queue(self, band_lo: float = 0.70, band_hi: float = 0.90, limit: int = 50) -> list[dict]:
        """Build review queue from borderline pairs."""
        from goldenmatch.core.explainer import explain_pair

        if not self.result:
            return []

        matchkeys = self.config.get_matchkeys()
        fields = []
        threshold = 0.80
        for mk in matchkeys:
            if mk.type == "weighted":
                fields = mk.fields
                threshold = mk.threshold or 0.80
                break

        queue = []
        for a, b, score in self.result.scored_pairs:
            if band_lo <= score <= band_hi:
                idx_a = self._id_to_idx.get(a)
                idx_b = self._id_to_idx.get(b)
                if idx_a is None or idx_b is None:
                    continue

                row_a = {k: v for k, v in self._rows[idx_a].items() if not k.startswith("__")}
                row_b = {k: v for k, v in self._rows[idx_b].items() if not k.startswith("__")}

                exp = explain_pair(self._rows[idx_a], self._rows[idx_b], fields, threshold)

                queue.append({
                    "pair_id": f"{a}_{b}",
                    "row_id_a": a,
                    "row_id_b": b,
                    "score": round(score, 4),
                    "is_match": exp.is_match,
                    "record_a": row_a,
                    "record_b": row_b,
                    "top_contributor": exp.top_contributor,
                    "weakest_field": exp.weakest_field,
                    "status": "pending",
                })

        queue.sort(key=lambda x: abs(x["score"] - threshold))
        self._review_queue = queue[:limit]
        return self._review_queue

    def review_decision(self, pair_id: str, decision: str, reviewer: str = "api") -> dict:
        """Record a review decision (approve/reject)."""
        from datetime import datetime

        for item in self._review_queue:
            if item["pair_id"] == pair_id:
                item["status"] = decision
                record = {
                    **item,
                    "reviewed_by": reviewer,
                    "reviewed_at": datetime.now().isoformat(),
                }
                self._review_decisions.append(record)

                # Apply decision: unmerge if rejected
                if decision == "reject":
                    self.engine.unmerge_record(item["row_id_a"])
                    self.result = self.engine._last_result

                self._record_memory_correction(item, decision)

                return {"status": "recorded", "pair_id": pair_id, "decision": decision}

        return {"error": f"Pair {pair_id} not found in review queue"}

    def shatter_cluster(self, cluster_id: int) -> dict:
        """Break a cluster into singletons (mirrors the MCP shatter_cluster tool)."""
        if not self.result:
            return {"error": "Server not initialized"}
        info = self.result.clusters.get(cluster_id)
        if info is None:
            return {"error": f"Cluster {cluster_id} not found"}
        member_count = info["size"]
        updated = self.engine.unmerge_cluster(cluster_id)
        if updated is None:
            return {"error": "No matching results. Run matching first."}
        self.result = updated
        return {
            "status": "shattered",
            "cluster_id": cluster_id,
            "records_freed": member_count,
            "total_clusters": self.result.stats.total_clusters,
        }

    def unmerge_record_op(self, record_id: int, threshold: float = 0.0) -> dict:
        """Pull a record out of its cluster into a singleton (re-clusters the rest)."""
        if not self.result:
            return {"error": "Server not initialized"}
        updated = self.engine.unmerge_record(record_id, threshold)
        if updated is None:
            return {"error": "No matching results. Run matching first."}
        self.result = updated
        return {
            "status": "unmerged",
            "record_id": record_id,
            "total_clusters": self.result.stats.total_clusters,
        }

    def _record_memory_correction(self, item: dict, decision: str) -> None:
        """Persist a Correction with source='steward', trust=1.0, empty hashes."""
        if self._memory_store is None:
            return
        try:
            import uuid
            from datetime import datetime as _dt

            from goldenmatch.core.memory.store import Correction

            self._memory_store.add_correction(Correction(
                id=str(uuid.uuid4()),
                id_a=int(item["row_id_a"]),
                id_b=int(item["row_id_b"]),
                decision=decision,
                source="steward",
                trust=1.0,
                field_hash="",
                record_hash="",
                original_score=float(item.get("score", 0.0)),
                matchkey_name=None,
                reason=None,
                dataset=self._memory_dataset,
                created_at=_dt.now(),
            ))
        except Exception as e:
            logger.warning("REST /reviews/decide memory write failed: %s", e)

    # ── v1.7-v1.12 controller telemetry ──────────────────────────────────

    def autoconfigure(
        self,
        records: list[dict] | None = None,
        exclude_columns: list[str] | None = None,
    ) -> dict:
        """Run AutoConfigController; return committed config + telemetry.

        ``records`` overrides the server's currently-loaded data. ``None``
        re-runs auto-config against the data the server was booted with —
        useful for "show me what auto-config would do on this dataset" without
        re-uploading rows.

        ``exclude_columns`` is a list of column names to skip across the
        suite (matchkeys + blocking + GoldenFlow transforms). Layered
        additively with detector-derived exclusions. Same shape as the
        Python kwarg + CLI flag. See spec
        docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md.

        Caches the telemetry on ``self._last_telemetry`` so a follow-up
        ``GET /controller/telemetry`` request returns the same blob without
        re-running the controller.
        """
        import polars as pl

        from goldenmatch.core.autoconfig import (
            _LAST_CONTROLLER_RUN,
            _RUNTIME_EXCLUDE_COLUMNS,
            auto_configure_df,
        )

        df = pl.DataFrame(records) if records else self.engine.data
        _excl_token = None
        if exclude_columns:
            _excl_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(exclude_columns))
        try:
            cfg = auto_configure_df(df)
        finally:
            if _excl_token is not None:
                _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        state = _LAST_CONTROLLER_RUN.get()
        profile, history = state if state is not None else (None, None)

        # Reuse the same serializer the web / SQL surfaces use so callers
        # parsing telemetry across REST + MCP + A2A see one schema.
        try:
            from goldenmatch.web.controller_telemetry import serialize_telemetry
            telemetry = serialize_telemetry(
                profile=profile,
                history=history,
                committed_config=cfg,
                source="rest_autoconfig",
                run_name=None,
                recorded_at=None,
            )
        except Exception:
            telemetry = {"available": state is not None, "source": "rest_autoconfig"}

        self._last_telemetry = telemetry

        return {
            "config": cfg.model_dump(mode="json", exclude_none=True)
            if hasattr(cfg, "model_dump") else {},
            "telemetry": telemetry,
        }

    def get_controller_telemetry(self) -> dict:
        """Return the most recent ``autoconfigure`` call's telemetry blob.

        Returns the unavailable sentinel before any ``POST /autoconfig`` call.
        """
        if self._last_telemetry is None:
            return {"available": False, "source": None}
        return self._last_telemetry

    def certify_recall(self) -> dict:
        """Unsupervised recall estimate (no ground truth) over the loaded data,
        via capture-recapture across the config's matchkeys/passes. Point
        estimate; a safe lower bound needs a labelled audit (CLI --audit-out)."""
        from goldenmatch.core.recall_certificate import certify_recall_df

        df = getattr(self.engine, "data", None)
        if df is None:
            return {"error": "no data loaded"}
        est = certify_recall_df(df, self.config)
        return {
            "estimated_recall": est.recall,
            "n_systems": est.n_systems,
            "found_pairs": est.found_pairs,
            "system_overlap": round(est.mean_overlap, 3),
            "estimable": est.estimable,
            "note": est.note,
        }


# Global server instance
_server_instance: MatchServer | None = None
# Bearer token enforced on every endpoint except /health (None = open, loopback only).
_auth_token: str | None = None


def resolve_api_auth_token(host: str) -> str | None:
    """Return the REST API bearer token, enforcing the fail-closed bind rule.

    Raises ``RuntimeError`` when binding to a non-loopback host without
    ``GOLDENMATCH_API_TOKEN`` set. Returns the token (or ``None`` for an
    intentionally-open loopback bind).
    """
    token = os.environ.get("GOLDENMATCH_API_TOKEN")
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not token and not is_loopback:
        raise RuntimeError(
            f"Refusing to start an unauthenticated REST API on host {host!r}. "
            "Set GOLDENMATCH_API_TOKEN, or bind to 127.0.0.1 for local use."
        )
    return token


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the GoldenMatch API."""

    def _authorized(self) -> bool:
        """True when no token is configured or the Bearer header matches."""
        if not _auth_token:
            return True
        header = self.headers.get("Authorization", "")
        return header.startswith("Bearer ") and header[7:] == _auth_token

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            ready = _server_instance is not None and _server_instance.result is not None
            self._json_response(
                {"status": "ok" if ready else "initializing", "service": "goldenmatch", "ready": ready},
                200 if ready else 503,
            )
            return
        if not self._authorized():
            self._json_response({"error": "Unauthorized"}, 401)
            return

        if path == "/stats":
            self._json_response(_server_instance.get_stats())
        elif path == "/clusters":
            params = parse_qs(parsed.query)
            min_size = int(params.get("min_size", ["2"])[0])
            self._json_response(_server_instance.get_clusters(min_size))
        elif path.startswith("/clusters/"):
            try:
                cid = int(path.split("/")[-1])
                detail = _server_instance.get_cluster_detail(cid)
                if detail:
                    self._json_response(detail)
                else:
                    self._json_response({"error": "Cluster not found"}, 404)
            except ValueError:
                self._json_response({"error": "Invalid cluster ID"}, 400)
        elif path == "/reviews":
            params = parse_qs(parsed.query)
            lo = float(params.get("lo", ["0.70"])[0])
            hi = float(params.get("hi", ["0.90"])[0])
            limit = int(params.get("limit", ["50"])[0])
            queue = _server_instance.build_review_queue(lo, hi, limit)
            self._json_response({"queue": queue, "count": len(queue)})
        elif path == "/reviews/decisions":
            self._json_response({
                "decisions": _server_instance._review_decisions,
                "count": len(_server_instance._review_decisions),
            })
        elif path == "/controller/telemetry":
            self._json_response(_server_instance.get_controller_telemetry())
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._authorized():
            self._json_response({"error": "Unauthorized"}, 401)
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        if path == "/match":
            record = data.get("record", data)
            top_k = data.get("top_k", 5)
            matches = _server_instance.match_record(record, top_k)
            self._json_response({"matches": matches, "count": len(matches)})
        elif path == "/autoconfig":
            # Optional `records` body to autoconfig against a different dataset.
            # Omit it to reprofile the server's loaded data.
            #
            # Optional `exclude_columns: list[str]` skips those columns
            # across matching + transforms. See spec
            # docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md.
            records = data.get("records")
            exclude_columns = data.get("exclude_columns") or None
            if exclude_columns is not None and not isinstance(exclude_columns, list):
                self._json_response(
                    {"error": "exclude_columns must be a list of strings"}, 400,
                )
                return
            try:
                result = _server_instance.autoconfigure(
                    records=records, exclude_columns=exclude_columns,
                )
                self._json_response(result)
            except Exception as exc:
                self._json_response({"error": f"autoconfig failed: {exc}"}, 400)
        elif path == "/match/batch":
            records = data.get("records", [])
            results = []
            for record in records:
                matches = _server_instance.match_record(record, top_k=3)
                results.append({"record": record, "matches": matches})
            self._json_response({"results": results})
        elif path == "/explain":
            record_a = data.get("record_a", {})
            record_b = data.get("record_b", {})
            explanation = _server_instance.explain_pair(record_a, record_b)
            self._json_response(explanation)
        elif path == "/reviews/decide":
            pair_id = data.get("pair_id", "")
            decision = data.get("decision", "")
            reviewer = data.get("reviewer", "api")
            if decision not in ("approve", "reject"):
                self._json_response({"error": "Decision must be 'approve' or 'reject'"}, 400)
            else:
                result = _server_instance.review_decision(pair_id, decision, reviewer)
                self._json_response(result)
        elif path == "/shatter":
            cluster_id = data.get("cluster_id")
            if not isinstance(cluster_id, int):
                self._json_response({"error": "cluster_id (int) is required"}, 400)
            else:
                result = _server_instance.shatter_cluster(cluster_id)
                self._json_response(result, 404 if "error" in result else 200)
        elif path == "/unmerge":
            record_id = data.get("record_id")
            if not isinstance(record_id, int):
                self._json_response({"error": "record_id (int) is required"}, 400)
            else:
                threshold = float(data.get("threshold", 0.0))
                result = _server_instance.unmerge_record_op(record_id, threshold)
                self._json_response(result, 404 if "error" in result else 200)
        elif path == "/certify-recall":
            result = _server_instance.certify_recall()
            self._json_response(result, 400 if "error" in result else 200)
        else:
            self._json_response({"error": "Not found"}, 404)

    def _safe_cors_origin(self, origin: str, allowed: set[str]) -> str:
        """Return a safe origin for header reflection, or empty string if invalid."""
        if not origin:
            return ""
        # Prevent HTTP response splitting via CR/LF injection.
        if "\r" in origin or "\n" in origin:
            return ""
        sanitized = origin.strip()
        # Reject values that change after stripping to avoid ambiguous whitespace.
        if sanitized != origin:
            return ""
        if sanitized not in allowed:
            return ""
        return sanitized

    def _json_response(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # CORS: only echo an Origin that's on the explicit allowlist
        # (GOLDENMATCH_API_CORS_ORIGINS, comma-separated). No wildcard.
        allowed = {
            o.strip()
            for o in os.environ.get("GOLDENMATCH_API_CORS_ORIGINS", "").split(",")
            if o.strip()
        }
        origin = self.headers.get("Origin", "")
        safe_origin = self._safe_cors_origin(origin, allowed)
        if safe_origin:
            self.send_header("Access-Control-Allow-Origin", safe_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args) -> None:
        logger.info("%s %s", self.address_string(), format % args)


def start_server(
    engine,
    config: GoldenMatchConfig,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    """Start the GoldenMatch API server."""
    global _server_instance, _auth_token
    _auth_token = resolve_api_auth_token(host)
    _server_instance = MatchServer(engine, config)

    print("Initializing GoldenMatch API...")
    _server_instance.initialize()

    server = HTTPServer((host, port), APIHandler)
    stats = _server_instance.get_stats()

    print(f"\n⚡ GoldenMatch API running at http://{host}:{port}")
    print(f"   Records: {stats.get('total_records', 0):,}")
    print(f"   Clusters: {stats.get('total_clusters', 0):,}")
    print("\n   Endpoints:")
    print("   GET  /health              Health check")
    print("   GET  /stats               Dataset statistics")
    print("   POST /match               Match a record")
    print("   POST /match/batch         Match multiple records")
    print("   POST /explain             Explain a match")
    print("   GET  /clusters            List clusters")
    print("   GET  /clusters/<id>       Cluster detail")
    print("   POST /autoconfig          Run AutoConfigController (returns config + telemetry)")
    print("   GET  /controller/telemetry Last autoconfig's stop_reason / decisions / NE / priors")
    print("   GET  /reviews             Review queue (steward)")
    print("   POST /reviews/decide      Approve/reject a pair")
    print("\n   Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
