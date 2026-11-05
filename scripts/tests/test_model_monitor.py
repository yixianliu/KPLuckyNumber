#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试模型监控反馈机制
"""

import sys
import os
import time
# 项目根目录（scripts/tests → scripts → 项目根），保证 modules.* 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.model_monitor import (ModelMonitor, record_prediction_metrics,
                                   start_model_monitoring, stop_model_monitoring)
import json

def test_model_monitor():
    """测试模型监控功能"""
    print("测试模型监控反馈机制...")
    
    # 测试反馈回调函数
    feedback_received = []
    
    def test_feedback_callback(data):
        feedback_received.append(data)
        print(f"✓ 收到反馈数据包含 {len(data.get('metrics', {}))} 个指标类别")
    
    # 创建监控器实例
    monitor = ModelMonitor(feedback_callback=test_feedback_callback)
    
    # 测试记录预测结果
    print("\n1. 测试记录预测结果...")
    try:
        for i in range(15):
            # 模拟不同性能的预测结果
            hit_rate = 0.08 + (i * 0.005)  # 逐渐改善的命中率
            confidence = 0.5 + (i * 0.02)   # 逐渐提升的置信度
            response_time = 2.0 - (i * 0.05) # 逐渐减少的响应时间
            training_errors = max(0, 3 - i//5) # 减少的训练错误
            
            monitor.record_prediction_result(
                'test_algorithm', 
                hit_rate, 
                confidence, 
                response_time, 
                training_errors,
                {'feature_a': 0.8, 'feature_b': 0.6}  # 特征有效性
            )
            time.sleep(0.01)  # 小延迟
        
        print("✓ 预测结果记录成功")
    except Exception as e:
        print(f"✗ 预测结果记录失败: {e}")
        raise AssertionError('模型监控测试步骤失败（详见上方日志）')
    
    # 测试监控状态
    print("\n2. 测试监控状态...")
    try:
        status = monitor.get_current_status()
        print(f"✓ 监控状态获取成功")
        print(f"  数据点数: 命中率={status['data_points']['hit_rates']}, "
              f"置信度={status['data_points']['confidences']}")
        print(f"  最新值: 命中率={status['latest_values'].get('hit_rate', 'N/A'):.4f}")
    except Exception as e:
        print(f"✗ 监控状态获取失败: {e}")
        raise AssertionError('模型监控测试步骤失败（详见上方日志）')
    
    # 测试启动/停止监控
    print("\n3. 测试启动/停止监控...")
    try:
        monitor.start_monitoring()
        time.sleep(0.5)  # 让监控线程运行一下
        print("✓ 监控启动成功")
        
        monitor.stop_monitoring()
        print("✓ 监控停止成功")
        # 反馈回调通路验证（原脚本仅打印，改为显式断言）
        assert feedback_received, '监控停止后未收到任何反馈数据'
    except Exception as e:
        print(f"✗ 监控启动/停止测试失败: {e}")
        raise AssertionError('模型监控测试步骤失败（详见上方日志）')
    
    # 测试数据导出
    print("\n4. 测试数据导出...")
    try:
        export_path = monitor.export_monitor_data()
        if export_path and os.path.exists(export_path):
            print(f"✓ 监控数据导出成功: {export_path}")
            # 读取导出的数据验证
            with open(export_path, 'r', encoding='utf-8') as f:
                exported_data = json.load(f)
            print(f"  导出数据包含: {list(exported_data.keys())}")
        else:
            print("✗ 监控数据导出失败")
            raise AssertionError('模型监控测试步骤失败（详见上方日志）')
    except Exception as e:
        print(f"✗ 监控数据导出测试失败: {e}")
        raise AssertionError('模型监控测试步骤失败（详见上方日志）')
    
    # 测试便捷函数
    print("\n5. 测试便捷函数...")
    try:
        # 便捷函数写入的是模块级全局监控器（非上面的局部实例），
        # 故按全局实例的状态增量验证，而不是继续看局部回调。
        from modules import model_monitor as _mm
        global_monitor = _mm.get_model_monitor()
        before = global_monitor.get_current_status()['data_points']['hit_rates']

        record_prediction_metrics(
            'convenience_test',
            0.12,  # 命中率
            0.75,  # 置信度
            1.5,   # 响应时间
            1,     # 训练错误
            {'test_feature': 0.9}
        )

        after = global_monitor.get_current_status()
        assert after['data_points']['hit_rates'] == before + 1, \
            '便捷函数未写入全局监控器'
        assert abs(after['latest_values']['hit_rate'] - 0.12) < 1e-9, \
            '便捷函数写入的命中率与入参不一致'
        print("✓ 便捷函数测试成功（已写入全局监控器）")
    except Exception as e:
        print(f"✗ 便捷函数测试失败: {e}")
        raise AssertionError('模型监控测试步骤失败（详见上方日志）')

    print(f"\n=== 测试完成 ===")
    print(f"局部监控器累计反馈 {len(feedback_received)} 条（第 5 步前已清零）")

if __name__ == '__main__':
    test_model_monitor()