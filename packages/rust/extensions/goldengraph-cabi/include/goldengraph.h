/* goldengraph C ABI -- the C surface of the goldengraph knowledge-graph engine.
 *
 * It wraps the SAME core as the Python (pyo3) and WASM (wasm-bindgen) bindings,
 * so output is byte-identical across surfaces. The boundary is stateless: there
 * are no handles or objects -- the store's snapshot JSON IS the portable state,
 * and every op is `(json, args...) -> json`.
 *
 * Calling convention (ALL op functions):
 *   - inputs are NUL-terminated UTF-8 JSON C strings;
 *   - the result JSON is written NUL-terminated into `out`;
 *   - the return value is the result's content length in bytes, EXCLUDING the
 *     NUL terminator;
 *   - TWO-CALL SIZING: call once with `out=NULL, out_cap=0` to learn the length,
 *     allocate `len + 1` bytes, then call again. (A single call with a buffer
 *     that's already big enough -- `out_cap > len` -- writes on the first call.)
 *   - a NEGATIVE return is an error code (see GG_ERR_*); the human-readable
 *     message is available from gg_last_error() on the same thread.
 *
 * Link against libgoldengraph_cabi (cdylib) or goldengraph_cabi (staticlib).
 */
#ifndef GOLDENGRAPH_H
#define GOLDENGRAPH_H

#include <stddef.h> /* size_t */
#include <stdint.h> /* intptr_t, int64_t, uint64_t, uint32_t, uint8_t */

#ifdef __cplusplus
extern "C" {
#endif

/* Error codes -- any negative return from an op function. */
#define GG_ERR_NULL_ARG (-1)  /* a required pointer argument was NULL */
#define GG_ERR_BAD_UTF8 (-2)  /* a string argument was not valid UTF-8 */
#define GG_ERR_OP_FAILED (-3) /* bad JSON / store error; see gg_last_error() */

/* ABI version of this library (bumped on any breaking signature change). */
uint32_t gg_abi_version(void);

/* SP1: resolve mentions + merge into a graph. */
intptr_t gg_build_graph(const char *mentions_json, const char *edges_json,
                        const char *resolution_json, char *out, size_t out_cap);

/* 1-2 hop retrieval around seed entity ids. */
intptr_t gg_neighborhood(const char *graph_json, const char *seeds_json,
                         uint8_t hops, char *out, size_t out_cap);

/* Seed entity ids by canonical OR surface name. */
intptr_t gg_seeds_by_name(const char *graph_json, const char *name, char *out,
                          size_t out_cap);

/* SP3: label-propagation community partition. */
intptr_t gg_communities(const char *graph_json, char *out, size_t out_cap);

/* SP2: append a batch; "" snapshot opens a fresh store. Returns new snapshot. */
intptr_t gg_store_append(const char *snapshot_json, const char *batch_json,
                         char *out, size_t out_cap);

/* SP2: bi-temporal slice at (valid_t, tx_t) -> graph. */
intptr_t gg_store_as_of(const char *snapshot_json, int64_t valid_t, int64_t tx_t,
                        char *out, size_t out_cap);

/* SP2: per-entity history event log. */
intptr_t gg_store_history(const char *snapshot_json, uint64_t id, char *out,
                          size_t out_cap);

/* The last error message on the CURRENT thread (same two-call sizing). Empty if
 * no error has occurred on this thread. */
intptr_t gg_last_error(char *out, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* GOLDENGRAPH_H */
