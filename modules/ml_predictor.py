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
- 纯 numpy 实现：无需 sklearn，任何 Python 环境均可运行。
- 数据库懒加载：DB 连接在方法内按需建立，失败时自动降级为仅用历史序列特征。
- 串行训练：避免 Windows daemon 进程嵌套限制，5 位依次训练。
- 返回契约：``predict_next`` 返回 ``List[Dict[int, float]]``（长度=5 个位置，
  每位为 {0..9: 概率} 且和为 1），与 predictor 的算法输出格式一致。

注意：本模块已改为纯 numpy 实现，不再依赖 sklearn，任何 Python 环境均可运行。
"""
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
import traceback

logger = None  # 延迟初始化，避免循环导入


def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger(__name__)
    return logger


POS = ['wan', 'qian', 'bai', 'shi', 'ge']
NUM = list(range(10))
ML_MIN_SAMPLES = 160  # GBRT 有效训练所需最少期数（warmup 60 + 特征样本 100）

# ----------------------------------------------------------------------------
# v3.70 (roadmap 1.3) sklearn 条件激活
# ----------------------------------------------------------------------------
# 纯 numpy GBRT 是本模块默认实现（无 sklearn 依赖，任何 Python 环境均可运行）。
# 当环境安装了 scikit-learn（>=1.3）时，自动优先使用 sklearn GradientBoostingClassifier
# 走更快、更稳定的真实 GBRT 路径；缺失或 import 失败时自动降级回纯 numpy 路径，
# 并输出降级日志，保证系统功能不受影响（降级保底原则）。
_SKLEARN_AVAILABLE = False
_SKLEARN_IMPORT_ERROR: Optional[str] = None
try:
    import sklearn
    from sklearn.ensemble import GradientBoostingClassifier
    _SKLEARN_AVAILABLE = True
except Exception as _sk_err:  # pragma: no cover - 取决于运行环境是否安装 sklearn
    _SKLEARN_IMPORT_ERROR = f'{type(_sk_err).__name__}: {_sk_err}'
    _SKLEARN_AVAILABLE = False

# v3.70 (roadmap 1.5) Optuna 超参数调优
# ----------------------------------------------------------------------------
_OPTUNA_AVAILABLE = False
_OPTUNA_IMPORT_ERROR: Optional[str] = None
try:
    import optuna
    _OPTUNA_AVAILABLE = True
except Exception as _opt_err:
    _OPTUNA_IMPORT_ERROR = f'{type(_opt_err).__name__}: {_opt_err}'
    _OPTUNA_AVAILABLE = False


def _sklearn_gbrt_predict(X: np.ndarray, y: np.ndarray,
                          n_estimators: int, learning_rate: float,
                          max_depth: int) -> float:
    """v3.70 (1.3): 使用 sklearn GradientBoostingClassifier 训练二分类 GBDT。

    与 _gbrt_predict（纯 numpy）签名兼容，但优先使用 sklearn 实现以获得更快、
    更稳定的训练。返回最后一个样本的预测值（log-odds 空间，与 numpy 路径对齐，
    便于 softmax 归一化时两条路径结果可比）。

    Args:
        X: (n_samples, n_features) 特征矩阵
        y: (n_samples,) 二分类标签（0/1 float）
        n_estimators: 树的数量（与 numpy 路径同参数）
        learning_rate: 学习率（与 numpy 路径同参数）
        max_depth: 树的最大深度（与 numpy 路径同参数）

    Returns:
        最后一个样本的 log-odds 预测值（标量）。
        调用方捕获异常后自动降级回纯 numpy 路径。
    """
    # 把 sklearn 概率映射回 log-odds 空间，与 numpy 路径的 F[-1] 语义对齐
    clf = GradientBoostingClassifier(
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        random_state=42,
    )
    clf.fit(X, y.astype(int))
    p_last = float(clf.predict_proba(X[-1:])[0, 1])  # P(y=1 | x_last)
    eps = 1e-12
    p_last = np.clip(p_last, eps, 1 - eps)
    return float(np.log(p_last / (1.0 - p_last)))


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


def _load_full_history(conn) -> List[Dict]:
    """
    从数据库加载 p5_history_data 全量有效记录（按 issue 正序）。
    用于调用方传入数据不足时的补全回退。

    Args:
        conn: 已建立的数据库连接。

    Returns:
        按 issue 正序排列的历史记录列表，每项含 issue + numbers 字段；
        连接无效时返回空列表。
    """
    if conn is None:
        return []
    try:
        cur = conn.cursor()
        cur.execute('SELECT issue, wan, qian, bai, shi, ge, hezhi FROM p5_history_data WHERE is_valid = 1 ORDER BY issue ASC')
        rows = cur.fetchall()
        return [{'issue': str(r[0]), 'numbers': [int(r[1]), int(r[2]), int(r[3]), int(r[4]), int(r[5])],
                 'hezhi': int(r[6]) if r[6] is not None else None} for r in rows]
    except Exception as e:
        _get_logger().warning('[ml_predictor] 加载全量历史失败: %s', e)
        return []


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
            # 兼容数据库拆分行格式（wan/qian/bai/shi/ge 五列）
            if all(k in row for k in ('wan', 'qian', 'bai', 'shi', 'ge')):
                num = [row['wan'], row['qian'], row['bai'], row['shi'], row['ge']]
            else:
                continue
        try:
            num = [int(x) for x in num]
        except (TypeError, ValueError):
            continue
        issue = str(row.get('issue', ''))
        if not issue:
            continue
        issues.append(issue)
        for i, p in enumerate(POS):
            digits[p].append(num[i])
        hz_val = row.get('hezhi')
        hezhi.append(int(hz_val) if hz_val is not None else sum(num))
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
        11) 位置链特征(v3.70): 相邻位置对 diff_close_rate + move_same_rate -> 8
        12) 形态标记特征(v3.70): 近30期 lag_2pairs/lag_3seq/lag_4quad      -> 4
    合计 ~110 维（部分源缺失时自动缩减，不影响训练）。
    """
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

    # 11) 位置链特征（v3.70）：与目标位相邻的位置对的 diff_close_rate + move_same_rate
    # POS = ['wan','qian','bai','shi','ge']，目标位 idx 的相邻位置对：
    #   wan: (千,百)  |  qian: (万,百) |  bai: (千,十) |  shi: (百,个) |  ge: (十,百)
    # 每个位置对贡献 2 维（lag2 diff_close + move_same_rate），共 4 对 × 2 = 8 维
    p_idx = POS.index(p)
    adjacent_pairs = []
    if p_idx == 0:       # wan
        adjacent_pairs = [('qian', 'bai'), ('shi', 'ge'), ('bai', 'shi'), ('qian', 'shi')]
    elif p_idx == 1:    # qian
        adjacent_pairs = [('wan', 'bai'), ('bai', 'shi'), ('shi', 'ge'), ('wan', 'shi')]
    elif p_idx == 2:    # bai
        adjacent_pairs = [('qian', 'shi'), ('wan', 'shi'), ('qian', 'ge'), ('shi', 'ge')]
    elif p_idx == 3:    # shi
        adjacent_pairs = [('bai', 'ge'), ('qian', 'ge'), ('bai', 'wan'), ('qian', 'wan')]
    else:               # ge (p_idx == 4)
        adjacent_pairs = [('shi', 'bai'), ('shi', 'qian'), ('bai', 'wan'), ('qian', 'wan')]

    for a, b in adjacent_pairs:
        seq_a = digits.get(a, [])
        seq_b = digits.get(b, [])
        min_len = min(len(seq_a), len(seq_b))
        if min_len < 3:
            feats += [0.0, 0.0]
            continue

        # diff_close_rate：lag2 窗口内 |a[t] - b[t-2]| <= 2 的比率
        valid_pairs = 0
        diff_hits = 0
        move_hits = 0
        move_valid = 0
        for t in range(2, min_len):
            # 确保索引 t 在 i+1 之前（只用 <= i 的数据）
            if t > i:
                break
            diff_hits += 1 if abs(seq_a[t] - seq_b[t - 2]) <= 2 else 0
            valid_pairs += 1
            # move_same_rate：a[t]-a[t-1] 与 b[t]-b[t-1] 同向
            if t >= 1:
                da = seq_a[t] - seq_a[t - 1]
                db = seq_b[t] - seq_b[t - 1]
                move_valid += 1
                if (da > 0 and db > 0) or (da < 0 and db < 0):
                    move_hits += 1

        diff_rate = diff_hits / valid_pairs if valid_pairs > 0 else 0.0
        move_rate = move_hits / move_valid if move_valid > 0 else 0.0
        feats += [float(diff_rate), float(move_rate)]

    # 12) 形态标记特征（v3.70）：近 30 期窗口内对子/三连顺/四连顺的滞后期数
    form_start = max(0, i - 29)
    lag_2pairs = lag_3seq = lag_4quad = None
    form_pairs = form_3seq = form_4seq = form_total = 0

    for t in range(form_start, i + 1):
        row = [digits[pos][t] for pos in POS]
        form_total += 1
        # 对子：任意两位置数字相同
        pair_found = any(row[a] == row[b] for a in range(5) for b in range(a + 1, 5))
        if pair_found:
            form_pairs += 1
            lag_2pairs = i - t  # 窗口从旧→新遍历，持续覆盖使最终值=最近一次出现的滞后
        # 三连顺：相邻 3 位按 ±1 连续递进（位置 0-2 或 1-3 或 2-4）
        seq3_found = any(
            all(abs(row[s + 1] - row[s]) == 1 for s in range(k, k + 2))
            for k in (0, 1, 2)
        )
        # 四连顺：相邻 4 位按 ±1 连续递进（位置 0-3 或 1-4）
        seq4_found = any(
            all(abs(row[s + 1] - row[s]) == 1 for s in range(k, k + 3))
            for k in (0, 1)
        )
        if seq3_found:
            form_3seq += 1
            lag_3seq = i - t
        if seq4_found:
            form_4seq += 1
            lag_4quad = i - t

    if form_total > 0:
        feats += [
            float(lag_2pairs if lag_2pairs is not None else 99.0),
            float(lag_3seq if lag_3seq is not None else 99.0),
            float(lag_4quad if lag_4quad is not None else 99.0),
            float(form_pairs / form_total),  # 窗口内对子占比（辅助信号）
        ]
    else:
        feats += [99.0, 99.0, 99.0, 0.0]

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
    按位训练 GBRT（梯度提升回归树，纯 numpy 实现），预测下一期各位数字的概率分布。

    设计要点：
      - 仅用 issue i 之前（<= i）的可观测数据构造特征，严禁前视泄漏。
      - 每位置独立训练一个 One-vs-Rest GBDT，输出 softmax 概率向量。
      - v3.70 (1.3): 优先使用 sklearn GradientBoostingClassifier（若已安装），
        缺失或异常时自动降级回纯 numpy GBRT 路径并记录降级日志，保证降级保底。
      - 串行训练 5 位，异常时自动降级为均匀分布。
      - 诚实声明：公平摇号下该模型期望命中率 = 随机基线（Top-1 ≈ 10%）。

    Args:
        sorted_data: 按 issue 正序排列的历史数据。
        target_issue: 目标期号（用于日志）。
        progress_callback: 进度回调（可选）。

    Returns:
        List[Dict[int, float]]（5 个位置，每位 {0..9: 概率} 且和为 1）；
        若数据不足或训练失败，返回 None。
    """
    issues, digits, hezhi = _parse_history(sorted_data)
    _get_logger().info('[ml_predictor] _parse_history 返回: issues=%d, digits 类型=%s, hezhi=%d',
                       len(issues), type(digits).__name__, len(hezhi))
    if not isinstance(digits, dict):
        _get_logger().error('[ml_predictor] digits 不是字典！类型=%s, 值=%s', type(digits).__name__, digits)
        return None
    n = len(issues)
    if n < ML_MIN_SAMPLES:
        # 调用方传入数据不足（如 pipeline 默认 data_limit=60），自动从数据库补全全量历史
        _get_logger().info(
            '[ml_predictor] 传入样本 %d 期不足 %d 期，尝试从数据库补全全量历史...', n, ML_MIN_SAMPLES)
        conn_full = _connect_db()
        if conn_full is not None:
            try:
                full_data = _load_full_history(conn_full)
                if full_data and len(full_data) >= ML_MIN_SAMPLES:
                    _get_logger().info(
                        '[ml_predictor] 数据库补全成功: %d 期，重新解析...', len(full_data))
                    issues, digits, hezhi = _parse_history(full_data)
                    n = len(issues)
            except Exception as e:
                _get_logger().warning('[ml_predictor] 数据库补全失败: %s', e)
            finally:
                try:
                    conn_full.close()
                except Exception:
                    pass
    if n < ML_MIN_SAMPLES:
        _get_logger().warning('[ml_predictor] 历史样本不足(%d < %d)，跳过。', n, ML_MIN_SAMPLES)
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

    result: List[Dict[int, float]] = []
    DEFAULT_DIST = {d: 0.1 for d in NUM}

    # ---- 纯 numpy GBRT 路径（无 sklearn 依赖，串行训练避免 Windows daemon 限制）----
    for p_idx, p in enumerate(POS):
        try:
            _get_logger().debug('[ml_predictor] 开始训练 %s 位，digits 长度=%d, n=%d', p, len(digits[p]), n)
            dist = _train_gbml_model(p, issues, digits, hezhi, pos_trend, spj, hz, n)
            if dist is not None:
                result.append(dist)
                if progress_callback:
                    progress_callback(1, f'监督模型[{p}]完成（GBRT-numpy）')
            else:
                result.append(DEFAULT_DIST)
                _get_logger().warning('[ml_predictor] %s 位 GBRT 训练失败，回退均匀分布。', p)
        except Exception as e:
            result.append(DEFAULT_DIST)
            _get_logger().error('[ml_predictor] %s 位训练异常: %s\n%s', p, e, traceback.format_exc())

    _get_logger().info('[ml_predictor] 多源监督模型预测完成（目标期 %s）。', target_issue)
    return result if result else None


def _train_gbml_model(p: str, issues: List[str], digits: Dict[str, List[int]],
                      hezhi: List[int], pos_trend: Dict, spj: Dict, hz: Dict,
                      n: int, progress_callback=None) -> Optional[Dict[int, float]]:
    """
    对指定位置用纯 numpy 实现 GBRT（梯度提升回归树），输出预测概率向量。

    方法：
      - 特征向量 ~110 维（由 _build_feature 构造，含 v3.70 新增位置链/形态特征）。
      - One-vs-Rest 策略：对每个数字类训练一个 GBDT 二分类器。
      - 每棵树为简单深度3决策树（手工实现节点分裂）。
      - 最终概率 = softmax(各classifier的预测值)。

    Returns:
        {0: prob, 1: prob, ..., 9: prob} 归一化概率分布，或 None（训练失败）。
    """
    y_seq = digits[p]
    _get_logger().info('[ml_predictor] %s 位: y_seq 类型=%s, 长度=%d, 前5项=%s',
                       p, type(y_seq).__name__, len(y_seq), y_seq[:5] if y_seq else '空')
    X, y_labels = [], []

    for i in range(60, n):  # 从第60期开始（特征需60期历史）
        feat = _build_feature(p, i, issues, digits, hezhi, pos_trend, spj, hz)
        if feat is not None:
            X.append(feat)
            try:
                label = y_seq[i]
                # v3.57：循环逐样本 INFO → DEBUG，避免 ML 训练时刷屏几百行日志，
                # 进而触发 RotatingFileHandler 频繁 rollover（多线程并发 rollover 在
                # Windows 下会报 WinError 32）。仅在调试时开启。
                _get_logger().debug('[ml_predictor] i=%d, label=%s (type=%s)', i, label, type(label).__name__)
                y_labels.append(label)
            except Exception as e2:
                _get_logger().error('[ml_predictor] y_seq[%d] 索引失败: %s, y_seq 类型=%s, len=%d',
                                   i, e2, type(y_seq).__name__, len(y_seq))
                raise

    if len(X) < 100:
        _get_logger().warning(
            '[ml_predictor] %s 位: 有效特征样本不足(len X=%d, n=%d)，回退均匀分布。',
            p, len(X), n)
        return None  # 样本不足

    X = np.array(X, dtype=float)
    y = np.array(y_labels, dtype=int)
    n_samples, n_features = X.shape

    # v3.70 (1.4): 训练前用 SHAP / sklearn 重要性 / 方差 筛选 Top-K 特征子集
    # K 默认 60（SHAP_DEFAULT_TOP_K），与 110 维特征中的冗余部分匹配；
    # 筛选失败（返回 None）时自动回退使用全部特征，保证降级保底。
    feat_idx = _select_features_by_shap(X, y, k=SHAP_DEFAULT_TOP_K,
                                          n_estimators=30, learning_rate=0.1,
                                          max_depth=3)
    if feat_idx is not None and len(feat_idx) < n_features:
        X = X[:, feat_idx]
        _get_logger().info(
            '[ml_predictor] %s 位: 特征筛选 %d -> %d 列', p, n_features, len(feat_idx))

    # v3.70 (1.5): Optuna 超参数自动调优（仅当数据充足且 Optuna 可用时）
    n_estimators = 30
    learning_rate = 0.1
    max_depth = 3
    if _OPTUNA_AVAILABLE and X.shape[0] >= 200:
        try:
            best_params = _optimize_params_optuna(X, y, n_trials=15)
            if best_params:
                n_estimators = int(best_params.get('n_estimators', 30))
                learning_rate = float(best_params.get('learning_rate', 0.1))
                max_depth = int(best_params.get('max_depth', 3))
                _get_logger().info('[ml_predictor] %s 位 Optuna 调优成功: %s', p, best_params)
            else:
                _get_logger().info('[ml_predictor] %s 位 Optuna 调优无结果，使用默认值', p)
        except Exception as e:
            _get_logger().warning('[ml_predictor] %s 位 Optuna 调优失败，使用默认值: %s', p, e)
    else:
        if not _OPTUNA_AVAILABLE:
            _get_logger().debug('[ml_predictor] %s 位 Optuna 不可用，使用默认超参数', p)
        else:
            _get_logger().debug('[ml_predictor] %s 位 样本不足(%d)，跳过 Optuna 调优', p, X.shape[0])

    # One-vs-Rest：对每个数字类训练 GBDT 二分类器
    n_classes = 10
    final_scores = np.zeros(n_classes)

    # v3.70 (1.3): 优先走 sklearn 路径，缺失/异常自动降级回纯 numpy 路径并记录降级日志
    _used_sklearn = _SKLEARN_AVAILABLE
    _sklearn_degraded = False
    for c in range(n_classes):
        y_bin = (y == c).astype(float)
        if y_bin.sum() < 5:
            continue  # 该类样本太少，保持 score=0
        try:
            if _used_sklearn:
                try:
                    score = _sklearn_gbrt_predict(X, y_bin, n_estimators, learning_rate, max_depth)
                except Exception as _sk_ex:
                    _get_logger().warning(
                        '[ml_predictor] %s 位 数字%d sklearn GBRT 失败，降级 numpy 路径: %s',
                        p, c, _sk_ex)
                    _sklearn_degraded = True
                    _used_sklearn = False  # 后续类全部走 numpy，避免重复降级日志刷屏
                    score = _gbrt_predict(X, y_bin, n_estimators, learning_rate, max_depth, n_samples)
            else:
                score = _gbrt_predict(X, y_bin, n_estimators, learning_rate, max_depth, n_samples)
            final_scores[c] = score
        except Exception as ex:
            _get_logger().warning('[ml_predictor] %s 位 数字%d GBRT训练异常: %s', p, c, ex)
            pass  # 保持 score=0

    if _sklearn_degraded:
        _get_logger().info(
            '[ml_predictor] %s 位 GBRT 已降级为纯 numpy 路径（sklearn 不可用/异常: %s）。',
            p, _SKLEARN_IMPORT_ERROR or 'runtime_error')

    # softmax 归一化
    exp_vals = np.exp(final_scores - np.max(final_scores))
    probs = exp_vals / exp_vals.sum()

    return {d: float(probs[d]) for d in NUM}


# ----------------------------------------------------------------------------
# 纯 numpy GBDT 实现（无 sklearn 依赖）
# ----------------------------------------------------------------------------
def _gbrt_predict(X: np.ndarray, y: np.ndarray,
                  n_estimators: int, learning_rate: float,
                  max_depth: int, n_samples: int) -> float:
    """
    手工实现 GBDT 二分类器，返回最后一个样本的预测值（log-odds 空间）。

    算法：负梯度下降 + 回归树拟合残差
      - 初始预测：log(p/(1-p))，p = mean(y)
      - 每轮：计算负梯度（残差）→ 用决策树拟合 → 更新预测值
      - 最终预测值 = 初始值 + learning_rate * 所有树的预测之和

    Args:
        X: (n_samples, n_features) 特征矩阵
        y: (n_samples,) 二分类标签（0/1 float）
        n_estimators: 树的数量
        learning_rate: 学习率（步长）
        max_depth: 树的最大深度
        n_samples: 样本数（用于初始预测）

    Returns:
        最后一个样本的预测值（标量）
    """
    eps = 1e-12

    # 初始预测：log-odds
    p0 = np.clip(y.mean(), eps, 1 - eps)
    F = np.full(n_samples, np.log(p0 / (1 - p0)))

    for _ in range(n_estimators):
        # 负梯度（伪残差）：y - sigmoid(F)
        sigmoid_F = 1.0 / (1.0 + np.exp(-np.clip(F, -500, 500)))
        residuals = y - sigmoid_F

        # 用决策树拟合残差
        tree = _build_tree(X, residuals, depth=0, max_depth=max_depth)
        tree_pred = _tree_predict(X, tree)

        # 更新预测值
        F = F + learning_rate * tree_pred

    # 返回最后一个样本的预测值
    return float(F[-1])


def _build_tree(X: np.ndarray, y: np.ndarray,
                depth: int, max_depth: int) -> dict:
    """
    手工构建回归树（CART），使用方差最小化分裂。

    Returns:
        树节点字典：{'leaf': value} 或 {'feature': f, 'threshold': t, 'left': ..., 'right': ...}
    """
    n = len(y)
    if n < 2:
        return {'leaf': float(y[0]) if n == 1 else 0.0}

    if depth >= max_depth or n <= 4:
        return {'leaf': float(y.mean())}

    best_gain = -1.0
    best_feature = 0
    best_threshold = 0.0
    total_var = np.var(y) * n

    # 采样特征子集（约一半特征，加速训练）
    n_feat = X.shape[1]
    feat_indices = np.random.choice(n_feat, size=min(n_feat, max(8, n_feat // 2)), replace=False)

    for f in feat_indices:
        col = X[:, f]
        # 对特征值排序后取中点作为候选阈值
        sorted_idx = np.argsort(col)
        sorted_col = col[sorted_idx]
        sorted_y = y[sorted_idx]

        # 跳过相同特征值的边界
        unique_vals = np.unique(sorted_col)
        if len(unique_vals) <= 1:
            continue

        # 取均匀采样的候选阈值（最多20个）
        if len(unique_vals) > 20:
            thresholds = np.percentile(sorted_col, np.linspace(10, 90, 20))
        else:
            thresholds = (unique_vals[:-1] + unique_vals[1:]) / 2.0

        for t in thresholds:
            left_mask = col <= t
            right_mask = ~left_mask
            n_left = left_mask.sum()
            n_right = right_mask.sum()
            if n_left < 2 or n_right < 2:
                continue

            left_var = np.var(y[left_mask]) * n_left
            right_var = np.var(y[right_mask]) * n_right
            gain = total_var - left_var - right_var

            if gain > best_gain:
                best_gain = gain
                best_feature = f
                best_threshold = t

    if best_gain <= 0:
        return {'leaf': float(y.mean())}

    left_mask = X[:, best_feature] <= best_threshold
    right_mask = ~left_mask

    return {
        'feature': int(best_feature),
        'threshold': float(best_threshold),
        'left': _build_tree(X[left_mask], y[left_mask], depth + 1, max_depth),
        'right': _build_tree(X[right_mask], y[right_mask], depth + 1, max_depth),
    }


def _tree_predict(X: np.ndarray, tree: dict) -> np.ndarray:
    """用训练好的树对 X 所有样本进行预测。"""
    if 'leaf' in tree:
        return np.full(X.shape[0], tree['leaf'])

    feature = tree['feature']
    threshold = tree['threshold']
    left = tree['left']
    right = tree['right']

    result = np.zeros(X.shape[0])
    left_mask = X[:, feature] <= threshold
    result[left_mask] = _tree_predict(X[left_mask], left)
    result[~left_mask] = _tree_predict(X[~left_mask], right)
    return result


# ----------------------------------------------------------------------------
# v3.70 (roadmap 1.4) SHAP 特征筛选
# ----------------------------------------------------------------------------
# 新增 _select_features_by_shap：基于 SHAP 值（或 sklearn 重要性 / 方差回退）
# 筛选 Top-K 特征子集，减少冗余输入（K 可配置，默认 60）。
# 优先级:
#   1. 安装 shap + sklearn 时，使用 shap.TreeExplainer 对每个类的 GBRT 模型
#      提取特征重要性（全局 SHAP 均值绝对值），取 Top-K 列。
#   2. 无 shap 但有 sklearn 时，使用 GradientBoostingClassifier.feature_importances_
#      作为特征重要性代理，取 Top-K 列。
#   3. 无 sklearn 时，回退到基于特征方差的排序（纯 numpy），取 Top-K 列。
# 降级保底：任何路径失败返回 None，调用方自动使用全部特征。
_SHAP_AVAILABLE = False
try:
    import shap as _shap
    _SHAP_AVAILABLE = True
except Exception:  # pragma: no cover - 取决于运行环境是否安装 shap
    _SHAP_AVAILABLE = False

SHAP_DEFAULT_TOP_K = 60  # 默认 Top-K，与 110 维特征冗余部分匹配


def _select_features_by_shap(X: np.ndarray, y: np.ndarray,
                             k: int = SHAP_DEFAULT_TOP_K,
                             n_estimators: int = 30,
                             learning_rate: float = 0.1,
                             max_depth: int = 3) -> Optional[np.ndarray]:
    """v3.70 (1.4): 基于 SHAP / sklearn 重要性 / 方差 筛选 Top-K 特征子集。

    设计意图：
        110 维特征中存在冗余（如末位数字/和尾/SPJ方向/位走势等多组特征高度相关），
        训练前筛选 Top-K 子集可减少过拟合、加速训练。K 可配置，默认 60。

    优先级（自动降级）：
        1. shap.TreeExplainer（shap + sklearn 都安装时）
        2. sklearn GradientBoostingClassifier.feature_importances_
        3. 基于特征方差的排序（纯 numpy，不依赖任何第三方库）

    Args:
        X: (n_samples, n_features) 训练特征矩阵
        y: (n_samples,) 多分类标签（0..9），内部会做 One-vs-Rest 聚合
        k: 保留的特征数量（默认 60，k >= n_features 时返回 None 即不筛选）
        n_estimators / learning_rate / max_depth: 与 _train_gbml_model 同参数，
            用于训练临时 GBRT 模型以提取特征重要性。

    Returns:
        保留列的索引数组（np.ndarray[int]，长度 k，升序排列），
        或 None（筛选失败 / k >= n_features 时不筛选，调用方使用全部特征）。

    说明：
        本函数只做"特征筛选"，不做"特征训练"。返回的索引数组在调用方
        通过 X[:, idx] 切片后再喂给 _train_gbml_model 的 One-vs-Rest 循环。
        所有路径失败均不抛异常（降级保底），避免影响主流程。
    """
    n_samples, n_features = X.shape
    if k >= n_features:
        # k 不小于总维度，无需筛选
        return None

    # 路径 1: shap + sklearn（最优，真 SHAP 值）
    if _SHAP_AVAILABLE and _SKLEARN_AVAILABLE:
        try:
            import numpy as _np
            # 用一个临时 GBRT 模型提取特征重要性（对所有类平均）
            idx = _shap_imports_top_k(X, y, k, n_estimators, learning_rate, max_depth)
            if idx is not None:
                _get_logger().info(
                    '[ml_predictor] SHAP 特征筛选成功: 保留 %d / %d 列（路径=shap.TreeExplainer）',
                    len(idx), n_features)
                return idx
        except Exception as e:
            _get_logger().warning(
                '[ml_predictor] SHAP 路径失败，降级到 sklearn/方差路径: %s', e)

    # 路径 2: sklearn feature_importances_（次优）
    if _SKLEARN_AVAILABLE:
        try:
            idx = _sklearn_imports_top_k(X, y, k, n_estimators, learning_rate, max_depth)
            if idx is not None:
                _get_logger().info(
                    '[ml_predictor] sklearn 重要性筛选成功: 保留 %d / %d 列（路径=sklearn.feature_importances_）',
                    len(idx), n_features)
                return idx
        except Exception as e:
            _get_logger().warning(
                '[ml_predictor] sklearn 重要性路径失败，降级到方差路径: %s', e)

    # 路径 3: 基于特征方差的纯 numpy 回退（保底）
    try:
        variances = np.var(X, axis=0)
        idx = np.argsort(variances)[::-1][:k]
        idx = np.sort(idx)  # 升序排列，便于切片
        _get_logger().info(
            '[ml_predictor] 方差回退筛选成功: 保留 %d / %d 列（路径=numpy.var, shap/sklearn 不可用）',
            len(idx), n_features)
        return idx
    except Exception as e:
        _get_logger().warning('[ml_predictor] 方差回退筛选失败，使用全部特征: %s', e)
        return None


def _shap_imports_top_k(X: np.ndarray, y: np.ndarray, k: int,
                        n_estimators: int, learning_rate: float,
                        max_depth: int) -> Optional[np.ndarray]:
    """路径 1 实现：shap.TreeExplainer 对每个 One-vs-Rest 类提取重要性，取 Top-K。"""
    import shap as _shap_mod
    import numpy as _np
    n_classes = int(y.max()) + 1
    mean_abs = np.zeros(X.shape[1])
    n_used = 0
    for c in range(n_classes):
        y_bin = (y == c).astype(int)
        if y_bin.sum() < 5:
            continue
        clf = _sklearn_gradient_boosting_classifier(n_estimators, learning_rate, max_depth)
        clf.fit(X, y_bin)
        try:
            explainer = _shap_mod.TreeExplainer(clf)
            sv = explainer.shap_values(X[:min(200, X.shape[0])])
            if isinstance(sv, list):  # 二分类 shap 返回 [neg, pos]
                sv = sv[-1]
            mean_abs += np.abs(sv).mean(axis=0)
            n_used += 1
        except Exception:
            continue
    if n_used == 0:
        return None
    mean_abs /= n_used
    return np.sort(np.argsort(mean_abs)[::-1][:k])


def _sklearn_gradient_boosting_classifier(n_estimators: int, learning_rate: float,
                                           max_depth: int):
    """统一的 GradientBoostingClassifier 工厂（避免多处 import 不一致）。"""
    from sklearn.ensemble import GradientBoostingClassifier as _GBC
    return _GBC(n_estimators=n_estimators, learning_rate=learning_rate,
                 max_depth=max_depth, random_state=42)


def _sklearn_imports_top_k(X: np.ndarray, y: np.ndarray, k: int,
                           n_estimators: int, learning_rate: float,
                           max_depth: int) -> Optional[np.ndarray]:
    """路径 2 实现：sklearn feature_importances_ 对每个 One-vs-Rest 类提取重要性，取 Top-K。"""
    import numpy as _np
    n_classes = int(y.max()) + 1
    mean_imp = np.zeros(X.shape[1])
    n_used = 0
    for c in range(n_classes):
        y_bin = (y == c).astype(int)
        if y_bin.sum() < 5:
            continue
        clf = _sklearn_gradient_boosting_classifier(n_estimators, learning_rate, max_depth)
        clf.fit(X, y_bin)
        imp = np.asarray(clf.feature_importances_, dtype=float)
        mean_imp += imp
        n_used += 1
    if n_used == 0:
        return None
    mean_imp /= n_used
    return np.sort(np.argsort(mean_imp)[::-1][:k])


def _optimize_params_optuna(X: np.ndarray, y: np.ndarray, n_trials: int = 15) -> Optional[Dict]:
    """v3.70 (1.5): 使用 Optuna 自动调优 GBRT 超参数。
    
    仅在 Optuna 可用且 sklearn 可用时执行，使用交叉验证评估。
    返回最优参数字典，失败时返回 None。
    """
    if not _OPTUNA_AVAILABLE or not _SKLEARN_AVAILABLE:
        return None
    try:
        import optuna
        from sklearn.model_selection import cross_val_score
        from sklearn.ensemble import GradientBoostingClassifier
        
        def objective(trial):
            n_estimators = trial.suggest_int('n_estimators', 30, 200)
            learning_rate = trial.suggest_float('learning_rate', 0.01, 0.3)
            max_depth = trial.suggest_int('max_depth', 2, 6)
            
            # One-vs-Rest 平均交叉验证分数
            scores = []
            n_classes = int(y.max()) + 1
            for c in range(n_classes):
                y_bin = (y == c).astype(int)
                if y_bin.sum() < 5:
                    continue
                clf = GradientBoostingClassifier(
                    n_estimators=n_estimators,
                    learning_rate=learning_rate,
                    max_depth=max_depth,
                    random_state=42
                )
                try:
                    cv_score = cross_val_score(clf, X, y_bin, cv=3, scoring='accuracy')
                    scores.append(float(cv_score.mean()))
                except Exception:
                    continue
            if not scores:
                return 0.0
            return float(np.mean(scores))
        
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        
        if study.best_value and study.best_params:
            params = {
                'n_estimators': int(study.best_params['n_estimators']),
                'learning_rate': float(study.best_params['learning_rate']),
                'max_depth': int(study.best_params['max_depth'])
            }
            return params
    except Exception as e:
        _get_logger().warning('[ml_predictor] Optuna 优化异常: %s', e)
    return None


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
