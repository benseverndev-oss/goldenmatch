# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting

Email **benzsevern@gmail.com** with a description, reproduction steps, and potential impact.

**Response time:** 48 hours for acknowledgment, 7 days for initial assessment.

Please do not open public issues for security concerns.

## Scope

GoldenCheck processes data files that may contain sensitive information. Areas of concern:

- **Data exposure** -- findings and profiles should not leak raw sensitive values unnecessarily
- **Config parsing** -- YAML files are parsed with `yaml.safe_load` (no arbitrary code execution)
- **File access** -- the reader only accesses explicitly provided file paths
