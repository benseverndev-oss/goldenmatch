from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenflow.domains.base import DomainPack

_DOMAINS: dict[str, str] = {
    "people_hr": "goldenflow.domains.people_hr",
    "healthcare": "goldenflow.domains.healthcare",
    "finance": "goldenflow.domains.finance",
    "ecommerce": "goldenflow.domains.ecommerce",
    "real_estate": "goldenflow.domains.real_estate",
    "carceral": "goldenflow.domains.carceral",
}


def load_domain(name: str) -> DomainPack | None:
    module_path = _DOMAINS.get(name.lower().replace("-", "_").replace("/", "_"))
    if module_path is None:
        raise NotImplementedError(f"Domain pack '{name}' is not yet available")
    import importlib
    mod = importlib.import_module(module_path)
    return mod.PACK
