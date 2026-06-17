import os
import logging
import matplotlib.pyplot as plt
import matplotlib
from datetime import datetime
import io
import json

# 设置中文字体
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
matplotlib.rcParams['axes.unicode_minus'] = False

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
    报告生成器（专业版）

    生成规范、专业的七星彩统计分析报告，包含图表可视化。
    所有报告均包含随机性声明，明确区分统计描述与预测。
    """

    def __init__(self, output_dir='reports/'):
        self.output_dir = output_dir
        self._ensure_dir()
        self.position_names = ['第一位', '第二位', '第三位', '第四位',
                               '第五位', '第六位', '特别号']

    def _ensure_dir(self):
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    # ==================== 图表生成 ====================

    def _generate_frequency_chart_bytes(self, freq_data):
        """生成频率分布对比图（观测值 vs 理论值）"""
        try:
            fig, axes = plt.subplots(2, 3, figsize=(15, 10))
            axes = axes.flatten()

            for pos in range(6):
                ax = axes[pos]
                pos_data = freq_data.get(pos, {})
                stats = pos_data.get('number_stats', {})

                numbers = sorted(stats.keys()) if stats else list(range(10))
                observed = [stats.get(n, {}).get('frequency', 0) for n in numbers]
                expected = [stats.get(n, {}).get('expected_count', 0) for n in numbers]

                x = range(len(numbers))
                width = 0.35

                ax.bar([i - width / 2 for i in x], observed, width, label='观测频率', color='steelblue', alpha=0.8)
                ax.bar([i + width / 2 for i in x], expected, width, label='理论期望', color='orange', alpha=0.6)

                ax.set_title(f'{self.position_names[pos]} 频率分布', fontsize=11)
                ax.set_xlabel('号码')
                ax.set_ylabel('出现次数')
                ax.set_xticks(x)
                ax.set_xticklabels(numbers)
                ax.legend(fontsize=8)
                ax.grid(axis='y', linestyle='--', alpha=0.5)

            plt.suptitle('七星彩各位置号码频率分布（观测 vs 理论）', fontsize=14, fontweight='bold')
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()

            logger.info('频率分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成频率分布图失败: {e}')
            return None

    def _generate_omission_chart_bytes(self, omission_data):
        """生成遗漏值热力图"""
        try:
            fig, axes = plt.subplots(2, 3, figsize=(15, 10))
            axes = axes.flatten()

            for pos in range(6):
                ax = axes[pos]
                pos_data = omission_data.get(pos, {})
                stats = pos_data.get('number_stats', {})

                numbers = sorted(stats.keys()) if stats else list(range(10))
                omissions = [stats.get(n, {}).get('current_omission', 0) for n in numbers]
                avg_oms = [stats.get(n, {}).get('avg_omission', 1) for n in numbers]

                # 计算遗漏比率用于颜色
                ratios = [o / a if a > 0 else 0 for o, a in zip(omissions, avg_oms)]
                colors = ['red' if r > 1.5 else 'green' if r < 0.5 else 'gray' for r in ratios]

                bars = ax.bar(numbers, omissions, color=colors, alpha=0.7)
                ax.axhline(y=sum(avg_oms) / len(avg_oms) if avg_oms else 0, color='blue',
                           linestyle='--', label='平均遗漏', alpha=0.7)

                ax.set_title(f'{self.position_names[pos]} 当前遗漏值', fontsize=11)
                ax.set_xlabel('号码')
                ax.set_ylabel('遗漏期数')
                ax.legend(fontsize=8)
                ax.grid(axis='y', linestyle='--', alpha=0.5)

            # 添加图例说明
            fig.text(0.5, 0.02, '红色=冷号(遗漏>1.5倍平均)  绿色=热号(遗漏<0.5倍平均)  灰色=温号',
                     ha='center', fontsize=10, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

            plt.suptitle('七星彩各位置号码遗漏值分布', fontsize=14, fontweight='bold')
            plt.tight_layout(rect=[0, 0.03, 1, 0.95])

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()

            logger.info('遗漏值分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成遗漏值分布图失败: {e}')
            return None

    def _generate_hot_cold_chart_bytes(self, position_analysis):
        """生成冷热号分布图"""
        try:
            fig, axes = plt.subplots(2, 3, figsize=(15, 10))
            axes = axes.flatten()

            for pos in range(6):
                ax = axes[pos]
                pos_data = position_analysis.get(pos, {})
                num_analysis = pos_data.get('number_analysis', {})

                numbers = sorted(num_analysis.keys()) if num_analysis else list(range(10))
                heat_scores = [num_analysis.get(n, {}).get('heat_score', 50) for n in numbers]
                categories = [num_analysis.get(n, {}).get('category', 'warm') for n in numbers]
                colors = ['red' if c == 'hot' else 'blue' if c == 'cold' else 'gray' for c in categories]

                bars = ax.bar(numbers, heat_scores, color=colors, alpha=0.7)
                ax.axhline(y=60, color='red', linestyle='--', alpha=0.5, label='热号阈值')
                ax.axhline(y=40, color='blue', linestyle='--', alpha=0.5, label='冷号阈值')

                ax.set_title(f'{self.position_names[pos]} 冷热分布', fontsize=11)
                ax.set_xlabel('号码')
                ax.set_ylabel('热度评分')
                ax.set_ylim(0, 100)
                ax.legend(fontsize=8)
                ax.grid(axis='y', linestyle='--', alpha=0.5)

            fig.text(0.5, 0.02, '红色=热号  蓝色=冷号  灰色=温号',
                     ha='center', fontsize=10, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

            plt.suptitle('七星彩各位置号码冷热分布', fontsize=14, fontweight='bold')
            plt.tight_layout(rect=[0, 0.03, 1, 0.95])

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()

            logger.info('冷热分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成冷热分布图失败: {e}')
            return None

    def _generate_path_chart_bytes(self, path_data):
        """生成012路分布图"""
        try:
            fig, axes = plt.subplots(2, 3, figsize=(15, 10))
            axes = axes.flatten()

            for pos in range(6):
                ax = axes[pos]
                pos_data = path_data.get(pos, {})
                path_stats = pos_data.get('path_stats', {})

                paths = [0, 1, 2]
                counts = [path_stats.get(p, {}).get('count', 0) for p in paths]
                colors = ['#FF6B6B', '#4ECDC4', '#45B7D1']

                bars = ax.bar(paths, counts, color=colors, alpha=0.8)
                ax.set_title(f'{self.position_names[pos]} 012路分布', fontsize=11)
                ax.set_xlabel('路数')
                ax.set_ylabel('出现次数')
                ax.set_xticks(paths)
                ax.grid(axis='y', linestyle='--', alpha=0.5)

                # 添加数值标签
                for bar, count in zip(bars, counts):
                    if count > 0:
                        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                                str(count), ha='center', va='bottom', fontsize=9)

            plt.suptitle('七星彩各位置012路分布', fontsize=14, fontweight='bold')
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            chart_bytes = buf.getvalue()
            plt.close()

            logger.info('012路分布图已生成')
            return chart_bytes
        except Exception as e:
            logger.error(f'生成012路分布图失败: {e}')
            return None

    # ==================== 详细报告生成 ====================

    def generate_detailed_report(self, analysis_result, analyzer):
        """
        生成详细统计分析报告

        Args:
            analysis_result: 分析结果字典
            analyzer: 分析器实例

        Returns:
            包含报告内容和图表的字典
        """
        logger.info('开始生成详细分析报告')

        report_content = analyzer.generate_report(analysis_result)

        # 添加扩展分析内容
        report_lines = report_content.split('\n')

        # 在报告末尾添加更多专业指标
        extended_content = []
        extended_content.append('\n【九、位置独立性验证】')
        extended_content.append('-' * 60)

        corr = analysis_result.get('correlation', {})
        matrix = corr.get('correlation_matrix', {})

        # 计算平均相关系数
        corr_values = []
        for i in range(6):
            for j in range(i + 1, 6):
                val = abs(matrix.get(i, {}).get(j, 0))
                corr_values.append(val)

        avg_corr = sum(corr_values) / len(corr_values) if corr_values else 0
        max_corr = max(corr_values) if corr_values else 0

        extended_content.append(f'\n位置间平均相关系数: {avg_corr:.4f}')
        extended_content.append(f'最大相关系数: {max_corr:.4f}')
        extended_content.append(f'\n解读:')
        if avg_corr < 0.05:
            extended_content.append('  各位置间相关性极低，数据呈现良好的随机独立性。')
        elif avg_corr < 0.1:
            extended_content.append('  各位置间相关性较低，数据基本呈现随机独立性。')
        else:
            extended_content.append('  部分位置间存在一定相关性，但仍在正常随机波动范围内。')

        extended_content.append('\n【十、数据质量评估】')
        extended_content.append('-' * 60)

        total = analysis_result.get('total_samples', 0)
        extended_content.append(f'\n样本量评估:')
        if total >= 500:
            extended_content.append(f'  样本量 {total} 期，数据充分，统计结果可信度高。')
        elif total >= 200:
            extended_content.append(f'  样本量 {total} 期，数据较充分，统计结果具有参考价值。')
        elif total >= 50:
            extended_content.append(f'  样本量 {total} 期，数据量一般，统计结果仅供参考。')
        else:
            extended_content.append(f'  样本量 {total} 期，数据量不足，统计结果可能不稳定。')

        # 卡方检验汇总
        chi_sq = analysis_result.get('randomness', {}).get('chi_square_test', {})
        passed_tests = 0
        for pos in range(7):
            if pos in chi_sq:
                s = chi_sq[pos]
                if s.get('chi_square', 999) < s.get('degrees_of_freedom', 1) * 2:
                    passed_tests += 1

        extended_content.append(f'\n均匀性检验通过率: {passed_tests}/7')
        if passed_tests >= 6:
            extended_content.append('  绝大多数位置通过均匀性检验，数据分布符合随机预期。')
        elif passed_tests >= 4:
            extended_content.append('  大部分位置通过均匀性检验，个别位置存在轻微偏差。')
        else:
            extended_content.append('  多个位置未通过均匀性检验，数据分布可能存在异常。')

        extended_content.append('\n' + '=' * 80)
        extended_content.append('【最终声明】')
        extended_content.append('=' * 80)
        extended_content.append('1. 本报告所有分析均基于历史数据的统计描述，不构成任何预测。')
        extended_content.append('2. 七星彩每位号码的理论出现概率固定且均等。')
        extended_content.append('3. 彩票开奖是独立随机事件，历史规律不代表未来结果。')
        extended_content.append('4. 请理性购彩，量力而行，切勿沉迷。')
        extended_content.append('=' * 80)

        full_report = report_content + '\n'.join(extended_content)

        # 生成图表
        frequency_chart = self._generate_frequency_chart_bytes(analysis_result.get('frequency', {}))
        omission_chart = self._generate_omission_chart_bytes(analysis_result.get('omission', {}))
        hot_cold_chart = self._generate_hot_cold_chart_bytes(analysis_result.get('position_analysis', {}))
        path_chart = self._generate_path_chart_bytes(analysis_result.get('path_012', {}))

        logger.info('详细分析报告生成完成')

        return {
            'report_type': 'detailed',
            'report_content': full_report,
            'frequency_chart': frequency_chart,
            'omission_chart': omission_chart,
            'hot_cold_chart': hot_cold_chart,
            'path_chart': path_chart,
            'total_samples': analysis_result.get('total_samples', 0),
            'frequency_analysis': json.dumps(analysis_result.get('frequency', {}), ensure_ascii=False, default=str),
            'omission_analysis': json.dumps(analysis_result.get('omission', {}), ensure_ascii=False, default=str),
            'hot_cold_analysis': json.dumps(analysis_result.get('hot_cold', {}), ensure_ascii=False, default=str),
            'path_analysis': json.dumps(analysis_result.get('path_012', {}), ensure_ascii=False, default=str),
            'interval_analysis': json.dumps(analysis_result.get('omission', {}), ensure_ascii=False, default=str),
            'hezhi_analysis': json.dumps(analysis_result.get('hezhi', {}), ensure_ascii=False, default=str),
            'odd_even_analysis': json.dumps(analysis_result.get('odd_even', {}), ensure_ascii=False, default=str),
            'span_analysis': json.dumps(analysis_result.get('span', {}), ensure_ascii=False, default=str)
        }

    # ==================== 最优报告生成（新版）====================

    def generate_optimal_report(self, analysis_result):
        """
        生成最终综合分析报告（新版）

        不再提供"推荐号码"，而是提供基于统计的号码特征分析。

        Args:
            analysis_result: 分析结果字典

        Returns:
            包含报告内容的字典
        """
        logger.info('开始生成最终综合分析报告')

        report = []
        report.append('=' * 80)
        report.append('           七星彩统计特征综合分析报告')
        report.append('=' * 80)
        report.append(f'\n分析样本数: {analysis_result["total_samples"]} 期')
        report.append(f'分析时间: {analysis_result.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        report.append('-' * 80)
        report.append('\n【重要声明】')
        report.append('  本报告仅对历史开奖数据进行统计描述，所有号码的理论出现概率均等。')
        report.append('  彩票开奖为独立随机事件，历史统计特征不代表未来结果。')
        report.append('  本报告不构成任何投注建议，请理性购彩。')
        report.append('-' * 80)

        pos_analysis = analysis_result.get('position_analysis', {})

        # 一、各位置统计特征概览
        report.append('\n【一、各位置统计特征概览】')
        report.append('-' * 60)

        for pos in range(7):
            if pos not in pos_analysis:
                continue
            pos_data = pos_analysis[pos]
            pos_name = pos_data['position_name']
            theory_prob = pos_data['theory_prob']

            report.append(f'\n{pos_name} (理论概率: {theory_prob:.4f}):')

            hot = pos_data.get('hot_numbers', [])
            cold = pos_data.get('cold_numbers', [])

            if hot:
                report.append(f'  统计热号（近期出现频率高于理论值）: {", ".join(map(str, hot[:3]))}')
            if cold:
                report.append(f'  统计冷号（近期出现频率低于理论值）: {", ".join(map(str, cold[:3]))}')

            # 显示偏离度最大的号码
            num_analysis = pos_data.get('number_analysis', {})
            sorted_by_deviation = sorted(num_analysis.items(),
                                         key=lambda x: abs(x[1].get('deviation_rate', 0)),
                                         reverse=True)
            if sorted_by_deviation:
                report.append(f'  偏离度最大的号码:')
                for num, stats in sorted_by_deviation[:3]:
                    dev = stats.get('deviation_rate', 0)
                    direction = '高于' if dev > 0 else '低于'
                    report.append(f'    数字 {num}: {direction}理论值 {abs(dev):.1%}')

        # 二、遗漏值关注
        report.append('\n【二、当前遗漏值关注】')
        report.append('-' * 60)
        report.append('（以下号码自上次出现以来的期数较长，仅作统计记录）')

        omission = analysis_result.get('omission', {})
        for pos in range(6):
            if pos not in omission:
                continue
            pos_name = omission[pos]['position_name']
            stats = omission[pos]['number_stats']
            sorted_by_omission = sorted(stats.items(), key=lambda x: x[1]['current_omission'], reverse=True)

            report.append(f'\n{pos_name}:')
            for num, s in sorted_by_omission[:2]:
                report.append(f'  数字 {num}: 当前遗漏 {s["current_omission"]} 期 '
                              f'(平均遗漏 {s["avg_omission"]} 期)')

        # 三、012路特征
        report.append('\n【三、012路分布特征】')
        report.append('-' * 60)

        path_data = analysis_result.get('path_012', {})
        for pos in range(6):
            if pos not in path_data:
                continue
            pos_name = path_data[pos]['position_name']
            path_stats = path_data[pos]['path_stats']

            report.append(f'\n{pos_name}:')
            for p in [0, 1, 2]:
                s = path_stats.get(p, {})
                prob = s.get('probability', 0)
                theory = 1 / 3
                deviation = (prob - theory) / theory if theory > 0 else 0
                report.append(f'  {p}路: {s.get("count", 0)} 次 ({prob:.2%}, '
                              f'偏离理论值 {deviation:+.1%})')

        # 四、大小比与奇偶比
        report.append('\n【四、大小比与奇偶比统计】')
        report.append('-' * 60)

        big_small = analysis_result.get('big_small', {})
        odd_even = analysis_result.get('odd_even', {})

        for pos in range(6):
            if pos in big_small and pos in odd_even:
                pos_name = big_small[pos]['position_name']
                bs = big_small[pos]
                oe = odd_even[pos]

                bs_dev = (bs['big_probability'] - 0.5) / 0.5
                oe_dev = (oe['odd_probability'] - 0.5) / 0.5

                report.append(f'\n{pos_name}:')
                report.append(f'  大小比: 大 {bs["big_probability"]:.2%} : '
                              f'小 {bs["small_probability"]:.2%} '
                              f'(偏离 {bs_dev:+.1%})')
                report.append(f'  奇偶比: 奇 {oe["odd_probability"]:.2%} : '
                              f'偶 {oe["even_probability"]:.2%} '
                              f'(偏离 {oe_dev:+.1%})')

        # 五、和值与跨度
        report.append('\n【五、和值与跨度统计】')
        report.append('-' * 60)

        hezhi = analysis_result.get('hezhi', {})
        span = analysis_result.get('span', {})

        if hezhi:
            report.append(f'\n前6位和值:')
            report.append(f'  观测平均值: {hezhi.get("avg_hezhi", 0)}')
            report.append(f'  理论期望值: {hezhi.get("theory_avg", 27)}')
            report.append(f'  偏差: {hezhi.get("deviation_from_theory", 0):+.2f}')
            report.append(f'  历史范围: {hezhi.get("min_hezhi", 0)} - {hezhi.get("max_hezhi", 0)}')

        if span:
            report.append(f'\n前6位跨度:')
            report.append(f'  平均值: {span.get("avg_span", 0)}')
            report.append(f'  范围: {span.get("min_span", 0)} - {span.get("max_span", 0)}')

        # 六、随机性检验结论
        report.append('\n【六、随机性检验结论】')
        report.append('-' * 60)

        rand = analysis_result.get('randomness', {})
        report.append(f'\n{rand.get("overall_assessment", "数据随机性检验完成")}')

        chi_sq = rand.get('chi_square_test', {})
        passed = sum(1 for pos in range(7) if pos in chi_sq
                     and chi_sq[pos].get('chi_square', 999) < chi_sq[pos].get('degrees_of_freedom', 1) * 2)
        report.append(f'卡方均匀性检验通过: {passed}/7 个位置')

        corr = analysis_result.get('correlation', {})
        matrix = corr.get('correlation_matrix', {})
        corr_values = [abs(matrix.get(i, {}).get(j, 0))
                       for i in range(6) for j in range(i + 1, 6)]
        avg_corr = sum(corr_values) / len(corr_values) if corr_values else 0
        report.append(f'位置间平均相关系数: {avg_corr:.4f} '
                      f'({"独立" if avg_corr < 0.05 else "弱相关" if avg_corr < 0.1 else "存在一定相关"})')

        # 七、统计总结
        report.append('\n【七、统计总结】')
        report.append('-' * 60)
        report.append('\n基于以上统计分析，可以得出以下结论：')
        report.append('1. 各位置号码的理论出现概率均等，历史观测值围绕理论值波动。')
        report.append('2. 部分号码的观测频率暂时偏离理论值，这是随机波动的正常现象。')
        report.append('3. 位置间基本独立，符合七星彩开奖的随机性特征。')
        report.append('4. 冷热号、遗漏值等指标仅反映历史统计特征，不预示未来走势。')

        # 八、理性购彩提示
        report.append('\n【八、理性购彩提示】')
        report.append('-' * 60)
        report.append('\n1. 七星彩每位号码的中奖概率固定：')
        report.append('   - 前6位每位：1/10 = 10%')
        report.append('   - 特别号：1/15 ≈ 6.67%')
        report.append('   - 一等奖总概率：1/15,000,000')
        report.append('\n2. 历史数据分析不能提高中奖概率。')
        report.append('3. 请根据自身经济能力合理安排购彩预算。')
        report.append('4. 购彩有风险，投注需谨慎。')

        report.append('\n' + '=' * 80)
        report.append('            统计特征分析报告结束')
        report.append('=' * 80)
        report.append('\n【免责声明】')
        report.append('本报告仅供数据分析学习参考，不构成任何投注建议。')
        report.append('彩票开奖结果具有完全的随机性，请理性对待。')

        report_content = '\n'.join(report)

        # 生成图表
        frequency_chart = self._generate_frequency_chart_bytes(analysis_result.get('frequency', {}))
        omission_chart = self._generate_omission_chart_bytes(analysis_result.get('omission', {}))
        hot_cold_chart = self._generate_hot_cold_chart_bytes(analysis_result.get('position_analysis', {}))
        path_chart = self._generate_path_chart_bytes(analysis_result.get('path_012', {}))

        logger.info('最终综合分析报告生成完成')

        return {
            'report_type': 'optimal',
            'report_content': report_content,
            'recommended_numbers': '',  # 不再提供推荐号码
            'confidence_score': 0.0,    # 不再提供置信度分数
            'analysis_summary': f'基于{analysis_result["total_samples"]}期数据的统计特征分析',
            'frequency_chart': frequency_chart,
            'omission_chart': omission_chart,
            'hot_cold_chart': hot_cold_chart,
            'path_chart': path_chart,
            'key_metrics': {
                'total_samples': analysis_result.get('total_samples', 0),
                'avg_correlation': round(avg_corr, 4) if 'avg_corr' in locals() else 0,
                'chi_square_passed': passed if 'passed' in locals() else 0
            }
        }


if __name__ == '__main__':
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from database import Database
    from analyzer import ProbabilityAnalyzer

    db = Database()
    if db.connect():
        data = db.query_all_qxc_data()
        db.disconnect()

        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data)

            generator = ReportGenerator()

            # 生成详细报告
            detailed = generator.generate_detailed_report(result, analyzer)
            print(f'详细报告长度: {len(detailed["report_content"])} 字符')

            # 生成综合分析报告
            optimal = generator.generate_optimal_report(result)
            print(f'综合分析报告长度: {len(optimal["report_content"])} 字符')
            print('\n报告预览:')
            print(optimal['report_content'][:2000])
            print('...')
        else:
            print('数据库中没有数据')
    else:
        print('数据库连接失败')
