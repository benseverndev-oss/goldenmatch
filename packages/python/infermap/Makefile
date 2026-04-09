# infermap — bench + meta targets

.PHONY: bench bench-python bench-ts bench-baseline bench-comment bench-test bench-self-test help

help:
	@echo "infermap make targets:"
	@echo "  bench              run both benchmark runners + show delta vs local baseline"
	@echo "  bench-python       run only the Python benchmark runner"
	@echo "  bench-ts           run only the TypeScript benchmark runner"
	@echo "  bench-baseline     overwrite benchmark/baselines/main.json from local runs"
	@echo "  bench-comment      preview the PR comment that would be posted"
	@echo "  bench-test         run unit + fixture + parity tests for both bench runners"
	@echo "  bench-self-test    run the 5-case self-test smoke corpus"

bench-python:
	python -m infermap_bench run --output benchmark/report-python-local.json --seed 42

bench-ts:
	cd benchmark/runners/ts && node dist/cli.cjs run --output ../../report-ts-local.json --seed 42

bench: bench-python bench-ts
	python benchmark/aggregate.py \
		--python benchmark/report-python-local.json \
		--ts benchmark/report-ts-local.json \
		--baseline benchmark/baselines/main.json \
		--markdown benchmark/comment-local.md \
		--output benchmark/delta-local.json
	@cat benchmark/comment-local.md

bench-baseline: bench-python bench-ts
	python benchmark/build_baseline.py \
		--python benchmark/report-python-local.json \
		--ts benchmark/report-ts-local.json \
		--commit local \
		--output benchmark/baselines/main.json

bench-comment:
	python benchmark/aggregate.py \
		--python benchmark/report-python-local.json \
		--ts benchmark/report-ts-local.json \
		--baseline benchmark/baselines/main.json \
		--markdown /dev/stdout

bench-test:
	cd benchmark/runners/python && pytest --tb=short
	cd benchmark/runners/ts && npm test

bench-self-test:
	python -m infermap_bench run --self-test
	cd benchmark/runners/ts && node dist/cli.cjs run --self-test
