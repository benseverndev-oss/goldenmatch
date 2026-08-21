"""Gate for the doc-staleness checker itself (scripts/check_docs_staleness.py).

The flag rule is the only doc gate that a generator cannot fix -- adding a
``GOLDENMATCH_*`` flag means a human has to write the ``tuning.mdx`` entry. So
the checker being *silently* wrong is worse than it being absent: a broken read
of tuning.mdx makes every flag look undocumented, or (as on Windows) crashes
with a ``TypeError`` that reads like a real negative.

``tuning.mdx`` contains non-ASCII punctuation. ``subprocess.run(text=True)``
decodes with the locale default -- cp1252 on Windows -- which cannot decode it.
That decode raises inside subprocess's reader thread, so ``proc.stdout`` comes
back ``None`` with returncode 0, ``_git`` returns ``None`` without raising, and
the caller blows up on ``None``. These tests pin the encoding contract.
"""

from __future__ import annotations

from check_docs_staleness import TUNING_MDX, _documented_flags, _git


def test_git_returns_text_for_non_ascii_content():
    """``_git`` must decode as UTF-8, not the platform's locale encoding.

    ``tuning.mdx`` is the file the flag rule reads and it carries non-ASCII
    punctuation, so it is the exact input that breaks a locale-default decode.
    """
    out = _git("show", f"HEAD:{TUNING_MDX}")
    assert isinstance(out, str), (
        f"_git returned {type(out).__name__}, not str -- a decode error inside "
        "subprocess's reader thread silently yields None with returncode 0"
    )
    assert out, "_git returned empty content for a file known to be non-empty"


def test_documented_flags_finds_the_canonical_reference():
    """The flag rule is only sound if it can actually read tuning.mdx.

    If this returns an empty set, every flag in a diff looks undocumented and
    the gate fails every PR that touches one.
    """
    flags = _documented_flags("HEAD")
    assert flags, f"no GOLDENMATCH_* flags parsed from {TUNING_MDX}"
    assert "GOLDENMATCH_NATIVE" in flags, (
        "GOLDENMATCH_NATIVE is the most-documented flag in the reference; its "
        f"absence means {TUNING_MDX} was not read correctly"
    )
