"""Generate T3-style synthetic fixtures for v1.11 regression tests.

t3_synthetic.csv: 200 rows = 50 true dup pairs + 50 collision pairs + 100 singletons
t3_clean_compat.csv: 200 rows, no collision pattern (clean DBLP-ACM-style)

Run: python tests/fixtures/autoconfig/_gen_t3_synthetic.py
"""
import csv
import random
from pathlib import Path

random.seed(42)

OUT_DIR = Path(__file__).parent

# T3 synthetic: collision-prone
rows = []
for i in range(50):    # 50 true dup pairs (same person, same everything)
    name = f"User{i:03d}"
    email = f"user{i:03d}@gmail.com"
    phone = f"555-{1000+i:04d}"
    addr = f"{i} Main St"
    city = random.choice(["NYC", "LA", "SF"])
    rows.append({"id": f"dup_{i}_a", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone, "address": addr, "city": city})
    rows.append({"id": f"dup_{i}_b", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone, "address": addr, "city": city})

for i in range(50):    # 50 collision pairs (different people, same name+email)
    name = f"User{i:03d}"
    email = f"user{i:03d}@gmail.com"   # SAME as dup pairs (collision)
    phone_a = f"555-{2000+i:04d}"
    phone_b = f"555-{3000+i:04d}"
    addr_a = f"{100+i} Oak Ave"
    addr_b = f"{200+i} Pine Rd"
    rows.append({"id": f"coll_{i}_a", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone_a, "address": addr_a, "city": "Boston"})
    rows.append({"id": f"coll_{i}_b", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone_b, "address": addr_b, "city": "Chicago"})

for i in range(100):    # 100 unique singletons
    rows.append({
        "id": f"unique_{i:03d}",
        "first_name": f"Person{i:03d}",
        "last_name": f"Surname{i:03d}",
        "email": f"person{i:03d}@example.com",
        "phone": f"555-{4000+i:04d}",
        "address": f"{i} Random Way",
        "city": random.choice(["NYC", "LA", "SF", "Chicago", "Boston", "Houston"]),
    })

random.shuffle(rows)
fields = ["id", "first_name", "last_name", "email", "phone", "address", "city"]
out_path = OUT_DIR / "t3_synthetic.csv"
with out_path.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {out_path} ({len(rows)} rows)")

# T3 clean compat: 100 dup pairs (SAME person, same email) but NO collisions
clean_rows = []
random.seed(43)
for i in range(50):    # 50 true dup pairs — same person, same email
    name = f"CleanUser{i:03d}"
    email = f"cleanuser{i:03d}@example.com"
    phone = f"555-{5000+i:04d}"
    addr = f"{i} Clean Ave"
    city = random.choice(["NYC", "LA", "SF"])
    clean_rows.append({"id": f"cdup_{i}_a", "first_name": name, "last_name": "Jones",
                       "email": email, "phone": phone, "address": addr, "city": city})
    clean_rows.append({"id": f"cdup_{i}_b", "first_name": name, "last_name": "Jones",
                       "email": email, "phone": phone, "address": addr, "city": city})

for i in range(100):    # 100 unique singletons (each unique email)
    clean_rows.append({
        "id": f"cuniq_{i:03d}",
        "first_name": f"CleanPerson{i:03d}",
        "last_name": f"Cleansurname{i:03d}",
        "email": f"cleanperson{i:03d}@example.com",
        "phone": f"555-{6000+i:04d}",
        "address": f"{i} Clean Rd",
        "city": random.choice(["NYC", "LA", "SF", "Boston"]),
    })

random.shuffle(clean_rows)
out_clean = OUT_DIR / "t3_clean_compat.csv"
with out_clean.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(clean_rows)
print(f"wrote {out_clean} ({len(clean_rows)} rows)")
