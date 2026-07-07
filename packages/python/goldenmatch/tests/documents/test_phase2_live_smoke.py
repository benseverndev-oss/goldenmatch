import os

import pytest
from goldenmatch.documents.suggest import suggest_schema_from_file
from PIL import Image, ImageDraw

pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY_PERSONAL"),
                                reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL")


def test_live_suggest_schema(tmp_path):
    p = tmp_path / "card.png"
    img = Image.new("RGB", (400, 200), "white"); d = ImageDraw.Draw(img)
    d.text((20, 40), "Ada Lovelace", fill="black"); d.text((20, 80), "ada@x.io", fill="black")
    img.save(p)
    schema = suggest_schema_from_file(p)
    assert len(schema.fields) >= 1
