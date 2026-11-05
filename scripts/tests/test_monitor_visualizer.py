#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试模型监控可视化模块

历史沿革：本文件原以 `sys.path.append(scripts/tests/modules)` + 裸名
`from monitor_visualizer import ...` 导入，该目录不存在导致收集期
ModuleNotFoundError。实际模块仍在 `modules/monitor_visualizer.py`，
且部分绘图方法已更名、入参结构已从嵌套字典改为扁平字段：
  plot_confidence_distribution → plot_model_confidence
  plot_response_time_trend    → plot_response_time
  plot_monitoring_dashboard   → create_monitoring_dashboard
本文件已按当前 API 适配，并按「图表逐个验证」拆成独立用例，保留原有
「每张图必须真实生成文件」的验证意图。

曾发现的模块缺陷已修复：`_get_chart_colors('response_time')` /
`('training_stability')` 补齐 `line_warning`（`training_stability` 另补
`line_danger`），`plot_response_time` 与 `create_monitoring_dashboard`
不再因 KeyError 返回 None，对应用例已恢复为正常断言。
"""

import sys
import os
import time

# 项目根目录（scripts/tests → scripts → 项目根），保证 modules.* 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pytest

# matplotlib 缺失时模块只能返回 None，绘图验证无意义，按环境跳过而非误判失败
pytest.importorskip('matplotlib')

from modules.monitor_visualizer import MonitorVisualizer


def _assert_chart(path, desc):
    """断言图表真实落盘（既验证 API 调用参数，也验证文件写出）。"""
    assert path, f'{desc}返回 None'
    assert os.path.exists(path), f'{desc}文件不存在: {path}'
    print(f'✓ {desc}绘制成功: {path}')


@pytest.fixture(scope='module')
def viz():
    """单个可视化器实例供所有图表用例复用。"""
    return MonitorVisualizer()


@pytest.fixture(scope='module')
def test_data():
    """30 条嵌套结构的原始监控数据（置信度图仍按此结构取数）。"""
    return [
        {
            'timestamp': time.time() - (30 - i) * 10,  # 最近30期，每期10秒间隔
            'algorithm_1': {
                'hit_rate': 0.08 + (i * 0.002),  # 逐渐改善
                'confidence': 0.6 + (i * 0.01),
            },
            'algorithm_2': {
                'hit_rate': 0.06 + (i * 0.001),
                'confidence': 0.5 + (i * 0.005),
            }
        }
        for i in range(30)
    ]


def test_plot_model_hit_rate(viz, test_data):
    """模型命中率趋势图：当前 API 读取扁平字段 {算法名}_hit_rate。"""
    hit_rate_data = [
        {'algorithm_1_hit_rate': item['algorithm_1']['hit_rate'],
         'algorithm_2_hit_rate': item['algorithm_2']['hit_rate']}
        for item in test_data
    ]
    chart_path = viz.plot_model_hit_rate(
        history_data=hit_rate_data,
        algorithm_names=['algorithm_1', 'algorithm_2'],
        window_size=10
    )
    _assert_chart(chart_path, '模型命中率趋势图')


def test_plot_model_confidence(viz, test_data):
    """置信度趋势图：当前 API plot_model_confidence 只接收 history_data。"""
    confidence_data = [
        {'timestamp': item['timestamp'],
         'confidence': item['algorithm_1']['confidence']}
        for item in test_data
    ]
    chart_path = viz.plot_model_confidence(
        history_data=confidence_data,
        window_size=10
    )
    _assert_chart(chart_path, '置信度趋势图')


def test_plot_response_time(viz, test_data):
    """响应时间趋势图：当前 API 为 plot_response_time。"""
    response_time_data = [
        {'timestamp': item['timestamp'],
         'response_time': 2.0 - (i * 0.02)}  # 逐渐减少的响应时间
        for i, item in enumerate(test_data)
    ]
    chart_path = viz.plot_response_time(
        history_data=response_time_data,
        window_size=10
    )
    _assert_chart(chart_path, '响应时间趋势图')


def test_create_monitoring_dashboard(viz, test_data):
    """综合仪表板：当前 API 需 5 组扁平数据 + 算法名/特征名。"""
    hit_rate_data = [
        {'algorithm_1_hit_rate': item['algorithm_1']['hit_rate'],
         'algorithm_2_hit_rate': item['algorithm_2']['hit_rate']}
        for item in test_data
    ]
    confidence_data = [
        {'timestamp': item['timestamp'],
         'confidence': item['algorithm_1']['confidence']}
        for item in test_data
    ]
    response_time_data = [
        {'timestamp': item['timestamp'],
         'response_time': 2.0 - (i * 0.02)}
        for i, item in enumerate(test_data)
    ]
    training_stability_data = [
        {'training_errors': max(0, 3 - i // 5)}
        for i in range(len(test_data))
    ]
    feature_effectiveness_data = [
        {'feature_a_effectiveness': 0.8 - i * 0.005,
         'feature_b_effectiveness': 0.6 - i * 0.003}
        for i in range(len(test_data))
    ]
    dashboard_path = viz.create_monitoring_dashboard(
        hit_rate_data=hit_rate_data,
        confidence_data=confidence_data,
        response_time_data=response_time_data,
        training_stability_data=training_stability_data,
        feature_effectiveness_data=feature_effectiveness_data,
        algorithm_names=['algorithm_1', 'algorithm_2'],
        feature_names=['feature_a', 'feature_b'],
    )
    _assert_chart(dashboard_path, '综合监控仪表板')


def test_monitor_visualizer():
    """保留原入口语义：可视化器可正常实例化（图表用例见上方分项）。"""
    print(f"✓ 监控可视化器创建成功: {MonitorVisualizer()}")


if __name__ == '__main__':
    test_monitor_visualizer()
