#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试新增的交叉特征函数

历史沿革：本文件原以 `sys.path.append(scripts/tests/modules)` + 裸名
`from features import P5Features` 导入，该目录不存在导致收集期
ModuleNotFoundError。实际模块为 `modules/features.py`（类名 P5Features
未变），仅导入路径写错，已改为项目根路径 + `modules.features`。

原脚本逐段 `try/except` 后仅打印「✗」，pytest 会误判为通过；本次改为
异常直接抛出 + 缺失特征显式断言，保留「6 个交叉特征必须全部产出」的
原始验证意图。
"""

import sys
import os

# 项目根目录（scripts/tests → scripts → 项目根），保证 modules.* 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.features import P5Features

# 新增交叉特征在 extract_all_features 结果中的顶层键
NEW_FEATURE_KEYS = [
    'constraint_correlation',
    'digit_characteristic',
    'joint_frequency',
    'trend_association',
    'rolling_statistics',
    'position_pair',
]


def _test_data():
    """10 期连续递增样本（覆盖 0-9 全数字与跨位组合）。"""
    return [
        {'issue': '2024001', 'numbers': [1, 2, 3, 4, 5]},
        {'issue': '2024002', 'numbers': [2, 3, 4, 5, 6]},
        {'issue': '2024003', 'numbers': [3, 4, 5, 6, 7]},
        {'issue': '2024004', 'numbers': [4, 5, 6, 7, 8]},
        {'issue': '2024005', 'numbers': [5, 6, 7, 8, 9]},
        {'issue': '2024006', 'numbers': [6, 7, 8, 9, 0]},
        {'issue': '2024007', 'numbers': [7, 8, 9, 0, 1]},
        {'issue': '2024008', 'numbers': [8, 9, 0, 1, 2]},
        {'issue': '2024009', 'numbers': [9, 0, 1, 2, 3]},
        {'issue': '2024010', 'numbers': [0, 1, 2, 3, 4]},
    ]


def test_new_features():
    """6 个交叉特征函数可独立计算，且完整特征提取包含全部新特征。"""
    test_data = _test_data()
    fe = P5Features()

    # 1. 组合约束交叉特征
    constraint_result = fe.calculate_constraint_correlation_features(test_data)
    assert isinstance(constraint_result, dict)
    assert 'constraint_correlations' in constraint_result, \
        '缺少 constraint_correlations 输出'
    print(f"✓ 组合约束交叉特征：{len(constraint_result['constraint_correlations'])} 个约束相关键")

    # 2. 数字特性交叉特征
    digit_result = fe.calculate_digit_characteristic_features(test_data)
    assert isinstance(digit_result, dict)
    assert 'position_characteristic_ratios' in digit_result, \
        '缺少 position_characteristic_ratios 输出'
    print(f"✓ 数字特性交叉特征：{len(digit_result['position_characteristic_ratios'])} 个位置特性键")

    # 3. 频率分布交叉特征
    joint_result = fe.calculate_joint_frequency_features(test_data)
    assert isinstance(joint_result, dict)
    assert 'joint_frequencies' in joint_result, '缺少 joint_frequencies 输出'
    print(f"✓ 频率分布交叉特征：{len(joint_result['joint_frequencies'])} 个联合频率键")

    # 4. 趋势关联交叉特征
    trend_result = fe.calculate_trend_association_features(test_data)
    assert isinstance(trend_result, dict)
    assert 'trend_associations' in trend_result, '缺少 trend_associations 输出'
    print(f"✓ 趋势关联交叉特征：{len(trend_result['trend_associations'])} 个趋势关联键")

    # 5. 滚动统计特征
    rolling_result = fe.calculate_rolling_statistics_features(test_data)
    assert isinstance(rolling_result, dict) and rolling_result, \
        '滚动统计特征返回空结果'
    print(f"✓ 滚动统计特征：{len(rolling_result)} 个输出键")

    # 6. 位置对特征
    pair_result = fe.calculate_position_pair_features(test_data)
    assert isinstance(pair_result, dict) and pair_result, '位置对特征返回空结果'
    print(f"✓ 位置对特征：{list(pair_result.keys())}")

    # 7. 完整特征提取必须包含全部 6 个新特征
    all_features = fe.extract_all_features(test_data)
    assert isinstance(all_features, dict)
    missing = [k for k in NEW_FEATURE_KEYS if k not in all_features]
    assert not missing, f'完整特征提取缺失新特征: {missing}'
    feature_keys = [k for k in all_features if k not in ('extract_time', 'data_count')]
    print(f"✓ 完整特征提取：{len(feature_keys)} 类特征，6 个新特征全部存在")


if __name__ == '__main__':
    test_new_features()
