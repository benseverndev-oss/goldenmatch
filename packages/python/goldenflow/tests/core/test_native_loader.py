from goldenflow.core import _native_loader as L


def test_phone_validate_is_fallback_only(monkeypatch):
    # Even with native present, phone_validate must NOT dispatch to native:
    # its only native symbol (phone_valid_arrow -> is_valid) is the wrong spec.
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", object())  # pretend native is importable
    assert L.native_enabled("phone_validate") is False


def test_phone_wired_component_enabled_when_symbol_present(monkeypatch):
    class FakeNative:
        def phone_e164_arrow(self): ...
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    assert L.native_enabled("phone") is True


def test_fallback_only_excludes_even_when_symbol_present(monkeypatch):
    # If a phone_validate symbol WERE wired, _FALLBACK_ONLY must still exclude
    # it: this isolates the guard from the (unrelated) fact that
    # phone_validate currently has no entry in _COMPONENT_SYMBOLS.
    class FakeNative:
        def phone_valid_arrow(self): ...

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    monkeypatch.setitem(L._COMPONENT_SYMBOLS, "phone_validate", ("phone_valid_arrow",))
    # _has_symbol("phone_validate") is now True, so only _FALLBACK_ONLY keeps it off:
    assert L._has_symbol("phone_validate") is True
    assert L.native_enabled("phone_validate") is False


def test_force_off(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    monkeypatch.setattr(L, "_native", object())
    assert L.native_enabled("phone") is False


def test_profile_component_symbol():
    from goldenflow.core._native_loader import _COMPONENT_SYMBOLS
    assert _COMPONENT_SYMBOLS["profile"] == ("infer_type_list_arrow",)
