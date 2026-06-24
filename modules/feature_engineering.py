"""
排列5特征工程模块

负责从历史数据中提取丰富的统计特征，为AI模型提供高质量输入。

核心特征：
1. 基础统计特征：频率、遗漏、冷热号
2. 高级统计特征：012路、连号、重隔号、区间分布
3. 时序特征：滑动窗口统计、趋势动量
4. 交叉特征：位置关联、组合约束
"""

import logging
import numpy as np
from collections import defaultdict, Counter
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class P5FeatureEngineering:
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
                    corr = np.corrcoef(seq_i, seq_j)[0, 1]
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
            'mean': np.mean(sums),
            'std': np.std(sums),
            'min': min(sums),
            'max': max(sums),
            'median': np.median(sums)
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

        # 4. 交叉特征
        logger.info('提取交叉特征...')
        all_features['cross_position'] = self.calculate_cross_position_features(data)
        all_features['sum_span'] = self.calculate_sum_span_features(data)

        logger.info('特征提取完成')
        return all_features

    def get_feature_vector(self, features: Dict[str, Any], position: int) -> np.ndarray:
        """
        获取指定位置的特征向量（用于机器学习模型输入）

        Args:
            features: 完整特征字典
            position: 位置索引（0-4）

        Returns:
            特征向量
        """
        pos_name = self.position_names[position]
        vector = []

        # 频率特征（10维）
        freq_features = features.get('frequency', {}).get(pos_name, {})
        frequencies = freq_features.get('frequencies', {})
        for num in self.number_range:
            vector.append(frequencies.get(num, 0))

        # 遗漏特征（10维）
        omission_features = features.get('omission', {}).get(pos_name, {})
        omission_probs = omission_features.get('omission_probs', {})
        for num in self.number_range:
            vector.append(omission_probs.get(num, 0))

        # 012路特征（3维）
        road_features = features.get('road_012', {}).get(pos_name, {})
        road_ratios = road_features.get('road_ratios', {})
        vector.extend([road_ratios.get(0, 0), road_ratios.get(1, 0), road_ratios.get(2, 0)])

        # 区间特征（3维）
        interval_features = features.get('interval', {}).get(pos_name, {})
        interval_ratios = interval_features.get('interval_ratios', {})
        vector.extend([
            interval_ratios.get('low', 0),
            interval_ratios.get('mid', 0),
            interval_ratios.get('high', 0)
        ])

        # 趋势特征（2维）
        trend_features = features.get('trend', {}).get(pos_name, {})
        vector.append(trend_features.get('slope', 0))
        vector.append(trend_features.get('momentum', 0))

        return np.array(vector)


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

    fe = P5FeatureEngineering()
    features = fe.extract_all_features(test_data)

    print('特征提取完成')
    print(json.dumps(features, indent=2, ensure_ascii=False, default=str))