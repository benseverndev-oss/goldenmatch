"""UUIDv7 concurrent uniqueness validation.

The Phase 6 distributed identity stage relies on ``new_entity_id`` being
collision-free across many concurrent producers (each Ray worker mints
new ids for fresh clusters). UUIDv7's design gives this for free
(48-bit ms timestamp + 12 random + 62 random); test anyway since the
kill criterion explicitly assumes no duplicate entity_ids.
"""
from __future__ import annotations

import threading


def test_new_entity_id_unique_under_concurrent_generation() -> None:
    from goldenmatch.identity.store import new_entity_id

    ids: list[str] = []
    lock = threading.Lock()

    def gen() -> None:
        local = [new_entity_id() for _ in range(10_000)]
        with lock:
            ids.extend(local)

    threads = [threading.Thread(target=gen) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 100_000
    assert len(set(ids)) == 100_000, (
        f"UUIDv7 collisions: {len(ids) - len(set(ids))} duplicates"
    )
