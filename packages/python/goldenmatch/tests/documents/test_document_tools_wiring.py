import goldenmatch.mcp.server as srv


def test_document_tools_registered_in_all_surfaces():
    names = {t.name for t in srv.TOOLS}
    assert {"documents_suggest_schema", "documents_ingest"} <= names
    # dispatch routes doc tools without falling through to the base handler
    import goldenmatch.mcp.document_tools as dt
    assert dt.DOCUMENT_TOOL_NAMES <= {t.name for t in srv.TOOLS}


def test_dispatch_routes_document_tools(monkeypatch):
    monkeypatch.setattr(srv, "handle_document_tool", lambda name, args: {"routed": name})
    out = srv.dispatch("documents_ingest", {"paths": [], "schema": {"fields": [{"name": "x"}]}})
    assert out == {"routed": "documents_ingest"}
