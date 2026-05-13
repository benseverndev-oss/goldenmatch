"""GET /api/v1/controller/telemetry — surface the AutoConfigController's last
run telemetry (stop_reason, ComplexityProfile health, RunHistory decisions,
indicator priors, committed NE fields).

Populated by ``/api/v1/autoconfig`` and ``/api/v1/run`` whenever those
endpoints actually invoke ``auto_configure_df``. When the user has been
hand-editing rules and never triggered auto-config, this returns
``{"available": false}`` — the workbench panel renders a neutral "run
auto-config to see decisions" message in that case.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from goldenmatch.web.controller_telemetry import serialize_telemetry

router = APIRouter(prefix="/api/v1")


@router.get("/controller/telemetry")
def telemetry(request: Request) -> dict:
    state = request.app.state.app_state
    return serialize_telemetry(
        profile=state.last_controller_profile,
        history=state.last_controller_history,
        committed_config=state.last_controller_committed_config,
        source=state.last_controller_source,
        run_name=state.last_controller_run_name,
        recorded_at=state.last_controller_recorded_at,
    )
