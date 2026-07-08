"""Tunables for denial-constraint discovery (Stage 1)."""
MAX_LITERAL_CARD = 50      # only mine equality literals on columns with <= this many distinct values
MIN_SUPPORT = 0.01         # a literal/predicate must apply to >= this fraction of rows
MAX_PREDICATES = 64        # per evidence pass; a satisfaction mask fits one u64
DEFAULT_SAMPLE = 2000      # S rows for the pairwise pass
VALIDATION_SAMPLE = 20000  # bounded sample for cross-tuple g1 validation
DEFAULT_EPS = 0.05         # g1 threshold: keep DCs violated by <= eps of elements
MAX_CONSTRAINTS = 20       # top-N reported
MIN_ROWS = 100             # skip discovery below this row count
# Max predicates per DC. This is BOTH a tractability bound (discover is
# O(|distinct masks| * C(active, arity)); arity 3-4 on high-entropy evidence
# costs tens of seconds) AND an interestingness bound (conjunctions of 3-4
# independent comparison predicates coincidentally fall below eps on random data,
# manufacturing spurious "constraints"). The high-value DCs are binary
# (¬(A ∧ B), e.g. ¬(status='shipped' ∧ ship<order)). Single source of truth for
# both discover.py's default and the orchestrator; power users can opt into 3+
# via the discover_denial_constraints(arity_bound=…) kwarg.
MAX_REPORT_ARITY = 2
