"""
排列5报告生成模块

负责生成详细分析报告和最优选择报告，支持图表生成
支持整合走势图数据进行综合分析报告生成
"""

import os
import logging
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
import io

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

os.makedirs('logs', exist_ok=True)
os.makedirs('reports', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/report_generator_p5.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5ReportGenerator:
    """
    排列5报告生成器类
    
    负责生成详细分析报告和最优选择报告，支持图表生成
    支持整合走势图数据进行综合分析报告生成
    """
    
    def __init__(self, output_dir='reports/'):
        """初始化报告生成器"""
        self.output_dir = output_dir
        self._ensure_dir()
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
    
    def _ensure_dir(self):
        """确保输出目录存在"""
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def _generate_frequency_chart_bytes(self, freq_data):
        """生成频率分布图并返回字节流"""
        try:
            plt.figure(figsize=(14, 10))
            
            for pos in range(5):
                plt.subplot(2, 3, pos + 1)
                x = list(range(10))
                y = []
                for num in x:
                    if num in freq_data:
                        y.append(freq_data[num]['frequency'][pos])
                    else:
                        y.append(0)
                
                colors = ['#FF6B6B' if val == max(y) else '#4ECDC4' if val == min(y) else '#45B7D1' for val in y]
                bars = plt.bar(x, y, color=colors)
                plt.title(f'{self.position_names[pos]}号码频率分布', fontsize=12, fontweight='bold')
                plt.xlabel('数字', fontsize=10)
                plt.ylabel('出现次数', fontsize=10)
                plt.xticks(x)
                plt.grid(axis='y', linestyle='--', alpha=0.7)
                
                # 在柱状图上显示数值
                for bar, val in zip(bars, y):
                    if val > 0:
                        plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5, 
                                str(val), ha='center', va='bottom', fontsize=8)
            
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
            plt.figure(figsize=(12, 6))
            
            numbers = list(predictions.keys())
            probabilities = [pred['probability'] for pred in predictions.values()]
            confidences = [pred['confidence'] for pred in predictions.values()]
            
            x = range(len(numbers))
            width = 0.35
            
            fig, ax = plt.subplots(figsize=(12, 6))
            bars1 = ax.bar([i - width/2 for i in x], probabilities, width, label='概率', color='#3498DB')
            bars2 = ax.bar([i + width/2 for i in x], confidences, width, label='置信度', color='#E74C3C')
            
            ax.set_title('数字概率与置信度分布', fontsize=14, fontweight='bold')
            ax.set_xlabel('数字', fontsize=12)
            ax.set_ylabel('概率/置信度', fontsize=12)
            ax.set_xticks(x)
            ax.set_xticklabels(numbers)
            ax.legend()
            ax.grid(axis='y', linestyle='--', alpha=0.7)
            
            plt.tight_layout()
            
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
            
            fig, axes = plt.subplots(1, 2, figsize=(14, 6))
            
            # 热门号码图
            hot_positions = [item['position_name'] for item in trend_analysis['hot_numbers']]
            hot_numbers = [item['number'] for item in trend_analysis['hot_numbers']]
            hot_freqs = [item['frequency'] for item in trend_analysis['hot_numbers']]
            
            colors_hot = ['#FF6B6B' for _ in hot_positions]
            bars1 = axes[0].bar(range(len(hot_positions)), hot_freqs, color=colors_hot)
            axes[0].set_title('各位置热门号码频率', fontsize=12, fontweight='bold')
            axes[0].set_xlabel('位置', fontsize=10)
            axes[0].set_ylabel('出现频率', fontsize=10)
            axes[0].set_xticks(range(len(hot_positions)))
            axes[0].set_xticklabels(hot_positions)
            axes[0].grid(axis='y', linestyle='--', alpha=0.7)
            
            # 在柱状图上显示号码
            for bar, num in zip(bars1, hot_numbers):
                axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2, 
                            f'{num}', ha='center', va='bottom', fontsize=10, fontweight='bold')
            
            # 冷门号码图
            cold_positions = [item['position_name'] for item in trend_analysis['cold_numbers']]
            cold_numbers = [item['number'] for item in trend_analysis['cold_numbers']]
            cold_freqs = [item['frequency'] for item in trend_analysis['cold_numbers']]
            
            colors_cold = ['#4ECDC4' for _ in cold_positions]
            bars2 = axes[1].bar(range(len(cold_positions)), cold_freqs, color=colors_cold)
            axes[1].set_title('各位置冷门号码频率', fontsize=12, fontweight='bold')
            axes[1].set_xlabel('位置', fontsize=10)
            axes[1].set_ylabel('出现频率', fontsize=10)
            axes[1].set_xticks(range(len(cold_positions)))
            axes[1].set_xticklabels(cold_positions)
            axes[1].grid(axis='y', linestyle='--', alpha=0.7)
            
            # 在柱状图上显示号码
            for bar, num in zip(bars2, cold_numbers):
                axes[1].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2, 
                            f'{num}', ha='center', va='bottom', fontsize=10, fontweight='bold')
            
            plt.tight_layout()
            
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
    
    def _generate_hezhi_chart_bytes(self, hezhi_data):
        """生成和值分布图并返回字节流"""
        try:
            distribution = hezhi_data.get('distribution', {})
            if not distribution:
                return None
            
            plt.figure(figsize=(14, 6))
            
            hezhi_values = sorted(distribution.keys())
            counts = [distribution[h]['count'] for h in hezhi_values]
            
            plt.bar(hezhi_values, counts, color='#9B59B6', alpha=0.7)
            plt.axhline(y=sum(counts)/len(counts), color='red', linestyle='--', label='平均值')
            
            plt.title('和值分布统计', fontsize=14, fontweight='bold')
            plt.xlabel('和值', fontsize=12)
            plt.ylabel('出现次数', fontsize=12)
            plt.legend()
            plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            plt.tight_layout()
            
            # 转换为字节流
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()
            
            logger.info('和值分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成和值分布图失败: {e}')
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
        logger.info('开始生成排列5详细分析报告')
        
        report_content = analyzer.generate_report(analysis_result)
        
        # 添加扩展字段分析
        if 'hezhi' in analysis_result:
            report_content += '\n\n【十、和值深度分析】'
            report_content += '\n' + '-' * 50
            hezhi_dist = analysis_result['hezhi']['distribution']
            sorted_hezhi = sorted(hezhi_dist.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
            report_content += '\n出现频率最高的和值:'
            for hezhi, stats in sorted_hezhi:
                report_content += f'\n  {hezhi}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
        
        if 'odd_even' in analysis_result:
            report_content += '\n\n【十一、奇偶比深度分析】'
            report_content += '\n' + '-' * 50
            ratio_dist = analysis_result['odd_even']['ratio_distribution']
            sorted_ratio = sorted(ratio_dist.items(), key=lambda x: x[1]['count'], reverse=True)
            report_content += '\n奇偶比分布:'
            for ratio, stats in sorted_ratio:
                report_content += f'\n  {ratio}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
            
            if analysis_result['odd_even']['pattern_distribution']:
                report_content += '\n\n奇偶模式分布（前5种）:'
                sorted_patterns = sorted(analysis_result['odd_even']['pattern_distribution'].items(), key=lambda x: x[1], reverse=True)[:5]
                for pattern, count in sorted_patterns:
                    prob = count / analysis_result['total_samples'] if analysis_result['total_samples'] > 0 else 0
                    report_content += f'\n  {pattern}: {count} 次 ({prob:.2%})'
        
        if 'span' in analysis_result:
            report_content += '\n\n【十二、跨度深度分析】'
            report_content += '\n' + '-' * 50
            span_dist = analysis_result['span']['distribution']
            sorted_span = sorted(span_dist.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
            report_content += '\n出现频率最高的跨度:'
            for span, stats in sorted_span:
                report_content += f'\n  {span}: 出现 {stats["count"]} 次, 概率 {stats["probability"]:.2%}'
        
        # 添加关键指标解读
        report_content += '\n\n【十三、关键指标解读】'
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
        hezhi_chart = self._generate_hezhi_chart_bytes(analysis_result.get('hezhi', {}))
        
        logger.info('详细分析报告生成完成')
        return {
            'report_type': 'detailed',
            'report_content': report_content,
            'frequency_chart': frequency_chart,
            'probability_chart': probability_chart,
            'trend_chart': trend_chart,
            'hezhi_chart': hezhi_chart,
            'total_samples': analysis_result['total_samples'],
            'frequency_analysis': str(analysis_result.get('frequency', {})),
            'probability_analysis': str(analysis_result.get('predictions', {})),
            'interval_analysis': str(analysis_result.get('interval', {})),
            'hezhi_analysis': str(analysis_result.get('hezhi', {})),
            'odd_even_analysis': str(analysis_result.get('odd_even', {})),
            'span_analysis': str(analysis_result.get('span', {})),
            'big_small_analysis': str(analysis_result.get('big_small', {})),
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
        logger.info('开始生成排列5最终最优报告')
        
        report_content = []
        report_content.append('=' * 80)
        report_content.append('           排列5最终最优分析报告')
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
        hot_numbers = sorted_predictions[:5]
        report_content.append('\n1. 热门号码（概率最高）:')
        for num, pred in hot_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report_content.append(f'   数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        # 冷号分析
        cold_numbers = sorted_predictions[-5:]
        report_content.append('\n2. 冷门号码（概率最低）:')
        for num, pred in cold_numbers:
            trend_info = f', 趋势因子 {pred["trend_factor"]:.2f}' if 'trend_factor' in pred else ''
            report_content.append(f'   数字 {num}: 概率 {pred["probability"]:.2%}, 置信度 {pred["confidence"]:.2%}{trend_info}')
        
        # 和值趋势
        if 'hezhi' in analysis_result:
            hezhi_stats = analysis_result['hezhi']
            report_content.append(f'\n3. 和值趋势: 范围 {hezhi_stats["min"]}-{hezhi_stats["max"]}, 平均值约 {hezhi_stats["avg"]}')
        
        # 趋势分析补充
        if 'trend' in analysis_result and analysis_result['trend'] and 'hot_numbers' in analysis_result['trend']:
            report_content.append('\n4. 走势图趋势特征:')
            for hot_item in analysis_result['trend']['hot_numbers'][:3]:
                report_content.append(f'   {hot_item["position_name"]} 数字{hot_item["number"]} 呈现上升趋势')
        
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
        rec_confidence = min(pred['confidence'] for num, pred in sorted_predictions[:5])
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
                report_content.append(f'  {pos_acc["position_name"]}: {pos_acc["accuracy"]:.2%} ({pos_acc["matched"]}/{pos_acc["total"]})')
        
        # ========== 五、最终推荐号码 ==========
        report_content.append('\n【五、最终推荐号码】')
        report_content.append('-' * 60)
        
        recommended_numbers = []
        
        # 按位置推荐
        for pos in range(5):
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
                
                report_content.append(f'\n{self.position_names[pos]}:')
                report_content.append(f'   推荐: {best_num} (概率: {best_prob:.2%}, 置信度: {best_conf:.2%})')
                report_content.append(f'   备选: {", ".join([str(n) for n, _, _ in pos_predictions[1:3]])}')
            else:
                all_pos = [(num, pred['probability'], pred['confidence']) for num, pred in predictions.items()]
                all_pos.sort(key=lambda x: x[1], reverse=True)
                best_num = all_pos[0][0]
                recommended_numbers.append(str(best_num))
                
                report_content.append(f'\n{self.position_names[pos]}:')
                report_content.append(f'   推荐: {best_num} (概率: {all_pos[0][1]:.2%}, 置信度: {all_pos[0][2]:.2%})')
        
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
        
        report_content.append('\n2. 风险提示:')
        report_content.append('   - 本报告基于历史数据统计分析')
        report_content.append('   - 概率预测仅供参考，不保证中奖')
        report_content.append('   - 请理性投注，量力而行')
        
        report_content.append('\n3. 数据洞察:')
        if 'hezhi' in analysis_result:
            avg_hezhi = analysis_result['hezhi']['avg']
            report_content.append(f'   - 和值集中在 {max(0, avg_hezhi-10)}-{min(45, avg_hezhi+10)} 区间')
        if 'span' in analysis_result:
            avg_span = analysis_result['span']['avg']
            report_content.append(f'   - 跨度集中在 {max(0, avg_span-3)}-{min(9, avg_span+3)} 区间')
        
        report_content.append('\n\n' + '=' * 80)
        report_content.append(f'报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        report_content.append('=' * 80)
        
        # 生成图表
        frequency_chart = self._generate_frequency_chart_bytes(analysis_result['frequency'])
        probability_chart = self._generate_probability_chart_bytes(analysis_result['predictions'])
        
        logger.info('最终最优报告生成完成')
        return {
            'report_type': 'optimal',
            'report_content': '\n'.join(report_content),
            'recommended_numbers': ' '.join(recommended_numbers),
            'confidence_score': round(avg_confidence * 100, 2),
            'analysis_summary': f'基于{analysis_result["total_samples"]}期数据分析',
            'key_conclusions': f'热门号码: {", ".join([str(n) for n, _ in hot_numbers[:3]])}',
            'core_metrics': f'平均置信度: {avg_confidence:.2%}, 平均趋势因子: {avg_trend_factor:.2f}',
            'frequency_chart': frequency_chart,
            'probability_chart': probability_chart
        }
    
    def save_report_to_file(self, report_content, filename):
        """
        保存报告到文件
        
        Args:
            report_content: 报告内容
            filename: 文件名
        
        Returns:
            文件路径
        """
        try:
            filepath = os.path.join(self.output_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report_content)
            logger.info(f'报告已保存: {filepath}')
            return filepath
        except Exception as e:
            logger.error(f'保存报告失败: {e}')
            return None


def test_report_generator():
    """测试报告生成器"""
    # 模拟分析结果
    from modules.analyzer_p5 import P5Analyzer
    
    test_data = [
        {'issue': '2026001', 'date': '2026-01-01', 'numbers': [1, 2, 3, 4, 5], 'hezhi': 15, 'span': 4, 'odd_even_ratio': '2:3'},
        {'issue': '2026002', 'date': '2026-01-02', 'numbers': [6, 7, 8, 9, 0], 'hezhi': 30, 'span': 9, 'odd_even_ratio': '3:2'},
        {'issue': '2026003', 'date': '2026-01-03', 'numbers': [1, 3, 5, 7, 9], 'hezhi': 25, 'span': 8, 'odd_even_ratio': '5:0'},
    ]
    
    analyzer = P5Analyzer()
    analysis_result = analyzer.calculate_probability(test_data)
    
    generator = P5ReportGenerator()
    
    print('=== 测试详细报告生成 ===')
    detailed_report = generator.generate_detailed_report(analysis_result, analyzer)
    print(f'报告长度: {len(detailed_report["report_content"])} 字符')
    
    print('\n=== 测试最优报告生成 ===')
    optimal_report = generator.generate_optimal_report(analysis_result)
    print(f'推荐号码: {optimal_report["recommended_numbers"]}')
    print(f'置信分数: {optimal_report["confidence_score"]}')


if __name__ == '__main__':
    test_report_generator()
