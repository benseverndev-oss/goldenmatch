import numpy as np
from goldenmatch.core.embedder import get_embedder


def test_bare_inhouse_is_zero_config():
    emb = get_embedder("inhouse")          # no path, no env -> must NOT raise
    vecs = emb.embed(["International Business Machines", "Margaret Chen"])
    assert isinstance(vecs, np.ndarray) and vecs.shape[0] == 2


def test_inhouse_lexical_signal():
    emb = get_embedder("inhouse")
    v = emb.embed(["Robert Jones", "Bob Jones", "Margaret Chen"])
    n = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
    assert (n[0] @ n[1]) > (n[0] @ n[2])   # char-overlap signal present untrained
