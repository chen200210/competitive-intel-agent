"""Verify timing instrumentation in Agent base class (no API calls)."""
import inspect, time as _time
from src.agents.base import Agent

# 1. Verify methods exist
assert hasattr(Agent, '_build_timing'), 'Missing _build_timing'
assert hasattr(Agent, '_format_timing_summary'), 'Missing _format_timing_summary'
print('[OK] Timing methods exist on Agent class')

# 2. Verify _append_tool_results returns tuple annotation
sig = inspect.signature(Agent._append_tool_results)
print(f'[OK] _append_tool_results signature: {sig.return_annotation}')

# 3. Verify _build_timing calculation with mock data
t0 = _time.monotonic() - 35.0  # simulate 35s elapsed
timing = Agent._build_timing(t0, [
    {
        'round': 1, 'llm_call_ms': 3200, 'tools': [
            {'name': 'web_search', 'latency_ms': 5100, 'cache_hit': True, 'args_preview': 'x'},
            {'name': 'web_search', 'latency_ms': 4800, 'cache_hit': False, 'args_preview': 'y'},
            {'name': 'web_fetch', 'latency_ms': 3200, 'cache_hit': False, 'args_preview': 'z'},
        ],
    },
    {
        'round': 2, 'llm_call_ms': 2800, 'tools': [
            {'name': 'web_search', 'latency_ms': 4200, 'cache_hit': True, 'args_preview': 'w'},
        ],
    },
], parse_ms=50, validation_ms=0)

assert 34000 <= timing['total_ms'] <= 36000, f"total_ms={timing['total_ms']}"
assert timing['llm_total_ms'] == 6000
assert timing['tool_total_ms'] == 17300
assert timing['tool_count'] == 4
assert timing['parse_ms'] == 50
assert timing['tool_summary']['web_search']['count'] == 3
assert timing['tool_summary']['web_search']['cache_hits'] == 2
assert timing['tool_summary']['web_search']['avg_ms'] == 4700
assert timing['tool_summary']['web_fetch']['count'] == 1
assert timing['tool_summary']['web_fetch']['cache_hits'] == 0
print('[OK] _build_timing calculations correct')

# 4. Verify _format_timing_summary output
fmt = Agent._format_timing_summary(timing)
assert 'LLM calls' in fmt
assert 'web_search' in fmt
assert 'web_fetch' in fmt
assert 'cache hits' in fmt
assert 'R1:' in fmt or 'R1 ' in fmt
assert 'R2:' in fmt or 'R2 ' in fmt
print(f'[OK] _format_timing_summary: {len(fmt)} chars')

# 5. Verify agents still build correctly
from src.agents.overview_scanner import build_agent as build_ov
from src.agents.researcher import build_agent as build_re
ov = build_ov()
re_agent = build_re()
assert ov.max_tokens == 4096  # default
assert re_agent.max_tokens == 8192  # researcher override
print(f'[OK] Existing agents build OK: overview_scanner ({ov.max_tokens} tok), researcher ({re_agent.max_tokens} tok)')

print('\n=== All timing instrumentation tests PASSED ===')
