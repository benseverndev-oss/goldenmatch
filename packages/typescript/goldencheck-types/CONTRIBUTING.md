# Contributing

Thank you for contributing to GoldenCheck Community Types!

## How to Add a Domain Pack

1. Create a new YAML file in `domains/` (e.g., `domains/telecom.yaml`)
2. Follow the format in `README.md`
3. Include a `description` field
4. Include at least one type with `name_hints` and `suppress`
5. Open a PR — CI will validate your YAML

## Review Criteria

- Types should be specific to the domain (not generic)
- Name hints should be precise enough to avoid false matches
- Suppress lists should only include checks that are genuinely irrelevant for the type
- Description should be concise (one line)

## Questions?

Open an issue or discussion on [GoldenCheck](https://github.com/benzsevern/goldencheck/discussions).
