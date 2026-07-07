import goldenmatch.mcp.server as srv


def test_document_tools_registered_in_all_surfaces():
    names = {t.name for t in srv.TOOLS}
    assert {"documents_suggest_schema", "documents_ingest"} <= names
    # dispatch routes doc tools without falling through to the base handler
    import goldenmatch.mcp.document_tools as dt
    assert dt.DOCUMENT_TOOL_NAMES <= {t.name for t in srv.TOOLS}
