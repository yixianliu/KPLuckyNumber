"""
概率校准模块

职责：
    把模型输出的原始概率变换成**统计上诚实**的概率，并顺带给出一个
    极有价值的副产品：模型究竟含有多少真实信号。

───────────────────────────────────────────────────────────────
为什么这个模块比"再加一个算法"更有价值
───────────────────────────────────────────────────────────────
排列5每位服从均匀分布 U{0..9}，真实概率恒为 0.1。若模型输出的概率
在 0.06~0.16 之间波动，那些波动**全部是噪声**。此时：

    Brier = E[(p − y)²] = Var(p) + (E[p] − 0.1)² + 0.09

也就是说，在无信号的前提下，**降低预测概率的方差就能严格降低 Brier**。
这给了我们一个漂亮的自检机制：

    令 p' = (1 − ε)·p + ε·0.1        （向均匀分布收缩）

    · 若模型确实含有真实信号 → 最优 ε 会显著小于 1
    · 若模型只是在拟合噪声   → 最优 ε 会趋近 1（完全收缩）

于是 **信号强度 = 1 − ε** 就成了一个从数据中自动学出来的、
无法自欺的"模型有效性"度量。它比任何回测胜率都更难被 p-hacking。

───────────────────────────────────────────────────────────────
两种校准变换
───────────────────────────────────────────────────────────────
1. 温度缩放 Temperature scaling
       p'_c ∝ p_c^(1/T)
   T > 1 使分布变平（降低过度自信），T < 1 使分布变尖。
   需要完整的 10 维概率向量才能重新归一化。

2. 均匀收缩 Shrinkage
       p'_c = (1 − ε)·p_c + ε/10
   对单个分量封闭，因此**只要有"真实号码被赋予的概率"就能拟合**，
   可直接复用历史回测断点文件，无需重跑。

二者可组合使用：先温度、后收缩。

依赖：仅标准库。
"""

import json
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

NUMBER_SPACE = 10
POSITIONS = 5
UNIFORM_PROB = 1.0 / NUMBER_SPACE

#: 均匀猜测的负对数似然基准 ln(10)，任何校准结果都应与之对照
UNIFORM_NLL = math.log(NUMBER_SPACE)

#: 校准参数的默认持久化文件名
CALIBRATION_FILENAME = 'calibration_params.json'


# ============================================================
# 基础变换
# ============================================================

def apply_temperature(probs: Dict[int, float], temperature: float) -> Dict[int, float]:
    """
    温度缩放：p'_c ∝ p_c^(1/T)，随后重新归一化。

    T = 1 时恒等变换；T > 1 让分布更接近均匀（抑制过度自信）。
    使用对数域计算并减去最大值，避免下溢。
    """
    if temperature <= 0 or abs(temperature - 1.0) < 1e-9:
        return dict(probs)

    inv_t = 1.0 / temperature
    logits = {}
    for num in range(NUMBER_SPACE):
        p = max(probs.get(num, 0.0), 1e-12)
        logits[num] = inv_t * math.log(p)

    max_logit = max(logits.values())
    exps = {num: math.exp(v - max_logit) for num, v in logits.items()}
    total = sum(exps.values()) or 1.0
    return {num: v / total for num, v in exps.items()}


def apply_shrinkage(probs: Dict[int, float], epsilon: float) -> Dict[int, float]:
    """
    向均匀分布收缩：p'_c = (1 − ε)·p_c + ε/10。

    ε ∈ [0, 1]。ε = 0 保持原样，ε = 1 完全退化为均匀分布。
    变换后自动归一化，容忍输入未严格归一的情况。
    """
    epsilon = max(0.0, min(1.0, epsilon))
    if epsilon <= 0:
        return dict(probs)

    out = {num: (1 - epsilon) * probs.get(num, 0.0) + epsilon * UNIFORM_PROB
           for num in range(NUMBER_SPACE)}
    total = sum(out.values()) or 1.0
    return {num: v / total for num, v in out.items()}


def shrink_scalar(p: float, epsilon: float) -> float:
    """对单个概率分量做收缩（用于只有 prob_of_actual 的场景）。"""
    return (1 - epsilon) * p + epsilon * UNIFORM_PROB


# ============================================================
# 参数拟合
# ============================================================

def _nll_of_shrinkage(prob_of_actual: Sequence[float], epsilon: float) -> float:
    """给定收缩系数下的平均负对数似然。"""
    if not prob_of_actual:
        return UNIFORM_NLL
    total = 0.0
    for p in prob_of_actual:
        total += -math.log(max(shrink_scalar(p, epsilon), 1e-12))
    return total / len(prob_of_actual)


def _brier_of_shrinkage(prob_of_actual: Sequence[float], epsilon: float) -> float:
    """给定收缩系数下的平均 Brier（仅真实类别分量）。"""
    if not prob_of_actual:
        return 0.0
    return sum((shrink_scalar(p, epsilon) - 1.0) ** 2
               for p in prob_of_actual) / len(prob_of_actual)


def fit_shrinkage(prob_of_actual: Sequence[float],
                  objective: str = 'nll',
                  tolerance: float = 1e-5) -> Dict[str, Any]:
    """
    仅用"真实号码被赋予的概率"拟合最优收缩系数 ε。

    这是本模块最实用的入口：历史回测断点文件里已经保存了
    `predicted_probability` 字段，无需重跑回测即可完成拟合。

    优化方法：目标函数关于 ε 在 [0,1] 上是凸的（NLL 为 log-sum-exp
    型，Brier 为二次型），故用黄金分割搜索即可稳定收敛到全局最优。

    Args:
        prob_of_actual: 每个样本中真实号码被赋予的预测概率
        objective: 'nll'（默认，推荐）或 'brier'
        tolerance: 收敛精度

    Returns:
        {
          'epsilon': float,          # 最优收缩系数
          'signal_strength': float,  # 1 − ε，模型真实信号强度估计
          'objective': str,
          'before': {...}, 'after': {...},
          'improvement': {...},
          'samples': int,
          'interpretation': str      # 人话结论
        }
    """
    samples = len(prob_of_actual)
    if samples == 0:
        return {'epsilon': 1.0, 'signal_strength': 0.0, 'samples': 0,
                'objective': objective,
                'interpretation': '无样本可供拟合，默认完全收缩到均匀分布。'}

    score = _nll_of_shrinkage if objective == 'nll' else _brier_of_shrinkage

    # 黄金分割搜索
    golden = (math.sqrt(5) - 1) / 2
    lo, hi = 0.0, 1.0
    x1 = hi - golden * (hi - lo)
    x2 = lo + golden * (hi - lo)
    f1, f2 = score(prob_of_actual, x1), score(prob_of_actual, x2)

    while abs(hi - lo) > tolerance:
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - golden * (hi - lo)
            f1 = score(prob_of_actual, x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + golden * (hi - lo)
            f2 = score(prob_of_actual, x2)

    epsilon = round((lo + hi) / 2, 6)

    before = {
        'nll': round(_nll_of_shrinkage(prob_of_actual, 0.0), 6),
        'brier': round(_brier_of_shrinkage(prob_of_actual, 0.0), 6),
        'avg_prob': round(sum(prob_of_actual) / samples, 6),
    }
    after = {
        'nll': round(_nll_of_shrinkage(prob_of_actual, epsilon), 6),
        'brier': round(_brier_of_shrinkage(prob_of_actual, epsilon), 6),
        'avg_prob': round(sum(shrink_scalar(p, epsilon) for p in prob_of_actual) / samples, 6),
    }

    signal = round(1.0 - epsilon, 6)
    if signal < 0.05:
        interp = ('最优收缩系数接近 1，说明原始概率的波动几乎全部是噪声——'
                  '模型未检出可用信号，这与排列5公平摇号的理论预期一致。')
    elif signal < 0.25:
        interp = ('检出微弱信号（%.1f%%），但在排列5场景下更可能来自样本波动，'
                  '建议扩大样本后复核。' % (signal * 100))
    else:
        interp = ('检出较强信号（%.1f%%）。请务必用独立的时间段样本复核，'
                  '排除数据泄漏与前视偏差后再采信。' % (signal * 100))

    return {
        'epsilon': epsilon,
        'signal_strength': signal,
        'objective': objective,
        'samples': samples,
        'before': before,
        'after': after,
        'improvement': {
            'nll': round(before['nll'] - after['nll'], 6),
            'brier': round(before['brier'] - after['brier'], 6),
        },
        'uniform_reference_nll': round(UNIFORM_NLL, 6),
        'interpretation': interp,
    }


def fit_temperature(prob_vectors: Sequence[Dict[int, float]],
                    actuals: Sequence[int],
                    t_min: float = 0.5, t_max: float = 8.0,
                    tolerance: float = 1e-4) -> Dict[str, Any]:
    """
    用完整概率向量拟合温度参数 T（最小化 NLL）。

    Args:
        prob_vectors: 每个样本的完整 10 维概率分布
        actuals: 对应的真实号码
        t_min, t_max: 搜索区间
        tolerance: 收敛精度

    Returns:
        {'temperature': float, 'nll_before': float, 'nll_after': float, 'samples': int}
    """
    n = min(len(prob_vectors), len(actuals))
    if n == 0:
        return {'temperature': 1.0, 'samples': 0,
                'nll_before': UNIFORM_NLL, 'nll_after': UNIFORM_NLL}

    def nll_at(t: float) -> float:
        """计算给定温度下的平均负对数似然（NLL）。

        参数:
            t: 温度参数，>1 使概率分布更平滑，<1 使其更尖锐

        返回:
            float —— 全体样本的平均 NLL，值越小表示校准越好

        说明:
            对真实标签概率取对数前先做 1e-12 下限保护，避免 log(0) 溢出。
        """
        total = 0.0
        for i in range(n):
            scaled = apply_temperature(prob_vectors[i], t)
            total += -math.log(max(scaled.get(int(actuals[i]), 0.0), 1e-12))
        return total / n

    golden = (math.sqrt(5) - 1) / 2
    lo, hi = t_min, t_max
    x1 = hi - golden * (hi - lo)
    x2 = lo + golden * (hi - lo)
    f1, f2 = nll_at(x1), nll_at(x2)

    while abs(hi - lo) > tolerance:
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - golden * (hi - lo)
            f1 = nll_at(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + golden * (hi - lo)
            f2 = nll_at(x2)

    temperature = round((lo + hi) / 2, 5)
    return {
        'temperature': temperature,
        'samples': n,
        'nll_before': round(nll_at(1.0), 6),
        'nll_after': round(nll_at(temperature), 6),
        'uniform_reference_nll': round(UNIFORM_NLL, 6),
    }


# ============================================================
# 校准器
# ============================================================

class ProbabilityCalibrator:
    """
    可持久化的概率校准器。

    典型用法：
        cal = ProbabilityCalibrator.load()            # 读历史参数，无则用恒等变换
        fused = cal.transform_all(fused_probabilities)  # 预测时应用

        report = cal.fit_from_backtest_probs(probs)   # 用回测数据重新拟合
        cal.save()

    设计要点：
        · 未拟合时 transform 为**严格恒等变换**，保证接入后不改变既有行为
        · 参数与拟合元信息一并落盘，可追溯是"什么时候、用多少样本"拟出来的
    """

    def __init__(self, temperature: float = 1.0, epsilon: float = 0.0,
                 metadata: Optional[Dict[str, Any]] = None):
        """初始化概率校准器。

        参数:
            temperature: 温度缩放系数，1.0 表示不缩放
            epsilon: 与均匀分布混合的权重，越接近 1 表示模型信号越弱
            metadata: 附加元信息（如拟合样本数、拟合时间等）
        """
        self.temperature = float(temperature)
        self.epsilon = float(epsilon)
        self.metadata: Dict[str, Any] = metadata or {}

    # ---------- 变换 ----------

    @property
    def is_identity(self) -> bool:
        """是否为恒等变换（未校准状态）。"""
        return abs(self.temperature - 1.0) < 1e-9 and self.epsilon < 1e-9

    @property
    def signal_strength(self) -> float:
        """模型真实信号强度估计 = 1 − ε。"""
        return max(0.0, 1.0 - self.epsilon)

    def transform(self, probs: Dict[int, float]) -> Dict[int, float]:
        """对单个位置的概率分布做校准（先温度、后收缩）。"""
        out = apply_temperature(probs, self.temperature)
        out = apply_shrinkage(out, self.epsilon)
        return out

    def transform_all(self, fused_probs: List[Dict[int, float]]) -> List[Dict[int, float]]:
        """对 5 个位置的概率分布批量校准。"""
        if self.is_identity:
            return fused_probs
        return [self.transform(p) for p in fused_probs]

    # ---------- 拟合 ----------

    def fit_from_backtest_probs(self, prob_of_actual: Sequence[float],
                                objective: str = 'nll') -> Dict[str, Any]:
        """
        用回测中"真实号码被赋予的概率"序列拟合收缩系数。

        温度参数保持不变（因为该数据不足以识别温度）。
        """
        report = fit_shrinkage(prob_of_actual, objective=objective)
        self.epsilon = report['epsilon']
        self.metadata = {
            'fitted_at': datetime.now().isoformat(),
            'method': 'shrinkage_only',
            'objective': objective,
            'samples': report['samples'],
            'signal_strength': report['signal_strength'],
            'nll_before': report['before']['nll'],
            'nll_after': report['after']['nll'],
            'interpretation': report['interpretation'],
        }
        return report

    def fit_full(self, prob_vectors: Sequence[Dict[int, float]],
                 actuals: Sequence[int]) -> Dict[str, Any]:
        """
        用完整概率向量联合拟合温度与收缩（先温度，后在温度基础上拟收缩）。
        """
        temp_report = fit_temperature(prob_vectors, actuals)
        self.temperature = temp_report['temperature']

        scaled_actual_probs = [
            apply_temperature(prob_vectors[i], self.temperature).get(int(actuals[i]), 0.0)
            for i in range(min(len(prob_vectors), len(actuals)))
        ]
        shrink_report = fit_shrinkage(scaled_actual_probs, objective='nll')
        self.epsilon = shrink_report['epsilon']

        self.metadata = {
            'fitted_at': datetime.now().isoformat(),
            'method': 'temperature_then_shrinkage',
            'samples': temp_report['samples'],
            'temperature': self.temperature,
            'epsilon': self.epsilon,
            'signal_strength': shrink_report['signal_strength'],
            'nll_raw': temp_report['nll_before'],
            'nll_after_temperature': temp_report['nll_after'],
            'nll_final': shrink_report['after']['nll'],
            'interpretation': shrink_report['interpretation'],
        }
        return {'temperature': temp_report, 'shrinkage': shrink_report,
                'metadata': self.metadata}

    # ---------- 持久化 ----------

    def to_dict(self) -> Dict[str, Any]:
        """把校准器参数导出为可 JSON 序列化的字典。

        返回:
            dict —— 含 temperature、epsilon、metadata 三个键
        """
        return {'temperature': self.temperature, 'epsilon': self.epsilon,
                'metadata': self.metadata}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'ProbabilityCalibrator':
        """从字典还原校准器实例（to_dict 的逆操作）。

        参数:
            payload: 由 to_dict 产生或从磁盘读取的参数字典

        返回:
            ProbabilityCalibrator —— 还原后的校准器；缺失字段使用默认值
        """
        return cls(temperature=payload.get('temperature', 1.0),
                   epsilon=payload.get('epsilon', 0.0),
                   metadata=payload.get('metadata', {}))

    @staticmethod
    def default_path() -> str:
        """默认参数文件路径（predictions/calibration_params.json）。"""
        try:
            from paths import PREDICTIONS_DIR
            base = PREDICTIONS_DIR
        except Exception:
            base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'predictions')
        return os.path.join(base, CALIBRATION_FILENAME)

    def save(self, path: Optional[str] = None) -> str:
        """把校准参数写入 JSON 文件，返回实际写入路径。"""
        target = path or self.default_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        logger.info('校准参数已保存: %s (T=%.4f, eps=%.4f)',
                    target, self.temperature, self.epsilon)
        return target

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'ProbabilityCalibrator':
        """
        从 JSON 文件读取校准参数。文件不存在或损坏时返回**恒等校准器**，
        确保调用方永远不会因为缺少校准文件而失败。
        """
        target = path or cls.default_path()
        try:
            if os.path.exists(target):
                with open(target, 'r', encoding='utf-8') as fh:
                    return cls.from_dict(json.load(fh))
        except Exception as exc:
            logger.warning('读取校准参数失败(%s)，回退为恒等变换: %s', target, exc)
        return cls()

    # ---------- 报告 ----------

    def describe(self) -> str:
        """生成人类可读的校准状态说明。"""
        if self.is_identity:
            return ('概率校准: 未启用（恒等变换）\n'
                    '  提示: 运行「拟合概率校准」后，系统会自动学习收缩系数，\n'
                    '        并给出模型真实信号强度的量化估计。')

        lines = [
            '概率校准: 已启用',
            f'  温度参数 T = {self.temperature:.4f}'
            f'{"（>1，抑制过度自信）" if self.temperature > 1 else ""}',
            f'  收缩系数 ε = {self.epsilon:.4f}',
            f'  信号强度 = {self.signal_strength * 100:.2f}%  (1 − ε)',
        ]
        meta = self.metadata or {}
        if meta.get('samples'):
            lines.append(f'  拟合样本 = {meta["samples"]} 位次'
                         f'   拟合时间 = {meta.get("fitted_at", "未知")[:19]}')
        if meta.get('nll_before') is not None and meta.get('nll_after') is not None:
            lines.append(f'  NLL: {meta["nll_before"]:.5f} → {meta["nll_after"]:.5f}'
                         f'   (均匀基准 {UNIFORM_NLL:.5f})')
        if meta.get('interpretation'):
            lines.append(f'  结论: {meta["interpretation"]}')
        return '\n'.join(lines)


# ============================================================
# 便捷函数：从回测产物抽取校准样本
# ============================================================

def extract_probs_from_backtest(backtest_result: Dict[str, Any]) -> List[float]:
    """
    从 `Backtester.run_backtest` 的返回结构中抽取"真实号码被赋予的概率"。

    兼容两种来源：
      · 完整回测结果 {'results': [{'position_accuracy': [...]}, ...]}
      · 断点续跑文件 {'issues': {期号: {'eval': {...}}}}

    Returns:
        扁平的概率列表，每期贡献 5 个（万千百十个各一）
    """
    probs: List[float] = []

    records = backtest_result.get('results')
    if not records and isinstance(backtest_result.get('issues'), dict):
        records = [v.get('eval', {}) for v in backtest_result['issues'].values()]

    for rec in (records or []):
        for pos_info in (rec.get('position_accuracy') or []):
            p = pos_info.get('predicted_probability')
            if isinstance(p, (int, float)) and p > 0:
                probs.append(float(p))

    return probs


def load_probs_from_resume_files(directory: Optional[str] = None) -> Tuple[List[float], int]:
    """
    扫描回测断点目录，汇总所有可用的校准样本。

    这让用户无需重跑回测就能立即拟合校准参数——历史断点文件里
    已经存着每期每位的 `predicted_probability`。

    Returns:
        (概率列表, 读取到的文件数)
    """
    if directory is None:
        try:
            from paths import REPORTS_BACKTEST_DIR
            directory = REPORTS_BACKTEST_DIR
        except Exception:
            directory = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'reports', 'backtest')

    probs: List[float] = []
    file_count = 0

    if not os.path.isdir(directory):
        return probs, 0

    for name in sorted(os.listdir(directory)):
        if not (name.startswith('resume_') and name.endswith('.json')):
            continue
        try:
            with open(os.path.join(directory, name), 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            extracted = extract_probs_from_backtest(payload)
            if extracted:
                probs.extend(extracted)
                file_count += 1
        except Exception as exc:
            logger.warning('解析断点文件 %s 失败: %s', name, exc)

    return probs, file_count
