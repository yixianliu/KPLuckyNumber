import logging
from collections import defaultdict

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
    def __init__(self):
        self.positions = 7
        self.main_range = range(0, 10)
        self.special_range = range(0, 16)
    
    def analyze_frequency(self, data):
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
    
    def calculate_probability(self, data):
        if len(data) < 10:
            logger.warning('数据量不足，分析结果可能不准确')
        
        freq_result = self.analyze_frequency(data)
        interval_result = self.analyze_interval(data)
        
        predictions = {}
        total = len(data)
        
        for num in range(0, 10):
            prob_sum = 0
            conf_sum = 0
            for pos in range(6):
                if num in freq_result:
                    prob = freq_result[num]['probability'][pos]
                    prob_sum += prob
                    
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
                'expected_positions': self._predict_positions(freq_result, num)
            }
        
        sorted_predictions = dict(sorted(predictions.items(), key=lambda x: x[1]['probability'], reverse=True))
        
        return {
            'frequency': freq_result,
            'interval': interval_result,
            'predictions': sorted_predictions,
            'total_samples': total
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
        report = []
        report.append('=' * 60)
        report.append('        七星彩数字概率分析报告')
        report.append('=' * 60)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append('-' * 60)
        
        report.append('\n一、数字出现频率统计')
        report.append('-' * 40)
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
        
        report.append('\n二、数字概率预测排序')
        report.append('-' * 40)
        report.append(f'{"数字":^6} {"概率":^10} {"置信度":^10} {"推荐位置"}')
        report.append('-' * 40)
        for num, pred in analysis_result['predictions'].items():
            positions = ','.join(map(str, pred['expected_positions']))
            report.append(f'{num:^6} {pred["probability"]:^10.2%} {pred["confidence"]:^10.2%}  {positions}')
        
        report.append('\n三、间隔周期统计')
        report.append('-' * 40)
        interval_summary = []
        for (num, pos), stats in analysis_result['interval'].items():
            interval_summary.append((num, pos, stats['avg']))
        
        interval_summary.sort(key=lambda x: x[2], reverse=True)
        report.append('近期可能出现的号码（间隔周期较长）:')
        for num, pos, avg_interval in interval_summary[:10]:
            report.append(f'  数字 {num} 在位置 {pos + 1}: 平均间隔 {avg_interval:.1f} 期')
        
        report.append('\n' + '=' * 60)
        report.append('          分析报告结束')
        report.append('=' * 60)
        
        return '\n'.join(report)

if __name__ == '__main__':
    from database import Database
    
    db = Database()
    if db.connect():
        data = db.query_all()
        db.disconnect()
        
        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data)
            report = analyzer.generate_report(result)
            print(report)
        else:
            print('数据库中没有数据')
