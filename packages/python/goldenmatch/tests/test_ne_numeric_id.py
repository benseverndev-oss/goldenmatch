"""#3012: negative evidence on a numeric identifier compares exactly.

The fallback `ensemble` scorer read "1003" and "1004" as similar text (3 of 4
characters shared), so two different account numbers cleared the NE threshold
and no penalty fired -- exactly the disagreement NE exists to catch.
"""

import pyarrow as pa
from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig, MatchkeyField
from goldenmatch.core import scorer
from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
from goldenmatch.core.complexity_profile import ColumnPrior


def _ne_scorer(values: list) -> str | None:
    # Shared values (not a row key), so the column is eligible for NE.
    df = pa.table({"email": ["a@x.com"] * len(values), "acct": values})
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_email",
                type="exact",
                threshold=None,
                fields=[MatchkeyField(field="email", transforms=[], scorer=None, weight=None)],
            )
        ]
    )
    priors = {"acct": ColumnPrior(identity_score=0.9, corruption_score=0.0)}
    out = promote_negative_evidence(cfg, df, priors)
    for mk in out.matchkeys:
        for ne in mk.negative_evidence or []:
            if ne.field == "acct":
                return ne.scorer
    return None


def test_integer_and_digit_string_ids_get_the_exact_scorer():
    assert _ne_scorer([1003, 1003, 1004, 1005, 1005, 1006]) == "exact"
    assert _ne_scorer(["00123", "00123", "00124", "00125", "00125", None]) == "exact"


def test_text_and_float_columns_keep_the_fallback_scorer():
    assert _ne_scorer(["ab", "ab", "cd", "ef", "ef", "gh"]) == "ensemble"
    assert _ne_scorer([10.5, 10.5, 11.25, 12.0, 12.0, 13.5]) == "ensemble"


def test_exact_ne_penalises_adjacent_account_numbers():
    from goldenmatch.config.schemas import NegativeEvidenceField

    scorer._NE_BROKEN.clear()
    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.8,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="acct", scorer="exact", transforms=[], threshold=0.4, penalty=0.3)
        ],
    )
    assert scorer._apply_negative_evidence(mk, {"acct": (1003, 1004)}) == 0.3
    assert scorer._apply_negative_evidence(mk, {"acct": (1003, 1003)}) == 0.0
