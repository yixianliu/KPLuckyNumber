"""
排列5特征工程模块

负责从历史数据中提取丰富的统计特征，为AI模型提供高质量输入。

核心特征：
1. 基础统计特征：频率、遗漏、冷热号
2. 高级统计特征：012路、连号、重隔号、区间分布
3. 时序特征：滑动窗口统计、趋势动量、时序平滑/波动率/持续性、滚动统计特征
4. 交叉特征：位置关联、组合约束、位置链关联、形态标记、时序特征、交互特征、状态特征、上下文特征、数字特性、频率分布、趋势关联、约束相关、位置对特征
"""

import logging
import math
import numpy as np
from collections import defaultdict, Counter
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class P5Features:
    """
    排列5特征工程类

    提供全面的特征提取能力，支持单期特征和批量特征提取。
    """

    def __init__(self):
        """初始化特征工程器"""
        self.positions = 5
        self.number_range = range(0, 10)
        self.position_names = ['万位', '千位', '百位', '十位', '个位']

        # 012路定义
        self.road_0 = {0, 3, 6, 9}  # 0路：除3余0
        self.road_1 = {1, 4, 7}     # 1路：除3余1
        self.road_2 = {2, 5, 8}     # 2路：除3余2

        # 区间定义
        self.intervals = {
            'low': range(0, 3),      # 小区间：0-2
            'mid': range(3, 7),      # 中区间：3-6
            'high': range(7, 10)     # 大区间：7-9
        }

        # 质数定义（1不是质数）
        self.primes = {2, 3, 5, 7}
        self.composites = {0, 1, 4, 6, 8, 9}

        # 和值区间定义
        self.sum_intervals = {
            'very_low': range(0, 10),      # 极低和值：0-9
            'low': range(10, 18),          # 低和值：10-17
            'mid': range(18, 28),          # 中和值：18-27
            'high': range(28, 37),         # 高和值：28-36
            'very_high': range(37, 46)     # 极高和值：37-45
        }

        # 跨度区间定义
        self.span_intervals = {
            'very_small': range(0, 3),     # 极小跨度：0-2
            'small': range(3, 5),          # 小跨度：3-4
            'mid': range(5, 7),            # 中跨度：5-6
            'large': range(7, 9),          # 大跨度：7-8
            'very_large': range(9, 10)     # 极大跨度：9
        }

    # ==================== 1. 基础统计特征 ====================

    def calculate_frequency_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算频率特征

        Args:
            data: 历史数据列表（按时间正序排列）
            window: 滑动窗口大小，None表示使用全部数据

        Returns:
            频率特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计各位置各号码出现次数
        position_counts = [defaultdict(int) for _ in range(self.positions)]
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    position_counts[pos][int(num)] += 1

        # 计算频率和排名
        frequency_features = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            counts = position_counts[pos]

            # 计算频率
            frequencies = {num: counts.get(num, 0) / total for num in self.number_range}

            # 计算排名（按频率降序）
            sorted_nums = sorted(frequencies.items(), key=lambda x: x[1], reverse=True)
            ranks = {num: rank + 1 for rank, (num, _) in enumerate(sorted_nums)}

            # 计算冷热等级
            hot_threshold = np.percentile(list(frequencies.values()), 70)
            cold_threshold = np.percentile(list(frequencies.values()), 30)

            hot_levels = {}
            for num in self.number_range:
                freq = frequencies[num]
                if freq >= hot_threshold:
                    hot_levels[num] = 'hot'
                elif freq <= cold_threshold:
                    hot_levels[num] = 'cold'
                else:
                    hot_levels[num] = 'warm'

            frequency_features[pos_name] = {
                'frequencies': frequencies,
                'ranks': ranks,
                'hot_levels': hot_levels,
                'hot_count': sum(1 for level in hot_levels.values() if level == 'hot'),
                'warm_count': sum(1 for level in hot_levels.values() if level == 'warm'),
                'cold_count': sum(1 for level in hot_levels.values() if level == 'cold')
            }

        return frequency_features

    def calculate_omission_features(self, data: List[Dict]) -> Dict[str, Any]:
        """
        计算遗漏特征

        Args:
            data: 历史数据列表（按时间正序排列）

        Returns:
            遗漏特征字典
        """
        if not data:
            return {'error': '数据为空'}

        total = len(data)

        # 记录各位置各号码最后一次出现的位置索引
        last_occurrence = [{} for _ in range(self.positions)]
        for idx, item in enumerate(data):
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    last_occurrence[pos][int(num)] = idx

        # 计算当前遗漏值
        omission_features = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            omissions = {}
            max_omissions = {}

            for num in self.number_range:
                last_idx = last_occurrence[pos].get(num, -1)
                # 修复：当号码从未出现时，遗漏值应为total
                if last_idx == -1:
                    omission = total
                else:
                    omission = total - 1 - last_idx

                omissions[num] = omission

            # 计算历史最大遗漏
            max_omission_history = self._calculate_max_omission_history(data, pos)
            for num in self.number_range:
                max_omissions[num] = max_omission_history.get(num, 0)

            # 计算遗漏回补概率（遗漏越大，回补概率越高）
            omission_probs = {}
            for num in self.number_range:
                current = omissions[num]
                max_val = max_omissions[num]
                if max_val > 0:
                    # 指数衰减模型：遗漏/最大遗漏
                    omission_probs[num] = min(0.9, current / max_val)
                else:
                    omission_probs[num] = 0.1

            omission_features[pos_name] = {
                'current_omissions': omissions,
                'max_omissions': max_omissions,
                'omission_ratios': {num: omissions[num] / max(1, max_omissions[num])
                                   for num in self.number_range},
                'omission_probs': omission_probs
            }

        return omission_features

    def _calculate_max_omission_history(self, data: List[Dict], position: int) -> Dict[int, int]:
        """
        计算历史最大遗漏

        Args:
            data: 历史数据列表
            position: 位置索引

        Returns:
            各号码的历史最大遗漏
        """
        max_omissions = {num: 0 for num in self.number_range}
        current_omissions = {num: 0 for num in self.number_range}

        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                num = int(numbers[position])

                # 更新最大遗漏
                for n in self.number_range:
                    if n == num:
                        current_omissions[n] = 0
                    else:
                        current_omissions[n] += 1
                        if current_omissions[n] > max_omissions[n]:
                            max_omissions[n] = current_omissions[n]

        return max_omissions

    # ==================== 2. 高级统计特征 ====================

    def calculate_012_road_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算012路特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            012路特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计各位置012路分布
        road_counts = [defaultdict(int) for _ in range(self.positions)]
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    num = int(num)
                    if num in self.road_0:
                        road = 0
                    elif num in self.road_1:
                        road = 1
                    else:
                        road = 2
                    road_counts[pos][road] += 1

        # 计算特征
        road_features = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            counts = road_counts[pos]

            # 计算各路占比
            road_ratios = {
                0: counts.get(0, 0) / total,
                1: counts.get(1, 0) / total,
                2: counts.get(2, 0) / total
            }

            # 计算当前012路状态
            if data:
                last_num = int(data[-1].get('numbers', [])[pos])
                if last_num in self.road_0:
                    current_road = 0
                elif last_num in self.road_1:
                    current_road = 1
                else:
                    current_road = 2
            else:
                current_road = -1

            # 计算各路遗漏
            road_omissions = {0: 0, 1: 0, 2: 0}
            for item in reversed(data):
                num = int(item.get('numbers', [])[pos])
                if num in self.road_0:
                    road = 0
                elif num in self.road_1:
                    road = 1
                else:
                    road = 2

                if road == current_road:
                    break
                road_omissions[road] += 1

            road_features[pos_name] = {
                'road_ratios': road_ratios,
                'current_road': current_road,
                'road_omissions': road_omissions,
                'dominant_road': max(road_ratios.items(), key=lambda x: x[1])[0]
            }

        return road_features

    def calculate_consecutive_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算连号特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            连号特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计连号情况
        consecutive_stats = {
            'has_consecutive': 0,
            'consecutive_pairs': [],
            'max_consecutive_length': 0,
            'consecutive_positions': defaultdict(int)
        }

        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = sorted([int(n) for n in numbers])

                # 检查连号
                consecutive_lengths = []
                current_length = 1

                for i in range(1, len(nums)):
                    if nums[i] - nums[i-1] == 1:
                        current_length += 1
                    else:
                        if current_length > 1:
                            consecutive_lengths.append(current_length)
                        current_length = 1

                if current_length > 1:
                    consecutive_lengths.append(current_length)

                if consecutive_lengths:
                    consecutive_stats['has_consecutive'] += 1
                    max_len = max(consecutive_lengths)
                    if max_len > consecutive_stats['max_consecutive_length']:
                        consecutive_stats['max_consecutive_length'] = max_len

                    # 记录连号对
                    for i in range(len(nums) - 1):
                        if nums[i+1] - nums[i] == 1:
                            consecutive_stats['consecutive_pairs'].append((nums[i], nums[i+1]))

        # 计算特征
        consecutive_features = {
            'consecutive_rate': consecutive_stats['has_consecutive'] / total if total > 0 else 0,
            'max_consecutive_length': consecutive_stats['max_consecutive_length'],
            'consecutive_pair_counts': Counter(consecutive_stats['consecutive_pairs'])
        }

        return consecutive_features

    def calculate_repeat_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算重隔号特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            重隔号特征字典
        """
        use_data = data[-window:] if window else data

        if len(use_data) < 2:
            return {'error': '数据不足，至少需要2期'}

        # 统计重号（与上期重复）和隔号（隔期重复）
        repeat_stats = {
            'repeat_counts': [],      # 每期重号数量
            'separate_counts': [],    # 每期隔号数量
            'repeat_numbers': defaultdict(int),  # 各号码重号次数
            'separate_numbers': defaultdict(int)  # 各号码隔号次数
        }

        for i in range(1, len(use_data)):
            current_nums = set(int(n) for n in use_data[i].get('numbers', []))
            prev_nums = set(int(n) for n in use_data[i-1].get('numbers', []))

            # 重号
            repeats = current_nums & prev_nums
            repeat_stats['repeat_counts'].append(len(repeats))
            for num in repeats:
                repeat_stats['repeat_numbers'][num] += 1

            # 隔号（如果存在前两期数据）
            if i >= 2:
                prev2_nums = set(int(n) for n in use_data[i-2].get('numbers', []))
                separates = current_nums & prev2_nums - prev_nums
                repeat_stats['separate_counts'].append(len(separates))
                for num in separates:
                    repeat_stats['separate_numbers'][num] += 1

        # 计算特征
        repeat_features = {
            'avg_repeat_count': np.mean(repeat_stats['repeat_counts']) if repeat_stats['repeat_counts'] else 0,
            'avg_separate_count': np.mean(repeat_stats['separate_counts']) if repeat_stats['separate_counts'] else 0,
            'repeat_number_counts': dict(repeat_stats['repeat_numbers']),
            'separate_number_counts': dict(repeat_stats['separate_numbers']),
            'hot_repeat_numbers': [num for num, count in repeat_stats['repeat_numbers'].items()
                                  if count >= np.mean(list(repeat_stats['repeat_numbers'].values()))]
        }

        return repeat_features

    def calculate_interval_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算区间分布特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            区间分布特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计各位置区间分布
        interval_counts = [defaultdict(int) for _ in range(self.positions)]
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    num = int(num)
                    if num in self.intervals['low']:
                        interval = 'low'
                    elif num in self.intervals['mid']:
                        interval = 'mid'
                    else:
                        interval = 'high'
                    interval_counts[pos][interval] += 1

        # 计算特征
        interval_features = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            counts = interval_counts[pos]

            # 计算各区间占比
            interval_ratios = {
                'low': counts.get('low', 0) / total,
                'mid': counts.get('mid', 0) / total,
                'high': counts.get('high', 0) / total
            }

            # 计算当前区间
            if data:
                last_num = int(data[-1].get('numbers', [])[pos])
                if last_num in self.intervals['low']:
                    current_interval = 'low'
                elif last_num in self.intervals['mid']:
                    current_interval = 'mid'
                else:
                    current_interval = 'high'
            else:
                current_interval = 'unknown'

            interval_features[pos_name] = {
                'interval_ratios': interval_ratios,
                'current_interval': current_interval,
                'dominant_interval': max(interval_ratios.items(), key=lambda x: x[1])[0]
            }

        return interval_features

    # ==================== 3. 时序特征 ====================

    def calculate_sliding_window_features(self, data: List[Dict], windows: List[int] = [5, 10, 20, 50]) -> Dict[str, Any]:
        """
        计算滑动窗口特征

        Args:
            data: 历史数据列表
            windows: 窗口大小列表

        Returns:
            滑动窗口特征字典
        """
        if not data:
            return {'error': '数据为空'}

        window_features = {}

        for window in windows:
            window_data = data[-window:] if len(data) >= window else data

            # 计算该窗口内的频率特征
            freq_features = self.calculate_frequency_features(window_data, window=None)

            # 计算该窗口内的遗漏特征
            omission_features = self.calculate_omission_features(window_data)

            # 计算该窗口内的012路特征
            road_features = self.calculate_012_road_features(window_data, window=None)

            window_features[f'window_{window}'] = {
                'frequency': freq_features,
                'omission': omission_features,
                'road_012': road_features,
                'sample_count': len(window_data)
            }

        return window_features

    def calculate_trend_features(self, data: List[Dict], window: int = 10) -> Dict[str, Any]:
        """
        计算趋势特征

        Args:
            data: 历史数据列表
            window: 趋势观察窗口

        Returns:
            趋势特征字典
        """
        if len(data) < window:
            return {'error': f'数据不足，至少需要{window}期'}

        recent = data[-window:]

        trend_features = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]

            # 提取该位置近期序列
            seq = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    seq.append(int(numbers[pos]))

            if len(seq) < 2:
                trend_features[pos_name] = {'trend': 'unknown', 'slope': 0}
                continue

            # 线性回归求趋势方向
            x = np.arange(len(seq))
            y = np.array(seq)
            slope = np.polyfit(x, y, 1)[0] if len(x) > 1 else 0

            # 判断趋势方向
            if slope > 0.1:
                trend = 'up'
            elif slope < -0.1:
                trend = 'down'
            else:
                trend = 'stable'

            # 计算动量
            momentum = seq[-1] - seq[0]

            trend_features[pos_name] = {
                'trend': trend,
                'slope': round(slope, 4),
                'momentum': momentum,
                'recent_values': seq[-5:]  # 最近5期值
            }

        return trend_features

    # ==================== 3.11 滚动统计特征（v4.0） ====================

    def calculate_rolling_statistics_features(self, data: List[Dict], windows: List[int] = [5, 10, 20]) -> Dict[str, Any]:
        """
        计算滚动统计特征
        计算各统计量在不同窗口上的滚动值，捕捉短期变化动态
        扩展时序特征维度，提升对短期波动的敏感度

        Args:
            data: 历史数据列表（按时间正序排列）
            windows: 滚动窗口大小列表

        Returns:
            滚动统计特征字典，包含各位置号码的滚动均值、标准差、最大值、最小值等统计量
        """
        if not data:
            return {'error': '数据为空'}

        rolling_features = {}

        for window in windows:
            if len(data) < window:
                # 数据不足时使用所有可用数据
                window_data = data
                actual_window = len(data)
            else:
                window_data = data[-window:]
                actual_window = window

            if actual_window == 0:
                continue

            # 为每个位置计算滚动统计量
            for pos in range(self.positions):
                pos_name = self.position_names[pos]
                
                # 提取该位置的时间序列
                sequence = []
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        sequence.append(int(numbers[pos]))

                if len(sequence) < 2:
                    # 数据点太少，跳过
                    continue

                # 计算滚动统计量
                mean_val = np.mean(sequence)
                std_val = np.std(sequence) if len(sequence) > 1 else 0
                min_val = np.min(sequence)
                max_val = np.max(sequence)
                median_val = np.median(sequence)
                
                # 计算变异系数（标准化的离散度量）
                cv = std_val / mean_val if mean_val != 0 else 0
                
                # 计算范围（极差）
                range_val = max_val - min_val
                
                # 计算偏度和峰度（简化版）
                if len(sequence) >= 3 and std_val > 0:
                    skewness = np.mean([(x - mean_val) ** 3 for x in sequence]) / (std_val ** 3)
                    # 简化的峰度计算
                    kurtosis = np.mean([(x - mean_val) ** 4 for x in sequence]) / (std_val ** 4) - 3
                else:
                    skewness = 0
                    kurtosis = 0

                rolling_features[f'{pos_name}_window{window}_mean'] = mean_val
                rolling_features[f'{pos_name}_window{window}_std'] = std_val
                rolling_features[f'{pos_name}_window{window}_min'] = min_val
                rolling_features[f'{pos_name}_window{window}_max'] = max_val
                rolling_features[f'{pos_name}_window{window}_median'] = median_val
                rolling_features[f'{pos_name}_window{window}_cv'] = cv
                rolling_features[f'{pos_name}_window{window}_range'] = range_val
                rolling_features[f'{pos_name}_window{window}_skewness'] = skewness
                rolling_features[f'{pos_name}_window{window}_kurtosis'] = kurtosis

        return rolling_features

    # ==================== 4. 交叉特征 ====================

    def calculate_cross_position_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算位置交叉特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            位置交叉特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计位置间关联
        cross_features = {}

        # 统计相邻位置相同号码的次数
        same_adjacent = defaultdict(int)
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                for i in range(self.positions - 1):
                    if nums[i] == nums[i+1]:
                        same_adjacent[f'{i}-{i+1}'] += 1

        cross_features['same_adjacent_rate'] = {
            pos_pair: count / total for pos_pair, count in same_adjacent.items()
        }

        # 统计位置间相关性
        position_correlations = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                seq_i = []
                seq_j = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        seq_i.append(int(numbers[i]))
                        seq_j.append(int(numbers[j]))

                if len(seq_i) > 1:
                    # v4.0 §2.4 健壮性加固：任一位置序列为常量（方差=0，如某位全 0）
                    # 时 np.corrcoef 返回 nan，round(nan) 会污染下游且违反 [-1,1] 契约。
                    # 常量序列视作「无相关性」，显式记 0.0；数值异常同样降级 0.0。
                    if len(set(seq_i)) > 1 and len(set(seq_j)) > 1:
                        try:
                            corr = float(np.corrcoef(seq_i, seq_j)[0, 1])
                            if np.isnan(corr):
                                corr = 0.0
                        except Exception:
                            corr = 0.0
                    else:
                        corr = 0.0
                    position_correlations[f'{i}-{j}'] = round(corr, 4)

        cross_features['position_correlations'] = position_correlations

        return cross_features

    def calculate_sum_span_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算和值跨度特征

        Args:
            data: 历史数据列表
            window: 滑动窗口大小

        Returns:
            和值跨度特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 统计和值和跨度
        sums = []
        spans = []

        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                sums.append(sum(nums))
                spans.append(max(nums) - min(nums))

        # 计算和值特征
        sum_stats = {
            'mean': float(np.mean(sums)),
            'std': float(np.std(sums)),
            'min': int(min(sums)),
            'max': int(max(sums)),
            'median': float(np.median(sums))
        }

        # 统计和值区间分布
        sum_interval_counts = defaultdict(int)
        for s in sums:
            if s in self.sum_intervals['very_low']:
                sum_interval_counts['very_low'] += 1
            elif s in self.sum_intervals['low']:
                sum_interval_counts['low'] += 1
            elif s in self.sum_intervals['mid']:
                sum_interval_counts['mid'] += 1
            elif s in self.sum_intervals['high']:
                sum_interval_counts['high'] += 1
            else:
                sum_interval_counts['very_high'] += 1

        sum_interval_ratios = {
            interval: count / total for interval, count in sum_interval_counts.items()
        }

        # 计算跨度特征
        span_stats = {
            'mean': np.mean(spans),
            'std': np.std(spans),
            'min': min(spans),
            'max': max(spans),
            'median': np.median(spans)
        }

        # 统计跨度区间分布
        span_interval_counts = defaultdict(int)
        for s in spans:
            if s in self.span_intervals['very_small']:
                span_interval_counts['very_small'] += 1
            elif s in self.span_intervals['small']:
                span_interval_counts['small'] += 1
            elif s in self.span_intervals['mid']:
                span_interval_counts['mid'] += 1
            elif s in self.span_intervals['large']:
                span_interval_counts['large'] += 1
            else:
                span_interval_counts['very_large'] += 1

        span_interval_ratios = {
            interval: count / total for interval, count in span_interval_counts.items()
        }

        return {
            'sum_stats': sum_stats,
            'sum_interval_ratios': sum_interval_ratios,
            'span_stats': span_stats,
            'span_interval_ratios': span_interval_ratios,
            'current_sum': sums[-1] if sums else None,
            'current_span': spans[-1] if spans else None
        }

    # ==================== 4.5 位置链特征（v3.70） ====================

    def calculate_position_chain_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算位置链特征（v3.70 新增）

        提取相邻/跨位位置对（如 万→千、百→个）的滞后关联特征：
        - diff_dist_lag{L}：两位置在滞后 L 期后数字差 |差| 落在 0-2 的比率
        - diff_close_rate：L∈{2,5,10} 的 diff_dist 均值
        - move_same_rate：L∈{2,5,10} 内「升升/降降」同向移动比率均值
        - 末两位（十→个）的近期转移矩阵（最近 50 期，供 ml_supervised 输入）

        注意：排列5开奖为独立随机事件，本特征仅为历史归纳，无预测能力。

        Args:
            data: 历史数据列表（按时间正序排列）
            window: 滑动窗口大小，None表示使用全部数据

        Returns:
            位置链特征字典
        """
        use_data = data[-window:] if window else data
        total = len(use_data)

        if total == 0:
            return {'error': '数据为空'}

        # 定义位置链：相邻链(万→千、千→百、百→十、十→个) + 跨位链(万→百、千→个)
        chain_pairs = [
            (0, 1, '万→千'),
            (1, 2, '千→百'),
            (2, 3, '百→十'),
            (3, 4, '十→个'),
            (0, 2, '万→百'),
            (1, 4, '千→个')
        ]
        lags = [2, 5, 10]

        # 提取合法序列
        seq = []
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                seq.append([int(n) for n in numbers])

        chain_features: Dict[str, Any] = {}
        for pos_i, pos_j, label in chain_pairs:
            pair_features: Dict[str, Any] = {}

            # 滞后 diff_dist 统计：|t期位置i数字 - t-L期位置j数字| 落在 0-2 的比率
            diff_dist_rates = {}
            for lag in lags:
                pairs = [(row[pos_i], prev[pos_j])
                         for row, prev in zip(seq[lag:], seq[:-lag])]
                if pairs:
                    hit = sum(1 for a, b in pairs if abs(a - b) <= 2)
                    diff_dist_rates[f'diff_dist_lag{lag}'] = round(hit / len(pairs), 4)
                else:
                    diff_dist_rates[f'diff_dist_lag{lag}'] = 0.0

            pair_features.update(diff_dist_rates)
            # diff_close_rate：多滞后 diff_dist 均值（反映位置对数值接近程度）
            pair_features['diff_close_rate'] = round(
                sum(diff_dist_rates.values()) / len(diff_dist_rates), 4)

            # move_same_rate：相邻期同向移动比率（升升/降降）
            move_same_rates = {}
            for lag in lags:
                triples = [(prev[pos_i], row[pos_i], prev[pos_j], row[pos_j])
                           for row, prev in zip(seq[lag:], seq[:-lag])]
                if triples:
                    same_dir = sum(
                        1 for a_i, b_i, a_j, b_j in triples
                        if ((a_i - b_i) > 0 and (a_j - b_j) > 0)
                        or ((a_i - b_i) < 0 and (a_j - b_j) < 0)
                    )
                    move_same_rates[f'move_same_rate_lag{lag}'] = round(
                        same_dir / len(triples), 4)
                else:
                    move_same_rates[f'move_same_rate_lag{lag}'] = 0.0

            pair_features.update(move_same_rates)
            pair_features['move_same_rate'] = round(
                sum(move_same_rates.values()) / len(move_same_rates), 4)

            chain_features[label] = pair_features

        # 末两位（十→个）近期转移矩阵：近 50 期 十位数字 → 个位数字 的条件频率
        recent_window = min(50, len(seq))
        trans_matrix: Dict[str, Dict[str, float]] = {}
        for src in range(10):
            for dst in range(10):
                # 统计 十位=src 且 下一期个位=dst 的相邻期对数
                cnt = 0
                denom = 0
                for k in range(len(seq) - recent_window - 1, len(seq) - 1):
                    if seq[k][3] == src:
                        denom += 1
                        if seq[k + 1][4] == dst:
                            cnt += 1
                if denom > 0:
                    trans_matrix[f'{src}->{dst}'] = round(cnt / denom, 4)

        chain_features['recent_shi_ge_transition'] = trans_matrix
        chain_features['sample_count'] = total

        return chain_features

    # ==================== 4.6 形态标记特征（v3.70） ====================

    def calculate_recent_form_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算近期形态标记特征（v3.70 新增）

        基于最近 30 期窗口，统计整期形态（对子/顺子/豹子）最近出现的滞后期数：
        - lag_2pairs：最近 30 期内「含对子（两位置同数字）」形态的滞后期数
        - lag_3seq：最近 30 期内「含三连顺（相邻3位连号）」形态的滞后期数
        - lag_4quad：最近 30 期内「含四连顺（相邻4位连号）」形态的滞后期数
        - window_2pairs_rate / window_3seq_rate / window_4quad_rate：窗口内形态占比

        形态判定规则：
        - 对子：某期5个数字中存在任意两位置数字相同
        - 三连顺：存在相邻3位按 ±1 递增/递减排列（如 3-4-5）
        - 四连顺：存在相邻4位按 ±1 递增/递减排列

        注意：排列5开奖为独立随机事件，形态滞后期数仅为历史归纳指标，无预测能力。

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 回溯窗口期数（默认30期）

        Returns:
            形态标记特征字典；数据不足时返回全 None 占位
        """
        window = data[-lookback:] if lookback else data
        n = len(window)

        if n == 0:
            return {'error': '数据为空'}

        # 逐期判定形态
        has_2pairs: List[bool] = []
        has_3seq: List[bool] = []
        has_4seq: List[bool] = []

        for item in window:
            numbers = item.get('numbers', [])
            if len(numbers) != self.positions:
                # 数据不完整，标记为无形态
                has_2pairs.append(False)
                has_3seq.append(False)
                has_4seq.append(False)
                continue

            nums = [int(x) for x in numbers]

            # 对子：任意两位置数字相同
            pair_found = False
            for i in range(5):
                for j in range(i + 1, 5):
                    if nums[i] == nums[j]:
                        pair_found = True
                        break
                if pair_found:
                    break
            has_2pairs.append(pair_found)

            # 三连顺 / 四连顺：相邻位置按 ±1 递进
            seq3_found = False
            seq4_found = False
            for start in range(5):
                # 检查 start 起 3 位是否形成连号
                if start + 3 <= 5:
                    seg3 = nums[start:start + 3]
                    if all(seg3[k + 1] - seg3[k] in (1, -1) for k in range(2)):
                        seq3_found = True
                if start + 4 <= 5:
                    seg4 = nums[start:start + 4]
                    if all(seg4[k + 1] - seg4[k] in (1, -1) for k in range(3)):
                        seq4_found = True

            has_3seq.append(seq3_found)
            has_4seq.append(seq4_found)

        # 滞后期数：窗口内最后一次出现的滞后（0=最近一期出现，None=窗口内未出现）
        def _last_lag(flags: List[bool]) -> Optional[int]:
            for idx in range(len(flags) - 1, -1, -1):
                if flags[idx]:
                    return len(flags) - 1 - idx
            return None

        lag_2pairs = _last_lag(has_2pairs)
        lag_3seq = _last_lag(has_3seq)
        lag_4quad = _last_lag(has_4seq)

        # 窗口内形态占比
        window_2pairs_rate = round(sum(has_2pairs) / n, 4)
        window_3seq_rate = round(sum(has_3seq) / n, 4)
        window_4quad_rate = round(sum(has_4seq) / n, 4)

        return {
            'lag_2pairs': lag_2pairs,
            'lag_3seq': lag_3seq,
            'lag_4quad': lag_4quad,
            'window_2pairs_rate': window_2pairs_rate,
            'window_3seq_rate': window_3seq_rate,
            'window_4quad_rate': window_4quad_rate,
            'sample_count': n,
            'lookback': lookback
        }

    # ==================== 4.7 时序特征 ====================

    def calculate_timing_features(self, data: List[Dict], lookback: int = 60) -> Dict[str, Any]:
        """
        计算数字出现时序特征（平滑/波动率/持续性）
        扩展时序特征维度，提升预测数字出现概率的能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 时序分析窗口期数（默认60期）

        Returns:
            时序特征字典，包含每个数字(0-9)的时序平滑、波动率和持续性特征
        """
        if not data:
            return {'error': '数据为空'}
        
        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)
        total_history = len(data)
        
        if total_window == 0:
            return {'error': '窗口数据为空'}
        
        timing_features = {}
        
        # 为每个数字(0-9)计算时序特征
        for digit in range(10):
            # 获取该数字在历史数据中的所有出现位置
            positions_in_history = []
            positions_in_window = []
            
            # 历史数据中的位置
            for i, item in enumerate(data):
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    if digit in [int(n) for n in numbers]:
                        positions_in_history.append(i)
            
            # 窗口数据中的位置
            for i, item in enumerate(window_data):
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    if digit in [int(n) for n in numbers]:
                        positions_in_window.append(i)
            
            # 时序平滑：最近出现频率相对历史频率的比值
            if len(positions_in_history) > 0:
                recent_freq = len(positions_in_window) / max(total_window, 1)
                history_freq = len(positions_in_history) / max(total_history, 1)
                smooth = recent_freq / (history_freq + 1e-6)
            else:
                smooth = 0.0
            
            # 时序波动率：出现间隔的标准差（标准化）
            if len(positions_in_history) >= 2:
                intervals = []
                for i in range(1, len(positions_in_history)):
                    intervals.append(positions_in_history[i] - positions_in_history[i-1])
                volatility = np.std(intervals) / max(total_history, 1) if len(intervals) > 0 else 0.0
            else:
                volatility = 0.0
            
            # 时序持续性：连续出现的最大长度
            max_consecutive = 0
            current_consecutive = 0
            
            # 将历史数据转换为该数字的出现序列
            digit_sequence = [1 if digit in [int(n) for n in item.get('numbers', [])] else 0 
                            for item in data if len(item.get('numbers', [])) == self.positions]
            
            for val in digit_sequence:
                if val == 1:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
            
            continuity = max_consecutive / max(total_history, 1)
            
            timing_features[f'digit_{digit}_smooth'] = smooth
            timing_features[f'digit_{digit}_volatility'] = volatility
            timing_features[f'digit_{digit}_continuity'] = continuity
        
        return timing_features

    # ==================== 4.8 交互特征 ====================

    def calculate_interaction_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算位置间数字交互特征
        扩展交互特征维度，提升组合规律识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            window: 滑动窗口大小，None表示使用全部数据

        Returns:
            交互特征字典，包含位置间交互关联、跨位差分布和位置关联强度
        """
        use_data = data[-window:] if window else data
        total = len(use_data)
        
        if total == 0:
            return {'error': '数据为空'}
        
        interaction_features = {}
        
        # 1. 相邻位数字交互关联
        adjacency_interaction = {}
        for i in range(self.positions - 1):
            # 计算相邻位置的数字差分布
            diffs = []
            for item in use_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    a, b = int(numbers[i]), int(numbers[i+1])
                    diffs.append(b - a)
            
            if diffs:
                # 计算差值的均值、标准差、偏度
                mean_diff = np.mean(diffs)
                std_diff = np.std(diffs)
                # 简单的偏度计算
                if std_diff > 0:
                    skewness = np.mean([(diff - mean_diff) ** 3 for diff in diffs]) / (std_diff ** 3)
                else:
                    skewness = 0.0
                
                adjacency_interaction[f'{i}_{i+1}_mean_diff'] = mean_diff
                adjacency_interaction[f'{i}_{i+1}_std_diff'] = std_diff
                adjacency_interaction[f'{i}_{i+1}_skewness'] = skewness
        
        interaction_features['adjacency_interaction'] = adjacency_interaction
        
        # 2. 跨位差分布（非相邻位置的差值）
        cross_position_diff = {}
        for i in range(self.positions):
            for j in range(i+2, self.positions):  # 非相邻位置
                diffs = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        a, b = int(numbers[i]), int(numbers[j])
                        diffs.append(b - a)
                
                if diffs:
                    mean_diff = np.mean(diffs)
                    std_diff = np.std(diffs)
                    cross_position_diff[f'{i}_{j}_mean_diff'] = mean_diff
                    cross_position_diff[f'{i}_{j}_std_diff'] = std_diff
        
        interaction_features['cross_position_diff'] = cross_position_diff
        
        # 3. 位置关联强度（基于互信息）
        position_association = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                seq_i = []
                seq_j = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        seq_i.append(int(numbers[i]))
                        seq_j.append(int(numbers[j]))
                
                if len(seq_i) > 1:
                    # 计算互信息的简化版本（基于协方差）
                    cov = np.cov(seq_i, seq_j)[0, 1]
                    std_i = np.std(seq_i)
                    std_j = np.std(seq_j)
                    if std_i > 0 and std_j > 0:
                        correlation = cov / (std_i * std_j)
                    else:
                        correlation = 0.0
                    
                    position_association[f'{i}_{j}_correlation'] = correlation
        
        interaction_features['position_association'] = position_association
        
        return interaction_features

    # ==================== 4.10 位置对特征（v4.0） ====================

    def calculate_position_pair_features(self, data: List[Dict], window: Optional[int] = None) -> Dict[str, Any]:
        """
        计算位置对特征
        分析特定位置对之间的数字关系和关联模式
        扩展交互特征维度，提升特定位置组合的规律识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            window: 滑动窗口大小，None表示使用全部数据

        Returns:
            位置对特征字典，包含各位置对的数字差分布、关联强度和模式一致性
        """
        use_data = data[-window:] if window else data
        total = len(use_data)
        
        if total == 0:
            return {'error': '数据为空'}
        
        position_pair_features = {}
        
        # 1. 计算所有位置对的数字差分布
        pair_differences = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                pos_i_name = self.position_names[i]
                pos_j_name = self.position_names[j]
                pair_key = f'{pos_i_name}_{pos_j_name}'
                
                # 计算数字差序列
                diffs = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num_i = int(numbers[i])
                        num_j = int(numbers[j])
                        diffs.append(num_j - num_i)  # j位置减i位置
                
                if diffs:
                    # 计算差值的统计特征
                    mean_diff = np.mean(diffs)
                    std_diff = np.std(diffs) if len(diffs) > 1 else 0
                    
                    # 计算差值的分布特征
                    # 正差、零差、负差的比例
                    pos_count = sum(1 for d in diffs if d > 0)
                    zero_count = sum(1 for d in diffs if d == 0)
                    neg_count = sum(1 for d in diffs if d < 0)
                    
                    pair_differences[pair_key] = {
                        'mean_diff': mean_diff,
                        'std_diff': std_diff,
                        'positive_ratio': pos_count / len(diffs),
                        'zero_ratio': zero_count / len(diffs),
                        'negative_ratio': neg_count / len(diffs),
                        'min_diff': min(diffs),
                        'max_diff': max(diffs)
                    }
        
        position_pair_features['pair_differences'] = pair_differences
        
        # 2. 计算位置对之间的关联强度（基于数值关联）
        pair_correlations = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                pos_i_name = self.position_names[i]
                pos_j_name = self.position_names[j]
                pair_key = f'{pos_i_name}_{pos_j_name}'
                
                # 提取两个位置的数值序列
                seq_i = []
                seq_j = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        seq_i.append(int(numbers[i]))
                        seq_j.append(int(numbers[j]))
                
                if len(seq_i) > 1:
                    # 计算皮尔逊相关系数
                    try:
                        corr = np.corrcoef(seq_i, seq_j)[0, 1]
                        if np.isnan(corr):
                            corr = 0.0
                    except:
                        corr = 0.0
                    
                    pair_correlations[pair_key] = round(corr, 4)
        
        position_pair_features['pair_correlations'] = pair_correlations
        
        # 3. 计算位置对的模式一致性（同向变化的比例）
        pair_consistency = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                pos_i_name = self.position_names[i]
                pos_j_name = self.position_names[j]
                pair_key = f'{pos_i_name}_{pos_j_name}'
                
                # 提取两个位置的数值序列
                seq_i = []
                seq_j = []
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        seq_i.append(int(numbers[i]))
                        seq_j.append(int(numbers[j]))
                
                if len(seq_i) >= 2:
                    # 计算同向变化的比例
                    same_direction_count = 0
                    total_changes = 0
                    
                    for k in range(1, len(seq_i)):
                        diff_i = seq_i[k] - seq_i[k-1]
                        diff_j = seq_j[k] - seq_j[k-1]
                        
                        # 只要有变化就计算方向一致性
                        if diff_i != 0 or diff_j != 0:
                            total_changes += 1
                            # 同向：都是增加、都是减少，或者都没变化
                            if (diff_i > 0 and diff_j > 0) or (diff_i < 0 and diff_j < 0) or (diff_i == 0 and diff_j == 0):
                                same_direction_count += 1
                    
                    consistency = same_direction_count / max(total_changes, 1)
                    pair_consistency[pair_key] = consistency
        
        position_pair_features['pair_consistency'] = pair_consistency
        
        # 4. 计算位置对的特殊模式（如相等、固定差值等）
        pair_patterns = {}
        for i in range(self.positions):
            for j in range(i+1, self.positions):
                pos_i_name = self.position_names[i]
                pos_j_name = self.position_names[j]
                pair_key = f'{pos_i_name}_{pos_j_name}'
                
                # 检查特殊模式出现的频率
                equal_count = 0  # 两位置数字相等
                fixed_diff_count = 0  # 固定差值（这里简化为差值为1或-1）
                
                for item in use_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num_i = int(numbers[i])
                        num_j = int(numbers[j])
                        
                        if num_i == num_j:
                            equal_count += 1
                        
                        # 检查固定差值（相邻数字）
                        if abs(num_j - num_i) == 1:
                            fixed_diff_count += 1
                
                pair_patterns[pair_key] = {
                    'equal_ratio': equal_count / total if total > 0 else 0,
                    'fixed_diff_ratio': fixed_diff_count / total if total > 0 else 0
                }
        
        position_pair_features['pair_patterns'] = pair_patterns
        
        return position_pair_features

    # ==================== 4.9 状态特征 ====================

    def calculate_state_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算号码状态特征
        扩展状态特征维度，提升组合筛选与优化能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 状态分析窗口期数（默认30期）

        Returns:
            状态特征字典，包含冷热状态演变、形态状态和异常状态标记
        """
        if not data:
            return {'error': '数据为空'}
        
        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)
        
        if total_window == 0:
            return {'error': '窗口数据为空'}
        
        state_features = {}
        
        # 1. 冷热状态演变
        hot_cold_evolution = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            
            # 计算该位置在窗口期内的冷热分布
            hot_count = 0
            cold_count = 0
            warm_count = 0
            
            # 首先计算全历史数据的频率基准
            position_freq = [0] * 10
            for item in data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    num = int(numbers[pos])
                    position_freq[num] += 1
            
            # 计算热冷阈值（使用百分位数）
            sorted_freq = sorted(position_freq)
            hot_threshold = sorted_freq[int(0.7 * len(sorted_freq))] if len(sorted_freq) > 0 else 0
            cold_threshold = sorted_freq[int(0.3 * len(sorted_freq))] if len(sorted_freq) > 0 else 0
            
            # 统计窗口期内的冷热分布
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    num = int(numbers[pos])
                    freq = position_freq[num]
                    if freq >= hot_threshold:
                        hot_count += 1
                    elif freq <= cold_threshold:
                        cold_count += 1
                    else:
                        warm_count += 1
            
            hot_cold_evolution[f'{pos_name}_hot_ratio'] = hot_count / max(total_window, 1)
            hot_cold_evolution[f'{pos_name}_cold_ratio'] = cold_count / max(total_window, 1)
            hot_cold_evolution[f'{pos_name}_warm_ratio'] = warm_count / max(total_window, 1)
        
        state_features['hot_cold_evolution'] = hot_cold_evolution
        
        # 2. 形态状态（连号/豹子状态）
        pattern_state = {}
        
        # 统计最近lookback期的形态出现频率
        seq_count = 0
        pair_count = 0
        triple_count = 0
        
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                
                # 检测顺子（连号）
                is_sequence = False
                for start in range(self.positions - 1):
                    # 检查递增顺序
                    if all(nums[start + k + 1] - nums[start + k] == 1 for k in range(self.positions - start - 1)):
                        is_sequence = True
                        break
                    # 检查递减顺序
                    if all(nums[start + k + 1] - nums[start + k] == -1 for k in range(self.positions - start - 1)):
                        is_sequence = True
                        break
                
                if is_sequence:
                    seq_count += 1
                
                # 检测对子
                has_pair = False
                seen_nums = set()
                for num in nums:
                    if num in seen_nums:
                        has_pair = True
                        break
                    seen_nums.add(num)
                
                if has_pair:
                    pair_count += 1
                
                # 检测豹子（三个相同数字）
                from collections import Counter
                num_counts = Counter(nums)
                if any(count >= 3 for count in num_counts.values()):
                    triple_count += 1
        
        pattern_state['seq_ratio'] = seq_count / max(total_window, 1)
        pattern_state['pair_ratio'] = pair_count / max(total_window, 1)
        pattern_state['triple_ratio'] = triple_count / max(total_window, 1)
        
        state_features['pattern_state'] = pattern_state
        
        # 3. 异常状态标记
        anomaly_state = {}
        
        # 检测异常的和值和跨度
        sum_anomaly_count = 0
        span_anomaly_count = 0
        
        # 计算正常范围（基于历史数据）
        sums = []
        spans = []
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                sums.append(sum(nums))
                spans.append(max(nums) - min(nums))
        
        if sums and spans:
            sum_mean, sum_std = np.mean(sums), np.std(sums)
            span_mean, span_std = np.mean(spans), np.std(spans)
            
            # 异常阈值：超过2个标准差
            sum_anomaly_threshold = 2 * sum_std
            span_anomaly_threshold = 2 * span_std
            
            # 检测窗口期内的异常
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    nums = [int(n) for n in numbers]
                    curr_sum = sum(nums)
                    curr_span = max(nums) - min(nums)
                    
                    if sum_std > 0 and abs(curr_sum - sum_mean) > sum_anomaly_threshold:
                        sum_anomaly_count += 1
                    if span_std > 0 and abs(curr_span - span_mean) > span_anomaly_threshold:
                        span_anomaly_count += 1
            
            anomaly_state['sum_anomaly_ratio'] = sum_anomaly_count / max(total_window, 1)
            anomaly_state['span_anomaly_ratio'] = span_anomaly_count / max(total_window, 1)
        
        state_features['anomaly_state'] = anomaly_state
        
        return state_features

    # ==================== 4.10 上下文特征 ====================

    def calculate_contextual_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算上下文特征
        扩展上下文特征维度，提升预测趋势延续性的能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 上下文分析窗口期数（默认30期）

        Returns:
            上下文特征字典，包含历史期上下文、趋势延续性和模式延续标记
        """
        if not data:
            return {'error': '数据为空'}
        
        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)
        
        if total_window == 0:
            return {'error': '窗口数据为空'}
        
        contextual_features = {}
        
        # 1. 历史期上下文（最近期数的特征保留度）
        historical_context = {}
        
        # 计算各位置号码在历史期和窗口期的一致性
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            
            # 历史期号码分布
            history_nums = []
            for item in data[-lookback*2:-lookback] if len(data) >= lookback*2 else data[:-lookback] if len(data) > lookback else []:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    history_nums.append(int(numbers[pos]))
            
            # 窗口期号码分布
            window_nums = []
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    window_nums.append(int(numbers[pos]))
            
            # 计算历史期到窗口期的号码保留率
            if history_nums:
                history_set = set(history_nums)
                window_set = set(window_nums)
                if history_set:
                    retention_rate = len(history_set & window_set) / len(history_set)
                else:
                    retention_rate = 0.0
            else:
                retention_rate = 0.0
            
            historical_context[f'{pos_name}_retention_rate'] = retention_rate
        
        contextual_features['historical_context'] = historical_context
        
        # 2. 趋势延续性
        trend_continuity = {}
        
        # 计算各位置的趋势方向一致性
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            
            # 获取历史期和窗口期的趋势
            def calculate_trend_direction(nums):
                if len(nums) < 2:
                    return 'stable'
                # 简单线性趋势
                x = list(range(len(nums)))
                y = nums
                slope = np.polyfit(x, y, 1)[0] if len(x) > 1 else 0
                if slope > 0.1:
                    return 'up'
                elif slope < -0.1:
                    return 'down'
                else:
                    return 'stable'
            
            # 历史期趋势
            history_nums = []
            for item in data[-lookback*2:-lookback] if len(data) >= lookback*2 else data[:-lookback] if len(data) > lookback else []:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    history_nums.append(int(numbers[pos]))
            history_trend = calculate_trend_direction(history_nums)
            
            # 窗口期趋势
            window_nums = []
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    window_nums.append(int(numbers[pos]))
            window_trend = calculate_trend_direction(window_nums)
            
            # 趋势延续性：历史趋势和窗口趋势一致性
            trend_continuity[f'{pos_name}_trend_match'] = 1.0 if history_trend == window_trend else 0.0
        
        contextual_features['trend_continuity'] = trend_continuity
        
        # 3. 模式延续标记
        pattern_continuity = {}
        
        # 检测特定模式的延续性
        patterns_to_check = [
            ('all_even', lambda nums: all(n % 2 == 0 for n in nums)),
            ('all_odd', lambda nums: all(n % 2 == 1 for n in nums)),
            ('all_high', lambda nums: all(n >= 5 for n in nums)),
            ('all_low', lambda nums: all(n < 5 for n in nums))
        ]
        
        for pattern_name, pattern_check in patterns_to_check:
            # 历史期模式出现率
            history_pattern_count = 0
            history_total = 0
            for item in data[-lookback*2:-lookback] if len(data) >= lookback*2 else data[:-lookback] if len(data) > lookback else []:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    history_total += 1
                    nums = [int(n) for n in numbers]
                    if pattern_check(nums):
                        history_pattern_count += 1
            
            # 窗口期模式出现率
            window_pattern_count = 0
            window_total = 0
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    window_total += 1
                    nums = [int(n) for n in numbers]
                    if pattern_check(nums):
                        window_pattern_count += 1
            
            # 模式延续性
            if history_total > 0:
                history_rate = history_pattern_count / history_total
            else:
                history_rate = 0.0
            
            if window_total > 0:
                window_rate = window_pattern_count / window_total
            else:
                window_rate = 0.0
            
            # 计算模式延续指数（窗口率/历史率）
            if history_rate > 0:
                continuity_index = window_rate / history_rate
            else:
                continuity_index = 0.0
            
            pattern_continuity[f'{pattern_name}_continuity'] = continuity_index
        
        contextual_features['pattern_continuity'] = pattern_continuity
        
        return contextual_features

    # ==================== 4.11 组合约束交叉特征（v4.0） ====================

    def calculate_constraint_correlation_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算组合约束交叉特征
        分析不同组合约束条件之间的相关性和交互关系
        扩展组合约束分析维度，提升约束规律识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 分析窗口期数（默认30期）

        Returns:
            组合约束交叉特征字典，包含各约束条件之间的相关性分析
        """
        if not data:
            return {'error': '数据为空'}

        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)

        if total_window == 0:
            return {'error': '窗口数据为空'}

        constraint_features = {}

        # 1. 计算各组合约束条件的时间序列
        constraint_series = {
            'sum_values': [],           # 和值
            'span_values': [],          # 跨度
            'odd_even_ratios': [],      # 奇偶比例
            'prime_ratios': [],         # 质数比例
            'high_low_ratios': [],      # 大小比例
            '012_road_ratios': [],      # 012路比例（主导路径）
            'consecutive_counts': [],   # 连号数量
            'repeat_counts': []         # 重号数量
        }

        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                
                # 和值
                constraint_series['sum_values'].append(sum(nums))
                
                # 跨度
                constraint_series['span_values'].append(max(nums) - min(nums))
                
                # 奇偶比例（奇数个数 / 总个数）
                odd_count = sum(1 for n in nums if n % 2 == 1)
                constraint_series['odd_even_ratios'].append(odd_count / self.positions)
                
                # 质数比例
                prime_count = sum(1 for n in nums if n in self.primes)
                constraint_series['prime_ratios'].append(prime_count / self.positions)
                
                # 大小比例（大于等于5的个数 / 总个数）
                high_count = sum(1 for n in nums if n >= 5)
                constraint_series['high_low_ratios'].append(high_count / self.positions)
                
                # 012路比例（找出数量最多的路径）
                road_counts = [0, 0, 0]  # 0路, 1路, 2路
                for n in nums:
                    if n in self.road_0:
                        road_counts[0] += 1
                    elif n in self.road_1:
                        road_counts[1] += 1
                    else:
                        road_counts[2] += 1
                # 主导路径占比
                max_road_count = max(road_counts)
                constraint_series['012_road_ratios'].append(max_road_count / self.positions)
                
                # 连号数量
                consecutive_count = 0
                nums_sorted = sorted(nums)
                for i in range(len(nums_sorted) - 1):
                    if nums_sorted[i+1] - nums_sorted[i] == 1:
                        consecutive_count += 1
                constraint_series['consecutive_counts'].append(consecutive_count)
                
                # 重号数量（与上期相比，但这里简化为当期重复数字数量）
                # 实际的重号需要和上期比较，这里我们计算当期内的重复程度
                from collections import Counter
                num_counts = Counter(nums)
                repeat_count = sum(count - 1 for count in num_counts.values() if count > 1)
                constraint_series['repeat_counts'].append(repeat_count)

        # 2. 计算各约束条件之间的相关性
        constraint_correlations = {}
        constraint_names = list(constraint_series.keys())
        
        for i in range(len(constraint_names)):
            for j in range(i+1, len(constraint_names)):
                name_i = constraint_names[i]
                name_j = constraint_names[j]
                series_i = constraint_series[name_i]
                series_j = constraint_series[name_j]
                
                if len(series_i) > 1 and len(series_j) > 1:
                    # 计算皮尔逊相关系数
                    try:
                        corr = np.corrcoef(series_i, series_j)[0, 1]
                        # 处理可能的NaN值
                        if np.isnan(corr):
                            corr = 0.0
                    except:
                        corr = 0.0
                    
                    constraint_correlations[f'{name_i}_{name_j}'] = round(corr, 4)

        # 3. 计算约束条件的变化趋势一致性
        trend_consistency = {}
        for name in constraint_names:
            series = constraint_series[name]
            if len(series) >= 3:
                # 计算最近3期的变化趋势
                recent_trend = series[-1] - series[-3]
                # 计算整体窗口的变化趋势
                overall_trend = series[-1] - series[0] if len(series) > 1 else 0
                # 趋势一致性：最近趋势与整体趋势的方向一致性
                if overall_trend != 0:
                    consistency = 1.0 if (recent_trend * overall_trend) > 0 else 0.0
                else:
                    consistency = 1.0 if recent_trend == 0 else 0.0
                trend_consistency[f'{name}_trend_consistency'] = consistency
            else:
                trend_consistency[f'{name}_trend_consistency'] = 0.0

        # 4. 计算约束条件的稳定性（变化系数的倒数）
        stability = {}
        for name in constraint_names:
            series = constraint_series[name]
            if len(series) > 0 and np.mean(series) != 0:
                cv = np.std(series) / np.abs(np.mean(series))  # 变化系数
                stability[f'{name}_stability'] = 1.0 / (1.0 + cv)  # 稳定性得分，范围(0,1]
            else:
                stability[f'{name}_stability'] = 0.0

        return {
            'constraint_correlations': constraint_correlations,
            'trend_consistency': trend_consistency,
            'stability': stability,
            'constraint_means': {name: np.mean(series) if series else 0 for name, series in constraint_series.items()},
            'constraint_stds': {name: np.std(series) if series else 0 for name, series in constraint_series.items()}
        }

    # ==================== 4.12 数字特性交叉特征（v4.0） ====================

    def calculate_digit_characteristic_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算数字特性交叉特征
        分析数字特性（奇偶、质合、大小等）在不同位置之间的分布和关联
        扩展数字特性分析维度，提升特征规律识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 分析窗口期数（默认30期）

        Returns:
            数字特性交叉特征字典，包含各数字特性在位置之间的分布关联
        """
        if not data:
            return {'error': '数据为空'}

        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)

        if total_window == 0:
            return {'error': '窗口数据为空'}

        characteristic_features = {}

        # 定义数字特性
        characteristics = {
            'is_odd': lambda x: x % 2 == 1,
            'is_even': lambda x: x % 2 == 0,
            'is_prime': lambda x: x in self.primes,
            'is_composite': lambda x: x in self.composites,
            'is_low': lambda x: x < 5,    # 0-4为小
            'is_high': lambda x: x >= 5,  # 5-9为大
            'is_road_0': lambda x: x in self.road_0,
            'is_road_1': lambda x: x in self.road_1,
            'is_road_2': lambda x: x in self.road_2
        }

        # 1. 计算每个位置各特性的出现频率
        position_characteristic_ratios = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            position_characteristic_ratios[pos_name] = {}
            
            for char_name, char_func in characteristics.items():
                # 计算该位置在窗口期内具有该特性的频率
                count = 0
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num = int(numbers[pos])
                        if char_func(num):
                            count += 1
                
                ratio = count / total_window if total_window > 0 else 0
                position_characteristic_ratios[pos_name][char_name] = ratio

        # 2. 计算位置间特性相关性（同一特性在不同位置之间的关联）
        position_characteristic_correlations = {}
        for char_name in characteristics.keys():
            # 提取每个位置该特性的时间序列
            position_sequences = {}
            for pos in range(self.positions):
                pos_name = self.position_names[pos]
                sequence = []
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num = int(numbers[pos])
                        sequence.append(1 if char_func(num) else 0)
                position_sequences[pos_name] = sequence
            
            # 计算位置间相关性
            for i in range(self.positions):
                for j in range(i+1, self.positions):
                    pos_i_name = self.position_names[i]
                    pos_j_name = self.position_names[j]
                    seq_i = position_sequences[pos_i_name]
                    seq_j = position_sequences[pos_j_name]
                    
                    if len(seq_i) > 1 and len(seq_j) > 1:
                        try:
                            corr = np.corrcoef(seq_i, seq_j)[0, 1]
                            if np.isnan(corr):
                                corr = 0.0
                        except:
                            corr = 0.0
                            
                        position_characteristic_correlations[f'{char_name}_{pos_i_name}_{pos_j_name}'] = round(corr, 4)

        # 3. 计算特性组合模式（例如：奇数+质数、大数+偶数等）
        combination_patterns = {}
        # 定义一些有趣的特性组合
        characteristic_combinations = [
            ('is_odd', 'is_prime'),      # 奇质数
            ('is_even', 'is_prime'),     # 偶质数（只有2）
            ('is_odd', 'is_composite'),  # 奇合数
            ('is_even', 'is_composite'), # 偶合数
            ('is_low', 'is_odd'),        # 小奇数
            ('is_low', 'is_even'),       # 小偶数
            ('is_high', 'is_odd'),       # 大奇数
            ('is_high', 'is_even'),      # 大偶数
            ('is_road_0', 'is_odd'),     # 0路奇数
            ('is_road_0', 'is_even'),    # 0路偶数
            ('is_road_1', 'is_odd'),     # 1路奇数
            ('is_road_1', 'is_even'),    # 1路偶数
            ('is_road_2', 'is_odd'),     # 2路奇数
            ('is_road_2', 'is_even'),    # 2路偶数
        ]

        for char1_name, char2_name in characteristic_combinations:
            char1_func = characteristics[char1_name]
            char2_func = characteristics[char2_name]
            
            # 计算该组合在每个位置的出现频率
            position_combination_ratios = {}
            for pos in range(self.positions):
                pos_name = self.position_names[pos]
                count = 0
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num = int(numbers[pos])
                        if char1_func(num) and char2_func(num):
                            count += 1
                ratio = count / total_window if total_window > 0 else 0
                position_combination_ratios[pos_name] = ratio
            
            combination_patterns[f'{char1_name}_{char2_name}'] = position_combination_ratios

        # 4. 计算位置间特性组合的一致性
        combination_consistency = {}
        for char1_name, char2_name in characteristic_combinations:
            combination_key = f'{char1_name}_{char2_name}'
            if combination_key in combination_patterns:
                # 检查该组合在各位置的一致性（所有位置是否有相似的出现频率）
                ratios = list(combination_patterns[combination_key].values())
                if len(ratios) > 0:
                    # 计算变异系数的倒数作为一致性度量
                    mean_ratio = np.mean(ratios)
                    if mean_ratio > 0:
                        std_ratio = np.std(ratios)
                        cv = std_ratio / mean_ratio
                        consistency = 1.0 / (1.0 + cv)  # 一致性得分，范围(0,1]
                    else:
                        consistency = 0.0
                else:
                    consistency = 0.0
                combination_consistency[combination_key] = consistency

        return {
            'position_characteristic_ratios': position_characteristic_ratios,
            'position_characteristic_correlations': position_characteristic_correlations,
            'characteristic_combination_ratios': combination_patterns,
            'combination_consistency': combination_consistency
        }

    # ==================== 4.13 频率分布交叉特征（v4.0） ====================

    def calculate_joint_frequency_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算频率分布交叉特征
        分析号码联合频率分布和条件概率
        扩展频率分析维度，提升联合特征识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 分析窗口期数（默认30期）

        Returns:
            频率分布交叉特征字典，包含联合频率、条件概率和频率关联度量
        """
        if not data:
            return {'error': '数据为空'}

        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)

        if total_window == 0:
            return {'error': '窗口数据为空'}

        joint_features = {}

        # 1. 计算联合频率：两个位置上特定号码组合出现的频率
        joint_frequencies = {}
        for pos1 in range(self.positions):
            for pos2 in range(pos1+1, self.positions):  # 避免重复和自我组合
                pos1_name = self.position_names[pos1]
                pos2_name = self.position_names[pos2]
                key = f'{pos1_name}_{pos2_name}'
                
                # 初始化联合频率字典
                joint_freq = {}
                for num1 in self.number_range:
                    for num2 in self.number_range:
                        joint_freq[f'{num1},{num2}'] = 0
                
                # 统计出现次数
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        num1 = int(numbers[pos1])
                        num2 = int(numbers[pos2])
                        joint_freq[f'{num1},{num2}'] += 1
                
                # 转换为频率
                for key in joint_freq:
                    joint_freq[key] = joint_freq[key] / total_window if total_window > 0 else 0
                
                joint_frequencies[key] = joint_freq

        # 2. 计算边际频率（单个位置上号码出现的频率）
        marginal_frequencies = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            marginal_freq = {num: 0 for num in self.number_range}
            
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    num = int(numbers[pos])
                    marginal_freq[num] += 1
            
            # 转换为频率
            for num in self.number_range:
                marginal_freq[num] = marginal_freq[num] / total_window if total_window > 0 else 0
            
            marginal_frequencies[pos_name] = marginal_freq

        # 3. 计算条件概率：P(pos1=num1 | pos2=num2)
        conditional_probabilities = {}
        for pos1 in range(self.positions):
            for pos2 in range(self.positions):
                if pos1 != pos2:  # 不同位置之间
                    pos1_name = self.position_names[pos1]
                    pos2_name = self.position_names[pos2]
                    key = f'{pos1_name}_given_{pos2_name}'  # P(pos1|pos2)
                    
                    conditional_prob = {}
                    for num1 in self.number_range:
                        conditional_prob[num1] = {}
                        for num2 in self.number_range:
                            # P(pos1=num1 | pos2=num2) = P(pos1=num1, pos2=num2) / P(pos2=num2)
                            # 确定联合频率的键（总是用较小的位置索引在前）
                            joint_pos1 = min(pos1, pos2)
                            joint_pos2 = max(pos1, pos2)
                            joint_key = f'{self.position_names[joint_pos1]}_{self.position_names[joint_pos2]}'
                            
                            if pos1 == joint_pos1 and pos2 == joint_pos2:
                                # 顺序匹配：pos1在前，pos2在后
                                joint_num_key = f'{num1},{num2}'
                            else:
                                # 顺序不匹配：需要交换
                                joint_num_key = f'{num2},{num1}'
                            
                            joint_prob = joint_frequencies.get(joint_key, {}).get(joint_num_key, 0)
                            marginal_prob = marginal_frequencies[pos2_name].get(num2, 0)
                            
                            if marginal_prob > 0:
                                conditional_prob[num1][num2] = joint_prob / marginal_prob
                            else:
                                conditional_prob[num1][num2] = 0
                    
                    conditional_probabilities[key] = conditional_prob

        # 4. 计算频率关联度量：互信息、 chi-square统计量等
        association_measures = {}
        for pos1 in range(self.positions):
            for pos2 in range(pos1+1, self.positions):
                pos1_name = self.position_names[pos1]
                pos2_name = self.position_names[pos2]
                key = f'{pos1_name}_{pos2_name}'
                
                # 互信息
                mutual_info = 0.0
                for num1 in self.number_range:
                    for num2 in self.number_range:
                        joint_key = f'{num1},{num2}'
                        joint_prob = joint_frequencies.get(key, {}).get(joint_key, 0)
                        prob1 = marginal_frequencies[pos1_name].get(num1, 0)
                        prob2 = marginal_frequencies[pos2_name].get(num2, 0)
                        
                        if joint_prob > 0 and prob1 > 0 and prob2 > 0:
                            mutual_info += joint_prob * np.log2(joint_prob / (prob1 * prob2))
                
                # 方差关联系数（简化版）
                # 这里我们计算号码值之间的线性关联
                seq1 = []
                seq2 = []
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        seq1.append(int(numbers[pos1]))
                        seq2.append(int(numbers[pos2]))
                
                pearson_corr = 0.0
                if len(seq1) > 1:
                    try:
                        pearson_corr = np.corrcoef(seq1, seq2)[0, 1]
                        if np.isnan(pearson_corr):
                            pearson_corr = 0.0
                    except:
                        pearson_corr = 0.0
                
                association_measures[key] = {
                    'mutual_information': round(max(mutual_info, 0), 4),
                    'pearson_correlation': round(pearson_corr, 4)
                }

        # 5. 计算频率分布的均匀性偏离度
        uniformity_deviation = {}
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            # 理想均匀分布下每个号码的频率应该是 1/10 = 0.1
            expected_freq = 0.1
            actual_freqs = list(marginal_frequencies[pos_name].values())
            
            # 卡方统计量（简化版，不考虑自由度）
            chi_square = 0.0
            for freq in actual_freqs:
                if expected_freq > 0:
                    chi_square += ((freq - expected_freq) ** 2) / expected_freq
            
            # 均匀性偏离度：越小越均匀
            uniformity_deviation[pos_name] = round(chi_square, 4)

        return {
            'joint_frequencies': joint_frequencies,
            'marginal_frequencies': marginal_frequencies,
            'conditional_probabilities': conditional_probabilities,
            'association_measures': association_measures,
            'uniformity_deviation': uniformity_deviation
        }

    # ==================== 4.14 趋势关联交叉特征（v4.0） ====================

    def calculate_trend_association_features(self, data: List[Dict], lookback: int = 30) -> Dict[str, Any]:
        """
        计算趋势关联交叉特征
        分析不同维度趋势之间的关联和同步性
        扩展趋势分析维度，提升趋势规律识别能力

        Args:
            data: 历史数据列表（按时间正序排列）
            lookback: 分析窗口期数（默认30期）

        Returns:
            趋势关联交叉特征字典，包含各维度趋势之间的关联分析
        """
        if not data:
            return {'error': '数据为空'}

        # 限制分析窗口
        window_data = data[-lookback:] if len(data) > lookback else data
        total_window = len(window_data)

        if total_window == 0:
            return {'error': '窗口数据为空'}

        trend_features = {}

        # 1. 提取各种趋势时间序列
        trend_series = {
            # 位置趋势：每个位置号码的趋势
            'position_trends': {},
            # 和值趋势
            'sum_trend': [],
            # 跨度趋势
            'span_trend': [],
            # 奇偶比例趋势
            'odd_even_trend': [],
            # 质数比例趋势
            'prime_trend': [],
            # 012路分布趋势（熵）
            '012_entropy_trend': [],
            # 区间分布趋势（熵）
            'interval_entropy_trend': []
        }

        # 提取位置趋势
        for pos in range(self.positions):
            pos_name = self.position_names[pos]
            position_sequence = []
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    position_sequence.append(int(numbers[pos]))
            trend_series['position_trends'][pos_name] = position_sequence

        # 提取和值趋势
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                trend_series['sum_trend'].append(sum(nums))

        # 提取跨度趋势
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                trend_series['span_trend'].append(max(nums) - min(nums))

        # 提取奇偶比例趋势
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                odd_count = sum(1 for n in nums if n % 2 == 1)
                trend_series['odd_even_trend'].append(odd_count / self.positions)

        # 提取质数比例趋势
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                nums = [int(n) for n in numbers]
                prime_count = sum(1 for n in nums if n in self.primes)
                trend_series['prime_trend'].append(prime_count / self.positions)

        # 提取012路分布趋势（使用香农熵）
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                road_counts = [0, 0, 0]  # 0路, 1路, 2路
                for n in numbers:
                    n_int = int(n)
                    if n_int in self.road_0:
                        road_counts[0] += 1
                    elif n_int in self.road_1:
                        road_counts[1] += 1
                    else:
                        road_counts[2] += 1
                
                # 计算香农熵
                total = sum(road_counts)
                if total > 0:
                    proportions = [c/total for c in road_counts if c > 0]
                    entropy = -sum(p * np.log2(p) for p in proportions) if proportions else 0
                else:
                    entropy = 0
                trend_series['012_entropy_trend'].append(entropy)

        # 提取区间分布趋势（使用香农熵）
        for item in window_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                interval_counts = {'low': 0, 'mid': 0, 'high': 0}
                for n in numbers:
                    n_int = int(n)
                    if n_int in self.intervals['low']:
                        interval_counts['low'] += 1
                    elif n_int in self.intervals['mid']:
                        interval_counts['mid'] += 1
                    else:
                        interval_counts['high'] += 1
                
                # 计算香农熵
                total = sum(interval_counts.values())
                if total > 0:
                    proportions = [c/total for c in interval_counts.values() if c > 0]
                    entropy = -sum(p * np.log2(p) for p in proportions) if proportions else 0
                else:
                    entropy = 0
                trend_series['interval_entropy_trend'].append(entropy)

        # 2. 计算趋势之间的关联性
        trend_associations = {}
        trend_names = list(trend_series.keys())
        
        # 位置趋势之间的关联
        position_trends = trend_series['position_trends']
        for pos1_name in position_trends:
            for pos2_name in position_trends:
                if pos1_name != pos2_name:
                    seq1 = position_trends[pos1_name]
                    seq2 = position_trends[pos2_name]
                    if len(seq1) > 1 and len(seq2) > 1:
                        try:
                            corr = np.corrcoef(seq1, seq2)[0, 1]
                            if np.isnan(corr):
                                corr = 0.0
                        except:
                            corr = 0.0
                        trend_associations[f'position_{pos1_name}_{pos2_name}'] = round(corr, 4)
        
        # 位置趋势与其他趋势的关联
        for pos_name in position_trends:
            pos_seq = position_trends[pos_name]
            for trend_name in trend_names:
                if trend_name != 'position_trends':
                    other_seq = trend_series[trend_name]
                    if len(pos_seq) > 1 and len(other_seq) > 1:
                        try:
                            corr = np.corrcoef(pos_seq, other_seq)[0, 1]
                            if np.isnan(corr):
                                corr = 0.0
                        except:
                            corr = 0.0
                        trend_associations[f'position_{pos_name}_{trend_name}'] = round(corr, 4)
        
        # 其他趋势之间的关联
        other_trend_names = [name for name in trend_names if name != 'position_trends']
        for i in range(len(other_trend_names)):
            for j in range(i+1, len(other_trend_names)):
                name_i = other_trend_names[i]
                name_j = other_trend_names[j]
                seq_i = trend_series[name_i]
                seq_j = trend_series[name_j]
                if len(seq_i) > 1 and len(seq_j) > 1:
                    try:
                        corr = np.corrcoef(seq_i, seq_j)[0, 1]
                        if np.isnan(corr):
                            corr = 0.0
                    except:
                        corr = 0.0
                    trend_associations[f'{name_i}_{name_j}'] = round(corr, 4)

        # 3. 计算趋势同步性：多个趋势同时转折的程度
        syncronicity = {}
        # 为了简化，我们检查趋势方向的一致性
        trend_directions = {}
        
        # 处理位置趋势（字典形式）
        if 'position_trends' in trend_series:
            position_trends_dict = trend_series['position_trends']
            for pos_name, position_series in position_trends_dict.items():
                if len(position_series) >= 3:
                    # 计算最近的趋势方向（最近3点的线性趋势）
                    recent = position_series[-3:] if len(position_series) >= 3 else position_series
                    if len(recent) >= 2:
                        x = list(range(len(recent)))
                        y = recent
                        try:
                            slope = np.polyfit(x, y, 1)[0]
                            if slope > 0.1:
                                direction = 'up'
                            elif slope < -0.1:
                                direction = 'down'
                            else:
                                direction = 'stable'
                        except:
                            direction = 'stable'
                    else:
                        direction = 'stable'
                    trend_directions[f'position_{pos_name}'] = direction
                else:
                    trend_directions[f'position_{pos_name}'] = 'insufficient_data'
        
        # 处理其他趋势（列表形式）
        for name, series in trend_series.items():
            if name == 'position_trends':
                continue  # 已经单独处理过
            if len(series) >= 3:
                # 计算最近的趋势方向（最近3点的线性趋势）
                recent = series[-3:] if len(series) >= 3 else series
                if len(recent) >= 2:
                    x = list(range(len(recent)))
                    y = recent
                    try:
                        slope = np.polyfit(x, y, 1)[0]
                        if slope > 0.1:
                            direction = 'up'
                        elif slope < -0.1:
                            direction = 'down'
                        else:
                            direction = 'stable'
                    except:
                        direction = 'stable'
                else:
                    direction = 'stable'
                trend_directions[name] = direction
            else:
                trend_directions[name] = 'insufficient_data'
        
        # 计算方向一致性：有多少趋势在同一方向上（不考虑'insufficient_data'）
        valid_directions = [d for d in trend_directions.values() if d in ['up', 'down', 'stable']]
        if valid_directions:
            up_count = valid_directions.count('up')
            down_count = valid_directions.count('down')
            stable_count = valid_directions.count('stable')
            max_same_direction = max(up_count, down_count, stable_count)
            syncronicity['direction_consistency'] = max_same_direction / len(valid_directions)
        else:
            syncronicity['direction_consistency'] = 0.0
        
        # 计算趋势强度的一致性（标准化后的斜率绝对值的一致性）
        trend_strengths = {}
        
        # 处理位置趋势强度
        if 'position_trends' in trend_series:
            position_trends_dict = trend_series['position_trends']
            for pos_name, position_series in position_trends_dict.items():
                if len(position_series) >= 2:
                    # 计算标准化的趋势强度（斜率的绝对值）
                    x = list(range(len(position_series)))
                    y = position_series
                    try:
                        slope = np.polyfit(x, y, 1)[0]
                        # 标准化：除以序列的标准差或均值
                        scale = np.std(position_series) if np.std(position_series) > 0 else np.mean(position_series) if np.mean(position_series) != 0 else 1
                        strength = abs(slope) / scale if scale != 0 else 0
                    except:
                        strength = 0
                    trend_strengths[f'position_{pos_name}'] = strength
                else:
                    trend_strengths[f'position_{pos_name}'] = 0
        
        # 处理其他趋势强度
        for name, series in trend_series.items():
            if name == 'position_trends':
                continue  # 已经单独处理过
            if len(series) >= 2:
                # 计算标准化的趋势强度（斜率的绝对值）
                x = list(range(len(series)))
                y = series
                try:
                    slope = np.polyfit(x, y, 1)[0]
                    # 标准化：除以序列的标准差或均值
                    scale = np.std(series) if np.std(series) > 0 else np.mean(series) if np.mean(series) != 0 else 1
                    strength = abs(slope) / scale if scale != 0 else 0
                except:
                    strength = 0
                trend_strengths[name] = strength
            else:
                trend_strengths[name] = 0
        
        if trend_strengths:
            strengths = list(trend_strengths.values())
            if np.mean(strengths) > 0:
                strength_cv = np.std(strengths) / np.mean(strengths)
                syncronicity['strength_consistency'] = 1.0 / (1.0 + strength_cv)  # 一致性得分
            else:
                syncronicity['strength_consistency'] = 0.0
        else:
            syncronicity['strength_consistency'] = 0.0

        # 准备返回的趋势序列（最近5值）
        trend_series_recent = {}
        for name, series in trend_series.items():
            if name == 'position_trends':
                # 处理位置趋势字典
                position_recent = {}
                for pos_name, position_series in series.items():
                    if len(position_series) >= 5:
                        position_recent[pos_name] = position_series[-5:]
                    else:
                        position_recent[pos_name] = position_series
                trend_series_recent[name] = position_recent
            else:
                # 处理其他趋势列表
                if len(series) >= 5:
                    trend_series_recent[name] = series[-5:]
                else:
                    trend_series_recent[name] = series
        
        return {
            'trend_series': trend_series_recent,  # 最近5值
            'trend_associations': trend_associations,
            'trend_directions': trend_directions,
            'syncronicity': syncronicity,
            'trend_strengths': trend_strengths
        }

    # ==================== 5. 综合特征提取 ====================

    def extract_all_features(self, data: List[Dict], windows: List[int] = [5, 10, 20, 50]) -> Dict[str, Any]:
        """
        提取所有特征

        Args:
            data: 历史数据列表（按时间正序排列）
            windows: 滑动窗口大小列表

        Returns:
            完整特征字典
        """
        logger.info('开始提取所有特征...')

        all_features = {
            'extract_time': datetime.now().isoformat(),
            'data_count': len(data)
        }

        # 1. 基础统计特征
        logger.info('提取基础统计特征...')
        all_features['frequency'] = self.calculate_frequency_features(data)
        all_features['omission'] = self.calculate_omission_features(data)

        # 2. 高级统计特征
        logger.info('提取高级统计特征...')
        all_features['road_012'] = self.calculate_012_road_features(data)
        all_features['consecutive'] = self.calculate_consecutive_features(data)
        all_features['repeat'] = self.calculate_repeat_features(data)
        all_features['interval'] = self.calculate_interval_features(data)

        # 3. 时序特征
        logger.info('提取时序特征...')
        all_features['sliding_window'] = self.calculate_sliding_window_features(data, windows)
        all_features['trend'] = self.calculate_trend_features(data)
        all_features['rolling_statistics'] = self.calculate_rolling_statistics_features(data)

        # 4. 交叉特征
        logger.info('提取交叉特征...')
        all_features['cross_position'] = self.calculate_cross_position_features(data)
        all_features['sum_span'] = self.calculate_sum_span_features(data)

        # 4.5. 位置链特征（v3.70）
        logger.info('提取位置链特征...')
        all_features['position_chain'] = self.calculate_position_chain_features(data)

        # 4.6. 形态标记特征（v3.70）
        logger.info('提取形态标记特征...')
        all_features['recent_form'] = self.calculate_recent_form_features(data)

        # 4.7. 时序特征
        logger.info('提取时序特征...')
        all_features['timing'] = self.calculate_timing_features(data)

        # 4.8. 交互特征
        logger.info('提取交互特征...')
        all_features['interaction'] = self.calculate_interaction_features(data)
        all_features['position_pair'] = self.calculate_position_pair_features(data)

        # 4.9. 状态特征
        logger.info('提取状态特征...')
        all_features['state'] = self.calculate_state_features(data)

        # 4.10. 上下文特征
        logger.info('提取上下文特征...')
        all_features['contextual'] = self.calculate_contextual_features(data)

        # 4.11. 组合约束交叉特征（v4.0）
        logger.info('提取组合约束交叉特征...')
        all_features['constraint_correlation'] = self.calculate_constraint_correlation_features(data)

        # 4.12. 数字特性交叉特征（v4.0）
        logger.info('提取数字特性交叉特征...')
        all_features['digit_characteristic'] = self.calculate_digit_characteristic_features(data)

        # 4.13. 频率分布交叉特征（v4.0）
        logger.info('提取频率分布交叉特征...')
        all_features['joint_frequency'] = self.calculate_joint_frequency_features(data)

        # 4.14. 趋势关联交叉特征（v4.0）
        logger.info('提取趋势关联交叉特征...')
        all_features['trend_association'] = self.calculate_trend_association_features(data)

        logger.info('特征提取完成')
        return all_features

    def calculate_bayesian_features(self, data, prior_weights=None, window=30):
        """贝叶斯特征计算 (v3.0 新增)

        基于历史频率构建先验分布,根据近期验证反馈更新后验概率。

        核心思想:
        - 先验概率 prior: 基于长期历史频率分布
        - 似然值 likelihood: 基于近期 window 期窗口内的观测频率
        - 后验概率 posterior: 通过贝叶斯公式更新: P(A|B) = P(B|A)*P(A) / P(B)
        - 意外度 surprise: posterior - prior,衡量号码出现的"出人意料"程度

        Args:
            data: 历史开奖数据列表
            prior_weights: 先验权重字典 {position: {number: prior_prob}}
                           如果为None,则基于全局历史频率自动计算先验
            window: 验证窗口期数,用于计算似然值

        Returns:
            Bayesian特征字典,格式:
            {
                'positions': [
                    {
                        'prior': {0:0.1, 1:0.1, ...},  # 先验概率
                        'likelihood': {0:0.12, 1:0.08, ...},  # 似然值
                        'posterior': {0:0.11, 1:0.09, ...},  # 后验概率
                        'evidence': 0.10,  # 边际概率
                        'surprise': {0:0.1, 1:-0.01, ...}  # 意外度 = posterior - prior
                    },
                    ...
                ]
            }
        """
        if not data:
            logger.warning('贝叶斯特征计算: 数据为空')
            return {'positions': [{} for _ in range(self.positions)]}

        total = len(data)
        # 使用窗口内数据计算似然
        window_data = data[-window:] if window and len(data) > window else data
        window_count = len(window_data)

        # ---- Step 1: 计算先验概率 ----
        global_counts = [defaultdict(int) for _ in range(self.positions)]
        for item in data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    global_counts[pos][int(num)] += 1

        # 优先使用传入的先验权重,否则基于全局频率构建
        prior_weights_raw = prior_weights or {}
        priors = []
        for pos in range(self.positions):
            pos_name = self.position_names[pos]

            if pos_name in prior_weights_raw:
                prior = {str(n): prior_weights_raw[pos_name].get(str(n), 0.1) for n in self.number_range}
            else:
                counts = global_counts[pos]
                prior = {str(n): (counts.get(n, 0) + 1) / (total + 10) for n in self.number_range}

            # 归一化
            total_prior = sum(prior.values())
            prior = {k: v / total_prior for k, v in prior.items()}
            priors.append(prior)

        # ---- Step 2: 计算似然值 ----
        likelihoods = []
        for pos in range(self.positions):
            counts = defaultdict(int)
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    counts[int(numbers[pos])] += 1

            likelihood = {str(n): (counts.get(n, 0) + 1) / (window_count + 10) for n in self.number_range}
            total_like = sum(likelihood.values())
            likelihood = {k: v / total_like for k, v in likelihood.items()}
            likelihoods.append(likelihood)

        # ---- Step 3: 计算后验概率 & 边际概率 ----
        bayesian_positions = []
        for pos in range(self.positions):
            prior = priors[pos]
            likelihood = likelihoods[pos]

            posterior = {}
            evidence = 0.0
            surprise = {}

            for key in self.number_range:
                p_key = str(key)
                l_val = likelihood[p_key]
                p_val = prior[p_key]
                # Bayes: posterior ∝ likelihood * prior
                # v3.60 修复: 改用 log-space 累加, 防止长期运行后似然无界增长导致后验失真.
                # 原实现 posterior += l_val * p_val 随样本增多方差膨胀; log-space 等价但数值稳定.
                log_post = math.log(max(l_val, 1e-12)) + math.log(max(p_val, 1e-12))
                posterior[p_key] = log_post
                evidence += log_post
                # 意外度
                surprise[p_key] = log_post - math.log(max(p_val, 1e-12))

            # 归一化后验: exp(log_post - max) 防下溢, 等价于 softmax
            max_log = max(posterior.values()) if posterior else 0.0
            exp_post = {k: math.exp(v - max_log) for k, v in posterior.items()}
            total_exp = sum(exp_post.values()) or 1.0
            posterior = {k: v / total_exp for k, v in exp_post.items()}
            # 重新计算意外度 (posterior 已是概率空间)
            surprise = {k: posterior[k] - prior[k] for k in posterior}
            # evidence 转为概率空间的边际
            evidence = sum(posterior.values())  # 归一化后 = 1.0

            bayesian_positions.append({
                'prior': prior,
                'likelihood': likelihood,
                'posterior': posterior,
                'evidence': round(evidence, 6),
                'surprise': surprise,
            })

        return {'positions': bayesian_positions}

    def calculate_feature_importance(self, data, top_n=5):
        """计算特征重要性得分 (v3.0 新增)

        基于各特征在历史预测中的区分能力来计算重要性。
        使用熵减和互信息方法评估每个特征的重要性。

        核心方法:
        - 对各特征类型(频率/遗漏/趋势等),分别计算其信息熵
        - 使用互信息衡量特征与目标变量(下期开奖号码)的依赖程度
        - 互信息越高,说明该特征的预测价值越大

        Args:
            data: 历史开奖数据列表
            top_n: 返回最重要的前N个特征类型

        Returns:
            {
                'feature_importance': {
                    'frequency': 0.35,
                    'omission': 0.25,
                    'trend': 0.12,
                    'markov': 0.10,
                    'pattern': 0.08,
                    'bayesian': 0.10,
                },
                'ranking': ['frequency', 'omission', 'trend', ...],
            }
        """
        if not data or len(data) < 10:
            logger.warning('特征重要性计算: 数据不足')
            return {
                'feature_importance': {},
                'ranking': [],
            }

        feature_types = ['frequency', 'omission', 'trend', 'markov', 'pattern', 'bayesian']
        scores = {}

        for ft in feature_types:
            try:
                score = self._compute_feature_score(data, ft)
                scores[ft] = max(score, 1e-9)  # 避免零值
            except Exception as e:
                logger.warning(f'特征重要性计算失败 [{ft}]: {e}')
                scores[ft] = 1e-9  # 兜底最小分数

        total = sum(scores.values())
        if total > 0:
            importance = {k: round(v / total, 4) for k, v in scores.items()}
        else:
            importance = {k: 1.0 / len(feature_types) for k in feature_types}

        ranking = sorted(importance.keys(), key=lambda x: importance[x], reverse=True)

        # 返回 top_n
        top_importance = {k: importance[k] for k in ranking[:top_n]}
        top_ranking = ranking[:top_n]

        return {
            'feature_importance': top_importance,
            'ranking': top_ranking,
        }

    def _calculate_entropy(self, distribution):
        """计算信息熵,用于特征重要性评估

        H(X) = -Σ p(x) * log2(p(x))

        Args:
            distribution: 概率分布字典或列表

        Returns:
            信息熵值 (float),若输入无效则返回0
        """
        if isinstance(distribution, dict):
            probs = list(distribution.values())
        elif hasattr(distribution, '__iter__'):
            probs = list(distribution)
        else:
            return 0.0

        # 过滤非正数
        probs = [p for p in probs if p > 0]
        if not probs:
            return 0.0

        try:
            entropy = -sum(p * np.log2(p) for p in probs)
            return float(entropy)
        except (ValueError, OverflowError):
            return 0.0

    def _mutual_information(self, x_values, y_values):
        """计算互信息,评估两个变量之间的依赖程度

        MI(X;Y) = Σ Σ p(x,y) * log2(p(x,y) / (p(x)*p(y)))

        离散变量的互信息估计,用于衡量特征与目标的相关性。

        Args:
            x_values: 第一个变量取值列表
            y_values: 第二个变量取值列表

        Returns:
            互信息值 (float),若无法计算则返回0
        """
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return 0.0

        n = len(x_values)
        pairs = Counter(zip(x_values, y_values))

        # 边缘概率
        px = Counter(x_values)
        py = Counter(y_values)

        mi = 0.0
        for (x, y), count in pairs.items():
            p_xy = count / n
            p_x = px[x] / n
            p_y = py[y] / n

            if p_x > 0 and p_y > 0:
                mi += p_xy * np.log2(p_xy / (p_x * p_y))

        return float(max(mi, 0.0))

    def _compute_feature_score(self, data, feature_type):
        """内部方法:计算单个特征类型的得分

        通过对每个位置的每个号码,比较该特征与下期实际开奖的关系,
        用互信息给出该特征的评分。

        Args:
            data: 历史数据
            feature_type: 特征类型名称

        Returns:
            特征得分 (float)
        """
        feature_seq_x = []
        target_seq_y = []

        for i in range(1, len(data)):
            feat_val = self._get_feature_representative_value(data, feature_type, i)
            target_num = int(data[i].get('numbers', [0])[0])

            if feat_val is not None:
                feature_seq_x.append(feat_val)
                target_seq_y.append(target_num)

        if len(feature_seq_x) < 5:
            return 1e-9

        mi = self._mutual_information(feature_seq_x, target_seq_y)
        feat_entropy = self._calculate_entropy(feature_seq_x)
        return mi * (1 + feat_entropy)

    def _get_feature_representative_value(self, data, feature_type, idx):
        """获取指定特征在给定数据点上的代表值

        Args:
            data: 历史数据
            feature_type: 特征类型
            idx: 数据索引

        Returns:
            代表数值,无法获取时返回None
        """
        if idx < 0 or idx >= len(data):
            return None

        if feature_type == 'frequency':
            fe = P5Features()
            freq = fe.calculate_frequency_features(data[:idx])
            pos_name = fe.position_names[0]
            if pos_name in freq:
                freq_data = freq[pos_name].get('frequencies', {})
                return int(max(freq_data, key=freq_data.get)) if freq_data else None

        elif feature_type == 'omission':
            fe = P5Features()
            om = fe.calculate_omission_features(data[:idx])
            pos_name = fe.position_names[0]
            if pos_name in om:
                om_data = om[pos_name].get('omission_probs', {})
                return int(max(om_data, key=om_data.get)) if om_data else None

        elif feature_type == 'trend':
            fe = P5Features()
            if len(data) >= 10:
                trends = fe.calculate_trend_features(data[:idx])
                pos_name = fe.position_names[0]
                if pos_name in trends:
                    t = trends[pos_name].get('recent_values', [])
                    return int(np.mean(t[-3:])) if t else None

        elif feature_type in ('markov', 'pattern'):
            if idx > 0:
                nums = data[idx - 1].get('numbers', [])
                return int(nums[0]) if nums else None

        elif feature_type == 'bayesian':
            fe = P5Features()
            bfeat = fe.calculate_bayesian_features(data[:idx])
            pos_data = bfeat.get('positions', [])
            if pos_data:
                surprise = pos_data[0].get('surprise', {})
                if surprise:
                    return int(max(surprise, key=surprise.get))

        if idx > 0 and data[idx - 1].get('numbers'):
            return int(data[idx - 1]['numbers'][0])

        return None

if __name__ == '__main__':
    # 测试特征工程
    import json

    # 模拟数据
    test_data = [
        {'issue': '2024001', 'numbers': [1, 2, 3, 4, 5]},
        {'issue': '2024002', 'numbers': [2, 3, 4, 5, 6]},
        {'issue': '2024003', 'numbers': [3, 4, 5, 6, 7]},
        {'issue': '2024004', 'numbers': [4, 5, 6, 7, 8]},
        {'issue': '2024005', 'numbers': [5, 6, 7, 8, 9]},
    ]

    fe = P5Features()
    features = fe.extract_all_features(test_data)

    print('特征提取完成')
    print(json.dumps(features, indent=2, ensure_ascii=False, default=str))
