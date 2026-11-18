import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.maintenance_life import MIN_EVENTS, assess, build_intervals, fingerprint


def _snapshot(cycles, open_hours=19.0, extra=None, cutoff='2026-11-18T00:00:00+00:00'):
    """Build a snapshot with one completed inspection per cycle."""
    executions, completions = [], []
    for index, (day, hours) in enumerate(cycles, 1):
        executions.append({'id': index, 'ended_at': f'2026-{day}T00:00:00+00:00', 'hours': hours})
        completions.append({'window_id': index, 'completed_at': f'2026-{day}T01:00:00+00:00'})
    executions.append({'id': 99, 'ended_at': '2026-10-14T00:00:00+00:00', 'hours': open_hours})
    if extra:
        executions.extend(extra)
    return {'mould_id': 1, 'cutoff_at': cutoff, 'executions': executions, 'completions': completions}


CYCLES = [('06-05', 13), ('06-24', 27), ('07-12', 16), ('07-30', 31), ('08-18', 18), ('09-06', 25), ('09-25', 14)]


class IntervalTests(unittest.TestCase):
    def test_only_completed_pairs_become_observed_intervals(self):
        intervals, current = build_intervals(_snapshot(CYCLES))
        events = [i for i in intervals if i['kind'] == 'event']
        self.assertEqual(len(events), len(CYCLES) - 1)
        self.assertEqual(events[0]['hours'], 27)
        self.assertEqual(current, 19)
        self.assertEqual(intervals[-1]['kind'], 'censored')

    def test_open_cycle_is_censored_not_an_event(self):
        intervals, _ = build_intervals(_snapshot(CYCLES))
        self.assertEqual([i['kind'] for i in intervals].count('censored'), 1)

    def test_late_record_changes_fingerprint(self):
        base = _snapshot(CYCLES)
        late = {'id': 500, 'ended_at': '2026-09-27T00:00:00+00:00', 'hours': 3}
        changed = _snapshot(CYCLES, extra=[late])
        self.assertNotEqual(
            fingerprint(base['executions'], base['completions']),
            fingerprint(changed['executions'], changed['completions']),
        )


class AssessTests(unittest.TestCase):
    def test_probability_is_monotonic_in_requested_hours(self):
        probabilities = [assess(_snapshot(CYCLES), h)['probability'] for h in (2, 10, 30)]
        self.assertTrue(probabilities[0] <= probabilities[1] <= probabilities[2])

    def test_probability_between_zero_and_one(self):
        result = assess(_snapshot(CYCLES), 10)
        self.assertEqual(result['status'], 'ready')
        self.assertGreaterEqual(result['probability'], 0.0)
        self.assertLessEqual(result['probability'], 1.0)
        self.assertGreater(result['shape'], 0)
        self.assertGreater(result['scale_hours'], 0)

    def test_sample_shortage_blocks(self):
        result = assess(_snapshot(CYCLES[:MIN_EVENTS]), 10)
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['probability'])
        self.assertTrue(any('样本不足' in reason for reason in result['blocking_reasons']))

    def test_no_completions_blocks(self):
        snapshot = _snapshot([])
        result = assess(snapshot, 10)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['sample']['observed_intervals'], 0)

    def test_non_positive_hours_blocks(self):
        self.assertEqual(assess(_snapshot(CYCLES), 0)['status'], 'blocked')

    def test_result_is_deterministic(self):
        first = assess(_snapshot(CYCLES), 12)
        second = assess(_snapshot(CYCLES), 12)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
