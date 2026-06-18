"""
排列5预测结果历史准确率跟踪模块

负责记录每期预测结果、开奖后自动比对实际号码、
计算并统计各算法及综合模型的历史准确率，为预测模型优化提供数据支撑。

核心能力：
1. 预测结果存档 - 保存每期预测的概率分布和推荐组合
2. 准确率评估 - 开奖后自动计算位置命中率和组合匹配率
3. 历史统计 - 汇总多期准确率趋势，评估模型有效性
4. 校准分析 - 分析预测概率与实际频率的偏差，指导模型调参
"""

import logging
import os
import json
import math
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/p5_prediction_tracker.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5PredictionTracker:
    """
    排列5预测准确率跟踪器

    记录预测结果并在开奖后评估预测准确性，支持多维度统计分析。
    """

    def __init__(self, db_instance=None):
        """
        初始化跟踪器

        Args:
            db_instance: P5Database实例，None时会自动创建
        """
        self.db = db_instance
        self.position_names = ['万位', '千位', '百位', '十位', '个位']

    def _get_db(self):
        """获取数据库连接（懒加载）"""
        if self.db is None:
            from modules.database_p5 import P5Database
            self.db = P5Database()
        return self.db

    def save_prediction(self, prediction_result: Dict[str, Any]) -> bool:
        """
        保存预测结果到数据库

        Args:
            prediction_result: P5Predictor.predict()返回的预测结果字典

        Returns:
            保存是否成功
        """
        db = self._get_db()
        try:
            if not db.connection:
                db.connect()
                db.create_tables()

            fused = prediction_result.get('fused_probabilities', [])
            position_json = {}
            for pos in range(5):
                if pos < len(fused):
                    position_json[f'position_{pos+1}'] = {
                        str(n): round(p, 6) for n, p in fused[pos].items()
                    }

            sql = '''
            INSERT INTO p5_prediction_result
            (predict_uuid, target_issue, base_issue, predict_time, algorithm_config,
             position_predictions, top_combinations, trend_forecast, summary_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''

            db.cursor.execute(sql, (
                prediction_result.get('predict_uuid', ''),
                prediction_result.get('target_issue', ''),
                prediction_result.get('base_issue', ''),
                prediction_result.get('predict_time', datetime.now().isoformat()),
                json.dumps(prediction_result.get('algorithm_config', {}), ensure_ascii=False),
                json.dumps(position_json, ensure_ascii=False),
                json.dumps(prediction_result.get('top_combinations', []), ensure_ascii=False),
                json.dumps(prediction_result.get('trend_forecast', {}), ensure_ascii=False),
                prediction_result.get('summary', '')
            ))
            db.connection.commit()
            logger.info(f'预测结果已保存: target_issue={prediction_result.get("target_issue")}')
            return True
        except Exception as e:
            logger.error(f'保存预测结果失败: {e}')
            return False

    def evaluate_prediction(self, target_issue: str, actual_numbers: List[int]) -> Optional[Dict[str, Any]]:
        """
        评估指定期号的预测准确率

        Args:
            target_issue: 目标期号
            actual_numbers: 实际开奖号码列表（5个数字）

        Returns:
            准确率评估结果字典，None表示未找到预测记录
        """
        db = self._get_db()
        try:
            if not db.connection:
                db.connect()

            # 查询预测记录
            sql = 'SELECT * FROM p5_prediction_result WHERE target_issue = %s ORDER BY predict_time DESC LIMIT 1'
            db.cursor.execute(sql, (target_issue,))
            record = db.cursor.fetchone()

            if not record:
                logger.warning(f'未找到期号{target_issue}的预测记录，无法评估')
                return None

            position_predictions = json.loads(record['position_predictions']) if record['position_predictions'] else {}
            top_combinations = json.loads(record['top_combinations']) if record['top_combinations'] else []

            # 计算各位置准确率
            position_accuracy = []
            for pos in range(5):
                pos_key = f'position_{pos+1}'
                probs = position_predictions.get(pos_key, {})
                actual_num = int(actual_numbers[pos])

                # 排序获取排名
                sorted_nums = sorted(probs.items(), key=lambda x: x[1], reverse=True)
                rank = next((i + 1 for i, (n, _) in enumerate(sorted_nums) if int(n) == actual_num), 10)

                # Top-1, Top-3, Top-5命中率
                top1_hit = rank == 1
                top3_hit = rank <= 3
                top5_hit = rank <= 5

                position_accuracy.append({
                    'position': pos + 1,
                    'position_name': self.position_names[pos],
                    'actual_number': actual_num,
                    'predicted_rank': rank,
                    'predicted_probability': round(probs.get(str(actual_num), 0), 6),
                    'top1_hit': top1_hit,
                    'top3_hit': top3_hit,
                    'top5_hit': top5_hit
                })

            # 组合匹配分析
            combination_hits = []
            for combo in top_combinations:
                pred_nums = combo.get('numbers', [])
                match_count = sum(1 for i in range(5) if i < len(pred_nums) and int(pred_nums[i]) == actual_numbers[i])
                match_positions = [i + 1 for i in range(5) if i < len(pred_nums) and int(pred_nums[i]) == actual_numbers[i]]
                combination_hits.append({
                    'rank': combo.get('rank', 0),
                    'combination': combo.get('combination', ''),
                    'match_count': match_count,
                    'match_positions': match_positions,
                    'match_rate': round(match_count / 5, 2)
                })

            # 综合评分
            top1_hits = sum(1 for p in position_accuracy if p['top1_hit'])
            top3_hits = sum(1 for p in position_accuracy if p['top3_hit'])
            overall_score = round((top1_hits * 40 + top3_hits * 20) / 5, 2)  # 满分100分制

            # 概率校准度：预测概率 vs 实际命中（Brier Score简化版）
            brier_scores = []
            for pos in range(5):
                pos_key = f'position_{pos+1}'
                probs = position_predictions.get(pos_key, {})
                actual_num = actual_numbers[pos]
                prob_hit = probs.get(str(actual_num), 0)
                # Brier Score = (p - o)^2，o=1表示命中
                brier = (prob_hit - 1) ** 2
                brier_scores.append(brier)
            avg_brier = round(sum(brier_scores) / len(brier_scores), 6) if brier_scores else 1.0
            calibration_score = round(max(0, 1 - avg_brier) * 100, 2)  # 转换为百分制，越高越好

            eval_result = {
                'target_issue': target_issue,
                'predict_uuid': record['predict_uuid'],
                'actual_numbers': actual_numbers,
                'evaluated_at': datetime.now().isoformat(),
                'position_accuracy': position_accuracy,
                'combination_hits': combination_hits,
                'overall_score': overall_score,
                'top1_hit_count': top1_hits,
                'top3_hit_count': top3_hits,
                'calibration_score': calibration_score,
                'avg_brier_score': avg_brier
            }

            # 保存评估结果
            self._save_accuracy(eval_result)

            logger.info(f'预测准确率评估完成: {target_issue}, 综合得分{overall_score}, Top1命中{top1_hits}/5')
            return eval_result

        except Exception as e:
            logger.error(f'评估预测准确率失败: {e}')
            return None

    def _save_accuracy(self, eval_result: Dict[str, Any]) -> bool:
        """保存准确率评估结果到数据库"""
        db = self._get_db()
        try:
            sql = '''
            INSERT INTO p5_prediction_accuracy
            (target_issue, predict_uuid, actual_numbers, position_accuracy,
             combination_hits, overall_score, top1_hit_count, top3_hit_count,
             calibration_score, avg_brier_score, evaluated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            actual_numbers=VALUES(actual_numbers),
            position_accuracy=VALUES(position_accuracy),
            combination_hits=VALUES(combination_hits),
            overall_score=VALUES(overall_score),
            top1_hit_count=VALUES(top1_hit_count),
            top3_hit_count=VALUES(top3_hit_count),
            calibration_score=VALUES(calibration_score),
            avg_brier_score=VALUES(avg_brier_score),
            evaluated_at=VALUES(evaluated_at)
            '''

            db.cursor.execute(sql, (
                eval_result['target_issue'],
                eval_result['predict_uuid'],
                json.dumps(eval_result['actual_numbers']),
                json.dumps(eval_result['position_accuracy'], ensure_ascii=False),
                json.dumps(eval_result['combination_hits'], ensure_ascii=False),
                eval_result['overall_score'],
                eval_result['top1_hit_count'],
                eval_result['top3_hit_count'],
                eval_result['calibration_score'],
                eval_result['avg_brier_score'],
                eval_result['evaluated_at']
            ))
            db.connection.commit()
            return True
        except Exception as e:
            logger.error(f'保存准确率评估结果失败: {e}')
            return False

    def get_accuracy_statistics(self, limit: int = 100) -> Dict[str, Any]:
        """
        获取历史准确率统计数据

        Args:
            limit: 统计最近多少期

        Returns:
            历史准确率统计字典
        """
        db = self._get_db()
        try:
            if not db.connection:
                db.connect()

            sql = f'''
            SELECT * FROM p5_prediction_accuracy
            ORDER BY target_issue DESC
            LIMIT {limit}
            '''
            db.cursor.execute(sql)
            records = db.cursor.fetchall()

            if not records:
                return {'error': '暂无准确率评估记录'}

            total = len(records)
            avg_overall = round(sum(r['overall_score'] for r in records) / total, 2)
            avg_top1 = round(sum(r['top1_hit_count'] for r in records) / total, 2)
            avg_top3 = round(sum(r['top3_hit_count'] for r in records) / total, 2)
            avg_calibration = round(sum(r['calibration_score'] for r in records) / total, 2)

            # 各位置命中率
            pos_top1_rates = [0.0] * 5
            pos_top3_rates = [0.0] * 5
            for r in records:
                pos_acc = json.loads(r['position_accuracy']) if r['position_accuracy'] else []
                for item in pos_acc:
                    idx = item['position'] - 1
                    if 0 <= idx < 5:
                        if item['top1_hit']:
                            pos_top1_rates[idx] += 1
                        if item['top3_hit']:
                            pos_top3_rates[idx] += 1

            for i in range(5):
                pos_top1_rates[i] = round(pos_top1_rates[i] / total * 100, 2)
                pos_top3_rates[i] = round(pos_top3_rates[i] / total * 100, 2)

            # 趋势：最近10期 vs 前10期
            recent_10 = records[:min(10, total)]
            previous_10 = records[min(10, total):min(20, total)]
            recent_avg = round(sum(r['overall_score'] for r in recent_10) / len(recent_10), 2) if recent_10 else 0
            prev_avg = round(sum(r['overall_score'] for r in previous_10) / len(previous_10), 2) if previous_10 else 0

            return {
                'total_evaluated': total,
                'average_overall_score': avg_overall,
                'average_top1_hits': avg_top1,
                'average_top3_hits': avg_top3,
                'average_calibration_score': avg_calibration,
                'position_top1_rates': {self.position_names[i]: pos_top1_rates[i] for i in range(5)},
                'position_top3_rates': {self.position_names[i]: pos_top3_rates[i] for i in range(5)},
                'recent_10_avg_score': recent_avg,
                'previous_10_avg_score': prev_avg,
                'trend_direction': '上升' if recent_avg > prev_avg else '下降' if recent_avg < prev_avg else '持平',
                'latest_records': [
                    {
                        'target_issue': r['target_issue'],
                        'overall_score': r['overall_score'],
                        'top1_hits': r['top1_hit_count'],
                        'top3_hits': r['top3_hit_count']
                    }
                    for r in records[:10]
                ]
            }

        except Exception as e:
            logger.error(f'获取准确率统计失败: {e}')
            return {'error': str(e)}

    def generate_accuracy_report(self, limit: int = 100) -> str:
        """
        生成历史准确率统计报告文本

        Args:
            limit: 统计最近多少期

        Returns:
            报告文本
        """
        stats = self.get_accuracy_statistics(limit)
        if 'error' in stats:
            return f'准确率报告生成失败: {stats["error"]}'

        lines = []
        lines.append('=' * 70)
        lines.append('           排列5预测模型历史准确率统计报告')
        lines.append('=' * 70)
        lines.append(f'\n统计期数: {stats["total_evaluated"]} 期')
        lines.append(f'统计时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append('-' * 70)

        lines.append('\n【一、综合准确率指标】')
        lines.append(f'  平均综合得分: {stats["average_overall_score"]}/100')
        lines.append(f'  平均Top-1命中: {stats["average_top1_hits"]:.2f}/5 位')
        lines.append(f'  平均Top-3命中: {stats["average_top3_hits"]:.2f}/5 位')
        lines.append(f'  平均校准得分: {stats["average_calibration_score"]}/100')

        lines.append('\n【二、各位置Top-1命中率】')
        for name, rate in stats['position_top1_rates'].items():
            bar = '█' * int(rate / 5)
            lines.append(f'  {name}: {rate}% {bar}')

        lines.append('\n【三、各位置Top-3命中率】')
        for name, rate in stats['position_top3_rates'].items():
            bar = '█' * int(rate / 5)
            lines.append(f'  {name}: {rate}% {bar}')

        lines.append('\n【四、准确率趋势（最近10期 vs 前10期）】')
        lines.append(f'  最近10期平均得分: {stats["recent_10_avg_score"]}')
        lines.append(f'  前10期平均得分: {stats["previous_10_avg_score"]}')
        lines.append(f'  趋势方向: {stats["trend_direction"]}')

        lines.append('\n【五、最近10期评估记录】')
        for rec in stats['latest_records']:
            lines.append(f"  期号{rec['target_issue']}: 综合{rec['overall_score']}分, "
                        f"Top1命中{rec['top1_hits']}/5, Top3命中{rec['top3_hits']}/5")

        lines.append('\n【六、模型优化建议】')
        worst_pos = min(stats['position_top1_rates'].items(), key=lambda x: x[1])
        lines.append(f'  1. {worst_pos[0]}命中率最低({worst_pos[1]}%)，建议重点优化该位置预测模型')
        if stats['average_calibration_score'] < 60:
            lines.append('  2. 概率校准得分偏低，建议调整概率平滑参数')
        if stats['trend_direction'] == '下降':
            lines.append('  3. 近期准确率呈下降趋势，建议检查数据源质量或调整算法权重')
        else:
            lines.append('  3. 近期准确率趋势稳定或上升，当前模型配置效果良好')
        lines.append('  4. 建议持续跟踪至少50期数据后再做重大模型调整')
        lines.append('=' * 70)

        return '\n'.join(lines)

    def auto_evaluate_latest(self) -> Optional[Dict[str, Any]]:
        """
        自动评估最近一期的预测准确率

        从数据库获取最新开奖数据，查找对应的预测记录并评估。

        Returns:
            评估结果字典，None表示无数据或评估失败
        """
        db = self._get_db()
        try:
            if not db.connection:
                db.connect()

            # 获取最新开奖期号和号码
            sql = 'SELECT issue, num_wan, num_qian, num_bai, num_shi, num_ge FROM p5_history_data ORDER BY CAST(issue AS UNSIGNED) DESC LIMIT 1'
            db.cursor.execute(sql)
            row = db.cursor.fetchone()

            if not row:
                logger.warning('数据库中无开奖数据，无法自动评估')
                return None

            actual_numbers = [row['num_wan'], row['num_qian'], row['num_bai'], row['num_shi'], row['num_ge']]
            issue = row['issue']

            # 检查是否已有评估记录
            sql_check = 'SELECT id FROM p5_prediction_accuracy WHERE target_issue = %s'
            db.cursor.execute(sql_check, (issue,))
            if db.cursor.fetchone():
                logger.info(f'期号{issue}的准确率已评估过，跳过')
                return None

            return self.evaluate_prediction(issue, actual_numbers)

        except Exception as e:
            logger.error(f'自动评估最新期准确率失败: {e}')
            return None


if __name__ == '__main__':
    tracker = P5PredictionTracker()
    stats = tracker.get_accuracy_statistics(50)
    if 'error' in stats:
        print(f'统计失败: {stats["error"]}')
    else:
        print(tracker.generate_accuracy_report(50))
