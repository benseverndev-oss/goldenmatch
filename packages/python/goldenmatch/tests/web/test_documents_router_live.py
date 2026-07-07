import io
import os

import pytest
from fastapi.testclient import TestClient
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState
from PIL import Image, ImageDraw

pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY_PERSONAL"),
                                reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL")


def test_live_suggest_then_ingest(tmp_path):
    img = Image.new("RGB", (400, 200), "white"); d = ImageDraw.Draw(img)
    d.text((20, 40), "Ada Lovelace", fill="black"); d.text((20, 80), "ada@x.io", fill="black")
    buf = io.BytesIO(); img.save(buf, format="PNG"); png = buf.getvalue()
    client = TestClient(create_app(AppState(project_root=tmp_path, config_path=None,
                                            labels_path=tmp_path / "labels.jsonl")))
    s = client.post("/api/v1/documents/suggest-schema",
                    files={"file": ("card.png", png, "image/png")})
    assert s.status_code == 200 and s.json()["schema"]["fields"]
