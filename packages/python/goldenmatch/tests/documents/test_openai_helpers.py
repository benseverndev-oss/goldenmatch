import pytest
from goldenmatch.documents._openai import image_blocks, parse_message_text, urllib_transport
from goldenmatch.documents.types import PageImage


def test_image_blocks_emit_data_uris():
    pages = [PageImage(b"\x89PNG\r\n\x1a\n0", 1, 1, 0), PageImage(b"abc", 1, 1, 1)]
    blocks = image_blocks(pages)
    assert len(blocks) == 2
    assert blocks[0]["type"] == "image_url"
    assert blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_urllib_transport_is_callable():
    assert callable(urllib_transport("k"))


def test_parse_message_text_strips_fence():
    resp = {"choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}]}
    assert parse_message_text(resp) == '{"a": 1}'


def test_parse_message_text_truncated_is_error():
    resp = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
    with pytest.raises(ValueError, match="truncat"):
        parse_message_text(resp)


def test_parse_message_text_missing_choices_is_error():
    with pytest.raises(ValueError):
        parse_message_text({"nope": True})
