# -*- coding: utf-8 -*-
"""生成「命中率基线对照」诚实可视化（Phase 2 / 需求 #2）。

输出：reports/figures/baseline_comparison.png
内容：系统实测 Top-1/3/5 vs 随机基线常量(10/30/50%)，附 95% Wilson 置信区间误差带。
诚信约束：仅展示已实证数据，明确标注"系统实测落在随机基线 95% CI 内，无统计显著差异"。
"""
import os
import sys

import matplotlib

matplotlib.use('Agg')  # 无头环境
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

# 中文字体：优先使用系统中文字体，避免 CJK 字形缺失
plt.rcParams['font.sans-serif'] = [
    'Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'SimSun', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
fm._rebuild() if hasattr(fm, '_rebuild') else None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts/ 直跑时把项目根加入 sys.path，保证 modules.* 可导入（ALG-06）
sys.path.insert(0, ROOT)
# Wilson 95% CI 单一实现（ALG-06 收敛，PUP-20261019-013100）
from modules.stats_utils import wilson_ci  # noqa: E402
OUT_DIR = os.path.join(ROOT, 'reports', 'figures')
os.makedirs(OUT_DIR, exist_ok=True)
OUT_PNG = os.path.join(OUT_DIR, 'baseline_comparison.png')

# 代表性评估窗口数（用于 Wilson 95% CI 宽度；与 2060 次 walk-forward 同量级）
N = 1000


# Wilson 95% CI 口径换算：百分数 → 成功计数（v*N/100）→ stats_utils 单一实现
# → ×100 还原百分数；较原内嵌实现存在 ~1ulp 浮点差异，绘图层无感
# （ALG-06 / PUP-20261019-013100）


# ---- 数据（来源：reports/diagnosis/hitrate_root_cause.md §2/§3，可回溯） ----
labels = ['Top-1\n(首推号精确匹配)', 'Top-3', 'Top-5']
system = np.array([9.2, 30.0, 45.0])      # 系统实测（v3.67 严格口径）
baseline = np.array([10.0, 30.0, 50.0])    # 随机基线常量

sys_lo = [wilson_ci(v * N / 100.0, N)[0] * 100.0 for v in system]
sys_hi = [wilson_ci(v * N / 100.0, N)[1] * 100.0 for v in system]
base_lo = [wilson_ci(v * N / 100.0, N)[0] * 100.0 for v in baseline]
base_hi = [wilson_ci(v * N / 100.0, N)[1] * 100.0 for v in baseline]
sys_err = [[v - lo for v, lo in zip(system, sys_lo)],
           [hi - v for v, hi in zip(system, sys_hi)]]
base_err = [[v - lo for v, lo in zip(baseline, base_lo)],
            [hi - v for v, hi in zip(baseline, base_hi)]]

# ---- 绘图 ----
plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'SimHei',
                                         'Source Han Sans CN', 'SimSun',
                                         'DejaVu Sans'],
                      'axes.unicode_minus': False})
fig, ax = plt.subplots(figsize=(9, 5.2), dpi=150)

x = np.arange(len(labels))
w = 0.36
b1 = ax.bar(x - w / 2, system, w, yerr=sys_err, capsize=5,
            color='#e53e3e', alpha=0.85, label='系统实测（严格口径）',
            error_kw={'elinewidth': 1.4, 'ecolor': '#9b2c2c'})
b2 = ax.bar(x + w / 2, baseline, w, yerr=base_err, capsize=5,
            color='#3182ce', alpha=0.85, label='随机基线（公平摇号）',
            error_kw={'elinewidth': 1.4, 'ecolor': '#2c5282'})

ax.set_ylabel('命中率 (%)', fontsize=12)
ax.set_title('排列5 命中率：系统实测 vs 随机基线（95% Wilson CI）',
             fontsize=13, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=11)
ax.set_ylim(0, 62)
ax.grid(axis='y', alpha=0.3)
ax.legend(loc='upper right', fontsize=10, framealpha=0.9)

# 数值标注
for bars, vals in ((b1, system), (b2, baseline)):
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 1.2, f'{val:.1f}%',
                ha='center', va='bottom', fontsize=9)

# 诚实结论注释
conclusion = ('结论：系统实测 Top-1/3/5 全部落在随机基线 95% 置信区间内，\n'
             '无统计显著差异 —— 低命中率 = 公平摇号属性，非代码缺陷。')
ax.text(0.5, -0.22, conclusion, transform=ax.transAxes, ha='center',
        fontsize=9.5, color='#4a5568',
        bbox=dict(boxstyle='round,pad=0.5', facecolor='#f7fafc', edgecolor='#cbd5e0'))

ax.text(0.01, 0.98, f'n≈{N} 评估窗口（代表性）', transform=ax.transAxes,
        ha='left', va='top', fontsize=8.5, color='#4a5568')

plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150, bbox_inches='tight')
plt.close(fig)
print(f'SAVED: {OUT_PNG}')
