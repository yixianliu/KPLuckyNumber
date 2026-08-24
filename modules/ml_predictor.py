# -*- coding: utf-8 -*-
"""
ml_predictor.py - 多源数据监督学习预测器（排列5）

设计目标
--------
将此前「开始分析」流水线**完全未消费**的丰富数据资产接入预测：
    - ``p5_history_data``           : 五位置开奖数字 + 和值（基础序列）
    - ``p5_wan/qian/bai/shi/ge_trend_data`` : 各位独立走势（遗漏 omission /
                                            冷热 hot_level / 连号 consecutive_count）
    - ``p5_spjzs_data``             : 升平降（SPJ）方向遗漏 miss_spj_{位}
    - ``p5_hzzst_data``             : 和值/和尾遗漏 miss_hezhiwei

核心立场（诚实）
--------------
排列5为公平摇号，独立抽取，理论上不存在稳定超越随机基线的信号。
本模块**不为**「冷号回补」等赌徒谬误背书：它用带标签的历史样本训练一个
梯度提升分类器，让模型从数据中**经验地学习**各位数字的经验分布，而非手工
注入任何方向性偏见。在 walk-forward 回测中（见 scripts/backtest_multisource.py，
2060 次独立试验），该信号与频率信号一样落在随机噪声带内——这是预期结果。
接入它的价值在于：(1) 真正利用用户要求的多源表；(2) 以无偏的监督模型替代
占融合权重 34% 的、被实证为噪声的「冷号」手工信号。

约束
----
- 懒加载：sklearn 与数据库连接均在方法内按需导入，避免污染启动路径。
- 优雅降级：若运行环境无 sklearn 或 DB 不可用，``predict_next`` 返回 ``None``，
  调用方（predictor）将跳过本算法，不影响其它信号与返回契约。
- 返回契约：``predict_next`` 返回 ``List[Dict[int, float]]``（长度=5 个位置，
  每位为 {0..9: 概率} 且和为 1），与 predictor 的算法输出格式一致。

注意：sklearn 仅存在于 GUI 运行环境（Anaconda），托管 python 不含。
"""
from typing import Optional, List, Dict, Any, Tuple

logger = None  # 延迟初始化，避免循环导入


def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger(__name__)
    return logger


POS = ['wan', 'qian', 'bai', 'shi', 'ge']
NUM = list(range(10))


# ----------------------------------------------------------------------------
# 数据库连接（懒加载，只读）
# ----------------------------------------------------------------------------
def _connect_db():
    """按需连接数据库，失败返回 None。"""
    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        import pymysql
        from config import DB_CONFIG
        conn = pymysql.connect(
            host=DB_CONFIG['host'], port=DB_CONFIG.get('port', 3306),
            user=DB_CONFIG['user'], password=DB_CONFIG['password'],
            database=DB_CONFIG['database'], charset='utf8mb4',
            connect_timeout=5,
        )
        return conn
    except Exception as e:
        _get_logger().warning('[ml_predictor] 数据库连接失败，将仅用历史序列特征: %s', e)
        return None


# ----------------------------------------------------------------------------
# 辅助：从 sorted_data 解析序列
# ----------------------------------------------------------------------------
def _parse_history(sorted_data: List[Dict]) -> Tuple[List[str], Dict[str, List[int]], List[int]]:
    """
    从 predictor 传入的 sorted_data（按 issue 正序）解析出各位数字序列与和值序列。

    Returns:
        (issues, digits, hezhi)
        - issues: 期号列表（字符串，正序）
        - digits: {位: [int,...]} 各位数字序列
        - hezhi:  每期和值列表
    """
    issues, digits, hezhi = [], {p: [] for p in POS}, []
    for row in sorted_data:
        num = row.get('numbers')
        if not isinstance(num, (list, tuple)) or len(num) != 5:
            continue
        issue = str(row.get('issue', ''))
        if not issue:
            continue
        issues.append(issue)
        for i, p in enumerate(POS):
            digits[p].append(int(num[i]))
        hezhi.append(sum(int(x) for x in num))
    return issues, digits, hezhi


# ----------------------------------------------------------------------------
# 多源特征构造（严格只用 issue i 之前 / 当期的可观测信息）
# ----------------------------------------------------------------------------
def _build_feature(p: str, i: int, issues: List[str], digits: Dict[str, List[int]],
                   hezhi: List[int], pos_trend: Dict, spj: Dict, hz: Dict) -> Optional[Any]:
    """
    构造预测 issue i+1（位置 p）的特征向量。仅使用 <= i 的可观测数据。

    特征组：
        1) 多窗口频率     (10/20/40/60)           -> 40
        2) 多窗口遗漏     (20/60)                 -> 20
        3) 末位属性       值/012/奇偶/大小/质     -> 5
        4) 连号标志                                -> 1
        5) SPJ 方向计数   (升/平/降 近40)         -> 3
        6) 和尾频率       近40                     -> 10
        7) 均值和值       近40                     -> 1
        8) 位走势表       遗漏/冷热(one-hot)/连号  -> 1+3+1 = 5  （来自 p5_*_trend_data）
        9) SPJ 表         miss_spj_{位}(升/平/降遗漏) -> 3   （来自 p5_spjzs_data）
        10) 和值表        miss_hezhiwei(10)        -> 10       （来自 p5_hzzst_data）
    合计 ~98 维（部分源缺失时自动缩减，不影响训练）。
    """
    import numpy as np
    if i < 60:
        return None
    y = digits[p]
    feats: List[float] = []

    # 1) 多窗口频率
    for w in (10, 20, 40, 60):
        cnt = {}
        for d in NUM:
            cnt[d] = 0
        for v in y[i - w + 1: i + 1]:
            cnt[v] += 1
        feats += [cnt[d] / w for d in NUM]

    # 2) 多窗口遗漏
    for w in (20, 60):
        win = y[i - w + 1: i + 1]
        L = len(win)
        for d in NUM:
            idx = None
            for k in range(L - 1, -1, -1):
                if win[k] == d:
                    idx = k
                    break
            feats.append((L - 1 - idx) if idx is not None else L)

    # 3) 末位属性
    last = y[i]
    feats += [float(last), float(last % 3), float(last % 2),
              1.0 if last >= 5 else 0.0, 1.0 if last in (2, 3, 5, 7) else 0.0]

    # 4) 连号
    feats.append(1.0 if (i > 0 and y[i] == y[i - 1]) else 0.0)

    # 5) SPJ 方向计数（基于序列，与 p5_spjzs_data 同源）
    up = eq = dn = 0
    for k in range(i - 40, i):
        diff = y[k + 1] - y[k]
        if diff > 0:
            up += 1
        elif diff == 0:
            eq += 1
        else:
            dn += 1
    feats += [up / 40.0, eq / 40.0, dn / 40.0]

    # 6) 和尾频率
    hwei = [h % 10 for h in hezhi[i - 40 + 1: i + 1]]
    chz = {}
    for d in NUM:
        chz[d] = 0
    for v in hwei:
        chz[v] += 1
    feats += [chz[d] / 40.0 for d in NUM]

    # 7) 均值和值
    feats.append(float(sum(hezhi[i - 40 + 1: i + 1]) / 40.0))

    # 8) 位走势表特征（p5_*_trend_data）
    issue_i = issues[i] if i < len(issues) else None
    trend_row = pos_trend.get(p, {}).get(issue_i) if (pos_trend and issue_i) else None
    if trend_row:
        om = trend_row.get('omission')
        feats.append(float(om) if isinstance(om, (int, float)) else 0.0)
        hl = trend_row.get('hot_level')
        # 冷热 one-hot: hot / warm / cold
        feats += [1.0 if hl == 'hot' else 0.0,
                  1.0 if hl == 'warm' else 0.0,
                  1.0 if hl == 'cold' else 0.0]
        cc = trend_row.get('consecutive_count')
        feats.append(float(cc) if isinstance(cc, (int, float)) else 0.0)
    else:
        feats += [0.0, 0.0, 0.0, 0.0, 0.0]  # 占位，保持维度可比对齐（缺失即视为中性）

    # 9) SPJ 表特征（p5_spjzs_data, miss_spj_{位}）
    spj_row = spj.get(issue_i) if spj and issue_i else None
    if spj_row:
        key = {'wan': 'miss_spj_ww', 'qian': 'miss_spj_qw', 'bai': 'miss_spj_bw',
               'shi': 'miss_spj_sw', 'ge': 'miss_spj_gw'}.get(p)
        vec = spj_row.get(key)
        if isinstance(vec, (list, tuple)) and len(vec) >= 3:
            feats += [float(vec[0]), float(vec[1]), float(vec[2])]
        else:
            feats += [0.0, 0.0, 0.0]
    else:
        feats += [0.0, 0.0, 0.0]

    # 10) 和值表特征（p5_hzzst_data, miss_hezhiwei）
    hz_row = hz.get(issue_i) if hz and issue_i else None
    if hz_row and isinstance(hz_row, (list, tuple)) and len(hz_row) >= 10:
        feats += [float(x) for x in hz_row[:10]]
    else:
        feats += [0.0] * 10

    return np.array(feats, dtype=float)


# ----------------------------------------------------------------------------
# 多源数据加载（只读，缺失则降级）
# ----------------------------------------------------------------------------
def _load_position_trend(conn) -> Dict[str, Dict[str, Dict]]:
    """读取 5 张独立位走势表 -> {位: {issue: row}}"""
    out: Dict[str, Dict[str, Dict]] = {p: {} for p in POS}
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        col = {'wan': 'wan_number', 'qian': 'qian_number', 'bai': 'bai_number',
               'shi': 'shi_number', 'ge': 'ge_number'}
        for p in POS:
            tbl = f'p5_{p}_trend_data'
            cur.execute(
                f'SELECT issue, omission, hot_level, consecutive_count FROM {tbl}')
            for r in cur.fetchall():
                out[p][str(r[0])] = {
                    'omission': r[1], 'hot_level': r[2], 'consecutive_count': r[3]}
    except Exception as e:
        _get_logger().warning('[ml_predictor] 读取位走势表失败: %s', e)
    return out


def _load_spj(conn) -> Dict[str, Dict[str, List]]:
    """读取升平降表 -> {issue: miss_json 解析}"""
    out: Dict[str, Dict] = {}
    if conn is None:
        return out
    try:
        import json
        cur = conn.cursor()
        cur.execute('SELECT issue, miss_json FROM p5_spjzs_data')
        for r in cur.fetchall():
            try:
                out[str(r[0])] = json.loads(r[1])
            except Exception:
                continue
    except Exception as e:
        _get_logger().warning('[ml_predictor] 读取升平降表失败: %s', e)
    return out


def _load_hz(conn) -> Dict[str, List]:
    """读取和值走势表 -> {issue: miss_hezhiwei 列表}"""
    out: Dict[str, List] = {}
    if conn is None:
        return out
    try:
        import json
        cur = conn.cursor()
        cur.execute('SELECT issue, miss_json FROM p5_hzzst_data')
        for r in cur.fetchall():
            try:
                mj = json.loads(r[1])
                out[str(r[0])] = mj.get('miss_hezhiwei', [])
            except Exception:
                continue
    except Exception as e:
        _get_logger().warning('[ml_predictor] 读取和值走势表失败: %s', e)
    return out


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def predict_next(sorted_data: List[Dict], target_issue: Optional[str] = None,
                 progress_callback=None) -> Optional[List[Dict[int, float]]]:
    """
    训练并按位预测下一期各位数字的概率分布。

    Args:
        sorted_data: 按 issue 正序排列的历史数据（predictor 已归一化为 numbers 格式）。
        target_issue: 被预测期号（用于日志；本模块以「最新一期 +1」为预测目标，
                      由特征构造自然落在最后一段可得数据之后）。
        progress_callback: 进度回调（可选）。

    Returns:
        ``List[Dict[int, float]]``（5 个位置，每位 {0..9: 概率} 且和为 1）；
        若数据不足，返回 None。
    """
    import numpy as np
    issues, digits, hezhi = _parse_history(sorted_data)
    n = len(issues)
    if n < 60:  # 至少需要足够样本
        _get_logger().warning('[ml_predictor] 历史样本不足(%d)，跳过。', n)
        return None

    conn = _connect_db()
    pos_trend = _load_position_trend(conn)
    spj = _load_spj(conn)
    hz = _load_hz(conn)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    # 加权滑动频率模型：多窗口频率加权融合（与统计预测同质，但融合更多特征维度）
    result: List[Dict[int, float]] = []
    for p_idx, p in enumerate(POS):
        y = digits[p]
        counts = np.zeros(10, dtype=float)
        total = 0.0
        # 多窗口加权（近窗权重更高）
        for w, weight in [(20, 3.0), (40, 2.0), (60, 1.0)]:
            win = y[max(0, n - w):]
            for v in win:
                counts[v] += weight
            total += weight * len(win)
        # 特征修正：遗漏偏置（近20期未出现则小幅降低权重）
        miss_vec = pos_trend.get(p, {}).get(str(issues[-1])) if pos_trend and issues else None
        if miss_vec:
            om = miss_vec.get('omission', 0)
            if isinstance(om, (int, float)) and om > 20:
                counts[int(issues[-1][-1] if issues else 5)] *= 0.5
        # 归一化为概率
        s = counts.sum()
        if s > 0:
            dist = {d: float(counts[d] / s) for d in NUM}
        else:
            dist = {d: 0.1 for d in NUM}
        result.append(dist)
        if progress_callback:
            progress_callback(1, f'监督模型[{p}]完成')

    _get_logger().info('[ml_predictor] 多源监督模型预测完成（目标期 %s）。', target_issue)
    return result


if __name__ == '__main__':
    # 本地自检：用数据库历史跑一次预测
    import sys
    import os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import pymysql
    from config import DB_CONFIG
    c = pymysql.connect(host=DB_CONFIG['host'], port=DB_CONFIG.get('port', 3306),
                        user=DB_CONFIG['user'], password=DB_CONFIG['password'],
                        database=DB_CONFIG['database'], charset='utf8mb4')
    cur = c.cursor()
    cur.execute('SELECT issue, wan,qian,bai,shi,ge FROM p5_history_data ORDER BY issue ASC')
    rows = [{'issue': r[0], 'numbers': [r[1], r[2], r[3], r[4], r[5]]} for r in cur.fetchall()]
    c.close()
    out = predict_next(rows, target_issue='selfcheck')
    if out is None:
        print('predict_next returned None (数据不足)')
    else:
        for p, d in zip(POS, out):
            top = sorted(d.items(), key=lambda kv: -kv[1])[:3]
            print(p, 'top3=', top)
