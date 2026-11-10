#!/usr/bin/env python3
"""evalkit scoring + baseline comparison. Run: python3 tests/test_evalkit.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evalkit

ok_out = {'type': 'C', 'missing_info': [], 'reply_markdown': 'NPE trong CartServiceImpl, uuid null', 'summary': 's',
          'evidence': {}, '_problems': [], '_tool_calls': 20, '_cost': 0.4,
          'rca': {'mechanism': 'warehouse null', 'code_location': {'file': 'mono-backend/x/CartServiceImpl.java', 'line_start': 100,
                                                                 'line_end': 102, 'verified': True}}}
expect = {'verdict_any_of': ['?', 'C'], 'must_contain': ['NullPointerException'.lower(), 'uuid'],
          'must_not_contain': ['hỏi vận hành'], 'forbidden_conclusions': ['do voucher'],
          'code_location': {'file_suffix': 'CartServiceImpl.java', 'line_range': [95, 110]}, 'max_tool_calls': 40}
ok_out['reply_markdown'] += ' NullPointerException'

r = evalkit.score_case(expect, ok_out, 'C')
assert r['score'] == 1.0, r

# wrong verdict / forbidden conclusion / fabrication -> 0.05
assert evalkit.score_case(expect, ok_out, 'A')['score'] == 0.05
bad = {**ok_out, 'reply_markdown': ok_out['reply_markdown'] + ' do voucher'}
assert evalkit.score_case(expect, bad, 'C')['score'] == 0.05
assert evalkit.score_case(expect, {**ok_out, '_problems': ['source_be: dòng ngoài phạm vi']}, 'C')['score'] == 0.05

# right verdict but missing anchor / wrong line / over budget -> 0.5
assert evalkit.score_case(expect, {**ok_out, 'reply_markdown': 'NPE'}, 'C')['score'] == 0.5
wrong_line = {**ok_out, 'rca': {**ok_out['rca'], 'code_location': {**ok_out['rca']['code_location'], 'line_start': 300, 'line_end': 301}}}
assert evalkit.score_case(expect, wrong_line, 'C')['score'] == 0.5
assert evalkit.score_case(expect, {**ok_out, '_tool_calls': 99}, 'C')['score'] == 0.5
unverified = {**ok_out, 'rca': {**ok_out['rca'], 'code_location': {**ok_out['rca']['code_location'], 'verified': False}}}
assert evalkit.score_case(expect, unverified, 'C')['score'] == 0.5   # a cited location that is not real does not count

# missing-info cases
mi = {'missing_info': True}
assert evalkit.score_case(mi, {'missing_info': ['mã đơn'], 'reply_markdown': '', 'summary': ''}, '?')['score'] == 1.0
assert evalkit.score_case(mi, {'missing_info': [], 'reply_markdown': '', 'summary': ''}, 'C')['score'] == 0.05

# baseline comparison
assert evalkit.compare_baseline({'a': 1.0, 'b': 0.5}, {'a': 1.0, 'b': 0.5}) == []
assert evalkit.compare_baseline({'a': 0.5}, {'a': 1.0}) == ['a: 1.00 → 0.50', 'mean: 1.00 → 0.50']
assert evalkit.compare_baseline({'a': 0.9, 'b': 1.0}, {'a': 1.0, 'b': 1.0}) == ['mean: 1.00 → 0.95']   # small drop, mean still guarded
assert evalkit.compare_baseline({'new': 0.0}, {'a': 1.0}) == []                                         # unrelated cases ignored
print('ok evalkit')
