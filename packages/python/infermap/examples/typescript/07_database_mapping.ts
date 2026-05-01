// 07 — Map from a SQLite database table to a JSON schema definition.
//
// Run: npm install better-sqlite3
//      npx tsx examples/typescript/07_database_mapping.ts
//
// Creates a throwaway SQLite DB with a realistic CRM table, then maps its
// columns onto a canonical customer schema loaded from a JSON definition
// file. Uses `infermap/node` for DB and schema-definition-file access.

import { MapEngine, parseSchemaDefinition } from "infermap";
import { extractDbSchema } from "infermap/node";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function main() {
  const tmp = await mkdtemp(join(tmpdir(), "infermap-db-"));
  const dbPath = join(tmp, "crm.sqlite");

  try {
    // Seed a throwaway DB — uses better-sqlite3 directly for the test data.
    // Each statement goes through prepare().run() individually.
    const Database = (await import("better-sqlite3")).default;
    const db = new Database(dbPath);
    db.prepare(
      "CREATE TABLE crm_customers (cust_id INTEGER PRIMARY KEY, fname TEXT, lname TEXT, email_addr TEXT, tel TEXT, signup_dt TEXT)"
    ).run();
    const insert = db.prepare(
      "INSERT INTO crm_customers VALUES (?, ?, ?, ?, ?, ?)"
    );
    insert.run(1, "Alice", "Smith", "alice@a.co", "555-0001", "2024-01-15");
    insert.run(2, "Bob", "Jones", "bob@b.co", "555-0002", "2024-02-20");
    insert.run(3, "Carol", "Diaz", "carol@c.co", "555-0003", "2024-03-10");
    db.close();

    // Extract the source schema from the database
    const source = await extractDbSchema(`sqlite:///${dbPath}`, {
      table: "crm_customers",
    });

    // Define the canonical target via a JSON schema definition
    const canonicalJson = JSON.stringify({
      fields: [
        { name: "customer_id", dtype: "integer", required: true },
        { name: "first_name", dtype: "string", aliases: ["fname", "forename"] },
        { name: "last_name", dtype: "string", aliases: ["lname", "surname"] },
        { name: "email", dtype: "string", aliases: ["email_addr", "e_mail"] },
        { name: "phone", dtype: "string", aliases: ["tel", "telephone"] },
        { name: "created_at", dtype: "date", aliases: ["signup_dt", "signup_date"] },
      ],
    });
    const target = parseSchemaDefinition(canonicalJson, "canonical_customer");

    // Map
    const engine = new MapEngine({ minConfidence: 0.3 });
    const result = engine.mapSchemas(source, target);

    console.log("Database column → canonical field mappings:");
    console.log("-".repeat(60));
    for (const m of result.mappings) {
      console.log(
        `  ${m.source.padEnd(12)} → ${m.target.padEnd(14)} (${m.confidence.toFixed(3)})`
      );
    }

    if (result.warnings.length > 0) {
      console.log("\nWarnings:");
      for (const w of result.warnings) console.log(`  ! ${w}`);
    }
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
