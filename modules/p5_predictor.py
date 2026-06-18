"""
排列5下一期走势预测与号码预测模块

综合运用历史数据统计分析、概率模型、趋势识别等方法，
生成下一期号码的走势预测图表及号码预测结果。

核心能力：
1. 多算法融合预测 - 频率加权、遗漏回归、趋势动量、马尔可夫转移、形态延续
2. 可配置预测参数 - 支持调整各算法权重和预测窗口
3. 走势预测图表生成 - 可视化各位置号码概率分布与趋势预判
4. 号码组合推荐 - 基于综合概率模型输出高概率号码组合
"""

import logging
import os
import json
import math
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

os.makedirs('logs', exist_ok=True)
os.makedirs('reports', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/p5_predictor.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5PredictorConfig:
    """
    排列5预测器配置类

    支持调整各预测算法的权重和参数，实现可配置的预测策略。
    """

    DEFAULT_CONFIG = {
        # 算法开关与权重（权重总和不需要为1，内部会归一化）
        'algorithms': {
            'frequency_weighted': {
                'enabled': True,
                'weight': 0.25,
                'params': {
                    'lookback_periods': None,  # None表示使用全部历史数据
                    'smoothing_factor': 0.1    # 拉普拉斯平滑系数
                }
            },
            'omission_regression': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'max_omission_cap': 50,     # 遗漏值上限
                    'regression_steepness': 0.08  # 回归陡峭度
                }
            },
            'trend_momentum': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'trend_window': 10,         # 趋势观察窗口
                    'momentum_factor': 1.2      # 动量放大系数
                }
            },
            'markov_transition': {
                'enabled': True,
                'weight': 0.20,
                'params': {
                    'order': 1,                 # 马尔可夫阶数（1或2）
                    'decay_factor': 0.95        # 历史衰减因子
                }
            },
            'pattern_continuation': {
                'enabled': True,
                'weight': 0.15,
                'params': {
                    'pattern_window': 5,        # 形态观察窗口
                    'continuation_boost': 1.3   # 形态延续增强系数
                }
            }
        },
        # 全局参数
        'global': {
            'hot_threshold_percentile': 70,     # 热号百分位阈值
            'cold_threshold_percentile': 30,    # 冷号百分位阈值
            'combination_count': 10,            # 推荐组合数量
            'position_top_n': 3,                # 每位取Top-N号码组合
            'probability_calibration': True,    # 是否进行概率校准
            'min_data_required': 30             # 最小所需历史数据量
        }
    }

    def __init__(self, custom_config: Optional[Dict] = None):
        """
        初始化配置

        Args:
            custom_config: 自定义配置字典，会与默认配置合并
        """
        self.config = self._merge_config(self.DEFAULT_CONFIG.copy(), custom_config or {})
        self._validate_config()

    def _merge_config(self, base: Dict, override: Dict) -> Dict:
        """递归合并配置字典"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._merge_config(base[key], value)
            else:
                base[key] = value
        return base

    def _validate_config(self):
        """验证配置有效性"""
        total_weight = 0.0
        enabled_count = 0
        for algo_name, algo_cfg in self.config['algorithms'].items():
            if algo_cfg.get('enabled', False):
                total_weight += algo_cfg.get('weight', 0)
                enabled_count += 1
        if enabled_count == 0:
            logger.warning('所有预测算法均被禁用，将启用默认频率加权算法')
            self.config['algorithms']['frequency_weighted']['enabled'] = True
            self.config['algorithms']['frequency_weighted']['weight'] = 1.0
        logger.info(f'预测配置已加载: 启用{enabled_count}个算法, 总权重{total_weight:.2f}')

    def get_algorithm_weights(self) -> Dict[str, float]:
        """获取归一化后的算法权重"""
        weights = {}
        total = 0.0
        for name, cfg in self.config['algorithms'].items():
            if cfg.get('enabled', False):
                w = cfg.get('weight', 0)
                weights[name] = w
                total += w
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return weights

    def get_global_param(self, key: str, default=None):
        """获取全局参数"""
        return self.config['global'].get(key, default)

    def to_dict(self) -> Dict:
        """导出配置字典"""
        return self.config.copy()


class P5Predictor:
    """
    排列5预测器核心类

    基于多算法融合模型，预测下一期各位置号码的出现概率，
    生成走势预测数据和推荐号码组合。
    """

    def __init__(self, config: Optional[P5PredictorConfig] = None):
        """
        初始化预测器

        Args:
            config: 预测器配置，None则使用默认配置
        """
        self.config = config or P5PredictorConfig()
        self.positions = 5
        self.number_range = range(0, 10)
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.primes = {2, 3, 5, 7}
        self.composites = {0, 4, 6, 8, 9}

    def predict(self, history_data: List[Dict], current_issue: Optional[str] = None) -> Dict[str, Any]:
        """
        执行下一期预测

        Args:
            history_data: 历史开奖数据列表，按时间倒序排列（最新在前）
            current_issue: 当前最新期号，用于推导下期期号

        Returns:
            预测结果字典，包含各位置概率分布、推荐组合、走势预测等
        """
        if not history_data:
            return {'error': '历史数据为空，无法预测'}

        min_required = self.config.get_global_param('min_data_required', 30)
        if len(history_data) < min_required:
            logger.warning(f'历史数据量{len(history_data)}少于建议最小值{min_required}，预测结果可能不稳定')

        # 推导下期期号
        next_issue = self._infer_next_issue(history_data, current_issue)

        # 确保数据按时间正序排列（旧→新）用于趋势分析
        sorted_data = sorted(history_data, key=lambda x: str(x.get('issue', '')))

        # 执行各算法预测
        algorithm_probs = self._run_algorithms(sorted_data)

        # 融合各算法概率
        fused_probs = self._fuse_probabilities(algorithm_probs)

        # 生成推荐组合
        top_combinations = self._generate_combinations(fused_probs)

        # 走势预测分析
        trend_forecast = self._forecast_trend(sorted_data, fused_probs)

        # 预测摘要
        summary = self._generate_summary(fused_probs, top_combinations, next_issue)

        predict_uuid = str(uuid.uuid4())

        result = {
            'predict_uuid': predict_uuid,
            'target_issue': next_issue,
            'base_issue': current_issue or history_data[0].get('issue', ''),
            'predict_time': datetime.now().isoformat(),
            'algorithm_config': self.config.to_dict(),
            'algorithm_weights': self.config.get_algorithm_weights(),
            'algorithm_probs': algorithm_probs,
            'fused_probabilities': fused_probs,
            'top_combinations': top_combinations,
            'trend_forecast': trend_forecast,
            'summary': summary,
            'data_samples': len(history_data)
        }

        logger.info(f'预测完成: 目标期号{next_issue}, 推荐组合数{len(top_combinations)}')
        return result

    def _infer_next_issue(self, history_data: List[Dict], current_issue: Optional[str] = None) -> str:
        """推导下一期期号"""
        if current_issue:
            base = str(current_issue)
        elif history_data:
            base = str(history_data[0].get('issue', ''))
        else:
            base = ''

        if base and base.isdigit():
            next_num = int(base) + 1
            return str(next_num)
        return '未知'

    def _run_algorithms(self, sorted_data: List[Dict]) -> Dict[str, List[Dict[int, float]]]:
        """
        执行所有启用的预测算法

        Returns:
            算法名称 -> 各位置概率分布列表的字典
        """
        results = {}
        weights = self.config.get_algorithm_weights()

        if 'frequency_weighted' in weights:
            results['frequency_weighted'] = self._algo_frequency_weighted(sorted_data)
        if 'omission_regression' in weights:
            results['omission_regression'] = self._algo_omission_regression(sorted_data)
        if 'trend_momentum' in weights:
            results['trend_momentum'] = self._algo_trend_momentum(sorted_data)
        if 'markov_transition' in weights:
            results['markov_transition'] = self._algo_markov_transition(sorted_data)
        if 'pattern_continuation' in weights:
            results['pattern_continuation'] = self._algo_pattern_continuation(sorted_data)

        return results

    def _algo_frequency_weighted(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        频率加权算法

        基于历史出现频率计算概率，使用拉普拉斯平滑避免零概率。
        """
        params = self.config.config['algorithms']['frequency_weighted']['params']
        smoothing = params.get('smoothing_factor', 0.1)
        lookback = params.get('lookback_periods')

        use_data = data[-lookback:] if lookback else data
        total = len(use_data)

        # 统计各位置各号码出现次数
        counts = [defaultdict(int) for _ in range(self.positions)]
        for item in use_data:
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    counts[pos][int(num)] += 1

        # 计算概率（拉普拉斯平滑）
        probs = []
        for pos in range(self.positions):
            pos_probs = {}
            for num in self.number_range:
                count = counts[pos].get(num, 0)
                pos_probs[num] = (count + smoothing) / (total + smoothing * 10)
            probs.append(pos_probs)

        return probs

    def _algo_omission_regression(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        遗漏回归算法

        基于当前遗漏值，遗漏越大短期回归概率越高（指数衰减模型）。
        核心假设：长期未出现的号码在短期内出现概率会上升。
        """
        params = self.config.config['algorithms']['omission_regression']['params']
        max_cap = params.get('max_omission_cap', 50)
        steepness = params.get('regression_steepness', 0.08)

        # 计算各位置各号码当前遗漏值
        last_occurrence = [{} for _ in range(self.positions)]
        for idx, item in enumerate(data):
            numbers = item.get('numbers', [])
            if len(numbers) == self.positions:
                for pos, num in enumerate(numbers):
                    last_occurrence[pos][int(num)] = idx

        total = len(data)
        probs = []
        for pos in range(self.positions):
            pos_probs = {}
            omissions = {}
            for num in self.number_range:
                last_idx = last_occurrence[pos].get(num, -1)
                omission = total - 1 - last_idx if last_idx >= 0 else total
                omissions[num] = min(omission, max_cap)

            # 指数回归概率：遗漏越大，概率越高
            raw_scores = {num: math.exp(steepness * omissions[num]) for num in self.number_range}
            total_score = sum(raw_scores.values())
            for num in self.number_range:
                pos_probs[num] = raw_scores[num] / total_score if total_score > 0 else 0.1
            probs.append(pos_probs)

        return probs

    def _algo_trend_momentum(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        趋势动量算法

        分析最近N期的号码变化趋势，赋予沿趋势方向号码更高概率。
        例如：某位置近期号码呈上升趋势，则较大号码概率提升。
        """
        params = self.config.config['algorithms']['trend_momentum']['params']
        window = params.get('trend_window', 10)
        momentum_factor = params.get('momentum_factor', 1.2)

        recent = data[-window:] if len(data) >= window else data
        if len(recent) < 2:
            # 数据不足时返回均匀分布
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        probs = []
        for pos in range(self.positions):
            # 提取该位置近期序列
            seq = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    seq.append(int(numbers[pos]))

            if len(seq) < 2:
                probs.append({n: 0.1 for n in self.number_range})
                continue

            # 线性回归求趋势方向
            x = np.arange(len(seq))
            y = np.array(seq)
            slope = np.polyfit(x, y, 1)[0] if len(x) > 1 else 0

            # 根据趋势斜率调整概率
            pos_probs = {}
            last_val = seq[-1]
            for num in self.number_range:
                distance = num - last_val
                # 沿趋势方向的距离获得正向加成
                trend_score = 1.0 + momentum_factor * slope * distance / 9.0
                # 高斯衰减：离上期值越远概率越低
                gaussian_decay = math.exp(-0.5 * ((distance / 3.0) ** 2))
                pos_probs[num] = max(0.01, trend_score * gaussian_decay)

            # 归一化
            total_score = sum(pos_probs.values())
            for num in self.number_range:
                pos_probs[num] /= total_score
            probs.append(pos_probs)

        return probs

    def _algo_markov_transition(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        马尔可夫转移算法

        基于最近一期号码，计算各位置的状态转移概率。
        一阶马尔可夫：P(X_n+1 = j | X_n = i)
        """
        params = self.config.config['algorithms']['markov_transition']['params']
        order = params.get('order', 1)
        decay = params.get('decay_factor', 0.95)

        if len(data) < order + 1:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        last_item = data[-1]
        last_numbers = last_item.get('numbers', [])
        if len(last_numbers) != self.positions:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 构建转移矩阵
        transition_counts = [defaultdict(lambda: defaultdict(float)) for _ in range(self.positions)]

        for idx in range(order, len(data)):
            weight = decay ** (len(data) - idx)
            prev_item = data[idx - order]
            curr_item = data[idx]
            prev_nums = prev_item.get('numbers', [])
            curr_nums = curr_item.get('numbers', [])
            if len(prev_nums) == self.positions and len(curr_nums) == self.positions:
                for pos in range(self.positions):
                    p = int(prev_nums[pos])
                    c = int(curr_nums[pos])
                    transition_counts[pos][p][c] += weight

        probs = []
        for pos in range(self.positions):
            prev_num = int(last_numbers[pos])
            counts = transition_counts[pos].get(prev_num, {})
            total = sum(counts.values())

            pos_probs = {}
            if total > 0:
                for num in self.number_range:
                    pos_probs[num] = counts.get(num, 0) / total
            else:
                # 无转移记录时回退到均匀分布
                for num in self.number_range:
                    pos_probs[num] = 0.1
            probs.append(pos_probs)

        return probs

    def _algo_pattern_continuation(self, data: List[Dict]) -> List[Dict[int, float]]:
        """
        形态延续算法

        分析奇偶、大小、质合形态的近期延续规律，
        对符合延续趋势的号码给予概率加成。
        """
        params = self.config.config['algorithms']['pattern_continuation']['params']
        window = params.get('pattern_window', 5)
        boost = params.get('continuation_boost', 1.3)

        recent = data[-window:] if len(data) >= window else data
        if len(recent) < 2:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        # 分析各位置近期形态趋势
        probs = []
        for pos in range(self.positions):
            seq = []
            for item in recent:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    seq.append(int(numbers[pos]))

            if not seq:
                probs.append({n: 0.1 for n in self.number_range})
                continue

            # 统计近期奇偶、大小、质合占比
            odd_ratio = sum(1 for n in seq if n % 2 == 1) / len(seq)
            big_ratio = sum(1 for n in seq if n >= 5) / len(seq)
            prime_ratio = sum(1 for n in seq if n in self.primes) / len(seq)

            pos_probs = {}
            for num in self.number_range:
                score = 1.0
                # 奇偶延续
                is_odd = num % 2 == 1
                if is_odd and odd_ratio > 0.6:
                    score *= boost
                elif not is_odd and odd_ratio < 0.4:
                    score *= boost

                # 大小延续
                is_big = num >= 5
                if is_big and big_ratio > 0.6:
                    score *= boost
                elif not is_big and big_ratio < 0.4:
                    score *= boost

                # 质合延续
                is_prime = num in self.primes
                if is_prime and prime_ratio > 0.4:
                    score *= boost
                elif not is_prime and prime_ratio < 0.6:
                    score *= boost

                pos_probs[num] = score

            total_score = sum(pos_probs.values())
            for num in self.number_range:
                pos_probs[num] /= total_score
            probs.append(pos_probs)

        return probs

    def _fuse_probabilities(self, algorithm_probs: Dict[str, List[Dict[int, float]]]) -> List[Dict[int, float]]:
        """
        融合各算法概率

        使用加权平均融合各算法的预测结果，并进行概率校准。
        """
        weights = self.config.get_algorithm_weights()
        if not weights or not algorithm_probs:
            return [{n: 0.1 for n in self.number_range} for _ in range(self.positions)]

        fused = []
        for pos in range(self.positions):
            pos_fused = defaultdict(float)
            for algo_name, pos_probs in algorithm_probs.items():
                w = weights.get(algo_name, 0)
                if pos < len(pos_probs):
                    for num, prob in pos_probs[pos].items():
                        pos_fused[num] += w * prob

            # 归一化
            total = sum(pos_fused.values())
            if total > 0:
                pos_fused = {k: v / total for k, v in pos_fused.items()}
            else:
                pos_fused = {n: 0.1 for n in self.number_range}

            # 概率校准（确保每位概率和为1）
            if self.config.get_global_param('probability_calibration', True):
                pos_fused = self._calibrate_probabilities(pos_fused)

            fused.append(dict(pos_fused))

        return fused

    def _calibrate_probabilities(self, probs: Dict[int, float]) -> Dict[int, float]:
        """使用Softmax校准概率分布，使分布更平滑且和为1"""
        values = np.array([probs.get(n, 0) for n in self.number_range])
        # 防止数值下溢
        exp_vals = np.exp(values - np.max(values))
        softmax = exp_vals / np.sum(exp_vals)
        return {n: float(softmax[i]) for i, n in enumerate(self.number_range)}

    def _generate_combinations(self, fused_probs: List[Dict[int, float]]) -> List[Dict[str, Any]]:
        """
        生成推荐号码组合

        策略：每位选择Top-N高概率号码，通过笛卡尔积生成组合，
        按联合概率排序取前K个。
        """
        top_n = self.config.get_global_param('position_top_n', 3)
        count = self.config.get_global_param('combination_count', 10)

        # 每位取Top-N号码
        top_numbers = []
        for pos in range(self.positions):
            sorted_nums = sorted(fused_probs[pos].items(), key=lambda x: x[1], reverse=True)
            top_numbers.append(sorted_nums[:top_n])

        # 生成笛卡尔积组合
        from itertools import product
        combinations = []
        for combo in product(*[[num for num, _ in pos_top] for pos_top in top_numbers]):
            joint_prob = 1.0
            details = []
            for pos, num in enumerate(combo):
                prob = fused_probs[pos].get(num, 0)
                joint_prob *= prob
                details.append({
                    'position': pos + 1,
                    'position_name': self.position_names[pos],
                    'number': num,
                    'probability': round(prob, 6)
                })
            combinations.append({
                'combination': '-'.join(str(n) for n in combo),
                'numbers': list(combo),
                'joint_probability': joint_prob,
                'joint_probability_pct': round(joint_prob * 100, 4),
                'details': details
            })

        # 按联合概率降序排列
        combinations.sort(key=lambda x: x['joint_probability'], reverse=True)

        # 分配排名和置信度分数
        for idx, combo in enumerate(combinations[:count]):
            combo['rank'] = idx + 1
            # 置信度分数：基于排名和联合概率的对数映射到0-100
            log_prob = math.log10(combo['joint_probability'] + 1e-10)
            score = max(0, min(100, 60 + (-log_prob) * 10))
            combo['confidence_score'] = round(score, 2)

        return combinations[:count]

    def _forecast_trend(self, data: List[Dict], fused_probs: List[Dict[int, float]]) -> Dict[str, Any]:
        """
        走势预测分析

        基于历史数据和融合概率，预测各位置下一期的走势方向。
        """
        forecast = {
            'overall_direction': [],
            'position_forecasts': [],
            'hot_numbers': [],
            'cold_numbers': [],
            'pattern_prediction': {}
        }

        for pos in range(self.positions):
            probs = fused_probs[pos]
            sorted_nums = sorted(probs.items(), key=lambda x: x[1], reverse=True)

            hot_nums = [n for n, p in sorted_nums[:3]]
            cold_nums = [n for n, p in sorted_nums[-3:]]

            # 计算预期值
            expected = sum(n * p for n, p in probs.items())

            # 获取最近一期该位置号码
            recent_val = None
            if data:
                last_nums = data[-1].get('numbers', [])
                if len(last_nums) == self.positions:
                    recent_val = int(last_nums[pos])

            direction = '持平'
            if recent_val is not None:
                diff = expected - recent_val
                if diff > 0.5:
                    direction = '上升'
                elif diff < -0.5:
                    direction = '下降'

            forecast['position_forecasts'].append({
                'position': pos + 1,
                'position_name': self.position_names[pos],
                'expected_value': round(expected, 2),
                'recent_value': recent_val,
                'predicted_direction': direction,
                'hot_numbers': hot_nums,
                'cold_numbers': cold_nums,
                'max_probability': round(sorted_nums[0][1], 4),
                'max_prob_number': sorted_nums[0][0]
            })

            forecast['hot_numbers'].append({
                'position': pos + 1,
                'position_name': self.position_names[pos],
                'numbers': hot_nums
            })
            forecast['cold_numbers'].append({
                'position': pos + 1,
                'position_name': self.position_names[pos],
                'numbers': cold_nums
            })

        # 奇偶/大小/质合形态预测
        odd_even_pred = []
        big_small_pred = []
        prime_composite_pred = []
        for pos in range(self.positions):
            probs = fused_probs[pos]
            expected = sum(n * p for n, p in probs.items())
            nearest = round(expected)
            odd_even_pred.append('奇' if nearest % 2 == 1 else '偶')
            big_small_pred.append('大' if nearest >= 5 else '小')
            if nearest in self.primes:
                prime_composite_pred.append('质')
            elif nearest in self.composites:
                prime_composite_pred.append('合')
            else:
                prime_composite_pred.append('1')

        forecast['pattern_prediction'] = {
            'odd_even': '-'.join(odd_even_pred),
            'big_small': '-'.join(big_small_pred),
            'prime_composite': '-'.join(prime_composite_pred)
        }

        return forecast

    def _generate_summary(self, fused_probs: List[Dict[int, float]], combinations: List[Dict], next_issue: str) -> str:
        """生成预测结果文本摘要"""
        lines = []
        lines.append('=' * 70)
        lines.append(f'           排列5下期走势预测报告（目标期号: {next_issue}）')
        lines.append('=' * 70)
        lines.append(f'\n预测时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append(f'预测算法: 频率加权 + 遗漏回归 + 趋势动量 + 马尔可夫转移 + 形态延续')
        lines.append(f'算法融合: 加权综合概率模型')
        lines.append('-' * 70)

        lines.append('\n【一、各位置号码概率分布】')
        for pos in range(self.positions):
            probs = fused_probs[pos]
            sorted_nums = sorted(probs.items(), key=lambda x: x[1], reverse=True)
            lines.append(f'\n{self.position_names[pos]}:')
            for num, prob in sorted_nums:
                bar = '█' * int(prob * 50)
                lines.append(f'  数字{num}: {prob:.4f} ({prob*100:.2f}%) {bar}')

        lines.append('\n【二、走势方向预测】')
        # 需要重新计算趋势
        lines.append('  详见趋势预测数据')

        lines.append('\n【三、推荐号码组合（Top 10）】')
        for combo in combinations[:10]:
            lines.append(f"  第{combo['rank']:>2}名: {combo['combination']}  "
                        f"联合概率: {combo['joint_probability_pct']:.4f}%  "
                        f"置信度: {combo['confidence_score']}")

        lines.append('\n【四、风险提示】')
        lines.append('  1. 彩票开奖为独立随机事件，历史数据不构成对未来结果的保证')
        lines.append('  2. 本预测基于统计学模型，仅供数据研究参考')
        lines.append('  3. 多算法融合可降低单一模型偏差，但无法消除随机性')
        lines.append('=' * 70)

        return '\n'.join(lines)

    def generate_forecast_charts(self, prediction_result: Dict[str, Any]) -> Dict[str, bytes]:
        """
        生成走势预测图表

        Returns:
            图表名称 -> 字节流的字典
        """
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        charts = {}
        fused = prediction_result.get('fused_probabilities', [])
        trend = prediction_result.get('trend_forecast', {})

        if not fused:
            return charts

        try:
            # 图1: 各位置号码概率热力分布
            fig, axes = plt.subplots(1, 5, figsize=(20, 4))
            for pos in range(self.positions):
                probs = fused[pos]
                nums = list(range(10))
                vals = [probs.get(n, 0) for n in nums]
                colors = ['#ef4444' if v == max(vals) else '#3b82f6' if v == min(vals) else '#94a3b8' for v in vals]
                axes[pos].bar(nums, vals, color=colors)
                axes[pos].set_title(f'{self.position_names[pos]}概率分布', fontsize=10, fontweight='bold')
                axes[pos].set_xlabel('号码')
                axes[pos].set_ylabel('概率')
                axes[pos].set_ylim(0, max(vals) * 1.2)
                for n, v in zip(nums, vals):
                    axes[pos].text(n, v + 0.005, f'{v:.2f}', ha='center', fontsize=7)
            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            charts['probability_distribution'] = buf.getvalue()
            plt.close()

            # 图2: 推荐组合联合概率对比
            combos = prediction_result.get('top_combinations', [])
            if combos:
                fig, ax = plt.subplots(figsize=(12, 6))
                ranks = [c['rank'] for c in combos]
                probs = [c['joint_probability_pct'] for c in combos]
                colors = ['#f59e0b' if i < 3 else '#64748b' for i in range(len(combos))]
                ax.barh([f"#{c['rank']} {c['combination']}" for c in combos], probs, color=colors)
                ax.set_xlabel('联合概率 (%)')
                ax.set_title('推荐号码组合联合概率排名', fontsize=12, fontweight='bold')
                ax.invert_yaxis()
                for i, (r, p) in enumerate(zip(ranks, probs)):
                    ax.text(p + 0.001, i, f'{p:.4f}%', va='center', fontsize=8)
                plt.tight_layout()
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
                buf.seek(0)
                charts['combination_ranking'] = buf.getvalue()
                plt.close()

            # 图3: 走势方向预测（预期值 vs 近期值）
            pos_forecasts = trend.get('position_forecasts', [])
            if pos_forecasts:
                fig, ax = plt.subplots(figsize=(10, 5))
                positions = [pf['position_name'] for pf in pos_forecasts]
                expected = [pf['expected_value'] for pf in pos_forecasts]
                recent = [pf['recent_value'] if pf['recent_value'] is not None else 0 for pf in pos_forecasts]
                x = range(len(positions))
                width = 0.35
                ax.bar([i - width/2 for i in x], recent, width, label='最近一期', color='#64748b')
                ax.bar([i + width/2 for i in x], expected, width, label='预测预期值', color='#10b981')
                ax.set_xticks(x)
                ax.set_xticklabels(positions)
                ax.set_ylabel('号码值')
                ax.set_title('各位置走势预测（预期值 vs 近期值）', fontsize=12, fontweight='bold')
                ax.legend()
                plt.tight_layout()
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
                buf.seek(0)
                charts['trend_forecast'] = buf.getvalue()
                plt.close()

            logger.info('走势预测图表生成完成')
        except Exception as e:
            logger.error(f'生成预测图表失败: {e}')

        return charts


if __name__ == '__main__':
    # 简单测试
    test_data = [
        {'issue': '2026001', 'numbers': [1, 2, 3, 4, 5]},
        {'issue': '2026002', 'numbers': [2, 3, 4, 5, 6]},
        {'issue': '2026003', 'numbers': [3, 4, 5, 6, 7]},
    ]
    predictor = P5Predictor()
    result = predictor.predict(test_data)
    if 'error' in result:
        print(f'预测失败: {result["error"]}')
    else:
        print(result['summary'])
        charts = predictor.generate_forecast_charts(result)
        print(f'生成图表数: {len(charts)}')
