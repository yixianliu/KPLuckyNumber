# -*- coding: utf-8 -*-
"""单次预测热点剖析脚本：cProfile 定位 48 秒耗时的构成。"""
import cProfile
import io
import os
import pstats
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.benchmark_prediction import load_data  # noqa: E402

import modules.predictor as _pred_mod
_pred_mod._CACHE_ENABLED = False

from modules.predictor import P5Predictor  # noqa: E402

data = load_data()
# 取倒数第 5 期做目标，前 60 期为窗口
i = len(data) - 5
hist = data[i - 60:i][::-1]
target = str(data[i]['issue'])

predictor = P5Predictor()
prof = cProfile.Profile()
prof.enable()
res = predictor.predict(hist, target_issue=target)
prof.disable()
print('error:', res.get('error'), '| top_combos:', len(res.get('top_combinations', [])))

s = io.StringIO()
ps = pstats.Stats(prof, stream=s).sort_stats('cumulative')
ps.print_stats(30)
out = s.getvalue()
print(out)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profile_predict.txt'),
          'w', encoding='utf-8') as f:
    f.write(out)
