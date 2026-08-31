"""``_apply_negative_evidence_to_exact_pairs`` is backend-neutral.

The function used to be typed ``pl.DataFrame`` and read with polars-only
indexing (``full_df[col][idx]``, ``.height``, ``.columns``), so its one caller in
``core/pipeline.py`` bridged with ``_as_polars_df``. That bridge was the last
polars import on the zero-config CLI path -- auto-config's
``promote_negative_evidence`` step puts NE on an exact matchkey by default -- and
it made ``goldenmatch dedupe <csv>`` exit 3 on a polars-free install.

It now reads through the Frame seam. These tests pin BOTH halves of that:

* it produces identical results on a ``pa.Table`` and a ``pl.DataFrame``, so the
  change is behaviour-neutral for anyone who has polars installed; and
* the arrow call never imports polars, so the seam is real rather than an
  ``isinstance`` check with a bridge behind it.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")


def _matchkey():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField

    return MatchkeyConfig(
        name="exact_email",
        type="exact",
        fields=[MatchkeyField(field="email")],
        threshold=0.5,
        negative_evidence=[
            NegativeEvidenceField(
                field="dob", scorer="exact", threshold=0.4, penalty=0.6,
            ),
            NegativeEvidenceField(
                field="city", scorer="token_sort", threshold=0.4, penalty=0.3,
            ),
        ],
    )


def _columns() -> dict[str, list]:
    """Rows 1-6 pair up on email; dob/city agreement varies so some pairs are
    penalized past the threshold and some are not (a test where every pair
    survives would not distinguish the backends)."""
    return {
        "__row_id__": [1, 2, 3, 4, 5, 6],
        "email": ["a@x.com", "a@x.com", "b@x.com", "b@x.com", "c@x.com", "c@x.com"],
        "dob": ["1990-01-01", "1990-01-01", "1975-06-30", "1988-02-11", None, "2001-09-09"],
        "city": ["Leeds", "Leeds", "Bristol", "Cardiff", "Dundee", None],
    }


_PAIRS = [(1, 2, 1.0), (3, 4, 1.0), (5, 6, 1.0)]


def test_arrow_and_polars_give_identical_results():
    from goldenmatch.core.scorer import _apply_negative_evidence_to_exact_pairs

    pl = pytest.importorskip("polars")
    cols = _columns()
    mk = _matchkey()

    arrow_out = _apply_negative_evidence_to_exact_pairs(_PAIRS, mk, pa.table(cols))
    polars_out = _apply_negative_evidence_to_exact_pairs(_PAIRS, mk, pl.DataFrame(cols))

    assert arrow_out == polars_out, f"arrow={arrow_out} polars={polars_out}"
    # Guard against the vacuous case: if NE filtered nothing, the two backends
    # would agree trivially and this test would prove nothing.
    assert len(arrow_out) < len(_PAIRS), (
        "NE penalized no pair -- fixture no longer exercises the filter"
    )


def test_arrow_path_does_not_import_polars():
    """The arrow call must not touch polars even when polars is installed."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    body = textwrap.dedent(
        """
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == 'polars' or name.startswith('polars.'):
                    raise ImportError('polars blocked')
                return None
        sys.meta_path.insert(0, _Block())

        import pyarrow as pa
        from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
        from goldenmatch.core.scorer import _apply_negative_evidence_to_exact_pairs

        mk = MatchkeyConfig(
            name="exact_email", type="exact", fields=[MatchkeyField(field="email")], threshold=0.5,
            negative_evidence=[
                NegativeEvidenceField(field="dob", scorer="exact", threshold=0.4, penalty=0.6),
            ],
        )
        tbl = pa.table({
            "__row_id__": [1, 2, 3, 4],
            "email": ["a@x.com", "a@x.com", "b@x.com", "b@x.com"],
            "dob": ["1990-01-01", "1990-01-01", "1975-06-30", "1988-02-11"],
        })
        out = _apply_negative_evidence_to_exact_pairs([(1, 2, 1.0), (3, 4, 1.0)], mk, tbl)
        assert "polars" not in sys.modules, "polars imported on the arrow NE path"
        print("NE ARROW POLARS-FREE OK", len(out))
        """
    )
    env_path = str(Path(__file__).parent.parent)
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = env_path
    env["GOLDENMATCH_NATIVE"] = "0"
    proc = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-3000:]}"
    assert "NE ARROW POLARS-FREE OK" in proc.stdout
