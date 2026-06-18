"""
P1 tests: Verifier output schema validation.

Uses mock findings — no LLM API calls.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.verifier import build_agent

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


# 1. Agent structure
agent = build_agent()
check("agent name", agent.prompt_name, "verifier")
check("2 tools", set(agent.tools.keys()), {"web_search", "web_fetch"})
check("6 rounds", agent.max_tool_rounds, 6)
check("4096 tokens", agent.max_tokens, 4096)

# 2. Verify output schema from DB (no LLM call)
from src.storage.sqlite import get_db
db = get_db()
rows = db._connect().execute(
    "SELECT id, verified_json FROM research_results WHERE verified_json IS NOT NULL LIMIT 1"
).fetchall()

if rows:
    verified = json.loads(rows[0]["verified_json"])
    if "parse_error" in verified:
        print("  [SKIP] Stored verification has parse error, skipping DB checks")
    else:
        fv_list = verified.get("findings_verified", [])
        if fv_list:
            fv = fv_list[0]
            check("has finding_index", "finding_index" in fv, True)
            check("has original_headline", "original_headline" in fv, True)
            check("has dimension", "dimension" in fv, True)
            check("has scores", "scores" in fv, True)
            if "scores" in fv:
                s = fv["scores"]
                check("source_authority in 1-5", 1 <= s.get("source_authority", 0) <= 5, True)
                check("cross_validation in 1-5", 1 <= s.get("cross_validation", 0) <= 5, True)
                check("causal_logic in 1-5", 1 <= s.get("causal_logic", 0) <= 5, True)
            check("has total_score", isinstance(fv.get("total_score"), (int, float)), True)
            check("has verdict", fv.get("verdict") in ("pass", "reject"), True)
            check("has verification_notes", isinstance(fv.get("verification_notes"), str), True)
            check("has cross_references", isinstance(fv.get("cross_references"), list), True)
        summary = verified.get("summary", {})
        check("summary has passed", "passed" in summary, True)
        check("summary has rejected", "rejected" in summary, True)
        check("summary has average_score", isinstance(summary.get("average_score"), (int, float)), True)
        check("summary has overall_assessment", isinstance(summary.get("overall_assessment"), str), True)


print(f"\n{'='*50}")
print(f"  Verifier Output: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
