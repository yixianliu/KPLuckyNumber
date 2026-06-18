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
        logging.FileHandler('logs/head4_analyzer_p5.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('modules.p5_head4_analyzer')


class P5Head4Analyzer:
    """
    排列5头4分析器

    分析排列5前四位数字（万位是头、千位+百位是中间、十位是尾）
    排列5数据格式：numbers = [万位, 千位, 百位, 十位, 个位]
    主要分析维度：
    - 头（万位）：0-9
    - 中间（千位+百位）：00-99
    - 尾（十位）：0-9
    """

    def __init__(self):
        self.position_names = ['头(万位)', '中间(千位)', '中间(百位)', '尾(十位)']
        self.head_range = list(range(0, 10))      # 头（万位）：0-9
        self.middle_range = list(range(0, 100))   # 中间（千位+百位）：00-99
        self.tail_range = list(range(0, 10))      # 尾（十位）：0-9

    # ==================== 基础频率分析 ====================

    def analyze_head_frequency(self, data: List[Dict]) -> Dict:
        """分析头（万位）号码频率"""
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
            'position_name': '头(万位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(3) if freq else [],
            'least_frequent': freq.most_common()[-3:] if len(freq) >= 3 else []
        }

    def analyze_middle_frequency(self, data: List[Dict]) -> Dict:
        """分析中间（千位+百位组合）频率"""
        total = len(data)
        if total == 0:
            return {}

        freq = Counter()
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    # 千位(numbers[1]) * 10 + 百位(numbers[2])
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    freq[middle_num] += 1
                except (ValueError, TypeError):
                    continue

        theory_prob = 1.0 / 100
        pos_stats = {}
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
            'position_name': '中间(千位+百位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(10) if freq else [],
            'least_frequent': freq.most_common()[-10:] if len(freq) >= 10 else []
        }

    def analyze_tail_frequency(self, data: List[Dict]) -> Dict:
        """分析尾（十位）号码频率"""
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
            'position_name': '尾(十位)',
            'total_samples': total,
            'number_stats': pos_stats,
            'most_frequent': freq.most_common(3) if freq else [],
            'least_frequent': freq.most_common()[-3:] if len(freq) >= 3 else []
        }

    # ==================== 遗漏值分析 ====================

    def analyze_head_omission(self, data: List[Dict]) -> Dict:
        """分析头（万位）遗漏值"""
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
            'position_name': '头(万位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    def analyze_middle_omission(self, data: List[Dict]) -> Dict:
        """分析中间（千位+百位）遗漏值"""
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
            'position_name': '中间(千位+百位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    def analyze_tail_omission(self, data: List[Dict]) -> Dict:
        """分析尾（十位）遗漏值"""
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
            'position_name': '尾(十位)',
            'total_periods': total_periods,
            'number_stats': num_stats
        }

    # ==================== 头尾组合分析 ====================

    def analyze_head_tail_combination(self, data: List[Dict]) -> Dict:
        """分析头尾组合频率（万位+十位）"""
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
            'position_name': '头尾组合(万位-十位)',
            'total_samples': total,
            'combo_stats': combo_stats,
            'most_frequent': freq.most_common(10) if freq else [],
            'least_frequent': freq.most_common()[-10:] if len(freq) >= 10 else []
        }

    # ==================== 中间位特征分析 ====================

    def analyze_middle_features(self, data: List[Dict]) -> Dict:
        """分析中间位（千位+百位）特征"""
        total = len(data)
        if total == 0:
            return {}

        # 分析千位(numbers[1])的分布
        pos2_freq = Counter()
        # 分析百位(numbers[2])的分布
        pos3_freq = Counter()
        # 分析中间和值（千位+百位）
        middle_sum_freq = Counter()
        # 分析中间跨度
        middle_span_freq = Counter()

        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    pos2 = int(numbers[1])  # 千位
                    pos3 = int(numbers[2])  # 百位
                    pos2_freq[pos2] += 1
                    pos3_freq[pos3] += 1
                    middle_sum = pos2 + pos3
                    middle_sum_freq[middle_sum] += 1
                    middle_span = abs(pos2 - pos3)
                    middle_span_freq[middle_span] += 1
                except (ValueError, TypeError):
                    continue

        return {
            'position_name': '中间位特征(千位+百位)',
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
        """执行排列5头4综合分析"""
        if len(data) < 10:
            logger.warning('数据量不足，排列5头4分析结果可能不准确')

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
            'methodology_note': '本分析基于排列5历史数据统计，所有号码的理论出现概率均等。'
        }

    # ==================== 报告生成 ====================

    def generate_head4_report(self, analysis_result: Dict, top10_combinations: Optional[List[Dict]] = None) -> str:
        """生成排列5头4分析报告

        Args:
            analysis_result: 头4综合分析结果字典
            top10_combinations: 最优10组数字组合列表，由 generate_top10_combinations 方法生成
        """
        report = []
        report.append('=' * 80)
        report.append('        排列5头4（前四位）分析报告')
        report.append('=' * 80)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 80)
        report.append('\n【重要声明】')
        report.append('  彩票开奖为独立随机事件，每位号码的理论出现概率均等。')
        report.append('  本报告仅提供历史数据的统计描述，不构成任何投注建议。')
        report.append('-' * 80)

        # 一、头（万位）频率分析
        report.append('\n【一、头（万位）频率分析】')
        report.append('-' * 60)
        head_freq = analysis_result.get('head_frequency', {})
        stats = head_freq.get('number_stats', {})
        sorted_nums = sorted(stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n头位号码出现频率（前5）:')
        for num, s in sorted_nums[:5]:
            report.append(f'  数字 {num}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}, '
                          f'偏离率 {s["deviation_rate"]:+.2%}')

        # 二、中间（千位+百位）频率分析
        report.append('\n【二、中间（千位+百位）频率分析】')
        report.append('-' * 60)
        middle_freq = analysis_result.get('middle_frequency', {})
        middle_stats = middle_freq.get('number_stats', {})
        sorted_middle = sorted(middle_stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n中间组合出现频率（前10）:')
        for num, s in sorted_middle[:10]:
            report.append(f'  组合 {num:02d}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}')

        # 三、尾（十位）频率分析
        report.append('\n【三、尾（十位）频率分析】')
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
        report.append('\n【四、头尾组合分析（万位-十位）】')
        report.append('-' * 60)
        combo = analysis_result.get('head_tail_combination', {})
        combo_stats = combo.get('combo_stats', {})
        sorted_combo = sorted(combo_stats.items(), key=lambda x: x[1]['frequency'], reverse=True)
        report.append('\n头尾组合出现频率（前10）:')
        for combo_key, s in sorted_combo[:10]:
            report.append(f'  组合 {combo_key}: 出现 {s["frequency"]} 次, '
                          f'观测概率 {s["observed_probability"]:.2%}')

        # 五、中间位特征分析
        report.append('\n【五、中间位特征分析（千位+百位）】')
        report.append('-' * 60)
        middle_feat = analysis_result.get('middle_features', {})
        pos2 = middle_feat.get('second_position', {})
        pos3 = middle_feat.get('third_position', {})
        mid_sum = middle_feat.get('middle_sum', {})
        mid_span = middle_feat.get('middle_span', {})

        report.append('\n千位热门号码:')
        for num, count in pos2.get('most_frequent', [])[:5]:
            report.append(f'  数字 {num}: {count} 次')

        report.append('\n百位热门号码:')
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
        report.append('\n头位(万位)当前遗漏最大:')
        for num, s in sorted_head_om[:3]:
            report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期, '
                          f'平均遗漏 {s["avg_omission"]} 期')

        tail_om = analysis_result.get('tail_omission', {})
        tail_om_stats = tail_om.get('number_stats', {})
        sorted_tail_om = sorted(tail_om_stats.items(), key=lambda x: x[1]['current_omission'], reverse=True)
        report.append('\n尾位(十位)当前遗漏最大:')
        for num, s in sorted_tail_om[:3]:
            report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期, '
                          f'平均遗漏 {s["avg_omission"]} 期')

        # 七、最优10组数字组合（如果提供）
        if top10_combinations:
            report.append('\n【七、最优10组数字组合推荐】')
            report.append('-' * 60)
            report.append('\n基于频率、遗漏和近期热度综合评分，推荐以下组合：\n')
            for item in top10_combinations:
                report.append(f"  第{item['rank']:>2}名: {item['combination']}  "
                              f"综合得分: {item['score']:.4f}")
            report.append('\n  说明：综合得分 = 头位得分*0.3 + 中间得分*0.4 + 尾位得分*0.3')

        report.append('\n' + '=' * 80)
        report.append('          排列5头4分析报告结束')
        report.append('=' * 80)
        report.append('\n【再次声明】')
        report.append('  彩票开奖为独立随机事件，历史数据的统计规律不代表未来结果。')
        report.append('  请理性购彩，量力而行。')

        return '\n'.join(report)

    # ==================== 最优组合生成 ====================

    def generate_top10_combinations(self, data: List[Dict]) -> List[Dict]:
        """
        基于频率分析和遗漏值分析，综合计算每个位置的得分，生成最优10组头4组合。

        得分计算规则：
        - 头位得分 = 频率得分 * 0.4 + 遗漏回补得分 * 0.3 + 近期热度得分 * 0.3
        - 中间组合得分 = 频率得分 * 0.3 + 遗漏回补得分 * 0.4 + 近期热度得分 * 0.3
        - 尾位得分 = 频率得分 * 0.4 + 遗漏回补得分 * 0.3 + 近期热度得分 * 0.3

        返回格式：
        [{'rank': 1, 'head': x, 'middle': xx, 'tail': x,
          'combination': 'x-xx-x', 'score': xx.xx}, ...]
        """
        if len(data) < 10:
            logger.warning('数据量不足（<10期），最优组合计算结果可能不可靠')

        # 按期号排序，确保时间顺序正确
        sorted_data = sorted(data, key=lambda x: x.get('issue', ''))
        total = len(sorted_data)

        # ---------- 1. 计算头位（万位）各数字得分 ----------
        head_freq = self.analyze_head_frequency(data)
        head_omission = self.analyze_head_omission(data)
        head_freq_stats = head_freq.get('number_stats', {})
        head_om_stats = head_omission.get('number_stats', {})

        # 近期热度窗口（最近30期）
        recent_window = min(30, total)
        recent_data = sorted_data[-recent_window:]
        recent_head_freq = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 1:
                try:
                    recent_head_freq[int(numbers[0])] += 1
                except (ValueError, TypeError):
                    continue

        head_scores = {}
        for num in self.head_range:
            # 频率得分：归一化到0-1
            freq_count = head_freq_stats.get(num, {}).get('frequency', 0)
            max_freq = max(s.get('frequency', 0) for s in head_freq_stats.values()) if head_freq_stats else 1
            freq_score = freq_count / max_freq if max_freq > 0 else 0

            # 遗漏回补得分：遗漏越大，回补可能性越高（归一化到0-1）
            om_data = head_om_stats.get(num, {})
            current_om = om_data.get('current_omission', 0)
            max_om = om_data.get('max_omission', 1)
            # 遗漏比 = 当前遗漏 / 最大遗漏，越接近1说明越可能回补
            omission_score = min(current_om / max_om, 1.0) if max_om > 0 else 0

            # 近期热度得分：归一化到0-1
            recent_count = recent_head_freq.get(num, 0)
            max_recent = max(recent_head_freq.values()) if recent_head_freq else 1
            recent_score = recent_count / max_recent if max_recent > 0 else 0

            # 综合得分：频率 * 0.4 + 遗漏回补 * 0.3 + 近期热度 * 0.3
            total_score = freq_score * 0.4 + omission_score * 0.3 + recent_score * 0.3
            head_scores[num] = round(total_score, 4)

        # ---------- 2. 计算中间组合（千位+百位）得分 ----------
        middle_freq = self.analyze_middle_frequency(data)
        middle_omission = self.analyze_middle_omission(data)
        middle_freq_stats = middle_freq.get('number_stats', {})
        middle_om_stats = middle_omission.get('number_stats', {})

        # 近期中间组合热度
        recent_middle_freq = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 3:
                try:
                    middle_num = int(numbers[1]) * 10 + int(numbers[2])
                    recent_middle_freq[middle_num] += 1
                except (ValueError, TypeError):
                    continue

        middle_scores = {}
        for num in range(100):
            freq_count = middle_freq_stats.get(num, {}).get('frequency', 0)
            max_freq = max(s.get('frequency', 0) for s in middle_freq_stats.values()) if middle_freq_stats else 1
            freq_score = freq_count / max_freq if max_freq > 0 else 0

            om_data = middle_om_stats.get(num, {})
            current_om = om_data.get('current_omission', 0)
            max_om = om_data.get('max_omission', 1)
            omission_score = min(current_om / max_om, 1.0) if max_om > 0 else 0

            recent_count = recent_middle_freq.get(num, 0)
            max_recent = max(recent_middle_freq.values()) if recent_middle_freq else 1
            recent_score = recent_count / max_recent if max_recent > 0 else 0

            # 中间组合权重：频率 * 0.3 + 遗漏回补 * 0.4 + 近期热度 * 0.3
            total_score = freq_score * 0.3 + omission_score * 0.4 + recent_score * 0.3
            middle_scores[num] = round(total_score, 4)

        # ---------- 3. 计算尾位（十位）得分 ----------
        tail_freq = self.analyze_tail_frequency(data)
        tail_omission = self.analyze_tail_omission(data)
        tail_freq_stats = tail_freq.get('number_stats', {})
        tail_om_stats = tail_omission.get('number_stats', {})

        # 近期尾位热度
        recent_tail_freq = Counter()
        for item in recent_data:
            numbers = item.get('numbers', [])
            if len(numbers) >= 4:
                try:
                    recent_tail_freq[int(numbers[3])] += 1
                except (ValueError, TypeError):
                    continue

        tail_scores = {}
        for num in self.tail_range:
            freq_count = tail_freq_stats.get(num, {}).get('frequency', 0)
            max_freq = max(s.get('frequency', 0) for s in tail_freq_stats.values()) if tail_freq_stats else 1
            freq_score = freq_count / max_freq if max_freq > 0 else 0

            om_data = tail_om_stats.get(num, {})
            current_om = om_data.get('current_omission', 0)
            max_om = om_data.get('max_omission', 1)
            omission_score = min(current_om / max_om, 1.0) if max_om > 0 else 0

            recent_count = recent_tail_freq.get(num, 0)
            max_recent = max(recent_tail_freq.values()) if recent_tail_freq else 1
            recent_score = recent_count / max_recent if max_recent > 0 else 0

            # 尾位权重：频率 * 0.4 + 遗漏回补 * 0.3 + 近期热度 * 0.3
            total_score = freq_score * 0.4 + omission_score * 0.3 + recent_score * 0.3
            tail_scores[num] = round(total_score, 4)

        # ---------- 4. 组合生成：选取得分最高的头、中间、尾，排列组合后取Top10 ----------
        # 取头位得分前5
        top_heads = sorted(head_scores.items(), key=lambda x: x[1], reverse=True)[:5]
        # 取中间组合得分前5
        top_middles = sorted(middle_scores.items(), key=lambda x: x[1], reverse=True)[:5]
        # 取尾位得分前5
        top_tails = sorted(tail_scores.items(), key=lambda x: x[1], reverse=True)[:5]

        # 生成所有候选组合并计算综合得分
        candidates = []
        for head_num, head_score in top_heads:
            for middle_num, middle_score in top_middles:
                for tail_num, tail_score in top_tails:
                    # 综合得分 = 头位得分 * 0.3 + 中间得分 * 0.4 + 尾位得分 * 0.3
                    combo_score = head_score * 0.3 + middle_score * 0.4 + tail_score * 0.3
                    candidates.append({
                        'head': head_num,
                        'middle': middle_num,
                        'tail': tail_num,
                        'head_score': head_score,
                        'middle_score': middle_score,
                        'tail_score': tail_score,
                        'score': round(combo_score, 4)
                    })

        # 按综合得分降序排列，取前10
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top10 = candidates[:10]

        # 格式化输出
        result = []
        for idx, item in enumerate(top10, 1):
            result.append({
                'rank': idx,
                'head': item['head'],
                'middle': item['middle'],
                'tail': item['tail'],
                'combination': f"{item['head']}-{item['middle']:02d}-{item['tail']}",
                'score': round(item['score'], 2),
                'head_score': round(item['head_score'], 4),
                'middle_score': round(item['middle_score'], 4),
                'tail_score': round(item['tail_score'], 4)
            })

        logger.info(f'已生成排列5头4最优{len(result)}组组合')
        return result
