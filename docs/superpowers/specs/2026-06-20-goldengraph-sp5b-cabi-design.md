# goldengraph SP5b — C ABI design

The C surface of the goldengraph engine. The third binding alongside
`goldengraph-native` (pyo3) and `goldengraph-wasm` (wasm-bindgen). Implements the
SP5b slice outlined in `2026-06-19-goldengraph-wasm-c-bindings-design.md`.

## Goal
Let any C / C++ / FFI-capable host (Go cgo, .NET P/Invoke, Java JNA, Zig, …) drive
the engine — resolve, store, query, communities — without CPython or a JS runtime.

## Approach: reuse, don't reimplement
A standalone crate `goldengraph-cabi` (`crate-type = ["cdylib", "staticlib", "rlib"]`)
path-depends on `goldengraph-wasm` and wraps its seven pub `*_impl` fns in
`extern "C"`. wasm-bindgen is `cfg`-gated to wasm32 in that crate, so on a host
target the dependency pulls only `serde_json` + `goldengraph-core` — no wasm
toolchain. Output is **byte-identical** across Python / WASM / C by construction
(one core, one set of `*_impl` marshalers).

## Boundary: stateless, two-call sizing
Same SP5 principle as WASM — **stateless functions over the snapshot JSON**, no
handles cross the boundary. Every op:
- inputs are NUL-terminated UTF-8 JSON C strings;
- the result JSON is written NUL-terminated into a caller buffer `out`/`out_cap`;
- the return is the content length in bytes (excl NUL);
- **two-call sizing** (the idiomatic C variable-length pattern): call with
  `out=NULL, out_cap=0` to learn the length, allocate `len+1`, call again. Writing
  happens only when `out_cap > len` (room for bytes + NUL), so an undersized
  buffer is never overrun;
- a **negative** return is an error code: `GG_ERR_NULL_ARG` (-1),
  `GG_ERR_BAD_UTF8` (-2), `GG_ERR_OP_FAILED` (-3). The message is on
  `gg_last_error` (a per-thread `thread_local`, same two-call sizing) — the
  sqlite3_errmsg pattern, keeping the success path's `out` clean.

## Surface (mirrors the seven `*_impl` + helpers)
`gg_build_graph`, `gg_neighborhood`, `gg_seeds_by_name`, `gg_communities`,
`gg_store_append` (`""` snapshot = fresh store), `gg_store_as_of`,
`gg_store_history`, plus `gg_last_error` and `gg_abi_version` (returns 1; bumped on
any breaking signature change). Hand-written header `include/goldengraph.h`
(`intptr_t`/`size_t`/`int64_t`/`uint64_t`/`uint8_t` ↔ Rust `isize`/`usize`/`i64`/
`u64`/`u8`).

## Validation — the CI lane is the surface's true gate
Local Windows dev usually has no C compiler, and the local Rust toolchain has
proven flaky, so the `cabi` job in `goldengraph.yml` is where this surface is
checked:
1. `cargo build --release` (the cdylib) + `cargo test --release` — six in-crate
   FFI tests exercise two-call sizing, the NULL/bad-UTF8/op-failed codes,
   `gg_last_error`, the undersized-buffer no-overrun guarantee, and
   byte-identical-to-`store_append_impl` parity (proves nothing is corrupted
   crossing C);
2. `cargo clippy -- -D warnings`;
3. **a real C smoke**: `cc smoke/smoke.c -I include -L target/release
   -lgoldengraph_cabi`, then RUN it under `LD_LIBRARY_PATH` — append → as_of
   round-trip + the error path, from actual C. The compile-link-run is what
   proves the symbols export and the ABI is callable.

Informational lane (not `ci-required`), like the other binding jobs.

## Out of scope
A published artifact (the `.so`/`.a` + header packaged for distribution) and a
`cbindgen`-generated header are follow-ups; the hand-written header is the
source of truth for now. No new engine behavior — pure surfacing.
