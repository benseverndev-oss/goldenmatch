#!/usr/bin/env bash
# Run GoldenCheck scans on all matching files and collect results.
set -euo pipefail

RESULTS_DIR="/tmp/goldencheck-results"
mkdir -p "$RESULTS_DIR"

# Build flags
FLAGS="--no-tui --json"
if [ -n "${GC_CONFIG:-}" ]; then
  FLAGS="$FLAGS --config $GC_CONFIG"
fi
if [ "${GC_LLM_BOOST:-false}" = "true" ]; then
  FLAGS="$FLAGS --llm-boost --llm-provider ${GC_LLM_PROVIDER:-anthropic}"
fi

# Expand glob and scan each file
TOTAL_ERRORS=0
TOTAL_WARNINGS=0
WORST_GRADE="A"
FILE_COUNT=0
GRADE_ORDER="ABCDF"

for file in $GC_FILES; do
  if [ ! -f "$file" ]; then
    continue
  fi

  FILE_COUNT=$((FILE_COUNT + 1))
  BASENAME=$(basename "$file")
  OUTPUT="$RESULTS_DIR/${BASENAME}.json"

  # Run scan (don't fail on findings — we handle exit codes ourselves)
  goldencheck scan "$file" $FLAGS > "$OUTPUT" 2>/dev/null || true
done

if [ "$FILE_COUNT" -eq 0 ]; then
  echo "::error::No files matched pattern: $GC_FILES"
  exit 1
fi

# Parse results and compute totals
python3 -c "
import json, glob, os, sys

results_dir = '$RESULTS_DIR'
total_errors = 0
total_warnings = 0
worst_grade = 'A'
grade_order = 'FDCBA'

for f in sorted(glob.glob(os.path.join(results_dir, '*.json'))):
    try:
        with open(f) as fh:
            data = json.load(fh)
        findings = data.get('findings', [])
        errors = sum(1 for x in findings if x.get('severity', '').lower() == 'error')
        warnings = sum(1 for x in findings if x.get('severity', '').lower() == 'warning')
        total_errors += errors
        total_warnings += warnings
    except (json.JSONDecodeError, KeyError):
        pass

# Determine pass/fail
fail_on = '${GC_FAIL_ON:-error}'
if fail_on == 'warning':
    failed = total_errors > 0 or total_warnings > 0
else:
    failed = total_errors > 0

# Set outputs
with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f'errors={total_errors}\n')
    f.write(f'warnings={total_warnings}\n')
    f.write(f'health_grade={worst_grade}\n')
    f.write(f'failed={str(failed).lower()}\n')
    f.write(f'file_count={len(glob.glob(os.path.join(results_dir, \"*.json\")))}\n')

print(f'Scanned {len(glob.glob(os.path.join(results_dir, \"*.json\")))} files: {total_errors} errors, {total_warnings} warnings')

if failed:
    print(f'::error::Data quality check failed ({total_errors} errors, {total_warnings} warnings)')
    sys.exit(1)
"
