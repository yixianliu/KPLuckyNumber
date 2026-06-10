import os
import logging
import matplotlib.pyplot as plt
from datetime import datetime
import io

# 确保日志目录存在
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/report_generator.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ReportGenerator:
    """
    报告生成器类
    
    负责生成详细分析报告和最优选择报告，支持图表生成
    支持整合走势图数据进行综合分析报告生成
    """
    
    def __init__(self, output_dir='reports/'):
        self.output_dir = output_dir
        self._ensure_dir()
    
    def _ensure_dir(self):
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def _generate_frequency_chart_bytes(self, freq_data):
        """生成频率分布图并返回字节流"""
        try:
            plt.figure(figsize=(12, 8))
            
            for pos in range(6):
                x = list(range(10))
                y = []
                for num in x:
                    if num in freq_data:
                        y.append(freq_data[num]['frequency'][pos])
                    else:
                        y.append(0)
                
                plt.subplot(2, 3, pos + 1)
                plt.bar(x, y, color='skyblue')
                plt.title(f'位置 {pos + 1} 号码频率分布')
                plt.xlabel('数字')
                plt.ylabel('出现次数')
                plt.xticks(x)
                plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            plt.tight_layout()
            
            # 转换为字节流
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()
            
            logger.info('频率分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成频率分布图失败: {e}')
            return None
    
    def _generate_probability_chart_bytes(self, predictions):
        """生成概率分布图并返回字节流"""
        try:
            plt.figure(figsize=(10, 6))
            
            numbers = list(predictions.keys())
            probabilities = [pred['probability'] for pred in predictions.values()]
            confidences = [pred['confidence'] for pred in predictions.values()]
            
            x = range(len(numbers))
            width = 0.35
            
            plt.bar([i - width/2 for i in x], probabilities, width, label='概率', color='blue')
            plt.bar([i + width/2 for i in x], confidences, width, label='置信度', color='orange')
            
            plt.title('数字概率与置信度分布')
            plt.xlabel('数字')
            plt.ylabel('概率/置信度')
            plt.xticks(x, numbers)
            plt.legend()
            plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            # 转换为字节流
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()
            
            logger.info('概率分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成概率分布图失败: {e}')
            return None
    
    def _generate_trend_chart_bytes(self, trend_analysis):
        """生成趋势分析图并返回字节流"""
        try:
            if not trend_analysis or 'hot_numbers' not in trend_analysis:
                return None
            
            plt.figure(figsize=(10, 6))
            
            hot_positions = [item['position'] for item in trend_analysis['hot_numbers']]
            hot_numbers = [item['number'] for item in trend_analysis['hot_numbers']]
            hot_freqs = [item['frequency'] for item in trend_analysis['hot_numbers']]
            
            cold_positions = [item['position'] for item in trend_analysis['cold_numbers']]
            cold_numbers = [item['number'] for item in trend_analysis['cold_numbers']]
            cold_freqs = [item['frequency'] for item in trend_analysis['cold_numbers']]
            
            x_hot = [p + 0.15 for p in hot_positions]
            x_cold = [p - 0.15 for p in cold_positions]
            
            plt.bar(x_hot, hot_freqs, width=0.3, label='热门号码', color='red')
            plt.bar(x_cold, cold_freqs, width=0.3, label='冷门号码', color='blue')
            
            plt.title('各位置冷热号码频率对比')
            plt.xlabel('位置')
            plt.ylabel('出现频率')
            plt.xticks(range(1, 7))
            plt.legend()
            plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            # 转换为字节流
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()
            
            logger.info('趋势分析图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成趋势分析图失败: {e}')
            return None
    
    def generate_detailed_report(self, analysis_result, analyzer):
        """
        生成详细分析报告
        
        Args:
            analysis_result: 分析结果字典
            analyzer: 分析器实例
        
        Returns:
            包含报告内容和图表的字典
        """
        logger.info('开始生成详细分析报告')
        
        report_content = analyzer.generate_report(analysis_result)
        
        # 添加扩展字段分析
        if 'hezhi' in analysis_result:
            report_content += '\n\n【七、和值分析】'
            report_content += '\n' + '-' * 50
            hezhi_dist = analysis_result['hezhi']['hezhi_distribution']
            sorted_hezhi = sorted(hezhi_dist.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
            report_content += '\n出现频率最高的和值:'
            for hezhi, stats in sorted_hezhi:
                report_content += f'\n  {hezhi}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
            
            if analysis_result['hezhi']['hezhi_type_distribution']:
                report_content += '\n\n和值类型分布:'
                for hezhi_type, count in analysis_result['hezhi']['hezhi_type_distribution'].items():
                    prob = count / analysis_result['total_samples'] if analysis_result['total_samples'] > 0 else 0
                    report_content += f'\n  {hezhi_type}: {count} 次 ({prob:.2%})'
        
        if 'odd_even' in analysis_result:
            report_content += '\n\n【八、奇偶比分析】'
            report_content += '\n' + '-' * 50
            ratio_dist = analysis_result['odd_even']['ratio_distribution']
            sorted_ratio = sorted(ratio_dist.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
            report_content += '\n出现频率最高的奇偶比:'
            for ratio, stats in sorted_ratio:
                report_content += f'\n  {ratio}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
            
            # 添加奇偶模式分析
            if analysis_result['odd_even']['pattern_distribution']:
                report_content += '\n\n奇偶模式分布（前5种）:'
                sorted_patterns = sorted(analysis_result['odd_even']['pattern_distribution'].items(), key=lambda x: x[1], reverse=True)[:5]
                for pattern, count in sorted_patterns:
                    prob = count / analysis_result['total_samples'] if analysis_result['total_samples'] > 0 else 0
                    report_content += f'\n  {pattern}: {count} 次 ({prob:.2%})'
        
        if 'span' in analysis_result:
            report_content += '\n\n【九、跨度分析】'
            report_content += '\n' + '-' * 50
            span_dist = analysis_result['span']
            sorted_span = sorted(span_dist.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
            report_content += '\n出现频率最高的跨度:'
            for span, stats in sorted_span:
                report_content += f'\n  {span}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
        
        # 添加关键指标解读
        report_content += '\n\n【十、关键指标解读】'
        report_content += '\n' + '-' * 50
        
        # 计算综合指标
        predictions = analysis_result['predictions']
        avg_probability = sum(pred['probability'] for pred in predictions.values()) / len(predictions)
        avg_confidence = sum(pred['confidence'] for pred in predictions.values()) / len(predictions)
        avg_trend_factor = sum(pred.get('trend_factor', 1.0) for pred in predictions.values()) / len(predictions)
        
        report_content += f'\n1. 整体概率水平: {avg_probability:.2%}'
        report_content += f'\n2. 平均置信度: {avg_confidence:.2%}'
        report_content += f'\n3. 平均趋势因子: {avg_trend_factor:.2f}'
        
        # 数据质量评估
        data_quality = '优秀' if analysis_result['total_samples'] >= 500 else '良好' if analysis_result['total_samples'] >= 200 else '一般'
        report_content += f'\n4. 数据样本质量: {data_quality} ({analysis_result["total_samples"]} 期)'
        
        # 走势图数据可用性
        has_trend_data = 'trend' in analysis_result and analysis_result['trend'] and 'error' not in analysis_result['trend']
        report_content += f'\n5. 走势图数据: {"已整合" if has_trend_data else "未获取"}'
        
        if has_trend_data and 'comparison' in analysis_result and analysis_result['comparison']:
            matching_rate = analysis_result['comparison'].get('matching_rate', 0)
            report_content += f'\n6. 数据一致性匹配率: {matching_rate:.2%}'
        
        report_content += f'\n\n生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
        
        # 生成图表字节流
        frequency_chart = self._generate_frequency_chart_bytes(analysis_result['frequency'])
        probability_chart = self._generate_probability_chart_bytes(analysis_result['predictions'])
        trend_chart = self._generate_trend_chart_bytes(analysis_result.get('trend'))
        
        logger.info('详细分析报告生成完成')
        return {
            'report_type': 'detailed',
            'report_content': report_content,
            'frequency_chart': frequency_chart,
            'probability_chart': probability_chart,
            'trend_chart': trend_chart,
            'total_samples': analysis_result['total_samples'],
            'frequency_analysis': str(analysis_result.get('frequency', {})),
            'probability_analysis': str(analysis_result.get('predictions', {})),
            'interval_analysis': str(analysis_result.get('interval', {})),
            'hezhi_analysis': str(analysis_result.get('hezhi', {})),
            'odd_even_analysis': str(analysis_result.get('odd_even', {})),
            'span_analysis': str(analysis_result.get('span', {})),
            'trend_analysis': str(analysis_result.get('trend', {})),
            'comparison_analysis': str(analysis_result.get('comparison', {}))
        }
    
    def generate_optimal_report(self, analysis_result):
        """
        生成最终最优报告
        
        Args:
            analysis_result: 分析结果字典
        
        Returns:
            包含报告内容、推荐号码和图表的字典
        """
        logger.info('开始生成最终最优报告')
        
        report_content = []
        report_content.append('=' * 80)
        report_content.append('           七星彩最终最优分析报告')
        report_content.append('=' * 80)
        report_content.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report_content.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report_content.append('-' * 80)
        
        # ========== 一、数据概览 ==========
        report_content.append('\n【一、数据概览】')
        report_content.append('-' * 60)
        
        report_content.append(f'\n1. 分析期数: {analysis_result["total_samples"]} 期')
        has_trend_data = 'trend' in analysis_result and analysis_result['trend'] and 'error' not in analysis_result['trend']
        report_content.append(f'2. 走势图数据: {"已整合" if has_trend_data else "未获取"}')
        
        if has_trend_data and 'comparison' in analysis_result and analysis_result['comparison']:
            matching_rate = analysis_result['comparison'].get('matching_rate', 0)
            report_content.append(f'3. 数据一致性匹配率: {matching_rate:.2%}')
        
        # ========== 二、关键分析结论 ==========
        report_content.append('\n【二、关键分析结论】')
        report_content.append('-' * 60)
        
        predictions = analysis_result['predictions']
        sorted_predictions = sorted(predictions.items(), key=lambda x: x[1]['probability'], reverse=True)
        
        # 热号分析
        hot_numbers = sorted_predictions[:3]
        report_content.append('\n1. 热门号码（概率最高）:')
        for num, pred in hot_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report_content.append(f'   数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        # 冷号分析
        cold_numbers = sorted_predictions[-3:]
        report_content.append('\n2. 冷门号码（概率最低）:')
        for num, pred in cold_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report_content.append(f'   数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        # 和值趋势
        if 'hezhi' in analysis_result:
            hezhi_dist = analysis_result['hezhi']['hezhi_distribution']
            if hezhi_dist:
                avg_hezhi = sum(k * v['count'] for k, v in hezhi_dist.items()) / sum(v['count'] for v in hezhi_dist.values())
                report_content.append(f'\n3. 和值趋势: 平均值约 {avg_hezhi:.1f}')
        
        # 趋势分析补充
        if 'trend' in analysis_result and analysis_result['trend']:
            report_content.append('\n4. 走势图趋势特征:')
            if 'hot_numbers' in analysis_result['trend']:
                hot_items = analysis_result['trend']['hot_numbers'][:3]
                for hot_item in hot_items:
                    report_content.append(f'   位置{hot_item["position"]} 数字{hot_item["number"]} 呈现上升趋势')
        
        # ========== 三、核心数据指标 ==========
        report_content.append('\n【三、核心数据指标】')
        report_content.append('-' * 60)
        
        # 号码覆盖率
        coverage_rate = len(predictions) / 10 * 100
        report_content.append(f'\n1. 号码覆盖率: {coverage_rate:.1f}%')
        
        # 平均置信度
        avg_confidence = sum(pred['confidence'] for pred in predictions.values()) / len(predictions)
        report_content.append(f'2. 平均置信度: {avg_confidence:.2%}')
        
        # 推荐组合置信度
        rec_confidence = min(pred['confidence'] for num, pred in sorted_predictions[:7])
        report_content.append(f'3. 推荐组合最低置信度: {rec_confidence:.2%}')
        
        # 平均趋势因子
        avg_trend_factor = sum(pred.get('trend_factor', 1.0) for pred in predictions.values()) / len(predictions)
        report_content.append(f'4. 平均趋势因子: {avg_trend_factor:.2f}')
        
        # 重号与连号指标
        if 'repeats' in analysis_result:
            report_content.append(f'5. 平均重号数: {analysis_result["repeats"]["avg_repeats"]:.1f}')
        if 'consecutive' in analysis_result:
            max_streak = max(analysis_result['consecutive'].keys(), default=1)
            report_content.append(f'6. 最大连号长度: {max_streak}')
        
        # ========== 四、数据对比分析 ==========
        if 'comparison' in analysis_result and analysis_result['comparison'] and 'matching_rate' in analysis_result['comparison']:
            report_content.append('\n【四、数据对比分析】')
            report_content.append('-' * 60)
            
            comp = analysis_result['comparison']
            report_content.append(f'\n走势图与开奖数据匹配率: {comp["matching_rate"]:.2%}')
            
            report_content.append('\n各位置准确率对比:')
            for pos_acc in comp['position_accuracy']:
                report_content.append(f'  位置{pos_acc["position"]}: {pos_acc["accuracy"]:.2%} ({pos_acc["matched"]}/{pos_acc["total"]})')
        
        # ========== 五、最终推荐号码 ==========
        report_content.append('\n【五、最终推荐号码】')
        report_content.append('-' * 60)
        
        recommended_numbers = []
        special_num = 0
        
        # 按位置推荐
        for pos in range(6):
            pos_predictions = []
            for num, pred in predictions.items():
                if pos + 1 in pred['expected_positions']:
                    pos_predictions.append((num, pred['probability'], pred['confidence']))
            
            pos_predictions.sort(key=lambda x: x[1], reverse=True)
            
            if pos_predictions:
                best_num = pos_predictions[0][0]
                best_prob = pos_predictions[0][1]
                best_conf = pos_predictions[0][2]
                recommended_numbers.append(str(best_num))
                
                report_content.append(f'\n位置 {pos + 1}:')
                report_content.append(f'   推荐: {best_num} (概率: {best_prob:.2%}, 置信度: {best_conf:.2%})')
                report_content.append(f'   备选: {", ".join([str(n) for n, _, _ in pos_predictions[1:3]])}')
            else:
                all_pos = [(num, pred['probability'], pred['confidence']) for num, pred in predictions.items()]
                all_pos.sort(key=lambda x: x[1], reverse=True)
                best_num = all_pos[0][0]
                recommended_numbers.append(str(best_num))
                
                report_content.append(f'\n位置 {pos + 1}:')
                report_content.append(f'   推荐: {best_num} (概率: {all_pos[0][1]:.2%}, 置信度: {all_pos[0][2]:.2%})')
        
        # 特别号码推荐
        special_num_freq = {}
        for num in range(15):
            if num in analysis_result['frequency']:
                special_num_freq[num] = analysis_result['frequency'][num]['frequency'][6]
        
        sorted_special = sorted(special_num_freq.items(), key=lambda x: x[1], reverse=True)
        if sorted_special:
            special_num = sorted_special[0][0]
            recommended_numbers.append(str(special_num))
            
            report_content.append('\n特别号码:')
            report_content.append(f'   推荐: {special_num} (出现频率: {sorted_special[0][1]} 次)')
            report_content.append(f'   备选: {", ".join([str(n) for n, _ in sorted_special[1:3]])}')
        
        # 综合推荐
        report_content.append('\n【六、综合推荐组合】')
        report_content.append('-' * 60)
        report_content.append(f'\n推荐号码组合: {" ".join(recommended_numbers)}')
        
        # ========== 七、决策建议 ==========
        report_content.append('\n【七、决策建议】')
        report_content.append('-' * 60)
        
        report_content.append('\n1. 核心策略:')
        report_content.append('   - 优先关注概率 > 10% 的号码')
        report_content.append('   - 考虑冷号回补可能性（间隔超过平均2倍）')
        report_content.append('   - 结合奇偶比和和值趋势综合判断')
        
        if has_trend_data:
            report_content.append('\n2. 趋势参考:')
            report_content.append('   - 热门号码可适当增加关注度')
            report_content.append('   - 冷门号码需谨慎选择')
        
        report_content.append('\n3. 投注建议:')
        report_content.append('   - 可选择推荐组合进行投注')
        report_content.append('   - 建议设置合理预算，理性购彩')
        report_content.append('   - 可考虑多组合覆盖策略')
        
        report_content.append('\n4. 风险提示:')
        report_content.append('   - 彩票开奖具有随机性，历史数据仅供参考')
        report_content.append('   - 本报告基于统计分析，不保证中奖结果')
        report_content.append('   - 请理性购彩，量力而行')
        
        report_content.append('\n' + '=' * 80)
        report_content.append('            最终最优报告结束')
        report_content.append('=' * 80)
        
        # 生成图表
        frequency_chart = self._generate_frequency_chart_bytes(analysis_result['frequency'])
        probability_chart = self._generate_probability_chart_bytes(analysis_result['predictions'])
        trend_chart = self._generate_trend_chart_bytes(analysis_result.get('trend'))
        
        logger.info('最终最优报告生成完成')
        
        return {
            'report_type': 'optimal',
            'report_content': '\n'.join(report_content),
            'recommended_numbers': ' '.join(recommended_numbers),
            'confidence_score': round(avg_confidence, 4),
            'analysis_summary': f'分析{analysis_result["total_samples"]}期数据，推荐号码: {" ".join(recommended_numbers)}',
            'frequency_chart': frequency_chart,
            'probability_chart': probability_chart,
            'trend_chart': trend_chart,
            'key_metrics': {
                'avg_confidence': round(avg_confidence, 4),
                'avg_trend_factor': round(avg_trend_factor, 4),
                'data_matching_rate': analysis_result['comparison'].get('matching_rate', 0) if analysis_result.get('comparison') else 0
            }
        }

if __name__ == '__main__':
    import sys
    import json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from database import Database
    from analyzer import ProbabilityAnalyzer
    
    db = Database()
    if db.connect():
        data = db.query_all_qxc_data()
        # 查询走势图数据
        try:
            db.cursor.execute('SELECT * FROM qxc_trend_data')
            trend_data = db.cursor.fetchall()
            trend_data = [{'issue': item['issue'], 'trend': json.loads(item['trend_values'])} for item in trend_data]
        except:
            trend_data = []
        db.disconnect()
        
        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data, trend_data)
            
            generator = ReportGenerator()
            
            # 生成详细报告
            detailed = generator.generate_detailed_report(result, analyzer)
            print(f'详细报告长度: {len(detailed["report_content"])} 字符')
            
            # 生成最优报告
            optimal = generator.generate_optimal_report(result)
            print(f'最优报告长度: {len(optimal["report_content"])} 字符')
            print('报告生成完成')
        else:
            print('数据库中没有数据')