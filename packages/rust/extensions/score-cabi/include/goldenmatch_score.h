/* goldenmatch_score.h -- C ABI over goldenmatch-score-core.
 *
 * Batch string scoring over Arrow's string layout, for JVM (Panama/JNI) and
 * other FFI hosts. The kernel is the same `score_one` that backs the Python
 * extension, the DataFusion FFI UDFs and the WASM build, so a score is
 * identical across all four by construction.
 *
 * BUFFER LAYOUT
 *   Values arrive as Arrow `StringArray` buffers: an int32 offsets array of
 *   length n+1 and a packed UTF-8 data buffer. Slot i is
 *   data[offsets[i] .. offsets[i+1]]. A host holding an Arrow buffer, an
 *   off-heap MemorySegment, or a DirectByteBuffer passes its address straight
 *   in; nothing is copied and nothing is allocated across the boundary.
 *
 * OWNERSHIP
 *   `out` is caller-allocated (n doubles). Nothing crosses the boundary owned,
 *   so there is no free function.
 *
 * NULLS ARE NOT HANDLED HERE, ON PURPOSE
 *   There is no validity bitmap parameter. Null semantics are a policy decision
 *   belonging to the caller. Putting it in the kernel has already cost this
 *   project once: substituting "" for a missing value made null-vs-null score a
 *   perfect 1.0, so two records whose only shared evidence was a shared absence
 *   merged at every threshold. Decide comparability from your own validity
 *   bitmaps and do not ask about pairs you consider unobserved.
 *
 * VERSIONING
 *   Call goldenmatch_score_abi_version() at load time and refuse a mismatch.
 *   goldenmatch_score_scorer_id_count() reports how many scorer ids this build
 *   dispatches, so a host fails loudly on a skew instead of silently scoring an
 *   unknown id as 0.0.
 */
#ifndef GOLDENMATCH_SCORE_H
#define GOLDENMATCH_SCORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Return codes. Negative so a caller can test rc < 0 without a table. */
#define GOLDENMATCH_SCORE_OK            0
#define GOLDENMATCH_SCORE_NULL_POINTER (-1)
#define GOLDENMATCH_SCORE_BAD_LENGTH   (-2)
#define GOLDENMATCH_SCORE_BAD_OFFSETS  (-3)
#define GOLDENMATCH_SCORE_INVALID_UTF8 (-4)

/* Score n pairs elementwise into `out`.
 *
 * scorer_id  dispatched by score_one; ids 0..=14 (see
 *            goldenmatch_score_scorer_id_count).
 * *_offsets  int32[n+1], non-decreasing, within the matching data buffer.
 * *_data     packed UTF-8 bytes.
 * *_data_len length of the matching data buffer, used to bounds-check offsets.
 * n          number of pairs.
 * out        caller-allocated double[n].
 *
 * Returns GOLDENMATCH_SCORE_OK, or a negative code. On error `out` may be
 * partially written; check the return code before reading it.
 */
int32_t goldenmatch_score_pairwise_utf8(uint8_t        scorer_id,
                                        const int32_t *a_offsets,
                                        const uint8_t *a_data,
                                        int64_t        a_data_len,
                                        const int32_t *b_offsets,
                                        const uint8_t *b_data,
                                        int64_t        b_data_len,
                                        int64_t        n,
                                        double        *out);

/* Number of scorer ids this build dispatches. */
uint32_t goldenmatch_score_scorer_id_count(void);

/* ABI version; bumped on any incompatible signature or contract change. */
uint32_t goldenmatch_score_abi_version(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GOLDENMATCH_SCORE_H */
