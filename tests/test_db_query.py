"""Quick test for db_query validation logic."""
from src.tools.db_query import _validate_sql, TOOL_DESCRIPTOR

# Test validation: allowed
assert _validate_sql('SELECT * FROM rankings') is None
assert _validate_sql('WITH cte AS (SELECT 1) SELECT * FROM cte') is None

# Test validation: forbidden
for bad in ['DROP TABLE x', 'INSERT INTO x VALUES(1)', 'DELETE FROM x', 'UPDATE x SET y=1']:
    err = _validate_sql(bad)
    assert err is not None, f'Should reject: {bad}'

err = _validate_sql('SELECT')
assert err is not None

print('db_query: All validation tests PASSED')
print(f'Tool: {TOOL_DESCRIPTOR["name"]}')
