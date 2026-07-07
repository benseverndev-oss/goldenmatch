from goldenmatch.documents._openai import image_blocks, urllib_transport
from goldenmatch.documents.types import PageImage


def test_image_blocks_emit_data_uris():
    pages = [PageImage(b"\x89PNG\r\n\x1a\n0", 1, 1, 0), PageImage(b"abc", 1, 1, 1)]
    blocks = image_blocks(pages)
    assert len(blocks) == 2
    assert blocks[0]["type"] == "image_url"
    assert blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_urllib_transport_is_callable():
    assert callable(urllib_transport("k"))
