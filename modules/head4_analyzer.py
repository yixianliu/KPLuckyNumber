import logging
import os
import json
import math
from collections import defaultdict, Counter
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/head4_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class Head4Analyzer:
    """
    头4（前四位）分析器

    分析七星彩前四位数字（第一位是头、第二第三位是中间、第四位是尾）
    主要分析维度：
    - 头（第一位）：0-9
    - 中间（第二、三位）：00-99
    - 尾（第四位）：0-9
    """

    def __init__(self):
        self.position_names = ['头(第一位)', '中间(第二位)', '中间(第三位)', '尾(第四位)']
        self.head_range = list(range(0, 10))      # 头：0-9
        self.middle_range = list(range(0, 100))   # 中间：00-99
        self.tail_range = list(range(0, 10))      # 尾：0-9

    # ==================== 基础频率分析 ====================

    def analyze_head_frequency(self, data: List[Dict]) -> Dict:
        """分析头（第一位）号码频率"""
        total = len(data)
        if total == 0:
            return {}

        freq = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 1:
                try:
                    num = int(numbers[0])
                    freq[num] += 1
                except (ValueError, TypeError):
                    continue

        theory_prob = 1.0 / 10
        pos_stats = {}
        for num in self.head_range:
            observed_count = freq.get(num, 0)
            observed_prob = observed_count / total if total > 0 else 0
            deviation = (observed_prob - theory_prob) / theory_prob if theory_prob > 0 else 0
            pos_stats[num] = {
                'frequency': observed_count,
                'observed_probability': round(observed_prob, 6),
                'theoretical_probability': round(theory_prob, 6),
                'deviation_rate': round(deviation, 4),
                'expected_count': round(total * theory_prob, 2)
            }

        return {
            'position_name': '头(第一位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(3) if freq else [],
            'least_frequent': freq.most_common()[-3:] if len(freq) >= 3 else []
        }

    def analyze_middle_frequency(self, data: List[Dict]) -> Dict:
        """分析中间（第二、三位组合）频率"""
        total = len(data)
        if total == 0:
            return {}

        freq = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    freq[middle_num] += 1
                except (ValueError, TypeError):
                    continue

        theory_prob = 1.0 / 100
        pos_stats = {}
        # 只统计出现过的和理论期望较高的
        for num in range(100):
            observed_count = freq.get(num, 0)
            observed_prob = observed_count / total if total > 0 else 0
            deviation = (observed_prob - theory_prob) / theory_prob if theory_prob > 0 else 0
            pos_stats[num] = {
                'frequency': observed_count,
                'observed_probability': round(observed_prob, 6),
                'theoretical_probability': round(theory_prob, 6),
                'deviation_rate': round(deviation, 4),
                'expected_count': round(total * theory_prob, 2)
            }

        return {
            'position_name': '中间(第二、三位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(10) if freq else [],
            'least_frequent': freq.most_common()[-10:] if len(freq) >= 10 else []
        }

    def analyze_tail_frequency(self, data: List[Dict]) -> Dict:
        """分析尾（第四位）号码频率"""
        total = len(data)
        if total == 0:
            return {}

        freq = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    num = int(numbers[3])
                    freq[num] += 1
                except (ValueError, TypeError):
                    continue

        theory_prob = 1.0 / 10
        pos_stats = {}
        for num in self.tail_range:
            observed_count = freq.get(num, 0)
            observed_prob = observed_count / total if total > 0 else 0
            deviation = (observed_prob - theory_prob) / theory_prob if theory_prob > 0 else 0
            pos_stats[num] = {
                'frequency': observed_count,
                'observed_probability': round(observed_prob, 6),
                'theoretical_probability': round(theory_prob, 6),
                'deviation_rate': round(deviation, 4),
                'expected_count': round(total * theory_prob, 2)
            }

        return {
            'position_name': '尾(第四位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(3) if freq else [],
            'least_frequent': freq.most_common()[-3:] if len(freq) >= 3 else []
        }

    # ==================== 遗漏值分析 ====================

    def analyze_head_omission(self, data: List[Dict]) -> Dict:
        """分析头（第一位）遗漏值"""
        if not data:
            return {}

        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        total_periods = len(sorted_data)

        occurrence_indices = {num: [] for num in self.head_range}
        for idx, item in enumerate(sorted_data):
            numbers = item.get('numbers', [])
            if len(numbers) >= 1:
                try:
                    num = int(numbers[0])
                    if num in occurrence_indices:
                        occurrence_indices[num].append(idx)
                except (ValueError, TypeError):
                    continue

        num_stats = {}
        for num in self.head_range:
            indices = occurrence_indices[num]
            current_omission = total_periods - 1 - indices[-1] if indices else total_periods
            gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
            max_omission = max(gaps) if gaps else current_omission
            avg_omission = sum(gaps) / len(gaps) if gaps else current_omission
            omission_ratio = current_omission / avg_omission if avg_omission > 0 else 0

            num_stats[num] = {
                'current_omission': current_omission,
                'max_omission': max_omission,
                'avg_omission': round(avg_omission, 2),
                'omission_ratio': round(omission_ratio, 4),
                'total_occurrences': len(indices),
                'occurrence_rate': round(len(indices) / total_periods, 4) if total_periods > 0 else 0
            }

        return {
            'position_name': '头(第一位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    def analyze_middle_omission(self, data: List[Dict]) -> Dict:
        """分析中间（第二、三位）遗漏值"""
        if not data:
            return {}

        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        total_periods = len(sorted_data)

        occurrence_indices = {num: [] for num in range(100)}
        for idx, item in enumerate(sorted_data):
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    if middle_num in occurrence_indices:
                        occurrence_indices[middle_num].append(idx)
                except (ValueError, TypeError):
                    continue

        num_stats = {}
        for num in range(100):
            indices = occurrence_indices[num]
            current_omission = total_periods - 1 - indices[-1] if indices else total_periods
            gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
            max_omission = max(gaps) if gaps else current_omission
            avg_omission = sum(gaps) / len(gaps) if gaps else current_omission
            omission_ratio = current_omission / avg_omission if avg_omission > 0 else 0

            num_stats[num] = {
                'current_omission': current_omission,
                'max_omission': max_omission,
                'avg_omission': round(avg_omission, 2),
                'omission_ratio': round(omission_ratio, 4),
                'total_occurrences': len(indices),
                'occurrence_rate': round(len(indices) / total_periods, 4) if total_periods > 0 else 0
            }

        return {
            'position_name': '中间(第二、三位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    def analyze_tail_omission(self, data: List[Dict]) -> Dict:
        """分析尾（第四位）遗漏值"""
        if not data:
            return {}

        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        total_periods = len(sorted_data)

        occurrence_indices = {num: [] for num in self.tail_range}
        for idx, item in enumerate(sorted_data):
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    num = int(numbers[3])
                    if num in occurrence_indices:
                        occurrence_indices[num].append(idx)
                except (ValueError, TypeError):
                    continue

        num_stats = {}
        for num in self.tail_range:
            indices = occurrence_indices[num]
            current_omission = total_periods - 1 - indices[-1] if indices else total_periods
            gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
            max_omission = max(gaps) if gaps else current_omission
            avg_omission = sum(gaps) / len(gaps) if gaps else current_omission
            omission_ratio = current_omission / avg_omission if avg_omission > 0 else 0

            num_stats[num] = {
                'current_omission': current_omission,
                'max_omission': max_omission,
                'avg_omission': round(avg_omission, 2),
                'omission_ratio': round(omission_ratio, 4),
                'total_occurrences': len(indices),
                'occurrence_rate': round(len(indices) / total_periods, 4) if total_periods > 0 else 0
            }

        return {
            'position_name': '尾(第四位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    # ==================== 头尾组合分析 ====================

    def analyze_head_tail_combination(self, data: List[Dict]) -> Dict:
        """分析头尾组合频率"""
        total = len(data)
        if total == 0:
            return {}

        freq = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    head = int(numbers[0])
                    tail = int(numbers[3])
                    combo = f"{head}-{tail}"
                    freq[combo] += 1
                except (ValueError, TypeError):
                    continue

        theory_prob = 1.0 / 100  # 10 * 10
        combo_stats = {}
        for head in range(10):
            for tail in range(10):
                combo = f"{head}-{tail}"
                observed_count = freq.get(combo, 0)
                observed_prob = observed_count / total if total > 0 else 0
                deviation = (observed_prob - theory_prob) / theory_prob if theory_prob > 0 else 0
                combo_stats[combo] = {
                    'frequency': observed_count,
                    'observed_probability': round(observed_prob, 6),
                    'theoretical_probability': round(theory_prob, 6),
                    'deviation_rate': round(deviation, 4)
                }

        return {
            'position_name': '头尾组合',
            'total_samples': total,
            'combo_stats': combo_stats,
            'most_frequent': freq.most_common(10) if freq else [],
            'least_frequent': freq.most_common()[-10:] if len(freq) >= 10 else []
        }

    # ==================== 中间位特征分析 ====================

    def analyze_middle_features(self, data: List[Dict]) -> Dict:
        """分析中间位（第二、三位）特征"""
        total = len(data)
        if total == 0:
            return {}

        # 分析第二位的分布
        pos2_freq = Counter()
        # 分析第三位的分布
        pos3_freq = Counter()
        # 分析中间和值（第二位+第三位）
        middle_sum_freq = Counter()
        # 分析中间跨度
        middle_span_freq = Counter()

        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    pos2 = int(numbers[1])
                    pos3 = int(numbers[2])
                    pos2_freq[pos2] += 1
                    pos3_freq[pos3] += 1
                    middle_sum = pos2 + pos3
                    middle_sum_freq[middle_sum] += 1
                    middle_span = abs(pos2 - pos3)
                    middle_span_freq[middle_span] += 1
                except (ValueError, TypeError):
                    continue

        return {
            'position_name': '中间位特征',
            'total_samples': total,
            'second_position': {
                'most_frequent': pos2_freq.most_common(5) if pos2_freq else [],
                'least_frequent': pos2_freq.most_common()[-5:] if len(pos2_freq) >= 5 else []
            },
            'third_position': {
                'most_frequent': pos3_freq.most_common(5) if pos3_freq else [],
                'least_frequent': pos3_freq.most_common()[-5:] if len(pos3_freq) >= 5 else []
            },
            'middle_sum': {
                'most_frequent': middle_sum_freq.most_common(5) if middle_sum_freq else [],
                'distribution': {str(k): v for k, v in sorted(middle_sum_freq.items())}
            },
            'middle_span': {
                'most_frequent': middle_span_freq.most_common(5) if middle_span_freq else [],
                'distribution': {str(k): v for k, v in sorted(middle_span_freq.items())}
            }
        }

    # ==================== 综合计算 ====================

    def calculate_head4_analysis(self, data: List[Dict]) -> Dict:
        """执行头4综合分析"""
        if len(data) < 10:
            logger.warning('数据量不足，头4分析结果可能不准确')

        total = len(data)

        # 执行所有分析
        head_freq = self.analyze_head_frequency(data)
        middle_freq = self.analyze_middle_frequency(data)
        tail_freq = self.analyze_tail_frequency(data)

        head_omission = self.analyze_head_omission(data)
        middle_omission = self.analyze_middle_omission(data)
        tail_omission = self.analyze_tail_omission(data)

        head_tail_combo = self.analyze_head_tail_combination(data)
        middle_features = self.analyze_middle_features(data)

        return {
            'head_frequency': head_freq,
            'middle_frequency': middle_freq,
            'tail_frequency': tail_freq,
            'head_omission': head_omission,
            'middle_omission': middle_omission,
            'tail_omission': tail_omission,
            'head_tail_combination': head_tail_combo,
            'middle_features': middle_features,
            'total_samples': total,
            'analysis_time': datetime.now().isoformat(),
            'methodology_note': '本分析基于历史数据统计，所有号码的理论出现概率均等。'
        }

    # ==================== 最优组合生成 ====================

    def generate_top10_combinations(self, data: List[Dict]) -> List[Dict]:
        """
        基于频率分析、遗漏值分析和近期热度，综合计算每个位置的得分，生成最优10组数字组合。

        七星彩头4结构：头(0-9) + 中间(00-99) + 尾(0-9)

        得分计算规则：
        - 头位得分 = 频率得分 * 0.4 + 遗漏回补得分 * 0.3 + 近期热度得分 * 0.3
        - 中间组合得分 = 频率得分 * 0.3 + 遗漏回补得分 * 0.4 + 近期热度得分 * 0.3
        - 尾位得分 = 频率得分 * 0.4 + 遗漏回补得分 * 0.3 + 近期热度得分 * 0.3

        Args:
            data: 历史开奖数据列表，每条记录包含 'numbers' 字段

        Returns:
            最优10组组合列表，每条包含 rank, head, middle, tail, combination, score
        """
        if not data or len(data) < 10:
            logger.warning('数据量不足，无法生成可靠的最优组合')
            return []

        total = len(data)
        # 按期号排序，确保时间顺序正确
        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        # 最近20期数据，用于近期热度计算
        recent_data = sorted_data[-20:] if len(sorted_data) >= 20 else sorted_data

        # ==================== 头位得分计算 ====================
        # 频率统计
        head_freq = Counter()
        for item in sorted_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 1:
                try:
                    head_freq[int(numbers[0])] += 1
                except (ValueError, TypeError):
                    continue

        # 遗漏值统计
        head_current_omission = {}
        head_max_omission = {}
        for num in self.head_range:
            indices = [idx for idx, item in enumerate(sorted_data)
                       if len(item.get('numbers', [])) >= 1
                       and self._safe_int(item['numbers'][0]) == num]
            if indices:
                current = len(sorted_data) - 1 - indices[-1]
                gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
                max_om = max(gaps) if gaps else current
            else:
                current = len(sorted_data)
                max_om = current
            head_current_omission[num] = current
            head_max_omission[num] = max_om

        # 近期热度统计（最近20期）
        head_recent_heat = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 1:
                try:
                    head_recent_heat[int(numbers[0])] += 1
                except (ValueError, TypeError):
                    continue

        # 计算头位综合得分
        head_scores = {}
        for num in self.head_range:
            freq_score = head_freq.get(num, 0) / total  # 归一化频率
            # 遗漏回补得分：当前遗漏值/最大遗漏值（遗漏越大越可能回补）
            max_om = head_max_omission.get(num, 1)
            omission_score = head_current_omission[num] / max_om if max_om > 0 else 0
            # 近期热度得分：最近20期出现次数/20
            heat_score = head_recent_heat.get(num, 0) / 20
            # 综合得分：频率 * 0.4 + 遗漏回补 * 0.3 + 近期热度 * 0.3
            head_scores[num] = freq_score * 0.4 + omission_score * 0.3 + heat_score * 0.3

        # ==================== 中间组合得分计算 ====================
        # 频率统计
        middle_freq = Counter()
        for item in sorted_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    middle_freq[middle_num] += 1
                except (ValueError, TypeError):
                    continue

        # 遗漏值统计
        middle_current_omission = {}
        middle_max_omission = {}
        for num in range(100):
            indices = [idx for idx, item in enumerate(sorted_data)
                       if len(item.get('numbers', [])) >= 3
                       and self._safe_int(item['numbers'][1]) * 10 + self._safe_int(item['numbers'][2]) == num]
            if indices:
                current = len(sorted_data) - 1 - indices[-1]
                gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
                max_om = max(gaps) if gaps else current
            else:
                current = len(sorted_data)
                max_om = current
            middle_current_omission[num] = current
            middle_max_omission[num] = max_om

        # 近期热度统计（最近20期）
        middle_recent_heat = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    middle_recent_heat[middle_num] += 1
                except (ValueError, TypeError):
                    continue

        # 计算中间组合综合得分
        middle_scores = {}
        for num in range(100):
            freq_score = middle_freq.get(num, 0) / total  # 归一化频率
            # 遗漏回补得分：当前遗漏值/最大遗漏值
            max_om = middle_max_omission.get(num, 1)
            omission_score = middle_current_omission[num] / max_om if max_om > 0 else 0
            # 近期热度得分：最近20期出现次数/20
            heat_score = middle_recent_heat.get(num, 0) / 20
            # 综合得分：频率 * 0.3 + 遗漏回补 * 0.4 + 近期热度 * 0.3
            middle_scores[num] = freq_score * 0.3 + omission_score * 0.4 + heat_score * 0.3

        # ==================== 尾位得分计算 ====================
        # 频率统计
        tail_freq = Counter()
        for item in sorted_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    tail_freq[int(numbers[3])] += 1
                except (ValueError, TypeError):
                    continue

        # 遗漏值统计
        tail_current_omission = {}
        tail_max_omission = {}
        for num in self.tail_range:
            indices = [idx for idx, item in enumerate(sorted_data)
                       if len(item.get('numbers', [])) >= 4
                       and self._safe_int(item['numbers'][3]) == num]
            if indices:
                current = len(sorted_data) - 1 - indices[-1]
                gaps = [indices[i] - indices[i - 1] - 1 for i in range(1, len(indices))]
                max_om = max(gaps) if gaps else current
            else:
                current = len(sorted_data)
                max_om = current
            tail_current_omission[num] = current
            tail_max_omission[num] = max_om

        # 近期热度统计（最近20期）
        tail_recent_heat = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    tail_recent_heat[int(numbers[3])] += 1
                except (ValueError, TypeError):
                    continue

        # 计算尾位综合得分
        tail_scores = {}
        for num in self.tail_range:
            freq_score = tail_freq.get(num, 0) / total  # 归一化频率
            # 遗漏回补得分：当前遗漏值/最大遗漏值
            max_om = tail_max_omission.get(num, 1)
            omission_score = tail_current_omission[num] / max_om if max_om > 0 else 0
            # 近期热度得分：最近20期出现次数/20
            heat_score = tail_recent_heat.get(num, 0) / 20
            # 综合得分：频率 * 0.4 + 遗漏回补 * 0.3 + 近期热度 * 0.3
            tail_scores[num] = freq_score * 0.4 + omission_score * 0.3 + heat_score * 0.3

        # ==================== 组合得分计算 ====================
        # 取头位和尾位得分最高的前5名，中间组合得分最高的前20名
        top_heads = sorted(head_scores.items(), key=lambda x: x[1], reverse=True)[:5]
        top_middles = sorted(middle_scores.items(), key=lambda x: x[1], reverse=True)[:20]
        top_tails = sorted(tail_scores.items(), key=lambda x: x[1], reverse=True)[:5]

        # 生成所有候选组合并计算综合得分
        candidates = []
        for head_num, head_score in top_heads:
            for middle_num, middle_score in top_middles:
                for tail_num, tail_score in top_tails:
                    # 组合综合得分 = 头位得分 + 中间组合得分 + 尾位得分
                    total_score = head_score + middle_score + tail_score
                    candidates.append({
                        'head': head_num,
                        'middle': middle_num,
                        'tail': tail_num,
                        'score': round(total_score, 4),
                        'head_score': round(head_score, 4),
                        'middle_score': round(middle_score, 4),
                        'tail_score': round(tail_score, 4)
                    })

        # 按综合得分降序排列，取前10组
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top10 = candidates[:10]

        # 格式化输出
        result = []
        for rank, item in enumerate(top10, start=1):
            combination = f"{item['head']}-{item['middle']:02d}-{item['tail']}"
            result.append({
                'rank': rank,
                'head': item['head'],
                'middle': item['middle'],
                'tail': item['tail'],
                'combination': combination,
                'score': item['score'],
                'head_score': item['head_score'],
                'middle_score': item['middle_score'],
                'tail_score': item['tail_score']
            })

        logger.info(f'已生成最优{len(result)}组数字组合')
        return result

    @staticmethod
    def _safe_int(value) -> int:
        """安全转换为整数，转换失败返回-1"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return -1

    # ==================== 报告生成 ====================

    def generate_head4_report(self, analysis_result: Dict, top10_combinations: Optional[List[Dict]] = None) -> str:
        """生成头4分析报告

        Args:
            analysis_result: 头4综合分析结果字典
            top10_combinations: 最优10组数字组合列表，由 generate_top10_combinations 方法生成
        """
        report = []
        report.append('=' * 80)
        report.append('        七星彩头4（前四位）分析报告')
        report.append('=' * 80)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 80)
        report.append('\n【重要声明】')
        report.append('  彩票开奖为独立随机事件，每位号码的理论出现概率均等。')
        report.append('  本报告仅提供历史数据的统计描述，不构成任何投注建议。')
        report.append('-' * 80)

        # 一、头（第一位）频率分析
        report.append('\n【一、头（第一位）频率分析】')
        report.append('-' * 60)
        head_freq = analysis_result.get('head_frequency', {})
        stats = head_freq.get('number_stats', {})
        sorted_nums = sorted(stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n头位号码出现频率（前5）:')
        for num, s in sorted_nums[:5]:
            report.append(f'  数字 {num}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}, '
                          f'偏离率 {s["deviation_rate"]:+.2%}')

        # 二、中间（第二、三位）频率分析
        report.append('\n【二、中间（第二、三位）频率分析】')
        report.append('-' * 60)
        middle_freq = analysis_result.get('middle_frequency', {})
        middle_stats = middle_freq.get('number_stats', {})
        sorted_middle = sorted(middle_stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n中间组合出现频率（前10）:')
        for num, s in sorted_middle[:10]:
            report.append(f'  组合 {num:02d}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}')

        # 三、尾（第四位）频率分析
        report.append('\n【三、尾（第四位）频率分析】')
        report.append('-' * 60)
        tail_freq = analysis_result.get('tail_frequency', {})
        tail_stats = tail_freq.get('number_stats', {})
        sorted_tail = sorted(tail_stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n尾位号码出现频率（前5）:')
        for num, s in sorted_tail[:5]:
            report.append(f'  数字 {num}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}, '
                          f'偏离率 {s["deviation_rate"]:+.2%}')

        # 四、头尾组合分析
        report.append('\n【四、头尾组合分析】')
        report.append('-' * 60)
        combo = analysis_result.get('head_tail_combination', {})
        combo_stats = combo.get('combo_stats', {})
        sorted_combo = sorted(combo_stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n头尾组合出现频率（前10）:')
        for combo_key, s in sorted_combo[:10]:
            report.append(f'  组合 {combo_key}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}')

        # 五、中间位特征分析
        report.append('\n【五、中间位特征分析】')
        report.append('-' * 60)
        middle_feat = analysis_result.get('middle_features', {})
        pos2 = middle_feat.get('second_position', {})
        pos3 = middle_feat.get('third_position', {})
        mid_sum = middle_feat.get('middle_sum', {})
        mid_span = middle_feat.get('middle_span', {})

        report.append('\n第二位热门号码:')
        for num, count in pos2.get('most_frequent', [])[:5]:
            report.append(f'  数字 {num}: {count} 次')

        report.append('\n第三位热门号码:')
        for num, count in pos3.get('most_frequent', [])[:5]:
            report.append(f'  数字 {num}: {count} 次')

        report.append('\n中间和值热门:')
        for num, count in mid_sum.get('most_frequent', [])[:5]:
            report.append(f'  和值 {num}: {count} 次')

        report.append('\n中间跨度热门:')
        for num, count in mid_span.get('most_frequent', [])[:5]:
            report.append(f'  跨度 {num}: {count} 次')

        # 六、遗漏值分析
        report.append('\n【六、遗漏值分析】')
        report.append('-' * 60)

        head_om = analysis_result.get('head_omission', {})
        head_om_stats = head_om.get('number_stats', {})
        sorted_head_om = sorted(head_om_stats.items(), key=lambda x: x[1]['current_omission'], reverse=True)
        report.append('\n头位当前遗漏最大:')
        for num, s in sorted_head_om[:3]:
            report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期, '
                          f'平均遗漏 {s["avg_omission"]} 期')

        tail_om = analysis_result.get('tail_omission', {})
        tail_om_stats = tail_om.get('number_stats', {})
        sorted_tail_om = sorted(tail_om_stats.items(), key=lambda x: x[1]['current_omission'], reverse=True)
        report.append('\n尾位当前遗漏最大:')
        for num, s in sorted_tail_om[:3]:
            report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期, '
                          f'平均遗漏 {s["avg_omission"]} 期')

        # 七、最优10组数字组合
        report.append('\n【七、最优10组数字组合】')
        report.append('-' * 60)
        report.append('\n基于频率分析、遗漏值分析和近期热度综合评分，推荐以下10组最优组合：')
        report.append('  评分规则：头位=频率*0.4+遗漏*0.3+热度*0.3')
        report.append('           中间=频率*0.3+遗漏*0.4+热度*0.3')
        report.append('           尾位=频率*0.4+遗漏*0.3+热度*0.3')
        report.append('')

        if top10_combinations:
            for item in top10_combinations:
                report.append(
                    f'  第{item["rank"]:>2d}名: {item["combination"]}  '
                    f'综合得分: {item["score"]:.4f}'
                )
        else:
            report.append('  暂无最优组合数据（需要传入 top10_combinations 参数）')

        report.append('\n' + '=' * 80)
        report.append('          头4分析报告结束')
        report.append('=' * 80)
        report.append('\n【再次声明】')
        report.append('  彩票开奖为独立随机事件，历史数据的统计规律不代表未来结果。')
        report.append('  请理性购彩，量力而行。')

        return '\n'.join(report)
