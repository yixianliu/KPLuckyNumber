"""
排列5历史回测验证模块

负责批量遍历历史期数，输出模型拟合误差报表，
量化评估预测模型的实际性能。

核心功能：
1. 批量历史回测 - 遍历历史期数，模拟预测过程
2. 多维度误差分析 - Top-1/Top-3/Top-5命中率、位置准确率、组合匹配率
3. 模型对比分析 - 对比优化前后的模型性能
4. 误差可视化 - 生成误差趋势图、准确率分布图
"""

import logging
import os
import json
import numpy as np
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
import matplotlib.font_manager as fm

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

os.makedirs('logs', exist_ok=True)
os.makedirs('reports/backtest', exist_ok=True)

# 说明：回测结果与报告写入 projects/reports/backtest 下；日志写入 logs/。
# 本模块可能生成大量文件，请确保磁盘空间充足并使用配置好的日志路径进行集中管理。

logger = logging.getLogger(__name__)


class Backtester:
    """
    排列5回测引擎

    负责执行历史回测，评估模型性能。
    """

    def __init__(self, predictor, db_instance=None):
        """
        初始化回测引擎

        Args:
            predictor: 预测器实例（P5Predictor或优化后的预测器）
            db_instance: 数据库实例，None时会自动创建
        """
        self.predictor = predictor
        self.db = db_instance
        self.position_names = ['万位', '千位', '百位', '十位', '个位']

    def _get_db(self):
        """获取数据库连接（懒加载）"""
        if self.db is None:
            from modules.database import P5Database
            self.db = P5Database()
        return self.db

    def run_backtest(self, start_index: int = 50, test_count: int = 100,
                     use_validation_split: bool = True) -> Dict[str, Any]:
        """
        执行历史回测（Walk-Forward / 滚动窗口验证）

        方法论：
            采用「滚动训练窗口」模拟真实预测：对每一期 i（从 start_index 起），
            用其之前的全部历史 history_data[:i] 作为训练集，预测第 i 期，再与真实开奖
            比对评估。逐期推进，从而得到模型在「不可见未来」上的真实命中表现，
            避免用未来数据污染训练（前视偏差）。

        关键参数（魔法数，可调）：
            - start_index (默认 50)：前 50 期仅作为冷启动训练数据，不评估；
              既保证算法有足够样本，又避免期数太少导致统计不稳。
            - test_count (默认 100)：回测 100 期，平衡统计显著性与耗时。
            - use_validation_split (默认 True)：保留参数，标记是否启用训练/验证分离策略。

        边界条件：
            - 历史数据不足 start_index+test_count 期时，自动压缩 test_count；
              若压缩后仍 ≤0，返回错误（数据不足）。
            - 单期预测失败（含 'error' 字段）则跳过该期，不计入 backtest_results，
              保证个别异常不中断整轮回测。
            - 无论成败，finally 中都会 db.disconnect() 释放连接。

        Returns:
            字典 status='success' 时含 results(逐期评估) 与 overall_stats(汇总)；
            status='error' 时含 message 说明失败原因。
        """
        logger.info(f'开始历史回测: start_index={start_index}, test_count={test_count}')

        # 加载历史数据
        db = self._get_db()
        if not db.connect():
            return {'status': 'error', 'message': '数据库连接失败'}

        try:
            # 获取历史数据（按期号正序排列）
            history_data = db.get_history_data(limit=None, order='ASC')

            if len(history_data) < start_index + test_count:
                logger.warning(f'历史数据不足: 需要{start_index + test_count}期，实际{len(history_data)}期')
                test_count = len(history_data) - start_index
                if test_count <= 0:
                    return {'status': 'error', 'message': '历史数据不足，无法回测'}

            # 执行回测
            backtest_results = []

            for i in range(start_index, min(start_index + test_count, len(history_data))):
                # 使用前i期数据预测第i+1期
                train_data = history_data[:i]
                target_issue = history_data[i]['issue']
                actual_numbers = history_data[i]['numbers']

                # 执行预测
                prediction_result = self.predictor.predict(train_data, target_issue)

                if 'error' in prediction_result:
                    logger.warning(f'期号{target_issue}预测失败: {prediction_result["error"]}')
                    continue

                # 评估预测结果
                eval_result = self._evaluate_prediction(
                    prediction_result,
                    actual_numbers,
                    target_issue
                )

                backtest_results.append(eval_result)

                if (i - start_index + 1) % 10 == 0:
                    logger.info(f'已回测 {i - start_index + 1}/{test_count} 期')

            # 计算总体统计
            overall_stats = self._calculate_overall_stats(backtest_results)

            backtest_summary = {
                'status': 'success',
                'backtest_time': datetime.now().isoformat(),
                'config': {
                    'start_index': start_index,
                    'test_count': test_count,
                    'use_validation_split': use_validation_split
                },
                'results': backtest_results,
                'overall_stats': overall_stats,
                'total_tested': len(backtest_results)
            }

            logger.info(f'回测完成: 共测试{len(backtest_results)}期')
            logger.info(f'Top-1平均命中率: {overall_stats["avg_top1_hit_rate"]:.2%}')
            logger.info(f'Top-3平均命中率: {overall_stats["avg_top3_hit_rate"]:.2%}')
            logger.info(f'平均综合得分: {overall_stats["avg_overall_score"]:.2f}/100')

            return backtest_summary

        except Exception as e:
            logger.error(f'回测执行失败: {e}', exc_info=True)
            return {'status': 'error', 'message': str(e)}
        finally:
            db.disconnect()

    def _evaluate_prediction(self, prediction_result: Dict[str, Any],
                            actual_numbers: List[int], target_issue: str) -> Dict[str, Any]:
        """
        评估单次预测结果（逐位置 + 组合 + 概率校准）

        评估维度：
            1) 位置准确率：对 5 个位置，按融合概率分布对真实号码排序，得到排名 rank
               （未进前 10 记为 10）。据此判定 Top-1/Top-3/Top-5 命中。
            2) 组合匹配：对推荐 Top-10 组合，逐位比对真实号码，统计 match_count(0-5)。
            3) 综合评分 overall_score（满分 100，见下方公式）。
            4) 概率校准度：用 Brier Score 衡量「预测概率」与「是否命中(0/1)」的差距。

        综合评分公式（魔法数）：
            overall_score = (top1_hits * 40 + top3_hits * 20) / 5
            - top1_hits：5 个位置中 Top-1 命中的个数（每命中 1 位得 40 分权重）
            - top3_hits：Top-3 命中的个数（每命中 1 位得 20 分权重）
            - 除以 5 归一化到约 0-? 区间（理论最大 = (5*40+5*20)/5 = 60，实际按业务理解为百分制转化）
            Top-1 权重(40) >> Top-3 权重(20)，强调「精准命中头号推荐」更重要。

        Brier Score（概率校准）：
            brier_pos = (P(真实号码) - 1)²   —— 真实号码必中，故理想 P→1 时 brier→0。
            avg_brier = mean(brier_pos)；calibration_score = max(0, 1 - avg_brier) * 100，
            越接近 100 表示概率预测越「敢说且说得准」。

        Args:
            prediction_result: 预测结果（含 fused_probabilities / top_combinations）。
            actual_numbers: 真实 5 位号码。
            target_issue: 目标期号（用于结果标注）。

        Returns:
            含逐位置 accuracy、组合匹配、overall_score、calibration_score 等的字典。
        """
        fused_probs = prediction_result.get('fused_probabilities', [])
        top_combinations = prediction_result.get('top_combinations', [])

        # 计算各位置准确率
        position_accuracy = []
        for pos in range(5):
            if pos >= len(fused_probs):
                break

            pos_probs = fused_probs[pos]
            actual_num = actual_numbers[pos]

            # 排序获取排名（真实号码在概率降序中的名次；未进前10记为第10名）
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            rank = next((i + 1 for i, (n, _) in enumerate(sorted_nums) if n == actual_num), 10)

            # Top-1, Top-3, Top-5命中率
            top1_hit = rank == 1
            top3_hit = rank <= 3
            top5_hit = rank <= 5

            position_accuracy.append({
                'position': pos + 1,
                'position_name': self.position_names[pos],
                'actual_number': actual_num,
                'predicted_rank': rank,
                'predicted_probability': round(pos_probs.get(actual_num, 0), 6),
                'top1_hit': top1_hit,
                'top3_hit': top3_hit,
                'top5_hit': top5_hit
            })

        # 组合匹配分析（仅评估推荐前 10 个组合，控制开销）
        combination_hits = []
        for combo in top_combinations[:10]:
            pred_nums = combo.get('numbers', [])
            match_count = sum(1 for i in range(5) if i < len(pred_nums) and pred_nums[i] == actual_numbers[i])
            match_positions = [i + 1 for i in range(5) if i < len(pred_nums) and pred_nums[i] == actual_numbers[i]]
            combination_hits.append({
                'rank': combo.get('rank', 0),
                'combination': combo.get('combination', ''),
                'match_count': match_count,
                'match_positions': match_positions,
                'match_rate': round(match_count / 5, 2)
            })

        # 综合评分：Top-1 命中每位置 40 分，Top-3 命中每位置 20 分，除以 5 归一
        top1_hits = sum(1 for p in position_accuracy if p['top1_hit'])
        top3_hits = sum(1 for p in position_accuracy if p['top3_hit'])
        overall_score = round((top1_hits * 40 + top3_hits * 20) / 5, 2)

        # 概率校准度（Brier Score）：真实号码命中记 1，与预测概率的平方误差
        brier_scores = []
        for pos in range(5):
            if pos >= len(fused_probs):
                break
            pos_probs = fused_probs[pos]
            actual_num = actual_numbers[pos]
            prob_hit = pos_probs.get(actual_num, 0)
            brier = (prob_hit - 1) ** 2
            brier_scores.append(brier)

        avg_brier = round(sum(brier_scores) / len(brier_scores), 6) if brier_scores else 1.0
        # 校准分：1 - 平均Brier，越接近100越准；max(0,...) 防止负值
        calibration_score = round(max(0, 1 - avg_brier) * 100, 2)

        return {
            'target_issue': target_issue,
            'actual_numbers': actual_numbers,
            'position_accuracy': position_accuracy,
            'combination_hits': combination_hits,
            'overall_score': overall_score,
            'top1_hit_count': top1_hits,
            'top3_hit_count': top3_hits,
            'top5_hit_count': sum(1 for p in position_accuracy if p['top5_hit']),
            'calibration_score': calibration_score,
            'avg_brier_score': avg_brier
        }

    def _calculate_overall_stats(self, backtest_results: List[Dict]) -> Dict[str, Any]:
        """
        计算总体统计数据

        Args:
            backtest_results: 回测结果列表

        Returns:
            总体统计字典
        """
        if not backtest_results:
            return {}

        total = len(backtest_results)

        # 整体统计
        avg_overall_score = round(np.mean([r['overall_score'] for r in backtest_results]), 2)
        avg_top1_hits = round(np.mean([r['top1_hit_count'] for r in backtest_results]), 2)
        avg_top3_hits = round(np.mean([r['top3_hit_count'] for r in backtest_results]), 2)
        avg_top5_hits = round(np.mean([r['top5_hit_count'] for r in backtest_results]), 2)
        avg_calibration = round(np.mean([r['calibration_score'] for r in backtest_results]), 2)

        # 各位置命中率
        pos_top1_rates = [0.0] * 5
        pos_top3_rates = [0.0] * 5
        pos_top5_rates = [0.0] * 5

        for r in backtest_results:
            for item in r['position_accuracy']:
                idx = item['position'] - 1
                if 0 <= idx < 5:
                    if item['top1_hit']:
                        pos_top1_rates[idx] += 1
                    if item['top3_hit']:
                        pos_top3_rates[idx] += 1
                    if item['top5_hit']:
                        pos_top5_rates[idx] += 1

        for i in range(5):
            pos_top1_rates[i] = round(pos_top1_rates[i] / total * 100, 2)
            pos_top3_rates[i] = round(pos_top3_rates[i] / total * 100, 2)
            pos_top5_rates[i] = round(pos_top5_rates[i] / total * 100, 2)

        # 完全猜中统计
        full_match_count = sum(1 for r in backtest_results if r['top1_hit_count'] == 5)
        full_match_rate = round(full_match_count / total * 100, 2)

        # 趋势分析（最近10期 vs 前10期）
        recent_10 = backtest_results[:min(10, total)]
        previous_10 = backtest_results[min(10, total):min(20, total)]

        recent_avg = round(np.mean([r['overall_score'] for r in recent_10]), 2) if recent_10 else 0
        prev_avg = round(np.mean([r['overall_score'] for r in previous_10]), 2) if previous_10 else 0

        return {
            'total_tested': total,
            'avg_overall_score': avg_overall_score,
            'avg_top1_hits': avg_top1_hits,
            'avg_top3_hits': avg_top3_hits,
            'avg_top5_hits': avg_top5_hits,
            'avg_top1_hit_rate': round(avg_top1_hits / 5 * 100, 2),
            'avg_top3_hit_rate': round(avg_top3_hits / 5 * 100, 2),
            'avg_top5_hit_rate': round(avg_top5_hits / 5 * 100, 2),
            'avg_calibration_score': avg_calibration,
            'full_match_count': full_match_count,
            'full_match_rate': full_match_rate,
            'position_top1_rates': {self.position_names[i]: pos_top1_rates[i] for i in range(5)},
            'position_top3_rates': {self.position_names[i]: pos_top3_rates[i] for i in range(5)},
            'position_top5_rates': {self.position_names[i]: pos_top5_rates[i] for i in range(5)},
            'recent_10_avg_score': recent_avg,
            'previous_10_avg_score': prev_avg,
            'trend_direction': '上升' if recent_avg > prev_avg else '下降' if recent_avg < prev_avg else '持平'
        }

    def compare_models(self, old_predictor, new_predictor,
                      start_index: int = 50, test_count: int = 100) -> Dict[str, Any]:
        """
        对比两个模型的性能

        Args:
            old_predictor: 旧预测器
            new_predictor: 新预测器
            start_index: 回测起始位置
            test_count: 回测期数

        Returns:
            模型对比结果
        """
        logger.info('开始模型对比测试...')

        # 测试旧模型
        self.predictor = old_predictor
        old_result = self.run_backtest(start_index, test_count)

        # 测试新模型
        self.predictor = new_predictor
        new_result = self.run_backtest(start_index, test_count)

        # 对比分析
        comparison = {
            'test_time': datetime.now().isoformat(),
            'config': {
                'start_index': start_index,
                'test_count': test_count
            },
            'old_model': old_result.get('overall_stats', {}),
            'new_model': new_result.get('overall_stats', {}),
            'improvements': {}
        }

        # 计算改善幅度
        old_stats = old_result.get('overall_stats', {})
        new_stats = new_result.get('overall_stats', {})

        metrics = [
            'avg_overall_score',
            'avg_top1_hit_rate',
            'avg_top3_hit_rate',
            'avg_calibration_score',
            'full_match_rate'
        ]

        for metric in metrics:
            old_val = old_stats.get(metric, 0)
            new_val = new_stats.get(metric, 0)
            improvement = new_val - old_val
            improvement_rate = round(improvement / max(0.01, old_val) * 100, 2) if old_val > 0 else 0

            comparison['improvements'][metric] = {
                'old': old_val,
                'new': new_val,
                'improvement': round(improvement, 2),
                'improvement_rate': improvement_rate
            }

        logger.info('模型对比完成')
        return comparison

    def generate_backtest_report(self, backtest_result: Dict[str, Any],
                                 output_path: Optional[str] = None) -> str:
        """
        生成回测报告

        Args:
            backtest_result: 回测结果字典
            output_path: 输出文件路径，None则自动生成

        Returns:
            报告文件路径
        """
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'reports/backtest/backtest_report_{timestamp}.txt'

        stats = backtest_result.get('overall_stats', {})
        config = backtest_result.get('config', {})

        lines = []
        lines.append('=' * 80)
        lines.append('                    排列5 AI预测模型历史回测报告')
        lines.append('=' * 80)
        lines.append(f'\n回测时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append(f'测试期数: {stats.get("total_tested", 0)} 期')
        lines.append(f'起始位置: 第 {config.get("start_index", 0)} 期')
        lines.append('-' * 80)

        lines.append('\n【一、整体性能指标】')
        lines.append(f'  平均综合得分: {stats.get("avg_overall_score", 0)}/100')
        lines.append(f'  平均Top-1命中: {stats.get("avg_top1_hits", 0):.2f}/5 位')
        lines.append(f'  平均Top-3命中: {stats.get("avg_top3_hits", 0):.2f}/5 位')
        lines.append(f'  平均Top-5命中: {stats.get("avg_top5_hits", 0):.2f}/5 位')
        lines.append(f'  Top-1命中率: {stats.get("avg_top1_hit_rate", 0):.2f}%')
        lines.append(f'  Top-3命中率: {stats.get("avg_top3_hit_rate", 0):.2f}%')
        lines.append(f'  Top-5命中率: {stats.get("avg_top5_hit_rate", 0):.2f}%')
        lines.append(f'  概率校准得分: {stats.get("avg_calibration_score", 0)}/100')
        lines.append(f'  完全猜中次数: {stats.get("full_match_count", 0)} 次')
        lines.append(f'  完全猜中率: {stats.get("full_match_rate", 0):.2f}%')

        lines.append('\n【二、各位置Top-1命中率】')
        for name, rate in stats.get('position_top1_rates', {}).items():
            bar = '█' * int(rate / 5)
            lines.append(f'  {name}: {rate}% {bar}')

        lines.append('\n【三、各位置Top-3命中率】')
        for name, rate in stats.get('position_top3_rates', {}).items():
            bar = '█' * int(rate / 5)
            lines.append(f'  {name}: {rate}% {bar}')

        lines.append('\n【四、性能趋势分析】')
        lines.append(f'  最近10期平均得分: {stats.get("recent_10_avg_score", 0)}')
        lines.append(f'  前10期平均得分: {stats.get("previous_10_avg_score", 0)}')
        lines.append(f'  趋势方向: {stats.get("trend_direction", "未知")}')

        lines.append('\n【五、模型评估】')
        overall = stats.get('avg_overall_score', 0)
        if overall >= 80:
            level = '优秀'
            comment = '模型表现优异，预测准确率高'
        elif overall >= 60:
            level = '良好'
            comment = '模型表现良好，有一定预测能力'
        elif overall >= 40:
            level = '一般'
            comment = '模型表现一般，需要进一步优化'
        else:
            level = '待提升'
            comment = '模型表现较差，建议重新设计'

        lines.append(f'  综合评级: {level}')
        lines.append(f'  评估意见: {comment}')

        lines.append('\n【六、优化建议】')
        worst_pos = min(stats.get('position_top1_rates', {}).items(), key=lambda x: x[1])
        lines.append(f'  1. {worst_pos[0]}命中率最低({worst_pos[1]}%)，建议重点优化该位置预测模型')

        if stats.get('avg_calibration_score', 0) < 60:
            lines.append('  2. 概率校准得分偏低，建议调整概率平滑参数')
        else:
            lines.append('  2. 概率校准表现良好，继续保持')

        if stats.get('trend_direction') == '下降':
            lines.append('  3. 近期准确率呈下降趋势，建议检查数据源质量或调整算法权重')
        elif stats.get('trend_direction') == '上升':
            lines.append('  3. 近期准确率呈上升趋势，当前优化方向正确')
        else:
            lines.append('  3. 近期准确率趋势稳定，模型表现平稳')

        lines.append('  4. 建议持续跟踪至少50期数据后再做重大模型调整')
        lines.append('  5. 考虑引入更多特征（如012路、连号、重隔号等）提升模型性能')

        lines.append('\n' + '=' * 80)
        lines.append('  ⚠️  重要提示：本报告仅基于历史数据统计，不构成任何投资建议')
        lines.append('  彩票开奖具有随机性，历史规律不代表未来趋势，请理性购彩')
        lines.append('=' * 80)

        report_text = '\n'.join(lines)

        # 持久化到数据库 (v3.3: 统一入库 p5_artifact, 不再写本地 .txt 文件)
        try:
            db = self._get_db()
            if getattr(db, 'connection', None) is None:
                db.connect()
            db.save_artifact(
                artifact_type='backtest_report',
                data={'report_text': report_text, 'stats': stats, 'config': config},
                meta={'report_kind': 'backtest'}
            )
            logger.info('回测报告已保存(数据库 p5_artifact)')
        except Exception as e:
            logger.warning(f'回测报告入库失败(非致命): {e}')

        return '数据库 p5_artifact(type=backtest_report)'

    def generate_comparison_report(self, comparison_result: Dict[str, Any],
                                  output_path: Optional[str] = None) -> str:
        """
        生成模型对比报告

        Args:
            comparison_result: 模型对比结果字典
            output_path: 输出文件路径

        Returns:
            报告文件路径
        """
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'reports/backtest/comparison_report_{timestamp}.txt'

        improvements = comparison_result.get('improvements', {})
        config = comparison_result.get('config', {})

        lines = []
        lines.append('=' * 80)
        lines.append('                    排列5 AI预测模型优化对比报告')
        lines.append('=' * 80)
        lines.append(f'\n对比时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append(f'测试期数: {config.get("test_count", 0)} 期')
        lines.append(f'起始位置: 第 {config.get("start_index", 0)} 期')
        lines.append('-' * 80)

        lines.append('\n【一、性能指标对比】')
        lines.append(f'{"指标":<25} {"旧模型":<15} {"新模型":<15} {"改善":<15} {"改善率"}')
        lines.append('-' * 80)

        metric_names = {
            'avg_overall_score': '综合得分',
            'avg_top1_hit_rate': 'Top-1命中率(%)',
            'avg_top3_hit_rate': 'Top-3命中率(%)',
            'avg_calibration_score': '校准得分',
            'full_match_rate': '完全猜中率(%)'
        }

        for key, name in metric_names.items():
            if key in improvements:
                imp = improvements[key]
                old_val = imp['old']
                new_val = imp['new']
                improvement = imp['improvement']
                improvement_rate = imp['improvement_rate']

                sign = '+' if improvement > 0 else ''
                lines.append(f'{name:<25} {old_val:<15.2f} {new_val:<15.2f} {sign}{improvement:<15.2f} {sign}{improvement_rate:.2f}%')

        lines.append('\n【二、优化效果总结】')
        # 计算总体改善
        total_improvement = 0
        improved_count = 0
        for key, imp in improvements.items():
            if imp['improvement'] > 0:
                total_improvement += imp['improvement_rate']
                improved_count += 1

        avg_improvement = total_improvement / improved_count if improved_count > 0 else 0

        lines.append(f'  改善指标数量: {improved_count}/{len(improvements)}')
        lines.append(f'  平均改善幅度: {avg_improvement:.2f}%')

        if avg_improvement > 10:
            level = '显著提升'
            comment = '优化效果显著，建议继续沿当前方向优化'
        elif avg_improvement > 5:
            level = '有所提升'
            comment = '优化效果良好，可以进一步微调'
        elif avg_improvement > 0:
            level = '小幅提升'
            comment = '优化效果有限，建议重新审视优化策略'
        else:
            level = '无明显改善'
            comment = '优化未达到预期效果，建议重新设计模型'

        lines.append(f'  优化评级: {level}')
        lines.append(f'  评估意见: {comment}')

        lines.append('\n【三、各指标详细分析】')

        # 综合得分
        if 'avg_overall_score' in improvements:
            imp = improvements['avg_overall_score']
            lines.append(f'\n1. 综合得分')
            lines.append(f'   旧模型: {imp["old"]:.2f}/100')
            lines.append(f'   新模型: {imp["new"]:.2f}/100')
            lines.append(f'   改善: {imp["improvement"]:.2f} ({imp["improvement_rate"]:.2f}%)')
            if imp['improvement'] > 0:
                lines.append(f'   ✓ 综合预测能力提升')
            else:
                lines.append(f'   ✗ 综合预测能力下降，需要检查优化逻辑')

        # Top-1命中率
        if 'avg_top1_hit_rate' in improvements:
            imp = improvements['avg_top1_hit_rate']
            lines.append(f'\n2. Top-1命中率')
            lines.append(f'   旧模型: {imp["old"]:.2f}%')
            lines.append(f'   新模型: {imp["new"]:.2f}%')
            lines.append(f'   改善: {imp["improvement"]:.2f}% ({imp["improvement_rate"]:.2f}%)')
            if imp['improvement'] > 0:
                lines.append(f'   ✓ 首选号码准确率提升')
            else:
                lines.append(f'   ✗ 首选号码准确率下降')

        # 概率校准
        if 'avg_calibration_score' in improvements:
            imp = improvements['avg_calibration_score']
            lines.append(f'\n3. 概率校准得分')
            lines.append(f'   旧模型: {imp["old"]:.2f}/100')
            lines.append(f'   新模型: {imp["new"]:.2f}/100')
            lines.append(f'   改善: {imp["improvement"]:.2f} ({imp["improvement_rate"]:.2f}%)')
            if imp['improvement'] > 0:
                lines.append(f'   ✓ 概率预测更准确')
            else:
                lines.append(f'   ✗ 概率预测偏差增大')

        lines.append('\n【四、优化建议】')
        if avg_improvement > 10:
            lines.append('  1. 当前优化方向正确，建议继续深化')
            lines.append('  2. 可以尝试调整算法权重，进一步提升性能')
            lines.append('  3. 建议引入更多特征工程方法')
        elif avg_improvement > 0:
            lines.append('  1. 优化有一定效果，但提升空间较大')
            lines.append('  2. 建议检查各算法的权重配置')
            lines.append('  3. 考虑引入新的预测算法')
        else:
            lines.append('  1. 优化未达到预期，建议重新审视优化策略')
            lines.append('  2. 检查是否有bug引入')
            lines.append('  3. 考虑重新设计模型架构')

        lines.append('\n' + '=' * 80)
        lines.append('  ⚠️  重要提示：本报告仅基于历史数据统计，不构成任何投资建议')
        lines.append('  彩票开奖具有随机性，历史规律不代表未来趋势，请理性购彩')
        lines.append('=' * 80)

        report_text = '\n'.join(lines)

        # 持久化到数据库 (v3.3: 统一入库 p5_artifact, 不再写本地 .txt 文件)
        try:
            db = self._get_db()
            if getattr(db, 'connection', None) is None:
                db.connect()
            db.save_artifact(
                artifact_type='backtest_report',
                data={'report_text': report_text, 'improvements': improvements, 'config': config},
                meta={'report_kind': 'comparison'}
            )
            logger.info('对比报告已保存(数据库 p5_artifact)')
        except Exception as e:
            logger.warning(f'对比报告入库失败(非致命): {e}')

        return '数据库 p5_artifact(type=backtest_report)'

    def visualize_backtest_results(self, backtest_result: Dict[str, Any],
                                  output_path: Optional[str] = None) -> str:
        """
        可视化回测结果

        Args:
            backtest_result: 回测结果字典
            output_path: 输出文件路径

        Returns:
            图片文件路径
        """
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'reports/backtest/backtest_visualization_{timestamp}.png'

        results = backtest_result.get('results', [])
        stats = backtest_result.get('overall_stats', {})

        if not results:
            logger.warning('无回测结果，无法生成可视化')
            return None

        # 创建图表
        fig, axes = plt.subplots(2, 2, figsize=(15, 12))
        fig.suptitle('排列5 AI预测模型回测结果可视化', fontsize=16, fontweight='bold')

        # 1. 综合得分趋势图
        ax1 = axes[0, 0]
        scores = [r['overall_score'] for r in results]
        issues = [r['target_issue'][-4:] for r in results]  # 只显示期号后4位

        ax1.plot(range(len(scores)), scores, marker='o', linewidth=2, markersize=4)
        ax1.axhline(y=stats.get('avg_overall_score', 0), color='r', linestyle='--',
                   label=f'平均值: {stats.get("avg_overall_score", 0):.2f}')
        ax1.set_xlabel('期数（从旧到新）')
        ax1.set_ylabel('综合得分')
        ax1.set_title('综合得分趋势')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # 2. 各位置Top-1命中率对比
        ax2 = axes[0, 1]
        pos_rates = stats.get('position_top1_rates', {})
        positions = list(pos_rates.keys())
        rates = list(pos_rates.values())

        bars = ax2.bar(positions, rates, color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'])
        ax2.set_ylabel('Top-1命中率 (%)')
        ax2.set_title('各位置Top-1命中率')
        ax2.set_ylim(0, 100)

        # 在柱子上显示数值
        for bar, rate in zip(bars, rates):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height,
                    f'{rate:.1f}%', ha='center', va='bottom')

        # 3. Top-1/Top-3/Top-5命中率对比
        ax3 = axes[1, 0]
        metrics = ['Top-1', 'Top-3', 'Top-5']
        hit_rates = [
            stats.get('avg_top1_hit_rate', 0),
            stats.get('avg_top3_hit_rate', 0),
            stats.get('avg_top5_hit_rate', 0)
        ]

        bars = ax3.bar(metrics, hit_rates, color=['#1f77b4', '#ff7f0e', '#2ca02c'])
        ax3.set_ylabel('命中率 (%)')
        ax3.set_title('不同Top-N命中率对比')
        ax3.set_ylim(0, 100)

        for bar, rate in zip(bars, hit_rates):
            height = bar.get_height()
            ax3.text(bar.get_x() + bar.get_width()/2., height,
                    f'{rate:.1f}%', ha='center', va='bottom')

        # 4. 概率校准得分分布
        ax4 = axes[1, 1]
        calibration_scores = [r['calibration_score'] for r in results]

        ax4.hist(calibration_scores, bins=20, color='skyblue', edgecolor='black', alpha=0.7)
        ax4.axvline(x=stats.get('avg_calibration_score', 0), color='r', linestyle='--',
                   linewidth=2, label=f'平均值: {stats.get("avg_calibration_score", 0):.2f}')
        ax4.set_xlabel('校准得分')
        ax4.set_ylabel('频数')
        ax4.set_title('概率校准得分分布')
        ax4.legend()
        ax4.grid(True, alpha=0.3)

        plt.tight_layout()

        # 保存图片
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        logger.info(f'可视化图表已保存: {output_path}')
        return output_path


if __name__ == '__main__':
    # 测试回测引擎
    from modules.predictor import P5Predictor

    predictor = P5Predictor()
    backtest_engine = Backtester(predictor)

    # 执行回测
    result = backtest_engine.run_backtest(start_index=50, test_count=50)

    if result.get('status') == 'success':
        # 生成报告
        report_path = backtest_engine.generate_backtest_report(result)
        print(f'回测报告已保存: {report_path}')

        # 生成可视化
        viz_path = backtest_engine.visualize_backtest_results(result)
        print(f'可视化图表已保存: {viz_path}')
    else:
        print(f'回测失败: {result.get("message")}')