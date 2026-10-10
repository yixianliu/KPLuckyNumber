#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试模型监控可视化模块
"""

import sys
import os
import time
sys.path.append(os.path.join(os.path.dirname(__file__), 'modules'))

from monitor_visualizer import MonitorVisualizer
import json

def test_monitor_visualizer():
    """测试监控可视化功能"""
    print("测试模型监控可视化模块...")
    
    # 创建可视化器实例
    try:
        viz = MonitorVisualizer()
        print("✓ 监控可视化器创建成功")
    except Exception as e:
        print(f"✗ 监控可视化器创建失败: {e}")
        return False
    
    # 创建一些测试数据
    print("\n1. 创建测试监控数据...")
    test_data = []
    base_time = time.time()
    
    for i in range(30):
        test_data.append({
            'timestamp': time.time() - (30 - i) * 10,  # 最近30期，每期10秒间隔
            'algorithm_1': {
                'hit_rate': 0.08 + (i * 0.002),  # 逐渐改善
                'confidence': 0.6 + (i * 0.01),
            },
            'algorithm_2': {
                'hit_rate': 0.06 + (i * 0.001),
                'confidence': 0.5 + (i * 0.005),
            }
        })
    
    print(f"✓ 创建了 {len(test_data)} 条测试数据")
    
    # 测试绘制模型命中率趋势图
    print("\n2. 测试绘制模型命中率趋势图...")
    try:
        chart_path = viz.plot_model_hit_rate(
            history_data=test_data,
            algorithm_names=['algorithm_1', 'algorithm_2'],
            window_size=10
        )
        if chart_path:
            print(f"✓ 模型命中率趋势图绘制成功: {chart_path}")
        else:
            print("! 模型命中率趋势图绘制返回None（可能matplotlib不可用）")
    except Exception as e:
        print(f"✗ 模型命中率趋势图绘制失败: {e}")
    
    # 测试绘制置信度分布图
    print("\n3. 测试绘制置信度分布图...")
    try:
        chart_path = viz.plot_confidence_distribution(
            history_data=test_data,
            algorithm_names=['algorithm_1', 'algorithm_2']
        )
        if chart_path:
            print(f"✓ 置信度分布图绘制成功: {chart_path}")
        else:
            print("! 置信度分布图绘制返回None（可能matplotlib不可用）")
    except Exception as e:
        print(f"✗ 置信度分布图绘制失败: {e}")
    
    # 测试绘制响应时间趋势图
    print("\n4. 测试绘制响应时间趋势图...")
    try:
        # 添加响应时间数据到测试数据
        response_time_data = []
        for i, item in enumerate(test_data):
            response_time_data.append({
                'timestamp': item['timestamp'],
                'response_time': 2.0 - (i * 0.02)  # 逐渐减少的响应时间
            })
        
        chart_path = viz.plot_response_time_trend(
            history_data=response_time_data,
            window_size=10
        )
        if chart_path:
            print(f"✓ 响应时间趋势图绘制成功: {chart_path}")
        else:
            print("! 响应时间趋势图绘制返回None（可能matplotlib不可用）")
    except Exception as e:
        print(f"✗ 响应时间趋势图绘制失败: {e}")
    
    # 测试绘制综合监控仪表板
    print("\n5. 测试绘制综合监控仪表板...")
    try:
        dashboard_path = viz.plot_monitoring_dashboard(
            history_data=test_data,
            algorithm_names=['algorithm_1', 'algorithm_2']
        )
        if dashboard_path:
            print(f"✓ 综合监控仪表板绘制成功: {dashboard_path}")
        else:
            print("! 综合监控仪表板绘制返回None（可能matplotlib不可用）")
    except Exception as e:
        print(f"✗ 综合监控仪表板绘制失败: {e}")
    
    print(f"\n=== 测试完成 ===")
    return True

if __name__ == '__main__':
    success = test_monitor_visualizer()
    sys.exit(0 if success else 1)