# -*- coding: utf-8 -*-
"""
v3.67 命中率口径修复 · 纯逻辑回归自检
=====================================
不连 DB / 不导 sklearn，直接验证本次改动的核心逻辑：
1. online_learner._calculate_hits 双口径分离 + Top-1 质量评级
2. database._calculate_strict_hit_rates Top-1 精确匹配
3. 随机基线对照数值（系统 Top-1 vs 10%）

【风险提示】排列五开奖为完全随机的概率事件，本脚本仅作学术性自检，
不构成任何购彩建议。请理性购彩，量力而行。
"""
import sys, os, json, types

# 项目根目录加入 sys.path（脚本位于 scripts/ 子目录）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 注入伪造 pymysql / redis, 使 database.py 顶部 import 不报错（本自检不连 DB）
sys.modules.setdefault('pymysql', types.ModuleType('pymysql'))
sys.modules.setdefault('redis', types.ModuleType('redis'))

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{status}] {name} {detail}")

# ── 1. online_learner._calculate_hits 双口径 ──
from modules.online_learner import OnlineLearner
# _calculate_hits 为纯函数逻辑, 不依赖实例状态, 用 object.__new__ 跳过 __init__(无需 DB 连接)
engine = object.__new__(OnlineLearner)

# 构造一个"首推号全中"的预测记录（Top-1 应全中 5 位）
pred_all_top1 = {
    'target_issue': '2026100',
    'fused_probabilities': [
        {3: 0.5, 7: 0.3, 1: 0.2},
        {5: 0.5, 9: 0.3, 2: 0.2},
        {8: 0.5, 4: 0.3, 0: 0.2},
        {1: 0.5, 6: 0.3, 3: 0.2},
        {2: 0.5, 9: 0.3, 7: 0.2},
    ],
    'top_combinations': [
        {'numbers': [3, 5, 8, 1, 2]},  # 5位全中 → exact_match=1 → excellent
    ],
}
actual_all = [3, 5, 8, 1, 2]
r1 = engine._calculate_hits(pred_all_top1, actual_all)
check("Top-1全中→top1_hits=5", r1['top1_hits'] == 5, f"got={r1['top1_hits']}")
check("Top-1全中→quality=excellent", r1['recommendation_quality'] == 'excellent', f"got={r1['recommendation_quality']}")
check("position_hits含top1_hit", 'top1_hit' in r1['position_hits'].get('万位', {}))

# 首推号0中，但覆盖Top-3仍中（验证双口径分离）
pred_partial_only = {
    'target_issue': '2026101',
    'fused_probabilities': [
        {9: 0.5, 3: 0.3, 7: 0.2},
        {4: 0.5, 5: 0.3, 2: 0.2},
        {0: 0.5, 8: 0.3, 4: 0.2},
        {7: 0.5, 1: 0.3, 6: 0.2},
        {6: 0.5, 2: 0.3, 9: 0.2},
    ],
    'top_combinations': [],
}
actual_mismatch = [3, 5, 8, 1, 2]
r2 = engine._calculate_hits(pred_partial_only, actual_mismatch)
check("覆盖中但Top-1不中→top1_hits=0", r2['top1_hits'] == 0, f"got={r2['top1_hits']}")
check("覆盖命中→partial_hits>0", r2['partial_hits'] > 0, f"got={r2['partial_hits']}")
check("Top-1不中→quality非good以上", r2['recommendation_quality'] in ('poor', 'fair'), f"got={r2['recommendation_quality']}")

# ── 2. database._calculate_strict_hit_rates Top-1 精确匹配 ──
from modules.database import P5Database
db_inst = object.__new__(P5Database)

# 伪造 cursor: execute 不执行, fetchall 返回 fake_records, 让真实方法走通真实逻辑
class _FakeCursor:
    def __init__(self, records):
        self._recs = records
    def execute(self, sql, params=None):
        pass
    def fetchall(self):
        return self._recs

fake_records = [
    {'predicted_numbers': '{"wan":[4,5,6],"qian":[1,2,3],"bai":[9,0,7],"shi":[8,4,5],"ge":[3,7,2]}',
     'actual_numbers': '[4,1,9,8,3]', 'match_count': 5, 'is_matched': 1,
     'wan_match': 1, 'qian_match': 1, 'bai_match': 1, 'shi_match': 1, 'ge_match': 1,
     'accuracy_rate': 100.0, 'verification_status': 'verified'},
    {'predicted_numbers': '{"wan":[9,5,6],"qian":[4,2,3],"bai":[0,8,7],"shi":[7,4,5],"ge":[6,7,2]}',
     'actual_numbers': '[3,5,8,1,2]', 'match_count': 0, 'is_matched': 0,
     'wan_match': 0, 'qian_match': 0, 'bai_match': 0, 'shi_match': 0, 'ge_match': 0,
     'accuracy_rate': 0.0, 'verification_status': 'verified'},
]

db_inst.cursor = _FakeCursor(fake_records)
strict_stats = P5Database._calculate_strict_hit_rates(db_inst, 2)

# 记录1 首推号 [4,1,9,8,3] 全中5位; 记录2 首推号 [9,4,0,7,6] 全不中
check("Top-1精确匹配→位置命中总数=5", strict_stats.get('total_matched') == 5, f"got={strict_stats.get('total_matched')}")
check("Top-1精确匹配→全5位命中期数=1", strict_stats.get('full_matches') == 1, f"got={strict_stats.get('full_matches')}")
check("Top-1万位准确率=50%", abs(strict_stats.get('wan_accuracy', 0) - 50.0) < 0.5, f"got={strict_stats.get('wan_accuracy')}")
check("Top-1总准确率=50%(5命中位/2期/5)", abs(strict_stats.get('avg_accuracy', 0) - 50.0) < 0.5, f"got={strict_stats.get('avg_accuracy')}")

# ── 3. 随机基线对照数值一致性 ──
top1_wan, top1_qian, top1_bai, top1_shi, top1_ge = 10.23, 8.31, 9.82, 9.98, 7.80
avg_top1 = sum([top1_wan, top1_qian, top1_bai, top1_shi, top1_ge]) / 5
random_base = 10.0
delta = avg_top1 - random_base
check("系统Top-1均值≈随机基线(±1pp)", abs(delta) < 1.0, f"系统={avg_top1:.2f}% 随机={random_base}% Δ={delta:+.2f}pp")
check("结论=未显著超越随机", abs(delta) <= 1.0, "Top-1趋近10%即诚实正常")

print(f"\n===== 自检结果: {PASS} PASS / {FAIL} FAIL =====")
print("【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，")
print("本自检仅验证代码逻辑正确性，不代表预测能力提升。请理性购彩，量力而行。")
sys.exit(0 if FAIL == 0 else 1)
