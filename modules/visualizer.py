"""
排列5可视化模块

负责生成各种统计图表和可视化效果，为GUI展示提供图形支持。
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
    logging.warning("matplotlib未安装，可视化功能将受限")

from paths import REPORTS_CHARTS_DIR
from config import DATA_WINDOW_PERIOD

logger = logging.getLogger(__name__)

# 颜色主题配置（与main.py的ThemeManager保持一致）
DARK_THEME = {
    'bg_primary': '#0f172a',
    'bg_secondary': '#1e293b',
    'bg_card': '#334155',
    'bg_card_hover': '#475569',
    'bg_input': '#475569',
    'bg_panel': '#334155',
    'accent_p5': '#10b981',
    'accent_p5_light': '#34d399',
    'accent_p5_bright': '#059669',
    'accent_ai': '#8b5cf6',
    'accent_ai_light': '#a78bfa',
    'accent_ai_bright': '#7c3aed',
    'accent_danger': '#ef4444',
    'accent_danger_light': '#f87171',
    'accent_info': '#0891b2',
    'accent_info_light': '#34d399',
    'accent_warning': '#f59e0b',
    'accent_warning_light': '#fbbf24',
    'text_primary': '#f8fafc',
    'text_secondary': '#e2e8f0',
    'text_muted': '#cbd5e1',
    'text_disabled': '#94a3b8',
    'border': '#475569',
    'border_light': '#64748b',
    'success': '#10b981',
    'success_light': '#34d399',
    'warning': '#f59e0b',
    'gradient_start': '#0f172a',
    'gradient_end': '#1e293b',
    'shadow': '#000000',
    'card_shadow': '#000000',
    'card_elevation': 0.04,
    'accent_pink': '#ec4899',
    'accent_pink_light': '#f472b6',
    'gradient_p5': ['#059669', '#10b981'],
    'gradient_ai': ['#7c3aed', '#a78bfa'],
    'surface_highlight': '#ffffff08',
    'divider': '#334155',
    'badge_active': '#059669',
    'badge_trial': '#64748b',
    'badge_rolledback': '#d97706',
    'badge_active_bg': '#059669',
    'badge_active_fg': '#ffffff',
    'badge_trial_bg': '#334155',
    'badge_trial_fg': '#94a3b8',
    'badge_rolledback_bg': '#451a03',
    'badge_rolledback_fg': '#f59e0b',
}

LIGHT_THEME = {
    'bg_primary': '#f1f5f9',
    'bg_secondary': '#e2e8f0',
    'bg_card': '#ffffff',
    'bg_card_hover': '#f1f5f9',
    'bg_input': '#f8fafc',
    'bg_panel': '#f1f5f9',
    'accent_p5': '#10b981',
    'accent_p5_light': '#34d399',
    'accent_p5_bright': '#059669',
    'accent_ai': '#8b5cf6',
    'accent_ai_light': '#a78bfa',
    'accent_ai_bright': '#7c3aed',
    'accent_danger': '#ef4444',
    'accent_danger_light': '#f87171',
    'accent_info': '#0891b2',
    'accent_info_light': '#34d399',
    'accent_warning': '#f59e0b',
    'accent_warning_light': '#fbbf24',
    'text_primary': '#0f172a',
    'text_secondary': '#475569',
    'text_muted': '#64748b',
    'text_disabled': '#94a3b8',
    'border': '#e2e8f0',
    'border_light': '#f1f5f9',
    'success': '#10b981',
    'success_light': '#34d399',
    'warning': '#d97706',
    'gradient_start': '#f8fafc',
    'gradient_end': '#f1f5f9',
    'shadow': '#000000',
    'card_shadow': '#00000010',
    'card_elevation': 0.06,
    'accent_pink': '#ec4899',
    'accent_pink_light': '#f472b6',
    'gradient_p5': ['#059669', '#10b981'],
    'gradient_ai': ['#7c3aed', '#a78bfa'],
    'surface_highlight': '#ffffff40',
    'divider': '#e2e8f0',
    'badge_active': '#059669',
    'badge_trial': '#64748b',
    'badge_rolledback': '#d97706',
    'badge_active_bg': '#059669',
    'badge_active_fg': '#ffffff',
    'badge_trial_bg': '#f1f5f9',
    'badge_trial_fg': '#64748b',
    'badge_rolledback_bg': '#fef3c7',
    'badge_rolledback_fg': '#d97706',
}

# 当前主题（将在初始化时从main.py同步）
_CURRENT_THEME = LIGHT_THEME  # 默认值，实际使用时会被主程序覆盖


def set_theme(theme: dict):
    """设置当前主题"""
    global _CURRENT_THEME
    _CURRENT_THEME = theme


def get_theme_colors() -> dict:
    """获取当前主题颜色"""
    return _CURRENT_THEME.copy()


class LotteryVisualizer:
    """排列5可视化类"""
    
    def __init__(self):
        """初始化可视化器"""
        self.positions = 5
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.number_range = list(range(10))
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 确保报告目录存在
        os.makedirs(REPORTS_CHARTS_DIR, exist_ok=True)
    
    def _get_chart_colors(self, chart_type: str = 'default') -> dict:
        """根据图表类型和主题获取颜色配置"""
        theme = get_theme_colors()
        
        if chart_type == 'trend':
            return {
                'line': theme['accent_p5'],
                'line_secondary': theme['accent_ai'],
                'background': theme['bg_card'],
                'grid': theme['border'],
                'text': theme['text_primary'],
                'hot': theme['accent_danger'],
                'warm': theme['accent_warning'],
                'cold': theme['accent_info']
            }
        elif chart_type == 'histogram':
            return {
                'bars': theme['accent_p5'],
                'bars_secondary': theme['accent_ai'],
                'background': theme['bg_card'],
                'grid': theme['border'],
                'text': theme['text_primary'],
                'hot': theme['accent_danger'],
                'warm': theme['accent_warning'],
                'cold': theme['accent_info']
            }
        elif chart_type == 'heatmap':
            return {
                'background': theme['bg_card'],
                'text': theme['text_primary'],
                'cmap': 'RdYlBu_r',  # 红黄蓝配色方案，红色表示高值
                'hot': theme['accent_danger'],
                'warm': theme['accent_warning'],
                'cold': theme['accent_info']
            }
        elif chart_type == 'line':
            return {
                'line': theme['accent_p5'],
                'line_secondary': theme['accent_ai'],
                'background': theme['bg_card'],
                'grid': theme['border'],
                'text': theme['text_primary'],
                'hot': theme['accent_danger'],
                'warm': theme['accent_warning'],
                'cold': theme['accent_info']
            }
        else:
            # 默认颜色
            return {
                'line': theme['accent_p5'],
                'background': theme['bg_card'],
                'grid': theme['border'],
                'text': theme['text_primary']
            }
    
    def plot_basic_trend(self, history_data: List[Dict], 
                        output_path: Optional[str] = None,
                        window: int = 50) -> Optional[str]:
        """
        绘制基础走势趋势图
        
        Args:
            history_data: 历史开奖数据列表（按时间正序排列）
            output_path: 输出文件路径，如果为None则自动生成
            window: 显示的历史期数窗口，默认50期
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成走势图")
                return None
                
            # 限制显示的数据量
            display_data = history_data[-window:] if len(history_data) > window else history_data
            
            # 提取每个位置的历史号码序列
            position_sequences = {pos: [] for pos in self.position_names}
            issues = []
            
            for item in display_data:
                issue = item.get('issue', '')
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    issues.append(issue)
                    for i, pos_name in enumerate(self.position_names):
                        position_sequences[pos_name].append(int(numbers[i]))
            
            if not issues:
                self.logger.warning("没有有效的历史数据，无法生成走势图")
                return None
                
            # 创建图表
            colors = self._get_chart_colors('trend')
            fig, axes = plt.subplots(self.positions, 1, figsize=(12, 2 * self.positions))
            if self.positions == 1:
                axes = [axes]
            fig.suptitle('排列5基本走势趋势图', fontsize=16, fontweight='bold', color=colors['text'])
            
            # 为每个位置绘制走势
            for i, (pos_name, sequence) in enumerate(position_sequences.items()):
                ax = axes[i]
                ax.plot(range(len(sequence)), sequence, 
                       color=colors['line'], linewidth=2, marker='o', markersize=3)
                ax.set_title(f'{pos_name}走势', fontsize=12, color=colors['text'])
                ax.set_xlabel('期数（从旧到新）', fontsize=10, color=colors['text'])
                ax.set_ylabel('号码', fontsize=10, color=colors['text'])
                ax.set_yticks(range(10))
                ax.grid(True, alpha=0.3, color=colors['grid'])
                ax.tick_params(colors=colors['text'])
                
                # 设置背景颜色
                ax.set_facecolor(colors['background'])
                
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'basic_trend_{timestamp}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'基本走势趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成基本走势趋势图时出错: {e}')
            return None
    
    def plot_cold_hot_histogram(self, history_data: List[Dict],
                               output_path: Optional[str] = None,
                               window: int = 30) -> Optional[str]:
        """
        绘制冷热分布直方图
        
        Args:
            history_data: 历史开奖数据列表（按时间正序排列）
            output_path: 输出文件路径，如果为None则自动生成
            window: 用于计算冷热的历史期数窗口，默认30期
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成冷热分布直方图")
                return None
                
            # 使用最近window期数据计算冷热分布
            window_data = history_data[-window:] if len(history_data) > window else history_data
            
            # 统计每个位置每个号码的出现频率
            position_freq = {pos: {num: 0 for num in self.number_range} 
                           for pos in self.position_names}
            
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    for i, pos_name in enumerate(self.position_names):
                        num = int(numbers[i])
                        position_freq[pos_name][num] += 1
            
            # 计算频率百分比
            total_counts = len(window_data)
            position_freq_pct = {}
            for pos_name, freq_dict in position_freq.items():
                position_freq_pct[pos_name] = {
                    num: (count / total_counts * 100) if total_counts > 0 else 0
                    for num, count in freq_dict.items()
                }
            
            # 定义冷热阈值（可以根据实际需求调整）
            # 这里我们使用频率的33%和66%分位点作为冷热阈值
            all_freqs = [freq for freq_dict in position_freq_pct.values() for freq in freq_dict.values()]
            if not all_freqs:
                self.logger.warning("没有频率数据，无法生成冷热分布直方图")
                return None
                
            sorted_freqs = sorted(all_freqs)
            low_threshold = sorted_freqs[len(sorted_freqs) // 3]
            high_threshold = sorted_freqs[2 * len(sorted_freqs) // 3]
            
            # 创建图表
            colors = self._get_chart_colors('histogram')
            fig, axes = plt.subplots(self.positions, 1, figsize=(12, 2 * self.positions))
            if self.positions == 1:
                axes = [axes]
            fig.suptitle('排列5冷热分布直方图', fontsize=16, fontweight='bold', color=colors['text'])
            
            # 为每个位置绘制直方图
            for i, (pos_name, freq_pct) in enumerate(position_freq_pct.items()):
                ax = axes[i]
                
                # 准备数据
                numbers = list(range(10))
                frequencies = [freq_pct[num] for num in numbers]
                
                # 根据冷热阈值着色
                bar_colors = []
                for freq in frequencies:
                    if freq >= high_threshold:
                        bar_colors.append(colors['hot'])  # 热号
                    elif freq >= low_threshold:
                        bar_colors.append(colors['warm'])  # 温号
                    else:
                        bar_colors.append(colors['cold'])  # 冷号
                
                # 绘制直方图
                bars = ax.bar(numbers, frequencies, color=bar_colors, alpha=0.8, edgecolor='black', linewidth=0.5)
                ax.set_title(f'{pos_name}号码冷热分布', fontsize=12, color=colors['text'])
                ax.set_xlabel('号码', fontsize=10, color=colors['text'])
                ax.set_ylabel('出现频率 (%)', fontsize=10, color=colors['text'])
                ax.set_xticks(range(10))
                ax.grid(True, alpha=0.3, axis='y', color=colors['grid'])
                ax.tick_params(colors=colors['text'])
                
                # 设置背景颜色
                ax.set_facecolor(colors['background'])
                
                # 添加阈值线
                ax.axhline(y=low_threshold, color=colors['cold'], linestyle='--', alpha=0.7, label=f'冷阈值 ({low_threshold:.1f}%)')
                ax.axhline(y=high_threshold, color=colors['hot'], linestyle='--', alpha=0.7, label=f'热阈值 ({high_threshold:.1f}%)')
                ax.legend(fontsize=8)
                
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'cold_hot_histogram_{timestamp}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'冷热分布直方图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成冷热分布直方图时出错: {e}')
            return None
    
    def plot_distribution_correlation(self, history_data: List[Dict],
                                     output_path: Optional[str] = None,
                                     window: int = 50) -> Optional[str]:
        """
        绘制分布关联矩阵热力图
        
        Args:
            history_data: 历史开奖数据列表（按时间正序排列）
            output_path: 输出文件路径，如果为None则自动生成
            window: 用于计算关联的历史期数窗口，默认50期
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成分布关联矩阵")
                return None
                
            # 使用最近window期数据计算关联矩阵
            window_data = history_data[-window:] if len(history_data) > window else history_data
            
            # 提取每个位置的号码序列
            position_sequences = {pos: [] for pos in self.position_names}
            
            for item in window_data:
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    for i, pos_name in enumerate(self.position_names):
                        position_sequences[pos_name].append(int(numbers[i]))
            
            # 计算位置之间的相关系数矩阵
            position_names_list = list(position_sequences.keys())
            n_positions = len(position_names_list)
            correlation_matrix = np.zeros((n_positions, n_positions))
            
            for i, pos_i in enumerate(position_names_list):
                seq_i = position_sequences[pos_i]
                for j, pos_j in enumerate(position_names_list):
                    seq_j = position_sequences[pos_j]
                    if len(seq_i) > 1 and len(seq_j) > 1:
                        # 计算皮尔逊相关系数
                        corr = np.corrcoef(seq_i, seq_j)[0, 1]
                        # 处理NaN值（当序列全相同的时候）
                        if np.isnan(corr):
                            corr = 0.0
                        correlation_matrix[i, j] = corr
                    else:
                        correlation_matrix[i, j] = 0.0
            
            # 创建图表
            colors = self._get_chart_colors('heatmap')
            fig, ax = plt.subplots(figsize=(10, 8))
            
            # 绘制热力图
            im = ax.imshow(correlation_matrix, cmap=colors['cmap'], vmin=-1, vmax=1)
            
            # 设置刻度
            ax.set_xticks(range(n_positions))
            ax.set_yticks(range(n_positions))
            ax.set_xticklabels(position_names_list, fontsize=10, color=colors['text'])
            ax.set_yticklabels(position_names_list, fontsize=10, color=colors['text'])
            
            # 在每个单元格中显示相关系数值
            for i in range(n_positions):
                for j in range(n_positions):
                    value = correlation_matrix[i, j]
                    text_color = 'white' if abs(value) > 0.5 else 'black'
                    ax.text(j, i, f'{value:.2f}', ha='center', va='center',
                           color=text_color, fontsize=9, fontweight='bold')
            
            ax.set_title('排列5位置号码分布关联矩阵', fontsize=16, fontweight='bold', color=colors['text'])
            
            # 添加颜色条
            cbar = plt.colorbar(im, ax=ax)
            cbar.set_label('相关系数', fontsize=12, color=colors['text'])
            cbar.ax.tick_params(colors=colors['text'])
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'distribution_correlation_{timestamp}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'分布关联矩阵已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成分布关联矩阵时出错: {e}')
            return None
    
    def plot_average_value_center(self, history_data: List[Dict],
                                 output_path: Optional[str] = None,
                                 window: int = 50) -> Optional[str]:
        """
        绘制和值重心曲线
        
        Args:
            history_data: 历史开奖数据列表（按时间正序排列）
            output_path: 输出文件路径，如果为None则自动生成
            window: 用于平滑的历史期数窗口，默认50期
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成和值重心曲线")
                return None
                
            # 计算每期的和值
            issues = []
            sum_values = []
            
            for item in history_data:
                issue = item.get('issue', '')
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    issues.append(issue)
                    sum_values.append(sum(int(n) for n in numbers))
            
            if not issues:
                self.logger.warning("没有有效的历史数据，无法生成和值重心曲线")
                return None
                
            # 如果数据量大于window，则应用移动平均平滑
            if len(sum_values) > window:
                # 简单移动平均
                smoothed_sums = []
                for i in range(len(sum_values)):
                    start_idx = max(0, i - window // 2)
                    end_idx = min(len(sum_values), i + window // 2 + 1)
                    window_sum = sum_values[start_idx:end_idx]
                    smoothed_sums.append(sum(window_sum) / len(window_sum))
                sum_values_to_plot = smoothed_sums
            else:
                sum_values_to_plot = sum_values
                
            # 创建图表
            colors = self._get_chart_colors('line')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 绘制和值曲线
            ax.plot(range(len(sum_values_to_plot)), sum_values_to_plot, 
                   color=colors['line'], linewidth=2, marker='o', markersize=3)
            
            # 添加平均值线
            avg_sum = np.mean(sum_values)
            ax.axhline(y=avg_sum, color=colors['line_secondary'], linestyle='--', 
                      alpha=0.8, label=f'历史平均和值: {avg_sum:.1f}')
            
            ax.set_title('排列5和值重心曲线', fontsize=16, fontweight='bold', color=colors['text'])
            ax.set_xlabel('期数（从旧到新）', fontsize=12, color=colors['text'])
            ax.set_ylabel('和值', fontsize=12, color=colors['text'])
            ax.set_yticks(range(0, 46, 5))  # 和值范围0-45
            ax.grid(True, alpha=0.3, color=colors['grid'])
            ax.tick_params(colors=colors['text'])
            ax.legend(fontsize=10)
            
            # 设置背景颜色
            ax.set_facecolor(colors['background'])
            fig.patch.set_facecolor(get_theme_colors()['bg_primary'])
            
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'average_value_center_{timestamp}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'和值重心曲线已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成和值重心曲线时出错: {e}')
            return None
    
    def plot_cold_hot_evolution(self, history_data: List[Dict],
                               output_path: Optional[str] = None,
                               window: int = 30,
                               evolution_window: int = 50) -> Optional[str]:
        """
        绘制冷热演变趋势图
        
        Args:
            history_data: 历史开奖数据列表（按时间正序排列）
            output_path: 输出文件路径，如果为None则自动生成
            window: 用于计算冷热的历史期数窗口，默认30期
            evolution_window: 用于显示演变的历史期数窗口，默认50期
            
        Returns:
            图片文件路径，如果失败则返回None
        """
        if not HAS_MATPLOTLIB:
            self.logger.warning("matplotlib未安装，无法生成图表")
            return None
            
        try:
            if not history_data:
                self.logger.warning("历史数据为空，无法生成冷热演变趋势图")
                return None
                
            # 限制显示的数据量用于演变趋势
            display_data = history_data[-evolution_window:] if len(history_data) > evolution_window else history_data
            
            # 为每个位置和每个号码计算其在滑动窗口中的冷热状态演变
            position_sequences = {pos: [] for pos in self.position_names}
            issues = []
            
            for item in display_data:
                issue = item.get('issue', '')
                numbers = item.get('numbers', [])
                if len(numbers) == self.positions:
                    issues.append(issue)
                    for i, pos_name in enumerate(self.position_names):
                        position_sequences[pos_name].append(int(numbers[i]))
            
            if not issues:
                self.logger.warning("没有有效的历史数据，无法生成冷热演变趋势图")
                return None
                
            # 计算每个位置每个号码在滑动窗口中的出现频率（用于确定冷热状态）
            # 我们将为每个时间点计算该点之前window期的频率
            evolution_data = {pos: {num: [] for num in self.number_range} 
                            for pos in self.position_names}
            
            # 对于每个时间点t，我们看[t-window, t)区间的数据来计算频率
            for t in range(len(display_data)):
                # 获取用于计算频率的窗口数据
                window_start = max(0, t - window)
                window_end = t
                if window_start >= window_end:
                    # 没有足够的历史数据，跳过
                    continue
                    
                window_data = display_data[window_start:window_end]
                
                # 计算这个窗口中每个位置每个号码的出现频率
                window_counts = {pos: {num: 0 for num in self.number_range} 
                               for pos in self.position_names}
                total_in_window = len(window_data)
                
                for item in window_data:
                    numbers = item.get('numbers', [])
                    if len(numbers) == self.positions:
                        for i, pos_name in enumerate(self.position_names):
                            num = int(numbers[i])
                            window_counts[pos_name][num] += 1
                
                # 计算频率并存储演变数据
                for pos_name in self.position_names:
                    for num in self.number_range:
                        freq = (window_counts[pos_name][num] / total_in_window * 100) if total_in_window > 0 else 0
                        evolution_data[pos_name][num].append(freq)
            
            # 定义冷热阈值（基于整个演变期间的频率分布）
            all_freqs = [freq for pos_data in evolution_data.values() 
                        for num_freqs in pos_data.values() 
                        for freq in num_freqs]
            if not all_freqs:
                self.logger.warning("没有频率数据，无法生成冷热演变趋势图")
                return None
                
            sorted_freqs = sorted(all_freqs)
            low_threshold = sorted_freqs[len(sorted_freqs) // 3]
            high_threshold = sorted_freqs[2 * len(sorted_freqs) // 3]
            
            # 创建图表
            colors = self._get_chart_colors('line')
            fig, axes = plt.subplots(self.positions, 1, figsize=(12, 2 * self.positions))
            if self.positions == 1:
                axes = [axes]
            fig.suptitle('排列5冷热演变趋势图', fontsize=16, fontweight='bold', color=colors['text'])
            
            # 为每个位置绘制冷热演变趋势（选择几个代表性号码）
            # 我们选择每个位置的中位数号码（4和5）以及冷热极端号码（0和9）
            representative_numbers = [0, 4, 5, 9]  # 冷、中偏冷、中偏热、热
            
            for i, (pos_name, num_sequences) in enumerate(evolution_data.items()):
                ax = axes[i]
                
                # 为每个代表性号码绘制其频率演变曲线
                for num in representative_numbers:
                    if num in num_sequences:
                        sequence = num_sequences[num]
                        # 只有当我们有足够的数据点时才绘制
                        if len(sequence) > 1:
                            label = f'号码{num}'
                            ax.plot(range(len(sequence)), sequence, 
                                   label=label, linewidth=2, marker='o', markersize=3)
                
                # 添加冷热阈值线
                ax.axhline(y=low_threshold, color=colors['cold'], linestyle='--', alpha=0.7, 
                          label=f'冷阈值 ({low_threshold:.1f}%)')
                ax.axhline(y=high_threshold, color=colors['hot'], linestyle='--', alpha=0.7, 
                          label=f'热阈值 ({high_threshold:.1f}%)')
                
                ax.set_title(f'{pos_name}号码冷热演变趋势', fontsize=12, color=colors['text'])
                ax.set_xlabel('时间点（从旧到新）', fontsize=10, color=colors['text'])
                ax.set_ylabel('出现频率 (%)', fontsize=10, color=colors['text'])
                ax.grid(True, alpha=0.3, color=colors['grid'])
                ax.tick_params(colors=colors['text'])
                ax.legend(fontsize=8, loc='upper right')
                
                # 设置背景颜色
                ax.set_facecolor(colors['background'])
                
            plt.tight_layout()
            
            # 保存图片
            if output_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = os.path.join(REPORTS_CHARTS_DIR, f'cold_hot_evolution_{timestamp}.png')
                
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight', 
                       facecolor=get_theme_colors()['bg_primary'])
            plt.close()
            
            self.logger.info(f'冷热演变趋势图已保存: {output_path}')
            return output_path
            
        except Exception as e:
            self.logger.error(f'生成冷热演变趋势图时出错: {e}')
            return None


# 创建全局可视化器实例
visualizer = LotteryVisualizer()