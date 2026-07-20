"""#1913 (P1 foundation): DedupeResult surfaces the identity-graph resolution
summary so callers -- and the goldenmatch-pg gm_resolve bridge -- can read the
write outcome off the public result instead of the internal pipeline dict."""
from __future__ import annotations

import polars as pl

from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    IdentityConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _people_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__source__": ["crm"] * 4,
            "id": ["1", "2", "3", "4"],
            "email": ["a@x.com", "a@x.com", "b@y.com", "c@z.com"],
            "name": ["Al", "Alice", "Bob", "Cara"],
        }
    )


def _email_key_matchkeys() -> list[MatchkeyConfig]:
    return [
        MatchkeyConfig(
            name="email_key",
            type="exact",
            fields=[MatchkeyField(field="email", scorer="exact")],
        )
    ]


def test_dedupe_df_surfaces_identity_summary(tmp_path):
    cfg = GoldenMatchConfig(
        matchkeys=_email_key_matchkeys(),
        identity=IdentityConfig(
            enabled=True,
            backend="sqlite",
            path=str(tmp_path / "identity.db"),
            dataset="people",
            source_pk_column="id",
        ),
    )
    res = dedupe_df(_people_df(), config=cfg)
    # The two a@x.com rows form one identity; the two singletons form one each.
    assert res.identity_summary is not None
    assert isinstance(res.identity_summary, dict)
    assert res.identity_summary.get("created", 0) >= 1


def test_identity_summary_none_when_disabled(tmp_path):
    # No identity config -> the field stays None (additive default, no writes).
    cfg = GoldenMatchConfig(matchkeys=_email_key_matchkeys())
    res = dedupe_df(_people_df(), config=cfg)
    assert res.identity_summary is None
