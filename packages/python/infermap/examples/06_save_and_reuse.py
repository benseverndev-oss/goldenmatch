"""Example 6: Save a mapping config and reapply it later.

Once you've validated a mapping, save it as a YAML config.
Next time, load it directly — no inference needed, instant remapping.
This is ideal for production pipelines where the schema is stable.
"""

import os

import polars as pl
import infermap

CONFIG_PATH = "data/crm_to_erp_mapping.yaml"

# --- Step 1: Run inference and save the mapping ---
print("=== Step 1: Infer mapping and save ===\n")

result = infermap.map("data/crm_export.csv", "data/erp_customers.csv")

for m in result.mappings:
    print(f"  {m.source:20s}  ->  {m.target:20s}  ({m.confidence:.3f})")

result.to_config(CONFIG_PATH)
print(f"\nSaved mapping config to: {CONFIG_PATH}")

# Show what was saved
print("\n--- Config file contents ---")
with open(CONFIG_PATH) as f:
    print(f.read())

# --- Step 2: Load the config and apply to new data ---
print("=== Step 2: Load config and remap new data ===\n")

loaded = infermap.from_config(CONFIG_PATH)
print(f"Loaded {len(loaded.mappings)} mappings from config")

# Read the source file and remap
df = pl.read_csv("data/crm_export.csv")
remapped = loaded.apply(df)

print(f"\nOriginal columns:  {df.columns}")
print(f"Remapped columns:  {remapped.columns}")
print(f"\nFirst 3 rows of remapped data:")
print(remapped.head(3).to_pandas().to_string(index=False))

# --- Step 3: Demonstrate the speed advantage ---
print("\n=== Step 3: Speed comparison ===\n")

import time

# Time inference-based mapping
start = time.perf_counter()
for _ in range(10):
    infermap.map("data/crm_export.csv", "data/erp_customers.csv")
infer_time = (time.perf_counter() - start) / 10

# Time config-based mapping
start = time.perf_counter()
for _ in range(10):
    loaded = infermap.from_config(CONFIG_PATH)
config_time = (time.perf_counter() - start) / 10

print(f"Inference mapping:  {infer_time*1000:.1f} ms/run")
print(f"Config reloading:   {config_time*1000:.1f} ms/run")
print(f"Speedup:            {infer_time/config_time:.0f}x faster with saved config")

# Cleanup
os.unlink(CONFIG_PATH)
