# -*- coding: utf-8 -*-
"""测试修复后的 predict_next"""
import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

import traceback
from modules.database import P5Database
from modules.self_evolution import _row_to_sorted
from modules.ml_predictor import predict_next

# 清除缓存
import importlib
if 'modules.ml_predictor' in sys.modules:
    importlib.reload(sys.modules['modules.ml_predictor'])
from modules.ml_predictor import predict_next as pn2

db = P5Database()
db.connect()
rows = db.get_history_data(limit=150, order='ASC')
db.disconnect()

sorted_data = _row_to_sorted(rows)
print(f'数据量: {len(sorted_data)}')

print('\n调用 predict_next...')
try:
    result = pn2(sorted_data, target_issue='test')
    print(f'结果: {result}')
except Exception as e:
    print(f'异常: {e}')
    traceback.print_exc()
