import logging
import os
import json
import math
from collections import defaultdict, Counter
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any

# 确保日志目录存在
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/analyzer.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ProbabilityAnalyzer:
    """
    七星彩概率分析器（专业版）

    基于统计学原理对七星彩历史数据进行多维度分析。
    重要声明：彩票开奖是独立随机事件，本分析仅提供历史数据统计描述，
    不构成任何投注建议，所有号码的理论中奖概率均等。
    """

    def __init__(self):
        self.positions = 7
        self.main_range = list(range(0, 10))      # 前6位 0-9
        self.special_range = list(range(0, 15))   # 特别号 0-14
        self.position_names = ['第一位', '第二位', '第三位', '第四位',
                               '第五位', '第六位', '特别号']

    # ==================== 基础频率分析 ====================

    def analyze_frequency(self, data: List[Dict]) -> Dict:
        """
        分析各号码在每个位置的出现频率与理论期望对比

        Returns:
            包含观测频率、理论概率、偏离度的字典
        """
        total = len(data)
        if total == 0:
            return {}

        # 初始化计数器: freq[pos][num] = count
        freq = {pos: Counter() for pos in range(self.positions)}

        for item in data:
            numbers = item.get('numbers', [])
            for pos in range(min(self.positions, len(numbers))):
                try:
                    num = int(numbers[pos])
                    freq[pos][num] += 1
                except (ValueError, TypeError):
                    continue

        result = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            num_range = self.special_range if pos == 6 else self.main_range
            theory_prob = 1.0 / len(num_range)

            pos_stats = {}
            for num in num_range:
                observed_count = freq[pos].get(num, 0)
                observed_prob = observed_count / total if total > 0 else 0
                # 计算偏离度 (Observed - Expected) / Expected
                deviation = (observed_prob - theory_prob) / theory_prob if theory_prob > 0 else 0

                pos_stats[num] = {
                    'frequency': observed_count,
                    'observed_probability': round(observed_prob, 6),
                    'theoretical_probability': round(theory_prob, 6),
                    'deviation_rate': round(deviation, 4),  # 偏离率
                    'expected_count': round(total * theory_prob, 2)
                }

            result[pos] = {
                'position_name': pos_name,
                'total_samples': total,
                'number_stats': pos_stats,
                'most_frequent': freq[pos].most_common(3) if freq[pos] else [],
                'least_frequent': freq[pos].most_common()[-3:] if len(freq[pos]) >= 3 else []
            }

        return result

    # ==================== 遗漏值分析（核心指标）====================

    def analyze_omission(self, data: List[Dict]) -> Dict:
        """
        分析号码遗漏值（自上次出现以来的期数）

        遗漏值是彩票分析的核心指标，反映号码的"冷热度"。
        当前遗漏越大，理论上该号码在近期出现的概率（从独立事件角度仍均等）。

        Returns:
            各位置各号码的当前遗漏、最大遗漏、平均遗漏
        """
        if not data:
            return {}

        # 按期号排序（升序，从旧到新）
        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        total_periods = len(sorted_data)

        result = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            num_range = self.special_range if pos == 6 else self.main_range

            # 记录每个号码每次出现的索引
            occurrence_indices = {num: [] for num in num_range}

            for idx, item in enumerate(sorted_data):
                numbers = item.get('numbers', [])
                if pos < len(numbers):
                    try:
                        num = int(numbers[pos])
                        if num in occurrence_indices:
                            occurrence_indices[num].append(idx)
                    except (ValueError, TypeError):
                        continue

            num_stats = {}
            for num in num_range:
                indices = occurrence_indices[num]
                current_omission = total_periods - 1 - indices[-1] if indices else total_periods

                # 计算遗漏间隔列表
                gaps = []
                for i in range(1, len(indices)):
                    gaps.append(indices[i] - indices[i - 1] - 1)

                max_omission = max(gaps) if gaps else current_omission
                avg_omission = sum(gaps) / len(gaps) if gaps else current_omission

                # 遗漏偏差率 = 当前遗漏 / 平均遗漏
                omission_ratio = current_omission / avg_omission if avg_omission > 0 else 0

                num_stats[num] = {
                    'current_omission': current_omission,
                    'max_omission': max_omission,
                    'avg_omission': round(avg_omission, 2),
                    'omission_ratio': round(omission_ratio, 4),
                    'total_occurrences': len(indices),
                    'occurrence_rate': round(len(indices) / total_periods, 4) if total_periods > 0 else 0
                }

            result[pos] = {
                'position_name': pos_name,
                'total_periods': total_periods,
                'number_stats': num_stats
            }

        return result

    # ==================== 冷热号分级分析 ====================

    def analyze_hot_cold(self, data: List[Dict], recent_n: int = 30) -> Dict:
        """
        基于遗漏值和近期频率进行冷热号分级

        分级标准：
        - 热号：当前遗漏 <= 平均遗漏的50%，或近期出现频率高于理论值
        - 温号：当前遗漏在平均遗漏的50%-150%之间
        - 冷号：当前遗漏 > 平均遗漏的150%，或长期未出现

        Args:
            data: 历史数据
            recent_n: 近期统计期数

        Returns:
            各位置冷热号分级结果
        """
        omission_data = self.analyze_omission(data)
        if not omission_data:
            return {}

        # 计算近期频率
        recent_data = sorted(data, key=lambda x: x.get('issue', ''))[-recent_n:] if len(data) >= recent_n else data
        recent_freq = {pos: Counter() for pos in range(self.positions)}

        for item in recent_data:
            numbers = item.get('numbers', [])
            for pos in range(min(self.positions, len(numbers))):
                try:
                    num = int(numbers[pos])
                    recent_freq[pos][num] += 1
                except (ValueError, TypeError):
                    continue

        result = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            num_range = self.special_range if pos == 6 else self.main_range
            theory_prob = 1.0 / len(num_range)
            theory_recent_count = recent_n * theory_prob

            hot_numbers = []
            warm_numbers = []
            cold_numbers = []

            for num in num_range:
                om_stats = omission_data[pos]['number_stats'].get(num, {})
                current_omission = om_stats.get('current_omission', 0)
                avg_omission = om_stats.get('avg_omission', 1)
                recent_count = recent_freq[pos].get(num, 0)

                # 遗漏比率
                omission_ratio = current_omission / avg_omission if avg_omission > 0 else 0
                # 近期频率比率
                freq_ratio = recent_count / theory_recent_count if theory_recent_count > 0 else 0

                # 综合评分 (越高越热)
                heat_score = freq_ratio * 0.6 + (1 / (1 + omission_ratio)) * 0.4

                if heat_score >= 1.2 or omission_ratio <= 0.5:
                    category = 'hot'
                    hot_numbers.append({
                        'number': num,
                        'heat_score': round(heat_score, 4),
                        'current_omission': current_omission,
                        'recent_count': recent_count
                    })
                elif heat_score <= 0.6 or omission_ratio >= 1.5:
                    category = 'cold'
                    cold_numbers.append({
                        'number': num,
                        'heat_score': round(heat_score, 4),
                        'current_omission': current_omission,
                        'recent_count': recent_count
                    })
                else:
                    category = 'warm'
                    warm_numbers.append({
                        'number': num,
                        'heat_score': round(heat_score, 4),
                        'current_omission': current_omission,
                        'recent_count': recent_count
                    })

            # 按热度排序
            hot_numbers.sort(key=lambda x: x['heat_score'], reverse=True)
            cold_numbers.sort(key=lambda x: x['heat_score'])
            warm_numbers.sort(key=lambda x: x['heat_score'], reverse=True)

            result[pos] = {
                'position_name': pos_name,
                'hot_numbers': hot_numbers,
                'warm_numbers': warm_numbers,
                'cold_numbers': cold_numbers,
                'theory_recent_count': round(theory_recent_count, 2)
            }

        return result

    # ==================== 012路分析 ====================

    def analyze_012_path(self, data: List[Dict]) -> Dict:
        """
        分析012路分布（除3余数分析）

        012路是彩票分析的标准方法：
        - 0路：号码 % 3 == 0 (0, 3, 6, 9)
        - 1路：号码 % 3 == 1 (1, 4, 7)
        - 2路：号码 % 3 == 2 (2, 5, 8)

        Returns:
            各位置012路分布统计
        """
        if not data:
            return {}

        total = len(data)
        result = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            path_counts = {0: Counter(), 1: Counter(), 2: Counter()}

            for item in data:
                numbers = item.get('numbers', [])
                if pos < len(numbers):
                    try:
                        num = int(numbers[pos])
                        path = num % 3
                        path_counts[path][num] += 1
                    except (ValueError, TypeError):
                        continue

            path_stats = {}
            for path in [0, 1, 2]:
                count = sum(path_counts[path].values())
                path_stats[path] = {
                    'count': count,
                    'probability': round(count / total, 4) if total > 0 else 0,
                    'numbers': sorted(list(path_counts[path].keys())),
                    'number_freq': dict(path_counts[path])
                }

            result[pos] = {
                'position_name': pos_name,
                'path_stats': path_stats,
                'total_samples': total
            }

        return result

    # ==================== 大小比分析 ====================

    def analyze_big_small(self, data: List[Dict]) -> Dict:
        """
        分析大小号分布

        七星彩标准：
        - 前6位：小号 0-4，大号 5-9
        - 特别号：小号 0-7，大号 8-14（或按 0-6/7-14）

        Returns:
            大小号分布统计
        """
        if not data:
            return {}

        total = len(data)
        result = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            # 特别号的大小分界
            big_small_threshold = 7 if pos == 6 else 5

            big_count = 0
            small_count = 0
            big_numbers = Counter()
            small_numbers = Counter()

            for item in data:
                numbers = item.get('numbers', [])
                if pos < len(numbers):
                    try:
                        num = int(numbers[pos])
                        if num >= big_small_threshold:
                            big_count += 1
                            big_numbers[num] += 1
                        else:
                            small_count += 1
                            small_numbers[num] += 1
                    except (ValueError, TypeError):
                        continue

            result[pos] = {
                'position_name': pos_name,
                'big_small_threshold': big_small_threshold,
                'big_count': big_count,
                'small_count': small_count,
                'big_probability': round(big_count / total, 4) if total > 0 else 0,
                'small_probability': round(small_count / total, 4) if total > 0 else 0,
                'big_numbers': dict(big_numbers.most_common()),
                'small_numbers': dict(small_numbers.most_common()),
                'total_samples': total
            }

        return result

    # ==================== 奇偶比分析（增强版）====================

    def analyze_odd_even(self, data: List[Dict]) -> Dict:
        """
        分析奇偶分布（增强版）

        Returns:
            各位置奇偶分布及组合模式
        """
        if not data:
            return {}

        total = len(data)
        result = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            odd_count = 0
            even_count = 0
            odd_numbers = Counter()
            even_numbers = Counter()

            for item in data:
                numbers = item.get('numbers', [])
                if pos < len(numbers):
                    try:
                        num = int(numbers[pos])
                        if num % 2 == 1:
                            odd_count += 1
                            odd_numbers[num] += 1
                        else:
                            even_count += 1
                            even_numbers[num] += 1
                    except (ValueError, TypeError):
                        continue

            result[pos] = {
                'position_name': pos_name,
                'odd_count': odd_count,
                'even_count': even_count,
                'odd_probability': round(odd_count / total, 4) if total > 0 else 0,
                'even_probability': round(even_count / total, 4) if total > 0 else 0,
                'odd_numbers': dict(odd_numbers.most_common()),
                'even_numbers': dict(even_numbers.most_common()),
                'total_samples': total
            }

        # 整体奇偶比分析（前6位）
        overall_patterns = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 6:
                try:
                    pattern = ''.join(['O' if int(n) % 2 == 1 else 'E' for n in numbers[:6]])
                    overall_patterns[pattern] += 1
                except (ValueError, TypeError):
                    continue

        result['overall'] = {
            'pattern_distribution': dict(overall_patterns.most_common(10)),
            'total_samples': total
        }

        return result

    # ==================== 和值分析（增强版）====================

    def analyze_hezhi(self, data: List[Dict]) -> Dict:
        """
        分析和值分布（增强版）

        七星彩前6位和值范围：0-54
        特别号单独分析

        Returns:
            和值统计、区间分布、理论对比
        """
        if not data:
            return {}

        hezhi_values = []
        hezhi_with_special = []

        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 6:
                try:
                    main_sum = sum(int(n) for n in numbers[:6])
                    hezhi_values.append(main_sum)
                    if len(numbers) >= 7:
                        total_sum = main_sum + int(numbers[6])
                        hezhi_with_special.append(total_sum)
                except (ValueError, TypeError):
                    continue

        if not hezhi_values:
            return {}

        total = len(hezhi_values)
        avg_hezhi = sum(hezhi_values) / total
        max_hezhi = max(hezhi_values)
        min_hezhi = min(hezhi_values)

        # 和值区间分布
        ranges = {
            '0-9': 0, '10-19': 0, '20-29': 0, '30-39': 0,
            '40-49': 0, '50-54': 0
        }
        for h in hezhi_values:
            if h <= 9:
                ranges['0-9'] += 1
            elif h <= 19:
                ranges['10-19'] += 1
            elif h <= 29:
                ranges['20-29'] += 1
            elif h <= 39:
                ranges['30-39'] += 1
            elif h <= 49:
                ranges['40-49'] += 1
            else:
                ranges['50-54'] += 1

        # 理论期望值（前6位，每个位置期望4.5）
        theory_avg = 27.0

        return {
            'total_samples': total,
            'avg_hezhi': round(avg_hezhi, 2),
            'max_hezhi': max_hezhi,
            'min_hezhi': min_hezhi,
            'theory_avg': theory_avg,
            'deviation_from_theory': round(avg_hezhi - theory_avg, 2),
            'range_distribution': {k: {'count': v, 'probability': round(v / total, 4)} for k, v in ranges.items()},
            'hezhi_values': hezhi_values  # 原始数据供进一步分析
        }

    # ==================== 跨度分析（增强版）====================

    def analyze_span(self, data: List[Dict]) -> Dict:
        """
        分析跨度分布（最大值 - 最小值）

        Returns:
            跨度统计
        """
        if not data:
            return {}

        spans = []
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 6:
                try:
                    main_numbers = [int(n) for n in numbers[:6]]
                    span = max(main_numbers) - min(main_numbers)
                    spans.append(span)
                except (ValueError, TypeError):
                    continue

        if not spans:
            return {}

        total = len(spans)
        span_counts = Counter(spans)

        return {
            'total_samples': total,
            'avg_span': round(sum(spans) / total, 2),
            'max_span': max(spans),
            'min_span': min(spans),
            'span_distribution': {str(k): {'count': v, 'probability': round(v / total, 4)}
                                  for k, v in sorted(span_counts.items())}
        }

    # ==================== 重号分析 ====================

    def analyze_repeats(self, data: List[Dict]) -> Dict:
        """
        分析相邻期重号情况

        Returns:
            重号统计
        """
        if len(data) < 2:
            return {}

        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        repeat_counts = Counter()
        consecutive_repeats = []

        for i in range(1, len(sorted_data)):
            prev_numbers = set(int(n) for n in sorted_data[i - 1].get('numbers', []) if str(n).isdigit())
            curr_numbers = set(int(n) for n in sorted_data[i].get('numbers', []) if str(n).isdigit())
            repeats = len(prev_numbers & curr_numbers)
            repeat_counts[repeats] += 1
            consecutive_repeats.append(repeats)

        total_pairs = len(sorted_data) - 1

        return {
            'total_pairs': total_pairs,
            'repeat_distribution': {str(k): {'count': v, 'probability': round(v / total_pairs, 4)}
                                    for k, v in sorted(repeat_counts.items())},
            'avg_repeats': round(sum(consecutive_repeats) / len(consecutive_repeats), 2) if consecutive_repeats else 0,
            'max_repeats': max(consecutive_repeats) if consecutive_repeats else 0,
            'min_repeats': min(consecutive_repeats) if consecutive_repeats else 0
        }

    # ==================== 连号分析 ====================

    def analyze_consecutive(self, data: List[Dict]) -> Dict:
        """
        分析连号情况

        Returns:
            连号统计
        """
        if not data:
            return {}

        streak_counts = Counter()
        all_streaks = []

        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 6:
                try:
                    main_numbers = sorted([int(n) for n in numbers[:6]])
                    current_streak = 1
                    max_streak = 1

                    for i in range(1, 6):
                        if main_numbers[i] == main_numbers[i - 1] + 1:
                            current_streak += 1
                            max_streak = max(max_streak, current_streak)
                        else:
                            if current_streak > 1:
                                all_streaks.append(current_streak)
                            current_streak = 1

                    if current_streak > 1:
                        all_streaks.append(current_streak)

                    streak_counts[max_streak] += 1
                except (ValueError, TypeError):
                    continue

        total = len(data)

        return {
            'total_samples': total,
            'consecutive_distribution': {str(k): {'count': v, 'probability': round(v / total, 4)}
                                         for k, v in sorted(streak_counts.items())},
            'avg_max_streak': round(sum(streak_counts.keys()) / len(streak_counts), 2) if streak_counts else 0,
            'has_consecutive_probability': round(sum(v for k, v in streak_counts.items() if k > 1) / total, 4) if total > 0 else 0
        }

    # ==================== 号码相关性分析 ====================

    def analyze_position_correlation(self, data: List[Dict]) -> Dict:
        """
        分析不同位置之间的号码相关性

        Returns:
            位置间相关性矩阵
        """
        if not data:
            return {}

        # 提取各位置号码序列
        position_series = {pos: [] for pos in range(self.positions)}

        for item in data:
            numbers = item.get('numbers', [])
            for pos in range(min(self.positions, len(numbers))):
                try:
                    position_series[pos].append(int(numbers[pos]))
                except (ValueError, TypeError):
                    position_series[pos].append(None)

        # 计算皮尔逊相关系数
        import statistics

        def pearson_correlation(x, y):
            """计算皮尔逊相关系数"""
            # 过滤None值
            pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
            if len(pairs) < 2:
                return 0

            x_vals = [p[0] for p in pairs]
            y_vals = [p[1] for p in pairs]

            try:
                mean_x = statistics.mean(x_vals)
                mean_y = statistics.mean(y_vals)

                numerator = sum((a - mean_x) * (b - mean_y) for a, b in pairs)
                denom_x = sum((a - mean_x) ** 2 for a in x_vals) ** 0.5
                denom_y = sum((b - mean_y) ** 2 for b in y_vals) ** 0.5

                if denom_x == 0 or denom_y == 0:
                    return 0
                return numerator / (denom_x * denom_y)
            except:
                return 0

        correlation_matrix = {}
        for i in range(self.positions):
            correlation_matrix[i] = {}
            for j in range(self.positions):
                if i == j:
                    correlation_matrix[i][j] = 1.0
                else:
                    corr = pearson_correlation(position_series[i], position_series[j])
                    correlation_matrix[i][j] = round(corr, 4)

        return {
            'position_names': self.position_names,
            'correlation_matrix': correlation_matrix,
            'total_samples': len(data),
            'note': '相关系数接近0表示位置间基本独立，符合随机性假设'
        }

    # ==================== 随机性检验 ====================

    def analyze_randomness(self, data: List[Dict]) -> Dict:
        """
        对数据进行基础随机性检验

        Returns:
            随机性检验结果
        """
        if not data:
            return {}

        total = len(data)

        # 1. 频率均匀性检验（卡方检验近似）
        freq_result = self.analyze_frequency(data)
        chi_square_stats = {}

        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            num_range = self.special_range if pos == 6 else self.main_range
            theory_prob = 1.0 / len(num_range)
            expected = total * theory_prob

            chi_sq = 0
            for num in num_range:
                observed = freq_result[pos]['number_stats'][num]['frequency']
                if expected > 0:
                    chi_sq += (observed - expected) ** 2 / expected

            # 自由度 = 号码个数 - 1
            df = len(num_range) - 1

            chi_square_stats[pos] = {
                'position_name': pos_name,
                'chi_square': round(chi_sq, 4),
                'degrees_of_freedom': df,
                'expected_frequency': round(expected, 2),
                'interpretation': '数据分布基本均匀' if chi_sq < df * 2 else '存在一定程度的分布偏差'
            }

        # 2. 连号随机性
        consecutive_result = self.analyze_consecutive(data)
        has_consecutive_prob = consecutive_result.get('has_consecutive_probability', 0)

        # 理论上有连号的概率（近似）
        theory_consecutive_prob = 0.55  # 经验值

        return {
            'chi_square_test': chi_square_stats,
            'consecutive_analysis': {
                'observed_prob': has_consecutive_prob,
                'theory_approx': theory_consecutive_prob,
                'interpretation': '连号出现频率正常' if abs(has_consecutive_prob - theory_consecutive_prob) < 0.15 else '连号出现频率偏离预期'
            },
            'overall_assessment': '历史数据整体呈现随机分布特征，各位置号码基本独立',
            'disclaimer': '彩票开奖为独立随机事件，历史数据统计特征不代表未来趋势'
        }

    # ==================== 综合概率计算（新版）====================

    def calculate_probability(self, data: List[Dict], trend_data: Optional[List[Dict]] = None) -> Dict:
        """
        综合计算概率分析结果（专业版）

        重要说明：
        1. 七星彩每位号码的理论出现概率均等（前6位各1/10，特别号1/15）
        2. 本方法计算的是基于历史数据的统计特征，而非"预测概率"
        3. 所有"概率"数值均为历史频率的统计描述

        Args:
            data: 历史开奖数据列表
            trend_data: 走势图数据（已废弃，保留参数兼容）

        Returns:
            综合分析结果字典
        """
        if len(data) < 10:
            logger.warning('数据量不足，分析结果可能不准确')

        total = len(data)

        # 执行所有分析
        freq_result = self.analyze_frequency(data)
        omission_result = self.analyze_omission(data)
        hot_cold_result = self.analyze_hot_cold(data)
        path_result = self.analyze_012_path(data)
        big_small_result = self.analyze_big_small(data)
        odd_even_result = self.analyze_odd_even(data)
        hezhi_result = self.analyze_hezhi(data)
        span_result = self.analyze_span(data)
        repeat_result = self.analyze_repeats(data)
        consecutive_result = self.analyze_consecutive(data)
        correlation_result = self.analyze_position_correlation(data)
        randomness_result = self.analyze_randomness(data)

        # 构建位置级综合分析（按位置独立分析）
        position_analysis = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            num_range = self.special_range if pos == 6 else self.main_range
            theory_prob = 1.0 / len(num_range)

            number_analysis = {}
            for num in num_range:
                # 综合评分（多因子加权）
                freq_stats = freq_result.get(pos, {}).get('number_stats', {}).get(num, {})
                om_stats = omission_result.get(pos, {}).get('number_stats', {}).get(num, {})

                observed_prob = freq_stats.get('observed_probability', 0)
                deviation = freq_stats.get('deviation_rate', 0)
                current_omission = om_stats.get('current_omission', 0)
                avg_omission = om_stats.get('avg_omission', 1)
                omission_ratio = om_stats.get('omission_ratio', 0)

                # 综合热度评分 (0-100)
                # 频率偏离度权重40%，遗漏偏差权重35%，出现率权重25%
                heat_score = (
                    (1 + deviation) * 40 +           # 频率偏离（正偏离=热）
                    (1 / (1 + omission_ratio)) * 35 +  # 遗漏偏差（低遗漏=热）
                    (observed_prob / theory_prob) * 25  # 出现率比率
                )
                heat_score = max(0, min(100, heat_score))

                number_analysis[num] = {
                    'observed_probability': observed_prob,
                    'theoretical_probability': theory_prob,
                    'deviation_rate': deviation,
                    'current_omission': current_omission,
                    'avg_omission': avg_omission,
                    'omission_ratio': omission_ratio,
                    'heat_score': round(heat_score, 2),
                    'category': 'hot' if heat_score >= 60 else 'cold' if heat_score <= 40 else 'warm'
                }

            # 按热度排序
            sorted_numbers = sorted(number_analysis.items(), key=lambda x: x[1]['heat_score'], reverse=True)

            position_analysis[pos] = {
                'position_name': pos_name,
                'theory_prob': theory_prob,
                'number_analysis': number_analysis,
                'hot_numbers': [n for n, s in sorted_numbers if s['category'] == 'hot'],
                'warm_numbers': [n for n, s in sorted_numbers if s['category'] == 'warm'],
                'cold_numbers': [n for n, s in sorted_numbers if s['category'] == 'cold'],
                'sorted_by_heat': [(n, s['heat_score']) for n, s in sorted_numbers]
            }

        return {
            'frequency': freq_result,
            'omission': omission_result,
            'hot_cold': hot_cold_result,
            'path_012': path_result,
            'big_small': big_small_result,
            'odd_even': odd_even_result,
            'hezhi': hezhi_result,
            'span': span_result,
            'repeats': repeat_result,
            'consecutive': consecutive_result,
            'correlation': correlation_result,
            'randomness': randomness_result,
            'position_analysis': position_analysis,
            'total_samples': total,
            'analysis_time': datetime.now().isoformat(),
            'methodology_note': '本分析基于历史数据统计，所有号码的理论出现概率均等。'
        }

    # ==================== 报告生成（兼容旧接口）====================

    def generate_report(self, analysis_result: Dict) -> str:
        """
        生成综合分析报告（兼容旧接口）

        Args:
            analysis_result: 分析结果字典

        Returns:
            报告字符串
        """
        report = []
        report.append('=' * 80)
        report.append('        七星彩数字概率综合分析报告（专业版）')
        report.append('=' * 80)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 80)
        report.append('\n【重要声明】')
        report.append('  彩票开奖为独立随机事件，每位号码的理论出现概率均等。')
        report.append('  本报告仅提供历史数据的统计描述，不构成任何投注建议。')
        report.append('-' * 80)

        # 一、各位置号码频率统计
        report.append('\n【一、各位置号码频率统计】')
        report.append('-' * 60)
        freq = analysis_result.get('frequency', {})
        for pos in range(self.positions):
            if pos not in freq:
                continue
            pos_name = freq[pos]['position_name']
            report.append(f'\n{pos_name}:')
            stats = freq[pos]['number_stats']
            sorted_nums = sorted(stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
            for num, s in sorted_nums[:5]:
                report.append(f'  数字 {num}: 出现 {s["frequency"]} 次, '
                              f'观测概率 {s["observed_probability"]:.2%}, '
                              f'偏离率 {s["deviation_rate"]:+.2%}')

        # 二、遗漏值分析
        report.append('\n【二、遗漏值分析】')
        report.append('-' * 60)
        omission = analysis_result.get('omission', {})
        for pos in range(self.positions):
            if pos not in omission:
                continue
            pos_name = omission[pos]['position_name']
            stats = omission[pos]['number_stats']
            # 按当前遗漏排序
            sorted_by_omission = sorted(stats.items(), key=lambda x: x[1]['current_omission'], reverse=True)
            report.append(f'\n{pos_name} - 当前遗漏最大的号码:')
            for num, s in sorted_by_omission[:3]:
                report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期, '
                              f'平均遗漏 {s["avg_omission"]} 期, '
                              f'最大遗漏 {s["max_omission"]} 期')

        # 三、冷热号分级
        report.append('\n【三、冷热号分级】')
        report.append('-' * 60)
        hot_cold = analysis_result.get('hot_cold', {})
        for pos in range(self.positions):
            if pos not in hot_cold:
                continue
            pos_name = hot_cold[pos]['position_name']
            report.append(f'\n{pos_name}:')
            hot = hot_cold[pos].get('hot_numbers', [])
            cold = hot_cold[pos].get('cold_numbers', [])
            if hot:
                report.append(f'  热号: {", ".join([str(n["number"]) for n in hot[:3]])}')
            if cold:
                report.append(f'  冷号: {", ".join([str(n["number"]) for n in cold[:3]])}')

        # 四、012路分析
        report.append('\n【四、012路分析】')
        report.append('-' * 60)
        path = analysis_result.get('path_012', {})
        for pos in range(self.positions):
            if pos not in path:
                continue
            pos_name = path[pos]['position_name']
            path_stats = path[pos]['path_stats']
            report.append(f'\n{pos_name}:')
            for p in [0, 1, 2]:
                s = path_stats.get(p, {})
                report.append(f'  {p}路: 出现 {s.get("count", 0)} 次, '
                              f'概率 {s.get("probability", 0):.2%}')

        # 五、大小比与奇偶比
        report.append('\n【五、大小比与奇偶比】')
        report.append('-' * 60)
        big_small = analysis_result.get('big_small', {})
        odd_even = analysis_result.get('odd_even', {})
        for pos in range(self.positions):
            if pos in big_small and pos in odd_even:
                pos_name = big_small[pos]['position_name']
                bs = big_small[pos]
                oe = odd_even[pos]
                report.append(f'\n{pos_name}:')
                report.append(f'  大号: {bs["big_count"]} 次 ({bs["big_probability"]:.2%}), '
                              f'小号: {bs["small_count"]} 次 ({bs["small_probability"]:.2%})')
                report.append(f'  奇数: {oe["odd_count"]} 次 ({oe["odd_probability"]:.2%}), '
                              f'偶数: {oe["even_count"]} 次 ({oe["even_probability"]:.2%})')

        # 六、和值与跨度
        report.append('\n【六、和值与跨度分析】')
        report.append('-' * 60)
        hezhi = analysis_result.get('hezhi', {})
        span = analysis_result.get('span', {})
        if hezhi:
            report.append(f'\n和值统计:')
            report.append(f'  平均值: {hezhi.get("avg_hezhi", 0)} (理论期望: {hezhi.get("theory_avg", 27)})')
            report.append(f'  范围: {hezhi.get("min_hezhi", 0)} - {hezhi.get("max_hezhi", 0)}')
            report.append(f'  区间分布:')
            for range_key, s in hezhi.get('range_distribution', {}).items():
                report.append(f'    {range_key}: {s["count"]} 次 ({s["probability"]:.2%})')
        if span:
            report.append(f'\n跨度统计:')
            report.append(f'  平均值: {span.get("avg_span", 0)}')
            report.append(f'  范围: {span.get("min_span", 0)} - {span.get("max_span", 0)}')

        # 七、位置相关性
        report.append('\n【七、位置相关性分析】')
        report.append('-' * 60)
        corr = analysis_result.get('correlation', {})
        report.append('\n各位置间皮尔逊相关系数矩阵（绝对值越大相关性越强）:')
        matrix = corr.get('correlation_matrix', {})
        for i in range(min(6, self.positions)):
            row = []
            for j in range(min(6, self.positions)):
                val = matrix.get(i, {}).get(j, 0)
                row.append(f'{val:+.3f}')
            report.append(f'  位置{i + 1}: {" | ".join(row)}')
        report.append('\n  注：相关系数接近0表示位置间基本独立，符合随机性假设。')

        # 八、随机性检验
        report.append('\n【八、随机性检验】')
        report.append('-' * 60)
        rand = analysis_result.get('randomness', {})
        chi_sq = rand.get('chi_square_test', {})
        report.append('\n卡方均匀性检验:')
        for pos in range(self.positions):
            if pos in chi_sq:
                s = chi_sq[pos]
                report.append(f'  {s["position_name"]}: χ² = {s["chi_square"]:.4f}, '
                              f'自由度 = {s["degrees_of_freedom"]}, '
                              f'结论: {s["interpretation"]}')

        report.append('\n' + '=' * 80)
        report.append('          分析报告结束')
        report.append('=' * 80)
        report.append('\n【再次声明】')
        report.append('  彩票开奖为独立随机事件，历史数据的统计规律不代表未来结果。')
        report.append('  请理性购彩，量力而行。')

        return '\n'.join(report)


if __name__ == '__main__':
    # 测试代码
    from database import Database

    db = Database()
    if db.connect():
        data = db.query_all_qxc_data()
        db.disconnect()

        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data)
            report = analyzer.generate_report(result)
            print(report)
        else:
            print('数据库中没有数据')
    else:
        print('数据库连接失败')
