#!/usr/bin/env python3
"""Offline check for tools/eval_closed.py scoring (phase 1a, no model call). Run: python3 tests/test_eval_closed.py"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('eval_closed', Path(__file__).resolve().parent.parent / 'tools' / 'eval_closed.py')
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

# RC Category -> expected family; 'Support' = answered or manual fix (A/B/D all count); multi/ambiguous labels skipped
assert ev.truth({'RC Category': ['Not a bug']}) == 'AB' and ev.truth({'RC Category': ['Support']}) == 'S'
assert ev.truth({'RC Category': ['BE', 'QA']}) is None and ev.truth({'RC Category': ['Duplicate']}) is None and ev.truth({}) is None
s = ev.summarize([{'truth': 'C', 'type': 'A', 'needs_info': False, 'priority': 'High', 'reported_priority': 'High'},
                  {'truth': 'AB', 'type': 'B', 'needs_info': True, 'priority': 'Low', 'reported_priority': 'High'},
                  {'truth': 'S', 'type': 'D', 'needs_info': False, 'priority': 'High', 'reported_priority': 'High'}])
assert s['bug_called_support'] == '1/1' and s['notbug_recognised'] == '1/1' and s['match'] == 2, s
print('ok eval_closed')
