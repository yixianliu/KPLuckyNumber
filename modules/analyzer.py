import logging
import os
import json
from collections import defaultdict
from datetime import datetime

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
    概率分析器类
    
    负责对七星彩数据进行多维度分析，包括频率统计、间隔分析、概率计算等
    支持整合走势图数据进行综合分析
    """
    
    def __init__(self):
        self.positions = 7
        self.main_range = range(0, 10)
        self.special_range = range(0, 16)
    
    def analyze_frequency(self, data):
        """
        分析号码出现频率
        
        Args:
            data: 数据列表
        
        Returns:
            频率分析结果字典
        """
        freq = defaultdict(lambda: [0] * self.positions)
        
        for item in data:
            numbers = item['numbers']
            for i in range(min(self.positions, len(numbers))):
                num = numbers[i]
                if i < 6:
                    if 0 <= num <= 9:
                        freq[num][i] += 1
                else:
                    freq[num][i] += 1
        
        total = len(data)
        freq_prob = {}
        
        for num in sorted(freq.keys()):
            prob_list = []
            for i in range(self.positions):
                prob = freq[num][i] / total if total > 0 else 0
                prob_list.append(round(prob, 4))
            freq_prob[num] = {
                'frequency': freq[num],
                'probability': prob_list
            }
        
        return freq_prob
    
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
        
        interval_stats = {}
        for (num, pos), interval_list in intervals.items():
            if interval_list:
                avg_interval = sum(interval_list) / len(interval_list)
                max_interval = max(interval_list)
                min_interval = min(interval_list)
                interval_stats[(num, pos)] = {
                    'count': len(interval_list),
                    'avg': round(avg_interval, 2),
                    'max': max_interval,
                    'min': min_interval
                }
        
        return interval_stats
    
    def analyze_hezhi(self, data):
        """
        分析和值分布
        
        Args:
            data: 数据列表
        
        Returns:
            和值统计结果字典
        """
        hezhi_counts = defaultdict(int)
        hezhi_type_counts = defaultdict(int)
        
        for item in data:
            hezhi = item.get('hezhi', '')
            hezhi_type = item.get('hezhi_type', '')
            
            if hezhi.isdigit():
                hezhi_counts[int(hezhi)] += 1
            
            if hezhi_type:
                hezhi_type_counts[hezhi_type] += 1
        
        total = len(data)
        hezhi_prob = {}
        for hezhi, count in hezhi_counts.items():
            hezhi_prob[hezhi] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return {
            'hezhi_distribution': hezhi_prob,
            'hezhi_type_distribution': dict(hezhi_type_counts)
        }
    
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
        ratio_prob = {}
        for ratio, count in ratio_counts.items():
            ratio_prob[ratio] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return {
            'ratio_distribution': ratio_prob,
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
        
        for item in data:
            span = item.get('span', '')
            if span.isdigit():
                span_counts[int(span)] += 1
        
        total = len(data)
        span_prob = {}
        for span, count in span_counts.items():
            span_prob[span] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return span_prob
    
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
        repeat_prob = {}
        for repeat_count, count in repeat_counts.items():
            repeat_prob[repeat_count] = {
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
            'repeat_distribution': repeat_prob,
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
            numbers = sorted(item['numbers'][:6])
            consecutive_streaks = []
            current_streak = 1
            
            for i in range(1, 6):
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
        consecutive_prob = {}
        for streak, count in consecutive_counts.items():
            consecutive_prob[streak] = {
                'count': count,
                'probability': round(count / total, 4) if total > 0 else 0
            }
        
        return consecutive_prob
    
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
        trend_dict = {item['issue']: item.get('trend', []) for item in trend_data}
        
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
        
        for pos in range(6):
            pos_trends = []
            for issue in recent_issues:
                if issue in trend_dict and len(trend_dict[issue]) > pos:
                    try:
                        val = int(trend_dict[issue][pos])
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
                for issue in recent_issues:
                    if issue in trend_dict and len(trend_dict[issue]) > pos:
                        try:
                            num = int(trend_dict[issue][pos])
                            num_counts[num] += 1
                        except:
                            pass
                
                if num_counts:
                    sorted_nums = sorted(num_counts.items(), key=lambda x: x[1], reverse=True)
                    hot_num = sorted_nums[0][0]
                    cold_num = sorted_nums[-1][0]
                    
                    trend_features['hot_numbers'].append({
                        'position': pos + 1,
                        'number': hot_num,
                        'frequency': sorted_nums[0][1],
                        'trend_type': 'hot'
                    })
                    trend_features['cold_numbers'].append({
                        'position': pos + 1,
                        'number': cold_num,
                        'frequency': sorted_nums[-1][1],
                        'trend_type': 'cold'
                    })
        
        # 结合历史数据验证趋势
        if history_data:
            for feature in trend_features['hot_numbers']:
                pos = feature['position'] - 1
                num = feature['number']
                count = sum(1 for item in history_data[-30:] if pos < len(item['numbers']) and item['numbers'][pos] == num)
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
        
        trend_dict = {item['issue']: item.get('trend', []) for item in trend_data}
        
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
                trend_values = trend_dict[issue]
                numbers = item['numbers']
                
                for pos in range(min(6, len(numbers), len(trend_values))):
                    try:
                        trend_num = int(trend_values[pos])
                        actual_num = numbers[pos]
                        total_count += 1
                        if trend_num == actual_num:
                            matched_count += 1
                    except:
                        pass
        
        comparison_results['matching_rate'] = round(matched_count / total_count, 4) if total_count > 0 else 0
        
        # 位置准确率分析
        for pos in range(6):
            pos_matched = 0
            pos_total = 0
            
            for item in history_data[:50]:
                issue = item['issue']
                if issue in trend_dict:
                    trend_values = trend_dict[issue]
                    numbers = item['numbers']
                    
                    if pos < len(numbers) and pos < len(trend_values):
                        try:
                            trend_num = int(trend_values[pos])
                            actual_num = numbers[pos]
                            pos_total += 1
                            if trend_num == actual_num:
                                pos_matched += 1
                        except:
                            pass
            
            comparison_results['position_accuracy'].append({
                'position': pos + 1,
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
            
            for pos in range(6):
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
            
            avg_prob = prob_sum / 6 if total > 0 else 0
            avg_conf = conf_sum / 6 if total > 0 else 0.5
            
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
            'repeats': repeat_result,
            'consecutive': consecutive_result,
            'trend': trend_analysis,
            'comparison': data_comparison,
            'predictions': sorted_predictions,
            'total_samples': total,
            'analysis_time': datetime.now().isoformat()
        }
    
    def _get_last_occurrence(self, data, num, pos):
        for idx, item in enumerate(reversed(data)):
            if pos < len(item['numbers']) and item['numbers'][pos] == num:
                return len(data) - 1 - idx
        return None
    
    def _predict_positions(self, freq_result, num):
        if num not in freq_result:
            return []
        
        prob_list = freq_result[num]['probability'][:6]
        sorted_positions = sorted(range(6), key=lambda i: prob_list[i], reverse=True)
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
        report.append('        七星彩数字概率综合分析报告')
        report.append('=' * 70)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 70)
        
        # 一、数字出现频率统计
        report.append('\n【一、数字出现频率统计】')
        report.append('-' * 50)
        for pos in range(6):
            report.append(f'\n位置 {pos + 1} 号码频率:')
            freq_list = []
            for num in range(10):
                if num in analysis_result['frequency']:
                    freq = analysis_result['frequency'][num]['frequency'][pos]
                    prob = analysis_result['frequency'][num]['probability'][pos]
                    freq_list.append((num, freq, prob))
            
            freq_list.sort(key=lambda x: x[1], reverse=True)
            for num, freq, prob in freq_list[:5]:
                report.append(f'  {num}: 出现 {freq} 次, 概率 {prob:.2%}')
        
        # 二、数字概率预测排序
        report.append('\n【二、数字概率预测排序】')
        report.append('-' * 50)
        report.append(f'{"数字":^6} {"概率":^10} {"置信度":^10} {"趋势因子":^12} {"推荐位置"}')
        report.append('-' * 50)
        for num, pred in analysis_result['predictions'].items():
            positions = ','.join(map(str, pred['expected_positions']))
            report.append(f'{num:^6} {pred["probability"]:^10.2%} {pred["confidence"]:^10.2%} {pred["trend_factor"]:^12.2f}  {positions}')
        
        # 三、间隔周期统计
        report.append('\n【三、间隔周期统计】')
        report.append('-' * 50)
        interval_summary = []
        for (num, pos), stats in analysis_result['interval'].items():
            interval_summary.append((num, pos, stats['avg']))
        
        interval_summary.sort(key=lambda x: x[2], reverse=True)
        report.append('近期可能出现的号码（间隔周期较长）:')
        for num, pos, avg_interval in interval_summary[:10]:
            report.append(f'  数字 {num} 在位置 {pos + 1}: 平均间隔 {avg_interval:.1f} 期')
        
        # 四、重号与连号分析
        report.append('\n【四、重号与连号分析】')
        report.append('-' * 50)
        
        if 'repeats' in analysis_result:
            repeats = analysis_result['repeats']
            report.append(f'\n重号统计:')
            report.append(f'  平均重号数: {repeats["avg_repeats"]:.1f}')
            report.append(f'  最大重号数: {repeats["max_repeats"]}')
            report.append(f'  最小重号数: {repeats["min_repeats"]}')
            
            report.append('\n重号分布:')
            for repeat_count, stats in sorted(repeats['repeat_distribution'].items()):
                report.append(f'  {repeat_count}个重号: {stats["count"]}次 ({stats["probability"]:.2%})')
        
        if 'consecutive' in analysis_result:
            report.append('\n连号分布:')
            for streak, stats in sorted(analysis_result['consecutive'].items()):
                report.append(f'  {streak}连号: {stats["count"]}次 ({stats["probability"]:.2%})')
        
        # 五、趋势分析（如果有走势图数据）
        if 'trend' in analysis_result and analysis_result['trend']:
            report.append('\n【五、走势图趋势分析】')
            report.append('-' * 50)
            
            if 'hot_numbers' in analysis_result['trend']:
                report.append('\n热门号码（近期高频出现）:')
                for hot_item in analysis_result['trend']['hot_numbers']:
                    report.append(f'  位置{hot_item["position"]} 数字{hot_item["number"]}: 出现{hot_item["frequency"]}次')
            
            if 'cold_numbers' in analysis_result['trend']:
                report.append('\n冷门号码（近期低频出现）:')
                for cold_item in analysis_result['trend']['cold_numbers']:
                    report.append(f'  位置{cold_item["position"]} 数字{cold_item["number"]}: 出现{cold_item["frequency"]}次')
        
        # 六、数据对比分析
        if 'comparison' in analysis_result and analysis_result['comparison'] and 'matching_rate' in analysis_result['comparison']:
            report.append('\n【六、数据对比分析】')
            report.append('-' * 50)
            
            comp = analysis_result['comparison']
            report.append(f'\n走势图与开奖数据匹配率: {comp["matching_rate"]:.2%}')
            
            report.append('\n各位置准确率:')
            for pos_acc in comp['position_accuracy']:
                report.append(f'  位置{pos_acc["position"]}: {pos_acc["accuracy"]:.2%} ({pos_acc["matched"]}/{pos_acc["total"]})')
        
        report.append('\n' + '=' * 70)
        report.append('          分析报告结束')
        report.append('=' * 70)
        
        return '\n'.join(report)

if __name__ == '__main__':
    from database import Database
    
    db = Database()
    if db.connect():
        data = db.query_all_qxc_data()
        # 查询走势图数据
        try:
            db.cursor.execute('SELECT * FROM qxc_trend_data')
            trend_data = db.cursor.fetchall()
            # 转换格式
            trend_data = [{'issue': item['issue'], 'trend': json.loads(item['trend_values'])} for item in trend_data]
        except:
            trend_data = []
        db.disconnect()
        
        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data, trend_data)
            report = analyzer.generate_report(result)
            print(report)
        else:
            print('数据库中没有数据')