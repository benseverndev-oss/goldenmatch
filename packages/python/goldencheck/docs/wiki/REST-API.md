# REST API

Run GoldenCheck as an HTTP microservice.

## Start the Server

```bash
goldencheck serve                    # default: 0.0.0.0:8000
goldencheck serve --port 9000       # custom port
goldencheck serve --host 127.0.0.1  # localhost only
```

## Endpoints

### `GET /health`

Health check.

```bash
curl http://localhost:8000/health
# {"status": "ok", "tool": "goldencheck"}
```

### `GET /checks`

List available profiler checks.

### `GET /domains`

List available domain packs.

### `POST /scan`

Scan a file by uploading its contents. Accepts raw CSV body.

```bash
curl -X POST http://localhost:8000/scan --data-binary @data.csv
curl -X POST "http://localhost:8000/scan?domain=healthcare" --data-binary @patients.csv
```

### `POST /scan/url`

Scan a file by URL. POST JSON body with `url` field.

```bash
curl -X POST http://localhost:8000/scan/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/data.csv", "domain": "finance"}'
```

## Response Format

```json
{
  "rows": 10000,
  "columns": 12,
  "health_grade": "B",
  "health_score": 82,
  "errors": 2,
  "warnings": 5,
  "findings_count": 24,
  "findings": [...]
}
```
