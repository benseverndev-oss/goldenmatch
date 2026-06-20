/* C smoke test for the goldengraph C ABI.
 *
 * Proves the surface from ACTUAL C: the symbols link, the two-call sizing
 * protocol works, a real append->as_of round-trips, and the error path sets
 * gg_last_error. Exits non-zero (and prints) on any failure. Run by the
 * `cabi` CI job after linking against the cdylib.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "goldengraph.h"

/* Run append via two-call sizing; returns a malloc'd NUL-terminated string
 * (caller frees) or NULL on a negative (error) return. */
static char *call_append(const char *snapshot, const char *batch) {
    intptr_t n = gg_store_append(snapshot, batch, NULL, 0);
    if (n < 0) return NULL;
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) return NULL;
    intptr_t m = gg_store_append(snapshot, batch, buf, (size_t)n + 1);
    if (m != n) { free(buf); return NULL; }
    return buf;
}

static char *call_as_of(const char *snapshot, int64_t v, int64_t t) {
    intptr_t n = gg_store_as_of(snapshot, v, t, NULL, 0);
    if (n < 0) return NULL;
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) return NULL;
    intptr_t m = gg_store_as_of(snapshot, v, t, buf, (size_t)n + 1);
    if (m != n) { free(buf); return NULL; }
    return buf;
}

#define FAIL(msg)                                                              \
    do {                                                                       \
        fprintf(stderr, "SMOKE FAIL: %s\n", msg);                              \
        return 1;                                                              \
    } while (0)

static const char *BATCH =
    "{\"entities\":["
    "{\"local_id\":0,\"canonical_name\":\"Acme\",\"typ\":\"org\","
    "\"surface_names\":[\"Acme\"],\"record_keys\":[\"k0\"]},"
    "{\"local_id\":1,\"canonical_name\":\"Rocket\",\"typ\":\"product\","
    "\"surface_names\":[\"Rocket\"],\"record_keys\":[\"k1\"]}],"
    "\"edges\":[{\"subj_local\":0,\"predicate\":\"made\",\"obj_local\":1,"
    "\"valid_from\":100,\"valid_to\":null,\"source_refs\":[\"doc\"]}],"
    "\"ingested_at\":100}";

int main(void) {
    if (gg_abi_version() != 1) FAIL("unexpected ABI version");

    /* append into a fresh store, then read a bi-temporal slice */
    char *snap = call_append("", BATCH);
    if (!snap) FAIL("gg_store_append returned an error");
    if (!strstr(snap, "Acme") || !strstr(snap, "Rocket"))
        FAIL("snapshot missing expected entities");

    char *graph = call_as_of(snap, 1000, 1000);
    if (!graph) FAIL("gg_store_as_of returned an error");
    if (!strstr(graph, "Acme") || !strstr(graph, "Rocket") || !strstr(graph, "made"))
        FAIL("as_of slice missing expected entities/edge");

    /* error path: bad JSON -> negative code + a populated last-error */
    intptr_t rc = gg_store_append("", "{ not valid json", NULL, 0);
    if (rc != GG_ERR_OP_FAILED) FAIL("bad JSON did not return GG_ERR_OP_FAILED");
    intptr_t elen = gg_last_error(NULL, 0);
    if (elen <= 0) FAIL("gg_last_error empty after a failure");

    /* null arg -> the null-arg code */
    if (gg_store_append(NULL, BATCH, NULL, 0) != GG_ERR_NULL_ARG)
        FAIL("NULL arg did not return GG_ERR_NULL_ARG");

    free(snap);
    free(graph);
    printf("SMOKE OK\n");
    return 0;
}
