-- GoldenMatch DuckDB extension: core-API + goldenflow demo.
--
-- The DuckDB UDFs are registered from Python:
--
--   import duckdb, goldenmatch_duckdb
--   con = duckdb.connect()
--   goldenmatch_duckdb.register(con)
--
-- Then run any of the SQL below on `con`. All `goldenmatch_*` core-API UDFs
-- return JSON strings (except goldenmatch_suggest_threshold, which returns a
-- DOUBLE / SQL NULL). `goldenflow_*` UDFs are scalar text -> text.

-- A toy table to profile / dedupe.
CREATE TABLE customers AS SELECT * FROM (VALUES
    ('John Smith',  'JOHN@example.com ', '212-555-0100'),
    ('Jon Smyth',   'john@example.com',  '(212) 555-0100'),
    ('Jane Doe',    'jane@example.com',  '415-555-0199')
) AS t(name, email, phone);

-- 1) Profile a table -- column stats, types, quality signals (JSON).
SELECT goldenmatch_profile_table('customers');

-- 2) Suggest a threshold from a score distribution (Otsu).
--    Returns SQL NULL when the distribution is unimodal / too few scores.
SELECT goldenmatch_suggest_threshold('[0.10, 0.12, 0.11, 0.90, 0.92, 0.91]');

-- 3) GoldenFlow transforms -- normalize a column before matching.
SELECT
    goldenflow_normalize_email(email)  AS email_norm,
    goldenflow_normalize_phone(phone)  AS phone_e164,
    goldenflow_whitespace_normalize(name) AS name_clean
FROM customers;

-- 4) Evaluate predicted pairs against ground truth (precision / recall / F1).
--    pairs: [[id_a, id_b, score], ...]   ground truth: [[id_a, id_b], ...]
SELECT goldenmatch_evaluate(
    '[[0, 1, 0.95], [0, 2, 0.40]]',
    '[[0, 1]]'
);
