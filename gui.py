"""
排列5 AI智能分析系统 - GUI界面

基于tkinter的桌面应用程序，提供以下核心功能：
1. 数据爬取（增量/全量） - 从多个数据源获取排列5开奖数据并存储到MySQL
2. AI智能分析 - 集成文章爬取、双阶段AI分析（文章初步分析+综合预测）
3. 历史回测 - 批量历史数据验证，评估模型Top-1/Top-3命中率
4. 特征分析 - 提取频率、遗漏、012路、连号等统计特征
5. 预测验证 - 自动比对预测与实际开奖结果，生成性能报告

工作流程：
  数据爬取 → AI智能分析（文章→初步AI→Redis→综合AI→数据库） → 预测验证
                                                                    ↓
                               历史回测 ← 特征分析 ← 性能评估报告

架构说明：
  - TaskManager: 异步任务管理器，通过ThreadPoolExecutor在后台线程执行耗时操作
  - LotteryGUI: 主界面类，负责UI构建、事件绑定和业务逻辑协调
  - 所有业务方法通过_task_wrapper在后台线程执行，通过消息队列更新UI
"""

import sys
import os
import threading
import queue
import traceback
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("=" * 60)
    print("  [错误] 缺少 tkinter 模块！")
    print("  Python环境未包含tkinter，无法启动GUI。")
    print("=" * 60)
    input("\n  按回车键退出...")
    sys.exit(1)

# 确保项目根目录在sys.path中，以便模块导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 核心模块导入
# P5Database: 数据库连接、建表、增删改查操作
from modules.database_p5 import P5Database
# P5Spider: 多源数据爬虫（历史开奖数据+走势数据）
from modules.spider_p5 import P5Spider
# P5PredictionValidator: 预测结果验证与性能统计
from modules.prediction_validator import P5PredictionValidator
# OptimizedP5Predictor: 优化后的预测引擎（修复了原始版的排序/质数等Bug）
from modules.optimized_p5_predictor import OptimizedP5Predictor, OptimizedP5PredictorConfig
# P5BacktestEngine: 历史回测引擎，支持对比分析和可视化
from modules.backtest_engine import P5BacktestEngine
# P5FeatureEngineering: 特征工程模块（频率、遗漏、012路、连号等）
from modules.feature_engineering import P5FeatureEngineering
# ArticleAnalyzer: 文章分析器（爬取→初步AI→Redis→综合AI→数据库）
from modules.article_analyzer import ArticleAnalyzer

# 全局颜色主题配置（暗色主题，基于 Tailwind CSS 色板）
COLORS = {
    'bg_primary': '#0f172a',  # 主背景色（深蓝灰）
    'bg_secondary': '#1e293b',  # 次背景色（稍浅蓝灰）
    'bg_card': '#334155',  # 卡片背景色
    'bg_input': '#1e1e2e',  # 输入/输出区域背景色
    'accent_p5': '#10b981',  # 排列5主题色（翠绿）
    'accent_ai': '#8b5cf6',  # AI分析主题色（紫色）
    'accent_danger': '#ef4444',  # 危险/错误色（红色）
    'text_primary': '#f1f5f9',  # 主文字色（浅白）
    'text_secondary': '#94a3b8',  # 次要文字色（灰蓝）
    'text_muted': '#64748b',  # 弱化文字色（中灰）
    'border': '#475569',  # 边框色
    'success': '#22c55e',  # 成功状态色（绿色）
    'warning': '#f59e0b',  # 警告状态色（琥珀色）
}


class TaskManager:
    """
    异步任务管理器

    通过 ThreadPoolExecutor（单工作线程）在后台执行耗时任务，
    使用 queue.Queue 作为消息通道将日志、进度、状态更新传递回主线程UI。
    这样既保证了UI响应性，又避免了线程安全问题。

    消息类型：
    - 'log': 日志文本 → _append_log()
    - 'progress': 进度更新 → _update_progress_ui()
    - 'status': 状态栏更新 → _update_status_ui()
    - 'finished': 任务完成 → _on_task_finished()
    - 'error': 任务失败 → _on_task_error()
    - 'report': 报告数据 → _display_report()
    """

    def __init__(self, gui_instance):
        """
        初始化任务管理器

        Args:
            gui_instance: LotteryGUI实例，用于回调UI更新方法
        """
        self.gui = gui_instance
        self._task_queue = queue.Queue()  # 线程安全的消息队列
        self._running = False  # 任务运行状态标志
        self._current_future = None  # 当前任务的Future对象
        self._executor = ThreadPoolExecutor(max_workers=1)  # 单线程池，保证任务串行
        self._lock = threading.Lock()  # 保护_running状态的锁
        self._cancelled = False  # 取消标志
        self._poll_ui_updates()  # 启动消息轮询循环

    def _poll_ui_updates(self):
        """
        定时轮询消息队列（每50ms），将后台线程的消息传递到主线程UI。
        通过 tkinter 的 after() 方法实现递归调度，不阻塞主线程。
        """
        try:
            while True:
                msg = self._task_queue.get_nowait()
                msg_type = msg.get('type', 'log')

                if msg_type == 'log':
                    self.gui._append_log(msg['text'])
                elif msg_type == 'progress':
                    self.gui._update_progress_ui(msg.get('value', 0), msg.get('text', ''))
                elif msg_type == 'status':
                    self.gui._update_status_ui(msg.get('text', ''), msg.get('color', COLORS['text_muted']))
                elif msg_type == 'finished':
                    self._on_task_finished()
                elif msg_type == 'error':
                    self._on_task_error(msg.get('error', '未知错误'))
                elif msg_type == 'report':
                    self.gui._display_report(msg.get('data', {}))
        except queue.Empty:
            pass

        self.gui.root.after(50, self._poll_ui_updates)

    def log(self, text):
        """向输出区域追加日志文本（线程安全）"""
        self._task_queue.put({'type': 'log', 'text': text})

    def progress(self, value, text=""):
        """更新进度条和进度文本（线程安全）"""
        self._task_queue.put({'type': 'progress', 'value': value, 'text': text})

    def status(self, text, color=COLORS['text_muted']):
        """更新状态栏文本（线程安全）"""
        self._task_queue.put({'type': 'status', 'text': text, 'color': color})

    def report(self, data):
        """投递报告数据到UI显示（线程安全）"""
        self._task_queue.put({'type': 'report', 'data': data})

    def finished(self):
        """通知UI任务已完成（线程安全）"""
        self._task_queue.put({'type': 'finished'})

    def error(self, err_text):
        """通知UI任务执行出错（线程安全）"""
        self._task_queue.put({'type': 'error', 'error': err_text})

    def is_running(self):
        """检查当前是否有任务正在执行（线程安全）"""
        with self._lock:
            return self._running

    def submit(self, task_func, task_name="任务"):
        """
        提交后台任务

        Args:
            task_func: 接收TaskManager实例作为参数的可调用对象
            task_name: 任务显示名称

        Returns:
            bool: 是否成功提交（如果已有任务运行则返回False）
        """
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancelled = False

        self.gui._on_task_started(task_name)
        self._current_future = self._executor.submit(self._task_wrapper, task_func)
        return True

    def _task_wrapper(self, task_func):
        """
        任务包装器，在线程池中执行。

        捕获所有异常并记录详细堆栈，确保即使任务崩溃也能正确清理状态。
        """
        try:
            task_func(self)
        except Exception as e:
            error_detail = traceback.format_exc()
            self.log(f"\n  [错误] 任务执行失败: {str(e)}\n")
            self.log(f"  [错误详情]\n{error_detail}\n")
            self.error(str(e))
        finally:
            if not self._cancelled:
                self.finished()

    def _on_task_finished(self):
        """任务完成时的清理工作"""
        with self._lock:
            self._running = False
        self.gui._on_task_finished()

    def _on_task_error(self, error_msg):
        """任务出错时的清理工作"""
        with self._lock:
            self._running = False
        self.gui._on_task_error(error_msg)

    def cancel(self):
        """取消当前任务（设置取消标志，但不会强制中断线程）"""
        self._cancelled = True
        with self._lock:
            self._running = False

    def shutdown(self):
        """关闭线程池（不等待正在执行的任务）"""
        self._executor.shutdown(wait=False)


class LotteryGUI:
    """
    排列5 AI智能分析系统主界面

    负责构建和管理整个GUI，包括：
    - 控制面板（数据爬取、AI分析、预测验证、系统操作四个功能卡片）
    - 输出面板（AI分析报告/日志显示区域）
    - 状态栏（任务状态、进度条、快捷统计）

    通过 TaskManager 将所有耗时业务逻辑放到后台线程执行，
    确保UI保持响应。
    """

    def __init__(self, root):
        """
        初始化主界面

        Args:
            root: tkinter.Tk 根窗口实例
        """
        self.root = root
        self.root.title("排列5 AI智能分析系统")
        # 适配常见屏幕尺寸，默认使用较大窗口确保内容完全可见
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        win_width = min(1280, screen_width - 40)
        win_height = min(860, screen_height - 80)
        self.root.geometry(f"{win_width}x{win_height}+{max(0, (screen_width - win_width) // 2)}+{max(0, (screen_height - win_height) // 2)}")
        self.root.minsize(1024, 720)
        self.root.configure(bg=COLORS['bg_primary'])

        self.task_mgr = TaskManager(self)  # 异步任务管理器
        self._buttons = []  # 所有按钮列表（用于批量启用/禁用）
        self._current_task_name = ""  # 当前正在执行的任务名称

        self._setup_window_style()
        self._build_ui()

    def _setup_window_style(self):
        """配置ttk样式主题（暗色主题，clam引擎）"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('微软雅黑', 10),
                        background=COLORS['bg_primary'],
                        foreground=COLORS['text_primary'],
                        fieldbackground=COLORS['bg_secondary'])
        style.configure('TFrame', background=COLORS['bg_primary'])
        style.configure('Horizontal.TProgressbar',
                        background=COLORS['accent_p5'],
                        troughcolor=COLORS['bg_card'],
                        borderwidth=0)

    def _build_ui(self):
        """构建完整的UI布局：顶部标题栏 + 中部（左侧控制面板 + 右侧输出区） + 底部状态栏"""
        main_container = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_container.pack(fill=tk.BOTH, expand=True)

        self._build_header(main_container)
        self._build_content(main_container)
        self._build_status_bar(main_container)

    def _build_header(self, parent):
        """构建顶部标题栏（Logo + 系统名称 + 实时时钟）"""
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLORS['bg_secondary'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        icon = tk.Canvas(left, width=32, height=32, bg=COLORS['bg_secondary'],
                         highlightthickness=0)
        icon.pack(side=tk.LEFT, pady=9)
        icon.create_rectangle(2, 2, 30, 14, fill=COLORS['accent_p5'], outline='', width=0)
        icon.create_rectangle(2, 18, 30, 30, fill=COLORS['accent_ai'], outline='', width=0)

        title_box = tk.Frame(left, bg=COLORS['bg_secondary'])
        title_box.pack(side=tk.LEFT, padx=(8, 0), pady=6)

        tk.Label(title_box, text="排列5 AI智能分析系统",
                 font=('微软雅黑', 13, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        tk.Label(title_box, text="多模型综合预测分析平台",
                 font=('微软雅黑', 8),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W)

        self.time_label = tk.Label(header, text="",
                                   font=('Consolas', 9),
                                   bg=COLORS['bg_secondary'],
                                   fg=COLORS['text_secondary'])
        self.time_label.pack(side=tk.RIGHT, padx=12, pady=15)
        self._update_time()

    def _update_time(self):
        """每秒更新顶部时钟显示"""
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.root.after(1000, self._update_time)

    def _build_content(self, parent):
        """构建中部内容区：左侧控制面板（可滚动，280px）+ 右侧输出面板（自适应）"""
        content = tk.Frame(parent, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # 左侧控制面板（可滚动，固定宽度280px）
        left_container = tk.Frame(content, bg=COLORS['bg_primary'], width=280)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left_container.pack_propagate(False)

        # 创建Canvas+Scrollbar实现左侧面板滚动
        left_canvas = tk.Canvas(left_container, bg=COLORS['bg_primary'],
                                highlightthickness=0, width=280)
        left_scrollbar = tk.Scrollbar(left_container, orient='vertical',
                                      command=left_canvas.yview, width=8,
                                      bg=COLORS['bg_card'],
                                      troughcolor=COLORS['bg_secondary'])
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left_inner = tk.Frame(left_canvas, bg=COLORS['bg_primary'])
        left_canvas.create_window((0, 0), window=left_inner, anchor='nw', width=270)

        # 让内部Frame自适应Canvas宽度
        def _on_left_configure(event):
            left_canvas.itemconfig('all', width=event.width - 4)

        left_canvas.bind('<Configure>', _on_left_configure)

        # 更新Canvas滚动区域
        def _on_inner_configure(event):
            left_canvas.configure(scrollregion=left_canvas.bbox('all'))

        left_inner.bind('<Configure>', _on_inner_configure)

        # 鼠标滚轮滚动支持
        def _on_mousewheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        left_canvas.bind('<Enter>', lambda e: left_canvas.bind_all('<MouseWheel>', _on_mousewheel))
        left_canvas.bind('<Leave>', lambda e: left_canvas.unbind_all('<MouseWheel>'))

        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._build_control_panel(left_inner)

        # 右侧输出面板（自适应宽度）
        right = tk.Frame(content, bg=COLORS['bg_primary'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_output_panel(right)

    def _build_control_panel(self, parent):
        """
        构建左侧控制面板，包含四个功能卡片：

        1. 数据爬取卡片 - 增量爬取 / 全量爬取
        2. AI智能分析卡片 - 执行AI分析 / 历史回测 / 特征分析
        3. 预测验证卡片 - 验证预测 / 性能报告
        4. 系统操作卡片 - 数据库检测 / 更新统计 / 清空输出

        以及进度条和快捷统计面板
        """
        # 数据爬取卡片
        crawl_card = self._create_card(parent, "数据爬取", '#f59e0b')
        crawl_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(crawl_card, "增量爬取数据", '#f59e0b',
                             lambda: self._on_button_click("增量爬取数据", self._execute_crawl_incremental))
        self._add_action_button(crawl_card, "全量爬取数据", '#d97706',
                                lambda: self._on_button_click("全量爬取数据", self._execute_crawl_full))

        # AI分析卡片（优化版）
        p5_card = self._create_card(parent, "AI智能分析", COLORS['accent_p5'])
        p5_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(p5_card, "执行AI智能分析", COLORS['accent_p5'],
                             lambda: self._on_button_click("AI智能分析", self._execute_optimized_p5_ai))
        self._add_action_button(p5_card, "执行历史回测", '#22c55e',
                                lambda: self._on_button_click("历史回测", self._execute_backtest))
        self._add_action_button(p5_card, "执行特征分析", '#f59e0b',
                                lambda: self._on_button_click("特征分析", self._execute_feature_analysis))

        # 预测验证卡片
        verify_card = self._create_card(parent, "预测验证", '#ec4899')
        verify_card.pack(fill=tk.X, pady=(0, 8))

        self._add_action_button(verify_card, "验证待验证预测", '#ec4899',
                                lambda: self._on_button_click("验证预测", self._execute_verify_predictions))
        self._add_action_button(verify_card, "性能评估报告", '#db2777',
                                lambda: self._on_button_click("性能评估", self._execute_performance_report))

        # 系统操作卡片
        common_card = self._create_card(parent, "系统操作", COLORS['accent_ai'])
        common_card.pack(fill=tk.X, pady=(0, 8))

        self._add_action_button(common_card, "数据库检测", COLORS['accent_ai'],
                                lambda: self._on_button_click("数据库检测", self._check_database))
        self._add_action_button(common_card, "更新快捷统计", '#06b6d4',
                                lambda: self._on_button_click("更新统计", self._update_quick_stats))
        self._add_action_button(common_card, "清空输出", COLORS['accent_danger'],
                                self._clear_output)

        progress_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                                 highlightbackground=COLORS['border'],
                                 highlightthickness=1)
        progress_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(progress_card, text="任务进度",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(8, 4))

        self.progress = ttk.Progressbar(progress_card, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.progress_label = tk.Label(progress_card, text="0%",
                                       font=('Consolas', 11, 'bold'),
                                       bg=COLORS['bg_secondary'],
                                       fg=COLORS['accent_p5'])
        self.progress_label.pack(anchor=tk.CENTER, pady=(0, 4))

        self.task_status_label = tk.Label(progress_card, text="就绪",
                                          font=('微软雅黑', 8),
                                          bg=COLORS['bg_secondary'],
                                          fg=COLORS['text_muted'])
        self.task_status_label.pack(anchor=tk.W, padx=10, pady=(0, 8))

        stats_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                              highlightbackground=COLORS['border'],
                              highlightthickness=1)
        stats_card.pack(fill=tk.X)

        tk.Label(stats_card, text="快捷统计",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=10, pady=(8, 4))

        self.stats_content = tk.Label(stats_card,
                                      text="点击「执行AI智能分析」开始",
                                      font=('微软雅黑', 8),
                                      bg=COLORS['bg_secondary'],
                                      fg=COLORS['text_muted'],
                                      justify=tk.LEFT)
        self.stats_content.pack(anchor=tk.W, padx=10, pady=(0, 8))

    def _create_card(self, parent, title, accent_color):
        """
        创建一个带标题和顶部彩色装饰条的功能卡片

        Args:
            parent: 父容器
            title: 卡片标题
            accent_color: 装饰条和圆点颜色

        Returns:
            tk.Frame: 卡片框架
        """
        card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'],
                        highlightthickness=1)

        # 顶部彩色装饰条（2px高度）
        top_bar = tk.Frame(card, bg=accent_color, height=2)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        # 标题行（彩色圆点 + 标题文字）
        title_frame = tk.Frame(card, bg=COLORS['bg_secondary'])
        title_frame.pack(fill=tk.X, padx=10, pady=(8, 6))

        dot = tk.Canvas(title_frame, width=8, height=8,
                        bg=COLORS['bg_secondary'], highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 6))
        dot.create_oval(1, 1, 7, 7, fill=accent_color, outline='')

        tk.Label(title_frame, text=title,
                 font=('微软雅黑', 11, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(side=tk.LEFT)

        return card

    def _add_big_button(self, parent, text, color, command):
        """
        添加主要操作按钮（大号、彩色背景、占满整行）

        Args:
            parent: 父容器
            text: 按钮文字
            color: 按钮背景色
            command: 点击回调函数

        Returns:
            tk.Button: 按钮实例
        """
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 11, 'bold'),
                        bg=color,
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=12, pady=10)
        btn.pack(fill=tk.X)

        light_color = self._lighten_color(color, 1.15)
        btn.bind('<Enter>', lambda e: btn.config(bg=light_color))
        btn.bind('<Leave>', lambda e: btn.config(bg=color))

        self._buttons.append(btn)
        return btn

    def _add_action_button(self, parent, text, color, command):
        """
        添加次要操作按钮（小号、深色背景、hover时变色）

        Args:
            parent: 父容器
            text: 按钮文字
            color: hover时的背景色
            command: 点击回调函数

        Returns:
            tk.Button: 按钮实例
        """
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=10, pady=2)

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 9),
                        bg=COLORS['bg_card'],
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=12, pady=5)
        btn.pack(fill=tk.X)

        # hover效果：鼠标进入时变亮，离开时恢复
        btn.bind('<Enter>', lambda e, b=btn, c=color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=COLORS['bg_card']))

        self._buttons.append(btn)
        return btn

    @staticmethod
    def _lighten_color(hex_color, factor):
        """
        将十六进制颜色按比例变亮

        Args:
            hex_color: 十六进制颜色字符串（如 '#10b981'）
            factor: 亮度因子（>1变亮，<1变暗）

        Returns:
            变亮后的十六进制颜色字符串
        """
        hex_color = hex_color.lstrip('#')
        r = min(255, int(int(hex_color[0:2], 16) * factor))
        g = min(255, int(int(hex_color[2:4], 16) * factor))
        b = min(255, int(int(hex_color[4:6], 16) * factor))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _build_output_panel(self, parent):
        """构建右侧输出面板（报告标题 + 可滚动的文本输出区）"""
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=30)
        header.pack(fill=tk.X, pady=(0, 2))
        header.pack_propagate(False)

        tk.Label(header, text="AI分析报告",
                 font=('微软雅黑', 9, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10, pady=5)

        self.log_level_label = tk.Label(header, text="INFO",
                                        font=('Consolas', 7),
                                        bg=COLORS['success'],
                                        fg=COLORS['text_primary'],
                                        padx=5, pady=1)
        self.log_level_label.pack(side=tk.RIGHT, padx=10, pady=5)

        text_container = tk.Frame(parent, bg=COLORS['bg_input'])
        text_container.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(text_container, bg=COLORS['bg_card'],
                                 troughcolor=COLORS['bg_secondary'],
                                 activebackground=COLORS['border'],
                                 relief='flat',
                                 width=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.output_text = tk.Text(text_container,
                                   wrap=tk.WORD,
                                   font=('Consolas', 9),
                                   bg=COLORS['bg_input'],
                                   fg=COLORS['text_primary'],
                                   insertbackground=COLORS['accent_p5'],
                                   relief='flat',
                                   padx=8, pady=8,
                                   state=tk.NORMAL,
                                   yscrollcommand=scrollbar.set)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar.config(command=self.output_text.yview)

        self._show_welcome()

    def _show_welcome(self):
        """显示欢迎信息和工作流程说明"""
        welcome = f"""
{'=' * 70}
  欢迎使用 排列5 AI智能分析系统 v2.0
{'=' * 70}

  【数据爬取】
    [增量爬取数据] 仅获取数据库中缺失的新数据
    [全量爬取数据] 重新爬取全部历史数据和走势数据

  【AI智能分析】（核心工作流）
    [执行AI智能分析] 完整分析流水线：
       阶段1: 文章爬取（从ydniu.com获取专家推荐）
       阶段2: 第一次AI分析（结构化整理文章内容）
       阶段3: Redis存储（所有文章缓存，7天过期）
       阶段4: 第二次AI分析（整合文章+历史数据，综合预测）
       阶段5: 数据库存储（保存最终分析报告）
    [执行历史回测] 批量历史回测，验证模型Top-1/Top-3命中率
    [执行特征分析] 提取频率、遗漏、012路、连号、重隔号等统计特征

  【预测验证】
    [验证待验证预测] 自动比对预测与实际开奖结果
    [性能评估报告] 生成AI预测命中率统计报告

  【系统操作】
    [数据库检测] 检测数据库连接、表结构、数据量
    [更新快捷统计] 刷新右侧统计面板的最新数据
    [清空输出] 清除当前输出区域内容

  ⚠️ 重要提示：本系统仅基于历史数据统计分析，无法预测开奖结果，
     不构成任何投资建议。彩票开奖具有随机性，请理性购彩。

  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'=' * 70}
"""
        self.output_text.insert(tk.END, welcome)
        self.output_text.see(tk.END)

    def _build_status_bar(self, parent):
        """构建底部状态栏（状态指示灯 + 状态文字 + 技术栈信息）"""
        status_bar = tk.Frame(parent, bg=COLORS['bg_secondary'], height=28)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        self.status_dot = tk.Canvas(status_bar, width=10, height=10,
                                    bg=COLORS['bg_secondary'],
                                    highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(12, 5), pady=8)
        self._status_dot_id = self.status_dot.create_oval(1, 1, 9, 9,
                                                          fill=COLORS['success'], outline='')

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(status_bar, textvariable=self.status_var,
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT, padx=5, pady=4)

        tk.Frame(status_bar, bg=COLORS['border'], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=5)

        tk.Label(status_bar, text="Python 3.x | AI分析 | MySQL 数据库",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(side=tk.LEFT, padx=5, pady=4)

    def _on_button_click(self, task_name, task_func):
        """按钮点击统一入口：检查任务状态后提交到后台线程"""
        if self.task_mgr.is_running():
            messagebox.showwarning("提示", "当前有任务正在执行，请等待完成")
            return

        success = self.task_mgr.submit(task_func, task_name)
        if not success:
            messagebox.showwarning("提示", "任务提交失败，请重试")

    def _on_task_started(self, task_name):
        """任务启动时：禁用按钮、重置进度条、更新状态指示器"""
        self._current_task_name = task_name
        self._set_buttons_state(tk.DISABLED)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("正在执行...")
        self.task_status_label.config(text=f"{task_name} 运行中...", fg=COLORS['warning'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])

        now = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"\n{'=' * 70}\n")
        self._append_log(f"  [{now}] 开始执行: {task_name}\n")
        self._append_log(f"{'=' * 70}\n\n")

    def _on_task_finished(self):
        """任务完成时：恢复按钮、进度条置100%、状态指示器变绿"""
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self.status_var.set("任务完成")
        self.task_status_label.config(text=f"{self._current_task_name} 已完成", fg=COLORS['success'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['success'])

        now = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"\n{'=' * 70}\n")
        self._append_log(f"  [{now}] 任务完成: {self._current_task_name}\n")
        self._append_log(f"{'=' * 70}\n")

    def _on_task_error(self, error_msg):
        """任务出错时：恢复按钮、进度条归零、状态指示器变红、弹窗提示"""
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("任务失败")
        self.task_status_label.config(text=f"{self._current_task_name} 失败", fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])

        messagebox.showerror("错误", f"任务执行失败:\n{error_msg}")

    def _append_log(self, text):
        """向输出文本区追加内容并自动滚动到底部"""
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)

    def _clear_output(self):
        """清空输出区并重新显示欢迎信息"""
        self.output_text.delete(1.0, tk.END)
        self._show_welcome()

    def _update_progress_ui(self, value, text=""):
        """更新进度条值和进度文本"""
        self.progress['value'] = value
        self.progress_label.config(text=f"{int(value)}%")
        if text:
            self.task_status_label.config(text=text, fg=COLORS['text_secondary'])

    def _update_status_ui(self, text, color=COLORS['text_muted']):
        """更新底部状态栏文字"""
        self.status_var.set(text)

    def _display_report(self, data):
        """显示从后台线程传递来的报告数据"""
        if data.get('report'):
            self._append_log(data['report'])

    def _set_buttons_state(self, state):
        """批量设置所有操作按钮的启用/禁用状态"""
        for btn in self._buttons:
            btn.config(state=state)

    # ============================================================
    # 业务任务 - 系统操作
    # ============================================================

    def _check_database(self, task_mgr):
        """数据库检测：验证MySQL连接、创建数据表、显示数据量统计"""
        task_mgr.log("正在检测数据库连接...")

        db = P5Database()
        if db.connect():
            task_mgr.log("✓ 数据库连接成功")

            db.create_tables()
            task_mgr.log("✓ 数据表创建成功")

            history_count = db.get_history_count()
            task_mgr.log(f"✓ 历史数据数量: {history_count}")

            report_count = db.get_report_count()
            task_mgr.log(f"✓ AI分析报告数量: {report_count}")

            latest_report = db.get_latest_ai_report()
            if latest_report:
                task_mgr.log(f"✓ 最新报告日期: {latest_report.get('report_date', '')}")
                task_mgr.log(f"✓ 最新报告期号: {latest_report.get('latest_issue', '')}")

            db.disconnect()
            task_mgr.log("\n数据库检测完成，系统状态正常")
            self.stats_content.config(
                text=f"历史数据: {history_count} | 报告: {report_count}",
                fg=COLORS['success']
            )
        else:
            task_mgr.log("✗ 数据库连接失败，请检查配置")
            self.stats_content.config(text="数据库连接失败", fg=COLORS['accent_danger'])

    # ============================================================
    # 业务任务 - 数据爬取
    # ============================================================

    def _execute_crawl_incremental(self, task_mgr):
        """增量爬取数据：仅获取数据库中缺失的新期号数据，含历史+走势数据"""
        task_mgr.log("启动增量数据爬取...")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(20, "连接数据库")

        from modules.database_p5 import P5Database
        db = P5Database()
        if not db.connect():
            task_mgr.log("✗ 数据库连接失败")
            return

        db.create_tables()
        task_mgr.progress(30, "获取已有最新期号")

        latest_issue = db.get_latest_history_issue()
        task_mgr.log(f"数据库最新期号: {latest_issue or '无数据'}")

        task_mgr.progress(40, "爬取历史数据")
        history_data = spider.crawl_history_data(max_records=500)
        task_mgr.log(f"爬取到 {len(history_data)} 条历史数据")

        if latest_issue and history_data:
            # 过滤已存在的期号
            new_data = [item for item in history_data if str(item.get('issue', '')) > str(latest_issue)]
            task_mgr.log(f"新增数据: {len(new_data)} 条")
        else:
            new_data = history_data

        task_mgr.progress(60, "保存历史数据")
        if new_data:
            history_success, history_skip = db.insert_history_data(new_data)
            task_mgr.log(f"历史数据保存: 成功{history_success}条, 跳过{history_skip}条")
        else:
            history_success = 0
            task_mgr.log("无新增历史数据")

        task_mgr.progress(80, "爬取走势数据")
        trend_data = spider.crawl_trend_data()
        task_mgr.log(f"爬取到 {len(trend_data)} 条走势数据")

        task_mgr.progress(90, "保存走势数据")
        if trend_data:
            trend_success, trend_skip = db.insert_trend_data(trend_data)
            task_mgr.log(f"走势数据保存: 成功{trend_success}条, 跳过{trend_skip}条")
        else:
            task_mgr.log("无新增走势数据")

        db.disconnect()
        task_mgr.progress(100, "完成")
        task_mgr.log(f"\n增量爬取完成: 新增历史{history_success}条")

    def _execute_crawl_full(self, task_mgr):
        """全量爬取数据：重新爬取全部历史数据（最多2000条）和走势数据"""
        task_mgr.log("启动全量数据爬取...")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(30, "爬取历史数据")

        history_data = spider.crawl_history_data(max_records=2000)
        task_mgr.log(f"爬取到 {len(history_data)} 条历史数据")

        task_mgr.progress(50, "爬取走势数据")
        trend_data = spider.crawl_trend_data()
        task_mgr.log(f"爬取到 {len(trend_data)} 条走势数据")

        task_mgr.progress(70, "连接数据库")
        from modules.database_p5 import P5Database
        db = P5Database()
        if not db.connect():
            task_mgr.log("✗ 数据库连接失败")
            return

        db.create_tables()
        task_mgr.progress(80, "保存历史数据")

        history_success, history_skip = db.insert_history_data(history_data)
        task_mgr.log(f"历史数据保存: 成功{history_success}条, 跳过{history_skip}条")

        task_mgr.progress(90, "保存走势数据")
        trend_success, trend_skip = db.insert_trend_data(trend_data)
        task_mgr.log(f"走势数据保存: 成功{trend_success}条, 跳过{trend_skip}条")

        db.disconnect()
        task_mgr.progress(100, "完成")
        task_mgr.log(f"\n全量爬取完成: 历史{history_success}条, 走势{trend_success}条")

    # ============================================================
    # 业务任务 - 预测验证
    # ============================================================

    def _execute_verify_predictions(self, task_mgr):
        """预测验证：获取待验证预测，比对实际开奖结果，更新性能统计"""
        task_mgr.log("启动预测结果验证...")
        task_mgr.progress(20, "初始化验证器")

        validator = P5PredictionValidator()
        task_mgr.progress(40, "获取待验证预测")

        pending = validator.get_pending_predictions()
        task_mgr.log(f"待验证预测数量: {len(pending)}")

        if not pending:
            task_mgr.log("当前没有待验证的预测记录")
            task_mgr.progress(100, "完成")
            return

        task_mgr.progress(60, "执行验证")
        results = validator.verify_all_pending()
        task_mgr.log(f"验证完成: {len(results)} 条记录已处理")

        for result in results:
            if result.get('status') == 'success':
                task_mgr.log(
                    f"  期号{result['target_issue']}: 命中{result['match_count']}/5, "
                    f"准确率{result['accuracy_rate']}%"
                )
            else:
                task_mgr.log(f"  验证失败: {result.get('message', '')}")

        task_mgr.progress(80, "更新性能统计")
        stats_result = validator.get_performance_stats()

        if stats_result.get('status') == 'success':
            stats = stats_result.get('current_stats', {})
            task_mgr.log(f"\n性能统计更新完成:")
            task_mgr.log(f"  总预测次数: {stats.get('total', 0)}")
            task_mgr.log(f"  完全猜中: {stats.get('total_matched', 0)}")
            task_mgr.log(f"  平均准确率: {stats.get('avg_accuracy', 0)}%")

        task_mgr.progress(100, "完成")
        task_mgr.log("\n预测验证流程结束")

    def _execute_performance_report(self, task_mgr):
        """性能评估报告：生成AI预测命中率统计报告，含总预测数/完全猜中/平均准确率"""
        task_mgr.log("生成AI预测性能评估报告...")
        task_mgr.progress(30, "获取统计数据")

        validator = P5PredictionValidator()
        report = validator.generate_performance_report()

        task_mgr.progress(80, "渲染报告")
        task_mgr.log("\n" + report)
        task_mgr.progress(100, "完成")

    def _update_quick_stats(self, task_mgr):
        """更新快捷统计面板：从数据库获取历史数据量、AI报告数和预测验证统计"""
        task_mgr.log("更新快捷统计...")

        db = P5Database()
        if not db.connect():
            task_mgr.log("数据库连接失败")
            return

        history_count = db.get_history_count()
        report_count = db.get_report_count()

        validator = P5PredictionValidator()
        stats_result = validator.get_performance_stats()

        stats_text = f"历史数据: {history_count} 条\nAI报告: {report_count} 份"

        if stats_result.get('status') == 'success':
            stats = stats_result.get('current_stats', {})
            if stats.get('total', 0) > 0:
                stats_text += (
                    f"\n预测验证: {stats['total']} 次"
                    f"\n完全猜中: {stats['total_matched']} 次"
                    f"\n平均准确: {stats.get('avg_accuracy', 0)}%"
                )

        db.disconnect()
        self.stats_content.config(text=stats_text, fg=COLORS['text_secondary'])
        task_mgr.log("快捷统计更新完成")

    # ============================================================
    # 业务任务 - AI分析核心流水线（v2.0）
    # ============================================================

    def _execute_optimized_p5_ai(self, task_mgr):
        """
        AI智能分析核心流水线（5阶段）

        阶段1: 文章爬取 - 从ydniu.com获取专家推荐文章
        阶段2: 第一次AI分析 - 结构化整理文章内容，提取预测信息
        阶段3: Redis存储 - 所有文章缓存到Redis，7天自动过期
        阶段4: 第二次AI分析 - 整合文章分析结果+数据库历史数据，综合预测
        阶段5: 数据库存储 - 保存最终分析报告到MySQL，生成JSON文件

        输出: 各位置预测号码/置信度、推荐组合、趋势分析、统计特征、推理过程
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("✨ 启动AI智能分析流程（集成文章爬取）")
            task_mgr.log("=" * 70)

            task_mgr.progress(5, "初始化分析器")

            analyzer = ArticleAnalyzer()

            task_mgr.log("\n【阶段1】文章爬取与存储")
            task_mgr.log("-" * 50)
            task_mgr.progress(10, "初始化爬虫模块")

            analyzer._init_spider()
            if not analyzer.spider:
                task_mgr.log("✗ 爬虫模块初始化失败")
                task_mgr.progress(0, "爬虫初始化失败")
                return

            task_mgr.log("正在爬取文章内容...")
            task_mgr.progress(20, "爬取文章")

            crawl_result = analyzer.spider.crawl_all_articles(target_issue=None, max_articles=30)

            if not crawl_result.get('articles'):
                task_mgr.log("✗ 未爬取到文章内容")
                task_mgr.progress(0, "无文章数据")
                return

            articles = crawl_result['articles']
            task_mgr.log(f"✓ 成功爬取 {len(articles)} 篇文章")

            task_mgr.progress(35, "文章爬取完成")

            task_mgr.log("\n【阶段2】初步AI分析（结构化整理）")
            task_mgr.log("-" * 50)
            task_mgr.progress(40, "执行第一次AI分析")

            first_ai_result = analyzer.first_ai_analysis(articles)

            if not first_ai_result:
                task_mgr.log("✗ 第一次AI分析失败")
                task_mgr.progress(0, "第一次AI分析失败")
                return

            task_mgr.log(f"✓ 第一次AI分析完成")
            task_mgr.log(f"  期号: {first_ai_result.get('issue_number', '未知')}")
            task_mgr.log(f"  置信度: {first_ai_result.get('confidence_level', '未知')}")

            task_mgr.progress(50, "初步分析完成")

            task_mgr.log("\n【阶段3】Redis存储")
            task_mgr.log("-" * 50)
            task_mgr.progress(55, "保存所有文章到Redis")

            issue = first_ai_result.get('issue_number')
            if issue:
                redis_save_result = analyzer.save_all_articles_to_redis(issue, articles, first_ai_result)
                if redis_save_result.get('success'):
                    saved_count = redis_save_result.get('saved_count', 0)
                    failed_count = redis_save_result.get('failed_count', 0)
                    total_count = redis_save_result.get('total_count', 0)
                    task_mgr.log(f"✓ 所有文章已保存到Redis")
                    task_mgr.log(f"  总文章数: {total_count} 篇")
                    task_mgr.log(f"  成功保存: {saved_count} 篇")
                    if failed_count > 0:
                        task_mgr.log(f"  保存失败: {failed_count} 篇")
                    task_mgr.log(f"  过期策略: 7天自动过期")
                else:
                    error_msg = redis_save_result.get('error', '未知错误')
                    task_mgr.log(f"⚠️ Redis保存失败: {error_msg}")
                    task_mgr.log("  继续执行后续流程")
            else:
                task_mgr.log("⚠️ 无法确定期号，跳过Redis存储")

            task_mgr.progress(65, "存储完成")

            task_mgr.log("\n【阶段4】综合AI分析（整合数据预测）")
            task_mgr.log("-" * 50)
            task_mgr.progress(70, "获取历史数据")

            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            history_data = db.get_history_data(limit=30, order_by='issue DESC')
            db.disconnect()

            if not history_data:
                task_mgr.log("✗ 数据库中没有历史数据")
                task_mgr.log("建议操作: 先执行「增量爬取数据」或「全量爬取数据」")
                task_mgr.progress(0, "无数据")
                return

            current_issue = history_data[0].get('issue', '')
            task_mgr.log(f"✓ 获取历史数据: {len(history_data)} 条")
            task_mgr.log(f"  当前最新期号: {current_issue}")

            task_mgr.progress(75, "准备第二次AI分析")

            analyzer._init_ai_client()
            if not analyzer.ai_client:
                task_mgr.log("✗ AI客户端初始化失败")
                task_mgr.progress(0, "AI客户端初始化失败")
                return

            db_history = analyzer.ai_client._fetch_data_from_database(limit=30)
            if db_history.get('error'):
                task_mgr.log(f"✗ 获取历史数据失败: {db_history['error']}")
                task_mgr.progress(0, "数据获取失败")
                return

            # 从Redis加载所有文章数据
            redis_data = analyzer.load_from_redis(issue)
            if not redis_data:
                task_mgr.log("⚠️ 从Redis加载数据失败，使用当前数据继续")
                redis_data = {
                    'ai_analysis': first_ai_result,
                    'articles': articles,
                    'article_data': article_data,
                    'issue': issue
                }

            task_mgr.log("正在执行第二次AI分析（整合文章+历史数据）...")
            task_mgr.progress(85, "第二次AI分析")

            second_ai_result = analyzer.second_ai_analysis(redis_data, db_history)

            if not second_ai_result:
                task_mgr.log("✗ 第二次AI分析失败")
                task_mgr.progress(0, "第二次AI分析失败")
                return

            task_mgr.log("✓ 第二次AI分析完成")

            task_mgr.log("\n【阶段5】数据库存储")
            task_mgr.log("-" * 50)
            task_mgr.progress(90, "保存到数据库")

            report_uuid = analyzer.save_to_database(second_ai_result, db_history)

            if report_uuid:
                task_mgr.log(f"✓ 最终报告已保存到数据库")
                task_mgr.log(f"  报告UUID: {report_uuid[:8]}...")
            else:
                task_mgr.log("⚠️ 数据库保存失败")

            task_mgr.progress(95, "生成分析报告")

            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("📊 AI智能分析报告（综合版）")
            task_mgr.log("=" * 70)

            task_mgr.log(f"\n【基本信息】")
            task_mgr.log(f"  数据来源: {second_ai_result.get('data_source', '未知')}")
            task_mgr.log(f"  分析时间: {second_ai_result.get('analysis_time', '未知')}")
            task_mgr.log(f"  当前期号: {second_ai_result.get('current_issue', '未知')}")
            task_mgr.log(f"  预测期号: {second_ai_result.get('next_issue', '未知')}")

            task_mgr.log(f"\n【各位置预测】")
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
            prediction = second_ai_result.get('prediction', {})
            for pos_key, pos_name in pos_names.items():
                pos_data = prediction.get(pos_key, {})
                numbers = pos_data.get('numbers', [])
                confidence = pos_data.get('confidence', [])
                reason = pos_data.get('reason', '')

                task_mgr.log(f"\n{pos_name}:")
                if numbers:
                    for i, (num, conf) in enumerate(zip(numbers, confidence), 1):
                        task_mgr.log(f"  {i}. 号码{num} (置信度: {conf:.2%})")
                if reason:
                    task_mgr.log(f"  理由: {reason[:50]}...")

            task_mgr.log(f"\n【推荐组合】")
            combinations = second_ai_result.get('recommended_combinations', [])
            for i, combo in enumerate(combinations, 1):
                if isinstance(combo, dict):
                    combo_str = combo.get('combination', combo.get('numbers', ''))
                    if isinstance(combo_str, list):
                        combo_str = ''.join(str(n) for n in combo_str)
                    confidence = combo.get('confidence', '')
                    reason = combo.get('reason', '')
                    task_mgr.log(f"  {i}. {combo_str}")
                    if confidence:
                        task_mgr.log(f"     置信度: {confidence:.2%}" if isinstance(confidence, float) else f"     置信度: {confidence}")
                    if reason:
                        task_mgr.log(f"     理由: {str(reason)[:60]}...")
                elif isinstance(combo, list):
                    task_mgr.log(f"  {i}. {''.join(str(n) for n in combo)}")
                else:
                    task_mgr.log(f"  {i}. {combo}")

            if second_ai_result.get('trend_analysis'):
                task_mgr.log(f"\n【趋势分析】")
                trend_summary = second_ai_result['trend_analysis'].get('summary', '')
                task_mgr.log(f"  {trend_summary[:100]}...")

            task_mgr.log(f"\n【关键统计特征】")
            statistical_features = second_ai_result.get('statistical_features', {})
            if statistical_features:
                task_mgr.log(f"  和值范围: {statistical_features.get('hezhi_range', '')}")
                task_mgr.log(f"  跨度范围: {statistical_features.get('span_range', '')}")
                task_mgr.log(f"  奇偶比: {statistical_features.get('odd_even_ratio', '')}")
                task_mgr.log(f"  大小比: {statistical_features.get('big_small_ratio', '')}")
                task_mgr.log(f"  热号: {statistical_features.get('hot_numbers', '')}")
                task_mgr.log(f"  冷号: {statistical_features.get('cold_numbers', '')}")
                patterns = statistical_features.get('key_patterns', [])
                for i, pattern in enumerate(patterns, 1):
                    task_mgr.log(f"  模式{i}: {str(pattern)[:60]}")
            else:
                # 回退：尝试显示 key_conclusions 或 key_features
                conclusions = (second_ai_result.get('key_conclusions')
                               or second_ai_result.get('key_features', []))
                if isinstance(conclusions, list):
                    for i, c in enumerate(conclusions, 1):
                        task_mgr.log(f"  {i}. {str(c)[:60]}")
                elif conclusions:
                    task_mgr.log(f"  {str(conclusions)[:200]}")

            task_mgr.log(f"\n【推理过程】")
            reasoning = second_ai_result.get('reasoning_process', '')
            if reasoning:
                if isinstance(reasoning, list):
                    for i, part in enumerate(reasoning, 1):
                        task_mgr.log(f"  {i}. {str(part)[:80]}...")
                elif isinstance(reasoning, str):
                    reasoning_parts = reasoning.split('；') if '；' in reasoning else reasoning.split(';')
                    for i, part in enumerate(reasoning_parts, 1):
                        task_mgr.log(f"  {i}. {part.strip()[:80]}...")
                else:
                    task_mgr.log(f"  {str(reasoning)[:200]}")
            else:
                task_mgr.log("  暂无推理过程")

            task_mgr.log("\n" + "=" * 70)
            risk_warning = second_ai_result.get('risk_warning', '本分析基于历史数据统计，不保证中奖，请理性购彩。')
            task_mgr.log(f"⚠️ {risk_warning}")
            task_mgr.log("=" * 70)

            stats_text = (
                f"数据量: {db_history.get('data_count', 0)} 条\n"
                f"最新期号: {current_issue}\n"
                f"预测期号: {second_ai_result.get('next_issue', '未知')}\n"
                f"报告UUID: {report_uuid[:8]}..." if report_uuid else ""
            )
            self.stats_content.config(text=stats_text, fg=COLORS['success'])

            os.makedirs('predictions', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'predictions/article_ai_prediction_{timestamp}.json'

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(second_ai_result, f, indent=2, ensure_ascii=False, default=str)

            task_mgr.log(f"\n✓ 预测结果已保存到: {filename}")

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n✓ AI智能分析流程全部完成（集成文章爬取）")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ AI分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_backtest(self, task_mgr):
        """
        历史回测：使用OptimizedP5Predictor在历史数据上执行滚动预测回测

        流程:
        1. 加载全部历史数据（至少100期）
        2. 从第50期开始，每期用前N期数据训练后预测下一期
        3. 统计Top-1/Top-3命中率、综合得分、完全猜中次数
        4. 生成回测报告文件

        输出: 回测统计指标、前10期详情、报告文件路径
        """
        try:
            task_mgr.log("正在执行历史回测...")
            task_mgr.progress(10, "初始化回测引擎")

            # 初始化预测器
            predictor = OptimizedP5Predictor()

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("正在加载历史数据...")
            task_mgr.progress(20, "加载数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if len(history_data) < 100:
                task_mgr.log(f"✗ 历史数据不足: 需要至少100期，实际{len(history_data)}期")
                task_mgr.progress(0, "数据不足")
                return

            task_mgr.log(f"✓ 历史数据加载完成: 共{len(history_data)}期")

            # 初始化回测引擎
            task_mgr.log("正在初始化回测引擎...")
            task_mgr.progress(30, "初始化引擎")

            backtest_engine = P5BacktestEngine(predictor, db)

            # 配置回测参数
            start_index = 50
            test_count = min(50, len(history_data) - start_index)

            task_mgr.log(f"回测配置: 起始位置={start_index}, 测试期数={test_count}")

            # 执行回测
            task_mgr.log("\n正在执行回测...")
            task_mgr.progress(40, "执行回测")

            backtest_result = backtest_engine.run_backtest(start_index, test_count)

            if backtest_result.get('status') != 'success':
                task_mgr.log(f"\n✗ 回测失败: {backtest_result.get('message', '未知错误')}")
                task_mgr.progress(0, "回测失败")
                return

            task_mgr.progress(80, "回测完成")

            # 输出回测结果
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("✓ 历史回测完成！")
            task_mgr.log("=" * 70)

            stats = backtest_result.get('overall_stats', {})

            task_mgr.log("\n【回测统计指标】")
            task_mgr.log(f"测试期数: {backtest_result.get('total_tested', 0)}")
            task_mgr.log(f"平均综合得分: {stats.get('avg_overall_score', 0):.2f}/100")
            task_mgr.log(f"平均Top-1命中: {stats.get('avg_top1_hits', 0):.2f}/5 位")
            task_mgr.log(f"平均Top-3命中: {stats.get('avg_top3_hits', 0):.2f}/5 位")
            task_mgr.log(f"Top-1命中率: {stats.get('avg_top1_hit_rate', 0):.2f}%")
            task_mgr.log(f"Top-3命中率: {stats.get('avg_top3_hit_rate', 0):.2f}%")
            task_mgr.log(f"概率校准得分: {stats.get('avg_calibration_score', 0):.2f}/100")
            task_mgr.log(f"完全猜中次数: {stats.get('full_match_count', 0)} 次")
            task_mgr.log(f"完全猜中率: {stats.get('full_match_rate', 0):.2f}%")

            # 输出详细结果（前10期）
            task_mgr.log("\n【前10期回测详情】")
            results = backtest_result.get('results', [])
            for i, result in enumerate(results[:10], 1):
                issue = result.get('issue', '')
                top1_hits = result.get('top1_hits', 0)
                top3_hits = result.get('top3_hits', 0)
                overall_score = result.get('overall_score', 0)
                task_mgr.log(f"{i}. 期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 综合得分{overall_score:.1f}")

            # 生成回测报告
            task_mgr.log("\n正在生成回测报告...")
            report_path = backtest_engine.generate_backtest_report(backtest_result)
            task_mgr.log(f"✓ 回测报告已保存到: {report_path}")

            # 更新统计面板
            stats_text = (
                f"回测期数: {backtest_result.get('total_tested', 0)}\n"
                f"Top-1命中率: {stats.get('avg_top1_hit_rate', 0):.2f}%\n"
                f"Top-3命中率: {stats.get('avg_top3_hit_rate', 0):.2f}%\n"
                f"综合得分: {stats.get('avg_overall_score', 0):.2f}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['accent_ai'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n✓ 历史回测流程全部完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 历史回测过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_feature_analysis(self, task_mgr):
        """
        特征分析：提取全部历史数据的统计特征

        提取的特征类型:
        - 频率特征: 各位置热号/温号/冷号
        - 012路特征: 各位置0路/1路/2路比例
        - 连号特征: 平均连号数、连号出现率
        - 重隔号特征: 重号率、隔号率、无重复率
        - 和值与跨度特征: 和值范围/平均值、跨度范围/平均值

        输出: 特征分析报告JSON文件（保存到 reports/features/）
        """
        try:
            task_mgr.log("正在执行特征分析...")
            task_mgr.progress(10, "初始化特征工程")

            # 初始化特征工程
            fe = P5FeatureEngineering()

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("正在加载历史数据...")
            task_mgr.progress(20, "加载数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if not history_data:
                task_mgr.log("✗ 数据库中没有历史数据")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.log(f"✓ 历史数据加载完成: 共{len(history_data)}期")

            # 提取所有特征
            task_mgr.log("\n正在提取特征...")
            task_mgr.progress(40, "提取频率特征")

            features = fe.extract_all_features(history_data)

            task_mgr.progress(70, "特征提取完成")

            # 输出特征分析结果
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("✓ 特征分析完成！")
            task_mgr.log("=" * 70)

            pos_names = ['万位', '千位', '百位', '十位', '个位']

            # 频率特征
            task_mgr.log("\n【频率特征】")
            freq_features = features.get('frequency', {})
            for pos_name in pos_names:
                pos_freq = freq_features.get(pos_name, {})
                hot_numbers = pos_freq.get('hot_numbers', [])
                cold_numbers = pos_freq.get('cold_numbers', [])
                warm_numbers = pos_freq.get('warm_numbers', [])
                task_mgr.log(f"{pos_name}: 热号={hot_numbers}, 温号={warm_numbers}, 冷号={cold_numbers}")

            # 012路特征
            task_mgr.log("\n【012路特征】")
            road_features = features.get('road_012', {})
            for pos_name in pos_names:
                pos_road = road_features.get(pos_name, {})
                road_ratios = pos_road.get('road_ratios', {})
                task_mgr.log(f"{pos_name}: 0路={road_ratios.get(0, 0):.2%}, 1路={road_ratios.get(1, 0):.2%}, 2路={road_ratios.get(2, 0):.2%}")

            # 连号特征
            task_mgr.log("\n【连号特征】")
            consecutive_features = features.get('consecutive', {})
            task_mgr.log(f"平均连号数: {consecutive_features.get('avg_consecutive_count', 0):.2f}")
            task_mgr.log(f"最大连号数: {consecutive_features.get('max_consecutive_count', 0)}")
            task_mgr.log(f"连号出现率: {consecutive_features.get('consecutive_rate', 0):.2%}")

            # 重隔号特征
            task_mgr.log("\n【重隔号特征】")
            repeat_features = features.get('repeat', {})
            task_mgr.log(f"重号率: {repeat_features.get('repeat_rate', 0):.2%}")
            task_mgr.log(f"隔号率: {repeat_features.get('skip_rate', 0):.2%}")
            task_mgr.log(f"无重复率: {repeat_features.get('no_repeat_rate', 0):.2%}")

            # 和值与跨度特征
            task_mgr.log("\n【和值与跨度特征】")
            sum_span_features = features.get('sum_span', {})
            task_mgr.log(f"和值范围: {sum_span_features.get('sum_range', [])}")
            task_mgr.log(f"平均和值: {sum_span_features.get('avg_sum', 0):.2f}")
            task_mgr.log(f"跨度范围: {sum_span_features.get('span_range', [])}")
            task_mgr.log(f"平均跨度: {sum_span_features.get('avg_span', 0):.2f}")

            # 保存特征分析结果
            os.makedirs('reports/features', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'reports/features/feature_analysis_{timestamp}.json'

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(features, f, indent=2, ensure_ascii=False, default=str)

            task_mgr.log(f"\n✓ 特征分析结果已保存到: {filename}")

            # 更新统计面板
            stats_text = (
                f"数据量: {len(history_data)} 条\n"
                f"连号率: {consecutive_features.get('consecutive_rate', 0):.1%}\n"
                f"重号率: {repeat_features.get('repeat_rate', 0):.1%}\n"
                f"平均和值: {sum_span_features.get('avg_sum', 0):.1f}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['warning'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n✓ 特征分析流程全部完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 特征分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")


def main():
    """启动排列5 AI智能分析系统GUI应用程序"""
    root = tk.Tk()
    app = LotteryGUI(root)

    def on_closing():
        """窗口关闭时优雅关闭线程池"""
        app.task_mgr.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
