from goldenmatch.documents.extractor import Extractor, FakeExtractor
from goldenmatch.documents.types import ExtractResult, Field, PageImage, TargetSchema


def test_fake_extractor_returns_scripted_result_and_satisfies_protocol():
    schema = TargetSchema([Field("full_name")])
    canned = ExtractResult(rows=[])
    fake = FakeExtractor([canned])
    assert isinstance(fake, Extractor)
    out = fake.extract([PageImage(b"x", 1, 1, 0)], schema)
    assert out is canned
