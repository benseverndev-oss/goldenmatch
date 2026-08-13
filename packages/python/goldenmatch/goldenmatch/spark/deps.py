"""Ship the client's Python environment to Spark executors (P1).

Spark Connect's ``addArtifact(..., archive=True)`` uploads an archive and unpacks
it executor-side; ``spark.sql.execution.pyspark.python`` then points the UDF
worker at that interpreter. Together they put ``goldenmatch`` on the executors
**without a cluster-side install**, which is the whole zero-friction claim for a
Splink-on-Spark cutover: no platform-team ticket, no cluster libraries, no image
rebuild.

**Spark Connect only.** ``addArtifact`` raises on a classic session; that is a
deliberate constraint of the design, not an oversight (spec
``2026-08-10-spark-native-execution-design`` §4).

Why this module exists at all: P0 (run 31496638072) found the tier is fully
Spark-Connect-compatible -- 36 tests pass, no API gaps -- and that all 20
failures were ``ModuleNotFoundError`` inside the Python UDF worker. The tier
works; its dependencies were simply never delivered. Under pysail this could not
surface, because pysail's Connect server is in-process and its worker shares the
client interpreter.
"""
from __future__ import annotations

import json
import posixpath
import re
import stat
import tarfile
import zipfile
from typing import Any

_ENV_NAME = "environment"

#: Where a packed environment keeps its interpreter, most specific first. The
#: POSIX names are what ``python -m venv`` produces; ``Scripts/`` is the Windows
#: layout, which is included because an archive can be INSPECTED anywhere even
#: though it can only be shipped to Linux executors.
_INTERPRETERS = ("bin/python", "bin/python3", "Scripts/python.exe", "Scripts/python")

#: A symlink chain is followed at most this far. A venv's real chain is two hops
#: (``python -> python3 -> python3.12``); the bound exists so a cyclic archive
#: cannot hang the driver.
_MAX_LINK_HOPS = 12

#: ``C:\Python312\...`` -- absolute, but not by the POSIX rule.
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class ExternalInterpreterError(RuntimeError):
    """The archive's interpreter is a symlink pointing out of the archive.

    Raised on the DRIVER, before anything is uploaded. The alternative is an
    executor-side ``java.io.IOException: Cannot run program
    "./environment/bin/python" ... error=2`` -- a JVM error, in a task log, on
    another machine, naming a path that exists on the machine you packed from.
    """


def _archive_members(path: str) -> dict[str, str | None]:
    """Map member name -> symlink target (``None`` for a regular file).

    Two containers, because the format decides the answer. ``venv-pack``
    preserves the interpreter symlink in a tarball, but its ``ZipArchive.add``
    dereferences one unless ``--zip-symlinks`` is passed -- so the same venv is
    relocatable packed as ``.zip`` and not as ``.tar.gz``. That asymmetry is the
    cheapest fix available to a user and is worth modelling exactly.
    """
    members: dict[str, str | None] = {}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = _normalise(info.filename)
                mode = info.external_attr >> 16
                if info.create_system == 3 and stat.S_ISLNK(mode):
                    members[name] = zf.read(info).decode("utf-8", "replace")
                else:
                    members[name] = None
        return members
    with tarfile.open(path) as tf:  # handles .tar, .gz, .bz2, .xz by magic
        for info in tf.getmembers():
            name = _normalise(info.name)
            # A hard link points at another member by name, so it resolves with
            # exactly the same walk as a symlink.
            members[name] = info.linkname if (info.issym() or info.islnk()) else None
    return members


def _normalise(name: str) -> str:
    return name[2:] if name.startswith("./") else name.rstrip("/")


def _find_interpreter(members: dict[str, str | None]) -> str | None:
    """Locate the interpreter, allowing ONE leading directory.

    Packers differ on whether the venv is at the archive root or under a
    directory named for it, and the check has to work either way -- a report of
    "no interpreter" on a perfectly good archive would send someone chasing the
    wrong thing.
    """
    for suffix in _INTERPRETERS:
        if suffix in members:
            return suffix
        # Sorted so an archive with more than one candidate prefix resolves the
        # same way every run; a check that picks a different member per
        # invocation is worse than no check.
        for name in sorted(members):
            if name.partition("/")[2] == suffix:
                return name
    return None


def _is_absolute(target: str) -> bool:
    return target.startswith("/") or bool(_WINDOWS_ABSOLUTE.match(target))


def inspect_environment_archive(archive: str) -> dict[str, Any]:
    """Ask whether a packed environment carries its own interpreter.

    Returns ``interpreter``, ``self_contained``, ``escapes_to`` and ``error``.

    ``self_contained`` is deliberately three-valued:

    ``True``
        the interpreter is a real file in the archive, or a symlink chain that
        ends at one. It will run wherever the archive unpacks.
    ``False``
        the chain leaves the archive. It will run only on a host that happens to
        have that exact path -- which the driver cannot check, so this is a
        statement about the ARCHIVE, not a verdict about your cluster.
    ``None``
        no opinion: no interpreter member (a PEX is a single file, not a packed
        venv), or the archive could not be read. Never treat this as a failure;
        a check that fails closed on layouts it does not model stops people
        using supported things.
    """
    report: dict[str, Any] = {
        "archive": archive,
        "interpreter": None,
        "self_contained": None,
        "escapes_to": None,
        "error": None,
    }
    try:
        members = _archive_members(archive)
    except Exception as exc:  # noqa: BLE001 - any unreadable archive is "no opinion"
        report["error"] = f"could not read {archive}: {exc}"
        return report

    start = _find_interpreter(members)
    if start is None:
        report["error"] = (
            "no bin/python member; not a packed venv (a PEX, or another layout)"
        )
        return report
    report["interpreter"] = start

    current = start
    seen = {current}
    for _ in range(_MAX_LINK_HOPS):
        target = members.get(current)
        if target is None:
            report["self_contained"] = True
            return report
        if _is_absolute(target):
            report["self_contained"] = False
            report["escapes_to"] = target
            return report
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(current), target))
        if resolved == ".." or resolved.startswith("../"):
            report["self_contained"] = False
            # The raw target, not the normalised one: `../../usr/bin/python3.12`
            # is what the archive says, and what the user has to recognise.
            report["escapes_to"] = target
            return report
        if resolved not in members:
            report["self_contained"] = False
            report["escapes_to"] = resolved
            return report
        if resolved in seen:
            report["self_contained"] = False
            report["error"] = f"symlink cycle at {resolved}"
            return report
        seen.add(resolved)
        current = resolved

    report["self_contained"] = False
    report["error"] = f"symlink chain from {start} exceeds {_MAX_LINK_HOPS} hops"
    return report


def _external_interpreter_message(report: dict[str, Any]) -> str:
    target = report["escapes_to"] or report["error"] or "outside the archive"
    return (
        f"{report['archive']}: the interpreter at {report['interpreter']} is a "
        f"symlink to {target}, which is outside the archive.\n"
        "\n"
        "`bin/python` in a `python -m venv` environment is a symlink to the "
        "interpreter the venv was created against. Shipping that archive to an "
        "executor unpacks fine and then cannot execute:\n"
        "\n"
        '    java.io.IOException: Cannot run program "./environment/bin/python"'
        " ... error=2, No such file or directory\n"
        "\n"
        "Three ways forward:\n"
        "  1. Pack as .zip. venv-pack stores the interpreter BY VALUE in a zip "
        "(it only preserves symlinks with --zip-symlinks), so the archive "
        "becomes self-contained: `venv-pack -p ENV -o env.zip`.\n"
        "  2. Build the venv against an interpreter at a path the executors "
        "also have (inside a container matching the executor image), or point "
        "the link there at pack time with venv-pack's --python-prefix.\n"
        "  3. If the executors really do have that interpreter at that exact "
        "path, say so: ship_python_environment(..., "
        "allow_external_interpreter=True).\n"
        "\n"
        "This is checked on the driver because the executor-side failure is a "
        "JVM IOException in a task log on another machine (issue #2531)."
    )


def ship_python_environment(
    spark: Any,
    archive: str,
    env_name: str = _ENV_NAME,
    *,
    allow_external_interpreter: bool = False,
) -> None:
    """Upload ``archive`` and point the UDF workers at its interpreter.

    ``archive`` is a relocatable virtualenv -- e.g. built with ``venv-pack``,
    ``conda-pack`` or PEX -- containing ``goldenmatch`` and its runtime deps.

    **Platform trap.** The archive must be built for the EXECUTOR platform
    (manylinux), not the client's. A venv packed on macOS or Windows will upload
    and unpack happily, then fail to execute on Linux executors. Worse, the
    scorer falls back to the pure ``core.strsim`` floor on any error, so a
    mismatched archive can look like success while delivering none of the native
    speed. Build it on the target platform (CI is the obvious place).

    **What must be in it.** ``goldenmatch`` plus **pandas** and **pyarrow**.
    goldenmatch does not depend on pandas, and the tier no longer needs it: its
    UDFs are ``arrow_udf`` over ``pa.Array``. Shipping it was required while the
    worker -- installing goldenmatch alone ships an archive that unpacks cleanly
    and cannot run a single UDF. rapidfuzz is NOT needed: the scorer floor is
    goldenmatch's own ``core.strsim``, and rapidfuzz is a dev-only extra.

    **Relocation trap** (issue #2531). Same OS and arch is not sufficient: the
    interpreter PATH has to resolve on the executor too. ``bin/python`` in a
    ``python -m venv`` environment is a symlink to the interpreter the venv was
    created against, and ``venv-pack`` preserves that symlink in a tarball. This
    function inspects the archive and refuses one whose interpreter points out
    of it; see :func:`inspect_environment_archive`.

    Args:
        spark: an active Spark **Connect** session.
        archive: path to the packed environment.
        env_name: unpack directory name on the executor; also the interpreter
            path prefix. Rarely needs changing.
        allow_external_interpreter: ship even when the interpreter symlink
            leaves the archive. Correct when the executors genuinely have that
            path -- under ``local[*]``, where the executor IS the driver, or on
            a cluster whose image carries the same interpreter. The driver
            cannot verify either, which is why this is a statement you make
            rather than a check that passes.

    Raises:
        ExternalInterpreterError: the archive cannot run on a host that does not
            already have the interpreter it was packed against.
    """
    if not allow_external_interpreter:
        report = inspect_environment_archive(archive)
        # `is False` on purpose: `None` means the check formed no opinion, and
        # must not block a working setup.
        if report["self_contained"] is False:
            raise ExternalInterpreterError(_external_interpreter_message(report))
    spark.addArtifact(f"{archive}#{env_name}", archive=True)
    spark.conf.set("spark.sql.execution.pyspark.python", f"./{env_name}/bin/python")


def executor_probe(spark: Any) -> dict[str, Any]:
    """Report what is importable **on the executor**. Never raises.

    Deliberately implemented as a UDF: a driver-side check proves nothing,
    because the driver is where the client venv already lives. A UDF body only
    executes in a Python worker, so reaching the probe at all establishes that we
    are off the driver.

    Returns a dict with ``ran_on``, ``goldenmatch``, ``strsim``, ``pandas``,
    ``pyarrow``, ``native_kernel`` and ``executable``.

    ``native_kernel: False`` is **not** a dependency failure on its own --
    ``sail_scoring`` is in ``_FALLBACK_ONLY`` (f32 vs the f64 floor) and does not
    run under ``auto`` regardless. Lifting that is P3.
    """
    from pyspark.sql.functions import udf
    from pyspark.sql.types import StringType

    @udf(returnType=StringType())
    def _probe(_ignored: str) -> str:
        import importlib.util
        import json as _json
        import os as _os
        import sys as _sys

        def _importable(name: str) -> bool:
            try:
                return importlib.util.find_spec(name) is not None
            except Exception:  # noqa: BLE001 - a probe must never raise
                return False

        native = False
        try:
            from goldenmatch.core._native_loader import native_module

            native = native_module() is not None
        except Exception:  # noqa: BLE001 - absent kernel is a datum, not an error
            native = False

        return _json.dumps(
            {
                "ran_on": "executor",
                "goldenmatch": _importable("goldenmatch"),
                # The scorer floor is goldenmatch's OWN strsim, not rapidfuzz --
                # rapidfuzz is a dev-only extra and must NOT be shipped.
                "strsim": _importable("goldenmatch.core.strsim"),
                # Reported, not required. The tier moved off pandas_udf, so a shipped env
        # without pandas is CORRECT -- this stays only so a probe of an older
        # env still says what is there.
        "pandas": _importable("pandas"),
                "pyarrow": _importable("pyarrow"),
                "native_kernel": native,
                "executable": _sys.executable,
                "pyspark_python": _os.environ.get("PYSPARK_PYTHON", "?"),
            }
        )

    row = (
        spark.range(1)
        .selectExpr("cast(id as string) as s")
        .select(_probe("s").alias("report"))
        .collect()[0]
    )
    return json.loads(row["report"])
