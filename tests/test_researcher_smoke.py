"""Smoke test for Researcher — tests DB lookup + template rendering only (no LLM call)."""
import json
from src.agents.researcher import build_agent, _safe_str, _safe_int
from src.agents.base import load_prompt
from src.storage.sqlite import get_db

# 1. Verify agent build
agent = build_agent()
assert agent.prompt_name == "researcher"
assert set(agent.tools.keys()) == {"web_search", "web_fetch", "db_query"}
assert agent.max_tool_rounds == 12
print(f"[OK] Agent: {agent.prompt_name}, tools={list(agent.tools.keys())}, max_rounds={agent.max_tool_rounds}")

# 2. Verify prompt loads and template renders
prompt = load_prompt("researcher")
assert "五个搜索维度" in prompt["system"]
assert "in_development_signals" in prompt["user_template"]
# Test template rendering with sample data
try:
    rendered = prompt["user_template"].format(
        game_name="测试游戏",
        bundle_id="com.test.game",
        developer="测试工作室",
        platform="iOS",
        today_rank=5,
        yesterday_rank=10,
        rank_change=5,
        change_type="up",
        date="2026-06-16",
        context_from_scanner="进入前5名，建议深度调研",
    )
    assert "测试游戏" in rendered
    assert "com.test.game" in rendered
    assert "5" in rendered
    print(f"[OK] Template renders correctly ({len(rendered)} chars)")
except KeyError as e:
    print(f"[FAIL] Template missing key: {e}")
    exit(1)

# 3. Verify DB lookup works (find a change for default CLI mode)
db = get_db()
dates = db.get_available_dates()
if dates:
    latest = dates[0]
    changes = db.get_changes_by_date(latest)
    if changes:
        top = changes[0]
        print(f"[OK] Default change: {top['game_name']} ({top['change_type']}, score={top['attention_score']})")
    else:
        print(f"[OK] No changes for {latest} (first day?)")
else:
    print("[SKIP] No data in DB")

# 4. Verify helper functions
assert _safe_str(None) == "未知"
assert _safe_str("hello") == "hello"
assert _safe_int(None) == "N/A"
assert _safe_int(42) == 42
assert _safe_int("99") == 99
print("[OK] Helper functions")

print("\n=== Researcher smoke test PASSED ===")
