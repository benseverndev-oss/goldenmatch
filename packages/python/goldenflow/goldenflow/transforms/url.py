from __future__ import annotations

import re

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    url_canonical_native,
    url_extract_domain_native,
    url_normalize_native,
    url_strip_tracking_native,
    url_strip_www_native,
)

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

# Query-param keys treated as tracking noise for dedup (case-insensitive on the
# KEY). Keep byte-for-byte in lockstep with goldenflow-core's TRACKING_PARAMS
# (url.rs) and the TS fallback (url.ts).
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "utm_cid",
        "utm_reader",
        "utm_referrer",
        "utm_social",
        "utm_social_type",
        "gclid",
        "gclsrc",
        "dclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "msclkid",
        "mc_eid",
        "mc_cid",
        "yclid",
        "igshid",
        "twclid",
        "_ga",
        "_gl",
        "ref",
        "ref_src",
        "spm",
    }
)


def _strip_tracking_query(query: str) -> str:
    """Drop tracking params from a raw query string; keep the rest in order."""
    return "&".join(
        p for p in query.split("&") if p.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    )


# Pure-Python reference for goldenflow-core's ``url`` kernel. MUST reproduce
# the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl).


def _url_normalize_py(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    # Add scheme if missing
    if not _SCHEME_RE.match(val):
        val = "https://" + val
    # Split scheme from rest
    scheme_end = val.index("://") + 3
    scheme = val[:scheme_end].lower()
    rest = val[scheme_end:]
    # Lowercase the domain (everything before first /)
    slash_idx = rest.find("/")
    if slash_idx == -1:
        domain = rest.lower()
        path = ""
    else:
        domain = rest[:slash_idx].lower()
        path = rest[slash_idx:]
    # Strip trailing slash (but not if path is just "/")
    result = scheme + domain + path
    if result.endswith("/") and len(result) > scheme_end + len(domain) + 1:
        result = result.rstrip("/")
    elif result.endswith("/") and path == "/":
        result = result[:-1]
    return result


def _url_extract_domain_py(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    # Strip scheme
    if "://" in val:
        val = val.split("://", 1)[1]
    # Take everything before the first /
    domain = val.split("/", 1)[0]
    return domain.lower() if domain else None


def _url_strip_tracking_py(val: str | None) -> str | None:
    if val is None:
        return None
    t = val.strip()
    if not t:
        return None
    hash_idx = t.find("#")
    if hash_idx == -1:
        main, fragment = t, ""
    else:
        main, fragment = t[:hash_idx], t[hash_idx:]
    q_idx = main.find("?")
    if q_idx == -1:
        return main + fragment
    prefix = main[:q_idx]
    stripped = _strip_tracking_query(main[q_idx + 1 :])
    if not stripped:
        return prefix + fragment
    return f"{prefix}?{stripped}{fragment}"


def _url_strip_www_py(val: str | None) -> str | None:
    if val is None:
        return None
    t = val.strip()
    if not t:
        return None
    scheme_idx = t.find("://")
    if scheme_idx == -1:
        scheme, rest = "", t
    else:
        scheme, rest = t[: scheme_idx + 3], t[scheme_idx + 3 :]
    slash_idx = rest.find("/")
    if slash_idx == -1:
        host, path = rest, ""
    else:
        host, path = rest[:slash_idx], rest[slash_idx:]
    if host[:4].lower() == "www.":
        host = host[4:]
    return scheme + host + path


def _url_canonical_py(val: str | None) -> str | None:
    if val is None:
        return None
    t = val.strip()
    if not t:
        return None
    hash_idx = t.find("#")
    main = t if hash_idx == -1 else t[:hash_idx]
    with_scheme = main if _SCHEME_RE.match(main) else "https://" + main
    scheme_end = with_scheme.find("://") + 3
    scheme = with_scheme[:scheme_end].lower()
    rest = with_scheme[scheme_end:]
    slash_idx = rest.find("/")
    if slash_idx == -1:
        host_raw, path = rest, ""
    else:
        host_raw, path = rest[:slash_idx], rest[slash_idx:]
    host = host_raw.lower()
    if host[:4] == "www.":  # host already lowercased
        host = host[4:]
    q_idx = path.find("?")
    if q_idx == -1:
        pathpart, query_raw = path, ""
    else:
        pathpart, query_raw = path[:q_idx], path[q_idx + 1 :]
    pathpart = pathpart.rstrip("/")
    query = _strip_tracking_query(query_raw)
    result = scheme + host + pathpart
    if query:
        result += "?" + query
    return result


@register_transform(
    name="url_normalize",
    input_types=["url", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def url_normalize(series: pl.Series) -> pl.Series:
    """Normalize URLs: ensure scheme, lowercase domain, strip trailing slash.

    Native-first (goldenflow-core's ``url::url_normalize`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = url_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_url_normalize_py, return_dtype=pl.Utf8)


@register_transform(
    name="url_strip_tracking",
    input_types=["url", "string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def url_strip_tracking(series: pl.Series) -> pl.Series:
    """Remove tracking query params (utm_*, gclid, fbclid, ...), preserving the
    rest verbatim (scheme, host case, remaining query order, #fragment).

    Native-first (goldenflow-core's ``url::url_strip_tracking`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = url_strip_tracking_native()
    if native is not None:
        return native(series)
    return series.map_elements(_url_strip_tracking_py, return_dtype=pl.Utf8)


@register_transform(
    name="url_strip_www",
    input_types=["url", "string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def url_strip_www(series: pl.Series) -> pl.Series:
    """Strip a leading ``www.`` label from the host, preserving scheme, path,
    and host case otherwise.

    Native-first (goldenflow-core's ``url::url_strip_www`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = url_strip_www_native()
    if native is not None:
        return native(series)
    return series.map_elements(_url_strip_www_py, return_dtype=pl.Utf8)


@register_transform(
    name="url_canonical",
    input_types=["url", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def url_canonical(series: pl.Series) -> pl.Series:
    """Composite dedup key: ensure scheme, lowercase scheme+host, strip www.,
    drop #fragment, remove tracking params, strip trailing slashes.

    Native-first (goldenflow-core's ``url::url_canonical`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = url_canonical_native()
    if native is not None:
        return native(series)
    return series.map_elements(_url_canonical_py, return_dtype=pl.Utf8)


@register_transform(
    name="url_extract_domain",
    input_types=["url", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def url_extract_domain(series: pl.Series) -> pl.Series:
    """Extract domain from a URL.

    Native-first (goldenflow-core's ``url::url_extract_domain`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = url_extract_domain_native()
    if native is not None:
        return native(series)
    return series.map_elements(_url_extract_domain_py, return_dtype=pl.Utf8)
