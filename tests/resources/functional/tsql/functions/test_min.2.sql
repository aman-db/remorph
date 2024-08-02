-- ## MIN with DISTINCT
--
-- The MIN function is identical in Databricks SQL and T-SQL. As DISTINCT is merely removing duplicates,
-- its presence or otherwise is irrelevant to the MAX function.

-- tsql sql:
SELECT MIN(DISTINCT col1) FROM t1;

-- databricks sql:
SELECT MIN(DISTINCT col1) FROM t1;
