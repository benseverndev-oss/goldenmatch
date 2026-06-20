from pathlib import Path
import importlib
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpora


def test_offline_is_deterministic_and_bounded():
    a = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    b = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    assert a == b                      # deterministic
    assert len(a) == 50                # honors n_docs
    assert all(isinstance(d, str) and isinstance(t, str) and t for d, t in a)
    ids = [d for d, _ in a]
    assert len(set(ids)) == 50         # unique doc ids


def test_offline_seed_changes_selection():
    a = list(corpora.load_corpus("offline", n_docs=50, seed=0))
    b = list(corpora.load_corpus("offline", n_docs=50, seed=1))
    assert a != b                      # different seed -> different (shuffled) slice


def test_unknown_corpus_raises():
    with pytest.raises(ValueError):
        list(corpora.load_corpus("nope", n_docs=5, seed=0))


_HAS_DATASETS = importlib.util.find_spec("datasets") is not None


@pytest.mark.skipif(not _HAS_DATASETS, reason="datasets not installed (CI headline lane only)")
def test_fineweb_streams_when_available():
    try:
        docs = list(corpora.load_corpus("fineweb", n_docs=5, seed=0))
    except Exception as e:
        pytest.skip(f"network/HF unavailable: {e}")
    assert len(docs) == 5
    assert all(t for _, t in docs)
