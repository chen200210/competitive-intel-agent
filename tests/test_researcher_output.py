"""
P1 tests: Researcher output schema validation.

Uses mock tools — no LLM API calls.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.researcher import build_agent, _safe_str, _safe_int, _build_search_cache_hint

passed = 0
failed = 0

def check(name: str, actual, expected, note: str = ""):
    global passed, failed
    if isinstance(expected, type):
        ok = isinstance(actual, expected)
    else:
        ok = actual == expected
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        extra = f" ({note})" if note else ""
        print(f"  [FAIL] {name}: expected={repr(expected)}, got={repr(actual)}{extra}")


# ═══════════════════════════════════════════════════════════════
# Use a real change from DB (only tests schema, doesn't call LLM)
# The research() function exists — we test the helpers and DB lookup
# ═══════════════════════════════════════════════════════════════

from src.agents.researcher import build_agent, _safe_str, _safe_int, _build_search_cache_hint

# 1. Agent structure
agent = build_agent()
check("agent name", agent.prompt_name, "researcher")
check("3 tools", set(agent.tools.keys()), {"web_search", "web_fetch", "db_query"})
check("12 rounds", agent.max_tool_rounds, 12)
check("8192 tokens", agent.max_tokens, 8192)

# 2. Helpers
check("_safe_str None", _safe_str(None), "未知")
check("_safe_str value", _safe_str("hello"), "hello")
check("_safe_int None", _safe_int(None), "N/A")
check("_safe_int value", _safe_int(42), 42)
check("_safe_int str", _safe_int("99"), 99)

# 3. Cache hint builder (non-empty when data exists)
hint = _build_search_cache_hint("鸣潮")
check("cache hint is str", isinstance(hint, str), True)
if hint:
    check("cache hint mentions searches", "已搜过" in hint or "search_cache" in hint.lower(), True)

# 4. Change dict structure (don't call research() — it would trigger LLM)
change = {
    "game_name": "测试", "bundle_id": "com.test", "developer": "测试工作室",
    "platform": "iOS", "today_rank": 5, "yesterday_rank": 10,
    "rank_change": 5, "change_type": "up", "date": "2026-06-16",
}
check("change has all fields", all(k in change for k in
      ["game_name", "bundle_id", "developer", "today_rank", "change_type"]), True)

# 6. DB lookup for existing research
from src.storage.sqlite import get_db
db = get_db()
rows = db._connect().execute(
    "SELECT id, findings_json FROM research_results WHERE json_extract(findings_json, '$.findings') IS NOT NULL LIMIT 1"
).fetchall()
if rows:
    data = json.loads(rows[0]["findings_json"])
    findings = data.get("findings", [])
    if findings:
        f0 = findings[0]
        check("finding has dimension", "dimension" in f0, True)
        check("finding has headline", "headline" in f0, True)
        check("finding has sources", isinstance(f0.get("sources"), list), True)
        if f0.get("sources"):
            s0 = f0["sources"][0]
            check("source has url", "url" in s0, True)
            check("source has source_type", "source_type" in s0, True)
            # V2: check fetch_status
            if "fetch_status" in s0:
                check("source has fetch_status (V2)", s0["fetch_status"] in ("success", "failed"), True)
    check("has in_development_signals", "in_development_signals" in data, True)
    check("has search_coverage", "search_coverage" in data, True)


print(f"\n{'='*50}")
print(f"  Researcher Output: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
