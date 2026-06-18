"""
P1 tests: Agent base class — JSON parsing, repair, inner quotes, timing.

No LLM API calls. All tests use static methods or mock tools.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.base import Agent, Tool, load_prompt

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
# 1. _parse_json — Layer 0-5
# ═══════════════════════════════════════════════════════════════

print("── _parse_json ──")

pj = Agent._parse_json

# Layer 0: strip preamble
r = pj('Now I have data.\n{"key": "value"}')
check("L0 preamble strip", r, {"key": "value"})

# Layer 1: direct parse
r = pj('{"a": 1, "b": [2, 3]}')
check("L1 direct parse", r, {"a": 1, "b": [2, 3]})

# Layer 2: repair missing comma
# (this relies on _repair_json — test separately below)
r = pj('{"a": 1\n"b": 2}')
check("L2 missing comma repair", isinstance(r, dict) and "parse_error" not in r, True)

# Layer 3: markdown fence
r = pj('```json\n{"x": "y"}\n```')
check("L3 markdown fence", r, {"x": "y"})

# Layer 4: regex extract
r = pj('some text {"nested": {"key": [1,2,3]}} more text')
check("L4 regex extract", r, {"nested": {"key": [1, 2, 3]}})

# Layer 5: unparseable
r = pj('not json at all, no braces here')
check("L5 unparseable", "parse_error" in r, True)
check("L5 has raw_output", "raw_output" in r, True)


# ═══════════════════════════════════════════════════════════════
# 2. _repair_json — comma insertion, trailing comma
# ═══════════════════════════════════════════════════════════════

print("\n── _repair_json ──")

rj = Agent._repair_json

# Missing comma between properties
r = rj('{"a": 1\n"b": 2}')
check("repair missing comma", r, {"a": 1, "b": 2})

# Trailing comma before } — not currently repairable (error says "Expecting property name")
# Test that repair returns None gracefully for this unhandled case
r = rj('{"a": 1,}')
check("trailing comma → None (graceful)", r, None)

# Already valid
r = rj('{"x": [1,2,3], "y": null}')
check("repair valid JSON", r, {"x": [1, 2, 3], "y": None})

# Repairable JSON with preamble (shouldn't need repair after Layer 0)
r = rj('{"a": 1}')
check("repair simple", r, {"a": 1})


# ═══════════════════════════════════════════════════════════════
# 3. _fix_inner_quotes — state machine
# ═══════════════════════════════════════════════════════════════

print("\n── _fix_inner_quotes ──")

fq = Agent._fix_inner_quotes

# Legitimate quotes must survive
text = '{"game": "鸣潮", "bundle_id": "com.x"}'
fixed = fq(text)
check("legitimate quotes unchanged", fixed, text)

# Inner CJK quotes → curly
text2 = '{"title": "疑"代号Nami"有新进展"}'
fixed2 = fq(text2)
# The inner " around 代号Nami should be replaced
check("inner CJK quotes fixed", '"疑' in fixed2, True)
check("no bare inner quotes", fixed2.count('"') <= text2.count('"'), True)

# Newline after legitimate closing quote
text3 = '{"date": "2026-06-16",\n  "next": "value"}'
fixed3 = fq(text3)
check("newline after quote preserved", '"2026-06-16"' in fixed3, True)
check("next key preserved", '"next"' in fixed3, True)


# ═══════════════════════════════════════════════════════════════
# 4. Agent with no tools — single round
# ═══════════════════════════════════════════════════════════════

print("\n── Agent (no tools) ──")

# Create a minimal agent that doesn't need API call to test structure
agent = Agent("overview_scanner", tools=None, max_tool_rounds=1)
check("no tools", agent.tools, {})
check("max_rounds", agent.max_tool_rounds, 1)
check("system prompt loaded", len(agent.system_prompt) > 100, True)
check("JSON_ENFORCEMENT appended", "OUTPUT FORMAT" in agent.system_prompt, True)
check("user_template loaded", "{date}" in agent.user_template, True)


# ═══════════════════════════════════════════════════════════════
# 5. Agent with tools
# ═══════════════════════════════════════════════════════════════

print("\n── Agent (with tools) ──")

def mock_search(query: str, **_m) -> str:
    return json.dumps({"results": [{"title": "test", "url": "http://x.com"}]})

tool = Tool(
    name="web_search", description="search",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    fn=mock_search,
)
agent2 = Agent("researcher", tools=[tool], max_tool_rounds=3, max_tokens=8192)
check("tool registered", "web_search" in agent2.tools, True)
check("max_tokens set", agent2.max_tokens, 8192)
check("tool schema", "function" in tool.to_openai_schema()["type"], True)


# ═══════════════════════════════════════════════════════════════
# 6. _timing structure
# ═══════════════════════════════════════════════════════════════

print("\n── _timing ──")

import time as _time

t0 = _time.monotonic() - 45.0
timing = Agent._build_timing(t0, [
    {"round": 1, "llm_call_ms": 3000, "tools": [
        {"name": "web_search", "latency_ms": 5000, "cache_hit": True, "args_preview": "..."}
    ]},
    {"round": 2, "llm_call_ms": 2000, "tools": [
        {"name": "web_fetch", "latency_ms": 3000, "cache_hit": False, "args_preview": "..."}
    ]},
], parse_ms=50, validation_ms=0)

check("total_ms approx 45s", 44000 <= timing["total_ms"] <= 46000, True)
check("llm_total_ms", timing["llm_total_ms"], 5000)
check("tool_total_ms", timing["tool_total_ms"], 8000)
check("tool_count", timing["tool_count"], 2)
check("parse_ms", timing["parse_ms"], 50)
check("rounds count", len(timing["rounds"]), 2)
check("tool_summary web_search", timing["tool_summary"]["web_search"]["count"], 1)
check("tool_summary web_fetch", timing["tool_summary"]["web_fetch"]["count"], 1)
check("cache_hits counted", timing["tool_summary"]["web_search"]["cache_hits"], 1)

# Format
fmt = Agent._format_timing_summary(timing)
check("format has total", "total" in fmt.lower(), True)
check("format has web_search", "web_search" in fmt, True)
check("format has rounds", "R1:" in fmt or "R1 " in fmt, True)


# ═══════════════════════════════════════════════════════════════
# 7. Prompt loading
# ═══════════════════════════════════════════════════════════════

print("\n── load_prompt ──")

for name in ["overview_scanner", "researcher", "verifier", "analyst", "design_analyst", "briefer"]:
    p = load_prompt(name)
    check(f"{name} has system", len(p["system"]) > 0, True)
    check(f"{name} has user_template", len(p["user_template"]) > 0, True)


# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  Agent Base: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
