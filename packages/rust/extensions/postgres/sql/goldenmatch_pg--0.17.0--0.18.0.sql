-- Upgrade goldenmatch_pg 0.17.0 -> 0.18.0
--
-- Adds goldenmatch_docs(): the in-SQL orientation function.
--
-- A SQL connection is the surface with the least for an agent to read. There is
-- no filesystem, no package to import, no README -- just function names. Every
-- other GoldenMatch surface now ships an llms.txt inside the installed artifact;
-- this is that file, returned from inside the session.
--
-- The text is include_str!'d into the shared library at build time, so the
-- function is IMMUTABLE, PARALLEL SAFE, and free of every dependency the rest of
-- the extension carries -- no embedded CPython, no SPI, no GUC, no file I/O. It
-- answers even in a backend where the Python bridge would fail to initialise,
-- which is exactly when an agent most needs to be told where the docs are.
--
--   SELECT goldenmatch.goldenmatch_docs();

CREATE FUNCTION "goldenmatch_docs"() RETURNS TEXT
IMMUTABLE PARALLEL SAFE
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_docs_wrapper';
