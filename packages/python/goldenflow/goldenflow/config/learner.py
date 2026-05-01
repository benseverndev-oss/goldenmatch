from __future__ import annotations

from pathlib import Path


from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.connectors.file import read_file
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.engine.selector import select_transforms


def learn_config(path: Path) -> GoldenFlowConfig:
    """Profile a data file and generate a config from auto-detected transforms."""
    df = read_file(path)
    profile = profile_dataframe(df, file_path=str(path))

    transforms: list[TransformSpec] = []
    for col_profile in profile.columns:
        selected = select_transforms(col_profile)
        if selected:
            ops = [t.name for t in selected]
            transforms.append(TransformSpec(column=col_profile.name, ops=ops))

    return GoldenFlowConfig(
        source=str(path),
        transforms=transforms,
    )
