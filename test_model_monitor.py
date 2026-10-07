#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试模型监控反馈机制
"""

import sys
import os
import time
sys.path.append(os.path.join(os.path.dirname(__file__), 'modules'))

from model_monitor import ModelMonitor, record_prediction_metrics, start_model_monitoring, stop_model_monitoring
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
        return False
    
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
        return False
    
    # 测试启动/停止监控
    print("\n3. 测试启动/停止监控...")
    try:
        monitor.start_monitoring()
        time.sleep(0.5)  # 让监控线程运行一下
        print("✓ 监控启动成功")
        
        monitor.stop_monitoring()
        print("✓ 监控停止成功")
    except Exception as e:
        print(f"✗ 监控启动/停止测试失败: {e}")
        return False
    
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
            return False
    except Exception as e:
        print(f"✗ 监控数据导出测试失败: {e}")
        return False
    
    # 测试便捷函数
    print("\n5. 测试便捷函数...")
    try:
        # 重置反馈接收
        feedback_received.clear()
        
        # 使用便捷函数记录预测
        record_prediction_metrics(
            'convenience_test',
            0.12,  # 命中率
            0.75,  # 置信度
            1.5,   # 响应时间
            1,     # 训练错误
            {'test_feature': 0.9}
        )
        
        time.sleep(0.1)  # 等待反馈处理
        
        if feedback_received:
            print("✓ 便捷函数测试成功")
        else:
            print("! 便捷函数未触发反馈（可能数据不足）")
            
    except Exception as e:
        print(f"✗ 便捷函数测试失败: {e}")
        return False
    
    print(f"\n=== 测试完成 ===")
    print(f"共收到 {len(feedback_received)} 条反馈数据")
    return True

if __name__ == '__main__':
    success = test_model_monitor()
    sys.exit(0 if success else 1)