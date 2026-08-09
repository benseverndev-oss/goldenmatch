# Railway services

The two hosted services: the goldenmatch MCP server and the bench-data generator.

> Extracted from the root `CLAUDE.md` so it is read when relevant rather than
> loaded into every session. Linked from the map at the bottom of that file.

## Railway: goldenmatch-mcp service
- Project `golden-suite-mcp`, service `goldenmatch-mcp`, env `production`. IDs in `packages/python/goldenmatch/.railway/` after `railway link`.
- Build/deploy config pinned in `packages/python/goldenmatch/railway.json`. Service `rootDirectory='packages/python/goldenmatch'` set via Railway GraphQL — DO NOT revert unless also moving `Dockerfile.mcp` back to repo root.
- Access token: `~/.railway/config.json` → `user.accessToken`. GraphQL endpoint: `https://backboard.railway.com/graphql/v2`.
- Status check: `cd packages/python/goldenmatch && railway deployment list | head -5`. Build logs: `railway logs --build <deployment-id>`.

## Railway: goldenmatch-bench-gen service
- Separate service from `goldenmatch-mcp`. Hosts the bench-data generator -- a FastAPI control plane that runs `scripts/generate_phase5_dataset.py` (and future bench generators) on Railway's beefy box and persists the result on a `/data` volume mount. Modeled on `goldenmatch-shell-company-network`'s `shellnet-job` pattern.
- Build: `packages/python/goldenmatch/Dockerfile.bench`. Railway config: `packages/python/goldenmatch/railway-bench.json`.
- Env vars (set on the service): `GOLDENMATCH_BENCH_JOB_TOKEN` (bearer), `GOLDENMATCH_BENCH_DATA_DIR=/data` (volume mount target).
- Endpoints (all bearer-auth except `/healthz`): `POST /generate?rows=N&workers=W`, `GET /status`, `GET /download?file=NAME`, `GET /list`, `GET /logs?job_id=ID`.
- Laptop-side trigger: `scripts/trigger_bench_gen.py --rows 50000000 --workers 16 [--upload-to-release bench-dataset-v1]`. Needs `GOLDENMATCH_BENCH_JOB_URL` + `GOLDENMATCH_BENCH_JOB_TOKEN` in the shell env.
- The trigger uploads to the existing `bench-dataset-v1` GitHub Release as a new asset when `--upload-to-release` is set; the `bench-phase5-simulated` workflow then downloads `bench_50000000.parquet` from that release at job time.
- Generator perf (vectorized + ProcessPoolExecutor on `--workers 4`): 1M rows in 1.4s on a 16-core box. 50M extrapolates to ~70s; 100M to ~140s. Way under the 60-min job cap that prevented running this in CI.

