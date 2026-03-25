def test_transform_result_has_repr_html():
    from goldenflow.engine.transformer import TransformResult
    assert hasattr(TransformResult, "_repr_html_")

def test_manifest_has_repr_html():
    from goldenflow.engine.manifest import Manifest
    assert hasattr(Manifest, "_repr_html_")

def test_profile_has_repr_html():
    from goldenflow.engine.profiler_bridge import DatasetProfile
    assert hasattr(DatasetProfile, "_repr_html_")
