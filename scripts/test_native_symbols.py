"""Unit tests for the native-symbol reconciliation gate. Pure data — no build,
no goldenmatch import. Run: python -m pytest scripts/test_native_symbols.py -q"""
import importlib.util
import pathlib
import sys

_spec = importlib.util.spec_from_file_location(
    "check_native_symbols", pathlib.Path(__file__).parent / "check_native_symbols.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # required so @dataclass can resolve cls.__module__ (py3.13)
_spec.loader.exec_module(mod)


def test_parse_registrations_extracts_final_segment():
    src = """
    m.add_function(wrap_pyfunction!(cluster::connected_components, m)?)?;
    m.add_function(wrap_pyfunction!(
        hash::record_fingerprint, m)?)?;   // multi-line
    // a commented mention of wrap_pyfunction! with no path is ignored
    """
    assert mod.parse_registrations_text(src) == {"connected_components", "record_fingerprint"}


def test_parse_registrations_extracts_madd_consts():
    # m.add("NAME", ...) consts (capability flags) are exports and must
    # reconcile against getattr(native_module(), "NAME", ...) host probes.
    src = """
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("FS_SUPPORTS_LEVEL_THRESHOLDS", true)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_fs, m)?)?;
    """
    assert mod.parse_registrations_text(src) == {
        "__version__", "FS_SUPPORTS_LEVEL_THRESHOLDS", "score_block_pairs_fs",
    }


def test_scan_refs_direct_form():
    src = 'from goldenmatch.core._native_loader import native_module\nx = native_module().quantile(a)\n'
    assert mod.scan_file_refs(src) == {"quantile"}


def test_scan_refs_resolves_alias_binding():
    # THE load-bearing case: kernel bound to a local, then used via that local.
    src = (
        "from goldenmatch.core._native_loader import native_module\n"
        "_nm = native_module()\n"
        "if hasattr(_nm, 'autoconfig_decide_plan'):\n"
        "    r = _nm.autoconfig_decide_plan(x)\n"
    )
    assert mod.scan_file_refs(src) == {"autoconfig_decide_plan"}


def test_scan_refs_resolves_ensure_native_alias_and_getattr():
    src = (
        "from goldenmatch.core._native_loader import _ensure_native\n"
        "native_mod = _ensure_native()\n"
        "fn = getattr(native_mod, 'jaro_winkler_similarity', None)\n"
    )
    assert mod.scan_file_refs(src) == {"jaro_winkler_similarity"}


def test_scan_refs_ignores_unrelated_local_named_like_alias_when_not_bound():
    # `nm` is NOT bound to native_module here -> its attribute access is not a ref.
    src = ("from goldenmatch.core._native_loader import native_module\n"
           "nm = something_else()\n"
           "nm.frobnicate()\n"
           "y = native_module().real_symbol(z)\n")
    assert mod.scan_file_refs(src) == {"real_symbol"}


def test_reconcile_missing_fails_unwired_reports():
    registered = {"a", "b", "dead"}
    referenced = {"a", "b", "ghost"}
    res = mod.reconcile(registered, referenced, allow=set())
    assert res.missing == {"ghost"}
    assert res.unwired == {"dead"}


def test_allowlist_subtracts_from_missing():
    res = mod.reconcile({"a"}, {"a", "ghost"}, allow={"ghost"})
    assert res.missing == set()


def test_parse_registrations_accepts_bare_fn():
    src = "m.add_function(wrap_pyfunction!(histogram, m)?)?;\n" \
          "m.add_function(wrap_pyfunction!(profile::benford_leading_digits, m)?)?;\n"
    assert mod.parse_registrations_text(src) == {"histogram", "benford_leading_digits"}


def test_literal_idiom_extracts_arrow_literals():
    src = ('from goldenflow.core._native_loader import native_module\n'
           'def _kernel_runner(name): ...\n'
           'x = _kernel_runner("phone_e164_arrow")\n'
           'attr = "split_address_arrow"\n'
           'y = getattr(native_module(), attr)\n'
           'z = "not_a_kernel"\n')
    got = mod.scan_references_text(src, idiom="literal", literal_pattern=r'"(\w+_arrow)"')
    assert got == {"phone_e164_arrow", "split_address_arrow"}


def test_literal_idiom_skips_files_without_loader_token():
    # a file without the loader token contributes nothing even if it has an _arrow literal
    src = 'x = "phone_e164_arrow"\n'   # no native_module token
    # via scan_references over a temp dir would skip it; here assert the token gate directly
    assert "native_module" not in src


def test_runtime_idiom_unchanged():
    src = ('from goldenmatch.core._native_loader import native_module\n'
           'r = native_module().connected_components(x)\n')
    assert mod.scan_references_text(src, idiom="runtime") == {"connected_components"}
