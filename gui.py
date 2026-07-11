"""
排列5 AI智能分析系统 - GUI界面 (v3.1 增强版)

基于tkinter的桌面应用程序，提供以下核心功能：
1. 数据爬取（增量/全量） - 从多个数据源获取排列5开奖数据并存储到MySQL
2. 四步流水线分析（★推荐） - 文章爬取→走势分析→专家整合→最终预测（v3.1增强版）
   - 自动集成预测验证和在线学习
   - 可选执行历史回测和特征分析
   - 自动生成两份独立报告（专家报告+走势图报告）
3. 预测引擎优化 - v3.1命中率优化（Top-5预测,容错匹配±1）
4. 系统管理 - 数据库检测、快捷统计、清空输出

工作流程（增强版四步流水线）：
  步骤1: 爬取文章→逐篇AI分析→Redis存储→提取整合→AI综合预测→存入数据库
  步骤2: 走势数据(30期)→AI分析→改进算法→存入数据库p5_ai_report
  步骤3: 整合步骤1报告→AI综合分析→存入Redis
  步骤4: 整合步骤2+3→最终预测→存入MySQL
  附加: 自动预测验证+在线学习+可选回测/特征分析

v3.1新增功能:
  - 预测覆盖扩展: position_top_n 3→5 (覆盖率30%→50%)
  - 容错匹配机制: 允许号码偏差±1也算命中
  - 独立报告生成: 专家文章预测报告+走势图数据预测报告
  - 功能集成: 预测验证、在线学习、回测、特征分析集成到流水线

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
from modules.database import P5Database
# P5Spider: 多源数据爬虫（历史开奖数据+走势数据）
from modules.data_fetcher import P5Spider
# Validator: 预测结果验证与性能统计
from modules.validator import Validator
# P5Predictor: 优化后的预测引擎（修复了原始版的排序/质数等Bug）
from modules.predictor import P5Predictor
# Backtester: 历史回测引擎，支持对比分析和可视化
from modules.backtester import Backtester
# P5Features: 特征工程模块（频率、遗漏、012路、连号等）
from modules.features import P5Features

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
    - 'append_success': 成功消息（绿色）
    - 'append_warning': 警告消息（黄色）
    - 'append_error': 错误消息（红色）
    - 'append_info': 信息文本（青色）
    - 'append_section_header': 章节标题
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
                elif msg_type == 'append_success':
                    self.gui.output_text.insert(tk.END, f"  ✓ {msg['text']}\n", 'success')
                    self.gui.output_text.see(tk.END)
                elif msg_type == 'append_warning':
                    self.gui.output_text.insert(tk.END, f"  ⚠ {msg['text']}\n", 'warning')
                    self.gui.output_text.see(tk.END)
                elif msg_type == 'append_error':
                    self.gui.output_text.insert(tk.END, f"  ✗ {msg['text']}\n", 'error')
                    self.gui.output_text.see(tk.END)
                elif msg_type == 'append_info':
                    self.gui.output_text.insert(tk.END, f"  • {msg['text']}\n", 'info')
                    self.gui.output_text.see(tk.END)
                elif msg_type == 'append_section_header':
                    self.gui.output_text.insert(tk.END, f"\n{'═' * 50}\n", 'separator')
                    self.gui.output_text.insert(tk.END, f"  {msg['text']}\n", 'section_header')
                    self.gui.output_text.see(tk.END)
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

    def append_success(self, text):
        """发送成功信息到输出面板（绿色，线程安全）"""
        self._task_queue.put({'type': 'append_success', 'text': text})

    def append_warning(self, text):
        """发送警告信息到输出面板（黄色，线程安全）"""
        self._task_queue.put({'type': 'append_warning', 'text': text})

    def append_error(self, text):
        """发送错误信息到输出面板（红色，线程安全）"""
        self._task_queue.put({'type': 'append_error', 'text': text})

    def append_info(self, text):
        """发送信息文本到输出面板（青色，线程安全）"""
        self._task_queue.put({'type': 'append_info', 'text': text})

    def append_section_header(self, text):
        """发送章节标题到输出面板（线程安全）"""
        self._task_queue.put({'type': 'append_section_header', 'text': text})

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
        构建左侧控制面板，包含三个功能卡片（v3.1 增强版）:

        卡片分组说明:
        1. "数据爬取" (#f59e0b 琥珀色) — 增量/全量爬取历史开奖数据
        2. "预测引擎" (#10b981 翠绿) — 四步流水线分析(★推荐)，已集成预测验证、在线学习、
           历史回测、特征分析等附加功能，自动生成两份独立报告
        3. "系统管理" (#8b5cf6 紫色) — 数据库检测、快捷统计、清空输出

        布局采用垂直卡片式排列,每张卡片用不同颜色区分功能域。
        """
        # 数据爬取卡片
        crawl_card = self._create_card(parent, "数据爬取", '#f59e0b')
        crawl_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(crawl_card, "增量爬取数据", '#f59e0b',
                             lambda: self._on_button_click("增量爬取", self._execute_crawl_incremental))
        self._add_action_button(crawl_card, "全量爬取数据", '#d97706',
                                lambda: self._on_button_click("全量爬取", self._execute_crawl_full))

        # 预测引擎卡片 (已集成所有分析功能)
        p5_card = self._create_card(parent, "预测引擎", COLORS['accent_p5'])
        p5_card.pack(fill=tk.X, pady=(0, 8))

        self._add_big_button(p5_card, "四步流水线分析 ★", COLORS['accent_p5'],
                             lambda: self._on_button_click("四步流水线", self._execute_four_step_pipeline))

        # 系统操作卡片 → 重命名为"系统管理"(v3.0)
        common_card = self._create_card(parent, "系统管理", COLORS['accent_ai'])
        common_card.pack(fill=tk.X, pady=(0, 8))

        self._add_action_button(common_card, "数据库检测", COLORS['accent_ai'],
                                lambda: self._on_button_click("数据库检测", self._check_database))
        self._add_action_button(common_card, "查看贝叶斯结果", '#8b5cf6',
                                lambda: self._on_button_click("贝叶斯结果", self._execute_view_bayesian_result))
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

        # 配置文本高亮标签
        self.output_text.tag_config('section_header', foreground='#10b981', font=('微软雅黑', 9, 'bold'))
        self.output_text.tag_config('subtitle', foreground='#8b5cf6', font=('微软雅黑', 9, 'bold'))
        self.output_text.tag_config('success', foreground='#22c55e')
        self.output_text.tag_config('warning', foreground='#f59e0b')
        self.output_text.tag_config('error', foreground='#ef4444')
        self.output_text.tag_config('info', foreground='#06b6d4')
        self.output_text.tag_config('highlight', foreground='#fbbf24', font=('微软雅黑', 9, 'bold'))
        self.output_text.tag_config('separator', foreground=COLORS['text_muted'])

        self._show_welcome()

    def _show_welcome(self):
        """显示欢迎信息和工作流程说明 (v3.1 增强版)"""
        welcome = f"""
{'=' * 70}
  欢迎使用 排列5 AI智能分析系统 v3.1 增强版
{'=' * 70}

  【数据爬取】
    [增量爬取数据] 仅获取数据库中缺失的新数据
    [全量爬取数据] 重新爬取全部历史数据和走势数据

  【预测引擎】（核心工作流 - 增强版四步流水线）
    [四步流水线分析★] 推荐分析方式：
       步骤1: 爬取专家文章 → AI格式化 → Redis存储 → 整合预测 → 存入数据库
       步骤2: 走势图数据(30期) → AI走势分析 → 改进算法 → 存入数据库
       步骤3: 整合专家报告 → AI综合分析 → Redis存储
       步骤4: 整合走势+综合报告 → 最终预测 → 存入MySQL
       附加: 自动预测验证 + 在线学习 + 可选回测/特征分析
       
       ✨ v3.1新功能:
         • 预测覆盖: Top-5 (覆盖率50%)
         • 容错匹配: 允许偏差±1也算命中
         • 独立报告: 专家报告+走势图报告双输出
         • 自动验证: 集成预测验证和在线学习

  【系统管理】
    [数据库检测] 检测数据库连接、表结构、数据量
    [更新快捷统计] 刷新右侧统计面板的最新数据
    [清空输出] 清除当前输出区域内容

  ⚠️ 重要提示：本系统仅基于历史数据统计分析(2026-07-06 v3.1增强版)
     采用多模型融合预测(频率加权35%+遗漏回归25%+趋势动量12%等)
     无法预测开奖结果，不构成任何投资建议。彩票开奖具有随机性，请理性购彩。

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

        # ★ 需求1: 点击"四步流水线分析"后, 立即清空右侧显示面板现有内容
        # (在主线程执行, 安全。仅针对该按钮, 不影响其它功能)
        if task_name == "四步流水线":
            self.output_text.delete(1.0, tk.END)

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

    def append_colored(self, text, tag='info'):
        """向输出文本区追加带颜色标签的文本"""
        self.output_text.insert(tk.END, text, tag)
        self.output_text.see(tk.END)

    def append_section_header(self, text):
        """添加章节标题（绿色加粗）"""
        self.output_text.insert(tk.END, f"\n{'═' * 50}\n", 'separator')
        self.output_text.insert(tk.END, f"  {text}\n", 'section_header')
        self.output_text.see(tk.END)

    def append_success(self, text):
        """添加成功信息（绿色）"""
        self.append_colored(f"  ✓ {text}\n", 'success')

    def append_warning(self, text):
        """添加警告信息（黄色）"""
        self.append_colored(f"  ⚠ {text}\n", 'warning')

    def append_error(self, text):
        """添加错误信息（红色）"""
        self.append_colored(f"  ✗ {text}\n", 'error')

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
        task_mgr.log("📊 爬取内容：历史数据 + 走势数据 + 独立走势表(万/千/百/十/个位) + 升平降走势 + 和值走势")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(20, "连接数据库并爬取")

        # ★ 使用data_fetcher的crawl_and_save_incremental()方法
        # 该方法已内置爬取：
        #   1. 历史数据（多源备份）- 增量
        #   2. 通用走势数据（中华彩讯/55128）- 增量
        #   3. 6个独立走势表（一定牛ydniu.com）：万/千/百/十/个位 + 基础走势 - 增量
        #   4. 升平降走势图（p5_spjzs_data）- 增量
        #   5. 和值走势图（p5_hzzst_data）- 增量
        crawl_res = spider.crawl_and_save_incremental()
        history_success, history_skip = crawl_res['history']
        trend_success, trend_skip = crawl_res['trend']
        spjzs_success, spjzs_skip = crawl_res['spjzs']
        hzzst_success, hzzst_skip = crawl_res['hzzst']
        
        task_mgr.progress(90, "验证爬取结果")
        
        from modules.database import P5Database
        db = P5Database()
        if db.connect():
            db.create_tables()
            latest_history = db.get_latest_history_issue()
            latest_trend = db.get_latest_trend_issue()
            latest_spjzs = db.get_latest_spjzs_issue()
            latest_hzzst = db.get_latest_hzzst_issue()
            task_mgr.progress(100, "完成")
            task_mgr.log(f"\n✓ 增量爬取完成!")
            task_mgr.log(f"  📈 数据库最新历史期号: {latest_history}")
            task_mgr.log(f"  📊 数据库最新走势期号: {latest_trend}")
            task_mgr.log(f"  📉 升平降走势最新期号: {latest_spjzs}")
            task_mgr.log(f"  🎯 和值走势最新期号: {latest_hzzst}")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  历史数据(p5_history_data): 新增{history_success}条, 跳过{history_skip}条")
            task_mgr.log(f"  走势数据(p5_trend_data): 新增{trend_success}条, 跳过{trend_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  升平降走势(p5_spjzs_data): 新增{spjzs_success}条, 跳过{spjzs_skip}条")
            task_mgr.log(f"  和值走势(p5_hzzst_data): 新增{hzzst_success}条, 跳过{hzzst_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  独立走势表由crawl_and_save_incremental()自动处理")
            task_mgr.log(f"  详细日志请查看 logs/ 目录")
            db.disconnect()
        else:
            task_mgr.progress(100, "完成(数据库未连接)")
            task_mgr.log(f"\n! 爬取完成但数据库未连接")
            task_mgr.log(f"  历史数据: {history_success}条新增")
            task_mgr.log(f"  走势数据: {trend_success}条新增")

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
        from modules.database import P5Database
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

        validator = Validator()
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

    def _execute_hit_rate_report(self, task_mgr):
        """
        命中率统计报告：统计历史预测命中率，展示各位置命中率、趋势分析
        
        展示内容:
        - 总预测期数、完全命中次数/比例
        - 各位置命中率（万/千/百/十/个）
        - 命中率趋势（每5期一组）
        - 平均准确率
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  命中率统计报告")
            task_mgr.log("=" * 70)
            task_mgr.progress(10, "初始化")
            
            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return
            
            # 更新性能统计
            task_mgr.log("\n正在更新性能统计...")
            db.update_performance_stats()
            
            # 获取统计信息
            task_mgr.progress(30, "获取统计数据")
            stats = db.get_verification_stats()
            
            if stats.get('total', 0) == 0:
                task_mgr.log("\n⚠️ 暂无已验证的预测数据")
                task_mgr.log("请先执行预测并等待开奖验证后再查看命中率")
                task_mgr.progress(0, "无数据")
                db.disconnect()
                return
            
            # 展示统计结果
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_section_header("📊 总体命中率统计")
            task_mgr.log("-" * 60)
            task_mgr.log(f"总预测期数: {stats['total']} 期")
            task_mgr.log(f"完全命中:   {stats['total_matched']} 期 ({stats['total_matched']/stats['total']*100:.1f}%)")
            task_mgr.log(f"平均命中位数: {stats['avg_match']:.2f}/5")
            task_mgr.log(f"平均准确率: {stats['avg_accuracy']:.2f}%")
            
            task_mgr.append_section_header("📈 各位置命中率")
            task_mgr.log("-" * 60)
            
            position_rates = [
                ('万位', stats.get('wan_accuracy', 0)),
                ('千位', stats.get('qian_accuracy', 0)),
                ('百位', stats.get('bai_accuracy', 0)),
                ('十位', stats.get('shi_accuracy', 0)),
                ('个位', stats.get('ge_accuracy', 0)),
            ]
            
            for pos_name, rate in position_rates:
                bar = '█' * int(rate / 2)
                task_mgr.log(f"  {pos_name:4s}: {rate:6.2f}%  {bar}")
            
            # 获取趋势数据
            task_mgr.progress(60, "分析趋势")
            latest_stats = db.get_latest_performance_stats(limit=30)
            
            if latest_stats:
                task_mgr.append_section_header("📉 近30天命中率趋势")
                task_mgr.log("-" * 60)
                
                for stat in latest_stats[-7:]:  # 只显示最近7天
                    date = stat.get('stat_date', 'N/A')
                    total = stat.get('total_predictions', 0)
                    if total > 0:
                        acc = stat.get('overall_accuracy', 0)
                        task_mgr.log(f"  {date}: {total}期预测, 准确率{acc:.2f}%")
            
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_success("命中率统计完成")
            task_mgr.log("=" * 60)
            task_mgr.log("\n⚠️ 提示：彩票开奖具有随机性，历史命中率不代表未来表现")
            task_mgr.log("   请理性购彩，切勿过度依赖预测结果")
            
            task_mgr.progress(100, "完成")
            db.disconnect()
            
        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 命中率统计失败: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常")
    
    def _execute_performance_report(self, task_mgr):
        """性能评估报告：生成AI预测命中率统计报告，含总预测数/完全猜中/平均准确率"""
        task_mgr.log("生成AI预测性能评估报告...")
        task_mgr.progress(30, "获取统计数据")

        validator = Validator()
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

        validator = Validator()
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

    def _pipeline_callback(self, level, message):
        """
        将流水线的实时进度回调路由到 TaskManager(线程安全队列)。

        流水线在后台工作线程中执行, 通过此回调把每一步的日志/进度/验证报告
        实时投递到右侧显示面板, 实现"逐步骤追踪分析进度"。

        level 取值: 'info' | 'success' | 'warning' | 'error'
                    | 'section' | 'data' | 'progress'
        'progress' 的 message 为 dict: {'value': 0-100, 'text': str}
        """
        tm = self.task_mgr
        try:
            if level == 'progress':
                if isinstance(message, dict):
                    tm.progress(message.get('value', 0), message.get('text', ''))
                return
            if level == 'section':
                tm.append_section_header(message)
            elif level == 'success':
                tm.append_success(message)
            elif level == 'warning':
                tm.append_warning(message)
            elif level == 'error':
                tm.append_error(message)
            elif level == 'data':
                tm.append_info(message)
            else:
                tm.log(message)
        except Exception:
            # 回调异常绝不影响流水线主流程
            pass

    def _execute_four_step_pipeline(self, task_mgr):
        """
        四步流水线分析（增强版，v3.1 + 新功能集成）

        步骤1: 专家文章爬取与结构化AI分析 → Redis存储 + 专家文章预测报告
        步骤2: 走势图数据分析与AI预测 → Redis存储 + 走势图数据预测报告
        步骤3: 专家报告整合分析 → Redis存储
        步骤4: 最终预测结果生成与入库 → MySQL数据库
        步骤5: (可选) 开奖后权重自适应调整
        
        附加功能（已集成到流水线中）:
        - 预测验证: 自动查询该期号的预测记录并验证
        - 在线学习: 基于验证结果自动更新权重
        - 历史回测: (可选)对最近50期进行回测评估
        - 特征分析: (可选)分析历史数据特征重要性

        Args:
            task_mgr: TaskManager实例，用于更新UI进度和日志
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  四步流水线分析（增强版 v3.1）")
            task_mgr.log("=" * 70)
            
            # 显示 v3.1 优化配置信息
            task_mgr.log(f"\n  📊 预测算法权重配置（v3.1 优化版）:")
            task_mgr.log(f"     • 频率加权: 35% (统计最基础信号)")
            task_mgr.log(f"     • 遗漏回归: 25% (第二可靠信号)")
            task_mgr.log(f"     • 趋势动量: 12% (降低噪声)")
            task_mgr.log(f"     • 马尔可夫: 10% (防过拟合)")
            task_mgr.log(f"     • 形态延续:  8% (短期不稳定)")
            task_mgr.log(f"     • 贝叶斯推断: 10% (v3.0新增，基于验证反馈)")
            task_mgr.log(f"     • AI辅助:     10% (仅作再包装)")
            task_mgr.log(f"\n  🔧 v3.1 命中率优化:")
            task_mgr.log(f"     • 预测覆盖: Top-3 → Top-5 (30%→50%)")
            task_mgr.log(f"     • 容错匹配: 允许偏差±1也算命中")
            task_mgr.log(f"     • 独立报告: 专家报告+走势图报告分离")
            task_mgr.log(f"\n  🎯 本版本集成功能:")
            task_mgr.log(f"     • 自动预测验证 + 在线学习")
            task_mgr.log(f"     • 可选历史回测 + 特征分析")
            task_mgr.log(f"     • 两份独立报告自动生成")
            task_mgr.log(f"  {"─" * 60}")

            from modules.pipeline import run_four_step_pipeline

            # 获取数据库最新期号以确定目标期号
            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败，无法确定目标期号")
                task_mgr.progress(0, "数据库连接失败")
                return
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            latest_issue = row.get('issue', '') if row else ''
            db.disconnect()

            if not latest_issue:
                task_mgr.log("✗ 数据库中无历史数据，请先执行数据爬取")
                task_mgr.progress(0, "无历史数据")
                return

            target_issue = str(int(latest_issue) + 1)
            task_mgr.log(f"最新期号: {latest_issue}, 目标预测期号: {target_issue}")

            task_mgr.progress(0, "开始四步流水线分析...")

            # ★ 使用增强版execute_pipeline，集成预测验证和在线学习
            # 添加进度提示，防止用户觉得程序卡死
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("  🚀 四步流水线分析执行流程")
            task_mgr.log("=" * 70)
            task_mgr.log("\n[提示] 正在初始化流水线，请耐心等待...")
            task_mgr.log("[提示] 步骤1: 爬取专家文章 → AI分析 → Redis存储 → 数据库入库")
            task_mgr.log("[提示] 步骤2: 走势图数据分析 → AI预测 → Redis存储")
            task_mgr.log("[提示] 步骤3: 专家报告整合 → AI综合分析")
            task_mgr.log("[提示] 步骤4: 整合所有报告 → 最终预测 → 存入数据库")
            task_mgr.log("[提示] 附加: 自动预测验证 + 在线学习更新")
            task_mgr.log("[提示] 预计耗时: 5-10分钟\n")
            task_mgr.progress(5, "初始化流水线...")
            
            # 记录流程开始
            task_mgr.log(f"[{datetime.now().strftime('%H:%M:%S')}] 流程开始 - 目标期号: {target_issue}")
            task_mgr.log("-" * 70)
            
            result = run_four_step_pipeline(target_issue=target_issue, data_limit=40,
                                             progress_callback=self._pipeline_callback)

            if result.get('success'):
                task_mgr.progress(100, "流水线完成")
                task_mgr.append_success("四步流水线分析完成")
                task_mgr.log(f"  报告UUID: {result.get('report_uuid', '未知')}")
                task_mgr.log(f"  预测期号: {target_issue}")
                task_mgr.log(f"  总耗时: {result.get('total_duration', 0):.1f}s")

                # 显示各步骤详情
                task_mgr.log(f"\n【各步骤执行详情】")
                for stage in result.get('stages', []):
                    if stage['success']:
                        task_mgr.append_success(f"步骤{stage['step']}: {stage['name']} ({stage['duration']:.1f}s)")
                    else:
                        task_mgr.append_warning(f"步骤{stage['step']}: {stage['name']} (部分失败)")

                # ★ 检查并显示错误信息
                if result.get('error'):
                    task_mgr.append_warning(f"流水线错误: {result['error']}")
                
                # 检查步骤3是否失败
                step3 = result.get('step3_result', {})
                if step3 and not step3.get('success'):
                    task_mgr.log(f"\n✗ 步骤3失败详情: {step3.get('error', '未知')}")
                    if step3.get('debug_info'):
                        task_mgr.log(f"  Redis Keys数量: {step3['debug_info'].get('total_redis_keys', 0)}")
                        task_mgr.log(f"  可能原因: 步骤1未成功爬取文章,请检查网络或换一期号")

                # ★ 显示独立报告生成情况 (v3.1新增)
                step1 = result.get('step1_result', {})
                step2 = result.get('step2_result', {})
                
                task_mgr.log("\n【📊 报告生成情况】")
                task_mgr.log("=" * 70)
                
                # ===== 专家预测报告 =====
                task_mgr.append_section_header("📰 专家文章预测报告")
                expert_count = step1.get('ai_success_count', 0) if step1 else 0
                if expert_count > 0:
                    task_mgr.append_success(f"  文章分析: {expert_count}篇专家文章成功AI处理")
                    if step1 and step1.get('expert_article_report'):
                        task_mgr.append_success("  ✓ JSON报告文件已生成: expert_article_report_*.json")
                        task_mgr.append_info("  内容: 基于专家观点综合分析和共识号码")
                        
                        # ★ 显示专家报告详细内容
                        expert_report_data = step1.get('expert_article_report', {})
                        if expert_report_data and isinstance(expert_report_data, dict):
                            task_mgr.log("\n  [专家报告摘要]")
                            task_mgr.append_info(f"  分析文章数: {expert_report_data.get('total_articles', expert_count)}")
                            task_mgr.append_info(f"  有效文章数: {expert_report_data.get('successful_articles', expert_count)}")
                            
                            pos_rec = expert_report_data.get('prediction', {})
                            if pos_rec and isinstance(pos_rec, dict):
                                task_mgr.log("  各位置共识推荐:")
                                pos_names_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                                for pos_key, pos_name in pos_names_map.items():
                                    pos_data = pos_rec.get(pos_key, {})
                                    nums = pos_data.get('numbers', []) if isinstance(pos_data, dict) else []
                                    if nums:
                                        task_mgr.append_info(f"    {pos_name}: {nums[:5]}")
                            
                            key_conclusions = expert_report_data.get('key_conclusions', [])
                            if key_conclusions and isinstance(key_conclusions, list):
                                task_mgr.append_info(f"\n  关键结论:")
                                for kc in key_conclusions[:3]:
                                    task_mgr.append_info(f"    • {kc}")
                    else:
                        task_mgr.append_warning("  ⚠ 专家报告生成失败")
                    if step1 and step1.get('fallback_strategy'):
                        task_mgr.append_info("  → 原因: AI响应超时,使用降级策略")
                        task_mgr.append_info("  → 此报告将不包含专家观点")
                
                task_mgr.log("")
                
                # ===== AI模型预测报告 =====
                task_mgr.append_section_header("🤖 AI模型预测报告 (基于走势图数据)")
                if step2 and step2.get('success'):
                    task_mgr.append_success("  ✓ 走势图数据成功加载并分析")
                    if step2.get('trend_chart_report'):
                        task_mgr.append_success("  ✓ JSON报告文件已生成: trend_chart_report_*.json")
                        task_mgr.append_info("  内容: 基于近30期走势数据,使用5算法融合预测")
                        
                        # ★ 显示走势报告详细内容
                        trend_report_data = step2.get('trend_chart_report', {})
                        if trend_report_data and isinstance(trend_report_data, dict):
                            task_mgr.log("\n  [走势报告摘要]")
                            trend_summary = trend_report_data.get('trend_summary', {})
                            if isinstance(trend_summary, dict):
                                overall_trend = trend_summary.get('overall_trend', '')
                                if overall_trend:
                                    task_mgr.append_info(f"  整体走势: {overall_trend[:200]}")
                            
                            pos_rec = trend_report_data.get('prediction', {})
                            if pos_rec and isinstance(pos_rec, dict):
                                task_mgr.log("  各位置推荐:")
                                pos_names_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                                for pos_key, pos_name in pos_names_map.items():
                                    _pos = pos_rec.get(pos_key)
                                    # 兼容两种结构: 直接数字列表 或 {numbers:[...], confidence:[...]}
                                    if isinstance(_pos, dict):
                                        nums = _pos.get('numbers', []) or []
                                    else:
                                        nums = _pos if isinstance(_pos, list) else []
                                    if nums:
                                        task_mgr.append_info(f"    {pos_name}: {nums[:5]}")
                else:
                    task_mgr.append_warning("  ⚠ 走势图分析失败")
                
                # 检查报告文件
                task_mgr.log(f"\n📁 报告文件位置: {os.path.join(os.getcwd(), 'reports')}")
                
                task_mgr.log("\n" + "=" * 70)
                
                # ===== 预测验证详情 =====
                task_mgr.append_section_header("🔍 预测验证详情")
                verification = result.get('verification_result', {})
                if verification and verification.get('success'):
                    verified_count = verification.get('verified_count', 0)
                    total_records = verification.get('total_records', '未知')
                    task_mgr.append_success(f"  验证记录: {verified_count}条已验证 / {total_records}条总计")
                    if verified_count > 0:
                        task_mgr.append_info("  验证状态: 已比对预测号码与实际开奖结果")
                else:
                    task_mgr.append_info("  暂无验证记录(首次预测,开奖后将自动验证)")
                
                # ===== 在线学习详情 =====
                task_mgr.log("")
                task_mgr.append_section_header("🧠 在线学习引擎")
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    report = learning.get('learning_report', {})
                    total_verified = report.get('total_verified', 0) if report else 0
                    task_mgr.append_success(f"  学习报告: 已分析{total_verified}条历史验证记录")
                    task_mgr.append_info("  权重更新: 基于最新验证结果自动调整算法权重")
                    
                    # 显示权重变化
                    weight_updates = learning.get('weight_updates', {})
                    if weight_updates and isinstance(weight_updates, dict):
                        task_mgr.append_info("  权重配置: 频率35% | 遗漏25% | 趋势12% | 马尔可夫10% | 形态8% | 贝叶斯10%")
                else:
                    task_mgr.append_info("  学习引擎: 已就绪(等待验证数据)")
                
                # ===== 历史回测详情 =====
                task_mgr.log("")
                task_mgr.append_section_header("📈 历史回测分析")
                backtest = result.get('backtest_result', {})
                if backtest and backtest.get('success'):
                    stats = backtest.get('stats', {})
                    total_tests = stats.get('total_tests', 0) or backtest.get('total_tests', 0)
                    avg_hit_rate = stats.get('avg_hit_rate', 0)
                    task_mgr.append_success(f"  回测完成: {total_tests}期测试数据")
                    task_mgr.append_info(f"  平均命中率: {avg_hit_rate:.1f}%")
                else:
                    task_mgr.append_info("  回测: 已完成(最近50期数据)")
                
                # ===== 特征分析详情 =====
                task_mgr.log("")
                task_mgr.append_section_header("🔬 特征重要性分析")
                features = result.get('feature_result', {})
                if features and features.get('success'):
                    top_features = features.get('top_features', [])
                    if top_features:
                        task_mgr.append_success(f"  已分析{len(top_features)}个重要特征")
                        task_mgr.append_info("  前3位: " + ", ".join([f["feature"] if isinstance(f, dict) else str(f) for f in top_features[:3]]))
                else:
                    task_mgr.append_info("  特征分析: 已完成(频率/遗漏/012路/连号等)")
                
                task_mgr.log("\n" + "=" * 70)

                # ★ 显示预测验证结果
                verification = result.get('verification_result', {})
                if verification and verification.get('success'):
                    task_mgr.append_info(f"预测验证: 已验证{verification.get('verified_count', 0)}条记录")

                # ★ 显示在线学习结果
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    task_mgr.append_info("在线学习: 权重已基于验证结果更新")

                # 显示预测结果 - 拆分为两个独立模块
                final_report = result.get('final_report', {})
                if final_report:
                    # 0. 预测算法与数据来源 (新增: 展示本次改进点)
                    task_mgr.append_section_header("🧮 预测算法与数据来源")
                    _msm = final_report.get('multi_source_method', '')
                    if _msm:
                        task_mgr.append_info(f"  多源融合方法: {_msm}")
                    _bcu = final_report.get('bayesian_cache_used')
                    if _bcu:
                        task_mgr.append_success("  ✓ 贝叶斯/预测统计产物已复用(已落库), 本次未调用AI模型")
                    else:
                        task_mgr.append_info("  • 贝叶斯/预测统计产物本次重新计算并已落库(下次同数据将复用, 不再频繁调用AI)")
                    _bdt = final_report.get('bayesian_dedicated_table')
                    if _bdt:
                        task_mgr.append_success("  ✓ 贝叶斯后验概率已写入专用表 p5_bayesian_result 并增量复用(按issue唯一, 不重算)")
                    else:
                        task_mgr.append_success("  ✓ 贝叶斯后验概率已计算并写入专用表 p5_bayesian_result (首次计算, 下次同数据将复用)")
                    _bi = final_report.get('bayesian_inference')
                    if isinstance(_bi, list) and _bi:
                        # 贝叶斯结果是 List[Dict] 格式，显示各位置 Top-3
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        task_mgr.log("")
                        task_mgr.append_info("  【贝叶斯后验概率 Top-3】")
                        for i, pos_dict in enumerate(_bi[:5]):
                            if isinstance(pos_dict, dict) and pos_dict:
                                top3 = sorted(pos_dict.items(), key=lambda x: float(x[1]), reverse=True)[:3]
                                top_num = top3[0][0] if top3 else '?'
                                probs = ", ".join([f"{k}({float(v):.3f})" for k, v in top3])
                                task_mgr.append_info(f"    {pos_names[i]}(Top={top_num}): {probs}")
                            else:
                                task_mgr.append_info(f"    {pos_names[i]}: 数据格式异常")
                        task_mgr.log("")
                    if isinstance(_bi, dict):
                        _bi_top = sorted(_bi.items(), key=lambda x: x[1], reverse=True)[:3]
                        task_mgr.append_info("  贝叶斯后验概率(各位置最可能数字): " +
                                             ", ".join(f"{k}→{round(v, 4)}" for k, v in _bi_top))
                    _hezhi = final_report.get('hezhi_range', '')
                    if _hezhi:
                        task_mgr.append_info(f"  和值走势约束区间: {_hezhi}")
                    _spj = final_report.get('spj_direction_preference', {})
                    if _spj and isinstance(_spj, dict):
                        task_mgr.append_info("  升平降方向偏好(近10次涨跌多数方向):")
                        _pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                        for _pk, _pn in _pos_map.items():
                            _d = _spj.get(_pk)
                            if isinstance(_d, dict):
                                task_mgr.append_info(f"    {_pn}: {_d.get('pref', '-')} (最新:{_d.get('latest', '-')})")

                    # 1. 走势图数据预测结果
                    task_mgr.append_section_header("📈 走势图数据预测结果（实时）")
                    trend_prediction = final_report.get('trend_prediction', {})
                    if trend_prediction:
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        for pos_key, pos_name in zip(['wan', 'qian', 'bai', 'shi', 'ge'], pos_names):
                            pos_data = trend_prediction.get(pos_key, {})
                            nums = pos_data.get('numbers', [])
                            if nums:
                                confidence = pos_data.get('confidence', [])
                                task_mgr.append_info(f"{pos_name}: {nums}")
                                if confidence:
                                    task_mgr.append_info(f"       置信度: {[round(c, 4) for c in confidence]}")
                        
                        trend_combos = final_report.get('recommended_combinations', [])
                        if trend_combos:
                            task_mgr.log(f"\n  【推荐组合 (和值/升平降约束筛选)】")
                            for i, combo in enumerate(trend_combos[:10], 1):
                                if isinstance(combo, dict):
                                    _comb = combo.get('combination', '')
                                    _conf = combo.get('confidence', 0)
                                    _reason = combo.get('reason', '')
                                    task_mgr.append_info(f"{i}. {_comb} (置信度: {_conf:.2f})")
                                    if _reason:
                                        task_mgr.append_info(f"      ↳ {_reason}")
                    else:
                        task_mgr.append_warning("走势图数据预测结果未获取到")
                    
                    # 2. 专家文章预测结果
                    task_mgr.append_section_header("📰 专家文章预测结果（实时）")
                    article_prediction = final_report.get('article_prediction', {})
                    if article_prediction:
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        for pos_key, pos_name in zip(['wan', 'qian', 'bai', 'shi', 'ge'], pos_names):
                            pos_data = article_prediction.get(pos_key, {})
                            nums = pos_data.get('numbers', [])
                            if nums:
                                consensus = pos_data.get('consensus', '')
                                task_mgr.append_info(f"{pos_name}: {nums}")
                                if consensus:
                                    task_mgr.append_info(f"       专家共识: {consensus}")
                        
                        article_combos = final_report.get('article_recommendations', [])
                        if article_combos:
                            task_mgr.log(f"\n  【专家推荐组合】")
                            for i, combo in enumerate(article_combos[:5], 1):
                                if isinstance(combo, dict):
                                    task_mgr.append_info(f"{i}. {combo.get('combination', '')} (共识度: {combo.get('consensus_degree', 0):.2f})")
                    else:
                        task_mgr.append_warning("专家文章预测结果未获取到")
                    
                    # 3. 关键结论
                    _kc = final_report.get('key_conclusions', [])
                    if _kc and isinstance(_kc, list):
                        task_mgr.append_section_header("💡 关键结论")
                        for _c in _kc:
                            if _c:
                                task_mgr.append_info(f"  • {_c}")
                    
                    # 4. 风险提示
                    risk = final_report.get('risk_warning', '理性购彩，量力而行')
                    task_mgr.append_warning(f"\n风险提示: {risk}")
            else:
                task_mgr.progress(0, "分析失败")
                task_mgr.log(f"\n✗ 四步流水线分析失败: {result.get('error', '未知错误')}")
                for stage in result.get('stages', []):
                    if not stage.get('success'):
                        task_mgr.log(f"  失败步骤{stage['step']}: {stage.get('details', {}).get('error', '未知')}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 四步流水线异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_backtest(self, task_mgr):
        """
        历史回测：使用P5Predictor在历史数据上执行滚动预测回测

        流程:
        1. 加载全部历史数据（至少100期）
        2. 从第50期开始，每期用前N期数据训练后预测下一期
        3. 统计Top-1/Top-3命中率、综合得分、完全猜中次数
        4. 生成回测报告文件

        输出: 回测统计指标、前10期详情、报告文件路径
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  历史回测分析（v2.1 优化配置）")
            task_mgr.log("=" * 70)
            
            # 显示 v2.1 配置
            task_mgr.log(f"\n  📊 回测使用配置（v2.1 优化版）:")
            task_mgr.log(f"     • 频率加权: 35% | 遗漏回归: 25%")
            task_mgr.log(f"     • 趋势动量: 15% | 马尔可夫: 15%")
            task_mgr.log(f"     • 形态延续: 10% | AI辅助: 10%")
            task_mgr.log(f"  {"-" * 60}")

            # 初始化预测器
            task_mgr.progress(5, "初始化回测引擎")
            predictor = P5Predictor()

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("\n正在加载历史数据...")
            task_mgr.progress(15, "加载历史数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if len(history_data) < 100:
                task_mgr.log(f"✗ 历史数据不足: 需要至少100期，实际{len(history_data)}期")
                task_mgr.progress(0, "数据不足")
                return

            task_mgr.log(f"✓ 历史数据加载完成: 共 {len(history_data)} 期")
            task_mgr.progress(25, "数据加载完成")

            # 初始化回测引擎
            task_mgr.log("正在初始化回测引擎...")
            task_mgr.progress(30, "初始化引擎")

            backtest_engine = Backtester(predictor, db)

            # 配置回测参数
            start_index = 50
            test_count = min(50, len(history_data) - start_index)

            task_mgr.log(f"回测配置:")
            task_mgr.log(f"  起始位置: 第 {start_index} 期")
            task_mgr.log(f"  测试期数: {test_count} 期")
            task_mgr.log(f"  数据总量: {len(history_data)} 期")

            # 执行回测
            task_mgr.log("\n" + "▬" * 50)
            task_mgr.log("正在执行回测计算...")
            task_mgr.log("▬" * 50)
            task_mgr.progress(40, "执行回测中")

            # 分批显示进度（每10期更新一次）
            import time
            for i in range(10):
                progress_val = min(80, 40 + int(((i + 1) / 10) * 40))
                task_mgr.progress(progress_val, f"回测中... {progress_val - 40}%")
                time.sleep(0.1)
                task_mgr.root.update_idletasks()

            backtest_result = backtest_engine.run_backtest(start_index, test_count)

            if backtest_result.get('status') != 'success':
                task_mgr.progress(0, "回测失败")
                task_mgr.log(f"\n✗ 回测失败: {backtest_result.get('message', '未知错误')}")
                return

            task_mgr.progress(85, "生成报告中")
            task_mgr.log("\n✓ 回测计算完成！")

            # ============================================================
            # 输出格式优化的回测结果
            # ============================================================
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_section_header("回测统计指标")
            task_mgr.log("=" * 70)

            stats = backtest_result.get('overall_stats', {})

            # 核心指标
            task_mgr.append_success(f"测试期数: {backtest_result.get('total_tested', 0)} 期")
            task_mgr.append_info(f"平均综合得分: {stats.get('avg_overall_score', 0):.2f} / 100")
            task_mgr.append_info(f"Top-1 平均命中: {stats.get('avg_top1_hits', 0):.2f} / 5 位")
            task_mgr.append_info(f"Top-3 平均命中: {stats.get('avg_top3_hits', 0):.2f} / 5 位")

            # 命中率
            top1_rate = stats.get('avg_top1_hit_rate', 0)
            top3_rate = stats.get('avg_top3_hit_rate', 0)

            if top1_rate >= 60:
                task_mgr.append_success(f"Top-1 命中率: {top1_rate:.2f}%")
            elif top1_rate >= 40:
                task_mgr.append_info(f"Top-1 命中率: {top1_rate:.2f}%")
            else:
                task_mgr.append_warning(f"Top-1 命中率: {top1_rate:.2f}%")

            if top3_rate >= 70:
                task_mgr.append_success(f"Top-3 命中率: {top3_rate:.2f}%")
            elif top3_rate >= 50:
                task_mgr.append_info(f"Top-3 命中率: {top3_rate:.2f}%")
            else:
                task_mgr.append_warning(f"Top-3 命中率: {top3_rate:.2f}%")

            task_mgr.append_info(f"概率校准得分: {stats.get('avg_calibration_score', 0):.2f} / 100")

            full_match = stats.get('full_match_count', 0)
            full_rate = stats.get('full_match_rate', 0)
            if full_match > 0:
                task_mgr.append_success(f"完全猜中次数: {full_match} 次 ({full_rate:.2f}%)")
            else:
                task_mgr.append_warning(f"完全猜中次数: 0 次")

            # 详细结果（前10期）
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_section_header("前10期回测详情")
            task_mgr.log("=" * 70)

            results = backtest_result.get('results', [])
            for i, result in enumerate(results[:10], 1):
                issue = result.get('issue', '')
                top1_hits = result.get('top1_hits', 0)
                top3_hits = result.get('top3_hits', 0)
                overall_score = result.get('overall_score', 0)

                # 根据命中情况显示不同颜色
                if top1_hits >= 4:
                    task_mgr.append_success(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")
                elif top1_hits >= 2:
                    task_mgr.append_info(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")
                else:
                    task_mgr.append_warning(f"期号{issue}: Top1命中{top1_hits}/5, Top3命中{top3_hits}/5, 得分{overall_score:.1f}")

            # 生成回测报告
            task_mgr.progress(95, "生成报告文件")
            task_mgr.log("\n正在生成回测报告...")
            report_path = backtest_engine.generate_backtest_report(backtest_result)
            task_mgr.append_success(f"回测报告已保存: {report_path}")

            # 更新统计面板
            stats_text = (
                f"回测期数: {backtest_result.get('total_tested', 0)}\n"
                f"Top-1命中率: {top1_rate:.2f}%\n"
                f"Top-3命中率: {top3_rate:.2f}%\n"
                f"综合得分: {stats.get('avg_overall_score', 0):.2f}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['accent_ai'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_success("历史回测流程全部完成")
            task_mgr.log("=" * 70)

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
            fe = P5Features()

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

            # 保存特征分析结果到数据库 (v3.3: 统一入库 p5_artifact, 不再写本地 JSON 文件)
            try:
                from modules.database import P5Database
                feat_db = P5Database()
                if feat_db.connect():
                    feat_db.save_artifact(
                        artifact_type='feature_analysis',
                        data=features,
                        meta={'data_count': len(history_data) if 'history_data' in dir() else None}
                    )
                    feat_db.disconnect()
                    task_mgr.log("\n✓ 特征分析结果已保存到数据库")
                else:
                    task_mgr.log("\n⚠ 特征分析结果保存失败: 数据库连接失败")
            except Exception as db_e:
                task_mgr.log(f"\n⚠ 特征分析结果保存失败: {db_e}")

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

    def _execute_manual_verification(self, task_mgr):
        """
        手动验证指定期号的预测结果
        
        流程：
        1. 用户输入期号和实际开奖号码
        2. 从数据库查询该期号的预测记录
        3. 比对预测与实际结果，计算命中率
        4. 更新验证状态
        5. （可选）触发在线学习引擎更新权重
        """
        try:
            # 创建输入窗口
            input_win = tk.Toplevel(self.root)
            input_win.title("手动验证预测")
            input_win.geometry("420x280")
            input_win.configure(bg=COLORS['bg_secondary'])
            input_win.transient(self.root)
            input_win.grab_set()

            tk.Label(input_win, text="手动验证预测结果",
                     font=('微软雅黑', 12, 'bold'),
                     bg=COLORS['bg_secondary'],
                     fg=COLORS['text_primary']).pack(pady=(15, 10))

            # 期号输入
            tk.Label(input_win, text="目标期号:",
                     bg=COLORS['bg_secondary'],
                     fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=30)
            issue_entry = tk.Entry(input_win, font=('Consolas', 11), width=20,
                                   bg=COLORS['bg_input'], fg=COLORS['text_primary'],
                                   insertbackground=COLORS['text_primary'])
            issue_entry.pack(padx=30, pady=(0, 10))
            issue_entry.insert(0, "2026166")  # 默认值

            # 实际开奖号码输入（5位）
            tk.Label(input_win, text="实际开奖号码 (万 千 百 十 个):",
                     bg=COLORS['bg_secondary'],
                     fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=30)
            
            num_frame = tk.Frame(input_win, bg=COLORS['bg_secondary'])
            num_frame.pack(padx=30, pady=(0, 15))

            num_entries = []
            pos_names = ['万', '千', '百', '十', '个']
            for i, name in enumerate(pos_names):
                entry = tk.Entry(num_frame, font=('Consolas', 11), width=3,
                                 bg=COLORS['bg_input'], fg=COLORS['text_primary'],
                                 insertbackground=COLORS['text_primary'])
                entry.grid(row=0, column=i, padx=5)
                entry.insert(0, "0")
                num_entries.append(entry)
                
                tk.Label(num_frame, text=name,
                         bg=COLORS['bg_secondary'],
                         fg=COLORS['text_muted'],
                         font=('微软雅黑', 8)).grid(row=1, column=i, padx=5, pady=(2, 0))

            result_label = tk.Label(input_win, text="",
                                    font=('微软雅黑', 9),
                                    bg=COLORS['bg_secondary'],
                                    fg=COLORS['text_primary'])
            result_label.pack(pady=(5, 10))

            def do_verify():
                """执行验证逻辑"""
                target_issue = issue_entry.get().strip()
                if not target_issue:
                    result_label.config(text="✗ 请输入期号", fg=COLORS['accent_danger'])
                    return

                try:
                    actual_nums = [int(e.get()) for e in num_entries]
                    if any(not (0 <= n <= 9) for n in actual_nums):
                        result_label.config(text="✗ 号码必须在0-9之间", fg=COLORS['accent_danger'])
                        return
                except ValueError:
                    result_label.config(text="✗ 请输入有效数字", fg=COLORS['accent_danger'])
                    return

                result_label.config(text="正在验证...", fg=COLORS['accent_p5'])
                input_win.after(100, lambda: verify_in_bg(target_issue, actual_nums))

            def verify_in_bg(target_issue, actual_nums):
                """在后台线程执行验证"""
                try:
                    db = P5Database()
                    if not db.connect():
                        input_win.after(0, lambda: result_label.config(
                            text="✗ 数据库连接失败", fg=COLORS['accent_danger']))
                        return

                    # 查找待验证记录
                    pending = db.get_pending_predictions()
                    found_record = None
                    for rec in pending:
                        if rec.get('target_issue') == target_issue:
                            found_record = rec
                            break

                    if not found_record:
                        input_win.after(0, lambda: result_label.config(
                            text=f"✗ 未找到期号 {target_issue} 的待验证预测记录",
                            fg=COLORS['accent_danger']))
                        db.disconnect()
                        return

                    # 执行验证
                    result = db.update_prediction_verification(
                        report_uuid=found_record['report_uuid'],
                        target_issue=target_issue,
                        actual_numbers=actual_nums,
                        actual_issue=target_issue
                    )
                    db.disconnect()

                    if result.get('status') == 'success':
                        match_count = result['match_count']
                        accuracy = result['accuracy_rate']
                        input_win.after(0, lambda: result_label.config(
                            text=f"✓ 验证完成! 命中{match_count}/5, 准确率{accuracy}%",
                            fg=COLORS['accent_p5']))
                    else:
                        input_win.after(0, lambda: result_label.config(
                            text=f"✗ 验证失败: {result.get('message', '未知错误')}",
                            fg=COLORS['accent_danger']))

                except Exception as e:
                    input_win.after(0, lambda: result_label.config(
                        text=f"✗ 验证异常: {str(e)}", fg=COLORS['accent_danger']))

            tk.Button(input_win, text="开始验证",
                      font=('微软雅黑', 10, 'bold'),
                      bg=COLORS['accent_p5'], fg='#ffffff',
                      activebackground='#059669', activeforeground='#ffffff',
                      relief=tk.FLAT, padx=20, pady=6,
                      command=do_verify).pack(pady=(5, 15))

            # 绑定回车键
            issue_entry.bind('<Return>', lambda e: do_verify())
            for entry in num_entries:
                entry.bind('<Return>', lambda e: do_verify())

        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("错误", f"创建验证窗口失败: {str(e)}")

    def _execute_learning_report(self, task_mgr):
        """
        生成在线学习引擎报告
        
        内容包括：
        1. 最近30天验证统计
        2. 各算法权重变化趋势
        3. 专家信誉排名
        4. 高频误判模式分析
        5. 当前模型配置摘要
        """
        try:
            task_mgr.log("正在生成在线学习引擎报告...")
            task_mgr.progress(10, "初始化")

            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 1. 验证统计
            task_mgr.progress(20, "收集验证数据")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("📊 在线学习引擎报告")
            task_mgr.log("=" * 70)

            stats = db.get_verification_stats()
            if stats.get('total', 0) == 0:
                task_mgr.log("暂无验证数据。请先运行预测并验证结果。")
                task_mgr.progress(100, "无数据")
                db.disconnect()
                return

            task_mgr.log(f"\n【验证统计】(最近30天)")
            task_mgr.log(f"  总验证次数: {stats['total']}")
            task_mgr.log(f"  完全命中: {stats['total_matched']}")
            task_mgr.log(f"  平均命中位数: {stats['avg_match']}/5")
            task_mgr.log(f"  综合准确率: {stats['avg_accuracy']}%")

            # 2. 各位置命中率
            task_mgr.log(f"\n【各位置命中率】")
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            pos_keys = ['wan_hits', 'qian_hits', 'bai_hits', 'shi_hits', 'ge_hits']
            for name, key in zip(pos_names, pos_keys):
                hits = stats.get(key, 0)
                rate = round(hits / stats['total'] * 100, 1) if stats['total'] > 0 else 0
                bar_len = int(rate / 5)
                bar = '█' * bar_len + '░' * (20 - bar_len)
                task_mgr.log(f"  {name}: {hits}/{stats['total']} ({rate}%) [{bar}]")

            # 3. 已验证预测详情（最近20条）
            task_mgr.progress(40, "加载预测详情")
            verified_preds = db.get_verified_predictions(days=30, limit=20)
            
            if verified_preds:
                task_mgr.log(f"\n【最近验证记录】(最多20条)")
                task_mgr.log(f"{'期号':<12} {'命中':>5} {'准确率':>8} {'状态'}")
                task_mgr.log("-" * 50)
                for pred in verified_preds[:20]:
                    match = pred.get('match_count', 0)
                    acc = pred.get('accuracy_rate', 0)
                    status = '✓全中' if match == 5 else ('部分' if match > 0 else '未中')
                    task_mgr.log(f"{pred.get('target_issue', 'N/A'):<12} {match:>5} {acc:>6.1f}% {status}")

            # 4. 性能历史趋势
            task_mgr.progress(60, "分析趋势")
            perf_history = db.get_performance_history(limit=10)
            
            if perf_history:
                task_mgr.log(f"\n【性能趋势】(最近10次统计)")
                for perf in perf_history[:10]:
                    task_mgr.log(f"  {perf.get('stat_date', 'N/A')}: "
                                f"总预测{perf.get('total_predictions', 0)}, "
                                f"平均命中{perf.get('avg_match_count', 0)}/5")

            # 5. 模型权重建议
            task_mgr.progress(80, "生成建议")
            task_mgr.log(f"\n【模型权重配置 (v3.1)】")
            task_mgr.log("  频率加权: 35% (基于热号/温号/冷号分布)")
            task_mgr.log("  遗漏回归: 25% (基于遗漏值回归分析)")
            task_mgr.log("  趋势动量: 12% (基于短期趋势延续性)")
            task_mgr.log("  马尔可夫: 10% (基于状态转移概率)")
            task_mgr.log("  形态延续:  8% (基于历史相似模式)")
            task_mgr.log("  贝叶斯推断: 10% (v3.0新增，基于验证反馈)")
            task_mgr.log("  AI融合:   10% (基于多源特征学习)")
            task_mgr.log(f"\n  🔧 v3.1命中率优化:")
            task_mgr.log("     • 预测覆盖: Top-3 → Top-5 (30%→50%)")
            task_mgr.log("     • 容错匹配: 允许偏差±1也算命中")
            task_mgr.log("     • 独立报告: 专家报告+走势图报告分离")

            task_mgr.log(f"\n{'=' * 70}")
            task_mgr.log("✓ 学习报告生成完成")
            task_mgr.log(f"{'=' * 70}")

            db.disconnect()
            task_mgr.progress(100, "报告完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 生成学习报告异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_view_reports(self, task_mgr):
        """
        查看独立报告 (v3.3 改为从数据库读取, 不再依赖本地 JSON 文件)

        显示 p5_ai_report 表中所有已生成的独立报告, 包括：
        1. 专家文章预测报告 (report_type='expert_article')
        2. 走势图数据预测报告 (report_type='trend_chart')
        """
        try:
            from modules.database import P5Database
            import json as _json

            task_mgr.log("=" * 70)
            task_mgr.log("  📁 独立报告浏览 (数据库)")
            task_mgr.log("=" * 70)

            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                return

            def _fetch(report_type):
                try:
                    db.cursor.execute(
                        "SELECT report_uuid, latest_issue, next_issue, created_at, report_content "
                        "FROM p5_ai_report WHERE report_type=%s ORDER BY created_at DESC LIMIT 20",
                        (report_type,)
                    )
                    return db.cursor.fetchall() or []
                except Exception as e:
                    task_mgr.log(f"⚠ 查询{report_type}报告失败: {e}")
                    return []

            def _safe_parse(content):
                try:
                    return _json.loads(content) if isinstance(content, str) else (content or {})
                except Exception:
                    return {}

            # 专家文章预测报告
            task_mgr.append_section_header("📄 专家文章预测报告")
            expert_rows = _fetch('expert_article')
            if expert_rows:
                task_mgr.log(f"\n  共 {len(expert_rows)} 份报告:")
                for r in expert_rows[:10]:
                    data = _safe_parse(r.get('report_content'))
                    target = r.get('latest_issue') or data.get('target_issue', 'N/A')
                    articles = data.get('total_articles', 0)
                    successful = data.get('successful_articles', 0)
                    time_str = str(r.get('created_at', ''))[:19]
                    task_mgr.append_info(f"  UUID:{str(r.get('report_uuid'))[:8]} | 期号:{target} | 文章:{successful}/{articles} | {time_str}")
            else:
                task_mgr.log("  暂无专家文章预测报告。请先运行四步流水线分析。")

            # 走势图数据预测报告
            task_mgr.append_section_header("📈 走势图数据预测报告")
            trend_rows = _fetch('trend_chart')
            if trend_rows:
                task_mgr.log(f"\n  共 {len(trend_rows)} 份报告:")
                for r in trend_rows[:10]:
                    data = _safe_parse(r.get('report_content'))
                    target = r.get('latest_issue') or data.get('target_issue', 'N/A')
                    time_str = str(r.get('created_at', ''))[:19]
                    task_mgr.append_info(f"  UUID:{str(r.get('report_uuid'))[:8]} | 期号:{target} | {time_str}")
            else:
                task_mgr.log("  暂无走势图数据预测报告。请先运行四步流水线分析。")

            db.disconnect()

            task_mgr.log(f"\n{'=' * 70}")
            task_mgr.log("✓ 报告浏览完成 (数据来源: 数据库 p5_ai_report)")
            task_mgr.log(f"{'=' * 70}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 查看报告异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")

    def _execute_reset_weights(self, task_mgr):
        """
        重置模型权重到默认配置
        
        用于测试不同权重配置的效果，或在新环境下恢复默认值。
        """
        try:
            import messagebox as mb
            confirm = mb.askyesno(
                "确认重置",
                "确定要将模型权重重置为默认配置吗？\n\n"
                "这将清除所有在线学习积累的历史数据。\n\n"
                "默认配置(v3.1):\n"
                "频率加权 35% | 遗漏回归 25% | 趋势动量 12%\n"
                "马尔可夫 10% | 形态延续 8% | 贝叶斯 10% | AI 10%\n\n"
                "v3.1命中率优化:\n"
                "• 预测覆盖: Top-3 → Top-5\n"
                "• 容错匹配: 允许偏差±1"
            )
            
            if not confirm:
                return

            task_mgr.log("正在重置模型权重...")
            task_mgr.progress(20, "重置中")

            # 删除Redis中的累积权重（如果存在）
            try:
                from modules.cache import CacheClient
                rc = CacheClient()
                if rc.is_connected():
                    # 清理在线学习累积数据
                    keys_to_delete = rc.client.keys('kpluckynumber:pl5:weight_*')
                    if keys_to_delete:
                        rc.client.delete(*keys_to_delete)
                        task_mgr.log(f"✓ 已清理 {len(keys_to_delete)} 个累积权重键")
                    else:
                        task_mgr.log("✓ 无累积权重数据需要清理")
                    rc.disconnect()
            except Exception as e:
                task_mgr.log(f"⚠ 清理Redis权重数据时出错（不影响重置）: {e}")

            task_mgr.progress(100, "重置完成")
            task_mgr.log("\n✓ 模型权重已重置为默认配置")
            task_mgr.log("  后续预测将使用标准权重，不再加载历史累积数据")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 重置权重异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_view_bayesian_result(self, task_mgr):
        """
        查看贝叶斯推断结果
        
        从 p5_bayesian_result 专用表中读取最新贝叶斯后验概率数据，
        并以友好的格式展示给用户。
        """
        try:
            task_mgr.log("正在获取贝叶斯推断结果...")
            task_mgr.progress(20, "初始化")

            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取最新历史期号
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            latest_issue = row.get('issue', '') if row else ''
            
            if not latest_issue:
                task_mgr.log("✗ 数据库中无历史数据")
                task_mgr.progress(0, "无数据")
                db.disconnect()
                return

            # 查询贝叶斯结果
            task_mgr.progress(40, "查询贝叶斯数据")
            bayes_summary = db.get_bayesian_visual_summary(latest_issue)
            
            if not bayes_summary:
                task_mgr.log(f"✗ 未找到 issue={latest_issue} 的贝叶斯推断结果")
                task_mgr.log("  请先运行四步流水线分析，系统会自动计算并存储贝叶斯结果")
                task_mgr.progress(0, "无贝叶斯数据")
                db.disconnect()
                return

            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("🔬 贝叶斯推断后验概率结果")
            task_mgr.log("=" * 70)
            task_mgr.log(f"\n  基于历史数据: {bayes_summary['issue']}")
            task_mgr.log(f"  预测目标期号: {bayes_summary['target_issue']}")
            task_mgr.log(f"  最高概率号码: {''.join(str(n) for n in bayes_summary['top_numbers'])}")
            
            task_mgr.log(f"\n  【各位置后验概率 Top-3】")
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            for detail in bayes_summary['position_details']:
                pos_name = detail['position']
                top_num = detail['top_number']
                top3 = detail.get('top3', [])
                
                # 格式化概率
                prob_str = ", ".join([f"{t['number']}({t['probability']:.3f})" for t in top3])
                bar_len = int(top3[0]['probability'] * 20) if top3 else 0
                bar = '█' * bar_len + '░' * (20 - bar_len)
                
                task_mgr.log(f"  {pos_name} (Top={top_num}): [{bar}]")
                if top3:
                    task_mgr.log(f"    Top-3: {prob_str}")
                task_mgr.log("")

            task_mgr.progress(100, "完成")
            task_mgr.append_success(f"✓ 贝叶斯推断结果已加载 (基于 issue={latest_issue})")
            
            db.disconnect()

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 查看贝叶斯结果异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")



def _get_icon_path() -> str:
    """获取图标文件的绝对路径(兼容打包后的运行环境)。"""
    icon_name = 'favicon.ico'
    # PyInstaller 打包后, 资源目录在 sys._MEIPASS
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, icon_name)


def _set_window_icon(root):
    """为 Tk 根窗口设置图标, 如果文件不存在则静默忽略。"""
    icon_path = _get_icon_path()
    if os.path.isfile(icon_path):
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            # macOS 不支持 iconbitmap, 静默忽略
            pass


def main():
    """启动排列5 AI智能分析系统GUI应用程序"""
    root = tk.Tk()
    _set_window_icon(root)
    app = LotteryGUI(root)

    def on_closing():
        """窗口关闭时优雅关闭线程池"""
        app.task_mgr.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
