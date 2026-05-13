"""Generate v1_11_memory_snapshot.json for backward-compat tests.

v1.11 stored: GoldenMatchConfig with NE optional on weighted matchkeys only.
v1.12 adds: NE on exact matchkeys + threshold default 0.5.
A v1.11 cache entry has no NE on exact matchkeys (NE was never promoted on
exact in v1.11).
"""
import json
from pathlib import Path

v1_11_entry = {
    "signature": "v111_test_signature",
    "config_json": {
        "matchkeys": [
            {
                "name": "exact_email",
                "type": "exact",
                "threshold": None,
                "fields": [
                    {
                        "field": "email",
                        "transforms": ["lowercase"],
                        "scorer": "exact",
                        "weight": 1.0,
                    }
                ],
                "negative_evidence": None,  # v1.11 didn't promote NE on exact
            },
            {
                "name": "fuzzy_match",
                "type": "weighted",
                "threshold": 0.85,
                "fields": [
                    {
                        "field": "first_name",
                        "transforms": [],
                        "scorer": "ensemble",
                        "weight": 1.0,
                    }
                ],
                "negative_evidence": None,
            },
        ],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000,
            "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.11.0",
}

out = Path(__file__).parent / "v1_11_memory_snapshot.json"
out.write_text(json.dumps(v1_11_entry, indent=2))
print(f"wrote {out}")
