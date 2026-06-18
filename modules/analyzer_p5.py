"""
排列5数据分析模块

负责对排列5数据进行多维度分析，包括频率统计、间隔分析、概率计算等
支持整合走势图数据进行综合分析
"""

import logging
import os
import json
from collections import defaultdict
from datetime import datetime

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/analyzer_p5.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5Analyzer:
    """
    排列5数据分析器类
    
    负责对排列5数据进行多维度分析，包括频率统计、间隔分析、概率计算等
    支持整合走势图数据进行综合分析
    """
    
    def __init__(self):
        """初始化分析器"""
        self.positions = 5  # 排列5有5个位置
        self.number_range = range(0, 10)  # 每位号码范围0-9
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
    
    def analyze_frequency(self, data):
        """
        分析号码出现频率
        
        Args:
            data: 数据列表
        
        Returns:
            频率分析结果字典
        """
        # 初始化频率统计
        freq = defaultdict(lambda: [0] * self.positions)
        
        for item in data:
            numbers = item['numbers']
            for i in range(min(self.positions, len(numbers))):
                num = numbers[i]
                if 0 <= num <= 9:
                    freq[num][i] += 1
        
        total = len(data)
        freq_result = {}
        
        for num in sorted(freq.keys()):
            prob_list = []
            for i in range(self.positions):
                prob = freq[num][i] / total if total > 0 else 0
                prob_list.append(round(prob, 4))
            freq_result[num] = {
                'frequency': freq[num],
                'probability': prob_list,
                'total_count': sum(freq[num])
            }
        
        return freq_result
    
    def analyze_interval(self, data):
        """
        分析号码间隔周期
        
        Args:
            data: 数据列表
        
        Returns:
            间隔统计结果字典
        """
        last_occurrence = {}
        intervals = defaultdict(list)
        
        # 按期号排序
        sorted_data = sorted(data, key=lambda x: x['issue'])
        
        for idx, item in enumerate(sorted_data):
            numbers = item['numbers']
            for i in range(min(self.positions, len(numbers))):
                num = numbers[i]
                key = (num, i)
                
                if key in last_occurrence:
                    interval = idx - last_occurrence[key]
                    intervals[key].append(interval)
                
                last_occurrence[key] = idx
        
        interval_result = {}
        for (num, pos), interval_list in intervals.items():
            if interval_list:
                avg_interval = sum(interval_list) / len(interval_list)
                max_interval = max(interval_list)
                min_interval = min(interval_list)
                current_interval = len(sorted_data) - 1 - last_occurrence.get((num, pos), 0)
                
                interval_result[(num, pos)] = {
                    'count': len(interval_list),
                    'avg': round(avg_interval, 2),
                    'max': max_interval,
                    'min': min_interval,
                    'current': current_interval
                }
        
        return interval_result
    
    def analyze_hezhi(self, data):
        """
        分析和值分布
        
        Args:
            data: 数据列表
        
        Returns:
            和值统计结果字典
        """
        hezhi_counts = defaultdict(int)
        hezhi_list = []
        
        for item in data:
            hezhi = item.get('hezhi')
            if hezhi is not None:
                hezhi_counts[hezhi] += 1
                hezhi_list.append(hezhi)
        
        total = len(data)
        hezhi_result = {}
        
        for hezhi, count in sorted(hezhi_counts.items()):
            hezhi_result[hezhi] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        # 和值统计指标
        stats = {
            'min': min(hezhi_list) if hezhi_list else 0,
            'max': max(hezhi_list) if hezhi_list else 0,
            'avg': round(sum(hezhi_list) / len(hezhi_list), 2) if hezhi_list else 0,
            'distribution': hezhi_result
        }
        
        return stats
    
    def analyze_odd_even(self, data):
        """
        分析奇偶比分布
        
        Args:
            data: 数据列表
        
        Returns:
            奇偶比统计结果字典
        """
        ratio_counts = defaultdict(int)
        pattern_counts = defaultdict(int)
        
        for item in data:
            ratio = item.get('odd_even_ratio', '')
            pattern = item.get('odd_even_pattern', '')
            
            if ratio:
                ratio_counts[ratio] += 1
            
            if pattern:
                pattern_counts[pattern] += 1
        
        total = len(data)
        ratio_result = {}
        
        for ratio, count in ratio_counts.items():
            ratio_result[ratio] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return {
            'ratio_distribution': ratio_result,
            'pattern_distribution': dict(pattern_counts)
        }
    
    def analyze_span(self, data):
        """
        分析跨度分布
        
        Args:
            data: 数据列表
        
        Returns:
            跨度统计结果字典
        """
        span_counts = defaultdict(int)
        span_list = []
        
        for item in data:
            span = item.get('span')
            if span is not None:
                span_counts[span] += 1
                span_list.append(span)
        
        total = len(data)
        span_result = {}
        
        for span, count in sorted(span_counts.items()):
            span_result[span] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        # 跨度统计指标
        stats = {
            'min': min(span_list) if span_list else 0,
            'max': max(span_list) if span_list else 0,
            'avg': round(sum(span_list) / len(span_list), 2) if span_list else 0,
            'distribution': span_result
        }
        
        return stats
    
    def analyze_big_small(self, data):
        """
        分析大小比分布（0-4为小，5-9为大）
        
        Args:
            data: 数据列表
        
        Returns:
            大小比统计结果字典
        """
        ratio_counts = defaultdict(int)
        
        for item in data:
            numbers = item['numbers']
            big_count = sum(1 for n in numbers if n >= 5)
            small_count = len(numbers) - big_count
            ratio = f"{big_count}:{small_count}"
            ratio_counts[ratio] += 1
        
        total = len(data)
        ratio_result = {}
        
        for ratio, count in sorted(ratio_counts.items(), key=lambda x: x[1], reverse=True):
            ratio_result[ratio] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return ratio_result
    
    def analyze_repeats(self, data):
        """
        分析重号规律
        
        Args:
            data: 数据列表
        
        Returns:
            重号统计结果字典
        """
        repeat_counts = defaultdict(int)
        consecutive_repeats = []
        
        for i in range(1, len(data)):
            prev_numbers = set(data[i-1]['numbers'])
            curr_numbers = set(data[i]['numbers'])
            repeats = prev_numbers & curr_numbers
            repeat_counts[len(repeats)] += 1
            consecutive_repeats.append(len(repeats))
        
        total = len(data) - 1 if len(data) > 1 else 1
        repeat_result = {}
        
        for repeat_count, count in repeat_counts.items():
            repeat_result[repeat_count] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        if consecutive_repeats:
            avg_repeats = sum(consecutive_repeats) / len(consecutive_repeats)
            max_repeats = max(consecutive_repeats)
            min_repeats = min(consecutive_repeats)
        else:
            avg_repeats = 0
            max_repeats = 0
            min_repeats = 0
        
        return {
            'repeat_distribution': repeat_result,
            'avg_repeats': round(avg_repeats, 2),
            'max_repeats': max_repeats,
            'min_repeats': min_repeats
        }
    
    def analyze_consecutive(self, data):
        """
        分析连号规律
        
        Args:
            data: 数据列表
        
        Returns:
            连号统计结果字典
        """
        consecutive_counts = defaultdict(int)
        
        for item in data:
            numbers = sorted(item['numbers'])
            consecutive_streaks = []
            current_streak = 1
            
            for i in range(1, 5):
                if numbers[i] == numbers[i-1] + 1:
                    current_streak += 1
                else:
                    if current_streak > 1:
                        consecutive_streaks.append(current_streak)
                    current_streak = 1
            
            if current_streak > 1:
                consecutive_streaks.append(current_streak)
            
            max_consecutive = max(consecutive_streaks) if consecutive_streaks else 1
            consecutive_counts[max_consecutive] += 1
        
        total = len(data)
        consecutive_result = {}
        
        for streak, count in consecutive_counts.items():
            consecutive_result[streak] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return consecutive_result
    
    def analyze_trend_data(self, trend_data, history_data):
        """
        分析走势图数据，提取趋势特征
        
        Args:
            trend_data: 走势图数据列表
            history_data: 历史开奖数据列表
        
        Returns:
            趋势分析结果字典
        """
        if not trend_data:
            return {'error': '没有走势图数据'}
        
        # 将趋势数据转换为字典便于查找
        trend_dict = {item['issue']: item.get('trend', {}) for item in trend_data}
        
        trend_features = {
            'hot_numbers': [],
            'cold_numbers': [],
            'trending_up': [],
            'trending_down': [],
            'stable_numbers': [],
            'recent_patterns': []
        }
        
        # 分析近期趋势
        recent_issues = sorted(trend_dict.keys())[-20:]
        
        for pos in range(self.positions):
            pos_trends = []
            for issue in recent_issues:
                if issue in trend_dict:
                    trend = trend_dict[issue]
                    pos_name = self.position_names[pos]
                    if pos_name in trend:
                        try:
                            val = int(trend[pos_name])
                            pos_trends.append(val)
                        except:
                            pass
                    elif pos < len(trend.get('numbers', [])):
                        try:
                            val = int(trend['numbers'][pos])
                            pos_trends.append(val)
                        except:
                            pass
            
            if pos_trends:
                # 计算趋势指标
                avg_value = sum(pos_trends) / len(pos_trends)
                variance = sum((x - avg_value) ** 2 for x in pos_trends) / len(pos_trends)
                std_dev = variance ** 0.5
                
                # 识别热门和冷门号码
                num_counts = defaultdict(int)
                for val in pos_trends:
                    num_counts[val] += 1
                
                if num_counts:
                    sorted_nums = sorted(num_counts.items(), key=lambda x: x[1], reverse=True)
                    hot_num = sorted_nums[0][0]
                    cold_num = sorted_nums[-1][0]
                    
                    trend_features['hot_numbers'].append({
                        'position': pos + 1,
                        'position_name': self.position_names[pos],
                        'number': hot_num,
                        'frequency': sorted_nums[0][1],
                        'trend_type': 'hot'
                    })
                    trend_features['cold_numbers'].append({
                        'position': pos + 1,
                        'position_name': self.position_names[pos],
                        'number': cold_num,
                        'frequency': sorted_nums[-1][1],
                        'trend_type': 'cold'
                    })
        
        # 结合历史数据验证趋势
        if history_data:
            for feature in trend_features['hot_numbers']:
                pos = feature['position'] - 1
                num = feature['number']
                count = sum(1 for item in history_data[-30:] 
                           if pos < len(item['numbers']) and item['numbers'][pos] == num)
                feature['validation_count'] = count
                feature['validation_rate'] = round(count / 30, 2) if 30 > 0 else 0
        
        return trend_features
    
    def analyze_data_comparison(self, history_data, trend_data):
        """
        数据对比分析：比较历史数据和走势图数据的一致性
        
        Args:
            history_data: 历史开奖数据列表
            trend_data: 走势图数据列表
        
        Returns:
            数据对比分析结果字典
        """
        if not history_data or not trend_data:
            return {'error': '数据不足，无法进行对比分析'}
        
        trend_dict = {item['issue']: item.get('numbers', []) for item in trend_data}
        
        comparison_results = {
            'matching_rate': 0.0,
            'position_accuracy': [],
            'trend_confirmation': [],
            'discrepancies': []
        }
        
        matched_count = 0
        total_count = 0
        
        for item in history_data[:50]:
            issue = item['issue']
            if issue in trend_dict:
                trend_numbers = trend_dict[issue]
                numbers = item['numbers']
                
                for pos in range(min(self.positions, len(numbers), len(trend_numbers))):
                    try:
                        trend_num = int(trend_numbers[pos])
                        actual_num = numbers[pos]
                        total_count += 1
                        if trend_num == actual_num:
                            matched_count += 1
                    except:
                        pass
        
        comparison_results['matching_rate'] = round(matched_count / total_count, 4) if total_count > 0 else 0
        
        # 位置准确率分析
        for pos in range(self.positions):
            pos_matched = 0
            pos_total = 0
            
            for item in history_data[:50]:
                issue = item['issue']
                if issue in trend_dict:
                    trend_numbers = trend_dict[issue]
                    numbers = item['numbers']
                    
                    if pos < len(numbers) and pos < len(trend_numbers):
                        try:
                            trend_num = int(trend_numbers[pos])
                            actual_num = numbers[pos]
                            pos_total += 1
                            if trend_num == actual_num:
                                pos_matched += 1
                        except:
                            pass
            
            comparison_results['position_accuracy'].append({
                'position': pos + 1,
                'position_name': self.position_names[pos],
                'accuracy': round(pos_matched / pos_total, 4) if pos_total > 0 else 0,
                'matched': pos_matched,
                'total': pos_total
            })
        
        return comparison_results
    
    def calculate_probability(self, data, trend_data=None):
        """
        综合计算概率分析结果，整合走势图数据
        
        Args:
            data: 历史数据列表
            trend_data: 走势图数据列表（可选）
        
        Returns:
            综合分析结果字典
        """
        if len(data) < 10:
            logger.warning('数据量不足，分析结果可能不准确')
        
        # 基础分析
        freq_result = self.analyze_frequency(data)
        interval_result = self.analyze_interval(data)
        hezhi_result = self.analyze_hezhi(data)
        odd_even_result = self.analyze_odd_even(data)
        span_result = self.analyze_span(data)
        big_small_result = self.analyze_big_small(data)
        repeat_result = self.analyze_repeats(data)
        consecutive_result = self.analyze_consecutive(data)
        
        # 趋势分析（如果有走势图数据）
        trend_analysis = {}
        data_comparison = {}
        
        if trend_data:
            trend_analysis = self.analyze_trend_data(trend_data, data)
            data_comparison = self.analyze_data_comparison(data, trend_data)
        
        # 综合预测
        predictions = {}
        total = len(data)
        
        for num in range(0, 10):
            prob_sum = 0
            conf_sum = 0
            trend_factor = 1.0
            
            # 检查趋势数据中的热度
            if trend_analysis and 'hot_numbers' in trend_analysis:
                for hot_item in trend_analysis['hot_numbers']:
                    if hot_item['number'] == num:
                        trend_factor = 1.2 + (hot_item.get('validation_rate') or 0) * 0.5
                        break
                for cold_item in trend_analysis['cold_numbers']:
                    if cold_item['number'] == num:
                        trend_factor = 0.8 - (1 - (cold_item.get('validation_rate') or 0)) * 0.3
                        break
            
            for pos in range(self.positions):
                if num in freq_result:
                    prob = freq_result[num]['probability'][pos]
                    prob_sum += prob * trend_factor
                    
                    key = (num, pos)
                    if key in interval_result:
                        avg_interval = interval_result[key]['avg']
                        last_idx = self._get_last_occurrence(data, num, pos)
                        if last_idx is not None:
                            since_last = len(data) - 1 - last_idx
                            confidence = min(1.0, since_last / avg_interval) if avg_interval > 0 else 0.5
                        else:
                            confidence = 0.5
                        conf_sum += confidence
            
            avg_prob = prob_sum / self.positions if total > 0 else 0
            avg_conf = conf_sum / self.positions if total > 0 else 0.5
            
            predictions[num] = {
                'probability': round(avg_prob, 4),
                'confidence': round(avg_conf, 4),
                'trend_factor': round(trend_factor, 2),
                'expected_positions': self._predict_positions(freq_result, num)
            }
        
        sorted_predictions = dict(sorted(predictions.items(), key=lambda x: x[1]['probability'], reverse=True))
        
        return {
            'frequency': freq_result,
            'interval': interval_result,
            'hezhi': hezhi_result,
            'odd_even': odd_even_result,
            'span': span_result,
            'big_small': big_small_result,
            'repeats': repeat_result,
            'consecutive': consecutive_result,
            'trend': trend_analysis,
            'comparison': data_comparison,
            'predictions': sorted_predictions,
            'total_samples': total,
            'analysis_time': datetime.now().isoformat()
        }
    
    def _get_last_occurrence(self, data, num, pos):
        """获取号码最后一次出现的位置"""
        for idx, item in enumerate(reversed(data)):
            if pos < len(item['numbers']) and item['numbers'][pos] == num:
                return len(data) - 1 - idx
        return None
    
    def _predict_positions(self, freq_result, num):
        """预测号码可能出现的位置"""
        if num not in freq_result:
            return []
        
        prob_list = freq_result[num]['probability'][:self.positions]
        sorted_positions = sorted(range(self.positions), key=lambda i: prob_list[i], reverse=True)
        return [pos + 1 for pos in sorted_positions[:3]]
    
    def generate_report(self, analysis_result):
        """
        生成综合分析报告
        
        Args:
            analysis_result: 分析结果字典
        
        Returns:
            报告字符串
        """
        report = []
        report.append('=' * 70)
        report.append('        排列5数字概率综合分析报告')
        report.append('=' * 70)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 70)
        
        # 一、数字出现频率统计
        report.append('\n【一、数字出现频率统计】')
        report.append('-' * 50)
        for pos in range(self.positions):
            report.append(f'\n{self.position_names[pos]}号码频率:')
            freq_list = []
            for num in range(10):
                if num in analysis_result['frequency']:
                    freq = analysis_result['frequency'][num]['frequency'][pos]
                    prob = analysis_result['frequency'][num]['probability'][pos]
                    freq_list.append((num, freq, prob))
            
            freq_list.sort(key=lambda x: x[1], reverse=True)
            for num, freq, prob in freq_list[:5]:
                report.append(f'  {num}: 出现 {freq} 次, 概率 {prob:.2%}')
        
        # 二、数字间隔周期统计
        report.append('\n\n【二、数字间隔周期统计】')
        report.append('-' * 50)
        
        for pos in range(self.positions):
            report.append(f'\n{self.position_names[pos]}间隔统计:')
            interval_list = []
            for num in range(10):
                key = (num, pos)
                if key in analysis_result['interval']:
                    stats = analysis_result['interval'][key]
                    interval_list.append((num, stats['avg'], stats['current']))
            
            interval_list.sort(key=lambda x: x[2] / x[1] if x[1] > 0 else 0, reverse=True)
            for num, avg, current in interval_list[:5]:
                ratio = current / avg if avg > 0 else 0
                status = '已超期' if ratio > 1.5 else '正常'
                report.append(f'  {num}: 平均间隔 {avg} 期, 当前遗漏 {current} 期 ({status})')
        
        # 三、和值分析
        report.append('\n\n【三、和值分析】')
        report.append('-' * 50)
        hezhi_stats = analysis_result['hezhi']
        report.append(f'\n和值范围: {hezhi_stats["min"]} - {hezhi_stats["max"]}')
        report.append(f'平均和值: {hezhi_stats["avg"]}')
        
        sorted_hezhi = sorted(hezhi_stats['distribution'].items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        report.append('\n出现频率最高的和值:')
        for hezhi, stats in sorted_hezhi:
            report.append(f'  {hezhi}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}')
        
        # 四、奇偶比分析
        report.append('\n\n【四、奇偶比分析】')
        report.append('-' * 50)
        odd_even = analysis_result['odd_even']
        sorted_ratio = sorted(odd_even['ratio_distribution'].items(), key=lambda x: x[1]['count'], reverse=True)
        report.append('\n奇偶比分布:')
        for ratio, stats in sorted_ratio:
            report.append(f'  {ratio}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}')
        
        # 五、跨度分析
        report.append('\n\n【五、跨度分析】')
        report.append('-' * 50)
        span_stats = analysis_result['span']
        report.append(f'\n跨度范围: {span_stats["min"]} - {span_stats["max"]}')
        report.append(f'平均跨度: {span_stats["avg"]}')
        
        sorted_span = sorted(span_stats['distribution'].items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        report.append('\n出现频率最高的跨度:')
        for span, stats in sorted_span:
            report.append(f'  {span}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}')
        
        # 六、大小比分析
        report.append('\n\n【六、大小比分析】')
        report.append('-' * 50)
        big_small = analysis_result['big_small']
        sorted_big_small = sorted(big_small.items(), key=lambda x: x[1]['count'], reverse=True)
        report.append('\n大小比分布（大:小）:')
        for ratio, stats in sorted_big_small:
            report.append(f'  {ratio}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}')
        
        # 七、重号与连号分析
        report.append('\n\n【七、重号与连号分析】')
        report.append('-' * 50)
        repeats = analysis_result['repeats']
        report.append(f'\n平均重号数: {repeats["avg_repeats"]}')
        report.append(f'最大重号数: {repeats["max_repeats"]}')
        
        consecutive = analysis_result['consecutive']
        report.append('\n连号分布:')
        for streak, stats in sorted(consecutive.items()):
            report.append(f'  {streak}连号: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}')
        
        # 八、综合预测
        report.append('\n\n【八、综合预测】')
        report.append('-' * 50)
        predictions = analysis_result['predictions']
        
        report.append('\n热门号码（概率最高）:')
        hot_numbers = list(predictions.items())[:5]
        for num, pred in hot_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report.append(f'  数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        report.append('\n冷门号码（概率最低）:')
        cold_numbers = list(predictions.items())[-5:]
        for num, pred in cold_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report.append(f'  数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        # 九、趋势分析（如果有）
        if analysis_result.get('trend') and 'error' not in analysis_result['trend']:
            report.append('\n\n【九、走势图趋势分析】')
            report.append('-' * 50)
            trend = analysis_result['trend']
            
            if 'hot_numbers' in trend:
                report.append('\n各位置热门号码:')
                for hot_item in trend['hot_numbers']:
                    validation = f' (近30期验证: {hot_item.get("validation_count", 0)}次)'
                    report.append(f'  {hot_item["position_name"]}: 数字 {hot_item["number"]}, 出现 {hot_item["frequency"]} 次{validation}')
        
        report.append('\n\n' + '=' * 70)
        report.append(f'报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        report.append('=' * 70)
        
        return '\n'.join(report)


def test_analyzer():
    """测试分析器功能"""
    # 模拟测试数据
    test_data = [
        {'issue': '2026001', 'date': '2026-01-01', 'numbers': [1, 2, 3, 4, 5], 'hezhi': 15, 'span': 4, 'odd_even_ratio': '2:3'},
        {'issue': '2026002', 'date': '2026-01-02', 'numbers': [6, 7, 8, 9, 0], 'hezhi': 30, 'span': 9, 'odd_even_ratio': '3:2'},
        {'issue': '2026003', 'date': '2026-01-03', 'numbers': [1, 3, 5, 7, 9], 'hezhi': 25, 'span': 8, 'odd_even_ratio': '5:0'},
        {'issue': '2026004', 'date': '2026-01-04', 'numbers': [0, 2, 4, 6, 8], 'hezhi': 20, 'span': 8, 'odd_even_ratio': '0:5'},
        {'issue': '2026005', 'date': '2026-01-05', 'numbers': [5, 5, 5, 5, 5], 'hezhi': 25, 'span': 0, 'odd_even_ratio': '5:0'},
    ]
    
    analyzer = P5Analyzer()
    
    print('=== 测试分析器 ===')
    result = analyzer.calculate_probability(test_data)
    
    print(f'\n分析样本数: {result["total_samples"]}')
    print(f'和值范围: {result["hezhi"]["min"]} - {result["hezhi"]["max"]}')
    print(f'平均和值: {result["hezhi"]["avg"]}')
    
    print('\n热门号码:')
    for num, pred in list(result['predictions'].items())[:3]:
        print(f'  数字 {num}: 概率 {pred["probability"]:.2%}')


if __name__ == '__main__':
    test_analyzer()
