"""
选号策略引擎

职责：
    在「每位概率分布」已经给定之后，决定**如何把这些概率转化为 K 注具体号码**。
    这一步此前被严重低估——它不是预测问题，而是一个纯粹的组合优化问题，
    并且是整个系统中少数几个能带来**可证明、可复现提升**的环节。

───────────────────────────────────────────────────────────────
核心洞察
───────────────────────────────────────────────────────────────
原实现（predictor._generate_combinations_v2）的做法是：
    每位取 Top-6 → 笛卡尔积 7776 种 → 按联合概率排序 → 取前 10。

这个做法在"精确全中"目标下是最优的，但它有一个被忽视的副作用：
**排序靠前的 10 注高度同质**。实测每位平均只覆盖 2.2 个不同号码，
于是"至少一注命中该位"的期望只有 1.09 / 5 位。

而同样是 10 注，若让每位覆盖全部 0-9，该位必然被命中，期望变成 5.00 / 5。
这不是预测能力的提升，而是**把原本白白丢掉的组合自由度捡回来**。

两个目标是数学上不同的东西，必须分开讨论：

    目标 A（精确全中）: P = Σ_{i∈S} p(combo_i)
        · 公平摇号下 p(combo) 恒为 1e-5，故 P = K/100000，与选谁无关
        · 结论：不可优化，只与注数有关

    目标 B（位覆盖命中）: E[M] = Σ_pos |{第 pos 位出现过的号码}| / 10
        · 完全由选号集合的构造决定
        · 结论：可优化，且优化空间巨大（1.09 → 5.00）

本模块让用户显式选择要优化哪个目标，而不是稀里糊涂地只优化 A 却损失 B。

───────────────────────────────────────────────────────────────
策略清单
───────────────────────────────────────────────────────────────
    max_probability     纯概率贪心。最大化"某注全中"的概率。位覆盖最差。
    latin_coverage      拉丁方覆盖。每位强制覆盖 min(K,10) 个号码，E[M] 最大。
    weighted_coverage 概率加权覆盖（默认推荐）。按概率给每位号码分配槽位，
                        自动在"信任概率"与"分散覆盖"之间取平衡。
    hybrid              前若干注走 max_probability 保住尖峰，其余用覆盖填充。
    legacy_constrained  保留原有 7 项形态软约束的行为，用于回归对照。

关于形态约束（和值/跨度/奇偶/SSD）的立场：
    这些约束在公平摇号下**不会提升命中率**——它们只是重新排序了等概率的
    组合，并系统性排除了合法开奖号（历史上和值 <10 或 >35 的期数确实存在）。
    因此本模块默认**关闭**所有形态约束，仅在 legacy_constrained 下保留，
    并在返回结果中显式标注被约束排除掉的合法组合比例，供用户判断。

依赖：仅标准库。
"""

import itertools
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

POSITIONS = 5
NUMBER_SPACE = 10
POSITION_KEYS = ['wan', 'qian', 'bai', 'shi', 'ge']
POSITION_NAMES = ['万位', '千位', '百位', '十位', '个位']

#: 策略标识 → 中文显示名（GUI 下拉框直接消费）
STRATEGY_LABELS: Dict[str, str] = {
    'weighted_coverage': '概率加权覆盖（推荐）',
    'latin_coverage': '拉丁方全覆盖',
    'max_probability': '纯概率贪心',
    'hybrid': '混合（尖峰+覆盖）',
    'legacy_constrained': '传统形态约束（对照）',
}

#: 策略标识 → 优化目标说明
STRATEGY_OBJECTIVES: Dict[str, str] = {
    'weighted_coverage': '兼顾位覆盖与概率倾斜，综合期望最优',
    'latin_coverage': '最大化位覆盖命中数 E[M]',
    'max_probability': '最大化单注精确全中概率',
    'hybrid': '保留最高概率组合，同时补足位覆盖',
    'legacy_constrained': '复现 v3.x 原有行为，仅供回归对照',
}

DEFAULT_STRATEGY = 'weighted_coverage'


# ============================================================
# 配额分配：把概率转成"每个号码占几注"
# ============================================================

def _quota_allocation(probs: Dict[int, float], k: int) -> List[Tuple[int, int]]:
    """
    最大余数法（Hamilton 配额法）：按概率把 k 个槽位分配给 0-9 各号码。

    为什么用最大余数法而不是简单四舍五入：
        四舍五入后总数往往不等于 k，需要临时补丁；最大余数法天然保证
        Σ counts == k，且分配结果对概率的偏离最小。

    Args:
        probs: {号码: 概率}
        k: 总槽位数（即注数）

    Returns:
        [(号码, 槽位数)]，按"槽位数降序、概率降序"排列，仅含槽位数 > 0 的项
    """
    if k <= 0:
        return []

    ordered = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
    exact = [(num, prob * k) for num, prob in ordered]

    counts = {num: int(math.floor(val)) for num, val in exact}
    assigned = sum(counts.values())
    remaining = k - assigned

    # 按小数部分降序补齐剩余槽位
    by_frac = sorted(exact, key=lambda t: (-(t[1] - math.floor(t[1])), -probs.get(t[0], 0)))
    idx = 0
    while remaining > 0 and by_frac:
        num = by_frac[idx % len(by_frac)][0]
        counts[num] += 1
        remaining -= 1
        idx += 1

    result = [(num, cnt) for num, cnt in counts.items() if cnt > 0]
    result.sort(key=lambda t: (-t[1], -probs.get(t[0], 0.0), t[0]))
    return result


def _enforce_coverage_floor(allocation: List[Tuple[int, int]],
                            probs: Dict[int, float],
                            k: int,
                            floor: int) -> List[Tuple[int, int]]:
    """
    保证该位至少覆盖 `floor` 个不同号码。

    做法：若当前覆盖数不足，从槽位最多的号码里逐个"匀"出一个槽位，
    分给尚未获得槽位的最高概率号码。这样在满足覆盖下限的同时，
    对原概率分配的扰动最小。

    Args:
        allocation: `_quota_allocation` 的输出
        probs: 原概率分布
        k: 总槽位数
        floor: 覆盖下限（会自动截断到 min(k, 10)）

    Returns:
        调整后的 allocation
    """
    floor = max(1, min(int(floor), k, NUMBER_SPACE))
    current = {num: cnt for num, cnt in allocation}

    uncovered = [num for num, _ in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
                 if current.get(num, 0) == 0]

    while len(current) < floor and uncovered:
        # 找当前槽位最多的号码（并列时取概率最低者，扰动最小）
        donor = max(current.items(), key=lambda kv: (kv[1], -probs.get(kv[0], 0.0)))
        if donor[1] <= 1:
            break
        recipient = uncovered.pop(0)
        current[donor[0]] -= 1
        current[recipient] = 1

    result = [(num, cnt) for num, cnt in current.items() if cnt > 0]
    result.sort(key=lambda t: (-t[1], -probs.get(t[0], 0.0), t[0]))
    return result


def _expand_sequence(allocation: List[Tuple[int, int]], k: int) -> List[int]:
    """
    把配额展开成长度为 k 的号码序列，高概率号码排在前面。

    序列的第 0 个元素必定是该位概率最高的号码，这保证了后续装配出的
    第 1 注恰好是"每位最高概率"的组合（即原实现的 wildcard），
    从而在引入覆盖优化的同时不丢失最尖峰的那一注。
    """
    seq: List[int] = []
    for num, cnt in allocation:
        seq.extend([num] * cnt)
    if len(seq) < k:
        seq.extend([allocation[0][0] if allocation else 0] * (k - len(seq)))
    return seq[:k]


# ============================================================
# 组合装配：把 5 个序列错位组合成 K 注
# ============================================================

def _coprime_steps(k: int) -> List[int]:
    """
    取 5 个与 k 互质的步长，用于各位之间的错位轮转。

    互质保证 (i·step mod k) 在 i=0..k-1 上遍历全部索引，即该位的
    配额序列被完整使用一次，覆盖度不打折。不同位使用不同步长，
    避免两位之间产生同步（同步会让组合退化、多样性下降）。
    """
    steps = [s for s in range(1, max(2, k)) if math.gcd(s, k) == 1]
    if not steps:
        steps = [1]
    chosen: List[int] = []
    for pos in range(POSITIONS):
        chosen.append(steps[(pos * max(1, len(steps) // POSITIONS + 1)) % len(steps)])
    # 尽量去重，减少位间同步
    seen: set = set()
    for i, s in enumerate(chosen):
        if s in seen:
            for cand in steps:
                if cand not in seen:
                    chosen[i] = cand
                    break
        seen.add(chosen[i])
    return chosen


def _assemble_combinations(sequences: List[List[int]], k: int) -> List[Tuple[int, ...]]:
    """
    将每位的配额序列错位轮转装配成 K 注互不相同的组合。

    装配规则：第 i 注的第 pos 位 = sequences[pos][(i · step_pos) mod k]

    i = 0 时所有位索引都取 0，因此第 1 注恒为"每位最高概率"组合。
    随后各位以不同步长推进，使组合迅速分散，位覆盖被完整保留。

    若出现重复组合（当某位配额高度集中时可能发生），用同位序列内的
    其它元素做最小修复，保证输出 K 注两两不同。
    """
    if k <= 0:
        return []

    steps = _coprime_steps(k)
    combos: List[Tuple[int, ...]] = []
    seen: set = set()

    for i in range(k):
        combo = [sequences[pos][(i * steps[pos]) % len(sequences[pos])]
                 for pos in range(POSITIONS)]
        key = tuple(combo)

        if key in seen:
            # 最小修复：逐位尝试替换为该位序列中未导致冲突的其它号码
            repaired = False
            for pos in range(POSITIONS - 1, -1, -1):
                for alt in dict.fromkeys(sequences[pos]):
                    if alt == combo[pos]:
                        continue
                    trial = list(combo)
                    trial[pos] = alt
                    if tuple(trial) not in seen:
                        combo, key = trial, tuple(trial)
                        repaired = True
                        break
                if repaired:
                    break
            if not repaired:
                continue

        seen.add(key)
        combos.append(key)

    return combos


# ============================================================
# 各策略实现
# ============================================================

def _strategy_max_probability(fused_probs: List[Dict[int, float]], k: int,
                              position_top_n: int = 6) -> List[Tuple[int, ...]]:
    """纯概率贪心：Top-N 笛卡尔积按联合概率排序取前 K。"""
    candidates = []
    for pos in range(POSITIONS):
        ordered = sorted(fused_probs[pos].items(), key=lambda kv: (-kv[1], kv[0]))
        candidates.append([num for num, _ in ordered[:max(1, position_top_n)]])

    scored: List[Tuple[Tuple[int, ...], float]] = []
    for combo in itertools.product(*candidates):
        score = 1.0
        for pos, num in enumerate(combo):
            score *= fused_probs[pos].get(num, 0.0)
        if score > 0:
            scored.append((combo, score))

    scored.sort(key=lambda t: -t[1])
    return [combo for combo, _ in scored[:k]]


def _strategy_coverage(fused_probs: List[Dict[int, float]], k: int,
                       coverage_floor: int,
                       uniform_weight: float = 0.0) -> List[Tuple[int, ...]]:
    """
    覆盖类策略的统一实现。

    Args:
        coverage_floor: 每位至少覆盖的不同号码数
        uniform_weight: 0.0 表示完全按模型概率分配槽位（weighted_coverage）；
                        1.0 表示完全均匀分配（latin_coverage，纯覆盖优先）。
                        中间值做线性混合。
    """
    sequences: List[List[int]] = []
    for pos in range(POSITIONS):
        probs = dict(fused_probs[pos])
        if uniform_weight > 0:
            uniform_p = 1.0 / NUMBER_SPACE
            probs = {num: (1 - uniform_weight) * probs.get(num, 0.0) + uniform_weight * uniform_p
                     for num in range(NUMBER_SPACE)}
            total = sum(probs.values()) or 1.0
            probs = {num: p / total for num, p in probs.items()}

        allocation = _quota_allocation(probs, k)
        allocation = _enforce_coverage_floor(allocation, probs, k, coverage_floor)
        sequences.append(_expand_sequence(allocation, k))

    return _assemble_combinations(sequences, k)


def _strategy_hybrid(fused_probs: List[Dict[int, float]], k: int,
                     coverage_floor: int, anchor_count: int = 3,
                     position_top_n: int = 6) -> List[Tuple[int, ...]]:
    """混合策略：前 anchor_count 注取概率最高，其余用覆盖策略填充。"""
    anchor_count = max(0, min(anchor_count, k))
    anchors = _strategy_max_probability(fused_probs, anchor_count, position_top_n)

    remaining = k - len(anchors)
    if remaining <= 0:
        return anchors[:k]

    filler = _strategy_coverage(fused_probs, k, coverage_floor, uniform_weight=0.35)
    seen = set(anchors)
    out = list(anchors)
    for combo in filler:
        if len(out) >= k:
            break
        if combo not in seen:
            out.append(combo)
            seen.add(combo)
    return out[:k]


def _strategy_legacy(fused_probs: List[Dict[int, float]], k: int,
                     position_top_n: int, constraints: Dict[str, Any]
                     ) -> Tuple[List[Tuple[int, ...]], Dict[str, Any]]:
    """
    复现 v3.x 的形态软约束打分逻辑，用于 A/B 对照。

    同时统计"被约束显著降权的合法组合占比"，量化约束造成的信息损失。
    """
    hezhi_min = constraints.get('hezhi_min', 10)
    hezhi_max = constraints.get('hezhi_max', 35)
    span_min = constraints.get('span_min', 3)
    span_max = constraints.get('span_max', 8)
    enable_ssd = constraints.get('sum_of_squares_penalty', True)
    mean = 4.5

    candidates = []
    for pos in range(POSITIONS):
        ordered = sorted(fused_probs[pos].items(), key=lambda kv: (-kv[1], kv[0]))
        candidates.append([num for num, _ in ordered[:max(1, position_top_n)]])

    scored: List[Tuple[Tuple[int, ...], float]] = []
    penalized = 0
    total = 0

    for combo in itertools.product(*candidates):
        base = 1.0
        for pos, num in enumerate(combo):
            base *= fused_probs[pos].get(num, 0.0)
        if base <= 0:
            continue
        total += 1
        score = base

        adjacent_similar = sum(1 for i in range(POSITIONS - 1)
                               if abs(combo[i] - combo[i + 1]) <= 1)
        if adjacent_similar > 2:
            score *= 0.85

        hezhi = sum(combo)
        if hezhi < 5 or hezhi > 40:
            score *= 0.6
        elif hezhi < hezhi_min or hezhi > hezhi_max:
            score *= 0.85

        odd_count = sum(1 for num in combo if num % 2 == 1)
        if odd_count in (0, POSITIONS):
            score *= 0.6

        if enable_ssd:
            ssd = sum((num - mean) ** 2 for num in combo) / POSITIONS
            if ssd < 1.0:
                score *= 0.9
            elif ssd > 20.0:
                score *= 0.85
            elif ssd > 15.0:
                score *= 0.95

        combo_span = max(combo) - min(combo)
        if combo_span < span_min:
            score *= 0.85
        elif combo_span > span_max:
            score *= 0.9

        if score < base:
            penalized += 1
        scored.append((combo, score))

    scored.sort(key=lambda t: -t[1])
    diagnostics = {
        'candidates_evaluated': total,
        'candidates_penalized': penalized,
        'penalized_ratio': round(penalized / total, 4) if total else 0.0,
    }
    return [combo for combo, _ in scored[:k]], diagnostics


# ============================================================
# 指标评估
# ============================================================

def evaluate_selection(combinations: Sequence[Sequence[int]],
                       fused_probs: List[Dict[int, float]]) -> Dict[str, Any]:
    """
    评估一个选号集合的构造质量（与真实开奖无关，纯先验期望）。

    Returns:
        position_coverage: 每位覆盖的不同号码个数
        expected_covered_positions: 公平摇号下期望被覆盖命中的位数（0-5）
        model_expected_covered_positions: 按模型概率计算的期望覆盖位数
        exact_hit_probability_model: 按模型概率算的"至少一注全中"概率
        exact_hit_probability_fair: 公平摇号下的同一概率 = K/100000
        expected_single_matches: 单注期望位命中数（公平摇号下恒为 0.5）
        diversity: 组合两两平均汉明距离 / 5，衡量分散程度
    """
    k = len(combinations)
    if k == 0:
        return {'combination_count': 0}

    coverage_sets: List[set] = [set() for _ in range(POSITIONS)]
    for combo in combinations:
        for pos in range(min(POSITIONS, len(combo))):
            coverage_sets[pos].add(int(combo[pos]))

    coverage_sizes = [len(s) for s in coverage_sets]
    expected_fair = sum(coverage_sizes) / NUMBER_SPACE

    model_expected = 0.0
    for pos in range(POSITIONS):
        model_expected += sum(fused_probs[pos].get(num, 0.0) for num in coverage_sets[pos])

    exact_model = 0.0
    for combo in combinations:
        p = 1.0
        for pos in range(min(POSITIONS, len(combo))):
            p *= fused_probs[pos].get(int(combo[pos]), 0.0)
        exact_model += p

    # 平均两两汉明距离
    if k > 1:
        pair_total, pair_count = 0, 0
        for a in range(k):
            for b in range(a + 1, k):
                dist = sum(1 for pos in range(POSITIONS)
                           if combinations[a][pos] != combinations[b][pos])
                pair_total += dist
                pair_count += 1
        diversity = pair_total / pair_count / POSITIONS if pair_count else 0.0
    else:
        diversity = 0.0

    return {
        'combination_count': k,
        'position_coverage': coverage_sizes,
        'expected_covered_positions': round(expected_fair, 4),
        'model_expected_covered_positions': round(model_expected, 4),
        'exact_hit_probability_model': round(exact_model, 8),
        'exact_hit_probability_fair': round(k / (NUMBER_SPACE ** POSITIONS), 8),
        'expected_single_matches': round(POSITIONS / NUMBER_SPACE, 4),
        'diversity': round(diversity, 4),
    }


def derive_position_recommendations(combinations: Sequence[Sequence[int]],
                                    fused_probs: List[Dict[int, float]],
                                    per_position: int = 5) -> Dict[str, List[int]]:
    """
    从选号集合导出「每位推荐号码」（生产库 predicted_numbers 的扁平格式）。

    优先取集合中实际覆盖到的号码（按模型概率降序），不足时用概率最高的
    未覆盖号码补齐，保证每位恰好 per_position 个号码、且均为 0-9 整数。

    Returns:
        {'wan': [int × per_position], 'qian': [...], ...}
    """
    out: Dict[str, List[int]] = {}
    for pos in range(POSITIONS):
        covered = {int(c[pos]) for c in combinations if len(c) > pos}
        ranked_covered = sorted(covered, key=lambda n: -fused_probs[pos].get(n, 0.0))
        picks = ranked_covered[:per_position]

        if len(picks) < per_position:
            rest = sorted((n for n in range(NUMBER_SPACE) if n not in picks),
                          key=lambda n: -fused_probs[pos].get(n, 0.0))
            picks.extend(rest[:per_position - len(picks)])

        out[POSITION_KEYS[pos]] = [int(n) for n in picks[:per_position]]
    return out


# ============================================================
# 主入口
# ============================================================

def generate_combinations(fused_probs: List[Dict[int, float]],
                          k: int = 10,
                          strategy: str = DEFAULT_STRATEGY,
                          coverage_floor: Optional[int] = None,
                          position_top_n: int = 6,
                          anchor_count: int = 3,
                          constraints: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """
    选号策略主入口。

    Args:
        fused_probs: 融合概率，List[Dict[int, float]]，长度 5，每位和为 1
        k: 需要的注数
        strategy: STRATEGY_LABELS 中的任一键
        coverage_floor: 每位覆盖下限。None 时自动取 min(k, 10)，即尽可能全覆盖
        position_top_n: 概率类策略的每位候选数
        anchor_count: hybrid 策略保留的尖峰注数
        constraints: legacy_constrained 策略使用的形态约束参数

    Returns:
        {
          'strategy': str, 'strategy_label': str, 'objective': str,
          'combinations': List[Dict]  # 与 predictor.top_combinations 完全同构
          'metrics': Dict,            # evaluate_selection 的输出
          'position_recommendations': Dict[str, List[int]],
          'diagnostics': Dict
        }
    """
    if not fused_probs or len(fused_probs) < POSITIONS:
        return {'strategy': strategy, 'combinations': [], 'metrics': {},
                'position_recommendations': {}, 'diagnostics': {'error': '概率分布无效'}}

    k = max(1, int(k))
    if coverage_floor is None:
        coverage_floor = min(k, NUMBER_SPACE)

    strategy = strategy if strategy in STRATEGY_LABELS else DEFAULT_STRATEGY
    diagnostics: Dict[str, Any] = {}

    if strategy == 'max_probability':
        raw = _strategy_max_probability(fused_probs, k, position_top_n)
    elif strategy == 'latin_coverage':
        raw = _strategy_coverage(fused_probs, k, min(k, NUMBER_SPACE), uniform_weight=1.0)
    elif strategy == 'hybrid':
        raw = _strategy_hybrid(fused_probs, k, coverage_floor, anchor_count, position_top_n)
    elif strategy == 'legacy_constrained':
        raw, diagnostics = _strategy_legacy(fused_probs, k, position_top_n, constraints or {})
    else:
        strategy = 'weighted_coverage'
        raw = _strategy_coverage(fused_probs, k, coverage_floor, uniform_weight=0.0)

    if not raw:
        logger.warning('选号策略 %s 未产出组合，回退到纯概率贪心', strategy)
        raw = _strategy_max_probability(fused_probs, k, position_top_n)
        diagnostics['fallback'] = True

    # 统一封装为 predictor 兼容格式
    scored = []
    for combo in raw:
        prob = 1.0
        for pos, num in enumerate(combo):
            prob *= fused_probs[pos].get(num, 0.0)
        scored.append((combo, prob))

    max_prob = max((p for _, p in scored), default=0.0)
    combinations: List[Dict[str, Any]] = []
    for rank, (combo, prob) in enumerate(scored, 1):
        hezhi = sum(combo)
        span = max(combo) - min(combo)
        ssd = sum((n - 4.5) ** 2 for n in combo) / POSITIONS
        combinations.append({
            'rank': rank,
            'combination': ''.join(map(str, combo)),
            'numbers': list(combo),
            'probability': round(prob, 8),
            'confidence': round(100.0 * prob / max_prob, 2) if max_prob > 0 else 0.0,
            'hezhi': hezhi,
            'span': span,
            'ssd': round(ssd, 4),
            'strategy': strategy,
        })

    metrics = evaluate_selection(raw, fused_probs)
    diagnostics['coverage_floor'] = coverage_floor
    diagnostics['requested_k'] = k
    diagnostics['produced_k'] = len(combinations)

    return {
        'strategy': strategy,
        'strategy_label': STRATEGY_LABELS.get(strategy, strategy),
        'objective': STRATEGY_OBJECTIVES.get(strategy, ''),
        'combinations': combinations,
        'metrics': metrics,
        'position_recommendations': derive_position_recommendations(raw, fused_probs),
        'diagnostics': diagnostics,
    }


def compare_strategies(fused_probs: List[Dict[int, float]], k: int = 10,
                       position_top_n: int = 6) -> Dict[str, Any]:
    """
    在同一份概率分布上横向对比全部策略，输出可直接渲染的对照表。

    这是让用户"眼见为实"的关键功能：同样 K 注，不同构造方式在
    位覆盖期望上的差距可达 4 倍以上，而精确全中概率完全一致
    （因为它只取决于注数）。
    """
    rows: List[Dict[str, Any]] = []
    for key in ('weighted_coverage', 'latin_coverage', 'hybrid',
                'max_probability', 'legacy_constrained'):
        res = generate_combinations(fused_probs, k=k, strategy=key,
                                    position_top_n=position_top_n)
        m = res.get('metrics', {})
        rows.append({
            'strategy': key,
            'label': STRATEGY_LABELS.get(key, key),
            'objective': STRATEGY_OBJECTIVES.get(key, ''),
            'position_coverage': m.get('position_coverage', []),
            'expected_covered_positions': m.get('expected_covered_positions', 0.0),
            'model_expected_covered_positions': m.get('model_expected_covered_positions', 0.0),
            'exact_hit_probability_fair': m.get('exact_hit_probability_fair', 0.0),
            'diversity': m.get('diversity', 0.0),
            'top_combination': res['combinations'][0]['combination'] if res['combinations'] else '',
        })

    best = max(rows, key=lambda r: r['expected_covered_positions']) if rows else None
    return {
        'k': k,
        'rows': rows,
        'best_by_coverage': best['strategy'] if best else None,
        'note': ('精确全中概率对所有策略完全相同（= K/100000），'
                 '差异仅体现在位覆盖命中期望上。'),
    }


def format_strategy_comparison(comparison: Dict[str, Any]) -> str:
    """把 `compare_strategies` 的结果渲染为文本表格。"""
    rows = comparison.get('rows', [])
    if not rows:
        return '无策略对比数据。'

    lines: List[str] = []
    lines.append('=' * 74)
    lines.append(f"选号策略对照（注数 K = {comparison.get('k')}）")
    lines.append('=' * 74)
    lines.append(f"{'策略':<20}{'各位覆盖号码数':<20}{'期望覆盖位数':>12}{'多样性':>10}")
    lines.append('-' * 74)

    for r in rows:
        cov = '/'.join(str(c) for c in r['position_coverage'])
        mark = ' ' if r['strategy'] == comparison.get('best_by_coverage') else ' '
        lines.append(f"{r['label']:<20}{cov:<20}"
                     f"{r['expected_covered_positions']:>11.3f}"
                     f"{r['diversity']:>10.3f}{mark}")

    lines.append('-' * 74)
    fair = rows[0].get('exact_hit_probability_fair', 0.0)
    lines.append(f"精确全中概率（所有策略相同）: {fair:.8f}  = K/100000")
    lines.append(comparison.get('note', ''))
    lines.append('=' * 74)
    return '\n'.join(lines)
