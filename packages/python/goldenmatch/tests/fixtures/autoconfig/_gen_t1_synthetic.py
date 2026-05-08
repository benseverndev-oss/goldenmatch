"""Generate a synthetic 200-row DQbench T1-style fixture.

Run: python tests/fixtures/autoconfig/_gen_t1_synthetic.py
Output: tests/fixtures/autoconfig/t1_synthetic.csv

Mimics DQbench T1's failure mode: corrupted-email duplicates that v1.9's
controller catastrophically misclassifies. v1.10 should recover this via
rule_corruption_normalize + indicator priors.
"""
import csv
import random
from pathlib import Path

random.seed(42)
n_dup_pairs = 50
n_unique = 100
rows = []

# 50 duplicate pairs with corrupted emails (Brian@gmail vs BRIAN@gmail)
for i in range(n_dup_pairs):
    name = f"User{i:03d}"
    email_clean = f"user{i:03d}@gmail.com"
    rows.append({
        "id": f"dup_{i}_a", "name": name, "email": email_clean,
        "city": random.choice(["NYC", "LA", "SF"]),
    })
    # Corrupt the email: uppercase + add/remove dot
    email_corrupted = f"USER{i:03d}@gmail.com" if i % 2 == 0 else f"user{i:03d}@GMAIL.COM"
    rows.append({
        "id": f"dup_{i}_b", "name": name, "email": email_corrupted,
        "city": random.choice(["NYC", "LA", "SF"]),
    })

# 100 unique singletons
for i in range(n_unique):
    rows.append({
        "id": f"unique_{i:03d}",
        "name": f"Person{i:03d}",
        "email": f"person{i:03d}@example.com",
        "city": random.choice(["NYC", "LA", "SF", "Chicago", "Houston"]),
    })

random.shuffle(rows)

out_path = Path(__file__).parent / "t1_synthetic.csv"
with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "name", "email", "city"])
    writer.writeheader()
    writer.writerows(rows)

print(f"wrote {out_path} with {len(rows)} rows ({n_dup_pairs} duplicate pairs + {n_unique} singletons)")
