\# GoldenMatch Domain Packs



Domain packs are declarative YAML rulebooks that tell GoldenMatch how to extract domain-specific identifiers, brands, attributes, and normalized names from text.



Seven built-in packs ship with GoldenMatch: \*\*electronics\*\*, \*\*financial\*\*, \*\*healthcare\*\*, \*\*people\*\*, \*\*real\_estate\*\*, \*\*retail\*\*, and \*\*software\*\*.



\## Schema Reference



Every domain pack is a single YAML file with the following keys:



\### `name`



The domain identifier used in logs and auto-selection.



```yaml

name: electronics

```



\### `signals`



A list of lowercase keywords matched against column names to auto-detect which domain a dataset belongs to. Higher signal overlap = higher confidence.



\*\*Electronics example:\*\*



```yaml

signals: \["brand", "model", "sku", "upc", "ean", "megapixel", "ghz", "watt"]

```



\### `identifier\_patterns`



Named regex patterns for extracting structured identifiers (model numbers, SKUs, NDCs, CUSIPs, etc.). Each key is the identifier name; the value is a regex with one capture group. GoldenMatch runs these against text to pull out domain IDs.



\*\*Electronics example:\*\*



```yaml

identifier\_patterns:

&#x20; model\_number: '\\b(\[A-Z]{1,5}\[-]?\\d{2,}\[A-Z0-9]\*(?:\[-/]\\w+)?)\\b'

&#x20; sku: '\\b(\\d{6,}|\[A-Z]{2,}\\d{4,})\\b'

```



\### `brand\_patterns`



A list of brand/manufacturer/publisher names to detect in text. These are compiled into a single case-insensitive regex with word boundaries.



\*\*Software example:\*\*



```yaml

brand\_patterns: \["Adobe", "Microsoft", "Oracle", "SAP", "Intuit", "VMware"]

```



If a domain has no meaningful brands (e.g. people), leave this as an empty list:



```yaml

brand\_patterns: \[]

```



\### `attribute\_patterns`



Named regex patterns for domain-specific attributes: dosage, screen size, square footage, currency, etc. Each key is the attribute name; the value is a regex. Match groups contribute to the extraction confidence score.



\*\*Healthcare example:\*\*



```yaml

attribute\_patterns:

&#x20; dosage\_mg: '(\\d+(?:\\.\\d+)?)\\s\*mg\\b'

&#x20; gauge: '(\\d+)\\s\*(?:ga|gauge)\\b'

```



\### `stop\_words`



Words stripped during name normalization. After identifiers and attributes are removed from the text, these common words are filtered out to produce a clean `name\_normalized` field.



\*\*People example:\*\*



```yaml

stop\_words: \["mr", "mrs", "ms", "dr", "jr", "sr", "ii", "iii", "iv"]

```



\### `normalization`



A map of post-processing rules applied to extracted identifiers and names:



| Key                         | Effect                                       | Used by                                         |

| --------------------------- | -------------------------------------------- | ----------------------------------------------- |

| `lowercase: true`           | Normalize identifiers to lowercase           | healthcare, people, retail, software            |

| `uppercase: true`           | Normalize identifiers to uppercase           | electronics, financial, real\_estate             |

| `strip\_punctuation: true`   | Remove punctuation from identifiers          | financial, healthcare, people, retail, software |

| `strip\_hyphens: true`       | Remove hyphens (e.g. model numbers)          | electronics                                     |

| `strip\_region\_suffix: true` | Remove regional suffixes (e.g. "-US", "-EU") | electronics                                     |

| `strip\_color\_suffix: true`  | Remove color suffixes (e.g. "-black")        | electronics                                     |



\*\*Electronics example:\*\*



```yaml

normalization:

&#x20; strip\_hyphens: true

&#x20; uppercase: true

&#x20; strip\_region\_suffix: true

&#x20; strip\_color\_suffix: true

```



\## How Domain Packs Are Loaded



`domain\_registry.py` discovers domain packs from three locations (searched in order):



1\. \*\*Project-local:\*\* `.goldenmatch/domains/` — override or extend for a specific project

2\. \*\*User-global:\*\* `\~/.goldenmatch/domains/` — personal custom domains

3\. \*\*Built-in:\*\* `goldenmatch/domains/` — the 7 shipped packs



Files with `.yaml` or `.yml` extensions are loaded.



If the same domain name appears in multiple locations, the first one found wins (project-local overrides user-global, which overrides built-in).



Auto-selection works by matching `signals` keywords against your dataset's column names — the domain with the most keyword matches wins.



\## How to Add a New Domain Pack



\### 1. Copy an existing pack that's closest to your domain



```bash

cp packages/python/goldenmatch/goldenmatch/domains/electronics.yaml \\

&#x20;  packages/python/goldenmatch/goldenmatch/domains/my\_domain.yaml

```



\### 2. Fill in the keys



\* `name`: your domain identifier (use `snake\_case`)

\* `signals`: column-name keywords that suggest this domain

\* `identifier\_patterns`: regexes for IDs in your domain

\* `brand\_patterns`: known brands (or `\[]` if none)

\* `attribute\_patterns`: regexes for domain-specific attributes

\* `stop\_words`: common words to strip during normalization

\* `normalization`: choose lowercase or uppercase, and any strip rules



\### 3. Place the file in one of the three search paths



\* Built-in

\* User-global

\* Project-local



\### 4. Test it



GoldenMatch auto-discovers domain packs at runtime. Run your deduplication or extraction workflow — the new domain will be available immediately.



\## Example: Minimal Custom Domain



```yaml

name: automotive

signals: \["vin", "make", "model", "year", "engine", "trim"]



identifier\_patterns:

&#x20; vin: '\\b(\[A-HJ-NPR-Z0-9]{17})\\b'



brand\_patterns:

&#x20; - Toyota

&#x20; - Honda

&#x20; - Ford

&#x20; - BMW

&#x20; - Mercedes-Benz



attribute\_patterns:

&#x20; year: '\\b((?:19|20)\\d{2})\\b'

&#x20; engine: '(\\d+\\.?\\d\*)\\s\*L\\b'



stop\_words:

&#x20; - the

&#x20; - a

&#x20; - an

&#x20; - for

&#x20; - and

&#x20; - with



normalization:

&#x20; uppercase: true

&#x20; strip\_punctuation: true

```



\## All Keys at a Glance



| Key                   | Required | Type                 | Description                                                 |

| --------------------- | -------- | -------------------- | ----------------------------------------------------------- |

| `name`                | Yes      | string               | Domain identifier                                           |

| `signals`             | Yes      | list\[string]         | Column-name keywords for auto-detection                     |

| `identifier\_patterns` | No       | dict\[string, string] | Named regexes for structured IDs                            |

| `brand\_patterns`      | No       | list\[string]         | Known brand names (can be `\[]`)                             |

| `attribute\_patterns`  | No       | dict\[string, string] | Named regexes for domain attributes                         |

| `stop\_words`          | No       | list\[string]         | Words stripped during name normalization                    |

| `normalization`       | No       | dict\[string, string] | Post-processing rules (`lowercase`, `uppercase`, `strip\_\*`) |