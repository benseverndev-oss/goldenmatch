# Community & Ecosystem — Design Spec

## Goal

Build the foundation for community-contributed semantic type definitions: bundled domain packs, a contribution workflow, and MCP server integration for type discovery.

## Scope

Three phases, executed in order:

1. **Domain packs** — 3 bundled YAML type configs (healthcare, finance, e-commerce)
2. **Community contribution** — `goldencheck-types` GitHub repo with PR-based submission
3. **MCP type discovery** — 3 new tools added to the existing MCP server

---

## 1. Bundled Domain Packs

### Purpose

Ship domain-specific semantic type definitions that improve detection accuracy for common data domains. Users select a pack and get tailored suppression rules + type hints.

### CLI Interface

```bash
goldencheck scan data.csv --domain healthcare     # use healthcare types
goldencheck scan data.csv --domain finance         # use finance types
goldencheck scan data.csv --domain ecommerce       # use e-commerce types
goldencheck scan data.csv                          # base types only (default)
```

### Architecture

Domain packs are YAML files in `goldencheck/semantic/domains/`:

```
goldencheck/semantic/domains/
├── healthcare.yaml
├── finance.yaml
└── ecommerce.yaml
```

Each domain YAML extends the base `types.yaml` — it adds new types and can override base types. The format is identical to the existing `types.yaml`:

```yaml
# healthcare.yaml
types:
  npi:
    name_hints: ["npi", "npi_number", "provider_npi"]
    value_signals:
      min_unique_pct: 0.90
    suppress: ["cardinality", "pattern_consistency"]

  icd_code:
    name_hints: ["diagnosis", "dx", "icd", "procedure_code"]
    value_signals:
      short_strings: true
    suppress: ["type_inference", "cardinality"]

  insurance_id:
    name_hints: ["insurance_id", "policy_number", "member_id"]
    value_signals:
      min_unique_pct: 0.80
    suppress: ["cardinality"]

  patient_name:
    name_hints: ["patient_name", "patient_first", "patient_last"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency", "cardinality"]
```

### Domain Packs Content

**Healthcare:**
- NPI numbers, ICD codes, insurance IDs, patient demographics
- CPT codes, DRG codes, facility codes, provider types
- Suppress: type_inference on procedure codes, cardinality on diagnosis codes

**Finance:**
- Account numbers, routing numbers, CUSIP/ISIN, currency codes
- Transaction types, payment methods, merchant categories
- Suppress: pattern_consistency on account numbers, cardinality on currency codes

**E-commerce:**
- SKUs, order IDs, tracking numbers, product categories
- Payment statuses, shipping methods, coupon codes
- Suppress: uniqueness on SKUs (expected unique), pattern_consistency on tracking numbers

### Loading Mechanism

Modify `load_type_defs()` in `goldencheck/semantic/classifier.py`:

1. Always load base `types.yaml`
2. If `--domain` is specified, load `goldencheck/semantic/domains/{domain}.yaml` and merge (domain types override base types with same name; new types are added)
3. If `goldencheck_types.yaml` exists in the working directory, load and merge last (user types override everything)

Layer order: `base → domain → user`

### Integration

- Add `--domain` flag to `scan`, `review`, `fix`, and the `main()` fallback parser
- Pass domain through to `scan_file()` as a new optional parameter `domain: str | None = None`
- `scan_file()` passes domain to `load_type_defs(domain=domain)`
- The `learn` command also accepts `--domain` to give the LLM domain context

---

## 2. Community Contribution Workflow

### Purpose

Enable the community to contribute domain-specific type definitions via a dedicated GitHub repo.

### Repository: `benzsevern/goldencheck-types`

```
goldencheck-types/
├── README.md                    # How to contribute
├── CONTRIBUTING.md              # PR template, YAML format guide
├── domains/
│   ├── healthcare.yaml          # mirror of bundled pack
│   ├── finance.yaml
│   ├── ecommerce.yaml
│   ├── telecom.yaml             # community-contributed
│   ├── logistics.yaml           # community-contributed
│   └── ...
├── tests/
│   └── validate_yaml.py         # CI script: validates YAML format
└── .github/
    └── workflows/
        └── validate.yml         # PR CI: validates submitted YAMLs
```

### Contribution Flow

1. Contributor forks `goldencheck-types`
2. Adds a new YAML file in `domains/` following the format guide
3. Opens a PR — CI validates the YAML format automatically
4. Maintainer reviews and merges
5. Users can download the YAML and place it in their project as `goldencheck_types.yaml`, or install via CLI (Phase 3)

### YAML Validation CI

The `validate_yaml.py` script checks:
- Valid YAML syntax
- Follows the `types.yaml` schema (types dict with name_hints, value_signals, suppress)
- No duplicate type names within a file
- All suppress values are valid check names
- name_hints are non-empty strings

### CLI Install (future)

Not built in this phase. For now, users manually download domain YAMLs:

```bash
# Manual download for now
curl -o goldencheck_types.yaml https://raw.githubusercontent.com/benzsevern/goldencheck-types/main/domains/telecom.yaml
```

---

## 3. MCP Type Discovery

### Purpose

Add 3 tools to the existing MCP server so users can browse and install domain type packs from Claude Desktop.

### New Tools

Added to `goldencheck/mcp/server.py` alongside the existing 6 tools:

#### `list_domains`

List all available domain packs (bundled + community).

**Parameters:** None

**Returns:**
```json
{
  "domains": [
    {
      "name": "healthcare",
      "description": "NPI, ICD codes, insurance IDs, patient demographics",
      "types_count": 8,
      "source": "bundled"
    },
    {
      "name": "finance",
      "description": "Account numbers, routing numbers, CUSIP/ISIN, currency codes",
      "types_count": 6,
      "source": "bundled"
    }
  ]
}
```

#### `get_domain_info`

Get detailed info about a specific domain pack.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `domain` | string | yes | Domain pack name |

**Returns:**
```json
{
  "name": "healthcare",
  "types": {
    "npi": {
      "name_hints": ["npi", "npi_number"],
      "suppress": ["cardinality", "pattern_consistency"]
    }
  }
}
```

#### `install_domain`

Download a community domain pack from the `goldencheck-types` repo and save it as `goldencheck_types.yaml` in the current directory.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `domain` | string | yes | Domain pack name |
| `output_path` | string | no | Output path (default: `goldencheck_types.yaml`) |

**Returns:**
```json
{
  "installed": "telecom",
  "path": "goldencheck_types.yaml",
  "types_count": 5
}
```

**Implementation:** Fetches the YAML from `https://raw.githubusercontent.com/benzsevern/goldencheck-types/main/domains/{domain}.yaml` via `urllib.request`. No new dependencies.

### Scan with Domain

The existing `scan` MCP tool gets a new optional `domain` parameter:

```json
{
  "domain": {
    "type": "string",
    "description": "Domain pack to use: healthcare, finance, ecommerce, or a custom domain name",
    "default": null
  }
}
```

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| Domain YAMLs | Unit test: load each bundled domain, verify valid structure |
| Domain loading | Unit test: base → domain → user merge order; domain overrides base |
| `--domain` flag | CLI integration test: scan with `--domain healthcare` |
| MCP tools | Unit test: list_domains returns correct structure; get_domain_info for each bundled domain |
| YAML validation | Unit test: valid YAML passes; invalid YAML (bad schema, missing fields) fails |

## Non-Goals

- No CLI `publish` command (future, when community demand warrants it)
- No package registry or index server — GitHub is the registry
- No automatic domain detection ("you seem to have healthcare data, try --domain healthcare") — future enhancement
- No domain-specific profilers — domains only affect semantic types and suppression

## Version

Domain packs and MCP tools ship as part of GoldenCheck v0.5.0. The `goldencheck-types` repo is created as a separate project.
