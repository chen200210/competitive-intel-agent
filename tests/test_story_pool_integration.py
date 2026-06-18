"""Integration test: Story Picker (w/ cross_chart) -> Overview Scanner hint."""
from src.pipeline.story_picker import pick_stories_for_date, MAX_STORIES
from src.agents.overview_scanner import _build_story_pool_hint
from src.storage.sqlite import get_db

db = get_db()
dates = db.get_available_dates()
# Use the LATEST date (most likely to have diff data)
date = dates[0] if dates else None

if not date:
    print("No data in DB - import CSV first")
    exit(1)

result = pick_stories_for_date(date)
print(f"Date: {date}")
print(f"Total changes: {result['total_changes']}")
print(f"Stories selected: {result['stories_selected']} (cap: {MAX_STORIES})")

if result['stories']:
    print(f"Story types: {sorted(set(s['story_type'] for s in result['stories']))}")
else:
    print("(no stories — this date may have no changes yet)")

# Verify cap is respected
assert len(result['stories']) <= MAX_STORIES, f"Stories {len(result['stories'])} > cap {MAX_STORIES}"

# Verify hint builder works
hint = _build_story_pool_hint(date, result['stories'])
print(f"Story pool hint: {len(hint)} chars")
if result['stories']:
    assert len(hint) > 0, "Hint should be non-empty when stories exist"

print()
print("=== Integration test PASSED ===")
