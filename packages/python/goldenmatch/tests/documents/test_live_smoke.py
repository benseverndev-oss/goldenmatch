import os

import pytest
from goldenmatch.documents import ingest_documents
from goldenmatch.documents.types import Field, TargetSchema
from PIL import Image, ImageDraw

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY_PERSONAL"),
    reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL to run")


def test_live_extracts_a_synthetic_card(tmp_path):
    p = tmp_path / "card.png"
    img = Image.new("RGB", (400, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 40), "Ada Lovelace", fill="black")
    d.text((20, 80), "ada@analytical.io", fill="black")
    img.save(p)
    schema = TargetSchema([Field("full_name"), Field("email", kind="email")])
    df = ingest_documents([p], schema, backend="vlm", model="gpt-4o")
    assert df.height >= 1
    assert "ada@analytical.io" in " ".join(df["email"].to_list())
