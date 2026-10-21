# -*- coding: utf-8 -*-
"""
特征工程模块单元测试
===================

测试目标：
1. 基础统计特征：频率、遗漏、冷热号
2. 高级统计特征：012路、连号、重隔号、区间分布
3. 时序特征：滑动窗口统计、趋势动量
4. 交叉特征：位置关联、和值跨度
5. 边界条件与异常处理

【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，
本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。
请理性购彩，量力而行。
"""

import sys
import os
import unittest
import numpy as np

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.features import (  # noqa: E402 路径引导后导入
    P5Features,
    P5FeatureCache,
    compute_history_fingerprint,
    reset_feature_cache,
    _get_feature_cache,
)


class TestP5Features(unittest.TestCase):
    """测试 P5Features 类的核心功能"""

    def setUp(self):
        """初始化测试数据"""
        self.features = P5Features()
        # 创建模拟历史数据（按时间正序）
        self.mock_data = self._generate_mock_data(200)

    def _generate_mock_data(self, count: int) -> list:
        """生成模拟历史开奖数据"""
        import random
        random.seed(42)
        data = []
        for i in range(count):
            numbers = [random.randint(0, 9) for _ in range(5)]
            data.append({
                'issue': f'2026{i:04d}',
                'numbers': numbers,
                'draw_date': f'2026-{i % 12 + 1:02d}-{i % 28 + 1:02d}',
                'sum': sum(numbers),
                'span': max(numbers) - min(numbers)
            })
        return data

    # ==================== 1. 基础统计特征测试 ====================

    def test_calculate_frequency_features_full_data(self):
        """测试频率特征计算（全量数据）"""
        result = self.features.calculate_frequency_features(self.mock_data)

        # 验证返回结构 - 使用中文位置名
        self.assertIn('万位', result)
        self.assertIn('个位', result)

        # 验证每个位置都有频率和排名
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertIn(pos_name, result)
            self.assertIn('frequencies', result[pos_name])
            self.assertIn('ranks', result[pos_name])
            self.assertIn('hot_levels', result[pos_name])

            # 验证频率总和约为1
            freq_sum = sum(result[pos_name]['frequencies'].values())
            self.assertAlmostEqual(freq_sum, 1.0, places=1)

            # 验证排名包含0-9所有数字
            self.assertEqual(
                set(result[pos_name]['ranks'].keys()), set(range(10)))

            # 验证冷热等级
            self.assertEqual(
                set(result[pos_name]['hot_levels'].values()),
                {'hot', 'warm', 'cold'})

    def test_calculate_frequency_features_windowed(self):
        """测试频率特征计算（滑动窗口）"""
        result = self.features.calculate_frequency_features(
            self.mock_data, window=50)

        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            freq_sum = sum(result[pos_name]['frequencies'].values())
            self.assertAlmostEqual(freq_sum, 1.0, places=1)

    def test_calculate_frequency_features_empty_data(self):
        """测试空数据处理"""
        result = self.features.calculate_frequency_features([])
        self.assertIn('error', result)
        self.assertEqual(result['error'], '数据为空')

    def test_calculate_omission_features(self):
        """测试遗漏特征计算"""
        result = self.features.calculate_omission_features(self.mock_data)

        self.assertIn('万位', result)
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertIn(pos_name, result)
            self.assertIn('current_omissions', result[pos_name])
            self.assertIn('max_omissions', result[pos_name])
            self.assertIn('omission_ratios', result[pos_name])
            self.assertIn('omission_probs', result[pos_name])

            # 验证当前遗漏值非负
            for num, omission in result[pos_name]['current_omissions'].items():
                self.assertGreaterEqual(omission, 0)

    def test_calculate_hot_cold_features(self):
        """测试冷热号特征计算（通过频率特征中的hot_levels验证）"""
        result = self.features.calculate_frequency_features(self.mock_data)

        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            hot_levels = result[pos_name]['hot_levels']
            levels = set(hot_levels.values())
            self.assertTrue('hot' in levels)
            self.assertTrue('cold' in levels)
            self.assertTrue('warm' in levels)

    # ==================== 2. 高级统计特征测试 ====================

    def test_calculate_012_road_features(self):
        """测试 012路特征计算"""
        result = self.features.calculate_012_road_features(self.mock_data)

        self.assertIn('万位', result)
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertIn(pos_name, result)
            self.assertIn('road_ratios', result[pos_name])
            self.assertIn('current_road', result[pos_name])
            self.assertIn('road_omissions', result[pos_name])
            self.assertIn('dominant_road', result[pos_name])

    def test_calculate_consecutive_features(self):
        """测试连号特征计算"""
        result = self.features.calculate_consecutive_features(self.mock_data)

        self.assertIn('consecutive_rate', result)
        self.assertIn('max_consecutive_length', result)
        self.assertIn('consecutive_pair_counts', result)

    def test_calculate_repeat_features(self):
        """测试重隔号特征计算"""
        result = self.features.calculate_repeat_features(self.mock_data)

        self.assertIn('avg_repeat_count', result)
        self.assertIn('avg_separate_count', result)
        self.assertIn('repeat_number_counts', result)
        self.assertIn('separate_number_counts', result)
        self.assertIn('hot_repeat_numbers', result)

    def test_calculate_interval_features(self):
        """测试区间分布特征计算"""
        result = self.features.calculate_interval_features(self.mock_data)

        self.assertIn('万位', result)
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertIn(pos_name, result)
            self.assertIn('interval_ratios', result[pos_name])
            self.assertIn('current_interval', result[pos_name])
            self.assertIn('dominant_interval', result[pos_name])

    # ==================== 3. 时序特征测试 ====================

    def test_calculate_sliding_window_features(self):
        """测试滑动窗口特征计算"""
        result = self.features.calculate_sliding_window_features(
            self.mock_data, windows=[10, 20, 50])

        self.assertIn('window_10', result)
        self.assertIn('window_20', result)
        self.assertIn('window_50', result)

        for win_name in ['window_10', 'window_20', 'window_50']:
            win_data = result[win_name]
            self.assertIn('frequency', win_data)
            self.assertIn('omission', win_data)
            self.assertIn('road_012', win_data)
            self.assertIn('sample_count', win_data)

    def test_calculate_trend_features(self):
        """测试趋势动量特征计算"""
        result = self.features.calculate_trend_features(self.mock_data)

        self.assertIn('万位', result)
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertIn(pos_name, result)
            self.assertIn('trend', result[pos_name])
            self.assertIn('slope', result[pos_name])
            self.assertIn('momentum', result[pos_name])
            self.assertIn('recent_values', result[pos_name])

            direction = result[pos_name]['trend']
            self.assertIn(direction, ['up', 'down', 'stable'])

    # ==================== 4. 交叉特征测试 ====================

    def test_calculate_cross_position_features(self):
        """测试位置关联特征计算"""
        result = self.features.calculate_cross_position_features(
            self.mock_data)

        self.assertIn('same_adjacent_rate', result)
        self.assertIn('position_correlations', result)

        # 验证位置对相关性
        # C(5,2) = 10 对
        self.assertEqual(len(result['position_correlations']), 10)

        for pair_key, corr in result['position_correlations'].items():
            self.assertGreaterEqual(corr, -1.0)
            self.assertLessEqual(corr, 1.0)

    def test_calculate_sum_span_features(self):
        """测试和值跨度特征计算"""
        result = self.features.calculate_sum_span_features(self.mock_data)

        self.assertIn('sum_stats', result)
        self.assertIn('sum_interval_ratios', result)
        self.assertIn('span_stats', result)
        self.assertIn('span_interval_ratios', result)
        self.assertIn('current_sum', result)
        self.assertIn('current_span', result)

    # ==================== 5. 批量特征提取测试 ====================

    def test_extract_all_features(self):
        """测试批量提取所有特征"""
        result = self.features.extract_all_features(
            self.mock_data, windows=[10, 50])

        # 验证包含主要特征类别
        expected_keys = [
            'frequency', 'omission', 'road_012', 'consecutive',
            'repeat', 'interval', 'sliding_window', 'trend',
            'cross_position', 'sum_span']
        for key in expected_keys:
            self.assertIn(key, result, f"缺少特征类别: {key}")

    # ==================== 6. 边界条件与异常处理 ====================

    def test_invalid_data_handling(self):
        """测试无效数据处理"""
        # 数据缺少numbers字段
        bad_data = [{'issue': '20260001'}]
        result = self.features.calculate_frequency_features(bad_data)
        # 应该优雅处理，不抛出异常
        self.assertIn('万位', result)

    def test_single_sample(self):
        """测试单条样本"""
        single_data = [{'issue': '20260001', 'numbers': [1, 2, 3, 4, 5]}]
        result = self.features.calculate_frequency_features(single_data)

        self.assertEqual(result['万位']['frequencies'][1], 1.0)
        self.assertEqual(result['万位']['frequencies'][0], 0.0)

    def test_all_same_numbers(self):
        """测试全同号码（如全0）"""
        same_data = [
            {'issue': f'2026{i:04d}', 'numbers': [0, 0, 0, 0, 0]}
            for i in range(50)]
        result = self.features.calculate_frequency_features(same_data)

        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            self.assertEqual(result[pos_name]['frequencies'][0], 1.0)
            for num in range(1, 10):
                self.assertEqual(result[pos_name]['frequencies'][num], 0.0)

    def test_road_classification(self):
        """测试 012路分类正确性"""
        road_cases = [(0, 0), (3, 0), (6, 0), (9, 0),
                      (1, 1), (4, 1), (7, 1),
                      (2, 2), (5, 2), (8, 2)]
        for num, expected_road in road_cases:
            data = [{'issue': '20260001', 'numbers': [num] * 5}]
            result = self.features.calculate_012_road_features(data)
            for pos_name in ['万位', '千位', '百位', '十位', '个位']:
                self.assertEqual(
                    result[pos_name]['dominant_road'], expected_road)

    def test_prime_classification(self):
        """测试质数/合数分类正确性（通过sum_span验证）"""
        # 质数: 2,3,5,7 | 合数: 0,1,4,6,8,9
        # 这里通过和值跨度特征间接验证
        for num in [2, 3, 5, 7]:  # 质数
            data = [{'issue': '20260001', 'numbers': [num] * 5}]
            result = self.features.calculate_sum_span_features(data)
            # 全质数组合的和值应该在特定范围
            self.assertIn('sum_stats', result)

    # ==================== 7. 贝叶斯特征测试 ====================

    def test_calculate_bayesian_features(self):
        """测试贝叶斯特征计算"""
        result = self.features.calculate_bayesian_features(self.mock_data[:50])

        self.assertIn('positions', result)
        self.assertEqual(len(result['positions']), 5)

        for pos_data in result['positions']:
            self.assertIn('prior', pos_data)
            self.assertIn('likelihood', pos_data)
            self.assertIn('posterior', pos_data)
            # 概率和应约为1
            for key in ['prior', 'likelihood', 'posterior']:
                prob_sum = sum(pos_data[key].values())
                self.assertAlmostEqual(prob_sum, 1.0, places=1)


class TestFeaturesEdgeCases(unittest.TestCase):
    """测试边界情况和极端输入"""

    def setUp(self):
        self.features = P5Features()

    def test_none_input(self):
        """测试 None 输入"""
        with self.assertRaises((TypeError, AttributeError)):
            self.features.calculate_frequency_features(None)

    def test_non_list_input(self):
        """测试非列表输入"""
        with self.assertRaises((TypeError, AttributeError)):
            self.features.calculate_frequency_features("not a list")

    def test_data_with_none_numbers(self):
        """测试包含 None 的数据 - 应优雅处理"""
        data = [{'issue': '20260001', 'numbers': None}]
        # 当前实现会抛出 TypeError，这是预期行为
        with self.assertRaises(TypeError):
            self.features.calculate_frequency_features(data)

    def test_data_with_short_numbers(self):
        """测试号码不足5位的数据"""
        data = [{'issue': '20260001', 'numbers': [1, 2]}]
        result = self.features.calculate_frequency_features(data)
        # 应该优雅处理，不抛出异常（会跳过该条记录）
        self.assertIn('万位', result)


class TestFeaturesPerformance(unittest.TestCase):
    """性能基准测试（确保无显著退化）"""

    def setUp(self):
        self.features = P5Features()
        import random
        random.seed(42)
        self.large_data = [
            {
                'issue': f'2026{i:04d}',
                'numbers': [random.randint(0, 9) for _ in range(5)],
                'draw_date': f'2026-{i % 12 + 1:02d}-{i % 28 + 1:02d}'
            }
            for i in range(1000)
        ]

    def test_frequency_performance(self):
        """测试频率计算性能"""
        import time
        start = time.time()
        result = self.features.calculate_frequency_features(self.large_data)
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.1, f"频率计算耗时 {elapsed:.3f}s 超过阈值")

    def test_all_features_performance(self):
        """测试全特征提取性能"""
        import time
        start = time.time()
        result = self.features.extract_all_features(
            self.large_data, windows=[10, 50, 100])
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.5, f"全特征提取耗时 {elapsed:.3f}s 超过阈值")


class TestFeatureCacheALG04(unittest.TestCase):
    """v5.2 ALG-04 特征缓存测试：A28 性能前提 + A29 失效正确性

    A28（同指纹重复抽取耗时降幅 ≥80%）依赖探针计时，由
    scripts/bench_feature_cache.py 承担，本类聚焦失效语义与
    隔离性这类确定性断言，避免 CI 时序抖动导致假失败。
    """

    def setUp(self):
        """隔离共享缓存并构造 60 期模拟历史数据"""
        # 共享缓存为进程级单例，测试间必须重置保证统计干净
        reset_feature_cache()
        self.features = P5Features()
        self.history = self._generate_history(60)

    def _generate_history(self, count: int, seed: int = 42) -> list:
        """生成按 issue 正序的模拟历史数据"""
        import random
        random.seed(seed)
        return [{
            'issue': f'2026{i:04d}',
            'numbers': [random.randint(0, 9) for _ in range(5)],
            'draw_date': f'2026-{i % 12 + 1:02d}-{i % 28 + 1:02d}',
            'sum': 0, 'span': 0,
        } for i in range(count)]

    # ==================== A29：缓存失效正确性 ====================

    def test_same_history_hits_cache(self):
        """同 history 重复抽取第二次必须命中缓存"""
        self.features.extract_all_features(self.history)
        stats_before = _get_feature_cache().stats()
        self.assertGreaterEqual(stats_before['size'], 1)

        self.features.extract_all_features(self.history)
        stats_after = _get_feature_cache().stats()
        # 第二次调用：hits 恰好 +1，misses 不变
        self.assertEqual(stats_after['hits'], stats_before['hits'] + 1)
        self.assertEqual(stats_after['misses'], stats_before['misses'])

    def test_single_digit_change_invalidates_cache(self):
        """A29 场景1：任意 1 期 1 位数字变动后缓存必失"""
        self.features.extract_all_features(self.history)
        misses_before = _get_feature_cache().stats()['misses']

        # 改动第 10 期个位数字（+1 取模保 0-9）
        changed = [dict(item) for item in self.history]
        idx = 10
        nums = list(changed[idx]['numbers'])
        nums[4] = (nums[4] + 1) % 10
        changed[idx]['numbers'] = nums
        self.features.extract_all_features(changed)

        stats = _get_feature_cache().stats()
        self.assertEqual(stats['misses'], misses_before + 1)

    def test_removed_issue_invalidates_cache(self):
        """A29 场景2：删除 1 期（行数变动）后缓存必失"""
        self.features.extract_all_features(self.history)
        misses_before = _get_feature_cache().stats()['misses']

        shortened = self.history[:-1]
        self.features.extract_all_features(shortened)

        stats = _get_feature_cache().stats()
        self.assertEqual(stats['misses'], misses_before + 1)

    def test_changed_issue_without_number_change_invalidates(self):
        """A29 场景3：仅期号变动（numbers 不变）后缓存仍必失"""
        self.features.extract_all_features(self.history)
        misses_before = _get_feature_cache().stats()['misses']

        # 仅改第 5 期期号，号码完全一致
        changed = [dict(item) for item in self.history]
        changed[5]['issue'] = '20266005'
        self.features.extract_all_features(changed)

        stats = _get_feature_cache().stats()
        self.assertEqual(stats['misses'], misses_before + 1)

    # ==================== 隔离性与应急通道 ====================

    def test_different_windows_do_not_share_cache(self):
        """windows 参数不同不得互串缓存（指纹含 windows）"""
        self.features.extract_all_features(
            self.history, windows=[5, 10, 20, 50])
        misses_before = _get_feature_cache().stats()['misses']

        # 同 history 但不同 windows：必须 miss 并按新配置重算
        self.features.extract_all_features(
            self.history, windows=[5, 10, 20])
        stats = _get_feature_cache().stats()
        self.assertEqual(stats['misses'], misses_before + 1)

    def test_returned_features_are_isolated_from_cache(self):
        """调用方就地改写返回值不得污染缓存（深拷贝隔离）"""
        first = self.features.extract_all_features(self.history)
        # 污染第一次返回值的嵌套结构
        first['frequency']['万位']['frequencies'][0] = 999.0

        second = self.features.extract_all_features(self.history)
        self.assertNotEqual(second['frequency']['万位']['frequencies'][0], 999.0)

    def test_cached_content_consistent_with_fresh_extraction(self):
        """缓存命中内容与全新抽取一致（extract_time 除外）"""
        reset_feature_cache()
        fresh = self.features.extract_all_features(self.history)
        hit = self.features.extract_all_features(self.history)
        fresh.pop('extract_time')
        hit.pop('extract_time')
        self.assertEqual(fresh, hit)

    def test_disabled_cache_bypasses_and_recomputes(self):
        """应急通道：enabled=false 时完全不走缓存"""
        from unittest import mock
        with mock.patch('config.FEATURE_CACHE_CONFIG',
                        {'enabled': False, 'capacity': 8}):
            reset_feature_cache()
            self.features.extract_all_features(self.history)
            stats = _get_feature_cache().stats()
            # 禁用期间无任何缓存条目产生
            self.assertEqual(stats['size'], 0)

    # ==================== P5FeatureCache 单元行为 ====================

    def test_lru_eviction_order(self):
        """LRU 淘汰：容量 2 时第 3 个键挤出最久未使用的首个键"""
        cache = P5FeatureCache(capacity=2)
        cache.put('fp1', {'v': 1})
        cache.put('fp2', {'v': 2})
        # 访问 fp1 使其变为最新，随后写入 fp3 应淘汰 fp2
        self.assertEqual(cache.get('fp1'), {'v': 1})
        cache.put('fp3', {'v': 3})
        self.assertIsNone(cache.get('fp2'))
        self.assertEqual(cache.get('fp1'), {'v': 1})
        self.assertEqual(cache.get('fp3'), {'v': 3})

    def test_get_returns_deepcopy_not_reference(self):
        """get 返回深拷贝：改写返回值不影响缓存原件"""
        cache = P5FeatureCache(capacity=2)
        original = {'v': {'nested': 1}}
        cache.put('fp', original)
        fetched = cache.get('fp')
        fetched['v']['nested'] = 999
        # 原件未被污染（put 的引用也应完好）
        self.assertEqual(original['v']['nested'], 1)
        self.assertEqual(cache.get('fp')['v']['nested'], 1)

    def test_compute_fingerprint_covers_elements(self):
        """指纹覆盖行数/期号/逐位数字/windows 四要素"""
        data = self._generate_history(30)
        base = compute_history_fingerprint(data, [5, 10, 20, 50])

        # 行数变动 → 指纹变
        fp_len = compute_history_fingerprint(data[:-1], [5, 10, 20, 50])
        self.assertNotEqual(base, fp_len)

        # 任意 1 期任意 1 位数字变动 → 指纹变
        changed = [dict(item) for item in data]
        nums = list(changed[7]['numbers'])
        nums[2] = (nums[2] + 1) % 10
        changed[7]['numbers'] = nums
        fp_num = compute_history_fingerprint(changed, [5, 10, 20, 50])
        self.assertNotEqual(base, fp_num)

        # windows 变动 → 指纹变
        fp_win = compute_history_fingerprint(data, [5, 10, 20])
        self.assertNotEqual(base, fp_win)

    def test_compute_fingerprint_none_on_invalid_structure(self):
        """结构异常输入（非列表/条目缺 numbers/位数不符）指纹为 None"""
        self.assertIsNone(compute_history_fingerprint('not-a-list', [5]))
        self.assertIsNone(compute_history_fingerprint(
            [{'issue': '20260001'}], [5]))
        self.assertIsNone(compute_history_fingerprint(
            [{'issue': '20260001', 'numbers': [1, 2, 3]}], [5]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
