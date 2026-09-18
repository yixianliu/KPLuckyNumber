# -*- coding: utf-8 -*-
"""直接测试 predict_next 的完整堆栈"""
import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

import traceback
from modules.database import P5Database
from modules.self_evolution import _row_to_sorted
from modules.ml_predictor import predict_next

db = P5Database()
db.connect()
rows = db.get_history_data(limit=150, order='ASC')
db.disconnect()

print(f'获取 {len(rows)} 行')

sorted_data = _row_to_sorted(rows)
print(f'_row_to_sorted 输出 {len(sorted_data)} 行')
print(f'第一行: {sorted_data[0] if sorted_data else "空"}')

# 直接调用 predict_next
print('\n调用 predict_next...')
try:
    result = predict_next(sorted_data, target_issue='test')
    print(f'结果: {result}')
except Exception as e:
    print(f'异常: {e}')
    traceback.print_exc()
