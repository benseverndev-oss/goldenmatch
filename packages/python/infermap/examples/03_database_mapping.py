"""Example 3: Map a CSV to a database table.

Creates a SQLite database, then maps a CSV file to it.
This demonstrates the DB-resilient workflow — if the DB schema
changes (new columns added), infermap adapts automatically.
"""

import os
import sqlite3

import infermap

# Create a sample SQLite database
DB_PATH = "data/sample_customers.db"

conn = sqlite3.connect(DB_PATH)
conn.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        email TEXT,
        phone TEXT,
        address TEXT,
        zip_code TEXT,
        created_at TEXT,
        organization TEXT,
        sex TEXT
    )
""")
conn.execute("DELETE FROM customers")  # clear for re-runs
conn.executemany(
    "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)",
    [
        ("ERP001", "Sarah", "Connor", "sarah@skynet.com", "555-9001", "100 Future Blvd", "90001", "2024-01-01", "Cyberdyne", "F"),
        ("ERP002", "Mike", "Ross", "mike@pearson.com", "555-9002", "200 Legal Ave", "10002", "2024-02-01", "Pearson", "M"),
    ],
)
conn.commit()
conn.close()
print(f"Created SQLite database: {DB_PATH}\n")

# Map CRM CSV to the database table
result = infermap.map(
    "data/crm_export.csv",
    f"sqlite:///{DB_PATH}",
    table="customers",
)

print("=== CRM -> Database Mapping ===\n")
for m in result.mappings:
    print(f"  {m.source:20s}  ->  {m.target:20s}  ({m.confidence:.3f})")

# Demonstrate the "resilient to drift" concept
print("\n--- Simulating schema drift ---")
conn = sqlite3.connect(DB_PATH)
conn.execute("ALTER TABLE customers ADD COLUMN loyalty_tier TEXT")
conn.close()
print("Added 'loyalty_tier' column to database\n")

# Re-map — infermap picks up the new column automatically
result2 = infermap.map(
    "data/crm_export.csv",
    f"sqlite:///{DB_PATH}",
    table="customers",
)
print(f"Unmapped target columns: {result2.unmapped_target}")
print("(loyalty_tier has no match in the CRM — expected!)")

# Cleanup
os.unlink(DB_PATH)
