# -*- coding: utf-8 -*-
# --- 路径锚定(B3修复): 向上搜索项目根(modules/+main.py), 注入 sys.path ---
import os
import sys
def _find_project_root(_start):
    _cur = os.path.abspath(_start)
    while True:
        if os.path.isdir(os.path.join(_cur, 'modules')) and \
           os.path.isfile(os.path.join(_cur, 'main.py')):
            return _cur
        _p = os.path.dirname(_cur)
        if _p == _cur:
            return os.path.dirname(os.path.abspath(_start))
        _cur = _p
_PROJECT_ROOT = _find_project_root(__file__)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

"""
历史回放批量注册验证记录 (落实「积累验证记录」建议 v3.2)

方法:
  - 对历史每期做 walk-forward 纯先验预测(禁用AI与验证学习,避免前视)
  - 将「预测Top-5 + 实际开奖」注册为 verified 验证记录
  - 跳过已存在的期号(不覆盖真实预测产生的验证)

目的:
  将验证样本从~42条扩充到数百条, 使贝叶斯推断的验证学习(min_verification_samples=50)
  得以启用, 从而未来真实预测时贝叶斯能基于充足似然信号微调后验。

用法:
  python batch_generate_verification.py        # 全量回放
  python batch_generate_verification.py 30     # 仅前30期(测试)
"""
import logging
import json
import sys
import uuid

logging.getLogger().setLevel(logging.WARNING)
for _n in ['modules', 'urllib3', 'matplotlib']:
    logging.getLogger(_n).setLevel(logging.WARNING)

from modules.predictor import P5Predictor
from modules.database import P5Database

POS = ['wan', 'qian', 'bai', 'shi', 'ge']


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9
    start_index = 50

    db = P5Database()
    db.connect()
    data = db.get_history_data(limit=None, order='ASC')
    total = len(data)

    db.cursor.execute('SELECT target_issue FROM p5_prediction_record')
    existing = set(r['target_issue'] for r in db.cursor.fetchall())
    print(f'历史总期数={total}, 已有验证记录期号={len(existing)}')

    p = P5Predictor()
    p.ai_available = False
    p.config.config.setdefault('global', {})['enable_ai_model'] = False
    p._verification_cache = []  # 纯先验回放, 避免回放本身引入前视

    added = 0
    skipped = 0
    end = min(start_index + limit, total)
    for i in range(start_index, end):
        issue = data[i]['issue']
        if issue in existing:
            skipped += 1
            continue
        train = data[:i]
        actual = data[i]['numbers']
        try:
            res = p.predict(train, issue)
        except Exception as e:
            continue
        if 'error' in res:
            continue

        fused = res['fused_probabilities']
        flat = {}
        for pos_idx, pos_name in enumerate(POS):
            probs = fused[pos_idx]
            top = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
            flat[pos_name] = [int(n) for n, _ in top]

        uid = str(uuid.uuid4())
        db.insert_prediction_record(uid, issue, json.dumps(flat, ensure_ascii=False), '[]', '{}')
        db.update_prediction_verification(uid, issue, actual, issue)
        added += 1
        existing.add(issue)
        if added % 50 == 0:
            print(f'  已新增 {added} 条...')

    db.disconnect()
    print(f'完成: 新增 {added} 条验证记录, 跳过 {skipped} 条已存在')
    print(f'验证记录总数预计: {len(existing)} 条')


if __name__ == '__main__':
    main()
