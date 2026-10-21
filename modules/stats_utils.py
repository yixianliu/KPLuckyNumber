# -*- coding: utf-8 -*-
"""
stats_utils.py - 统计口径单一实现（ALG-06 / PUP-20261019-013100）

职责：
    集中管理「命中率评估 / 置信区间」等统计口径，消除历史上多点重复实现
    导致的口径漂移风险。当前收敛 wilson_ci（Wilson score interval），
    原分散于 6 处实现（modules × 2 / scripts × 3 / gui × 1），
    现已全部改为复用本模块，全仓 grep "def wilson_ci" 唯一命中本文件。

使用方式：
    from modules.stats_utils import wilson_ci
    lo, hi = wilson_ci(hits, total)   # 返回 (下界, 上界)，均已钳制到 [0, 1]

诚实边界（不可突破）：
    排列5 每期五位置独立公平摇号，Top-k 随机基线恒为 k/10
    （Top-1 = 10% / Top-3 = 30% / Top-5 = 50%）。
    本模块仅提供区间估计工具，任何「提升命中率」的说法必须同时给出
    点估计与 Wilson 95% CI；CI 与随机基线重叠即视为无统计显著超额。
    口径冻结定义见 docs/hitrate_spec.md。
"""

import math
from typing import Tuple


def wilson_ci(successes: int, total: int,
              z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval（二项比例置信区间）单一实现。

    小样本与极端比例（p→0 / p→1）下比正态近似（Wald 区间）更稳健：
    下界不会越出 [0, 1]、不会为负，是小样本命中率评估的标准口径。

    参数:
        successes: 成功次数（命中数），允许传入越界值，内部自动钳制
        total:     总试验次数（评估窗口数）
        z:         置信水平对应 z 分数，默认 1.96（95% 双侧）

    返回:
        (lower, upper) 二元组，均已钳制到 [0, 1]；total == 0 时返回 (0.0, 0.0)

    防御:
        successes 超出 [0, total] 时把比例钳到 [0, 1]，否则 p*(1-p) 为负会
        触发 math domain error；调用方传入越界计数时不应让整份报告中断
        （继承 scripts/backtest/algo_candidate_walkforward.py 原实现语义）。

    数值一致性（相对收敛前 6 处实现）:
        - 对合法输入（0 <= successes <= total），与其余各版逐位一致；
        - 原 backtest_multisource.py 版 n==0 返回整型 (0, 0)，此处统一为
          浮点 (0.0, 0.0)，其调用点仅作解包与格式化，无行为差异；
        - 与方案文本三元组签名 Tuple[float, float, float] 的差异已登记为
          偏差（见 docs/dev_schedule_v52_20261019.md §17）：保持二元组是为
          零漂移收敛，所有现存调用点均按二元组解包。
    """
    if total == 0:
        return (0.0, 0.0)
    # 防御：比例钳制到 [0, 1]，避免 p*(1-p) 为负导致 math domain error
    p = min(1.0, max(0.0, successes / total))
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    radicand = p * (1 - p) / total + z * z / (4 * total * total)
    half = z * math.sqrt(radicand) / denom
    return (max(0.0, center - half), min(1.0, center + half))
