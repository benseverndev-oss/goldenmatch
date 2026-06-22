/* goldenprofile.h -- C ABI for the goldenprofile Virtual Fingerprint engine.
 *
 * Cross-document entity resolution over rigid, LLM-synthesized profiles. Wraps
 * the SAME pyo3-free core as the Python (goldenprofile-native) and WASM
 * (goldenprofile-wasm) surfaces, so results are byte-identical across all three.
 *
 * Boundary is JSON. Request:
 *   { "profiles":  [ {"kind":"node"|"edge","name":..,"category":..,
 *                     "anchor":..,"attribute":..}, ... ],
 *     "embeddings": [[f64,...], ...]   // optional; one row per profile
 *     "config":     { ... }            // optional; partial overrides allowed
 *   }
 * Response:
 *   { "clusters": [[usize,...], ...], "edges": [ {a,b,score:{...}}, ... ] }
 */
#ifndef GOLDENPROFILE_H
#define GOLDENPROFILE_H

#ifdef __cplusplus
extern "C" {
#endif

/* Resolve a NUL-terminated JSON request into a NUL-terminated JSON result.
 * Returns NULL on NULL / invalid-UTF-8 / malformed-request input. The returned
 * pointer is owned by the caller and MUST be freed with
 * goldenprofile_string_free exactly once. */
char *goldenprofile_resolve_json(const char *request);

/* Free a string returned by goldenprofile_resolve_json. NULL is a no-op. */
void goldenprofile_string_free(char *s);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GOLDENPROFILE_H */
