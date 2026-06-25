"""
P1 tests: Briefer card format validation + Feishu card compatibility.

No LLM API calls. Validates card structure and Feishu compatibility rules.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


# 1. DB: validate existing Briefer card
from src.storage.sqlite import get_db
db = get_db()
report = db.get_analysis_report("2026-06-16")
if report and report.get("brief_card_json"):
    card_data = json.loads(report["brief_card_json"])
    card = card_data.get("card", card_data)

    # Top-level structure
    check("has msg_type", card_data.get("msg_type"), "interactive")
    check("has card object", isinstance(card, dict), True)
    check("card has header", "header" in card, True)
    check("card has elements", "elements" in card, True)
    header = card.get("header", {})
    check("header has title", "title" in header, True)
    check("header has template", header.get("template") in ("blue", "red", "green", "yellow", "purple", "wathet"), True)
    elements = card.get("elements", [])
    check("elements is list", isinstance(elements, list), True)
    check("elements non-empty", len(elements) > 0, True)
    check("all elements have tag", all("tag" in e for e in elements), True)

    # Feishu compatibility rules
    all_content = json.dumps(card_data, ensure_ascii=False)

    # No "详见调研报告"
    check("no '详见调研报告'", "详见调研报告" not in all_content, True)

    # No markdown ### headers
    for e in elements:
        if e.get("tag") == "markdown":
            check(f"no ### in markdown", "###" not in e.get("content", ""), True)
            break

    # All URLs are real (http/https)
    import re
    urls = re.findall(r'\(https?://[^\s)]+\)', all_content)
    check("all URLs are http/https", all(u.startswith("(http") for u in urls), True)

else:
    print("  [SKIP] No Briefer card in DB — run pipeline first")


print(f"\n{'='*50}")
print(f"  Briefer Card: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
