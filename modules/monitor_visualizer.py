"""
排列5 模型监控可视化模块

负责生成模型监控指标的可视化效果，为实时监控面板提供图形支持。
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

# 尝试导入matplotlib，如果不可用则使用占位符实现
try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # 使用非交互式后端
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logging.warning("matplotlib未安装，监控可视化功能将受限")

from paths import REPORTS_CHARTS_DIR

logger = logging.getLogger(__name__)

# 监控可视化主题配置
MONITOR_DARK_THEME = {
    'bg_primary': '#0f172a',
    'bg_secondary': '#1e293b',
    'bg_card': '#334155',
    'bg_card_hover': '#475569',
    'text_primary': '#f8fafc',
    'text_secondary': '#e2e8f0',
    'text_muted': '#cbd5e1',
    'border': '#475569',
    'border_light': '#64748b',
    'success': '#10b981',
    'warning': '#f59e0b',
    'danger': '#ef4444',
    'info': '#3b82f6',
    'grid': '#374151',
}

MONITOR_LIGHT_THEME = {
    'bg_primary': '#f8fafc',
    'bg_secondary': '#f1f5f9',
    'bg_card': '#ffffff',
    'bg_card_hover': '#f3f4f6',
    'text_primary': '#1f2937',
    'text_secondary': '#4b5563',
    'text_muted': '#6b7280',
    'border': '#e5e7eb',
    'border_light': '#d1d5db',
    'success': '#10b981',
    'warning': '#f59e0b',
    'danger': '#ef4444',
    'info': '#3b82f6',
    'grid': '#e5e7eb',
}

# 当前主题（将在初始化时从主程序同步）
_CURRENT_THEME = MONITOR_LIGHT_THEME  # 默认值


def set_theme(theme: dict):
    """设置当前主题"""
    global _CURRENT_THEME
    _CURRENT_THEME = theme


def get_theme_colors() -> dict:
    """获取当前主题颜色"""
    return _CURRENT_THEME.copy()


class MonitorVisualizer:
    """排列5模型监控可视化类"""
    
    def __init__(self):
        """初始化监控可视化器"""
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 确保报告目录存在
        os.makedirs(REPORTS_CHARTS_DIR, exist_ok=True)
    
    def _get_chart_colors(self, chart_type: str = 'default') -> dict:
        """根据图表类型和主题获取颜色配置"""
        theme = get_theme_colors()
        
        if chart_type == 'hit_rate':
            return {
                'line': theme['success'],
                'line_baseline': theme['info'],
                'line_warning': theme['warning'],
                'line_danger': theme['danger'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        elif chart_type == 'confidence':
            return {
                'line': theme['info'],
                'line_warning': theme['warning'],
                'line_danger': theme['danger'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        elif chart_type == 'response_time':
            return {
                'line': theme['warning'],
                'line_danger': theme['danger'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        elif chart_type == 'training_stability':
            return {
                'line': theme['danger'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        elif chart_type == 'feature_effectiveness':
            return {
                'line': theme['success'],
                'line_warning': theme['warning'],
                'line_danger': theme['danger'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        elif chart_type == 'dashboard':
            return {
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary'],
                'text_muted': theme['text_muted']
            }
        else:
            # 默认颜色
            return {
                'line': theme['success'],
                'background': theme['bg_card'],
                'grid': theme['grid'],
                'text': theme['text_primary']
            }
    
    def plot_model_hit_rate(self, 
                           history_data: List[Dict[str, Any]],
                           algorithm_names: List[str],
                           output_path: Optional[str] = None,
                           window_size: int = 30) -> Optional[str]:
        """
        绘制模型预测命中率趋势图
        
        Args:
            history_data: 历史监控数据列表，每项包含算法命中率信息
            algorithm_names: 要监控的算法名称列表
            output_path: 输出文件路径，如果为None则自动生成
            window_size: 移动平均窗口大小，默认30个数据点
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成模型命中率图表")
                return None
                
            # 提取时间序列和命中率数据
            timestamps = []
            hit_rates = {name: [] for name in algorithm_names}
            
            for record in history_data:
                if 'timestamp' in record:
                    timestamps.append(record['timestamp'])
                else:
                    # 如果没有时间戳，使用索引
                    timestamps.append(len(timestamps))
                    
                for name in algorithm_names:
                    rate_key = f'{name}_hit_rate' if f'{name}_hit_rate' in record else name
                    hit_rates[name].append(record.get(rate_key, 0.0))
            
            if not timestamps:
                self.logger.warning("没有有效的时间序列数据，无法生成模型命中率图表")
                return None
                
            # 应用移动平均平滑（如果数据量足够）
            smoothed_rates = {}
            for name in algorithm_names:
                rates = hit_rates[name]
                if len(rates) > window_size:
                    # 简单移动平均
                    smoothed = []
                    for i in range(len(rates)):
                        start_idx = max(0, i - window_size // 2)
                        end_idx = min(len(rates), i + window_size // 2 + 1)
                        window_data = rates[start_idx:end_idx]
                        smoothed.append(sum(window_data) / len(window_data))
                    smoothed_rates[name] = smoothed
                else:
                    smoothed_rates[name] = rates
            
            # 创建图表
            colors = self._get_chart_colors('hit_rate')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘制每个算法的命中率曲线
            for name in algorithm_names:
                ax.plot(range(len(smoothed_rates[name])), smoothed_rates[name], 
                       label=name, linewidth=2, marker='o', markersize=3)
            
            # 添加随机基线（10%）
            baseline = 0.10
            ax.axhline(y=baseline, color=colors['line_baseline'], linestyle='-', 
                      alpha=0.8, linewidth=2, label='随机基线 (10%)')
            
            # 添加警告线（基线-5%）
            warning_line = baseline - 0.05
            ax.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', 
                      alpha=0.8, linewidth=1.5, label='警告线 (5%)')
            
            # 添加危险线（基线-10%）
            danger_line = baseline - 0.10
            ax.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', 
                      alpha=0.8, linewidth=1.5, label='危险线 (10%)')
            
            ax.set_title('模型预测命令率趋势监控', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('监控点序号', fontsize=12, color=colors['text'])
            ax.set_ylabel('命中率', fontsize=12, color=colors['text'])
            ax.set_ylim(0, max(0.5, max([max(smoothed_rates[name]) for name in algorithm_names]) * 1.2))
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10, loc='upper right')
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'model_hit_rate_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'模型命中率趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成模型命中率趋势图时出错: {e}')
            return None
    
    def plot_model_confidence(self, 
                             history_data: List[Dict[str, Any]],
                             output_path: Optional[str] = None,
                             window_size: int = 30) -> Optional[str]:
        """
        绘制模型置信度趋势图
        
        Args:
            history_data: 历史监控数据列表，每项包含置信度信息
            output_path: 输出文件路径，如果为None则自动生成
            window_size: 移动平均窗口大小，默认30个数据点
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成模型置信度图表")
                return None
                
            # 提取时间序列和置信度数据
            timestamps = []
            confidences = []
            
            for record in history_data:
                if 'timestamp' in record:
                    timestamps.append(record['timestamp'])
                else:
                    timestamps.append(len(timestamps))
                    
                confidences.append(record.get('confidence', 0.0))
            
            if not timestamps:
                self.logger.warning("没有有效的时间序列数据，无法生成模型置信度图表")
                return None
                
            # 应用移动平均平滑（如果数据量足够）
            if len(confidences) > window_size:
                smoothed_confidences = []
                for i in range(len(confidences)):
                    start_idx = max(0, i - window_size // 2)
                    end_idx = min(len(confidences), i + window_size // 2 + 1)
                    window_data = confidences[start_idx:end_idx]
                    smoothed_confidences.append(sum(window_data) / len(window_data))
            else:
                smoothed_confidences = confidences
            
            # 创建图表
            colors = self._get_chart_colors('confidence')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘置信度曲线
            ax.plot(range(len(smoothed_confidences)), smoothed_confidences, 
                   label='模型置信度', linewidth=2, marker='o', markersize=3, color=colors['line'])
            
            # 添加警告线（0.6）
            warning_line = 0.6
            ax.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='警告线 (0.6)')
            
            # 添加危险线（0.3）
            danger_line = 0.3
            ax.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='危险线 (0.3)')
            
            ax.set_title('模型置信度趋势监控', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('监控点序号', fontsize=12, color=colors['text'])
            ax.set_ylabel('置信度', fontsize=12, color=colors['text'])
            ax.set_ylim(0, 1.0)
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10, loc='upper right')
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'model_confidence_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'模型置信度趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成模型置信度趋势图时出错: {e}')
            return None
    
    def plot_response_time(self, 
                          history_data: List[Dict[str, Any]],
                          output_path: Optional[str] = None,
                          window_size: int = 30) -> Optional[str]:
        """
        绘制模型响应时间趋势图
        
        Args:
            history_data: 历史监控数据列表，每项包含响应时间信息
            output_path: 输出文件路径，如果为None则自动生成
            window_size: 移动平均窗口大小，默认30个数据点
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成响应时间图表")
                return None
                
            # 提取时间序列和响应时间数据
            timestamps = []
            response_times = []
            
            for record in history_data:
                if 'timestamp' in record:
                    timestamps.append(record['timestamp'])
                else:
                    timestamps.append(len(timestamps))
                    
                response_times.append(record.get('response_time', 0.0))
            
            if not timestamps:
                self.logger.warning("没有有效的时间序列数据，无法生成响应时间图表")
                return None
                
            # 应用移动平均平滑（如果数据量足够）
            if len(response_times) > window_size:
                smoothed_times = []
                for i in range(len(response_times)):
                    start_idx = max(0, i - window_size // 2)
                    end_idx = min(len(response_times), i + window_size // 2 + 1)
                    window_data = response_times[start_idx:end_idx]
                    smoothed_times.append(sum(window_data) / len(window_data))
            else:
                smoothed_times = response_times
            
            # 创建图表
            colors = self._get_chart_colors('response_time')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘制响应时间曲线
            ax.plot(range(len(smoothed_times)), smoothed_times, 
                   label='响应时间', linewidth=2, marker='o', markersize=3, color=colors['line'])
            
            # 添加警告线（3秒）
            warning_line = 3.0
            ax.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='警告线 (3.0秒)')
            
            # 添加危险线（5秒）
            danger_line = 5.0
            ax.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='危险线 (5.0秒)')
            
            ax.set_title('模型响应时间趋势监控', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('监控点序号', fontsize=12, color=colors['text'])
            ax.set_ylabel('响应时间 (秒)', fontsize=12, color=colors['text'])
            ax.set_ylim(0, max(10.0, max(smoothed_times) * 1.2) if smoothed_times else 10.0)
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10, loc='upper right')
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'response_time_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'响应时间趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成响应时间趋势图时出错: {e}')
            return None
    
    def plot_training_stability(self, 
                               history_data: List[Dict[str, Any]],
                               output_path: Optional[str] = None,
                               window_size: int = 30) -> Optional[str]:
        """
        绘制训练稳定性趋势图（训练异常次数）
        
        Args:
            history_data: 历史监控数据列表，每项包含训练异常信息
            output_path: 输出文件路径，如果为None则自动生成
            window_size: 移动平均窗口大小，默认30个数据点
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成训练稳定性图表")
                return None
                
            # 提取时间序列和训练异常数据
            timestamps = []
            training_errors = []
            
            for record in history_data:
                if 'timestamp' in record:
                    timestamps.append(record['timestamp'])
                else:
                    timestamps.append(len(timestamps))
                    
                # 训练异常次数（可以是总异常次数或特定周期内的异常次数）
                training_errors.append(record.get('training_errors', 0))
            
            if not timestamps:
                self.logger.warning("没有有效的时间序列数据，无法生成训练稳定性图表")
                return None
                
            # 应用移动平均平滑（如果数据量足够）
            if len(training_errors) > window_size:
                smoothed_errors = []
                for i in range(len(training_errors)):
                    start_idx = max(0, i - window_size // 2)
                    end_idx = min(len(training_errors), i + window_size // 2 + 1)
                    window_data = training_errors[start_idx:end_idx]
                    smoothed_errors.append(sum(window_data) / len(window_data))
            else:
                smoothed_errors = training_errors
            
            # 创建图表
            colors = self._get_chart_colors('training_stability')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘制训练异常曲线
            ax.plot(range(len(smoothed_errors)), smoothed_errors, 
                   label='训练异常次数', linewidth=2, marker='o', markersize=3, color=colors['line'])
            
            # 添加警告线（2次）
            warning_line = 2.0
            ax.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='警告线 (2.0次)')
            
            # 添加危险线（5次）
            danger_line = 5.0
            ax.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', 
                      alpha=0.8, linewidth=2, label='危险线 (5.0次)')
            
            ax.set_title('训练稳定性趋势监控', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('监控点序号', fontsize=12, color=colors['text'])
            ax.set_ylabel('训练异常次数', fontsize=12, color=colors['text'])
            ax.set_ylim(0, max(10.0, max(smoothed_errors) * 1.2) if smoothed_errors else 10.0)
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10, loc='upper right')
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'training_stability_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'训练稳定性趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成训练稳定性趋势图时出错: {e}')
            return None
    
    def plot_feature_effectiveness(self, 
                                  history_data: List[Dict[str, Any]],
                                  feature_names: List[str],
                                  output_path: Optional[str] = None,
                                  window_size: int = 30) -> Optional[str]:
        """
        绘制特征有效性趋势图（特征贡献度变化率）
        
        Args:
            history_data: 历史监控数据列表，每项包含特征有效性信息
            feature_names: 要监控的特征名称列表
            output_path: 输出文件路径，如果为None则自动生成
            window_size: 移动平均窗口大小，默认30个数据点
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成特征有效性图表")
                return None
                
            # 提取时间序列和特征有效性数据
            timestamps = []
            effectiveness = {name: [] for name in feature_names}
            
            for record in history_data:
                if 'timestamp' in record:
                    timestamps.append(record['timestamp'])
                else:
                    timestamps.append(len(timestamps))
                    
                for name in feature_names:
                    eff_key = f'{name}_effectiveness' if f'{name}_effectiveness' in record else name
                    effectiveness[name].append(record.get(eff_key, 0.0))
            
            if not timestamps:
                self.logger.warning("没有有效的时间序列数据，无法生成特征有效性图表")
                return None
                
            # 应用移动平均平滑（如果数据量足够）
            smoothed_effectiveness = {}
            for name in feature_names:
                eff_values = effectiveness[name]
                if len(eff_values) > window_size:
                    smoothed = []
                    for i in range(len(eff_values)):
                        start_idx = max(0, i - window_size // 2)
                        end_idx = min(len(eff_values), i + window_size // 2 + 1)
                        window_data = eff_values[start_idx:end_idx]
                        smoothed.append(sum(window_data) / len(window_data))
                    smoothed_effectiveness[name] = smoothed
                else:
                    smoothed_effectiveness[name] = eff_values
            
            # 创建图表
            colors = self._get_chart_colors('feature_effectiveness')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘制每个特征的有效性曲线
            for name in feature_names:
                ax.plot(range(len(smoothed_effectiveness[name])), smoothed_effectiveness[name], 
                       label=name, linewidth=2, marker='o', markersize=3)
            
            ax.set_title('特征有效性趋势监控', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('监控点序号', fontsize=12, color=colors['text'])
            ax.set_ylabel('特征贡献度变化率', fontsize=12, color=colors['text'])
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10, loc='upper right')
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'feature_effectiveness_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'特征有效性趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成特征有效性趋势图时出错: {e}')
            return None
    
    def create_monitoring_dashboard(self, 
                                   hit_rate_data: List[Dict[str, Any]],
                                   confidence_data: List[Dict[str, Any]],
                                   response_time_data: List[Dict[str, Any]],
                                   training_stability_data: List[Dict[str, Any]],
                                   feature_effectiveness_data: List[Dict[str, Any]],
                                   algorithm_names: List[str],
                                   feature_names: List[str],
                                   output_path: Optional[str] = None) -> Optional[str]:
        """
        创建综合监控仪表板，包含所有监控指标
        
        Args:
            hit_rate_data: 模型命中率历史数据
            confidence_data: 模型置信度历史数据
            response_time_data: 响应时间历史数据
            training_stability_data: 训练稳定性历史数据
            feature_effectiveness_data: 特征有效性历史数据
            algorithm_names: 算法名称列表
            feature_names: 特征名称列表
            output_path: 输出文件路径，如果为None则自动生成
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            # 创建2x3的子图布局
            fig, axes = plt.subplots(2, 3, figsize=(18, 12))
            fig.suptitle('排列5模型实时监控仪表板', fontsize=20, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 1. 模型命中率趋势 (左上)
            ax1 = axes[0, 0]
            if hit_rate_data and algorithm_names:
                colors = self._get_chart_colors('hit_rate')
                timestamps = list(range(len(hit_rate_data)))
                for name in algorithm_names:
                    rates = [record.get(f'{name}_hit_rate', record.get(name, 0.0)) for record in hit_rate_data]
                    ax1.plot(timestamps, rates, label=name, linewidth=2)
                
                # 添加基线和警告线
                baseline = 0.10
                warning_line = baseline - 0.05
                danger_line = baseline - 0.10
                ax1.axhline(y=baseline, color=colors['line_baseline'], linestyle='-', alpha=0.8, linewidth=1.5, label='随机基线')
                ax1.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', alpha=0.8, linewidth=1, label='警告线')
                ax1.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', alpha=0.8, linewidth=1, label='危险线')
                
                ax1.set_title('模型预测命中率', fontsize=14, fontweight='bold', color=colors['text'])
                ax1.set_ylabel('命中率', fontsize=12, color=colors['text'])
                ax1.legend(fontsize=9)
                ax1.grid(True, alpha=0.3)
            else:
                ax1.text(0.5, 0.5, '暂无命中率数据', ha='center', va='center', transform=ax1.transAxes)
                ax1.set_title('模型预测命中率', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 2. 模型置信度趋势 (中上)
            ax2 = axes[0, 1]
            if confidence_data:
                colors = self._get_chart_colors('confidence')
                timestamps = list(range(len(confidence_data)))
                confidences = [record.get('confidence', 0.0) for record in confidence_data]
                ax2.plot(timestamps, confidences, label='模型置信度', linewidth=2, color=colors['line'])
                
                # 添加警告线
                warning_line = 0.6
                danger_line = 0.3
                ax2.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', alpha=0.8, linewidth=1.5, label='警告线 (0.6)')
                ax2.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', alpha=0.8, linewidth=1.5, label='危险线 (0.3)')
                
                ax2.set_title('模型置信度', fontsize=14, fontweight='bold', color=colors['text'])
                ax2.set_ylabel('置信度', fontsize=12, color=colors['text'])
                ax2.legend(fontsize=9)
                ax2.grid(True, alpha=0.3)
            else:
                ax2.text(0.5, 0.5, '暂无置信度数据', ha='center', va='center', transform=ax2.transAxes)
                ax2.set_title('模型置信度', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 3. 响应时间趋势 (右上)
            ax3 = axes[0, 2]
            if response_time_data:
                colors = self._get_chart_colors('response_time')
                timestamps = list(range(len(response_time_data)))
                response_times = [record.get('response_time', 0.0) for record in response_time_data]
                ax3.plot(timestamps, response_times, label='响应时间', linewidth=2, color=colors['line'])
                
                # 添加警告线
                warning_line = 3.0
                danger_line = 5.0
                ax3.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', alpha=0.8, linewidth=1.5, label='警告线 (3.0秒)')
                ax3.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', alpha=0.8, linewidth=1.5, label='危险线 (5.0秒)')
                
                ax3.set_title('模型响应时间', fontsize=14, fontweight='bold', color=colors['text'])
                ax3.set_ylabel('响应时间 (秒)', fontsize=12, color=colors['text'])
                ax3.legend(fontsize=9)
                ax3.grid(True, alpha=0.3)
            else:
                ax3.text(0.5, 0.5, '暂无响应时间数据', ha='center', va='center', transform=ax3.transAxes)
                ax3.set_title('模型响应时间', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 4. 训练稳定性趋势 (左下)
            ax4 = axes[1, 0]
            if training_stability_data:
                colors = self._get_chart_colors('training_stability')
                timestamps = list(range(len(training_stability_data)))
                training_errors = [record.get('training_errors', 0) for record in training_stability_data]
                ax4.plot(timestamps, training_errors, label='训练异常次数', linewidth=2, color=colors['line'])
                
                # 添加警告线
                warning_line = 2.0
                danger_line = 5.0
                ax4.axhline(y=warning_line, color=colors['line_warning'], linestyle='--', alpha=0.8, linewidth=1.5, label='警告线 (2.0次)')
                ax4.axhline(y=danger_line, color=colors['line_danger'], linestyle='--', alpha=0.8, linewidth=1.5, label='危险线 (5.0次)')
                
                ax4.set_title('训练稳定性', fontsize=14, fontweight='bold', color=colors['text'])
                ax4.set_ylabel('训练异常次数', fontsize=12, color=colors['text'])
                ax4.legend(fontsize=9)
                ax4.grid(True, alpha=0.3)
            else:
                ax4.text(0.5, 0.5, '暂无训练稳定性数据', ha='center', va='center', transform=ax4.transAxes)
                ax4.set_title('训练稳定性', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 5. 特征有效性趋势 (中下)
            ax5 = axes[1, 1]
            if feature_effectiveness_data and feature_names:
                colors = self._get_chart_colors('feature_effectiveness')
                timestamps = list(range(len(feature_effectiveness_data)))
                for name in feature_names:
                    effectiveness = [record.get(f'{name}_effectiveness', record.get(name, 0.0)) for record in feature_effectiveness_data]
                    ax5.plot(timestamps, effectiveness, label=name, linewidth=2)
                
                ax5.set_title('特征有效性', fontsize=14, fontweight='bold', color=colors['text'])
                ax5.set_ylabel('特征贡献度变化率', fontsize=12, color=colors['text'])
                ax5.legend(fontsize=9)
                ax5.grid(True, alpha=0.3)
            else:
                ax5.text(0.5, 0.5, '暂无特征有效性数据', ha='center', va='center', transform=ax5.transAxes)
                ax5.set_title('特征有效性', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 6. 系统状态指示灯 (右下)
            ax6 = axes[1, 2]
            ax6.axis('off')  # 不显示坐标轴
            
            # 创建状态指示灯
            status_items = [
                ("模型命中率", hit_rate_data is not None and len(hit_rate_data) > 0),
                ("模型置信度", confidence_data is not None and len(confidence_data) > 0),
                ("响应时间", response_time_data is not None and len(response_time_data) > 0),
                ("训练稳定性", training_stability_data is not None and len(training_stability_data) > 0),
                ("特征有效性", feature_effectiveness_data is not None and len(feature_effectiveness_data) > 0),
                ("数据更新", True)  # 假设数据总是可用的
            ]
            
            # 绘制状态指示灯
            for i, (label, is_ok) in enumerate(status_items):
                y_pos = 0.8 - i * 0.15
                color = get_theme_colors()['success'] if is_ok else get_theme_colors()['danger']
                ax6.add_patch(plt.Circle((0.2, y_pos), 0.03, color=color))
                ax6.text(0.25, y_pos, label, fontsize=10, va='center', color=get_theme_colors()['text_primary'])
            
            ax6.set_title('系统状态', fontsize=14, fontweight='bold', color=get_theme_colors()['text_primary'])
            
            # 设置整体背景
            for ax in axes.flat:
                ax.set_facecolor(get_theme_colors()['bg_card'])
                for spine in ax.spines.values():
                    spine.set_color(get_theme_colors()['border'])
            
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'monitoring_dashboard_{timestamp_str}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'监控仪表板已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成监控仪表板时出错: {e}')
            return None


# 创建全局监控可视化器实例
monitor_visualizer = MonitorVisualizer()