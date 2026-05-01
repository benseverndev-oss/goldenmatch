---
title: Scheduled Runs
layout: default
nav_order: 14
---

Run scans on a fixed schedule with optional webhook notifications.

## Usage

```bash
goldencheck schedule data/*.csv --interval daily
goldencheck schedule data/*.csv --interval hourly --webhook https://hooks.slack.com/...
goldencheck schedule orders.csv customers.csv --interval 30min --domain ecommerce
```

## Intervals

| Interval | Seconds |
|----------|---------|
| `5min` | 300 |
| `15min` | 900 |
| `30min` | 1,800 |
| `hourly` | 3,600 |
| `daily` | 86,400 |
| `weekly` | 604,800 |

Or pass a number for custom seconds: `--interval 120` (every 2 minutes).

## With Webhooks

```bash
goldencheck schedule data/*.csv \
  --interval hourly \
  --webhook https://hooks.slack.com/services/... \
  --notify-on grade-drop
```

Triggers: `grade-drop` (default), `any-error`, `any-warning`.

## Output

```
[14:00:00] GoldenCheck scheduler started — scanning 3 file(s) every hourly
[14:00:00] Run #1
  orders.csv: 0 errors, 2 warnings (A)
  customers.csv: 1 error, 3 warnings (B)
  products.csv: 0 errors, 0 warnings (A)
[15:00:00] Run #2
  orders.csv: 0 errors, 2 warnings (A)
  ...
```

## vs Watch Mode

| Feature | `watch` | `schedule` |
|---------|---------|-----------|
| Trigger | File changes (mtime) | Fixed time interval |
| Re-scans | Only changed files | All files every run |
| Use case | Dev/monitoring | CI/production pipelines |
