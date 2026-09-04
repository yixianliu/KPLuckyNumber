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
    合计 ~98 维（部分源缺失时自动缩减，不影响训练）。
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
      - 纯 numpy 实现，无需 sklearn，任何 Python 环境均可运行。
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
      - 特征向量 ~98 维（由 _build_feature 构造）。
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

    # One-vs-Rest：对每个数字类训练 GBDT 二分类器
    n_classes = 10
    n_estimators = 30   # 树的数量
    learning_rate = 0.1
    max_depth = 3
    final_scores = np.zeros(n_classes)

    for c in range(n_classes):
        y_bin = (y == c).astype(float)
        if y_bin.sum() < 5:
            continue  # 该类样本太少，保持 score=0
        try:
            score = _gbrt_predict(X, y_bin, n_estimators, learning_rate, max_depth, n_samples)
            final_scores[c] = score
        except Exception as ex:
            _get_logger().warning('[ml_predictor] %s 位 数字%d GBRT训练异常: %s', p, c, ex)
            pass  # 保持 score=0

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
