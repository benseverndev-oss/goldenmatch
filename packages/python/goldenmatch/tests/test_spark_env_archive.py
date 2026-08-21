"""#2531: an archive whose interpreter is a symlink OUT of the archive.

``ship_python_environment`` uploads a packed venv and points the executors'
Python worker at ``./environment/bin/python``. In a ``python -m venv``
environment that path is a **symlink to the interpreter the venv was created
against** -- on a GitHub runner, somewhere under ``/opt/hostedtoolcache``.
Upload that to a cluster and the archive unpacks perfectly and then cannot be
executed:

    java.io.IOException: Cannot run program "./environment/bin/python"
      (in directory "./17c42a47-..."): error=2, No such file or directory

The whole suite ran under ``local[*]``, where the executor IS the driver -- so
the symlink resolved and the helper's central claim, that you can pack a venv on
your client and ship it to a cluster, was only ever exercised where relocating
it is a no-op.

These tests build archives by hand rather than shelling out to ``venv-pack``:
the shapes below are taken from venv-pack's own source (``Packer.add`` in
``venv_pack/core.py`` preserves a symlink unless ``--python-prefix`` rewrites
it; ``ZipArchive.add`` dereferences one when ``zip_symlinks`` is false), and
building them directly means the check is tested on Windows, needs no venv, and
runs in milliseconds.
"""
from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile

import pytest
from goldenmatch.spark.deps import (
    ExternalInterpreterError,
    inspect_environment_archive,
)

# ── archive builders ─────────────────────────────────────────────────
#
# `tarfile`/`zipfile` write symlink entries without needing the filesystem to
# support them, which matters: this test suite's own dev machine is Windows,
# where creating a real symlink needs a privilege the CI account may not have.


def _tar(tmp_path, entries, name="env.tar.gz"):
    """entries: list of (path, kind, payload). kind in {"file", "link"}."""
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as tf:
        for member_path, kind, payload in entries:
            if kind == "link":
                info = tarfile.TarInfo(member_path)
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tf.addfile(info)
            else:
                data = payload.encode()
                info = tarfile.TarInfo(member_path)
                info.size = len(data)
                info.mode = 0o755
                tf.addfile(info, io.BytesIO(data))
    return str(path)


def _zip(tmp_path, entries, name="env.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member_path, kind, payload in entries:
            info = zipfile.ZipInfo(member_path)
            info.create_system = 3
            if kind == "link":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            else:
                info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(info, payload)
    return str(path)


_REAL = ("bin/python", "file", "#!/bin/sh\n")


# ── what the archive says about its interpreter ──────────────────────


def test_a_real_interpreter_file_is_self_contained(tmp_path):
    """conda-pack, and venv-pack's zip output: the executable is really there."""
    report = inspect_environment_archive(_tar(tmp_path, [_REAL]))
    assert report["self_contained"] is True
    assert report["interpreter"] == "bin/python"
    assert report["escapes_to"] is None


def test_an_absolute_symlink_escapes_the_archive(tmp_path):
    """The #2531 shape, exactly: venv-pack tar of a `python -m venv` env."""
    report = inspect_environment_archive(
        _tar(tmp_path, [("bin/python", "link",
                         "/opt/hostedtoolcache/Python/3.12.7/x64/bin/python3.12")])
    )
    assert report["self_contained"] is False
    assert report["escapes_to"] == (
        "/opt/hostedtoolcache/Python/3.12.7/x64/bin/python3.12"
    )


def test_a_relative_chain_inside_the_archive_is_followed(tmp_path):
    """A venv's real shape is a CHAIN: python -> python3 -> python3.12.

    Stopping at the first link would call this self-contained-by-accident or
    escaping-by-accident depending on which hop you looked at.
    """
    report = inspect_environment_archive(
        _tar(tmp_path, [
            ("bin/python", "link", "python3"),
            ("bin/python3", "link", "python3.12"),
            ("bin/python3.12", "file", "\x7fELF"),
        ])
    )
    assert report["self_contained"] is True
    assert report["escapes_to"] is None


def test_a_chain_that_ends_outside_the_archive_escapes(tmp_path):
    """Two relative hops and then out -- what a real venv-pack tar contains."""
    report = inspect_environment_archive(
        _tar(tmp_path, [
            ("bin/python", "link", "python3"),
            ("bin/python3", "link", "/usr/local/bin/python3.12"),
        ])
    )
    assert report["self_contained"] is False
    assert report["escapes_to"] == "/usr/local/bin/python3.12"


def test_a_relative_symlink_climbing_out_escapes(tmp_path):
    """`../../..` leaves the archive just as surely as a leading slash."""
    report = inspect_environment_archive(
        _tar(tmp_path, [("bin/python", "link", "../../usr/bin/python3.12")])
    )
    assert report["self_contained"] is False
    assert report["escapes_to"] == "../../usr/bin/python3.12"


def test_a_link_to_a_missing_member_is_not_self_contained(tmp_path):
    """Points inside the archive, at nothing. Unpacks, then cannot execute."""
    report = inspect_environment_archive(
        _tar(tmp_path, [("bin/python", "link", "python3.12")])
    )
    assert report["self_contained"] is False
    assert report["escapes_to"] == "bin/python3.12"


def test_a_top_level_prefix_directory_is_stripped(tmp_path):
    """Some packers root everything under a directory; the venv is one level in."""
    report = inspect_environment_archive(
        _tar(tmp_path, [
            ("gmenv/bin/python", "link", "python3"),
            ("gmenv/bin/python3", "file", "\x7fELF"),
        ])
    )
    assert report["self_contained"] is True
    assert report["interpreter"] == "gmenv/bin/python"


def test_an_archive_with_no_interpreter_is_unknown_not_broken(tmp_path):
    """A PEX is a single file, not a packed venv.

    ``ship_python_environment`` documents PEX as a supported input, so "no
    ``bin/python`` member" must mean "this check does not apply", never
    "refuse". A check that fails closed on layouts it does not model is a check
    that stops people using supported things.
    """
    report = inspect_environment_archive(
        _tar(tmp_path, [("some/other/file", "file", "x")])
    )
    assert report["self_contained"] is None
    assert report["interpreter"] is None


def test_windows_venvs_are_recognised(tmp_path):
    """`Scripts/python.exe`, and it is always a real file on Windows."""
    report = inspect_environment_archive(
        _tar(tmp_path, [("Scripts/python.exe", "file", "MZ")])
    )
    assert report["self_contained"] is True
    assert report["interpreter"] == "Scripts/python.exe"


# ── zip: the format changes the answer ───────────────────────────────


def test_zip_stores_the_interpreter_by_value(tmp_path):
    """venv-pack's zip output dereferences the symlink (``zip_symlinks`` off).

    Worth pinning: it means the SAME venv is relocatable packed one way and not
    the other, which is not obvious and is the cheapest real fix available.
    """
    report = inspect_environment_archive(_zip(tmp_path, [_REAL]))
    assert report["self_contained"] is True


def test_zip_with_symlinks_enabled_can_still_escape(tmp_path):
    """`--zip-symlinks` puts the same trap back."""
    report = inspect_environment_archive(
        _zip(tmp_path, [("bin/python", "link", "/opt/py/bin/python3.12")])
    )
    assert report["self_contained"] is False
    assert report["escapes_to"] == "/opt/py/bin/python3.12"


# ── the refusal, on the driver, at ship time ─────────────────────────


class _FakeSession:
    """Records what a Connect session would have been asked to do."""

    def __init__(self):
        self.artifacts = []
        self.conf = self  # `spark.conf.set(...)`
        self.settings = {}

    def addArtifact(self, spec, archive=False):  # noqa: N802 - Spark's name
        self.artifacts.append((spec, archive))

    def set(self, key, value):
        self.settings[key] = value


def test_ship_refuses_an_archive_that_cannot_run_on_an_executor(tmp_path):
    """The point of the whole exercise: fail on the DRIVER, with a reason.

    The executor-side failure is an ``IOException`` naming a path that does not
    exist, from a JVM, in a task log -- which is how this cost three CI runs to
    diagnose.
    """
    from goldenmatch.spark.deps import ship_python_environment

    archive = _tar(tmp_path, [
        ("bin/python", "link", "/opt/hostedtoolcache/Python/3.12.7/x64/bin/python3.12"),
    ])
    spark = _FakeSession()

    with pytest.raises(ExternalInterpreterError) as excinfo:
        ship_python_environment(spark, archive)

    message = str(excinfo.value)
    # The target, because "it is a symlink" without saying to WHERE is not
    # actionable.
    assert "/opt/hostedtoolcache/Python/3.12.7/x64/bin/python3.12" in message
    # Both supported repacks, by their real names -- these come from venv-pack's
    # own API and are the difference between a fixable message and a dead end.
    assert "--python-prefix" in message
    assert ".zip" in message
    # And the escape hatch, since the driver CANNOT know the executors'
    # filesystem: an absolute target is wrong only if it is absent there.
    assert "allow_external_interpreter" in message

    assert spark.artifacts == [], "nothing may be uploaded once we know it cannot run"
    assert spark.settings == {}


def test_ship_uploads_a_self_contained_archive(tmp_path):
    from goldenmatch.spark.deps import ship_python_environment

    archive = _tar(tmp_path, [_REAL])
    spark = _FakeSession()

    ship_python_environment(spark, archive)

    assert spark.artifacts == [(f"{archive}#environment", True)]
    assert spark.settings == {
        "spark.sql.execution.pyspark.python": "./environment/bin/python"
    }


def test_the_escape_hatch_ships_the_same_archive(tmp_path):
    """`local[*]`, or a cluster that really does have the interpreter there.

    Opt-in rather than default, because the failure it permits is silent and
    remote while the flag is local and reviewable.
    """
    from goldenmatch.spark.deps import ship_python_environment

    archive = _tar(tmp_path, [("bin/python", "link", "/opt/py/bin/python3.12")])
    spark = _FakeSession()

    ship_python_environment(spark, archive, allow_external_interpreter=True)

    assert spark.artifacts == [(f"{archive}#environment", True)]


def test_an_unreadable_archive_does_not_block_shipping(tmp_path):
    """A check that cannot read the file must not be the reason nothing ships.

    This helper's job is to upload an archive. If the inspection cannot form an
    opinion -- an unknown container, a truncated file, a format added later --
    the correct behaviour is to proceed, because refusing would break a working
    setup on the strength of the CHECK failing, not the archive.
    """
    from goldenmatch.spark.deps import ship_python_environment

    not_an_archive = tmp_path / "env.tar.gz"
    not_an_archive.write_bytes(b"this is not a tarball")
    spark = _FakeSession()

    ship_python_environment(spark, str(not_an_archive))

    assert len(spark.artifacts) == 1


def test_inspect_reports_the_reason_it_could_not_tell(tmp_path):
    not_an_archive = tmp_path / "env.tar.gz"
    not_an_archive.write_bytes(b"this is not a tarball")

    report = inspect_environment_archive(str(not_an_archive))

    assert report["self_contained"] is None
    assert report["error"]


def test_a_missing_archive_is_reported_not_raised(tmp_path):
    report = inspect_environment_archive(str(tmp_path / "nope.tar.gz"))
    assert report["self_contained"] is None
    assert report["error"]


# ── the trap this replaces ───────────────────────────────────────────


def test_a_symlink_cycle_terminates(tmp_path):
    """Following links needs a bound; a cycle must not hang the driver."""
    report = inspect_environment_archive(
        _tar(tmp_path, [
            ("bin/python", "link", "python3"),
            ("bin/python3", "link", "python"),
        ])
    )
    assert report["self_contained"] is False


def test_absolute_windows_targets_escape_too(tmp_path):
    report = inspect_environment_archive(
        _tar(tmp_path, [("Scripts/python.exe", "link", r"C:\Python312\python.exe")])
    )
    assert report["self_contained"] is False


@pytest.mark.skipif(os.name == "nt", reason="tarfile writes POSIX paths anyway")
def test_backslashes_are_not_path_separators_in_a_tar(tmp_path):
    """A member literally named with a backslash is one component, not two."""
    report = inspect_environment_archive(
        _tar(tmp_path, [("bin/python", "link", "py\\thon3")])
    )
    assert report["self_contained"] is False
