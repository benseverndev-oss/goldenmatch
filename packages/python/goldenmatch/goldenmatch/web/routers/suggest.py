"""GET /api/v1/suggest — config-healer suggestions for the workbench dataset.

Runs ``goldenmatch.core.suggest.review_config`` over the project's ``data.csv``
using the workbench's current rules (or zero-config when none are set) and
returns ranked, self-verified suggestions in the shared cross-surface wire
shape (``serialize_suggestions``) — the same shape the REST ``/suggest``
endpoint, the MCP ``review_config`` tool, and the A2A ``review_config`` skill
emit.

Fail-safe: the native kernel that powers suggestions is optional. When it's
absent the route returns ``{"suggestions": [], "native_required": true}``
rather than 500ing, so the workbench can render a friendly badge.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from goldenmatch._polars_lazy import pl

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

SUGGEST_TIMEOUT_S = 60
_executor = ThreadPoolExecutor(max_workers=1)


def _execute_suggest(project_root: Path, rules) -> dict[str, Any]:
    src = project_root / "data.csv"
    if not src.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")

    df = pl.read_csv(src)

    # Build a config from the workbench's current rules; fall back to
    # zero-config auto-configure when the user hasn't set rules yet (offline-safe).
    if rules is not None:
        from goldenmatch.web.preview import _build_config

        config = _build_config(rules)
    else:
        from goldenmatch.core.autoconfig import auto_configure_df

        config = auto_configure_df(df, allow_remote_assets=False)

    from goldenmatch.core.suggest import SuggestionsNativeRequired, review_config
    from goldenmatch.core.suggest.surface import serialize_suggestions

    try:
        suggestions = review_config(df, config)
    except SuggestionsNativeRequired as exc:
        return {"suggestions": [], "native_required": True, "message": str(exc)}

    return {"suggestions": serialize_suggestions(suggestions, verified=True)}


@router.get("/suggest")
async def suggest(request: Request) -> dict:
    state = request.app.state.app_state
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                lambda: _execute_suggest(state.project_root, state.rules),
            ),
            timeout=SUGGEST_TIMEOUT_S,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=408, detail=f"suggest exceeded {SUGGEST_TIMEOUT_S}s",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # review_config has many narrow failure modes
        raise HTTPException(status_code=400, detail=f"suggest failed: {exc}")
