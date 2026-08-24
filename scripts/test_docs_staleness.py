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

from check_docs_staleness import FLAG_SPECS, _documented_flags, _git

# The flag rule is now registry-driven (one entry per package declaring a
# `prose_flag_page`). goldenmatch is the only one today and is the package these
# encoding tests are about, so pin to it explicitly rather than to FLAG_SPECS[0].
GM = next(spec for spec in FLAG_SPECS if spec[0] == "goldenmatch")
GM_FLAG_RE, TUNING_MDX = GM[1], GM[2]


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
    flags = _documented_flags("HEAD", TUNING_MDX, GM_FLAG_RE)
    assert flags, f"no GOLDENMATCH_* flags parsed from {TUNING_MDX}"
    assert "GOLDENMATCH_NATIVE" in flags, (
        "GOLDENMATCH_NATIVE is the most-documented flag in the reference; its "
        f"absence means {TUNING_MDX} was not read correctly"
    )


def test_flag_specs_come_from_the_registry():
    """The roster must be DERIVED, not a second hardcoded copy.

    The whole point of driving this off ``config_matrix.registry`` is that giving a
    package a tuning page is a one-line registry change. If someone re-hardcodes a
    list here, this catches it.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config_matrix.registry import REGISTRY

    expected = {
        (spec.name, spec.prose_flag_page)
        for spec in REGISTRY.values()
        if spec.prose_flag_page
    }
    assert {(name, page) for name, _, page in FLAG_SPECS} == expected
    assert ("goldenmatch", "docs-site/goldenmatch/tuning.mdx") in expected


def test_registry_is_importable_without_pydantic():
    """``check_docs_staleness`` runs on a bare setup-python runner.

    It imports the registry for the roster, so ``config_matrix.registry`` must stay
    reachable WITHOUT the synced workspace. ``config_matrix/__init__.py`` re-exports
    the pydantic-dependent render half lazily to keep that true; an eager re-export
    would make this gate uninstallable in its own CI job.
    """
    import subprocess
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent
    probe = (
        f"import sys; sys.path.insert(0, {str(scripts)!r})\n"
        "sys.modules['pydantic'] = None\n"
        "from config_matrix.registry import REGISTRY\n"
        "assert REGISTRY\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, (
        "config_matrix.registry is no longer importable without pydantic:\n"
        + proc.stderr
    )


def test_flag_matcher_is_prefix_scoped():
    """A package's matcher must not claim another package's flags."""
    assert GM_FLAG_RE.findall("GOLDENMATCH_NATIVE and GOLDENCHECK_NATIVE") == [
        "GOLDENMATCH_NATIVE"
    ]
