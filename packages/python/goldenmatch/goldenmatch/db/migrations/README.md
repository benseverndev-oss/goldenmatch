# GoldenMatch DB migrations

SQL migration scripts that mirror the schemas embedded in the Python
`IdentityStore` / `MemoryStore` classes. Apply manually when you want the
shared Postgres state set up by a DBA rather than by the Python process.

## Files

- `identity_v1.sql` -- Identity Graph schema + analytical views
  (`v_identities`, `v_identity_pairs`, `v_identity_timeline`). Idempotent.

## Apply

```bash
psql -d $DB -f identity_v1.sql
```

The Python `IdentityStore(backend="postgres", connection=...)` will create
the same schema on first connect, so running the migration is only
required when:

1. The DBA owns DDL and the app role has only DML.
2. You want the analytical views without running the Python process first.
3. You're seeding a fresh environment via CI/IaC.
