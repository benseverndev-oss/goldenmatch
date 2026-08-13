"""Live-catalog write-back for the ontology layer — persist emitted RDF.

The ontology emitters (`emit_sameas_graph`, `discover_ontology`,
`emit_ontology_shapes`, `emit_golden_triples`) PRODUCE RDF as a string. This is
the last mile: write that RDF to a **file** catalog, or PUT/POST it to a live
**SPARQL 1.1 Graph Store HTTP Protocol** endpoint (a triple store — Fuseki,
GraphDB, Neptune, …) so "resolve once, every SPARQL query inherits correct
identity" lands in the running catalog, not just a returned string.

GoldenMatch conforms to the Graph Store protocol; it does not implement a triple
store (the replaceable-backend rule). The write path is stdlib-only (`urllib`) —
no rdflib needed to persist an already-serialized string, and no new dependency.
`write_resolved_identity_graph` is the convenience wrapper: emit a crosswalk's
`owl:sameAs`/PROV-O graph and write it in one call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

# RDF serialization format -> HTTP Content-Type (SPARQL Graph Store bodies).
_CONTENT_TYPES = {
    "turtle": "text/turtle",
    "ttl": "text/turtle",
    "nt": "application/n-triples",
    "ntriples": "application/n-triples",
    "xml": "application/rdf+xml",
    "pretty-xml": "application/rdf+xml",
    "json-ld": "application/ld+json",
    "json-ld11": "application/ld+json",
}


def _as_rdf_string(rdf: Any, fmt: str) -> str:
    """Coerce `rdf` to a serialized string: pass a str through; serialize an
    rdflib Graph (or anything with `.serialize`) in `fmt`."""
    if isinstance(rdf, str):
        return rdf
    if hasattr(rdf, "serialize"):
        return rdf.serialize(format=fmt)
    raise TypeError(f"write_ontology_catalog: rdf must be a str or an rdflib Graph, got {type(rdf)!r}")


def write_ontology_catalog(
    rdf: Any,
    dest: str | Path | None = None,
    *,
    endpoint: str | None = None,
    graph_iri: str | None = None,
    mode: str = "replace",
    overwrite: bool = False,
    fmt: str = "turtle",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Persist emitted RDF to a file OR a live SPARQL Graph Store endpoint.

    Exactly one destination:
      - `dest`: write the RDF to this file (refuses to clobber unless `overwrite`);
      - `endpoint`: a SPARQL 1.1 Graph Store HTTP Protocol URL. `mode="replace"`
        does an HTTP PUT (replace the named graph), `mode="merge"` a POST (add
        triples). `graph_iri` selects the named graph (default graph if omitted).

    Args:
        rdf: a serialized RDF string (e.g. `emit_sameas_graph(...)`) or an rdflib Graph.
        fmt: the RDF serialization (`turtle` default) — sets the endpoint Content-Type.

    Returns:
        A small status dict describing what was written (`{"written": path, "bytes": n}`
        or `{"endpoint": ..., "graph": ..., "mode": ..., "status": http_status}`).
    """
    if (dest is None) == (endpoint is None):
        raise ValueError("write_ontology_catalog: pass exactly one of dest= or endpoint=")

    body = _as_rdf_string(rdf, fmt)

    if dest is not None:
        out = Path(dest)
        if out.exists() and not overwrite:
            raise FileExistsError(
                f"write_ontology_catalog: {out} already exists; pass overwrite=True to replace it"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        return {"written": str(out), "bytes": len(body.encode("utf-8"))}

    # --- live SPARQL Graph Store endpoint ---
    key = mode.strip().lower()
    if key not in ("replace", "merge"):
        raise ValueError(f"write_ontology_catalog: mode must be 'replace' or 'merge', got {mode!r}")
    http_method = "PUT" if key == "replace" else "POST"
    content_type = _CONTENT_TYPES.get(fmt.strip().lower(), "text/turtle")

    # Graph Store Protocol: ?graph=<iri> for a named graph, ?default otherwise.
    sep = "&" if "?" in endpoint else "?"
    query = urlencode({"graph": graph_iri}) if graph_iri else "default"
    url = f"{endpoint}{sep}{query}"

    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url, data=body.encode("utf-8"), method=http_method,
                  headers={"Content-Type": content_type})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - caller-supplied catalog URL
            status = getattr(resp, "status", None) or resp.getcode()
    except HTTPError as exc:
        raise RuntimeError(
            f"write_ontology_catalog: {http_method} {url} failed: HTTP {exc.code} {exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"write_ontology_catalog: could not reach {endpoint}: {exc.reason}") from exc

    return {
        "endpoint": endpoint,
        "graph": graph_iri or "urn:x-arq:DefaultGraph",
        "mode": key,
        "method": http_method,
        "status": int(status),
        "bytes": len(body.encode("utf-8")),
    }


def write_resolved_identity_graph(
    crosswalk: Any,
    *,
    dest: str | Path | None = None,
    endpoint: str | None = None,
    graph_iri: str | None = None,
    mode: str = "replace",
    overwrite: bool = False,
    base_iri: str | None = None,
    run_name: str = "resolution",
    target_class: str | None = None,
    fmt: str = "turtle",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Emit a `ResolvedCrosswalk`'s `owl:sameAs` + PROV-O graph and write it to a
    file or a live SPARQL endpoint in one call — "resolve once, push the conformed
    identity into the running catalog." Thin wrapper over `emit_sameas_graph`
    (which needs the `goldenmatch[ontology]` extra) + `write_ontology_catalog`.
    """
    from goldenmatch.semantic.ontology import DEFAULT_BASE_IRI, emit_sameas_graph

    rdf = emit_sameas_graph(
        crosswalk, base_iri=base_iri or DEFAULT_BASE_IRI, run_name=run_name,
        target_class=target_class, fmt=fmt,
    )
    return write_ontology_catalog(
        rdf, dest, endpoint=endpoint, graph_iri=graph_iri, mode=mode,
        overwrite=overwrite, fmt=fmt, timeout=timeout,
    )
