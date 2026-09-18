# -*- coding: utf-8 -*-
"""直接测试 predict_next - 检查 digits 类型"""
import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from modules.database import P5Database
from modules.self_evolution import _row_to_sorted
from modules.ml_predictor import _parse_history, POS

db = P5Database()
db.connect()
rows = db.get_history_data(limit=150, order='ASC')
db.disconnect()

sorted_data = _row_to_sorted(rows)
print(f'sorted_data 数量: {len(sorted_data)}')
print(f'第一行: {sorted_data[0]}')

issues, digits, hezhi = _parse_history(sorted_data)
print(f'\n_parse_history 返回:')
print(f'  issues 类型: {type(issues).__name__}, 长度: {len(issues)}')
print(f'  digits 类型: {type(digits).__name__}')
print(f'  hezhi 类型: {type(hezhi).__name__}, 长度: {len(hezhi)}')

if isinstance(digits, dict):
    print(f'\ndigits 是字典，keys: {list(digits.keys())}')
    for p in POS:
        print(f'  {p}: 长度={len(digits[p])}, 前3项={digits[p][:3]}')
else:
    print(f'\nERROR: digits 不是字典！是 {type(digits).__name__}')
    print(f'  digits 值: {digits}')
