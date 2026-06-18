"""Smoke test for Verifier — template rendering + DB integration (no LLM call)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.verifier import build_agent, verify
from src.agents.base import load_prompt
from src.storage.sqlite import get_db

# 1. Agent build
agent = build_agent()
assert agent.prompt_name == "verifier"
assert set(agent.tools.keys()) == {"web_search", "web_fetch"}
assert agent.max_tool_rounds == 6
print(f"[OK] Agent: {agent.prompt_name}, tools={list(agent.tools.keys())}, max_rounds={agent.max_tool_rounds}")

# 2. Prompt
prompt = load_prompt("verifier")
assert "来源权威性" in prompt["system"]
assert "交叉验证" in prompt["system"]
assert "因果逻辑" in prompt["system"]
assert "pass" in prompt["user_template"]
assert "reject" in prompt["user_template"]

# Test template rendering
rendered = prompt["user_template"].format(
    game_name="test", yesterday_rank=3, today_rank=2, change_type="up",
    date="2026-06-16", finding_count=5, findings_json="[]"
)
assert "test" in rendered
assert "5" in rendered
print(f"[OK] Template renders ({len(rendered)} chars)")

# 3. verify() with empty findings (no API call)
result = verify({"game": "test", "bundle_id": "x", "findings": []})
assert result["summary"]["total_findings"] == 0
print(f"[OK] Empty findings: {result['summary']['overall_assessment']}")

# 4. DB: check latest research result is loadable
db = get_db()
rows = db._connect().execute(
    "SELECT id, findings_json FROM research_results WHERE verified_json IS NULL ORDER BY id DESC LIMIT 1"
).fetchall()
if rows:
    row = rows[0]
    research = json.loads(row["findings_json"])
    findings = research.get("findings", [])
    print(f"[OK] DB research id={row['id']}: {len(findings)} findings, ready to verify")
else:
    print(f"[SKIP] No unverified research results (all already verified or none exist)")

print("\n=== Verifier smoke test PASSED ===")
