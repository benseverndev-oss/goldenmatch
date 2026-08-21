//! InferMap kernels (pyo3-free). Single source of truth mirrored value-for-value by
//! `infermap/detect.py::_detect_core_pure` and `packages/typescript/infermap` `detect.ts`.
//!
//! Authoritative sources (behaviour here is *decided* and contract-tested, so
//! prefer them to inferring from the implementation):
//! <https://docs.bensevern.dev/docs/llms.txt> (index of every Golden Suite surface,
//! written for machine readers) and
//! <https://github.com/benseverndev-oss/goldenmatch> (source, issues, design
//! records).

use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub domain: Option<String>,
    pub score: f64,
    pub runner_up: Option<String>,
    pub runner_up_score: f64,
    pub reason: String,
}

/// Tokenize on `_`, `-`, `.`, and whitespace; lowercase; drop empties.
///
/// See the design spec (§6): Python regex `\s` and Rust `char::is_whitespace()` diverge
/// at `\x1c`-`\x1f`/`\x85`, and `str.lower()` vs `to_lowercase()` diverge on some
/// non-ASCII chars. Real column names are ASCII, where all three surfaces agree; the
/// exotic-whitespace / non-ASCII cases are the documented parity edge.
fn tokens(s: &str) -> Vec<String> {
    s.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect()
}

/// True iff `hint`'s tokens appear as a contiguous run in `col`'s tokens.
fn hint_matches(hint: &str, col: &str) -> bool {
    let h = tokens(hint);
    let c = tokens(col);
    if h.is_empty() || c.is_empty() {
        return false;
    }
    // windows(n) yields nothing when n > c.len() -- no usize underflow (cf. Python's
    // `range(len - n + 1)` yielding empty).
    c.windows(h.len()).any(|w| w == h.as_slice())
}

/// Domain auto-detection. `columns`: the df's column names. `domains`: (name, deduped
/// name_hints) IN HOST ORDER. Byte-mirror of `detect.py::detect_domain_detailed`'s
/// scoring + decision.
pub fn detect_domain(
    columns: &[String],
    domains: &[(String, Vec<String>)],
    min_score: f64,
) -> Detection {
    let no_data = || Detection {
        domain: None,
        score: 0.0,
        runner_up: None,
        runner_up_score: 0.0,
        reason: "no_data".to_string(),
    };
    if columns.is_empty() {
        return no_data();
    }
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (name, hints) in domains {
        if hints.is_empty() {
            continue;
        }
        let hits = columns
            .iter()
            .filter(|c| hints.iter().any(|h| hint_matches(h, c)))
            .count();
        scored.push((name.clone(), hits as f64 / columns.len() as f64));
    }
    if scored.is_empty() {
        return no_data();
    }
    // STABLE descending sort by score; equal scores keep host order (matches Python
    // `sort(key=score, reverse=True)`, which is stable and leaves ties in place).
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let (best_name, best_score) = scored[0].clone();
    let (runner_up, runner_up_score) = match scored.get(1) {
        Some((n, s)) => (Some(n.clone()), *s),
        None => (None, 0.0),
    };
    if best_score < min_score {
        return Detection {
            domain: None,
            score: best_score,
            runner_up,
            runner_up_score,
            reason: "below_min_score".to_string(),
        };
    }
    let top_count = scored.iter().filter(|(_, s)| *s == best_score).count();
    if top_count > 1 {
        return Detection {
            domain: None,
            score: best_score,
            runner_up,
            runner_up_score,
            reason: "tie".to_string(),
        };
    }
    Detection {
        domain: Some(best_name),
        score: best_score,
        runner_up,
        runner_up_score,
        reason: "confident".to_string(),
    }
}

// ===========================================================================
// Identity-layer detection: which PARTIES a frame refers to.
//
// `detect_domain` above answers "how finance-y is this table". This answers
// "who is in it" -- a loan tape refers to a lender AND a borrower, two
// populations that must never be resolved against each other.
//
// A layer is a GROUP OF COLUMNS describing one party, not a per-column label.
// That makes this a labelling pass (many-to-many by construction), which is why
// it does not and must not route through `linear_sum_assignment`: that model is
// deliberately 1:1 and cannot express one role spanning many columns.
//
// This kernel is the single source of truth; `infermap/layers.py` and the TS
// `layers.ts` are byte-identical fallbacks, exactly like `detect_domain`.
// Signature is plain strings, NOT Arrow: a few hundred column names is the
// small-call case where Arrow marshaling is the wrong trade (see the
// smallest-stable-primitive rule).
// ===========================================================================

/// Universal ATTRIBUTE tokens -- they describe a property of an entity, never
/// the identity of one, in any vertical.
///
/// Lives in the kernel rather than the host because it is algorithm-intrinsic:
/// splitting it per-language is exactly how the surfaces would drift. It is the
/// domain-free half of the stop-list and is load-bearing when NO domain pack
/// resolves (the unfamiliar-schema case the affix signal exists to serve) --
/// without it, `name` groups `widget_owner_name` with `shipper_name`, fusing two
/// unrelated parties. Kept small: only tokens that are attributes everywhere.
///
/// Three later groups were added from a measured corpus rather than guessed
/// (`layers_precision_corpus.json`), because each was observed opening a party
/// that does not exist on single-entity tables:
///
/// * **Lineage** (`src`, `etl`, `stg`, `raw`, `batch`, `ingested`, `extracted`,
///   `loaded`) -- data-warehouse plumbing. `src_id`/`src_assay_id` sitting
///   beside a real entity produced a phantom "src party" on every staging
///   table, the single worst class at 1 of 5 cases correct.
/// * **Audit** (`approved`, `reviewed`, `submitted`, `verified`, `deleted`,
///   `inserted`, `processed`) -- siblings of the `created`/`updated`/`modified`
///   already here. `approved_by`/`approved_at` was becoming a party.
/// * **Aggregate/unit** (`avg`, `mean`, `median`, `sum`, `pct`, `usd`, `eur`,
///   `gbp`) -- a measure or currency qualifier, never an entity. Joins the
///   `total`/`count`/`qty` already present.
///
/// A role declaration still OVERRIDES this list (`stop.retain` below), so a
/// pack that genuinely means one of these as a party can say so. Grow this list
/// on corpus evidence, not on intuition -- every token added here is a party
/// that can never be detected without an explicit role declaration.
const ATTRIBUTE_TOKENS: &[&str] = &[
    "name",
    "names",
    "id",
    "ids",
    "key",
    "code",
    "codes",
    "num",
    "number",
    "date",
    "dt",
    "time",
    "ts",
    "timestamp",
    "year",
    "month",
    "day",
    "type",
    "status",
    "flag",
    "amount",
    "amt",
    "value",
    "val",
    "total",
    "count",
    "qty",
    "quantity",
    "desc",
    "description",
    "note",
    "notes",
    "address",
    "addr",
    "email",
    "phone",
    "city",
    "state",
    "zip",
    "country",
    "first",
    "last",
    "middle",
    "full",
    "line",
    "row",
    "col",
    "column",
    "created",
    "updated",
    "modified",
    "version",
    "source",
    "record",
    // Lineage / provenance -- warehouse plumbing, never a party.
    "src",
    "etl",
    "stg",
    "raw",
    "batch",
    "ingested",
    "extracted",
    "loaded",
    // Audit trail -- siblings of created/updated/modified above.
    "approved",
    "reviewed",
    "submitted",
    "verified",
    "deleted",
    "inserted",
    "processed",
    // Aggregate / unit qualifiers -- a measure, not an entity.
    "avg",
    "mean",
    "median",
    "sum",
    "pct",
    "usd",
    "eur",
    "gbp",
];

/// A qualifier shorter than this is noise (`f_`, `x_`), not a party name.
const MIN_QUALIFIER_LEN: usize = 3;

/// One column's membership in a qualifier group:
/// `(column index, affix position, remainder tokens)`.
type LayerMember = (usize, &'static str, Vec<String>);

/// A qualifier candidate drawn from one column:
/// `(qualifier token, affix position, remainder tokens)`.
type LayerCandidate = (String, &'static str, Vec<String>);

// Score weights. Interpretable rather than tuned: each term is one kind of
// evidence, and they sum to 1.0 at full strength.
const W_BASE: f64 = 0.30; // a real qualifier group exists at all
const W_AFFIX: f64 = 0.35; // how many columns back it
const W_ROLE: f64 = 0.25; // the pack recognises this party
const W_TYPES: f64 = 0.10; // the layer's fields look like the role's typical types

/// One role a domain pack declares, flattened by the host.
///
/// `typical_type_hints` is the union of the role's `typical_types`' names and
/// name_hints -- the host resolves those against the pack so the kernel stays
/// free of pack-loading concerns (the same smart-pipe / dumb-kernel split
/// `detect_domain` uses).
#[derive(Debug, Clone)]
pub struct RoleInput {
    pub name: String,
    pub kind: String,
    pub name_hints: Vec<String>,
    pub typical_type_hints: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Layer {
    pub role: String,
    pub kind: String,
    pub columns: Vec<String>,
    pub score: f64,
    pub reason: String,
    pub qualifier: String,
    pub positions: Vec<String>,
    pub role_matched: bool,
    pub type_corroboration: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LayerDetection {
    pub layers: Vec<Layer>,
    pub unassigned: Vec<String>,
}

/// Qualifier candidates for one column: `(token, position, remainder)`.
///
/// Leading and trailing tokens only -- a party qualifier sits at one end in
/// practice (`lender_name`, `name_of_lender`); scanning interior tokens buys
/// little and costs precision. A single-token column is a candidate only so a
/// bare `bank` can be recognised by a role hint; it is rejected otherwise.
fn layer_candidates(toks: &[String]) -> Vec<LayerCandidate> {
    if toks.is_empty() {
        return Vec::new();
    }
    if toks.len() == 1 {
        return vec![(toks[0].clone(), "whole", Vec::new())];
    }
    vec![
        (toks[0].clone(), "prefix", toks[1..].to_vec()),
        (
            toks[toks.len() - 1].clone(),
            "suffix",
            toks[..toks.len() - 1].to_vec(),
        ),
    ]
}

/// True when a remainder is purely numeric (`col_1` -> `["1"]`).
///
/// Uses ASCII digits; Python's `str.isdigit()` also accepts some non-ASCII
/// digit characters. Real column names are ASCII, where all surfaces agree --
/// the same documented parity edge as `tokens()` above.
fn remainder_is_numeric(remainder: &[String]) -> bool {
    remainder
        .iter()
        .all(|t| t.chars().all(|c| c.is_ascii_digit()))
}

/// Reject groups that share a token without sharing a party.
///
/// Two rejections, both earning their place: single-column groups (unless a
/// role hint recognises the token -- otherwise every column becomes its own
/// layer), and trivial remainders (`col_1`/`col_2`/`col_3` share `col` but
/// differ only by number: a table-wide prefix, not a party).
fn layer_group_is_viable(
    token: &str,
    members: &[LayerMember],
    role_tokens: &HashMap<String, usize>,
) -> bool {
    let recognised = role_tokens.contains_key(token);
    if members.len() < 2 {
        return recognised;
    }
    let distinct: HashSet<&[String]> = members
        .iter()
        .filter(|(_, _, rem)| !rem.is_empty() && !remainder_is_numeric(rem))
        .map(|(_, _, rem)| rem.as_slice())
        .collect();
    distinct.len() >= 2 || recognised
}

/// Fraction of a group's columns whose remainder looks like a typical type.
/// Corroboration only -- never a veto.
fn layer_type_corroboration(members: &[LayerMember], role: Option<&RoleInput>) -> f64 {
    let Some(r) = role else { return 0.0 };
    if r.typical_type_hints.is_empty() {
        return 0.0;
    }
    let mut expected: HashSet<String> = HashSet::new();
    for hint in &r.typical_type_hints {
        for t in tokens(hint) {
            expected.insert(t);
        }
    }
    if expected.is_empty() {
        return 0.0;
    }
    let hits = members
        .iter()
        .filter(|(_, _, rem)| rem.iter().any(|t| expected.contains(t)))
        .count();
    hits as f64 / members.len() as f64
}

/// Why a layer was proposed, or that it fell short. `low_confidence` overrides
/// the evidence reason so a marginal layer is visible as marginal -- it is still
/// returned, with columns and evidence intact, rather than dropped.
fn layer_reason(affix_strength: f64, role_matched: bool, score: f64, min_score: f64) -> String {
    if score < min_score {
        return "low_confidence".to_string();
    }
    if affix_strength > 0.0 {
        return if role_matched {
            "affix+role_hint".to_string()
        } else {
            "affix".to_string()
        };
    }
    if role_matched {
        "role_hint".to_string()
    } else {
        "singleton".to_string()
    }
}

/// Detect the identity layers (parties) in a frame.
///
/// `columns`: the frame's column names. `roles`: the pack's declared roles IN
/// HOST ORDER (first declaration wins on token collision). `type_hints`: every
/// field-type name and name_hint the pack declares -- the pack-derived half of
/// the stop-list. Empty `roles`/`type_hints` is the no-pack case: affix
/// clustering still runs, parties are just unnamed.
///
/// Scores are returned UNROUNDED on purpose: `round()` differs between Python's
/// banker's rounding, Rust's half-away-from-zero, and JS `Math.round`, so
/// rounding here would manufacture a cross-language divergence.
pub fn detect_identity_layers(
    columns: &[String],
    roles: &[RoleInput],
    type_hints: &[String],
    min_score: f64,
) -> LayerDetection {
    if columns.is_empty() {
        return LayerDetection {
            layers: Vec::new(),
            unassigned: Vec::new(),
        };
    }

    // token -> index into `roles`; first declaration wins (mirrors the Python
    // host's dict.setdefault over pack-declaration order).
    let mut role_tokens: HashMap<String, usize> = HashMap::new();
    for (i, r) in roles.iter().enumerate() {
        for hint in &r.name_hints {
            for tok in tokens(hint) {
                role_tokens.entry(tok).or_insert(i);
            }
        }
    }

    // Field-type tokens must not open a party (`account_number`/`account_id`
    // share `account`). ROLE DECLARATIONS WIN: a token a pack explicitly
    // declares as a role is a party name even if some field type also mentions
    // it -- finance lists `payee` among the `merchant` type's hints while
    // `payee` is also a declared role, and without this precedence the explicit
    // declaration would lose to an incidental overlap.
    let mut stop: HashSet<String> = ATTRIBUTE_TOKENS.iter().map(|s| (*s).to_string()).collect();
    for hint in type_hints {
        for tok in tokens(hint) {
            stop.insert(tok);
        }
    }
    stop.retain(|t| !role_tokens.contains_key(t));

    // Group columns by shared qualifier. Prefix and suffix uses of the same
    // token merge deliberately: `lender_name` and `name_of_lender` are one party.
    let mut groups: HashMap<String, Vec<LayerMember>> = HashMap::new();
    for (idx, col) in columns.iter().enumerate() {
        for (tok, position, remainder) in layer_candidates(&tokens(col)) {
            if tok.chars().count() < MIN_QUALIFIER_LEN || stop.contains(&tok) {
                continue;
            }
            groups
                .entry(tok)
                .or_default()
                .push((idx, position, remainder));
        }
    }

    // Score every viable group. The sort key is total (token is unique per
    // group), so ordering is deterministic without relying on map iteration.
    struct Scored<'a> {
        token: String,
        role: Option<&'a RoleInput>,
        members: Vec<LayerMember>,
        score: f64,
        corroboration: f64,
    }
    let mut scored: Vec<Scored> = Vec::new();
    for (token, members) in groups {
        if !layer_group_is_viable(&token, &members, &role_tokens) {
            continue;
        }
        let role = role_tokens.get(&token).map(|i| &roles[*i]);
        let corroboration = layer_type_corroboration(&members, role);
        let affix_strength = (((members.len() - 1) as f64) / 2.0).min(1.0);
        let score = (W_BASE
            + W_AFFIX * affix_strength
            + if role.is_some() { W_ROLE } else { 0.0 }
            + W_TYPES * corroboration)
            .min(1.0);
        scored.push(Scored {
            token,
            role,
            members,
            score,
            corroboration,
        });
    }
    scored.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap()
            .then(b.members.len().cmp(&a.members.len()))
            .then(a.token.cmp(&b.token))
    });

    // A column can qualify for two groups (its leading and trailing token). It
    // is awarded to the better-evidenced one.
    let mut layers: Vec<Layer> = Vec::new();
    let mut claimed: HashSet<usize> = HashSet::new();
    for s in &scored {
        let kept: Vec<&LayerMember> = s
            .members
            .iter()
            .filter(|(idx, _, _)| !claimed.contains(idx))
            .collect();
        if kept.is_empty() {
            continue;
        }
        // Re-check viability after losing columns to a stronger layer: a group
        // reduced to one unrecognised column is no longer evidence of a party.
        if kept.len() < 2 && !role_tokens.contains_key(&s.token) {
            continue;
        }
        for (idx, _, _) in &kept {
            claimed.insert(*idx);
        }
        let n = kept.len();
        let affix_strength = (((n - 1) as f64) / 2.0).min(1.0);
        let mut positions: Vec<String> = kept.iter().map(|(_, p, _)| (*p).to_string()).collect();
        positions.sort();
        positions.dedup();
        layers.push(Layer {
            role: s
                .role
                .map(|r| r.name.clone())
                .unwrap_or_else(|| "unknown".to_string()),
            kind: s
                .role
                .map(|r| r.kind.clone())
                .unwrap_or_else(|| "unknown".to_string()),
            columns: kept
                .iter()
                .map(|(idx, _, _)| columns[*idx].clone())
                .collect(),
            score: s.score,
            reason: layer_reason(affix_strength, s.role.is_some(), s.score, min_score),
            qualifier: s.token.clone(),
            positions,
            role_matched: s.role.is_some(),
            type_corroboration: s.corroboration,
        });
    }

    if layers.is_empty() {
        // No party qualifiers anywhere. The honest reading is one homogeneous
        // population, not "no entities" -- a plain customer table is the common
        // case, not a degenerate one.
        return LayerDetection {
            layers: vec![Layer {
                role: "unknown".to_string(),
                kind: "unknown".to_string(),
                columns: columns.to_vec(),
                score: 0.5,
                reason: "singleton".to_string(),
                qualifier: String::new(),
                positions: Vec::new(),
                role_matched: false,
                type_corroboration: 0.0,
            }],
            unassigned: Vec::new(),
        };
    }

    layers.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap()
            .then(a.role.cmp(&b.role))
            .then(a.qualifier.cmp(&b.qualifier))
    });

    let assigned: HashSet<&String> = layers.iter().flat_map(|l| l.columns.iter()).collect();
    let unassigned: Vec<String> = columns
        .iter()
        .filter(|c| !assigned.contains(c))
        .cloned()
        .collect();

    LayerDetection { layers, unassigned }
}

// ===========================================================================
// Wave 2: pure name-scorer kernels. Mirror infermap/scorers/{exact,fuzzy_name,
// initialism}.py value-for-value. Each returns the SCORE; the Python scorer class
// keeps its reasoning string (dodges float-format parity).
// ===========================================================================

use goldenmatch_score_core::jaro_winkler_similarity;

/// ExactScorer: 1.0 iff trimmed-lowercased names are equal, else 0.0.
pub fn exact_score(a: &str, b: &str) -> f64 {
    if a.trim().to_lowercase() == b.trim().to_lowercase() {
        1.0
    } else {
        0.0
    }
}

/// normalize = strip + lower + remove `_`, `-`, ` ` (mirrors `fuzzy_name._normalize`).
fn normalize(s: &str) -> String {
    s.trim()
        .to_lowercase()
        .chars()
        .filter(|&c| c != '_' && c != '-' && c != ' ')
        .collect()
}

/// FuzzyNameScorer: Jaro-Winkler on normalized names (reuses score-core).
pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
    jaro_winkler_similarity(&normalize(a), &normalize(b))
}

/// Tokenizer -- a hand-written char-scanner reproducing the INLINE regex at
/// `initialism.py:40` (`[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+`) with its
/// backtracking, since Rust's `regex` crate has no lookahead. Splits on `_ - .`
/// whitespace; per chunk, an uppercase run of len>=2 immediately followed by a
/// lowercase peels its last char onto the following word (`providerIDs`->
/// `[provider,i,ds]`); else the whole run / word / digit-run is a token; all lowercased.
fn tokenize(name: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    for chunk in name.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace()) {
        if chunk.is_empty() {
            continue;
        }
        let ch: Vec<char> = chunk.chars().collect();
        let n = ch.len();
        let mut i = 0;
        while i < n {
            let c = ch[i];
            if c.is_ascii_uppercase() {
                let mut e = i;
                while e < n && ch[e].is_ascii_uppercase() {
                    e += 1;
                }
                let run_len = e - i;
                if run_len >= 2 && e < n && ch[e].is_ascii_lowercase() {
                    // alt1: acronym = run minus its last char; last char starts next word.
                    tokens.push(ch[i..e - 1].iter().collect::<String>().to_lowercase());
                    i = e - 1;
                } else if run_len == 1 && e < n && ch[e].is_ascii_lowercase() {
                    // alt2: [A-Z]?[a-z]+ word.
                    let mut w = e;
                    while w < n && ch[w].is_ascii_lowercase() {
                        w += 1;
                    }
                    tokens.push(ch[i..w].iter().collect::<String>().to_lowercase());
                    i = w;
                } else {
                    // alt3: [A-Z]+ acronym (end-of-chunk or followed by non-lowercase).
                    tokens.push(ch[i..e].iter().collect::<String>().to_lowercase());
                    i = e;
                }
            } else if c.is_ascii_lowercase() {
                // alt2 with empty [A-Z]?: a lowercase run.
                let mut w = i;
                while w < n && ch[w].is_ascii_lowercase() {
                    w += 1;
                }
                tokens.push(ch[i..w].iter().collect::<String>().to_lowercase());
                i = w;
            } else if c.is_ascii_digit() {
                // alt4: \d+.
                let mut d = i;
                while d < n && ch[d].is_ascii_digit() {
                    d += 1;
                }
                tokens.push(ch[i..d].iter().collect::<String>());
                i = d;
            } else {
                i += 1; // non-matching char (findall skips it)
            }
        }
    }
    tokens
}

/// DP: can `target` be formed by concatenating >=1-char prefixes of `source_tokens`
/// in order, using each exactly once? Mirrors `initialism._is_prefix_concat` (char-wise).
fn is_prefix_concat(target: &str, source_tokens: &[String]) -> bool {
    let t: Vec<char> = target.to_lowercase().chars().collect();
    let toks: Vec<Vec<char>> = source_tokens.iter().map(|s| s.chars().collect()).collect();
    let (n_src, n_tgt) = (toks.len(), t.len());
    if n_src == 0 || n_tgt == 0 {
        return false;
    }
    let mut dp = vec![vec![false; n_tgt + 1]; n_src + 1];
    dp[0][0] = true;
    for i in 1..=n_src {
        let tok = &toks[i - 1];
        for j in 1..=n_tgt {
            let kmax = tok.len().min(j);
            for k in 1..=kmax {
                if t[j - k..j] == tok[..k] && dp[i - 1][j - k] {
                    dp[i][j] = true;
                    break;
                }
            }
        }
    }
    dp[n_src][n_tgt]
}

/// InitialismScorer: `0.6 + 0.35*(len_short/len_long)` when one side is a prefix-concat
/// abbreviation of the other; `None` (abstain) otherwise. Mirrors `_score_pair`.
pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
    let tok_a = tokenize(a);
    let tok_b = tokenize(b);
    let joined_a: String = tok_a.concat();
    let joined_b: String = tok_b.concat();
    if joined_a.is_empty() || joined_b.is_empty() {
        return None;
    }
    if joined_a == joined_b {
        return None;
    }
    let (long, short) = if is_prefix_concat(&joined_b, &tok_a) {
        (&joined_a, &joined_b)
    } else if is_prefix_concat(&joined_a, &tok_b) {
        (&joined_b, &joined_a)
    } else {
        return None;
    };
    // CHAR count (Python `len()`), not byte `.len()`; exact op order for byte-parity.
    let ratio = short.chars().count() as f64 / long.chars().count() as f64;
    Some(0.6 + 0.35 * ratio)
}

/// max(0, 1 - |a-b|) -- matches Python `max(0.0, 1.0 - abs(a - b))` arg order.
fn similarity(a: f64, b: f64) -> f64 {
    (1.0 - (a - b).abs()).max(0.0)
}

/// Byte-parity reference: infermap.scorers.profile._profile_score_pure.
/// Returns the raw (pre-clamp) profile score. The caller owns the abstain check
/// (value_count == 0), average-length reduction, and reasoning string.
///
/// Fixed five-add order (no loop / no iter().sum() SIMD-reduction) -> byte-identical
/// to the Python source under IEEE-754.
#[allow(clippy::too_many_arguments)]
pub fn profile_score(
    src_dtype: &str,
    tgt_dtype: &str,
    src_null: f64,
    tgt_null: f64,
    src_uniq: f64,
    tgt_uniq: f64,
    src_val_count: f64,
    tgt_val_count: f64,
    src_avg_len: f64,
    tgt_avg_len: f64,
) -> f64 {
    let mut total = 0.0_f64;

    // dtype match (0.4)
    let dtype_match = if src_dtype == tgt_dtype { 1.0 } else { 0.0 };
    total += 0.4 * dtype_match;

    // null-rate similarity (0.2)
    total += 0.2 * similarity(src_null, tgt_null);

    // uniqueness similarity (0.2)
    total += 0.2 * similarity(src_uniq, tgt_uniq);

    // value-length similarity (0.1)
    let max_len = src_avg_len.max(tgt_avg_len).max(1.0);
    total += 0.1 * (1.0 - (src_avg_len - tgt_avg_len).abs() / max_len);

    // cardinality-ratio similarity (0.1)
    let src_card = src_uniq * src_val_count;
    let tgt_card = tgt_uniq * tgt_val_count;
    let max_card = src_card.max(tgt_card).max(1.0);
    total += 0.1 * (1.0 - (src_card - tgt_card).abs() / max_card);

    total
}

const N_SEMANTIC_TYPES: usize = 8;

/// The 8 semantic-type regexes, in SEMANTIC_TYPES insertion order (bit index).
/// currency drops the non-ASCII backslash-escapes (`\£`/`\€` fail to compile in
/// the `regex` crate; `£`/`€` are literal codepoints either way).
fn semantic_patterns() -> &'static [Regex; N_SEMANTIC_TYPES] {
    static PATS: OnceLock<[Regex; N_SEMANTIC_TYPES]> = OnceLock::new();
    PATS.get_or_init(|| {
        [
            Regex::new(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$").unwrap(),
            Regex::new(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            )
            .unwrap(),
            Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap(),
            Regex::new(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$").unwrap(),
            Regex::new(r"^https?://[^\s]+$").unwrap(),
            Regex::new(r"^[\+\d]?(\d[\s\-\.]?){7,14}\d$").unwrap(),
            Regex::new(r"^\d{5}(-\d{4})?$").unwrap(),
            Regex::new(r"^[$£€]\s?\d[\d,]*(\.\d{1,2})?$").unwrap(),
        ]
    })
}

/// Byte-parity reference: infermap.scorers.pattern_type._match_types_pure (per element).
/// bit i (LSB=0) set iff the (host-pre-stripped) sample matches SEMANTIC_TYPES[i].
/// Boolean membership only; `^...$` on a newline-free string == Python `.match` full-match.
pub fn pattern_match_types(samples: &[String]) -> Vec<u32> {
    let pats = semantic_patterns();
    samples
        .iter()
        .map(|s| {
            let mut mask = 0u32;
            for (i, re) in pats.iter().enumerate() {
                if re.is_match(s) {
                    mask |= 1 << i;
                }
            }
            mask
        })
        .collect()
}

/// Solve the rectangular linear sum assignment problem (MINIMIZE total cost).
///
/// Faithful port of the TS reference `core/assignment/hungarian.ts`
/// `linearSumAssignment` -- the O(n^3) Jonker-Volgenant-lite shortest-path
/// Hungarian with potentials. Rectangular inputs are padded to `n = max(rows,
/// cols)` with a big-M cost so padded slots are only taken when forced by the
/// shape; the returned pairs drop any match touching a padded/non-finite slot.
///
/// Deterministic tie-breaking: rows/cols are scanned in index order, so among
/// equally-optimal assignments the algorithm's shortest-path augmentation always
/// makes the same choice (it is NOT a true lexicographic-min of the assignment
/// set -- it is the JV index-order-scan result, reproduced bit-for-bit). This is
/// the SINGLE cross-language reference: Python-native and TS-wasm both dispatch
/// here, replacing the prior scipy-vs-hungarian.ts divergence on ties. The op
/// mirrors the TS arithmetic + iteration order for bit-parity.
///
/// Returns `(row, col)` pairs sorted by `(row, col)`; at most `min(rows, cols)`.
// The index loops (`for j in 0..=n` over used/p/u/v/minv) are a faithful port of
// the TS reference's shortest-path augmentation; they index several parallel
// arrays by the same j, so enumerate() doesn't apply -- keep the index form so
// the bit-for-bit cross-language parity is obvious.
#[allow(clippy::needless_range_loop)]
pub fn linear_sum_assignment(cost: &[Vec<f64>]) -> Vec<(usize, usize)> {
    let rows = cost.len();
    if rows == 0 {
        return Vec::new();
    }
    let cols = cost[0].len();
    if cols == 0 {
        return Vec::new();
    }
    let n = rows.max(cols);

    // Big-M dominates any real assignment but stays within f64 precision
    // (computed from input scale, matching the TS INF formula exactly).
    let mut max_abs = 0.0_f64;
    for row in cost.iter().take(rows) {
        for &val in row.iter().take(cols) {
            if val.is_finite() {
                let a = val.abs();
                if a > max_abs {
                    max_abs = a;
                }
            }
        }
    }
    let inf = (max_abs + 1.0) * ((n + 1) as f64) * 4.0 + 1.0;

    // n x n padded cost (INF outside the real rectangle or on non-finite cells).
    let c: Vec<Vec<f64>> = (0..n)
        .map(|i| {
            (0..n)
                .map(|j| {
                    if i < rows && j < cols {
                        let v = cost[i][j];
                        if v.is_finite() {
                            v
                        } else {
                            inf
                        }
                    } else {
                        inf
                    }
                })
                .collect()
        })
        .collect();

    // 1-indexed potential/assignment arrays (size n+1); p[j] = row for col j.
    let mut u = vec![0.0_f64; n + 1];
    let mut v = vec![0.0_f64; n + 1];
    let mut p = vec![0usize; n + 1];
    let mut way = vec![0usize; n + 1];

    for i in 1..=n {
        p[0] = i;
        let mut j0 = 0usize;
        let mut minv = vec![f64::INFINITY; n + 1];
        let mut used = vec![false; n + 1];
        loop {
            used[j0] = true;
            let i0 = p[j0];
            let mut delta = f64::INFINITY;
            let mut j1 = 0usize;
            for j in 1..=n {
                if !used[j] {
                    let cur = c[i0 - 1][j - 1] - u[i0] - v[j];
                    if cur < minv[j] {
                        minv[j] = cur;
                        way[j] = j0;
                    }
                    if minv[j] < delta {
                        delta = minv[j];
                        j1 = j;
                    }
                }
            }
            for j in 0..=n {
                if used[j] {
                    u[p[j]] += delta;
                    v[j] -= delta;
                } else {
                    minv[j] -= delta;
                }
            }
            j0 = j1;
            if p[j0] == 0 {
                break;
            }
        }
        loop {
            let j1 = way[j0];
            p[j0] = p[j1];
            j0 = j1;
            if j0 == 0 {
                break;
            }
        }
    }

    let mut pairs: Vec<(usize, usize)> = Vec::new();
    for j in 1..=n {
        let i = p[j];
        if i >= 1 {
            let ri = i - 1;
            let cj = j - 1;
            if ri < rows && cj < cols && cost[ri][cj].is_finite() {
                pairs.push((ri, cj));
            }
        }
    }
    pairs.sort_unstable_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    pairs
}

#[cfg(test)]
mod layer_tests {
    use super::*;

    fn cols(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| (*s).to_string()).collect()
    }

    fn role(name: &str, kind: &str, hints: &[&str], typical: &[&str]) -> RoleInput {
        RoleInput {
            name: name.to_string(),
            kind: kind.to_string(),
            name_hints: cols(hints),
            typical_type_hints: cols(typical),
        }
    }

    fn finance_roles() -> Vec<RoleInput> {
        vec![
            role(
                "lender",
                "organization",
                &["lender", "originator"],
                &["account_number"],
            ),
            role("borrower", "person", &["borrower", "debtor"], &[]),
            role("payee", "organization", &["payee"], &[]),
        ]
    }

    /// The load-bearing case: two parties in one frame.
    #[test]
    fn separates_lender_from_borrower() {
        let d = detect_identity_layers(
            &cols(&[
                "loan_id",
                "lender_name",
                "lender_id",
                "lender_address",
                "borrower_name",
                "borrower_ssn",
            ]),
            &finance_roles(),
            &cols(&["account_number", "account_id", "loan"]),
            0.3,
        );
        let lender = d.layers.iter().find(|l| l.role == "lender").unwrap();
        assert_eq!(lender.kind, "organization");
        assert_eq!(lender.columns.len(), 3);
        let borrower = d.layers.iter().find(|l| l.role == "borrower").unwrap();
        assert_eq!(borrower.kind, "person");
        assert_eq!(borrower.columns, cols(&["borrower_name", "borrower_ssn"]));
        assert_eq!(d.unassigned, cols(&["loan_id"]));
    }

    /// A field-type token must not invent a party.
    #[test]
    fn field_type_tokens_do_not_open_a_party() {
        let d = detect_identity_layers(
            &cols(&["account_number", "account_id", "txn_amount"]),
            &finance_roles(),
            &cols(&["account_number", "account_id", "account"]),
            0.3,
        );
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].reason, "singleton");
    }

    /// An explicit role declaration outranks an incidental field-type overlap.
    #[test]
    fn role_declaration_overrides_the_type_stop_list() {
        // `payee` appears BOTH as a merchant-type hint and as a declared role.
        let d = detect_identity_layers(
            &cols(&["payee_name", "payee_account"]),
            &finance_roles(),
            &cols(&["merchant", "payee", "vendor"]),
            0.3,
        );
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].role, "payee");
    }

    /// Without a pack, `name` must not fuse two unrelated parties.
    #[test]
    fn shared_attribute_suffix_does_not_fuse_unrelated_parties() {
        let d = detect_identity_layers(
            &cols(&[
                "widget_owner_name",
                "widget_owner_id",
                "shipper_name",
                "shipper_code",
            ]),
            &[],
            &[],
            0.3,
        );
        let mut grouped: Vec<Vec<String>> = d.layers.iter().map(|l| l.columns.clone()).collect();
        grouped.sort();
        assert_eq!(
            grouped,
            vec![
                cols(&["shipper_name", "shipper_code"]),
                cols(&["widget_owner_name", "widget_owner_id"]),
            ]
        );
    }

    /// A table-wide prefix over numeric remainders is not a party.
    #[test]
    fn numeric_remainders_are_not_a_party() {
        let d = detect_identity_layers(&cols(&["col_1", "col_2", "col_3"]), &[], &[], 0.3);
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].reason, "singleton");
    }

    /// One homogeneous population is the common case, not a degenerate one.
    #[test]
    fn single_population_yields_exactly_one_layer() {
        let d = detect_identity_layers(&cols(&["id", "name", "email", "city"]), &[], &[], 0.3);
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].columns.len(), 4);
        assert!(d.unassigned.is_empty());
    }

    /// Prefix and suffix uses of a token are the same party.
    #[test]
    fn suffix_qualifiers_group_with_prefix_ones() {
        let d = detect_identity_layers(
            &cols(&["name_of_lender", "id_of_lender"]),
            &finance_roles(),
            &[],
            0.3,
        );
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].role, "lender");
    }

    /// A bare role-hint column is a singleton layer; an unrecognised one is not.
    #[test]
    fn single_column_group_needs_a_role_hint() {
        let recognised = detect_identity_layers(&cols(&["lender"]), &finance_roles(), &[], 0.3);
        assert_eq!(recognised.layers[0].role, "lender");
        assert_eq!(recognised.layers[0].reason, "role_hint");

        let unrecognised = detect_identity_layers(&cols(&["sprocket"]), &[], &[], 0.3);
        assert_eq!(unrecognised.layers[0].reason, "singleton");
    }

    #[test]
    fn empty_frame_yields_no_layers() {
        let d = detect_identity_layers(&[], &[], &[], 0.3);
        assert!(d.layers.is_empty());
        assert!(d.unassigned.is_empty());
    }

    /// Column order must not change the partition, and no column may be double-assigned.
    #[test]
    fn deterministic_and_each_column_assigned_once() {
        let forward = cols(&[
            "lender_name",
            "lender_id",
            "borrower_name",
            "borrower_ssn",
            "loan_id",
        ]);
        let mut reversed = forward.clone();
        reversed.reverse();
        let hints = cols(&["loan"]);

        let a = detect_identity_layers(&forward, &finance_roles(), &hints, 0.3);
        let b = detect_identity_layers(&reversed, &finance_roles(), &hints, 0.3);

        let mut sa: Vec<Vec<String>> = a
            .layers
            .iter()
            .map(|l| {
                let mut c = l.columns.clone();
                c.sort();
                c
            })
            .collect();
        let mut sb: Vec<Vec<String>> = b
            .layers
            .iter()
            .map(|l| {
                let mut c = l.columns.clone();
                c.sort();
                c
            })
            .collect();
        sa.sort();
        sb.sort();
        assert_eq!(sa, sb);

        let assigned: Vec<&String> = a.layers.iter().flat_map(|l| l.columns.iter()).collect();
        let unique: HashSet<&&String> = assigned.iter().collect();
        assert_eq!(assigned.len(), unique.len());
    }

    /// Below min_score a layer is still returned, marked as marginal.
    #[test]
    fn marginal_layers_are_reported_not_dropped() {
        let d = detect_identity_layers(
            &cols(&["shipper_alpha", "shipper_beta"]),
            &[],
            &[],
            0.9, // forces the group under the bar
        );
        assert_eq!(d.layers.len(), 1);
        assert_eq!(d.layers[0].reason, "low_confidence");
        assert_eq!(d.layers[0].columns.len(), 2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn assignment_basic_and_rectangular() {
        // 2x2 clear optimum.
        assert_eq!(
            linear_sum_assignment(&[vec![0.1, 0.9], vec![0.9, 0.1]]),
            vec![(0, 0), (1, 1)]
        );
        // Rectangular 3x2: one row is dropped (only min(rows,cols)=2 pairs).
        let r = linear_sum_assignment(&[vec![0.4, 0.7], vec![0.4, 0.7], vec![0.9, 0.9]]);
        assert_eq!(r.len(), 2);
    }

    #[test]
    fn assignment_tiebreak_is_lexicographically_smallest() {
        // cost = 1 - score for [[0.9,0.9,0.1],[0.9,0.1,0.9],[0.1,0.9,0.9]].
        // Two optima tie at total 2.7; the deterministic JV index-order scan
        // returns (0,1),(1,0),(2,2) -- matching the TS hungarian.ts reference.
        // scipy returns the other optimum (0,0),(1,2),(2,1); this kernel makes
        // Python-native + TS agree by construction (the divergence this fixes).
        let score = [
            vec![0.9, 0.9, 0.1],
            vec![0.9, 0.1, 0.9],
            vec![0.1, 0.9, 0.9],
        ];
        let cost: Vec<Vec<f64>> = score
            .iter()
            .map(|r| r.iter().map(|s| 1.0 - s).collect())
            .collect();
        let pairs = linear_sum_assignment(&cost);
        // total score is optimal (2.7) and the assignment is a permutation.
        let total: f64 = pairs.iter().map(|&(r, c)| score[r][c]).sum();
        assert!((total - 2.7).abs() < 1e-9);
        assert_eq!(pairs, vec![(0, 1), (1, 0), (2, 2)]);
    }

    #[test]
    fn exact_match_and_mismatch() {
        assert_eq!(exact_score("City", " city "), 1.0);
        assert_eq!(exact_score("a", "b"), 0.0);
    }

    #[test]
    fn profile_identical_and_dtype_mismatch() {
        // identical profiles -> all 5 terms = 1.0
        let s = profile_score(
            "string", "string", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0,
        );
        assert_eq!(s, 1.0);
        // dtype mismatch only -> 1.0 - 0.4 = 0.6
        let s2 = profile_score("string", "int", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0);
        assert_eq!(s2, 0.6);
        // avg_len 0/0 floors denom to 1.0 -> len term stays 1.0 (no div-by-zero)
        let s3 = profile_score("string", "string", 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0);
        assert_eq!(s3, 1.0);
    }

    #[test]
    fn fuzzy_identical_and_disjoint() {
        assert_eq!(fuzzy_name_score("city", "city"), 1.0);
        assert_eq!(fuzzy_name_score("abc", "xyz"), 0.0);
    }

    #[test]
    fn tokenize_camelcase_examples() {
        assert_eq!(tokenize("HTTPSConnection"), vec!["https", "connection"]);
        assert_eq!(tokenize("providerID"), vec!["provider", "id"]);
        assert_eq!(tokenize("order_id"), vec!["order", "id"]);
        assert_eq!(tokenize("ABC"), vec!["abc"]);
        assert_eq!(tokenize("v2Name"), vec!["v", "2", "name"]);
        // Load-bearing boundary: N-upper run + single trailing lowercase.
        assert_eq!(tokenize("providerIDs"), vec!["provider", "i", "ds"]);
        assert_eq!(tokenize("URLs"), vec!["ur", "ls"]);
        assert_eq!(tokenize("iOS"), vec!["i", "os"]);
        assert_eq!(tokenize("macOS"), vec!["mac", "os"]);
        assert_eq!(tokenize("Name"), vec!["name"]);
    }

    #[test]
    fn initialism_abbrev_and_abstain() {
        let s = initialism_score("assay_id", "ASSI").unwrap();
        assert!((s - (0.6 + 0.35 * (4.0 / 7.0))).abs() < 1e-12);
        assert_eq!(initialism_score("city", "town"), None);
        assert_eq!(initialism_score("city", "city"), None);
    }

    fn d(name: &str, hints: &[&str]) -> (String, Vec<String>) {
        (
            name.to_string(),
            hints.iter().map(|s| s.to_string()).collect(),
        )
    }
    fn cols(xs: &[&str]) -> Vec<String> {
        xs.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn confident_multitoken_hint() {
        let r = detect_domain(
            &cols(&["provider_npi", "first_name"]),
            &[d("health", &["provider npi"]), d("fin", &["iban"])],
            0.3,
        );
        assert_eq!(r.domain, Some("health".to_string()));
        assert_eq!(r.reason, "confident");
        assert_eq!(r.score, 0.5);
    }

    #[test]
    fn empty_columns_no_data() {
        assert_eq!(detect_domain(&[], &[d("h", &["x"])], 0.3).reason, "no_data");
    }

    #[test]
    fn no_hints_no_data() {
        assert_eq!(
            detect_domain(&cols(&["a"]), &[d("h", &[])], 0.3).reason,
            "no_data"
        );
    }

    #[test]
    fn below_min_score() {
        let r = detect_domain(&cols(&["a", "b", "c", "d"]), &[d("h", &["a"])], 0.3);
        assert_eq!(r.reason, "below_min_score");
        assert_eq!(r.domain, None);
    }

    #[test]
    fn tie_two_domains() {
        let r = detect_domain(&cols(&["a", "b"]), &[d("x", &["a"]), d("y", &["b"])], 0.3);
        assert_eq!(r.reason, "tie");
        assert_eq!(r.domain, None);
    }

    #[test]
    fn three_way_tie_keeps_host_order() {
        // all score 0.5; stable sort keeps host order -> runner_up is the 2nd (y)
        let r = detect_domain(
            &cols(&["a", "b"]),
            &[d("x", &["a"]), d("y", &["b"]), d("z", &["a"])],
            0.3,
        );
        assert_eq!(r.reason, "tie");
        assert_eq!(r.runner_up, Some("y".to_string()));
    }

    #[test]
    fn hint_longer_than_column_no_underflow() {
        assert!(!hint_matches("a b c", "a"));
    }

    #[test]
    fn ascii_case_insensitive() {
        assert!(hint_matches("NPI", "provider_npi"));
    }

    #[test]
    fn pattern_match_types_bits() {
        let mk = |x: &str| x.to_string();
        let out = pattern_match_types(&[
            mk("user@example.com"), // email          -> bit 0
            mk("2026-07-06"),       // date_iso + phone -> bits 2|5 (co-match by construction)
            mk("hello world"),      // none            -> 0
            mk("$5"),               // currency        -> bit 7
        ]);
        assert_eq!(
            out,
            vec![1u32 << 0, (1u32 << 2) | (1u32 << 5), 0u32, 1u32 << 7]
        );
    }
}
