"""REST API server for GoldenCheck — scan files via HTTP."""
from __future__ import annotations

import json
import logging
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.models.finding import Severity

logger = logging.getLogger(__name__)

__all__ = ["run_server", "GoldenCheckHandler"]


class GoldenCheckHandler(BaseHTTPRequestHandler):
    """HTTP request handler for GoldenCheck API."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._json_response({"status": "ok", "tool": "goldencheck"})
        elif path == "/checks":
            from goldencheck.mcp.server import _tool_list_checks
            self._json_response(_tool_list_checks({}))
        elif path == "/domains":
            from goldencheck.semantic.classifier import list_available_domains
            self._json_response({"domains": list_available_domains()})
        else:
            self._json_response({"error": "Not found", "endpoints": [
                "GET /health", "GET /checks", "GET /domains",
                "POST /scan", "POST /scan/url",
            ]}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/scan":
            self._handle_scan()
        elif path == "/scan/url":
            self._handle_scan_url()
        else:
            self._json_response({"error": "Not found"}, status=404)

    def _handle_scan(self):
        """Handle file upload scan — expects multipart or raw CSV body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response({"error": "No data provided"}, status=400)
            return

        body = self.rfile.read(content_length)

        # Parse query params for options
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        domain = params.get("domain", [None])[0]

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
            tmp.write(body)
            tmp_path = Path(tmp.name)

        try:
            findings, profile = scan_file(tmp_path, domain=domain)
            findings = apply_confidence_downgrade(findings, llm_boost=False)
            self._json_response(_build_response(findings, profile))
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _handle_scan_url(self):
        """Handle scan by URL — POST JSON with {"url": "..."}."""
        import urllib.request

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, status=400)
            return

        url = data.get("url")
        if not url:
            self._json_response({"error": "Missing 'url' field"}, status=400)
            return

        domain = data.get("domain")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
            try:
                resp = urllib.request.urlopen(url, timeout=30)
                tmp.write(resp.read())
            except Exception as e:
                self._json_response({"error": f"Failed to download: {e}"}, status=400)
                return
            tmp_path = Path(tmp.name)

        try:
            findings, profile = scan_file(tmp_path, domain=domain)
            findings = apply_confidence_downgrade(findings, llm_boost=False)
            self._json_response(_build_response(findings, profile))
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode())

    def log_message(self, format, *args):
        logger.info(format, *args)


def _build_response(findings, profile) -> dict:
    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

    by_col: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.severity >= Severity.WARNING:
            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
            key = "errors" if f.severity == Severity.ERROR else "warnings"
            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1

    grade, score = profile.health_score(findings_by_column=by_col)

    return {
        "file": profile.file_path,
        "rows": profile.row_count,
        "columns": profile.column_count,
        "health_grade": grade,
        "health_score": score,
        "errors": errors,
        "warnings": warnings,
        "findings_count": len(findings),
        "findings": [
            {
                "severity": f.severity.name.lower(),
                "column": f.column,
                "check": f.check,
                "message": f.message,
                "affected_rows": f.affected_rows,
                "confidence": f.confidence,
            }
            for f in findings
        ],
    }


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the GoldenCheck REST API server."""
    server = HTTPServer((host, port), GoldenCheckHandler)
    print(f"GoldenCheck API server running on http://{host}:{port}")
    print("Endpoints: GET /health, /checks, /domains | POST /scan, /scan/url")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
