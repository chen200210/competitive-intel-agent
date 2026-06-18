"""Quick test for cross_chart pattern detection."""
from src.pipeline.cross_chart import detect_signal

tests = [
    # (name, charts, expected_pattern, expected_threat)
    ('A: leading',        {'免费榜': 3, '畅销榜': 2, '热门榜': 5}, 'leading',      'high'),
    ('B: traffic_leak',   {'免费榜': 5, '畅销榜': 48, '热门榜': 12}, 'traffic_leak', 'medium'),
    ('C: harvest',        {'免费榜': 82, '畅销榜': 8, '热门榜': 90}, 'harvest',      'medium'),
    ('D: word_of_mouth',  {'免费榜': 45, '热门榜': 5},              'word_of_mouth', 'medium'),
    ('E: divergence',     {'免费榜': 30, '畅销榜': 80},             'divergence',   'medium'),
    ('F: no signal',      {'免费榜': 25, '畅销榜': 30},             None,           None),
    ('G: only 1 chart',   {'免费榜': 10},                           None,           None),
    ('H: leading 2chart', {'免费榜': 2, '畅销榜': 1},               'leading',      'high'),
    ('I: traffic_leak 2', {'下载榜': 8, '收入榜': 55},              'traffic_leak', 'medium'),
    ('J: harvest 2',      {'下载榜': 60, '收入榜': 5},              'harvest',      'medium'),
]

for name, charts, exp_pat, exp_threat in tests:
    sig = detect_signal(f'test.{name}', name, charts)
    if exp_pat is None:
        assert sig is None, f'{name}: expected None, got {sig["signal_pattern"] if sig else "?"}'
        print(f'  [OK] {name} -> no signal')
    else:
        assert sig is not None, f'{name}: expected signal, got None'
        assert sig['signal_pattern'] == exp_pat, f'{name}: expected {exp_pat}, got {sig["signal_pattern"]}'
        assert sig['threat_level'] == exp_threat, f'{name}: threat {exp_threat} != {sig["threat_level"]}'
        print(f'  [OK] {name} -> {sig["signal_pattern"]} ({sig["threat_level"]})')

print('\nAll pattern detection tests PASSED')
