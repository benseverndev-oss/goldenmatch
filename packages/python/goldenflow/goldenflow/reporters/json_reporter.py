from __future__ import annotations

import json

from goldenflow.engine.manifest import Manifest


def manifest_to_json(manifest: Manifest) -> str:
    return json.dumps(manifest.to_dict(), indent=2)
