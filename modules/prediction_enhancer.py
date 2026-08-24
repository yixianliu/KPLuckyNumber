# -*- coding: utf-8 -*-
"""
预测算法增强模块

职责：
    在现有7算法基础上，增加序列模式挖掘和异常检测，
    为预测提供额外参考信息，提升预测的稳健性。

核心能力：
    1. 序列模式挖掘：识别号码出现的模式（连号、间隔、循环等）
    2. 异常检测：识别极端异常的数据模式
    3. 模式摘要：生成可解释的模式分析报告
"""

import logging
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class PatternMiner:
    """
    序列模式挖掘器

    挖掘历史数据中的统计模式，为预测提供参考信号。
    """

    def __init__(self, lookback: int = 60):
        """
        初始化模式挖掘器

        Args:
            lookback: 回看期数，默认60期
        """
        self.lookback = lookback

    def analyze(self, history_data: List[Dict]) -> Dict[str, Any]:
        """
        分析历史数据中的模式

        Args:
            history_data: 历史数据列表，每项包含'numbers'键（5位号码列表）

        Returns:
            模式分析结果字典
        """
        if len(history_data) < 10:
            return {'error': '数据不足', 'summary': '历史数据少于10期，无法有效分析模式'}

        recent = history_data[-self.lookback:]
        numbers = [d.get('numbers', []) for d in recent if len(d.get('numbers', [])) == 5]

        if not numbers:
            return {'error': '数据格式错误', 'summary': '无法解析历史数据'}

        return {
            'hot_cold': self._analyze_hot_cold(numbers),
            'consecutive': self._analyze_consecutive(numbers),
            'interval': self._analyze_interval(numbers),
            'cycle': self._analyze_cycle(numbers),
            'summary': self._generate_summary(numbers)
        }

    def _analyze_hot_cold(self, numbers: List[List[int]]) -> Dict:
        """分析冷热号"""
        pos_counts = [defaultdict(int) for _ in range(5)]

        for nums in numbers:
            for pos, num in enumerate(nums):
                pos_counts[pos][num] += 1

        result = {}
        for pos in range(5):
            counts = pos_counts[pos]
            total = len(numbers)
            avg = total / 10.0

            hot = sorted(
                [(n, c) for n, c in counts.items()],
                key=lambda x: x[1],
                reverse=True
            )[:3]
            cold = sorted(
                [(n, c) for n, c in counts.items()],
                key=lambda x: x[1]
            )[:3]

            result[str(pos)] = {
                'hot': [n for n, c in hot],
                'cold': [n for n, c in cold],
                'avg_freq': round(avg, 2)
            }

        return result

    def _analyze_consecutive(self, numbers: List[List[int]]) -> Dict:
        """分析连号模式"""
        consecutive_counts = defaultdict(int)
        total = len(numbers)

        for nums in numbers:
            for i in range(4):
                if nums[i + 1] - nums[i] == 1:
                    consecutive_counts[i] += 1

        result = {}
        for pos, count in consecutive_counts.items():
            rate = count / total
            result[str(pos)] = {
                'rate': round(rate, 3),
                'count': count
            }

        has_consecutive = any(v > total * 0.3 for v in consecutive_counts.values())

        return {
            'position_rates': result,
            'has_consecutive': has_consecutive
        }

    def _analyze_interval(self, numbers: List[List[int]]) -> Dict:
        """分析间隔模式"""
        intervals = defaultdict(list)

        for nums in numbers:
            for pos in range(5):
                intervals[pos].append(nums[pos])

        result = {}
        for pos in range(5):
            vals = intervals[pos]
            if len(vals) < 2:
                result[str(pos)] = {'mean_diff': 0, 'max_diff': 0}
                continue

            diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
            mean_diff = sum(diffs) / len(diffs) if diffs else 0
            max_diff = max(diffs) if diffs else 0

            result[str(pos)] = {
                'mean_diff': round(mean_diff, 2),
                'max_diff': max_diff
            }

        return result

    def _analyze_cycle(self, numbers: List[List[int]], max_cycle: int = 10) -> Dict:
        """分析周期模式（简化版）"""
        result = {}

        for pos in range(5):
            vals = [nums[pos] for nums in numbers]
            if len(vals) < 2 * max_cycle:
                result[str(pos)] = {'detected_cycle': 0, 'strength': 0.0}
                continue

            best_cycle = 0
            best_strength = 0.0

            for cycle_len in range(2, min(max_cycle + 1, len(vals) // 2)):
                matches = 0
                total_checks = 0
                for i in range(len(vals) - cycle_len):
                    if vals[i] == vals[i + cycle_len]:
                        matches += 1
                    total_checks += 1

                if total_checks > 0:
                    strength = matches / total_checks
                    if strength > best_strength:
                        best_strength = strength
                        best_cycle = cycle_len

            result[str(pos)] = {
                'detected_cycle': best_cycle if best_strength > 0.7 else 0,
                'strength': round(best_strength, 3)
            }

        return result

    def _generate_summary(self, numbers: List[List[int]]) -> str:
        """生成模式分析摘要"""
        if not numbers:
            return "无数据"

        # 和值统计
        sums = [sum(nums) for nums in numbers[-30:]]
        avg_sum = sum(sums) / len(sums) if sums else 0
        sum_std = (sum((s - avg_sum) ** 2 for s in sums) / len(sums)) ** 0.5 if sums else 0

        # 跨度统计
        spans = [max(nums) - min(nums) for nums in numbers[-30:]]
        avg_span = sum(spans) / len(spans) if spans else 0

        # 奇偶比统计
        odd_counts = [sum(1 for n in nums if n % 2 == 1) for nums in numbers[-30:]]
        most_common_odd_ratio = max(set(odd_counts), key=odd_counts.count) if odd_counts else 2

        return (
            f"和值: 平均{avg_sum:.1f}±{sum_std:.1f} | "
            f"跨度: 平均{avg_span:.1f} | "
            f"奇偶比: 最常见{most_common_odd_ratio}:{5-most_common_odd_ratio}"
        )


class AnomalyDetector:
    """
    异常检测器

    识别数据中的异常模式，帮助判断预测结果的可信度。
    """

    def __init__(self, threshold: float = 3.0):
        """
        初始化异常检测器

        Args:
            threshold: 异常判定阈值（标准差倍数），默认3.0
        """
        self.threshold = threshold

    def detect(self, history_data: List[Dict]) -> Dict[str, Any]:
        """
        检测异常模式

        Args:
            history_data: 历史数据列表

        Returns:
            异常检测结果
        """
        if len(history_data) < 10:
            return {'status': 'insufficient_data', 'anomalies': []}

        recent = history_data[-30:]
        numbers = [d.get('numbers', []) for d in recent if len(d.get('numbers', [])) == 5]

        anomalies = []

        # 1. 和值异常
        sums = [sum(nums) for nums in numbers]
        if sums:
            avg_sum = sum(sums) / len(sums)
            variance = sum((s - avg_sum) ** 2 for s in sums) / len(sums)
            std_sum = variance ** 0.5 if variance > 0 else 0

            for i, s in enumerate(sums):
                if std_sum > 0 and abs(s - avg_sum) > self.threshold * std_sum:
                    anomalies.append({
                        'type': 'sum_anomaly',
                        'issue_idx': len(recent) - len(sums) + i,
                        'value': s,
                        'expected_range': f'{avg_sum:.1f}±{std_sum:.1f}'
                    })

        # 2. 全奇/全偶检测
        for i, nums in enumerate(numbers):
            odd_count = sum(1 for n in nums if n % 2 == 1)
            if odd_count == 0 or odd_count == 5:
                anomalies.append({
                    'type': 'parity_extreme',
                    'issue_idx': len(recent) - len(numbers) + i,
                    'odd_count': odd_count
                })

        # 3. 连号缺失检测
        consecutive_count = 0
        for nums in reversed(numbers):
            has_consecutive = any(nums[i + 1] - nums[i] == 1 for i in range(4))
            if has_consecutive:
                break
            consecutive_count += 1

        if consecutive_count >= 5:
            anomalies.append({
                'type': 'no_consecutive',
                'count': consecutive_count,
                'note': f'连续{consecutive_count}期无连号'
            })

        return {
            'status': 'anomaly_detected' if anomalies else 'normal',
            'anomaly_count': len(anomalies),
            'anomalies': anomalies
        }


class PredictionEnhancer:
    """
    预测增强器

    整合模式挖掘和异常检测，为预测结果提供增强信息。
    """

    def __init__(self):
        """初始化预测增强器，组合模式挖掘器与异常检测器。

        说明:
            本模块只向预测结果追加分析字段，不修改任何核心概率，
            以保证「诚实边界」——增强信息仅用于解释，不参与决策。
        """
        self.pattern_miner = PatternMiner()
        self.anomaly_detector = AnomalyDetector()

    def enhance(self, prediction_result: Dict[str, Any],
                history_data: List[Dict]) -> Dict[str, Any]:
        """
        增强预测结果

        Args:
            prediction_result: 原始预测结果
            history_data: 历史数据

        Returns:
            增强后的预测结果
        """
        enhanced = prediction_result.copy()

        try:
            # 模式分析
            pattern_analysis = self.pattern_miner.analyze(history_data)
            enhanced['pattern_analysis'] = pattern_analysis

            # 异常检测
            anomaly_result = self.anomaly_detector.detect(history_data)
            enhanced['anomaly_detection'] = anomaly_result

            logger.info('预测增强完成')
        except Exception as e:
            logger.warning(f'预测增强失败（非致命）: {e}')
            enhanced['pattern_analysis'] = {'error': str(e)}
            enhanced['anomaly_detection'] = {'status': 'error', 'anomalies': []}

        return enhanced
