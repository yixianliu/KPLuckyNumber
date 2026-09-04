"""
走势图分析预测引擎

对应需求4功能:
  1) load_trend_data()      — 导入解析各类历史走势图数据(8张走势表)
  2) extract_signals()      — 模式识别与趋势学习(频率/遗漏/动量/方向/和值/贝叶斯)
  3) adapt_signal_weights() — 最新走势动态自动调整信号源参数(EWMA, 实验性)
  4) predict()              — 输出预测 + 置信度评估(相对热度口径, 诚实)

设计原则:
  - 不改动封板核心 (pipeline._predict_trend_multi_source 保持不动), 本模块为纯增量
  - 复用 P5Database 的8张走势表与读取接口
  - 严守诚实口径: relative_hotness = 相对热度(非命中概率)

诚实声明 (v3.14 审计结论):
  排列5 公平摇号, 历史走势无法稳定超越随机基线(Top-1≈10%)。
  本引擎目标是「最大化可解释统计信号 + 诚实呈现」, 不承诺超越随机。
  信号源自适应默认关闭(enable_adapt=False), 回退到对齐 v3.11 的配置常量;
  其价值在「信号源表现可解释」, 而非「提升准确性」。
"""
import logging
import math
from collections import Counter
from typing import Dict, List, Any, Optional, Set

logger = logging.getLogger(__name__)

# 7信号源默认权重 (对齐 pipeline._predict_trend_multi_source v3.11)
DEFAULT_SIGNAL_WEIGHTS: Dict[str, float] = {
    'frequency': 0.30, 'omission': 0.22, 'momentum': 0.13,
    'bayesian': 0.17, 'direction': 0.05, 'sum_center': 0.05,
}

HONEST_DISCLAIMER = (
    "相对热度(非命中概率); 排列5公平摇号, 历史走势无法稳定超越随机基线(Top-1≈10%); "
    "历史命中率不代表未来表现。"
)


def _exp_decay_weights(n: int, halflife: float = 12.0) -> List[float]:
    """指数衰减权重: 越近期权重越高 (对齐 pipeline halflife=12)"""
    lam = math.log(2) / halflife if halflife > 0 else 0.0
    return [math.exp(-lam * (n - 1 - i)) for i in range(n)]


class TrendSignalWeightManager:
    """走势信号源自适应权重管理器 (实验性, 默认关闭)

    追踪6类信号源近期预测表现, EWMA平滑。
    enable_adapt=False 时回退到 DEFAULT_SIGNAL_WEIGHTS。

    注: v3.14 审计确认走势无法稳定超越随机, 故自适应主要价值是「可解释性」,
    不预期突破随机基线。
    """

    def __init__(self, enable_adapt: bool = False, ewma_alpha: float = 0.3):
        """初始化走势信号源权重管理器。

        参数:
            enable_adapt: 是否启用信号源自适应调权（实验性，默认关闭）
            ewma_alpha: EWMA 平滑系数，越大越看重近期表现

        说明:
            每个信号源的表现以 EWMA 追踪，初值 0.5 表示中性、无先验偏好。
        """
        self.enable_adapt = bool(enable_adapt)
        self.ewma_alpha = float(ewma_alpha)
        # 各信号源的 EWMA 表现追踪 (0~1, 初值0.5中性)
        self.signal_perf = {k: {'ewma': 0.5, 'total': 0} for k in DEFAULT_SIGNAL_WEIGHTS}

    def record_signal_performance(self, signal_name: str, hit_rate: float):
        """记录某信号源单次表现 (hit_rate: 0-1)"""
        if signal_name not in self.signal_perf:
            return
        r = self.signal_perf[signal_name]
        r['ewma'] = self.ewma_alpha * float(hit_rate) + (1 - self.ewma_alpha) * r['ewma']
        r['total'] += 1

    def get_weights(self, available_signals: Set[str]) -> Dict[str, float]:
        """返回当前权重: adapt开启则按EWMA表现加权, 否则回退常量; 仅保留可用信号源并归一化"""
        if not self.enable_adapt:
            base = dict(DEFAULT_SIGNAL_WEIGHTS)
        else:
            base = {k: max(0.01, v['ewma']) for k, v in self.signal_perf.items()}
        filtered = {k: v for k, v in base.items() if k in available_signals}
        total = sum(filtered.values()) or 1.0
        return {k: v / total for k, v in filtered.items()}

    def diagnostics(self) -> Dict[str, Dict[str, float]]:
        """各信号源近期表现诊断 (用于诚实呈现)"""
        return {k: {'ewma_perf': round(v['ewma'], 4), 'samples': v['total']}
                for k, v in self.signal_perf.items()}


class TrendAnalyzer:
    """走势图分析预测引擎 (独立模块, 封板后增量)

    复用 P5Database 的8张走势表, 自实现信号提取/融合, 提供对应4功能点的清晰API。
    不依赖 pipeline._predict_trend_multi_source (封板核心保持不动)。
    """

    POSITIONS = ['wan', 'qian', 'bai', 'shi', 'ge']
    POS_NAMES = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

    def __init__(self, db, enable_adapt: bool = False, ewma_alpha: float = 0.3):
        """初始化走势图分析预测引擎。

        参数:
            db: 已连接的 P5Database 实例，用于读取各位置走势数据
            enable_adapt: 是否启用信号源自适应调权（实验性，默认关闭）
            ewma_alpha: 传递给权重管理器的 EWMA 平滑系数
        """
        self.db = db
        self.weight_mgr = TrendSignalWeightManager(enable_adapt, ewma_alpha)

    # ---------------- 功能1: 导入解析各类历史走势图数据 ----------------
    def load_trend_data(self, period: int = 40) -> Dict[str, Any]:
        """加载8类走势数据: 历史走势 / 基础走势 / 5位置走势 / 和值 / 贝叶斯(可选)"""
        if not self.db or not getattr(self.db, 'connection', None):
            logger.warning('TrendAnalyzer: 数据库未连接, 返回空')
            return {}
        db = self.db
        # 历史走势 (DESC, 最新在前)
        try:
            history = db.get_history_data(limit=period) or []
        except Exception as e:
            logger.warning(f'load history 失败: {e}')
            history = []
        # 基础走势 (和值/跨度/奇偶比/大小比)
        try:
            basic = db.get_trend_data(limit=period) or []
        except Exception as e:
            logger.warning(f'load basic trend 失败: {e}')
            basic = []
        # 5位置独立走势表 (含 omission/hot_level/奇偶大小质合)
        pos_trends: Dict[str, List] = {}
        for p in self.POSITIONS:
            try:
                m = getattr(db, f'get_{p}_trend_data', None)
                pos_trends[p] = m(limit=period) if m else []
            except Exception:
                pos_trends[p] = []
        # 和值重心 (p5_hzzst_data 近 period 期, 与整体走势窗口一致)
        hezhi_recent: List[int] = []
        try:
            db.cursor.execute('SELECT hezhi FROM p5_hzzst_data ORDER BY issue DESC LIMIT %s', (period,))
            hezhi_recent = [int(r['hezhi']) for r in (db.cursor.fetchall() or [])
                            if r.get('hezhi') is not None]
        except Exception:
            hezhi_recent = []
        # 贝叶斯后验 (可选, 从 p5_bayesian_result 增量复用)
        bayes = None
        try:
            if hasattr(db, 'get_bayesian_result_row') and history:
                bayes = db.get_bayesian_result_row(history[0].get('issue', ''))
        except Exception:
            bayes = None
        return {
            'history': history, 'basic': basic, 'pos_trends': pos_trends,
            'hezhi_recent': hezhi_recent, 'bayesian': bayes, 'period': period,
        }

    # ---------------- 功能2: 模式识别与趋势学习 ----------------
    def extract_signals(self, position: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """返回该位置各信号源对 0-9 每个数字的归一化打分(0-1) + 可读特征"""
        pos_trends = data.get('pos_trends', {}).get(position, [])
        history_desc = data.get('history', [])
        period = data.get('period', 60)
        # 序列: 优先位置走势表(DESC->reversed=ASC), 不足用历史补
        s = [r.get(f'{position}_number') for r in reversed(pos_trends)
             if r.get(f'{position}_number') is not None]
        if len(s) < 10:
            hist_asc = list(reversed(history_desc))
            s = [r.get(position) for r in hist_asc if r.get(position) is not None]
        s = [int(x) for x in s if x is not None][-period:]
        n = len(s)
        if n == 0:
            return {'available': set(), 'scores': {}, 'features': {}, 'seq_len': 0}

        # 频率(指数衰减加权, halflife=12 对齐 pipeline)
        decay = _exp_decay_weights(n, halflife=12.0)
        freq = Counter()
        for i, v in enumerate(s):
            freq[v] += decay[i]
        total_freq = sum(freq.values()) or 1.0
        freq_norm = {d: freq.get(d, 0) / total_freq for d in range(10)}

        # 遗漏: 距上次出现期数 (reversed后 index0=最新)
        omission = {}
        for d in range(10):
            last_idx = None
            for i, v in enumerate(reversed(s)):
                if v == d:
                    last_idx = i
                    break
            omission[d] = last_idx if last_idx is not None else n
        total_om = sum(omission.values()) or 1
        om_norm = {d: omission[d] / total_om for d in range(10)}

        # 动量: 近期(末8期)均值贴近度
        recent = s[-8:] if n >= 8 else s
        recent_avg = sum(recent) / len(recent)
        mom_raw = {d: -abs(d - recent_avg) for d in range(10)}
        mn, mx = min(mom_raw.values()), max(mom_raw.values())
        rng = (mx - mn) or 1.0
        momentum = {d: (mom_raw[d] - mn) / rng for d in range(10)}

        signals: Dict[str, Dict[int, float]] = {
            'frequency': freq_norm, 'omission': om_norm, 'momentum': momentum,
        }
        features: Dict[str, Any] = {
            'freq_pct': {d: round(freq_norm[d] * 100, 1) for d in range(10)},
            'omission': {int(d): omission[d] for d in range(10)},
        }

        # 升平降方向偏好 (从历史序列涨跌统计推算, 不依赖外部表)
        spref = self._direction_preference(s)
        if spref:
            if spref == 'up':
                signals['direction'] = {d: d / 9.0 for d in range(10)}
            elif spref == 'down':
                signals['direction'] = {d: (9 - d) / 9.0 for d in range(10)}
            else:  # flat: 贴近最新数字
                ld = s[-1]
                signals['direction'] = {d: max(0.0, 1.0 - abs(d - ld) / 5.0) for d in range(10)}
            features['direction_pref'] = spref

        # 和值重心
        hezhi = data.get('hezhi_recent', [])
        if hezhi:
            mean_hz = sum(hezhi) / len(hezhi)
            exp_digit = mean_hz / 5.0
            signals['sum_center'] = {d: (1.0 - min(1.0, abs(d - exp_digit) / 5.0)) for d in range(10)}
            features['hezhi_mean'] = round(mean_hz, 1)

        # 贝叶斯后验 (可选, 有则融合)
        bayes = data.get('bayesian')
        pos_idx = self.POSITIONS.index(position) if position in self.POSITIONS else None
        if bayes and pos_idx is not None and pos_idx < len(bayes):
            bp = bayes[pos_idx]
            if isinstance(bp, dict) and bp:
                tot = sum(float(v) for v in bp.values()) or 1.0
                signals['bayesian'] = {d: float(bp.get(str(d), 0.1)) / tot for d in range(10)}
                features['bayesian'] = True

        return {
            'available': set(signals.keys()),
            'scores': signals,
            'features': features,
            'recent_avg': round(recent_avg, 2),
            'seq_len': n,
        }

    @staticmethod
    def _direction_preference(seq: List[int]) -> Optional[str]:
        """从序列涨跌统计推算方向偏好 (up/down/flat)"""
        if len(seq) < 4:
            return None
        recent = seq[-10:]
        ups = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
        downs = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i - 1])
        if ups > downs + 2:
            return 'up'
        if downs > ups + 2:
            return 'down'
        return 'flat'

    # ---------------- 功能3: 最新走势动态自动调整参数 ----------------
    def adapt_signal_weights(self, feedback: Optional[Dict[str, float]] = None):
        """根据反馈更新信号源 EWMA 表现。

        feedback: {signal_name: hit_rate(0-1)} — 各信号源本期对实际开奖的解释力度。
        无 feedback 时权重管理器保持现状 (默认关闭则回退常量)。

        注: 此为实验性闭环。v3.14 审计确认走势无法稳定超越随机,
        故自适应不预期提升准确性, 价值在「信号源表现可解释」。
        """
        if not feedback:
            return
        for sig, hr in feedback.items():
            self.weight_mgr.record_signal_performance(sig, hr)

    # ---------------- 功能4: 输出预测 + 置信度评估(相对热度) ----------------
    def predict_with_data(self, data: Dict[str, Any], target_issue: str = '') -> Dict[str, Any]:
        """基于预加载数据输出预测 (支持 walk-forward 回测注入截止期数据切片)。

        与 predict() 区别: 调用方自行准备 data (load_trend_data 返回结构),
        适用于回测需要"截至某期"的数据切片——回测脚本取 history[t-period:t]
        构造 data 后调本方法, 避免每次连库取最新。
        """
        if not data or not data.get('history'):
            return {'error': '无历史走势数据', 'target_issue': target_issue}

        positions: Dict[str, Any] = {}
        all_signal_diag = self.weight_mgr.diagnostics()
        for p in self.POSITIONS:
            sig = self.extract_signals(p, data)
            available = sig.get('available', set())
            scores_raw = sig.get('scores', {})
            if not available:
                continue
            weights = self.weight_mgr.get_weights(available)
            # 加权融合
            fused: Dict[int, float] = {}
            for d in range(10):
                fused[d] = sum(weights.get(sname, 0.0) * scores_raw[sname].get(d, 0.0)
                               for sname in available)
            # 归一为相对热度(0-100, 非命中概率)
            mx = max(fused.values()) if fused else 1.0
            mn = min(fused.values()) if fused else 0.0
            rng = (mx - mn) or 1.0
            hotness = {d: round((fused[d] - mn) / rng * 100, 2) for d in range(10)}
            # Top-3 (用户要求浓缩到3个数字)
            top = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:3]
            positions[p] = {
                'position_name': self.POS_NAMES[p],
                'top5': [int(d) for d, _ in top],
                'relative_hotness': {int(d): float(hotness[d]) for d, _ in top},
                'signal_breakdown': {s: round(weights.get(s, 0.0), 3) for s in sorted(available)},
                'features': sig.get('features', {}),
                'recent_avg': sig.get('recent_avg'),
                'seq_len': sig.get('seq_len'),
            }

        return {
            'target_issue': target_issue,
            'data_period': data.get('period', 60),
            'positions': positions,
            'signal_weights': self.weight_mgr.get_weights(set(DEFAULT_SIGNAL_WEIGHTS)),
            'signal_diagnostics': all_signal_diag,
            'enable_adapt': self.weight_mgr.enable_adapt,
            'honest_disclaimer': HONEST_DISCLAIMER,
        }

    def predict(self, target_issue: str = '', period: int = 60) -> Dict[str, Any]:
        """输出各位置 Top-3 + 相对热度 + 信号源分解 + 诊断 + 诚实免责

        内部 = load_trend_data + predict_with_data; 回测场景请直接调
        predict_with_data 注入截止期数据切片。
        """
        data = self.load_trend_data(period=period)
        return self.predict_with_data(data, target_issue=target_issue)


# ---------------- CLI 入口 (独立可验证) ----------------
def _cli():
    """命令行调试入口：加载走势数据并输出一次预测结果（JSON）。

    说明:
        仅供开发调试，正式运行请使用 GUI；
        支持 --issue（目标期号，仅标注）、--period（历史期数，默认 40）、
        --adapt（启用信号源自适应，实验性）三个参数；
        无论预测是否成功都会断开数据库连接。
    """
    import argparse
    import json
    from modules.database import P5Database

    parser = argparse.ArgumentParser(description='走势图分析预测引擎 (v3.15 增量, 诚实口径)')
    parser.add_argument('--issue', default='', help='目标期号(仅标注, 不影响数据加载)')
    parser.add_argument('--period', type=int, default=60, help='使用历史期数 (默认60)')
    parser.add_argument('--adapt', action='store_true', help='启用信号源自适应(实验性, 默认关闭)')
    args = parser.parse_args()

    db = P5Database()
    if not db.connect():
        print('数据库连接失败')
        return
    try:
        analyzer = TrendAnalyzer(db, enable_adapt=args.adapt)
        result = analyzer.predict(target_issue=args.issue, period=args.period)
    finally:
        db.disconnect()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    _cli()
