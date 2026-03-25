from __future__ import annotations

from pydantic import BaseModel

from goldenflow.config.schema import GoldenFlowConfig


class DomainPack(BaseModel):
    name: str
    description: str
    transforms: list[str] = []
    default_config: GoldenFlowConfig = GoldenFlowConfig()
