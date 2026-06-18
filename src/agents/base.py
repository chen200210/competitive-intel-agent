"""
Agent base class — LLM invocation + tool-use loop + structured output.

Three layers of JSON guarantee (no LangChain needed):
  1. response_format {"type": "json_object"} — LLM-level forced JSON
  2. Pydantic schema validation + auto-retry (optional)
  3. Regex fallback extraction (last resort)

Uses OpenAI SDK to call DeepSeek (OpenAI-compatible protocol).

Usage:
    agent = Agent("overview_scanner", tools=[web_search], output_schema=MyModel)
    result = agent.run(date="2026-06-16", platform="iOS", ...)
"""

from __future__ import annotations

import json
import re
import time as _time
import uuid as _uuid
from typing import Any

import yaml
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src.config import settings

# ── Prompt loading ─────────────────────────────────────────────

PROMPTS_DIR = settings.prompts_dir

# Appended to every system prompt to enforce JSON-only output
JSON_ENFORCEMENT = """
 OUTPUT FORMAT:
- You MUST respond with valid JSON only.
- No markdown fences (```json), no explanation, no preamble.
- The response must start with '{' and end with '}'.
- CRITICAL: escape ALL double-quote characters (\") that appear inside JSON string values.
  Use backslash-escaped quotes: \\"text\\".  Never output raw " inside a JSON string.
  Example: "title": "Tom said \\"hello\\" to everyone"
  Chinese quotation marks 「」『』「」 are safe and do NOT need escaping.
"""


def load_prompt(name: str) -> dict[str, str]:
    """Load system + user_template from YAML file."""
    path = PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {
        "system": data.get("system", ""),
        "user_template": data.get("user_template", ""),
    }


# ── Tool definition ────────────────────────────────────────────

class Tool(BaseModel):
    """A tool that an Agent can call.

    name:        unique tool identifier (e.g. "web_search")
    description: what it does, passed to the LLM
    parameters:  JSON Schema for the tool's arguments
    fn:          callable receiving **kwargs, returning str
    """
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Any  # callable

    class Config:
        arbitrary_types_allowed = True

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ── Agent ──────────────────────────────────────────────────────

class Agent:
    """LLM Agent with tool-use loop and structured output.

    Uses DeepSeek via OpenAI-compatible SDK.
    Supports multi-turn tool calling: LLM decides → Agent executes → LLM continues.

    JSON enforcement strategy:
      - During tool-use rounds: LLM controls flow, may output text + tool_calls
      - Final round (no tool calls): response_format=json_object forces valid JSON
      - If output_schema is set: Pydantic validates + retries up to max_retries on failure
      - If all else fails: regex extracts {}-block from raw text
    """

    def __init__(
        self,
        prompt_name: str,
        tools: list[Tool] | None = None,
        output_schema: type[BaseModel] | None = None,
        model: str | None = None,
        max_tool_rounds: int = 5,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ):
        self.prompt_name = prompt_name
        self.tools: dict[str, Tool] = {}
        for t in (tools or []):
            self.tools[t.name] = t
        self.output_schema = output_schema
        self.model = model or settings.deepseek_model
        self.max_tool_rounds = max_tool_rounds
        self.max_retries = max_retries
        self.max_tokens = max_tokens

        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

        # Load prompt template
        prompt = load_prompt(prompt_name)
        self.system_prompt = prompt["system"] + JSON_ENFORCEMENT
        self.user_template = prompt["user_template"]

    # ── Public API ──────────────────────────────────────────

    @staticmethod
    def _v(msg: str) -> None:
        """Print verbose output to stderr (won't mix with JSON stdout)."""
        import sys
        print(msg, file=sys.stderr, flush=True)

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """Render template, call LLM with tool loop, return validated dict.

        Kwargs are interpolated into user_template via str.format().
        Special kwargs: _verbose (bool) — print tool calls & results to stderr.

        Returns dict includes _timing with per-round and per-tool latency breakdown.
        """
        verbose = bool(kwargs.pop("_verbose", False))
        run_id = _uuid.uuid4().hex[:12]  # short UUID for audit trail
        target_date = kwargs.get("date", "")
        user_message = self.user_template.format(**kwargs)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]

        if verbose:
            self._v("=" * 60)
            self._v(f"Agent: {self.prompt_name}  |  run_id: {run_id}")
            self._v(f"Tools: {', '.join(self.tools.keys()) or 'none'}")
            self._v(f"Max tool rounds: {self.max_tool_rounds}  |  max_tokens: {self.max_tokens}")
            self._v("=" * 60)

        # ── Timing ──
        t_total = _time.monotonic()
        timing_rounds: list[dict[str, Any]] = []
        timing_parse_ms = 0
        timing_validation_ms = 0

        # ── Tool loop ──
        for round_num in range(self.max_tool_rounds):
            t_round = _time.monotonic()
            if verbose:
                self._v(f"\n--- Round {round_num + 1}: calling LLM ---")
            response = self._call_llm(messages, use_tools=True)
            llm_call_ms = int((_time.monotonic() - t_round) * 1000)

            tool_calls = self._extract_tool_calls(response)
            if not tool_calls:
                # No tool calls → final answer
                if verbose:
                    content_preview = (response.choices[0].message.content or "")[:200]
                    self._v(f"[LLM final answer] llm={llm_call_ms}ms  |  {content_preview}...")
                content = response.choices[0].message.content or ""

                t_parse = _time.monotonic()
                result = self._parse_and_validate(content)
                timing_parse_ms = int((_time.monotonic() - t_parse) * 1000)

                # Record the final round for timing completeness
                timing_rounds.append({
                    "round": round_num + 1,
                    "llm_call_ms": llm_call_ms,
                    "tools": [],
                    "note": "final answer",
                })

                result["_run_id"] = run_id
                result["_timing"] = self._build_timing(
                    t_total, timing_rounds, timing_parse_ms, timing_validation_ms,
                )
                if verbose:
                    self._v(self._format_timing_summary(result["_timing"]))
                return result

            if verbose:
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    args_preview = tc["function"]["arguments"][:120]
                    self._v(f"  -> LLM wants to call: {fn}({args_preview}...)")

            # Execute tools and continue
            messages, tool_timings = self._append_tool_results(
                messages, response, tool_calls,
                run_id=run_id, target_date=target_date, round_num=round_num, verbose=verbose,
            )
            timing_rounds.append({
                "round": round_num + 1,
                "llm_call_ms": llm_call_ms,
                "tools": tool_timings,
            })

        # Max rounds exceeded → force finalize with JSON mode
        if verbose:
            self._v(f"\n--- Max rounds ({self.max_tool_rounds}) reached, forcing JSON output ---")
        t_final_llm = _time.monotonic()
        messages.append({
            "role": "user",
            "content": "请基于以上所有工具返回的结果，输出最终的 JSON 分析。",
        })
        final = self._call_llm(messages, use_tools=False, force_json=True)
        final_llm_ms = int((_time.monotonic() - t_final_llm) * 1000)
        timing_rounds.append({
            "round": self.max_tool_rounds + 1,
            "llm_call_ms": final_llm_ms,
            "tools": [],
            "note": "forced finalization",
        })

        content = final.choices[0].message.content or ""
        t_parse = _time.monotonic()
        result = self._parse_and_validate(content)
        timing_parse_ms = int((_time.monotonic() - t_parse) * 1000)

        result["_run_id"] = run_id
        result["_timing"] = self._build_timing(
            t_total, timing_rounds, timing_parse_ms, timing_validation_ms,
        )
        if verbose:
            self._v(self._format_timing_summary(result["_timing"]))
        return result

    # ── LLM invocation ──────────────────────────────────────

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        use_tools: bool = False,
        force_json: bool = False,
    ) -> Any:
        """Send messages to DeepSeek, return raw response object.

        Args:
            messages:   conversation history.
            use_tools:  if True, include tool definitions + tool_choice=auto.
            force_json: if True, use response_format=json_object to guarantee JSON.
                        (Cannot be combined with use_tools=True.)
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }

        if use_tools and self.tools:
            kwargs["tools"] = [t.to_openai_schema() for t in self.tools.values()]
            kwargs["tool_choice"] = "auto"

        if force_json:
            # response_format=json_object forces the LLM to output valid JSON.
            # DeepSeek supports this (OpenAI-compatible).
            kwargs["response_format"] = {"type": "json_object"}

        return self.client.chat.completions.create(**kwargs)

    # ── Tool execution ──────────────────────────────────────

    def _extract_tool_calls(self, response: Any) -> list[dict[str, Any]]:
        """Extract tool_calls from OpenAI response, converting to dicts."""
        msg = response.choices[0].message
        if not msg.tool_calls:
            return []
        result: list[dict[str, Any]] = []
        for tc in msg.tool_calls:
            result.append({
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
        return result

    def _append_tool_results(
        self,
        messages: list[dict[str, Any]],
        response: Any,
        tool_calls: list[dict[str, Any]],
        run_id: str = "",
        target_date: str = "",
        round_num: int = 0,
        verbose: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Execute tool calls, append assistant + tool messages, return (messages, timings).

        Returns:
            (messages, tool_timings) where tool_timings is a list of
            {name, args_preview, latency_ms, cache_hit} per tool call.
        """
        messages.append({
            "role": "assistant",
            "content": response.choices[0].message.content,
            "tool_calls": tool_calls,
        })

        tool_timings: list[dict[str, Any]] = []

        # ── Prepare all tool calls (resolve args) ──
        prepared: list[dict[str, Any]] = []
        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            args["_called_by"] = self.prompt_name
            args["_run_id"] = run_id
            args["_target_date"] = target_date
            prepared.append({
                "tc": tc,
                "tool_name": tool_name,
                "args": args,
            })

        # ── Execute all tools in parallel (I/O-bound, ThreadPoolExecutor fine) ──
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Map tool_call_id → result for ordered message appending
        results_map: dict[str, Any] = {}
        timings_map: dict[str, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=min(len(prepared), 6)) as ex:
            futures = {}
            for p in prepared:
                f = ex.submit(
                    self._execute_tool,
                    p["tool_name"], p["args"],
                    run_id, target_date, round_num,
                )
                futures[f] = p

            for f in as_completed(futures):
                p = futures[f]
                tc = p["tc"]
                tool_name = p["tool_name"]
                latency_ms = 0  # approximate; _execute_tool doesn't return timing
                try:
                    result = f.result()
                except Exception as e:
                    result = json.dumps({"error": str(e)}, ensure_ascii=False)

                # Detect cache hit
                cache_hit = None
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and "cache_hit" in parsed:
                        cache_hit = bool(parsed["cache_hit"])
                except (json.JSONDecodeError, TypeError):
                    pass

                results_map[tc["id"]] = result
                timings_map[tc["id"]] = {
                    "name": tool_name,
                    "args_preview": tc["function"]["arguments"][:120],
                    "latency_ms": latency_ms,
                    "cache_hit": cache_hit,
                }

        # ── Append results in original tool_call order ──
        for tc in tool_calls:
            result = results_map.get(tc["id"], json.dumps({"error": "no result"}))
            timing = timings_map.get(tc["id"], {})
            tool_timings.append(timing)

            if verbose:
                cache_tag = " [CACHE]" if timing.get("cache_hit") else ""
                preview = result[:300].replace("\n", " ") if isinstance(result, str) else str(result)[:300]
                self._v(f"  <- {timing.get('name', '?')} ({timing.get('latency_ms', '?')}ms{cache_tag}): {preview}...")
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        return messages, tool_timings

    def _execute_tool(
        self, name: str, args: dict[str, Any],
        run_id: str = "", target_date: str = "", round_num: int = 0,
    ) -> str:
        """Run a registered tool and return its result string. Logs to audit trail."""
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

        t0 = _time.time()
        try:
            result = tool.fn(**args)
            latency_ms = int((_time.time() - t0) * 1000)
        except Exception as e:
            latency_ms = int((_time.time() - t0) * 1000)
            result = json.dumps({"error": str(e)}, ensure_ascii=False)

        result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        # Safe truncation: cut at last complete JSON token before limit.
        # This prevents breaking multi-byte chars or splitting JSON strings.
        limit = 12000
        if len(result_str) > limit:
            safe = result_str[:limit]
            # Walk back to find a safe cut point (end of a JSON value)
            for cut in range(len(safe), max(0, len(safe) - 500), -1):
                ch = safe[cut - 1]
                if ch in ('}', ']', '\n'):
                    result_str = safe[:cut] + "\n...(truncated)"
                    break
            else:
                result_str = safe + "...(truncated)"

        # Detect cache hit from result
        cache_hit = None
        try:
            parsed = json.loads(result_str)
            if isinstance(parsed, dict) and "cache_hit" in parsed:
                cache_hit = bool(parsed["cache_hit"])
        except (json.JSONDecodeError, TypeError):
            pass

        # ── Audit log (fire-and-forget) ──
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            db.insert_audit_log(
                agent_name=self.prompt_name,
                run_id=run_id,
                target_date=target_date,
                round_num=round_num + 1,
                tool_name=name,
                tool_args_json=json.dumps(args, ensure_ascii=False),
                tool_result_preview=result_str[:2000],
                tool_result_length=len(result_str),
                cache_hit=cache_hit,
                latency_ms=latency_ms,
            )
        except Exception:
            pass  # audit logging must never block the agent

        return result_str

    # ── JSON parsing + validation ────────────────────────────

    def _parse_and_validate(self, content: str) -> dict[str, Any]:
        """Parse LLM output to dict. If output_schema is set, validate + retry."""
        parsed = self._parse_json(content)

        if self.output_schema is not None:
            for attempt in range(self.max_retries + 1):
                try:
                    self.output_schema.model_validate(parsed)
                    return parsed  # passes schema validation
                except ValidationError as e:
                    if attempt >= self.max_retries:
                        # Return raw dict but mark validation errors
                        parsed["_schema_errors"] = str(e)
                        return parsed
                    # Retry: ask LLM to fix invalid fields
                    parsed = self._retry_fix(e.errors())

        return parsed

    # ── Timing instrumentation (§16.1) ─────────────────────────

    @staticmethod
    def _build_timing(
        t_total_start: float,
        timing_rounds: list[dict[str, Any]],
        parse_ms: int,
        validation_ms: int,
    ) -> dict[str, Any]:
        """Build _timing dict from collected per-round and per-tool data."""
        total_ms = int((_time.monotonic() - t_total_start) * 1000)

        # Aggregate per-tool stats
        tool_stats: dict[str, dict[str, Any]] = {}
        total_llm_ms = 0
        total_tool_ms = 0
        total_tool_count = 0

        for r in timing_rounds:
            total_llm_ms += r.get("llm_call_ms", 0)
            for t in r.get("tools", []):
                name = t["name"]
                if name not in tool_stats:
                    tool_stats[name] = {"count": 0, "total_ms": 0, "cache_hits": 0}
                tool_stats[name]["count"] += 1
                tool_stats[name]["total_ms"] += t["latency_ms"]
                if t.get("cache_hit"):
                    tool_stats[name]["cache_hits"] += 1
                total_tool_ms += t["latency_ms"]
                total_tool_count += 1

        # Compute averages
        tool_summary = {}
        for name, stats in sorted(tool_stats.items()):
            tool_summary[name] = {
                "count": stats["count"],
                "total_ms": stats["total_ms"],
                "avg_ms": stats["total_ms"] // stats["count"] if stats["count"] else 0,
                "cache_hits": stats["cache_hits"],
            }

        return {
            "total_ms": total_ms,
            "llm_total_ms": total_llm_ms,
            "tool_total_ms": total_tool_ms,
            "tool_count": total_tool_count,
            "parse_ms": parse_ms,
            "validation_ms": validation_ms,
            "rounds": timing_rounds,
            "tool_summary": tool_summary,
        }

    @staticmethod
    def _format_timing_summary(timing: dict[str, Any]) -> str:
        """Format _timing dict as a human-readable summary for stderr output."""
        total_s = timing["total_ms"] / 1000
        lines = [
            "",
            "─" * 40,
            f"  Timing: {total_s:.1f}s total",
            f"    LLM calls:  {timing['llm_total_ms'] / 1000:.1f}s ({len(timing['rounds'])} rounds)",
            f"    Tool exec:  {timing['tool_total_ms'] / 1000:.1f}s ({timing['tool_count']} calls)",
            f"    JSON parse: {timing['parse_ms']}ms",
        ]
        if timing.get("validation_ms"):
            lines.append(f"    Validation: {timing['validation_ms']}ms")

        if timing.get("tool_summary"):
            lines.append("")
            lines.append("  Per-tool breakdown:")
            for name, stats in timing["tool_summary"].items():
                cache_str = f", {stats['cache_hits']} cache hits" if stats["cache_hits"] else ""
                lines.append(
                    f"    {name}: {stats['count']}x, "
                    f"total={stats['total_ms'] / 1000:.1f}s, "
                    f"avg={stats['avg_ms']}ms"
                    + cache_str
                )

        # Per-round detail
        lines.append("")
        lines.append("  Per-round detail:")
        for r in timing["rounds"]:
            tools_str = ""
            for t in r.get("tools", []):
                cache_tag = " [C]" if t.get("cache_hit") else ""
                tools_str += f" | {t['name']}({t['latency_ms']}ms{cache_tag})"
            note = f" ({r.get('note')})" if r.get("note") else ""
            lines.append(
                f"    R{r['round']}: LLM={r['llm_call_ms']}ms{note}{tools_str}"
            )

        lines.append("─" * 40)
        return "\n".join(lines)

    def _retry_fix(self, errors: list[dict[str, Any]]) -> dict[str, Any]:
        """Ask LLM to fix validation errors in a single-turn correction."""
        error_desc = json.dumps(errors, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": (
                    f"你上一次输出的 JSON 有以下字段不符合 schema，请修正后重新输出完整 JSON：\n\n"
                    f"Schema 校验错误：\n{error_desc}\n\n"
                    f"只输出修正后的完整 JSON，不要其他文字。"
                ),
            },
        ]
        response = self._call_llm(messages, force_json=True)
        content = response.choices[0].message.content or ""
        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        """Extract JSON from LLM response with layered fallbacks.

        Layer 0: strip leading text before first '{' (LLM preamble like "Now I have...")
        Layer 1: direct json.loads (works when response_format=json_object)
        Layer 2: auto-repair common LLM JSON errors (missing commas, trailing commas)
        Layer 3: strip ```json fences then parse
        Layer 4: regex extract first {}-block
        Layer 5: return raw text as error dict
        """
        text = content.strip()

        # Layer 0: strip preamble before first '{' (LLMs sometimes add human text)
        brace_idx = text.find('{')
        if brace_idx > 0:
            text = text[brace_idx:]

        # Layer 1: direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Layer 2: auto-repair common LLM JSON errors
        # DeepSeek occasionally produces minor syntax errors on very long outputs.
        # Common patterns: missing comma between properties, trailing comma before }]
        repaired = Agent._repair_json(text)
        if repaired is not None:
            return repaired

        # Layer 3: strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # Layer 4: regex find the first { } block
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Layer 5: give up
        return {
            "raw_output": content,
            "parse_error": "All JSON parse layers failed — LLM did not output valid JSON",
        }

    @staticmethod
    def _repair_json(text: str, max_repairs: int = 12) -> dict[str, Any] | None:
        """Attempt to repair common JSON syntax errors from LLM output.

        Handles: unescaped quotes in CJK text, missing commas, trailing commas.
        Works iteratively — fixes one error at a time, up to max_repairs times.
        Returns parsed dict or None.
        """
        # Pre-repair: replace bare ASCII " used as Chinese quotation marks
        # inside JSON string values.  Uses a state machine: after an odd number
        # of unescaped ", we're inside a string — any " that is NOT followed by
        # a JSON structural character (,:}]) is an unescaped inner quote.
        working = Agent._fix_inner_quotes(text)

        for _ in range(max_repairs):
            try:
                return json.loads(working)
            except json.JSONDecodeError as e:
                msg = str(e)
                pos = e.pos

                # Pattern 1: missing comma
                if "Expecting ',' delimiter" in msg:
                    working = working[:pos] + ',' + working[pos:]
                    continue

                # Pattern 2: trailing comma before } or ]
                if "Expecting value" in msg:
                    if pos < len(working) and working[pos] in ('}', ']'):
                        comma_pos = working.rfind(',', 0, pos)
                        if comma_pos > 0:
                            working = working[:comma_pos] + working[comma_pos + 1:]
                            continue
                    break

                # Pattern 3: unrepairable structural error
                if "Expecting ':'" in msg:
                    break

                # Pattern 4: invalid character — try a targeted CJK-quote fix
                # around the error position
                if "Invalid" in msg or "control character" in msg:
                    ctx_start = max(0, pos - 40)
                    ctx_end = min(len(working), pos + 40)
                    ctx = working[ctx_start:ctx_end]
                    # Replace bare " between CJK/alnum chars in this region
                    fixed_ctx = re.sub(
                        r'([一-鿿㐀-䶿a-zA-Z0-9])"([一-鿿㐀-䶿a-zA-Z0-9])',
                        r'\1“\2',
                        ctx
                    )
                    if fixed_ctx != ctx:
                        working = working[:ctx_start] + fixed_ctx + working[ctx_end:]
                        continue
                    break

                # Unknown error
                break

        return None

    @staticmethod
    def _is_cjk_or_alnum(ch: str) -> bool:
        """Check if a character is CJK, ASCII alphanumeric, or common punctuation."""
        cp = ord(ch)
        return (
            (0x4E00 <= cp <= 0x9FFF)     # CJK Unified
            or (0x3400 <= cp <= 0x4DBF)   # CJK Extension A
            or (0x3000 <= cp <= 0x303F)   # CJK Symbols/Punctuation
            or (0xFF00 <= cp <= 0xFFEF)   # Halfwidth/Fullwidth Forms
            or (0x41 <= cp <= 0x5A)       # A-Z
            or (0x61 <= cp <= 0x7A)       # a-z
            or (0x30 <= cp <= 0x39)       # 0-9
        )

    @staticmethod
    def _fix_inner_quotes(text: str) -> str:
        """Replace ASCII double-quotes used as Chinese quotation marks inside strings.

        A " inside a JSON string value is invalid.  LLMs sometimes use them as
        Chinese-style quotation marks (e.g. 疑"代号Nami"有新进展).
        This function finds such inner quotes and replaces them with Unicode
        curly quotes 「 」 which are safe in JSON.
        """
        result: list[str] = []
        in_string = False
        escape_next = False
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
                continue

            if ch == '\\':
                result.append(ch)
                escape_next = True
                i += 1
                continue

            if ch == '"':
                if not in_string:
                    in_string = True
                    result.append(ch)
                else:
                    # We're at a " inside (or ending) a string.
                    # Look ahead past whitespace: if next structural char is
                    # , : } ] or end-of-input, this is a legitimate closing quote.
                    j = i + 1
                    while j < n and text[j] in ' \t\n\r':
                        j += 1
                    if j >= n or text[j] in ',:}]':
                        in_string = False
                        result.append(ch)
                    else:
                        # Inner quote — replace with left or right curly quote
                        # based on context: if preceded by CJK, use right;
                        # if followed by CJK, use left.
                        prev_cjk = i > 0 and Agent._is_cjk_or_alnum(text[i - 1])
                        next_cjk = i + 1 < n and Agent._is_cjk_or_alnum(text[i + 1])
                        if prev_cjk:
                            result.append('”')  # closing
                        elif next_cjk:
                            result.append('“')  # opening
                        else:
                            result.append('“')  # default to opening
                i += 1
                continue

            result.append(ch)
            i += 1

        return ''.join(result)
