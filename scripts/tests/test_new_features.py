#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试新增的交叉特征函数
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'modules'))

from features import P5Features
import json

def test_new_features():
    # 创建测试数据
    test_data = [
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
    
    fe = P5Features()
    
    print("测试新增的特征函数...")
    
    # 测试组合约束交叉特征
    print("\n1. 测试组合约束交叉特征...")
    try:
        constraint_result = fe.calculate_constraint_correlation_features(test_data)
        print("✓ 组合约束交叉特征计算成功")
        print(f"  约束相关性键数: {len(constraint_result.get('constraint_correlations', {}))}")
    except Exception as e:
        print(f"✗ 组合约束交叉特征计算失败: {e}")
    
    # 测试数字特性交叉特征
    print("\n2. 测试数字特性交叉特征...")
    try:
        digit_result = fe.calculate_digit_characteristic_features(test_data)
        print("✓ 数字特性交叉特征计算成功")
        print(f"  位置特性比率键数: {len(digit_result.get('position_characteristic_ratios', {}))}")
    except Exception as e:
        print(f"✗ 数字特性交叉特征计算失败: {e}")
    
    # 测试频率分布交叉特征
    print("\n3. 测试频率分布交叉特征...")
    try:
        joint_result = fe.calculate_joint_frequency_features(test_data)
        print("✓ 频率分布交叉特征计算成功")
        print(f"  联合频率键数: {len(joint_result.get('joint_frequencies', {}))}")
    except Exception as e:
        print(f"✗ 频率分布交叉特征计算失败: {e}")
    
    # 测试趋势关联交叉特征
    print("\n4. 测试趋势关联交叉特征...")
    try:
        trend_result = fe.calculate_trend_association_features(test_data)
        print("✓ 趋势关联交叉特征计算成功")
        print(f"  趋势关联性键数: {len(trend_result.get('trend_associations', {}))}")
    except Exception as e:
        print(f"✗ 趋势关联交叉特征计算失败: {e}")
    
    # 测试滚动统计特征
    print("\n5. 测试滚动统计特征...")
    try:
        rolling_result = fe.calculate_rolling_statistics_features(test_data)
        print("✓ 滚动统计特征计算成功")
        print(f"  滚动统计特征键数: {len(rolling_result)}")
    except Exception as e:
        print(f"✗ 滚动统计特征计算失败: {e}")
    
    # 测试位置对特征
    print("\n6. 测试位置对特征...")
    try:
        pair_result = fe.calculate_position_pair_features(test_data)
        print("✓ 位置对特征计算成功")
        print(f"  位置对特征主要类别: {list(pair_result.keys())}")
    except Exception as e:
        print(f"✗ 位置对特征计算失败: {e}")
    
    # 测试完整特征提取包含新特征
    print("\n7. 测试完整特征提取...")
    try:
        all_features = fe.extract_all_features(test_data)
        print("✓ 完整特征提取成功")
        print(f"  特征主键: {list(all_features.keys())}")
        # 检查所有新特征是否存在
        all_new_features = ['constraint_correlation', 'digit_characteristic', 'joint_frequency', 'trend_association', 'rolling_statistics', 'position_pair']
        for feature in all_new_features:
            if feature in all_features:
                print(f"  ✓ 新特征 '{feature}' 存在")
            else:
                print(f"  ✗ 新特征 '{feature}' 缺失")
                
        # 显示特征总数（排除辅助信息）
        feature_keys = [k for k in all_features.keys() if k not in ['extract_time', 'data_count']]
        print(f"  特征类型总数: {len(feature_keys)}")
    except Exception as e:
        print(f"✗ 完整特征提取失败: {e}")

if __name__ == '__main__':
    test_new_features()