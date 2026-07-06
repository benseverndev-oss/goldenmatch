"""infermap-native -- optional Rust/PyO3 acceleration kernels for infermap.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't import it
directly; ``infermap`` discovers it through ``infermap._native_loader`` when present
and falls back to its pure-Python paths when it isn't. Mirrors goldencheck's native /
goldencheck-native split: the frontend (``infermap``) stays a pure-Python wheel, the
compiled runtime ships separately and is pulled in via ``pip install infermap[native]``.
"""
