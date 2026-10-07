"""
排列5 模型监控反馈机制

负责实时监控模型预测性能，收集监控指标，触发告警，并将监控数据反馈至模型优化过程。
"""

import logging
import os
import threading
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from collections import deque
import numpy as np

logger = logging.getLogger(__name__)

# 监控配置
MONITOR_CONFIG = {
    # 监控指标阈值
    'hit_rate_warning': 0.05,      # 命中率低于随机基线5%告警
    'hit_rate_danger': 0.10,       # 命中率低于随机基线10%危险
    'confidence_warning': 0.6,     # 置信度低于0.6告警
    'confidence_danger': 0.3,      # 置信度低于0.3危险
    'response_time_warning': 3.0,  # 响应时间超过3秒告警
    'response_time_danger': 5.0,   # 响应时间超过5秒危险
    'training_errors_warning': 2,  # 训练异常超过2次告警
    'training_errors_danger': 5,   # 训练异常超过5次危险
    
    # 监控窗口大小
    'monitor_window_size': 30,     # 监控窗口保留的历史数据点数
    
    # 告警检查间隔（秒）
    'alert_check_interval': 5,
    
    # 反馈触发条件
    'feedback_trigger_count': 10,  # 收集多少监控点后触发反馈
}

# 随机基线（诚实边界）
RANDOM_BASELINE = {
    'top1': 0.10,   # 10%
    'top3': 0.30,   # 30%
    'top5': 0.50,   # 50%
}

class ModelMonitor:
    """模型监控反馈机制类"""
    
    def __init__(self, feedback_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        """
        初始化模型监控器
        
        Args:
            feedback_callback: 监控数据反馈回调函数，用于将监控数据送入模型优化流程
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.feedback_callback = feedback_callback
        
        # 监控数据存储（使用固定大小的deque实现滑动窗口）
        self.monitor_data = {
            'hit_rates': deque(maxlen=MONITOR_CONFIG['monitor_window_size']),
            'confidences': deque(maxlen=MONITOR_CONFIG['monitor_window_size']),
            'response_times': deque(maxlen=MONITOR_CONFIG['monitor_window_size']),
            'training_errors': deque(maxlen=MONITOR_CONFIG['monitor_window_size']),
            'feature_effectiveness': deque(maxlen=MONITOR_CONFIG['monitor_window_size']),
            'timestamps': deque(maxlen=MONITOR_CONFIG['monitor_window_size'])
        }
        
        # 告警状态
        self.alert_states = {
            'hit_rate': False,
            'confidence': False,
            'response_time': False,
            'training_errors': False
        }
        
        # 反馈数据缓存
        self.feedback_buffer = []
        self.feedback_lock = threading.Lock()
        
        # 监控线程控制
        self._monitor_thread = None
        self._stop_monitor = threading.Event()
        self._is_monitoring = False
        
        # §3.4 反馈「沿边触发」守卫：记录上次触发所跨越的阈值倍数（floor(len/threshold)）。
        # 阈值检查是「水位型」(len >= threshold)：若守护线程每 5s 轮询一次，
        # 达到 10 个样本后会**恒真**、每 5s 重复调用 feedback_callback 并重复追加
        # 落盘洞察 → 无限刷 data/model_monitor_feedback.jsonl。
        # 改为按「跨越的阈值倍数」沿边触发：仅当 floor(len/threshold) 超过已记录值才触发，
        # 即恰好达到 threshold（=10）时触发 1 次，再需 +10 才触发下一次；未达阈值则等待。
        self._last_feedback_threshold_crossed = 0
        
        # 确保报告目录存在
        from paths import REPORTS_CHARTS_DIR
        os.makedirs(REPORTS_CHARTS_DIR, exist_ok=True)
        
        self.logger.info('模型监控反馈机制初始化完成')
    
    def start_monitoring(self):
        """启动实时监控线程"""
        if self._is_monitoring:
            self.logger.warning('监控已经在运行中')
            return
            
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        self._is_monitoring = True
        self.logger.info('实时模型监控已启动')
    
    def stop_monitoring(self):
        """停止实时监控线程"""
        if not self._is_monitoring:
            self.logger.warning('监控尚未启动')
            return
            
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
        self._is_monitoring = False
        self.logger.info('实时模型监控已停止')
    
    def _monitor_loop(self):
        """监控主循环"""
        while not self._stop_monitor.is_set():
            try:
                # 检查告警条件
                self._check_alerts()
                
                # 检查是否需要触发反馈
                self._check_feedback_trigger()
                
                # 等待下一个检查周期
                time.sleep(MONITOR_CONFIG['alert_check_interval'])
                
            except Exception as e:
                self.logger.error(f'监控循环出错: {e}')
                time.sleep(MONITOR_CONFIG['alert_check_interval'])
    
    def record_prediction_result(self, algorithm_name: str, hit_rate: float,
                               confidence: Optional[float] = None,
                               response_time: Optional[float] = None,
                               training_errors: Optional[int] = None,
                               feature_effectiveness: Optional[Dict[str, float]] = None):
        """
        记录单次预测结果的监控数据

        Args:
            algorithm_name: 算法名称
            hit_rate: 命中率 (0-1)，必填（验证后真实可知）
            confidence: 模型置信度 (0-1)，可选——验证点不可知时传 None 则跳过该窗口，
                避免用 0.0 污染 5 点均值窗口误触发告警
            response_time: 响应时间 (秒)，可选——同上，None 则跳过
            training_errors: 训练异常次数，可选——同上，None 则跳过
            feature_effectiveness: 特征有效性字典
        """
        timestamp = datetime.now()

        # 记录监控数据（未采集的指标传 None 时跳过，不污染对应窗口）
        self.monitor_data['hit_rates'].append(hit_rate)
        if confidence is not None:
            self.monitor_data['confidences'].append(confidence)
        if response_time is not None:
            self.monitor_data['response_times'].append(response_time)
        if training_errors is not None:
            self.monitor_data['training_errors'].append(training_errors)
        self.monitor_data['feature_effectiveness'].append(feature_effectiveness or {})
        self.monitor_data['timestamps'].append(timestamp)

        # 记录到日志（调试用）
        self.logger.debug(
            f'记录预测监控数据: 算法={algorithm_name}, 命中率={hit_rate:.4f}, '
            f'置信度={confidence if confidence is not None else "N/A"}, '
            f'响应时间={response_time if response_time is not None else "N/A"}, '
            f'训练异常={training_errors if training_errors is not None else "N/A"}'
        )
    
    def _check_alerts(self):
        """检查告警条件"""
        try:
            # 检查命中率告警
            if len(self.monitor_data['hit_rates']) >= 5:
                recent_hit_rate = np.mean(list(self.monitor_data['hit_rates'])[-5:])
                baseline_top1 = RANDOM_BASELINE['top1']
                
                hit_rate_delta = recent_hit_rate - baseline_top1
                
                if hit_rate_delta <= -MONITOR_CONFIG['hit_rate_danger']:
                    if not self.alert_states['hit_rate']:
                        self._trigger_alert('hit_rate', 'danger', 
                                          f'模型命中率显著低于随机基线: {recent_hit_rate:.4f} vs 基线 {baseline_top1:.4f}')
                        self.alert_states['hit_rate'] = True
                elif hit_rate_delta <= -MONITOR_CONFIG['hit_rate_warning']:
                    if not self.alert_states['hit_rate']:
                        self._trigger_alert('hit_rate', 'warning', 
                                          f'模型命中率低于随机基线: {recent_hit_rate:.4f} vs 基线 {baseline_top1:.4f}')
                        self.alert_states['hit_rate'] = True
                else:
                    self.alert_states['hit_rate'] = False
            
            # 检查置信度告警
            if len(self.monitor_data['confidences']) >= 5:
                recent_confidence = np.mean(list(self.monitor_data['confidences'])[-5:])
                
                if recent_confidence <= MONITOR_CONFIG['confidence_danger']:
                    if not self.alert_states['confidence']:
                        self._trigger_alert('confidence', 'danger', 
                                          f'模型置信度危险低: {recent_confidence:.4f}')
                        self.alert_states['confidence'] = True
                elif recent_confidence <= MONITOR_CONFIG['confidence_warning']:
                    if not self.alert_states['confidence']:
                        self._trigger_alert('confidence', 'warning', 
                                          f'模型置信度偏低: {recent_confidence:.4f}')
                        self.alert_states['confidence'] = True
                else:
                    self.alert_states['confidence'] = False
            
            # 检查响应时间告警
            if len(self.monitor_data['response_times']) >= 5:
                recent_response_time = np.mean(list(self.monitor_data['response_times'])[-5:])
                
                if recent_response_time >= MONITOR_CONFIG['response_time_danger']:
                    if not self.alert_states['response_time']:
                        self._trigger_alert('response_time', 'danger', 
                                          f'模型响应时间危险高: {recent_response_time:.2f}s')
                        self.alert_states['response_time'] = True
                elif recent_response_time >= MONITOR_CONFIG['response_time_warning']:
                    if not self.alert_states['response_time']:
                        self._trigger_alert('response_time', 'warning', 
                                          f'模型响应时间偏高: {recent_response_time:.2f}s')
                        self.alert_states['response_time'] = True
                else:
                    self.alert_states['response_time'] = False
            
            # 检查训练异常告警
            if len(self.monitor_data['training_errors']) >= 5:
                recent_training_errors = np.mean(list(self.monitor_data['training_errors'])[-5:])
                
                if recent_training_errors >= MONITOR_CONFIG['training_errors_danger']:
                    if not self.alert_states['training_errors']:
                        self._trigger_alert('training_errors', 'danger', 
                                          f'训练异常次数危险高: {recent_training_errors:.1f}')
                        self.alert_states['training_errors'] = True
                elif recent_training_errors >= MONITOR_CONFIG['training_errors_warning']:
                    if not self.alert_states['training_errors']:
                        self._trigger_alert('training_errors', 'warning', 
                                          f'训练异常次数偏高: {recent_training_errors:.1f}')
                        self.alert_states['training_errors'] = True
                else:
                    self.alert_states['training_errors'] = False
                    
        except Exception as e:
            self.logger.error(f'检查告警条件时出错: {e}')
    
    def _trigger_alert(self, metric_name: str, level: str, message: str):
        """
        触发告警
        
        Args:
            metric_name: 指标名称
            level: 告警级别 ('warning' 或 'danger')
            message: 告警消息
        """
        alert_info = {
            'timestamp': datetime.now().isoformat(),
            'metric': metric_name,
            'level': level,
            'message': message
        }
        
        if level == 'danger':
            self.logger.error(f'[MODEL MONITOR ALERT] {message}')
        else:
            self.logger.warning(f'[MODEL MONITOR ALERT] {message}')
        
        # 这里可以扩展为发送邮件、GUI弹窗、日志文件等多种告警方式
        # 目前先记录到日志，实际项目中可以根据需求扩展
        
        # 将告警信息也添加到反馈数据中
        with self.feedback_lock:
            self.feedback_buffer.append({
                'type': 'alert',
                'data': alert_info
            })
    
    def _check_feedback_trigger(self):
        """检查是否需要触发反馈（§3.4 沿边触发，避免守护线程每 5s 重复刷洞察）"""
        try:
            # 反馈触发以「命中率」为基准（hit_rate 为验证点必然记录的真实信号）。
            # 旧实现取 min(hit_rates, confidences, response_times)——当 response_time 等
            # 指标未采集（诚实原则下验证点不可知）时 min 恒为 0，反馈永不触发。
            # 改用 hit_rates 长度，使部分指标缺失时闭环仍能运转。
            threshold = MONITOR_CONFIG['feedback_trigger_count']
            if threshold <= 0:  # 防御：阈值守零/负则不触发反馈（避免 // 0 异常）
                return
            min_samples_needed = len(self.monitor_data['hit_rates'])

            # §3.4 沿边触发：仅当样本量「跨越」新的阈值倍数时才触发一次。
            # 水位型 `>= threshold` 在守护线程 5s 轮询下会恒真 → 每 5s 重复回调、
            # 重复追加落盘洞察。改为按 floor(len/threshold) 的沿边记录判断：
            # 达到 threshold(=10) 时触发 1 次；此后须再 +10 才触发下次；未达则等待。
            current_crossed = min_samples_needed // threshold
            if current_crossed > self._last_feedback_threshold_crossed:
                self._last_feedback_threshold_crossed = current_crossed

                # 准备反馈数据
                feedback_data = self._prepare_feedback_data()

                if feedback_data and self.feedback_callback:
                    try:
                        self.feedback_callback(feedback_data)
                        self.logger.debug('监控数据已反馈至优化流程')

                        # 清空反馈缓冲区（已处理的数据）
                        with self.feedback_lock:
                            # 只保留最近的告警数据，清空旧的监控数据反馈
                            recent_alerts = [item for item in self.feedback_buffer
                                           if item['type'] == 'alert']
                            self.feedback_buffer = recent_alerts[-5:]  # 保留最近5条告警
                    except Exception as e:
                        self.logger.error(f'反馈回调执行失败: {e}')

        except Exception as e:
            self.logger.error(f'检查反馈触发条件时出错: {e}')
    
    def _prepare_feedback_data(self) -> Optional[Dict[str, Any]]:
        """
        准备反馈数据
        
        Returns:
            反馈数据字典，如果数据不足则返回None
        """
        try:
            # 确保有足够的数据
            if len(self.monitor_data['hit_rates']) < 5:
                return None

            # 计算统计指标
            hit_rates = list(self.monitor_data['hit_rates'])
            confidences = list(self.monitor_data['confidences'])
            response_times = list(self.monitor_data['response_times'])
            training_errors = list(self.monitor_data['training_errors'])

            # 局部辅助：对单个指标窗口计算统计。空窗口（该指标未采集，如验证点
            # 不可知 response_time）返回全 None 的占位结构，避免 np.mean([])/np.min([])
            # 产生 nan 或抛异常，保证部分指标缺失时反馈数据仍可用。
            def _metric_stats(values: List[float]) -> Dict[str, Any]:
                if not values:
                    return {'current': None, 'trend': 'not_recorded',
                            'avg': None, 'std': None, 'min': None, 'max': None}
                recent5 = values[-5:]
                return {
                    'current': float(np.mean(recent5)),
                    'trend': self._calculate_trend(values),
                    'avg': float(np.mean(values)),
                    'std': float(np.std(values)) if len(values) > 1 else 0.0,
                    'min': float(np.min(values)),
                    'max': float(np.max(values)),
                }

            feedback_data = {
                'timestamp': datetime.now().isoformat(),
                'sample_count': len(hit_rates),
                'metrics': {
                    'hit_rate': _metric_stats(hit_rates),
                    'confidence': _metric_stats(confidences),
                    'response_time': _metric_stats(response_times),
                    'training_errors': _metric_stats(training_errors)
                },
                'baseline_comparison': {
                    'hit_rate_vs_random': {
                        'delta': (np.mean(hit_rates[-5:]) if len(hit_rates) >= 5 else np.mean(hit_rates)) - RANDOM_BASELINE['top1'],
                        'status': self._get_hit_rate_status()
                    }
                },
                'alert_summary': self._get_alert_summary()
            }
            
            # 特征有效性数据（如果有）
            if self.monitor_data['feature_effectiveness'] and any(self.monitor_data['feature_effectiveness']):
                feature_data_list = list(self.monitor_data['feature_effectiveness'])
                if feature_data_list:
                    # 合并所有特征数据，计算平均值
                    merged_features = {}
                    for feature_dict in feature_data_list:
                        if isinstance(feature_dict, dict):
                            for key, value in feature_dict.items():
                                if key not in merged_features:
                                    merged_features[key] = []
                                merged_features[key].append(value)
                    
                    # 计算每个特征的平均值
                    feature_avg = {}
                    for key, values in merged_features.items():
                        if values:
                            feature_avg[key] = np.mean(values)
                    
                    feedback_data['metrics']['feature_effectiveness'] = {
                        'current': feature_avg,
                        'avg': {key: np.mean(values) for key, values in merged_features.items() if values},
                        'count': len(feature_data_list)
                    }
            
            return feedback_data
            
        except Exception as e:
            self.logger.error(f'准备反馈数据时出错: {e}')
            return None
    
    def _calculate_trend(self, data: List[float]) -> str:
        """
        计算数据趋势
        
        Args:
            data: 数据列表
            
        Returns:
            趋势描述: 'up', 'down', 'stable'
        """
        if len(data) < 3:
            return 'insufficient_data'
        
        try:
            # 使用最近5个点或全部数据计算线性趋势
            recent_data = data[-5:] if len(data) >= 5 else data
            x = list(range(len(recent_data)))
            y = recent_data
            
            # 简单线性回归计算斜率
            if len(x) > 1:
                slope = np.polyfit(x, y, 1)[0]
                if slope > 0.01:
                    return 'up'
                elif slope < -0.01:
                    return 'down'
                else:
                    return 'stable'
            else:
                return 'stable'
        except Exception:
            return 'unknown'
    
    def _get_hit_rate_status(self) -> str:
        """
        获取命中率状态
        
        Returns:
            状态描述: 'excellent', 'good', 'warning', 'danger'
        """
        if len(self.monitor_data['hit_rates']) < 5:
            return 'unknown'
        
        recent_hit_rate = np.mean(list(self.monitor_data['hit_rates'])[-5:])
        baseline = RANDOM_BASELINE['top1']
        delta = recent_hit_rate - baseline
        
        if delta >= 0.05:
            return 'excellent'
        elif delta >= 0.02:
            return 'good'
        elif delta >= -MONITOR_CONFIG['hit_rate_warning']:
            return 'warning'
        else:
            return 'danger'
    
    def _get_alert_summary(self) -> Dict[str, Any]:
        """
        获取告警摘要
        
        Returns:
            告警摘要字典
        """
        active_alerts = []
        for metric, is_active in self.alert_states.items():
            if is_active:
                active_alerts.append(metric)
        
        return {
            'active_alerts': active_alerts,
            'alert_count': len(active_alerts),
            'has_danger': any(level == 'danger' for level in self.alert_states.values() if isinstance(level, str))
        }
    
    def get_current_status(self) -> Dict[str, Any]:
        """
        获取当前监控状态
        
        Returns:
            当前状态字典
        """
        status = {
            'is_monitoring': self._is_monitoring,
            'data_points': {
                'hit_rates': len(self.monitor_data['hit_rates']),
                'confidences': len(self.monitor_data['confidences']),
                'response_times': len(self.monitor_data['response_times']),
                'training_errors': len(self.monitor_data['training_errors'])
            },
            'alert_states': self.alert_states.copy(),
            'latest_values': {}
        }
        
        # 添加最新值
        if self.monitor_data['hit_rates']:
            status['latest_values']['hit_rate'] = self.monitor_data['hit_rates'][-1]
        if self.monitor_data['confidences']:
            status['latest_values']['confidence'] = self.monitor_data['confidences'][-1]
        if self.monitor_data['response_times']:
            status['latest_values']['response_time'] = self.monitor_data['response_times'][-1]
        if self.monitor_data['training_errors']:
            status['latest_values']['training_errors'] = self.monitor_data['training_errors'][-1]
        
        return status
    
    def export_monitor_data(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        导出监控数据到文件
        
        Args:
            output_path: 输出文件路径，如果为None则自动生成
            
        Returns:
            导出的文件路径
        """
        try:
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                from paths import REPORTS_CHARTS_DIR
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'model_monitor_data_{timestamp}.json')
            
            # 准备导出数据
            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'monitor_config': MONITOR_CONFIG,
                'random_baseline': RANDOM_BASELINE,
                'monitor_data': {
                    'hit_rates': list(self.monitor_data['hit_rates']),
                    'confidences': list(self.monitor_data['confidences']),
                    'response_times': list(self.monitor_data['response_times']),
                    'training_errors': list(self.monitor_data['training_errors']),
                    'feature_effectiveness': list(self.monitor_data['feature_effectiveness']),
                    'timestamps': [ts.isoformat() for ts in self.monitor_data['timestamps']]
                },
                'alert_states': self.alert_states.copy(),
                'current_status': self.get_current_status()
            }
            
            # 确保目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 写入文件
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f'监控数据已导出: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'导出监控数据时出错: {e}')
            return None


# 创建全局模型监控器实例
model_monitor = ModelMonitor()


def get_model_monitor() -> ModelMonitor:
    """
    获取全局模型监控器实例
    
    Returns:
        ModelMonitor实例
    """
    return model_monitor


# 便捷函数：记录预测结果
def record_prediction_metrics(algorithm_name: str, hit_rate: float,
                            confidence: Optional[float] = None,
                            response_time: Optional[float] = None,
                            training_errors: Optional[int] = None,
                            feature_effectiveness: Optional[Dict[str, float]] = None):
    """
    记录预测监控指标的便捷函数

    未采集的指标（验证时不可知的 confidence/response_time/training_errors）
    传 None 即可跳过，避免 0.0 污染 5 点均值窗口误触发告警。
    Args:
        algorithm_name: 算法名称
        hit_rate: 命中率 (0-1)
        confidence: 模型置信度 (0-1)
        response_time: 响应时间 (秒)
        training_errors: 训练异常次数
        feature_effectiveness: 特征有效性字典
    """
    model_monitor.record_prediction_result(
        algorithm_name, hit_rate, confidence, response_time, 
        training_errors, feature_effectiveness
    )


# 便捷函数：启动监控
def start_model_monitoring():
    """启动模型监控"""
    model_monitor.start_monitoring()


# 便捷函数：停止监控
def stop_model_monitoring():
    """停止模型监控"""
    model_monitor.stop_monitoring()


if __name__ == '__main__':
    # 测试代码
    def test_feedback_callback(data):
        print(f'收到反馈数据: {json.dumps(data, indent=2, ensure_ascii=False)}')
    
    # 创建监控器实例进行测试
    monitor = ModelMonitor(feedback_callback=test_feedback_callback)
    
    # 启动监控
    monitor.start_monitoring()
    
    # 模拟记录一些预测结果
    import random
    for i in range(20):
        hit_rate = random.uniform(0.08, 0.15)
        confidence = random.uniform(0.4, 0.9)
        response_time = random.uniform(0.5, 4.0)
        training_errors = random.randint(0, 3)
        
        monitor.record_prediction_result(
            'test_algorithm', hit_rate, confidence, response_time, training_errors
        )
        
        time.sleep(0.1)
    
    # 等待一下让监控线程处理数据
    time.sleep(2)
    
    # 获取当前状态
    status = monitor.get_current_status()
    print(f'当前监控状态: {json.dumps(status, indent=2, ensure_ascii=False)}')
    
    # 停止监控
    monitor.stop_monitoring()
    
    print('模型监控测试完成')