-- goldenmatch_pg 0.7.0 -> 0.8.0 upgrade.
--
-- Generalizes goldenmatch_autoconfig with a `mode` argument: 'standard'
-- (default, iterative AutoConfigController) or 'probabilistic' (Fellegi-Sunter
-- matchkeys). The `mode TEXT DEFAULT 'standard'` keeps existing 1-arg calls
-- working. The new 2-arg signature replaces the old 1-arg function (same
-- 'goldenmatch_autoconfig_wrapper' symbol -- pgrx threads `mode` through).
DROP FUNCTION IF EXISTS "goldenmatch_autoconfig"(TEXT);
CREATE FUNCTION "goldenmatch_autoconfig"(
    "table_name" TEXT,
    "mode" TEXT DEFAULT 'standard'
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_autoconfig_wrapper';
