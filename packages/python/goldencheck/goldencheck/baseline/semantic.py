"""Semantic type inferrer — maps DataFrame columns to semantic type labels."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword definitions per semantic type.
# Rules (same as semantic/classifier.py):
#   keyword ending with '_'  → prefix match  (e.g. "is_" matches "is_active")
#   keyword starting with '_' → suffix match (e.g. "_id" matches "user_id")
#   no marker                → substring match
# ---------------------------------------------------------------------------

_KEYWORD_MAP: dict[str, list[str]] = {
    "email": ["email", "e_mail"],
    "phone": ["phone", "mobile", "tel", "fax", "cell"],
    "person_name": ["first_name", "last_name", "full_name", "given_name", "surname", "fname",
                    "lname", "forename", "person_name"],
    "address": ["address", "street", "city", "state", "suburb", "locality", "province",
                "postcode", "zipcode", "zip_code"],
    "date": ["date", "dob", "birthday", "birthdate", "timestamp", "created_at", "updated_at",
             "deleted_at", "started_at", "ended_at", "expires_at"],
    "currency": ["price", "cost", "amount", "salary", "wage", "revenue", "fee", "charge",
                 "payment", "balance", "budget", "spend", "earning"],
    "identifier": ["_id", "_uuid", "_key", "_ref", "_code"],
    "category": ["category", "type", "kind", "group", "class", "label", "tag", "genre",
                 "segment", "tier", "status"],
    "percentage": ["percent", "pct", "rate", "ratio", "proportion", "share"],
    "boolean": ["is_", "has_", "flag", "active", "enabled", "disabled", "deleted",
                "visible", "hidden", "verified", "confirmed", "approved"],
    "geo": ["latitude", "longitude", "lat", "lon", "lng", "geo", "coord", "location"],
    "url": ["url", "uri", "link", "href", "website", "homepage", "endpoint"],
    "ssn": ["ssn", "sin", "nino", "national_id", "tax_id", "fiscal_id"],
}

# Exemplar phrases used for embedding-based inference (one per type).
_EXEMPLARS: dict[str, list[str]] = {
    "email": ["email address", "user email", "contact email", "customer email"],
    "phone": ["phone number", "telephone", "mobile phone", "contact number"],
    "person_name": ["person name", "full name", "first name", "last name"],
    "address": ["street address", "mailing address", "city", "postal code"],
    "date": ["date", "date of birth", "created date", "updated timestamp"],
    "currency": ["price", "cost in dollars", "salary", "payment amount"],
    "identifier": ["unique identifier", "record id", "primary key", "uuid"],
    "category": ["category", "product type", "item class", "group label"],
    "percentage": ["percentage", "percent value", "rate", "ratio"],
    "boolean": ["boolean flag", "is active", "has subscription", "enabled"],
    "geo": ["latitude", "longitude", "geographic coordinates", "location"],
    "url": ["url link", "website address", "http endpoint", "uri"],
    "ssn": ["social security number", "national id", "tax identification number"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def infer_semantic_types(
    df: pl.DataFrame,
    use_embeddings: bool = True,
) -> dict[str, list[str]]:
    """Infer semantic types for all columns in *df*.

    Returns a dict mapping each detected type name to the list of column names
    assigned to that type. Columns that cannot be classified are omitted.

    Parameters
    ----------
    df:
        Input Polars DataFrame.
    use_embeddings:
        When *True* (default) attempt to use ``sentence_transformers`` for
        embedding-based inference. Falls back to keyword matching on
        ``ImportError``.
    """
    if use_embeddings:
        try:
            return _infer_with_embeddings(df)
        except ImportError:
            logger.debug("sentence_transformers not available — falling back to keywords")

    return _infer_with_keywords(df)


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------


def _infer_with_keywords(df: pl.DataFrame) -> dict[str, list[str]]:
    """Classify columns using keyword matching against column names only."""
    type_to_cols: dict[str, list[str]] = {}

    for col_name in df.columns:
        matched_type = _match_column_keywords(col_name)
        if matched_type is not None:
            type_to_cols.setdefault(matched_type, []).append(col_name)

    return type_to_cols


def _match_column_keywords(col_name: str) -> str | None:
    """Return the first matching type name for *col_name*, or *None*."""
    col_lower = col_name.lower()

    for type_name, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw.endswith("_"):
                # Prefix match
                if col_lower.startswith(kw):
                    return type_name
            elif kw.startswith("_"):
                # Suffix match
                if col_lower.endswith(kw):
                    return type_name
            else:
                # Substring match
                if kw in col_lower:
                    return type_name

    return None


# ---------------------------------------------------------------------------
# Embedding-based inference
# ---------------------------------------------------------------------------


def _infer_with_embeddings(df: pl.DataFrame) -> dict[str, list[str]]:  # noqa: PLR0912
    """Classify columns using sentence-transformer cosine similarity.

    Raises ``ImportError`` if *sentence_transformers* is not installed.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore[import]
    import numpy as np  # type: ignore[import]

    model = SentenceTransformer("all-MiniLM-L6-v2")
    threshold = 0.3

    # Build one centroid embedding per type from exemplar phrases.
    type_embeddings: dict[str, object] = {}
    for type_name, phrases in _EXEMPLARS.items():
        embs = model.encode(phrases, convert_to_numpy=True, show_progress_bar=False)
        type_embeddings[type_name] = embs.mean(axis=0)

    type_to_cols: dict[str, list[str]] = {}

    for col_name in df.columns:
        # Build a short text representation: column name + up to 5 sample values.
        samples: list[str] = []
        try:
            col = df[col_name].drop_nulls()
            if len(col) > 0:
                samples = [str(v) for v in col.head(5).to_list()]
        except Exception as exc:
            logger.debug("Sample extraction failed for %s: %s", col_name, exc)
            pass

        query_text = col_name.replace("_", " ")
        if samples:
            query_text += " " + " ".join(samples[:5])

        query_emb = model.encode([query_text], convert_to_numpy=True, show_progress_bar=False)[0]

        best_type: str | None = None
        best_score: float = threshold

        for type_name, type_emb in type_embeddings.items():
            # Cosine similarity
            denom = np.linalg.norm(query_emb) * np.linalg.norm(type_emb)
            if denom == 0:
                continue
            score = float(np.dot(query_emb, type_emb) / denom)
            if score > best_score:
                best_score = score
                best_type = type_name

        if best_type is not None:
            type_to_cols.setdefault(best_type, []).append(col_name)

    return type_to_cols
