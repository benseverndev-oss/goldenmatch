"""Stage D.3: the fused_match_allowed hint threading from _api.py.

``dedupe_df`` / ``match_df`` have NO lineage/review/explain/anomaly kwargs and
pass no output dir to the pipeline, so they can never request a caller-intent
artifact -> they thread ``fused_match_allowed=True`` to the controller (the
config-driven divergence gate still hard-blocks routing there). Every other
entry point (direct ``auto_configure_df``, file-based dedupe()/CLI/MCP) leaves
the default ``fused_match_allowed=False`` -> match never routes there in v1
(default-deny).

These tests spy on ``AutoConfigController.run`` and delegate to the real
implementation, so they assert the hint value that actually reaches the
controller end-to-end.
"""

from __future__ import annotations

import polars as pl
from goldenmatch._api import dedupe_df
from goldenmatch.core.autoconfig import auto_configure_df
from goldenmatch.core.autoconfig_controller import AutoConfigController


def _df() -> pl.DataFrame:
    # 2-column shape (name + zip) keeps auto-config to <=2 fuzzy fields so it never
    # enables the 3+field rerank cross-encoder (HF download, offline-hostile).
    names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    return pl.DataFrame(
        {
            "name": [names[i % len(names)] for i in range(30)],
            "zip": [str(10000 + (i % 8)) for i in range(30)],
        }
    )


def _spy_controller_run(monkeypatch) -> dict:
    """Patch AutoConfigController.run to record fused_match_allowed, delegate to
    the real run. Returns the capture dict."""
    captured: dict = {}
    real_run = AutoConfigController.run

    def spy(self, df, **kw):
        captured["fused_match_allowed"] = kw.get("fused_match_allowed")
        return real_run(self, df, **kw)

    monkeypatch.setattr(AutoConfigController, "run", spy)
    return captured


def test_dedupe_df_threads_hint_true(monkeypatch):
    captured = _spy_controller_run(monkeypatch)
    dedupe_df(_df())
    assert captured.get("fused_match_allowed") is True


def test_direct_auto_configure_df_default_denies(monkeypatch):
    # A direct auto_configure_df call (no _api hint) leaves the default False.
    captured = _spy_controller_run(monkeypatch)
    auto_configure_df(_df(), _skip_finalize=True)
    assert captured.get("fused_match_allowed") is False


def test_controller_run_default_is_deny():
    # The signature default is the default-deny contract.
    import inspect

    sig = inspect.signature(AutoConfigController.run)
    assert sig.parameters["fused_match_allowed"].default is False
