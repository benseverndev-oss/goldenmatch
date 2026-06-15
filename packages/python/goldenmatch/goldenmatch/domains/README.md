# GoldenMatch Domain Packs

Domain packs are declarative YAML rulebooks that tell GoldenMatch how to extract
domain-specific identifiers, brands, attributes, and normalized names from text.

Seven built-in packs ship with GoldenMatch: **electronics**, **financial**,
**healthcare**, **people**, **real_estate**, **retail**, and **software**.

They are loaded by `goldenmatch/core/domain_registry.py`, which builds a
`DomainRulebook` from each YAML file. See that module for the authoritative
behavior.

## Schema Reference

Every domain pack is a single YAML file. All seven built-in packs set all seven
keys below, though only `name` is strictly required by the loader.

### `name`

The domain identifier, used in auto-selection and logs. If omitted, the loader
falls back to the file name (without extension).

```yaml
name: electronics
```

### `signals`

A list of lowercase keywords matched against your dataset's column names to
auto-detect which domain the data belongs to. Each keyword is checked as a
substring of the joined, lowercased column names; the pack with the most
matches wins (see [auto-selection](#how-domain-packs-are-loaded)).

```yaml
signals: ["brand", "model", "sku", "upc", "ean", "asin", "megapixel", "mp", "ghz", "watt", "battery", "wireless", "bluetooth"]
```

### `identifier_patterns`

A map of named regexes for extracting structured identifiers (model numbers,
SKUs, NDCs, CUSIPs, etc.). Each key is the identifier name; each value is a
regex compiled case-insensitively. On a match, the first capture group is
extracted (or the whole match if the regex has no groups).

```yaml
identifier_patterns:
  model_number: '\b([A-Z]{1,5}[-]?\d{2,}[A-Z0-9]*(?:[-/]\w+)?)\b'
  sku: '\b(\d{6,}|[A-Z]{2,}\d{4,})\b'
```

### `brand_patterns`

A list of brand / manufacturer / publisher names to detect in text. These are
**literal strings**, not regexes: they are escaped and compiled into a single
case-insensitive, word-boundary alternation. The first brand found in the text
is returned.

```yaml
brand_patterns:
  - Adobe
  - Microsoft
  - Intuit
  - Symantec
  - Norton
  - McAfee
  - Autodesk
```

If a domain has no meaningful brands (e.g. people), use an empty list:

```yaml
brand_patterns: []
```

### `attribute_patterns`

A map of named regexes for domain-specific attributes: dosage, gauge, count,
screen size, currency, etc. Each key is the attribute name; each value is a
regex compiled case-insensitively. The whole matched span is captured. Each
attribute match contributes to the extraction confidence score.

```yaml
attribute_patterns:
  dosage_mg: '(\d+(?:\.\d+)?)\s*mg\b'
  gauge: '(\d+)\s*(?:ga|gauge)\b'
  count: '(\d+)\s*(?:count|ct|pack|pk)\b'
```

### `stop_words`

Words stripped during name normalization. After identifiers and attributes are
removed from the text, these common words are filtered out (along with any
remaining single-character tokens) to produce the `name_normalized` field.

```yaml
stop_words:
  - mr
  - mrs
  - ms
  - dr
```

### `normalization`

A map of declarative post-processing flags describing how a pack's identifiers
and names are intended to be normalized. Every built-in pack sets this block.

> **Note:** these flags are metadata. The generic rulebook loader
> (`DomainRulebook.extract`) records them but does not currently apply the
> `uppercase` / `strip_*` rules itself; its name normalization always
> lowercases the text and strips matched identifiers, attributes, and
> stop-words. Document the intent for your pack here, but don't rely on the
> rulebook path to transform identifiers via these flags.

Flags used across the built-in packs:

| Flag                  | Intent                                           | Packs that set it                               |
| --------------------- | ------------------------------------------------ | ----------------------------------------------- |
| `lowercase`           | Normalize identifiers to lowercase               | healthcare, people, retail, software            |
| `uppercase`           | Normalize identifiers to uppercase               | electronics, financial, real_estate             |
| `strip_punctuation`   | Remove punctuation from identifiers              | financial, healthcare, people, retail, software |
| `strip_hyphens`       | Remove hyphens (e.g. from model numbers)         | electronics                                     |
| `strip_region_suffix` | Remove regional suffixes (e.g. `-US`, `-EU`)     | electronics                                     |
| `strip_color_suffix`  | Remove color suffixes (e.g. `-black`)            | electronics                                     |

```yaml
normalization:
  strip_hyphens: true
  uppercase: true
  strip_region_suffix: true
  strip_color_suffix: true
```

## How Domain Packs Are Loaded

`domain_registry.py::discover_rulebooks()` searches three locations and loads
every `.yaml` and `.yml` file it finds:

1. **Project-local:** `.goldenmatch/domains/` — per-project packs
2. **User-global:** `~/.goldenmatch/domains/` — personal custom packs
3. **Built-in:** `goldenmatch/domains/` — the seven shipped packs

Packs are keyed by their `name`. The three locations are loaded **in the order
above**, and a later load overwrites an earlier one with the same `name`. So if
the same domain name exists in more than one location, the **last one loaded
wins** — built-in overrides user-global, which overrides project-local.

> If you want a project-local or user-global pack to shadow a built-in one,
> give it a distinct `name` so it isn't overwritten.

Auto-selection (`match_domain`) scores each pack by how many of its `signals`
appear in your dataset's column names and returns the highest scorer; if no
pack scores above zero, it returns nothing.

## How to Add a New Domain Pack

### 1. Copy an existing pack that's closest to your domain

```bash
cp packages/python/goldenmatch/goldenmatch/domains/electronics.yaml \
   packages/python/goldenmatch/goldenmatch/domains/my_domain.yaml
```

### 2. Fill in the keys

- `name`: your domain identifier (use `snake_case`)
- `signals`: column-name keywords that suggest this domain
- `identifier_patterns`: regexes for IDs in your domain
- `brand_patterns`: known brand strings (or `[]` if none)
- `attribute_patterns`: regexes for domain-specific attributes
- `stop_words`: common words to strip during normalization
- `normalization`: declarative normalization intent (see the note above)

### 3. Place the file in one of the three search paths

Use a built-in, user-global, or project-local location. Remember that a
built-in pack with the same `name` will shadow a project-local one, so pick a
distinct `name` for custom packs.

### 4. Test it

GoldenMatch auto-discovers domain packs at runtime, so a new pack is available
on the next run with no registration step. The MCP server also exposes
`list_domains`, `create_domain`, and `test_domain` for inspecting and trying
packs interactively.

## Example: Minimal Custom Domain

```yaml
name: automotive
signals: ["vin", "make", "model", "year", "engine", "trim"]

identifier_patterns:
  vin: '\b([A-HJ-NPR-Z0-9]{17})\b'

brand_patterns:
  - Toyota
  - Honda
  - Ford
  - BMW
  - Mercedes-Benz

attribute_patterns:
  year: '\b((?:19|20)\d{2})\b'
  engine: '(\d+\.?\d*)\s*L\b'

stop_words:
  - the
  - a
  - an
  - for
  - and
  - with

normalization:
  uppercase: true
  strip_punctuation: true
```

## All Keys at a Glance

| Key                   | Required | Type                 | Description                                          |
| --------------------- | -------- | -------------------- | ---------------------------------------------------- |
| `name`                | Yes      | string               | Domain identifier (defaults to the file name)        |
| `signals`             | No       | list[string]         | Column-name keywords for auto-detection              |
| `identifier_patterns` | No       | map[string, string]  | Named regexes for structured IDs                     |
| `brand_patterns`      | No       | list[string]         | Literal brand names (can be `[]`)                    |
| `attribute_patterns`  | No       | map[string, string]  | Named regexes for domain attributes                  |
| `stop_words`          | No       | list[string]         | Words stripped during name normalization             |
| `normalization`       | No       | map[string, bool]    | Declarative normalization flags (see note)           |
