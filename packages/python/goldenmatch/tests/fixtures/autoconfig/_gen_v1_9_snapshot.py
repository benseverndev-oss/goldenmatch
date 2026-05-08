"""Generate a v1.9-vintage memory cache entry as a JSON fixture.

Run: python tests/fixtures/autoconfig/_gen_v1_9_snapshot.py
Output: tests/fixtures/autoconfig/v1_9_memory_snapshot.json
"""
import json
from pathlib import Path

# v1.9 stored: serialized GoldenMatchConfig + signature + succeeded flag.
# Profiles weren't persisted (they're per-run only).
v1_9_entry = {
    "signature": "abcdef1234567890",
    "config_json": {
        "matchkeys": [{
            "name": "primary",
            "type": "weighted",
            "threshold": 0.85,
            "fields": [{
                "field": "email", "transforms": ["lowercase"],
                "scorer": "ensemble", "weight": 1.0,
            }],
        }],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000,
            "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.9.0",
}

out = Path(__file__).parent / "v1_9_memory_snapshot.json"
out.write_text(json.dumps(v1_9_entry, indent=2))
print(f"wrote {out}")
