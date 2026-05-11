import threading

from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    DataProfile,
    DomainProfile,
    MatchkeyProfile,
    ScoringProfile,
)
from goldenmatch.core.profile_emitter import (
    ProfileEmitter,
    _NullEmitter,
    current_emitter,
    profile_capture,
)


def test_no_emitter_returns_null_singleton():
    a = current_emitter()
    b = current_emitter()
    assert isinstance(a, _NullEmitter)
    assert a is b


def test_null_emitter_drops_writes():
    e = current_emitter()
    # Must not raise; writes go nowhere
    e.set_blocking(BlockingProfile())
    e.set_scoring(ScoringProfile())
    e.set_cluster(ClusterProfile())
    e.set_data(DataProfile())
    e.set_domain(DomainProfile())
    e.set_matchkey(MatchkeyProfile())


def test_capture_yields_active_emitter():
    with profile_capture() as e:
        assert isinstance(e, ProfileEmitter)
        assert current_emitter() is e


def test_capture_buffers_writes():
    bp = BlockingProfile(keys_used=[["a"]], n_blocks=5)
    with profile_capture() as e:
        e.set_blocking(bp)
    assert e.blocking is bp


def test_capture_clears_after_exit():
    with profile_capture():
        assert not isinstance(current_emitter(), _NullEmitter)
    assert isinstance(current_emitter(), _NullEmitter)


def test_capture_unwinds_on_exception():
    try:
        with profile_capture() as e:
            assert current_emitter() is e
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert isinstance(current_emitter(), _NullEmitter)


def test_nested_capture_uses_inner_emitter():
    with profile_capture() as outer:
        assert current_emitter() is outer
        with profile_capture() as inner:
            assert current_emitter() is inner
            assert inner is not outer
        assert current_emitter() is outer
    assert isinstance(current_emitter(), _NullEmitter)


def test_emitter_isolation_across_threads():
    barrier = threading.Barrier(2)
    seen: dict[str, ProfileEmitter] = {}

    def worker(label: str) -> None:
        with profile_capture():
            barrier.wait()
            # ContextVar gives each thread its own stack
            seen[label] = current_emitter()

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert seen["a"] is not seen["b"]


def test_writes_after_outer_exit_dont_leak():
    with profile_capture() as outer:
        outer.set_blocking(BlockingProfile(n_blocks=1))
    # outer is still a live object but no longer current
    new_bp = BlockingProfile(n_blocks=99)
    current_emitter().set_blocking(new_bp)  # goes to null
    # outer.blocking is still what we wrote inside the context
    assert outer.blocking is not None
    assert outer.blocking.n_blocks == 1
