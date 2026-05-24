-- GoldenMatch Postgres extension: core-API + goldenflow demo.
--
-- Install once per database:
--
--   CREATE EXTENSION goldenmatch_pg;
--
-- All functions live in the `goldenmatch` schema, so either qualify each call
-- (`goldenmatch.fn(...)`) or `SET search_path = goldenmatch, public;` first.
-- The `goldenmatch_*` core-API functions return JSON text (except
-- goldenmatch_suggest_threshold, which returns DOUBLE PRECISION / NULL).
-- The `goldenflow_*` functions are scalar text -> text.

SET search_path = goldenmatch, public;

-- A toy table to profile / dedupe.
CREATE TABLE customers (name TEXT, email TEXT, phone TEXT);
INSERT INTO customers (name, email, phone) VALUES
    ('John Smith', 'JOHN@example.com ', '212-555-0100'),
    ('Jon Smyth',  'john@example.com',  '(212) 555-0100'),
    ('Jane Doe',   'jane@example.com',  '415-555-0199');

-- 1) Profile a table -- column stats, types, quality signals (JSON).
SELECT goldenmatch_profile_table('customers');

-- 2) Suggest a threshold from a score distribution (Otsu).
--    Returns SQL NULL when the distribution is unimodal / too few scores.
SELECT goldenmatch_suggest_threshold('[0.10, 0.12, 0.11, 0.90, 0.92, 0.91]');

-- 3) GoldenFlow transforms -- normalize a column before matching.
SELECT
    goldenflow_normalize_email(email)     AS email_norm,
    goldenflow_normalize_phone(phone)     AS phone_e164,
    goldenflow_whitespace_normalize(name) AS name_clean
FROM customers;

-- 4) Evaluate predicted pairs against ground truth (precision / recall / F1).
--    pairs: [[id_a, id_b, score], ...]   ground truth: [[id_a, id_b], ...]
SELECT goldenmatch_evaluate(
    '[[0, 1, 0.95], [0, 2, 0.40]]',
    '[[0, 1]]'
);
