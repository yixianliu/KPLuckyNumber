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
    'dynamic_slot': '动态槽位（遗漏加权）',
}

#: 策略标识 → 优化目标说明
STRATEGY_OBJECTIVES: Dict[str, str] = {
    'weighted_coverage': '兼顾位覆盖与概率倾斜，综合期望最优',
    'latin_coverage': '最大化位覆盖命中数 E[M]',
    'max_probability': '最大化单注精确全中概率',
    'hybrid': '保留最高概率组合，同时补足位覆盖',
    'legacy_constrained': '复现 v3.x 原有行为，仅供回归对照',
    'dynamic_slot': '基于近期遗漏值动态调整各位置候选槽位权重',
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


def _get_miss_adjusted_slots(miss_values: Optional[Sequence[Dict[int, int]]],
                             decay_lambda: float = 0.1
                             ) -> Optional[Dict[int, Dict[int, float]]]:
    """
    将原始遗漏期数数据转换为各位置、各号码的调整系数。

    调整逻辑：
        遗漏值越高 → 该号码"越冷"→ 给予轻度提升系数（>1.0），
        体现"冷号回补"倾向（注意：公平摇号下冷号并不会更可能开出，
        此调整仅为历史归纳的经验性参考，不影响真实随机性）。

    调整系数公式：
        factor = 1.0 + decay_lambda * min(miss, cap)
        cap = 20（防止极端遗漏导致系数过大）

    Args:
        miss_values: 可选，List[Dict[int, int]]，长度 5，
                     每位为 {号码(0-9): 遗漏期数}
        decay_lambda: 衰减系数（越小调整越温和，默认 0.1）

    Returns:
        调整系数字典 {position_index: {number: factor}}，
        若 miss_values 为 None 或空则返回 None
    """
    if not miss_values or len(miss_values) < POSITIONS:
        return None

    adjustments: Dict[int, Dict[int, float]] = {}
    for pos in range(POSITIONS):
        pos_miss = miss_values[pos] if pos < len(miss_values) else {}
        adjustments[pos] = {}
        for num in range(NUMBER_SPACE):
            miss = int(pos_miss.get(num, 0))
            capped = min(miss, 20)  # 封顶，防止极端值
            adjustments[pos][num] = 1.0 + decay_lambda * capped
    return adjustments


def _strategy_dynamic_slot(fused_probs: List[Dict[int, float]], k: int,
                          miss_adjustments: Optional[Dict[int, Dict[int, float]]] = None,
                          position_top_n: int = 6,
                          ) -> List[Tuple[int, ...]]:
    """
    动态槽位策略：基于遗漏值调整各位置候选槽位权重。
    
    如果提供了 miss_adjustments，则对各位置的分布进行调整，
    以体现对近期遗漏号码的偏好或惩罚。调整后调用覆盖策略生成组合。
    
    Args:
        fused_probs: 原始融合概率
        k: 注数
        miss_adjustments: {position_index: {number: adjustment_factor}}
        position_top_n: 备用
    
    Returns:
        组合列表
    """
    if not miss_adjustments:
        # 如果没有提供遗漏调整数据，回退到标准的加权覆盖策略
        return _strategy_coverage(fused_probs, k, min(k, NUMBER_SPACE), uniform_weight=0.2)
    
    adjusted_probs = []
    for pos in range(POSITIONS):
        base_probs = dict(fused_probs[pos])
        adjustments = miss_adjustments.get(pos, {})
        new_probs = {}
        for num in range(NUMBER_SPACE):
            factor = adjustments.get(num, 1.0)
            p = base_probs.get(num, 0.0) * factor
            new_probs[num] = p
        
        total = sum(new_probs.values())
        if total > 0:
            new_probs = {num: p / total for num, p in new_probs.items()}
        else:
            new_probs = {num: 1.0 / NUMBER_SPACE for num in range(NUMBER_SPACE)}
        
        adjusted_probs.append(new_probs)
    
    return _strategy_coverage(adjusted_probs, k, min(k, NUMBER_SPACE), uniform_weight=0.1)


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


def _jaccard_similarity(a: Sequence[int], b: Sequence[int]) -> float:
    """
    计算两个组合的 Jaccard 相似度。

    Jaccard(A, B) = |A ∩ B| / |A ∪ B|

    在 5 位组合场景下，数值越大代表两注越同质。
    公平摇号下任意两注的期望 Jaccard ≈ 0.1（1/10）。
    """
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _enforce_diversity(combinations: List[Tuple[int, ...]],
                       max_jaccard: float = 0.3,
                       k: int = 10,
                       ) -> List[Tuple[int, ...]]:
    """
    多样性约束（1.8）：过滤 Jaccard 相似度 ≥ max_jaccard 的高同质组合。

    贪心保留：按输入顺序逐个判断，若与已保留集合中任一组合
    的 Jaccard 相似度超过阈值则剔除；不足 k 注时保留原集合（不过滤）。

    Args:
        combinations: 候选组合列表
        max_jaccard: Jaccard 阈值（>= 该值视为高度同质），默认 0.3
        k: 目标注数

    Returns:
        过滤后的组合列表（可能少于 k 注）
    """
    if not combinations:
        return []

    kept: List[Tuple[int, ...]] = []
    for combo in combinations:
        if len(kept) >= k:
            break
        # 检查与已保留组合的 Jaccard 相似度
        too_similar = False
        for prev in kept:
            if _jaccard_similarity(combo, prev) >= max_jaccard:
                too_similar = True
                break
        if not too_similar:
            kept.append(combo)

    # 过滤后不足 k 注时，把原始顺序中未被保留的组合补回来
    if len(kept) < k:
        kept_set = set(map(tuple, kept))
        for combo in combinations:
            if len(kept) >= k:
                break
            if tuple(combo) not in kept_set:
                kept.append(combo)
                kept_set.add(tuple(combo))

    return kept[:k]


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
                          constraints: Optional[Dict[str, Any]] = None,
                          miss_values: Optional[Sequence[Dict[int, int]]] = None,
                          max_jaccard: Optional[float] = None,
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
        miss_values: 可选，List[Dict[int,int]]，每位遗漏期数，供 dynamic_slot 策略使用
        max_jaccard: 可选，多样性阈值（0-1）。非 None 时启用 1.8 多样性约束，
                     过滤 Jaccard ≥ 该阈值的组合；默认 None 表示不启用。

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
    elif strategy == 'dynamic_slot':
        miss_adj = _get_miss_adjusted_slots(miss_values)
        if miss_adj is None and miss_values is None:
            # 无遗漏数据时给出 diagnostics 提示
            diagnostics['dynamic_slot_note'] = '未提供 miss_values，已使用默认回退'
        raw = _strategy_dynamic_slot(fused_probs, k, miss_adj, position_top_n)
    else:
        strategy = 'weighted_coverage'
        raw = _strategy_coverage(fused_probs, k, coverage_floor, uniform_weight=0.0)

    if not raw:
        logger.warning('选号策略 %s 未产出组合，回退到纯概率贪心', strategy)
        raw = _strategy_max_probability(fused_probs, k, position_top_n)
        diagnostics['fallback'] = True

    # 1.8 多样性约束：过滤 Jaccard 相似度超过阈值的组合
    if max_jaccard is not None:
        before_count = len(raw)
        raw = _enforce_diversity(raw, max_jaccard=max_jaccard, k=k)
        if len(raw) < before_count:
            diagnostics['diversity_filtered'] = before_count - len(raw)

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
                'max_probability', 'legacy_constrained', 'dynamic_slot'):
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


# ============================================================
# v3.70 (roadmap 1.9) 策略 A/B walk-forward 命中率对比框架
# ============================================================
# 设计意图：
#   排列5 为公平摇号，策略 A/B 的"命中率"在精确全中口径下永远等价
#   （= K/100000），差异只体现在"位覆盖命中"口径上。本框架通过
#   walk-forward（滚动窗口）方式，在历史真实开奖数据上回放两个策略，
#   统计各自"至少一注命中第 pos 位"的频率，作为可复现的离线对比指标。
#
#   结果通过 persist_selection_ab_result 持久化到新表 p5_selection_ab_test，
#   供 GUI 端"策略对照"面板与回测报告消费。
#
# 诚实边界：
#   公平摇号下，任何策略的精确全中命中率 ≈ 随机基线；本框架仅用于
#   "位覆盖命中"口径的离线对比，不可作为"提升中奖概率"的依据。


def run_strategy_ab_walk_forward(history: List[Dict],
                                  strategy_a: str = 'weighted_coverage',
                                  strategy_b: str = 'latin_coverage',
                                  warmup: int = 60,
                                  top_k: int = 3,
                                  prob_window: int = 30) -> Dict[str, Any]:
    """v3.70 (1.9): 在历史真实开奖上做 walk-forward 对比两个策略的位覆盖命中。

    流程（严格无前视泄漏）：
        1. 按 issue 正序排列 history，取前 warmup 期作为"历史特征基线"。
        2. 对 i ∈ [warmup, len-1]：用前 prob_window 期开奖数字做等频频率
           概率（最朴素的无偏基准，让对比不受特征工程污染），调用
           generate_combinations 生成策略 A 与 B 的 top_k 注。
        3. 用实际第 i 期的开奖数字作为 ground truth，统计
           "K 注中至少 1 注命中第 pos 位"的频率（位覆盖命中口径）。
        4. 对每个策略累计 5 位的命中次数，除以总期数得到命中率。
        5. 同时统计 Top-1 命中口径（首注是否命中该位），用于精确对照。

    Args:
        history: [{'issue': str, 'numbers': [int]*5}, ...] 按 issue 正序排列
        strategy_a / strategy_b: 要对比的两个策略标识
        warmup: 特征基线所需最少历史期数（默认 60）
        top_k: 每个策略生成的注数（默认 3，用于统计 K 注覆盖命中）
        prob_window: 频率概率窗口（默认 30 期）

    Returns:
        {
          'strategy_a': str, 'strategy_b': str,
          'issue_range': str,  # 形如 '20260001-20260050'
          'total_periods': int,
          'top1_a': float, 'top1_b': float,  # Top-1 命中率（0-1）
          'top3_a': float, 'top3_b': float,  # K 注覆盖命中率（0-1）
          'positions_a': [float]*5, 'positions_b': [float]*5,
          'note': str,  # 诚实边界说明
        }
    """
    if not history or len(history) <= warmup + 1:
        return {
            'strategy_a': strategy_a, 'strategy_b': strategy_b,
            'issue_range': '', 'total_periods': 0,
            'top1_a': 0.0, 'top1_b': 0.0, 'top3_a': 0.0, 'top3_b': 0.0,
            'positions_a': [0.0] * 5, 'positions_b': [0.0] * 5,
            'note': '历史数据不足，无法做 walk-forward 对比',
        }

    # 构造等频概率（前 prob_window 期的频率）
    def _freq_probs(start_idx: int) -> List[Dict[int, float]]:
        s = max(0, start_idx - prob_window + 1)
        window = history[s:start_idx + 1]
        fused = []
        for pos in range(POSITIONS):
            counts = [0] * NUMBER_SPACE
            for row in window:
                nums = row.get('numbers', [])
                if pos < len(nums):
                    counts[int(nums[pos])] += 1
            total = sum(counts) or 1
            fused.append({d: counts[d] / total for d in range(NUMBER_SPACE)})
        return fused

    def _covered_positions(combos: List[str],
                            actual: Tuple[int, ...]) -> List[int]:
        """返回 K 注"位覆盖命中"指示向量（第 p 位是否被至少 1 注命中）。

        注意：generate_combinations 输出的 combination 是字符串（如 '01525'），
        需逐位转 int 再与 actual（int 元组）比较，否则比较恒为 False。
        """
        per_pos_hit: List[bool] = [False] * POSITIONS
        for combo in combos:
            for i in range(POSITIONS):
                if i < len(combo) and i < len(actual) \
                        and int(combo[i]) == actual[i]:
                    per_pos_hit[i] = True
        return [1 if h else 0 for h in per_pos_hit]

    total_periods = 0
    pos_first_a = [0] * POSITIONS
    pos_first_b = [0] * POSITIONS
    pos_covered_a = [0] * POSITIONS
    pos_covered_b = [0] * POSITIONS

    last_issue = ''
    first_issue = ''
    for i in range(warmup, len(history)):
        row = history[i]
        nums = tuple(int(x) for x in row.get('numbers', [])[:POSITIONS])
        if len(nums) < POSITIONS:
            continue
        fused = _freq_probs(i)
        res_a = generate_combinations(fused, k=top_k, strategy=strategy_a)
        res_b = generate_combinations(fused, k=top_k, strategy=strategy_b)
        combos_a = [c['combination'] for c in res_a.get('combinations', [])][:top_k]
        combos_b = [c['combination'] for c in res_b.get('combinations', [])][:top_k]

        cov_a = _covered_positions(combos_a, nums)
        cov_b = _covered_positions(combos_b, nums)
        first_a = _covered_positions([combos_a[0]], nums) if combos_a else [0] * POSITIONS
        first_b = _covered_positions([combos_b[0]], nums) if combos_b else [0] * POSITIONS
        for p in range(POSITIONS):
            pos_first_a[p] += first_a[p]
            pos_first_b[p] += first_b[p]
            pos_covered_a[p] += cov_a[p]
            pos_covered_b[p] += cov_b[p]

        total_periods += 1
        last_issue = str(row.get('issue', ''))
        if total_periods == 1:
            first_issue = str(row.get('issue', ''))

    # 位覆盖命中率（每位命中次数 / 总期数），取值恒在 [0,1]
    def _pos_rates(cov_list: List[int], total: int) -> List[float]:
        return [round(c / total, 4) if total > 0 else 0.0 for c in cov_list]

    return {
        'strategy_a': strategy_a,
        'strategy_b': strategy_b,
        'issue_range': f"{first_issue}-{last_issue}" if total_periods > 0 else '',
        'total_periods': total_periods,
        # Top-1 口径：首注命中第 p 位的频率（5 位平均），取值 [0,1]
        'top1_a': round(sum(_pos_rates(pos_first_a, total_periods)) / POSITIONS, 4),
        'top1_b': round(sum(_pos_rates(pos_first_b, total_periods)) / POSITIONS, 4),
        # K 注覆盖口径：K 注中至少 1 注命中第 p 位的频率（5 位平均），取值 [0,1]
        'top3_a': round(sum(_pos_rates(pos_covered_a, total_periods)) / POSITIONS, 4),
        'top3_b': round(sum(_pos_rates(pos_covered_b, total_periods)) / POSITIONS, 4),
        # 每位位覆盖命中率（K 注口径），取值 [0,1]
        'positions_a': _pos_rates(pos_covered_a, total_periods),
        'positions_b': _pos_rates(pos_covered_b, total_periods),
        'note': ('公平摇号下精确全中命中率恒为 K/100000，与策略无关；'
                 '本框架的 top1_* 为"首注命中"口径（首注命中第 p 位的频率），'
                 'top3_* 与 positions_* 为 K 注"位覆盖命中"口径'
                 '（K 注中至少 1 注命中第 p 位的频率）。'
                 '两者差异反映选号组合的"位覆盖"能力，而非"精确全中"预测力。'),
    }


def persist_selection_ab_result(result: Dict[str, Any],
                                  db=None) -> Optional[int]:
    """v3.70 (1.9): 将 walk-forward A/B 结果写入 p5_selection_ab_test。

    Args:
        result: run_strategy_ab_walk_forward 的返回值
        db: 数据库连接（P5Database 实例或 None）。
            db 为 None 时自动尝试连接；连接失败静默降级（降级保底原则），
            仅记录 warning，不抛异常。

    Returns:
        新记录 ab_id（int），失败返回 None（不影响主流程）。
    """
    try:
        if db is None:
            db = _connect_db_for_ab()
            if db is None:
                return None
        ab_id = db.insert_selection_ab_test(
            strategy_a=result.get('strategy_a', ''),
            strategy_b=result.get('strategy_b', ''),
            issue_range=result.get('issue_range', ''),
            top1_a=result.get('top1_a', 0.0),
            top1_b=result.get('top1_b', 0.0),
            top3_a=result.get('top3_a', 0.0),
            top3_b=result.get('top3_b', 0.0),
        )
        return ab_id
    except Exception as e:
        logger.warning('persist_selection_ab_result 失败（降级保底，不影响主流程）: %s', e)
        return None


def _connect_db_for_ab():
    """延迟连接数据库（避免模块 import 时强制依赖 DB）。失败返回 None。"""
    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from modules.database import P5Database
        db = P5Database()
        if db and db.connect():
            return db
        return None
    except Exception as e:
        logger.warning('策略A/B 数据库连接失败: %s', e)
        return None
