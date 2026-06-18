"""彩票数字概率统计分析系统 - GUI界面 (重构版)

核心架构改进:
1. 业务逻辑完全隔离到独立线程，UI主线程永不阻塞
2. 使用 queue.Queue 实现线程间通信，替代 sys.stdout 重定向
3. 所有UI更新通过 root.after() 调度到主线程执行
4. 任务状态机管理，防止并发冲突和状态卡死
5. 异常捕获和自动恢复机制
"""

import sys
import os
import threading
import queue
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# 前置检测：确保tkinter可用
# ============================================================
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("=" * 60)
    print("  [错误] 缺少 tkinter 模块！")
    print("  Python环境未包含tkinter，无法启动GUI。")
    print()
    print("  解决方案：")
    print("  1. 双击 start_gui.bat 启动（使用Anaconda Python）")
    print("  2. 或安装带tkinter的Python环境")
    print("=" * 60)
    input("\n  按回车键退出...")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.spider import QXCSpider
from modules.data_cleaner import DataCleaner
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from modules.report_generator import ReportGenerator
from modules.head4_analyzer import Head4Analyzer

from modules.spider_p5 import P5Spider
from modules.database_p5 import P5Database
from modules.analyzer_p5 import P5Analyzer
from modules.report_generator_p5 import P5ReportGenerator
from modules.p5_head4_analyzer import P5Head4Analyzer

# ============================================================
# 配色方案 - 现代化深色主题
# ============================================================
COLORS = {
    'bg_primary': '#0f172a',
    'bg_secondary': '#1e293b',
    'bg_card': '#334155',
    'bg_input': '#1e1e2e',
    'accent_qxc': '#3b82f6',
    'accent_p5': '#10b981',
    'accent_common': '#8b5cf6',
    'accent_danger': '#ef4444',
    'text_primary': '#f1f5f9',
    'text_secondary': '#94a3b8',
    'text_muted': '#64748b',
    'border': '#475569',
    'success': '#22c55e',
    'warning': '#f59e0b',
}


class TaskManager:
    """任务管理器 - 完全隔离业务线程与UI线程"""

    def __init__(self, gui_instance):
        self.gui = gui_instance
        self._task_queue = queue.Queue()
        self._running = False
        self._current_future = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._cancelled = False

        # 启动UI更新轮询
        self._poll_ui_updates()

    def _poll_ui_updates(self):
        """轮询任务队列，将业务线程的输出更新到UI"""
        try:
            while True:
                msg = self._task_queue.get_nowait()
                msg_type = msg.get('type', 'log')

                if msg_type == 'log':
                    self.gui._append_log(msg['text'])
                elif msg_type == 'progress':
                    self.gui._update_progress_ui(
                        msg.get('value', 0),
                        msg.get('text', '')
                    )
                elif msg_type == 'status':
                    self.gui._update_status_ui(
                        msg.get('text', ''),
                        msg.get('color', COLORS['text_muted'])
                    )
                elif msg_type == 'finished':
                    self._on_task_finished()
                elif msg_type == 'error':
                    self._on_task_error(msg.get('error', '未知错误'))
                elif msg_type == 'stats':
                    self.gui._update_stats_ui(msg.get('text', ''))
        except queue.Empty:
            pass

        # 每50ms轮询一次，保证UI响应性
        self.gui.root.after(50, self._poll_ui_updates)

    def log(self, text):
        """线程安全日志输出"""
        self._task_queue.put({'type': 'log', 'text': text})

    def progress(self, value, text=""):
        """线程安全进度更新"""
        self._task_queue.put({'type': 'progress', 'value': value, 'text': text})

    def status(self, text, color=COLORS['text_muted']):
        """线程安全状态更新"""
        self._task_queue.put({'type': 'status', 'text': text, 'color': color})

    def stats(self, text):
        """线程安全统计更新"""
        self._task_queue.put({'type': 'stats', 'text': text})

    def finished(self):
        """标记任务完成"""
        self._task_queue.put({'type': 'finished'})

    def error(self, err_text):
        """标记任务出错"""
        self._task_queue.put({'type': 'error', 'error': err_text})

    def is_running(self):
        with self._lock:
            return self._running

    def submit(self, task_func, task_name="任务"):
        """提交任务到线程池执行"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancelled = False

        # UI立即反馈 - 在主线程执行
        self.gui._on_task_started(task_name)

        # 在线程池中执行业务逻辑
        self._current_future = self._executor.submit(self._task_wrapper, task_func)
        return True

    def _task_wrapper(self, task_func):
        """任务包装器 - 捕获所有异常"""
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
        """任务完成回调（在主线程执行）"""
        with self._lock:
            self._running = False
        self.gui._on_task_finished()

    def _on_task_error(self, error_msg):
        """任务错误回调（在主线程执行）"""
        with self._lock:
            self._running = False
        self.gui._on_task_error(error_msg)

    def cancel(self):
        """取消当前任务"""
        self._cancelled = True
        with self._lock:
            self._running = False

    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=False)


class LotteryGUI:
    """彩票数据分析GUI主界面 - 完全线程安全重构版"""

    def __init__(self, root):
        self.root = root
        self.root.title("彩票数字概率统计分析系统")
        self.root.geometry("1300x850")
        self.root.minsize(1000, 700)
        self.root.configure(bg=COLORS['bg_primary'])

        # 任务管理器
        self.task_mgr = TaskManager(self)

        # UI状态
        self._buttons = []
        self._current_task_name = ""

        self._setup_window_style()
        self._build_ui()

    def _setup_window_style(self):
        """配置窗口样式"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('微软雅黑', 10),
                        background=COLORS['bg_primary'],
                        foreground=COLORS['text_primary'],
                        fieldbackground=COLORS['bg_secondary'])
        style.configure('TFrame', background=COLORS['bg_primary'])
        style.configure('Horizontal.TProgressbar',
                        background=COLORS['accent_qxc'],
                        troughcolor=COLORS['bg_card'],
                        borderwidth=0)

    # ============================================================
    # UI构建
    # ============================================================

    def _build_ui(self):
        """构建现代化用户界面"""
        main_container = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_container.pack(fill=tk.BOTH, expand=True)

        self._build_header(main_container)
        self._build_content(main_container)
        self._build_status_bar(main_container)

    def _build_header(self, parent):
        """构建顶部标题栏"""
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLORS['bg_secondary'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=15)

        icon = tk.Canvas(left, width=36, height=36, bg=COLORS['bg_secondary'],
                         highlightthickness=0)
        icon.pack(side=tk.LEFT, pady=12)
        icon.create_rectangle(2, 2, 16, 34, fill=COLORS['accent_qxc'], outline='', width=0)
        icon.create_rectangle(20, 2, 34, 34, fill=COLORS['accent_p5'], outline='', width=0)

        title_box = tk.Frame(left, bg=COLORS['bg_secondary'])
        title_box.pack(side=tk.LEFT, padx=(10, 0), pady=8)

        tk.Label(title_box, text="彩票数字概率统计分析系统",
                 font=('微软雅黑', 14, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        tk.Label(title_box, text="七星彩 & 排列5 智能数据分析平台",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(anchor=tk.W)

        self.time_label = tk.Label(header, text="",
                                   font=('Consolas', 10),
                                   bg=COLORS['bg_secondary'],
                                   fg=COLORS['text_secondary'])
        self.time_label.pack(side=tk.RIGHT, padx=15, pady=18)
        self._update_time()

    def _update_time(self):
        """更新时间显示"""
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.root.after(1000, self._update_time)

    def _build_content(self, parent):
        """构建主内容区"""
        content = tk.Frame(parent, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        left = tk.Frame(content, bg=COLORS['bg_primary'], width=320)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        self._build_control_panel(left)

        right = tk.Frame(content, bg=COLORS['bg_primary'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_output_panel(right)

    def _build_control_panel(self, parent):
        """构建左侧控制面板"""
        # 七星彩执行卡片
        qxc_card = self._create_card(parent, "七星彩", COLORS['accent_qxc'])
        qxc_card.pack(fill=tk.X, pady=(0, 10))
        self._add_big_button(qxc_card, "执行全部流程", COLORS['accent_qxc'],
                             lambda: self._on_button_click("七星彩全部流程", self._execute_qxc_all))

        # 排列5执行卡片
        p5_card = self._create_card(parent, "排列5", COLORS['accent_p5'])
        p5_card.pack(fill=tk.X, pady=(0, 10))
        self._add_big_button(p5_card, "执行全部流程", COLORS['accent_p5'],
                             lambda: self._on_button_click("排列5全部流程", self._execute_p5_all))

        # 通用操作卡片
        common_card = self._create_card(parent, "通用操作", COLORS['accent_common'])
        common_card.pack(fill=tk.X, pady=(0, 10))

        self._add_action_button(common_card, "数据概览", COLORS['accent_common'],
                                lambda: self._on_button_click("数据概览", self._view_data_summary))
        self._add_action_button(common_card, "数据库检测", COLORS['accent_common'],
                                lambda: self._on_button_click("数据库检测", self._check_database))
        self._add_action_button(common_card, "清空输出", COLORS['accent_danger'],
                                self._clear_output)

        # 进度指示器
        progress_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                                 highlightbackground=COLORS['border'],
                                 highlightthickness=1)
        progress_card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(progress_card, text="任务进度",
                 font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=12, pady=(10, 5))

        self.progress = ttk.Progressbar(progress_card, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, padx=12, pady=(0, 5))

        self.progress_label = tk.Label(progress_card, text="0%",
                                       font=('Consolas', 12, 'bold'),
                                       bg=COLORS['bg_secondary'],
                                       fg=COLORS['accent_qxc'])
        self.progress_label.pack(anchor=tk.CENTER, pady=(0, 5))

        self.task_status_label = tk.Label(progress_card, text="就绪",
                                          font=('微软雅黑', 9),
                                          bg=COLORS['bg_secondary'],
                                          fg=COLORS['text_muted'])
        self.task_status_label.pack(anchor=tk.W, padx=12, pady=(0, 10))

        # 快捷统计面板
        stats_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                              highlightbackground=COLORS['border'],
                              highlightthickness=1)
        stats_card.pack(fill=tk.X)

        tk.Label(stats_card, text="快捷统计",
                 font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=12, pady=(10, 5))

        self.stats_content = tk.Label(stats_card,
                                      text="点击「数据概览」查看详细统计",
                                      font=('微软雅黑', 9),
                                      bg=COLORS['bg_secondary'],
                                      fg=COLORS['text_muted'],
                                      justify=tk.LEFT)
        self.stats_content.pack(anchor=tk.W, padx=12, pady=(0, 10))

    def _create_card(self, parent, title, accent_color):
        """创建带标题的卡片容器"""
        card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'],
                        highlightthickness=1)

        top_bar = tk.Frame(card, bg=accent_color, height=3)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        title_frame = tk.Frame(card, bg=COLORS['bg_secondary'])
        title_frame.pack(fill=tk.X, padx=12, pady=(10, 8))

        dot = tk.Canvas(title_frame, width=10, height=10,
                        bg=COLORS['bg_secondary'], highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot.create_oval(1, 1, 9, 9, fill=accent_color, outline='')

        tk.Label(title_frame, text=title,
                 font=('微软雅黑', 12, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(side=tk.LEFT)

        return card

    def _add_big_button(self, parent, text, color, command):
        """添加大号执行按钮"""
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 10))

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 12, 'bold'),
                        bg=color,
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=15, pady=12)
        btn.pack(fill=tk.X)

        light_color = self._lighten_color(color, 1.15)
        btn.bind('<Enter>', lambda e: btn.config(bg=light_color))
        btn.bind('<Leave>', lambda e: btn.config(bg=color))

        self._buttons.append(btn)
        return btn

    def _add_action_button(self, parent, text, color, command):
        """添加普通操作按钮"""
        btn_frame = tk.Frame(parent, bg=COLORS['bg_secondary'])
        btn_frame.pack(fill=tk.X, padx=12, pady=3)

        btn = tk.Button(btn_frame, text=text,
                        font=('微软雅黑', 10),
                        bg=COLORS['bg_card'],
                        fg=COLORS['text_primary'],
                        activebackground=color,
                        activeforeground=COLORS['text_primary'],
                        relief='flat',
                        cursor='hand2',
                        command=command,
                        padx=15, pady=6)
        btn.pack(fill=tk.X)

        btn.bind('<Enter>', lambda e, b=btn, c=color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=COLORS['bg_card']))

        self._buttons.append(btn)
        return btn

    @staticmethod
    def _lighten_color(hex_color, factor):
        """将十六进制颜色变亮"""
        hex_color = hex_color.lstrip('#')
        r = min(255, int(int(hex_color[0:2], 16) * factor))
        g = min(255, int(int(hex_color[2:4], 16) * factor))
        b = min(255, int(int(hex_color[4:6], 16) * factor))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _build_output_panel(self, parent):
        """构建右侧输出面板"""
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=35)
        header.pack(fill=tk.X, pady=(0, 2))
        header.pack_propagate(False)

        tk.Label(header, text="输出日志",
                 font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(side=tk.LEFT, padx=12, pady=6)

        self.log_level_label = tk.Label(header, text="INFO",
                                        font=('Consolas', 8),
                                        bg=COLORS['success'],
                                        fg=COLORS['text_primary'],
                                        padx=6, pady=1)
        self.log_level_label.pack(side=tk.RIGHT, padx=12, pady=6)

        text_container = tk.Frame(parent, bg=COLORS['bg_input'])
        text_container.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(text_container, bg=COLORS['bg_card'],
                                 troughcolor=COLORS['bg_secondary'],
                                 activebackground=COLORS['border'],
                                 relief='flat',
                                 width=12)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.output_text = tk.Text(text_container,
                                   wrap=tk.WORD,
                                   font=('Consolas', 10),
                                   bg=COLORS['bg_input'],
                                   fg=COLORS['text_primary'],
                                   insertbackground=COLORS['accent_qxc'],
                                   relief='flat',
                                   padx=10, pady=10,
                                   state=tk.NORMAL,
                                   yscrollcommand=scrollbar.set)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar.config(command=self.output_text.yview)

        self._show_welcome()

    def _show_welcome(self):
        """显示欢迎信息"""
        welcome = f"""
{'='*70}
  欢迎使用 彩票数字概率统计分析系统
{'='*70}

  系统功能:
    [七星彩] 执行全部流程（爬取 + 分析 + 头4分析）
    [排列5]  执行全部流程（爬取 + 分析 + 头4分析）

  操作提示:
    - 点击左侧「执行全部流程」按钮开始完整分析
    - 所有任务在后台线程运行，界面不会卡顿
    - 执行中的任务会显示在进度条中
    - 报告和最优组合会自动存入数据库

  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*70}
"""
        self.output_text.insert(tk.END, welcome)
        self.output_text.see(tk.END)

    def _build_status_bar(self, parent):
        """构建底部状态栏"""
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

        tk.Label(status_bar, text="Python 3.x | tkinter GUI | MySQL 数据库",
                 font=('微软雅黑', 9),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_muted']).pack(side=tk.LEFT, padx=5, pady=4)

    # ============================================================
    # 按钮点击处理 - 所有按钮统一入口
    # ============================================================

    def _on_button_click(self, task_name, task_func):
        """统一按钮点击处理"""
        if self.task_mgr.is_running():
            messagebox.showwarning("提示", "当前有任务正在执行，请等待完成")
            return

        # 提交任务到线程池
        success = self.task_mgr.submit(task_func, task_name)
        if not success:
            messagebox.showwarning("提示", "任务提交失败，请重试")

    # ============================================================
    # 任务生命周期回调（主线程执行）
    # ============================================================

    def _on_task_started(self, task_name):
        """任务开始时的UI更新（主线程）"""
        self._current_task_name = task_name
        self._set_buttons_state(tk.DISABLED)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("正在执行...")
        self.task_status_label.config(text=f"{task_name} 运行中...", fg=COLORS['warning'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])

        now = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"\n{'='*70}\n")
        self._append_log(f"  [{now}] 开始执行: {task_name}\n")
        self._append_log(f"{'='*70}\n\n")

    def _on_task_finished(self):
        """任务完成时的UI更新（主线程）"""
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self.status_var.set("就绪")
        self.task_status_label.config(text="就绪", fg=COLORS['text_muted'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['success'])

        now = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"\n{'='*70}\n")
        self._append_log(f"  [{now}] 任务执行完成\n")
        self._append_log(f"{'='*70}\n\n")

    def _on_task_error(self, error_msg):
        """任务出错时的UI更新（主线程）"""
        self._set_buttons_state(tk.NORMAL)
        self.status_var.set("错误")
        self.task_status_label.config(text="执行出错", fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])
        messagebox.showerror("任务执行失败", f"错误: {error_msg}\n\n请查看输出日志获取详细信息")

    # ============================================================
    # UI更新方法（主线程安全）
    # ============================================================

    def _append_log(self, text):
        """追加日志到输出面板"""
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)

    def _update_progress_ui(self, value, text=""):
        """更新进度条（主线程）"""
        self.progress['value'] = value
        self.progress_label.config(text=f"{value}%")
        if text:
            self.task_status_label.config(text=text)

    def _update_status_ui(self, text, color):
        """更新状态标签（主线程）"""
        self.task_status_label.config(text=text, fg=color)

    def _update_stats_ui(self, text):
        """更新统计面板（主线程）"""
        self.stats_content.config(text=text)

    def _set_buttons_state(self, state):
        """设置按钮状态"""
        for btn in self._buttons:
            btn.config(state=state)

    def _clear_output(self):
        """清空输出面板"""
        self.output_text.delete(1.0, tk.END)
        self._show_welcome()

    # ============================================================
    # 业务逻辑方法（在后台线程执行，通过task_mgr与UI通信）
    # ============================================================

    def _execute_qxc_all(self, tm):
        """执行七星彩全部流程（后台线程）"""
        tm.log("=== 七星彩完整流程执行 ===\n")

        # 1. 爬取数据
        tm.progress(5, "正在爬取数据...")
        tm.log("【步骤 1/3】数据爬取\n")
        spider = QXCSpider()
        cleaner = DataCleaner()
        database = Database()

        tm.log('  正在爬取历史数据...')
        raw_data = spider.crawl_history_data()
        tm.log(f'  爬取到 {len(raw_data)} 条历史数据')

        tm.log('  正在清洗数据...')
        clean_data = cleaner.clean(raw_data)
        tm.log(f'  清洗后 {len(clean_data)} 条有效数据')

        tm.log('  正在存储到数据库...')
        if database.connect():
            database.create_tables()
            count = database.insert_or_update_qxc_data(clean_data)
            total = database.get_qxc_data_count()
            tm.log(f'  成功存储 {count} 条数据，数据库共 {total} 条')
            database.disconnect()
        else:
            tm.log('  数据库连接失败，跳过存储')
        tm.progress(33, "数据爬取完成")

        # 2. 分析报告
        tm.progress(40, "正在生成分析报告...")
        tm.log("\n【步骤 2/3】分析报告生成\n")
        database = Database()
        analyzer = ProbabilityAnalyzer()
        generator = ReportGenerator()

        if database.connect():
            data = database.query_all_qxc_data()
            database.disconnect()

            tm.log(f'  分析 {len(data)} 期历史数据...')
            result = analyzer.calculate_probability(data)

            tm.log('  生成详细统计分析报告...')
            report_result = generator.generate_detailed_report(result, analyzer)

            tm.log('  生成综合统计特征报告...')
            report_result_opt = generator.generate_optimal_report(result)

            if database.connect():
                database.create_tables()
                database.insert_detailed_report(
                    report_result['report_content'],
                    report_result.get('total_samples', 0),
                    report_result.get('frequency_analysis', ''),
                    report_result.get('omission_analysis', ''),
                    report_result.get('interval_analysis', ''),
                    report_result.get('frequency_chart'),
                    report_result.get('omission_chart')
                )
                database.insert_optimal_report(
                    report_result_opt['report_content'],
                    report_result_opt.get('recommended_numbers', ''),
                    report_result_opt.get('confidence_score', 0.0),
                    report_result_opt.get('analysis_summary', ''),
                    report_result_opt.get('frequency_chart'),
                    report_result_opt.get('omission_chart')
                )
                database.disconnect()
                tm.log('  分析报告已存入数据库')
        tm.progress(66, "分析报告完成")

        # 3. 头4分析
        tm.progress(75, "正在头4分析...")
        tm.log("\n【步骤 3/3】头4分析\n")
        database = Database()
        head4_analyzer = Head4Analyzer()

        if database.connect():
            data = database.query_all_qxc_data()
            database.disconnect()

            tm.log(f'  分析 {len(data)} 期历史数据的头4特征...')
            result = head4_analyzer.calculate_head4_analysis(data)

            tm.log('  生成最优10组数字组合...')
            top10 = head4_analyzer.generate_top10_combinations(data)

            tm.log('  生成头4分析报告...')
            report_content = head4_analyzer.generate_head4_report(result, top10_combinations=top10)

            tm.log('\n  【最优10组数字组合】')
            for item in top10:
                tm.log(f"    第{item['rank']:>2}名: {item['combination']}  "
                       f"得分: {item['score']:.4f}")

            if database.connect():
                database.create_tables()
                import uuid
                report_uuid = str(uuid.uuid4())
                report_date = datetime.now().strftime('%Y-%m-%d')

                database.insert_head4_report(
                    report_content,
                    result.get('total_samples', 0),
                    result.get('head_frequency_analysis', ''),
                    result.get('middle_frequency_analysis', ''),
                    result.get('tail_frequency_analysis', ''),
                    result.get('head_omission_analysis', ''),
                    result.get('middle_omission_analysis', ''),
                    result.get('tail_omission_analysis', ''),
                    result.get('head_tail_combination', ''),
                    result.get('middle_features', '')
                )
                database.insert_head4_top10(report_uuid, report_date, top10)
                database.disconnect()
                tm.log('  头4分析报告及最优组合已存入数据库')
        tm.progress(100, "头4分析完成")

        tm.log('\n=== 七星彩全部流程执行完成 ===')

    def _execute_p5_all(self, tm):
        """执行排列5全部流程（后台线程）"""
        tm.log("=== 排列5完整流程执行 ===\n")

        # 1. 爬取数据
        tm.progress(5, "正在爬取数据...")
        tm.log("【步骤 1/3】数据爬取\n")
        spider = P5Spider()
        database = P5Database()

        tm.log('  正在爬取历史数据...')
        history_data = spider.crawl_history_data()
        tm.log(f'  爬取到 {len(history_data)} 条历史数据')

        tm.log('  正在爬取走势图数据...')
        trend_data = spider.crawl_trend_data(record=120)
        tm.log(f'  爬取到 {len(trend_data)} 条走势图数据')

        tm.log('  正在存储到数据库...')
        if database.connect():
            database.create_tables()
            history_count = database.insert_history_data(history_data)
            trend_count = database.insert_trend_data(trend_data)
            tm.log(f'  成功存储 {history_count} 条历史数据')
            tm.log(f'  成功存储 {trend_count} 条走势图数据')
            database.disconnect()
        else:
            tm.log('  数据库连接失败，跳过存储')
        tm.progress(33, "数据爬取完成")

        # 2. 分析报告
        tm.progress(40, "正在生成分析报告...")
        tm.log("\n【步骤 2/3】分析报告生成\n")
        database = P5Database()
        analyzer = P5Analyzer()
        generator = P5ReportGenerator()

        if database.connect():
            history_data = database.get_history_data()
            trend_data = database.get_trend_data()
            database.disconnect()

            tm.log(f'  分析 {len(history_data)} 期历史数据...')
            if trend_data:
                tm.log(f'  整合 {len(trend_data)} 条走势图数据...')

            result = analyzer.calculate_probability(
                history_data,
                trend_data if trend_data else None
            )

            tm.log('  生成详细分析报告...')
            report_result = generator.generate_detailed_report(result, analyzer)

            tm.log('  生成最终最优报告...')
            report_result_opt = generator.generate_optimal_report(result)

            if database.connect():
                database.save_detailed_report(report_result)
                database.save_final_report(report_result_opt)
                database.disconnect()
                tm.log('  分析报告已存入数据库')
        tm.progress(66, "分析报告完成")

        # 3. 头4分析
        tm.progress(75, "正在头4分析...")
        tm.log("\n【步骤 3/3】头4分析\n")
        database = P5Database()
        head4_analyzer = P5Head4Analyzer()

        if database.connect():
            history_data = database.get_history_data()
            database.disconnect()

            tm.log(f'  分析 {len(history_data)} 期历史数据的头4特征...')
            result = head4_analyzer.calculate_head4_analysis(history_data)

            tm.log('  生成最优10组数字组合...')
            top10 = head4_analyzer.generate_top10_combinations(history_data)

            tm.log('  生成头4分析报告...')
            report_content = head4_analyzer.generate_head4_report(result, top10_combinations=top10)

            tm.log('\n  【最优10组数字组合】')
            for item in top10:
                tm.log(f"    第{item['rank']:>2}名: {item['combination']}  "
                       f"得分: {item['score']:.4f}")

            if database.connect():
                database.create_tables()
                import uuid
                report_uuid = str(uuid.uuid4())
                report_date = datetime.now().strftime('%Y-%m-%d')

                database.insert_head4_report(
                    report_content,
                    result.get('total_samples', 0),
                    result.get('head_frequency_analysis', ''),
                    result.get('middle_frequency_analysis', ''),
                    result.get('tail_frequency_analysis', ''),
                    result.get('head_omission_analysis', ''),
                    result.get('middle_omission_analysis', ''),
                    result.get('tail_omission_analysis', ''),
                    result.get('head_tail_combination', ''),
                    result.get('middle_features', '')
                )
                database.insert_head4_top10(report_uuid, report_date, top10)
                database.disconnect()
                tm.log('  头4分析报告及最优组合已存入数据库')
        tm.progress(100, "头4分析完成")

        tm.log('\n=== 排列5全部流程执行完成 ===')

    def _check_database(self, tm):
        """检测并修复数据库表（后台线程）"""
        tm.log("=== 数据库检测与修复 ===\n")

        tm.log("【七星彩数据库检测】")
        db = Database()
        result = db.check_and_repair_tables()
        if result['status'] == 'ok':
            tm.log(f"  状态: 正常")
            tm.log(f"  现有表: {', '.join(result['existing'])}")
        elif result['status'] == 'repaired':
            tm.log(f"  状态: 已修复")
            tm.log(f"  修复表: {', '.join(result['missing'])}")
        else:
            tm.log(f"  状态: 错误 - {result['message']}")

        tm.log("")

        tm.log("【排列5数据库检测】")
        db5 = P5Database()
        result5 = db5.check_and_repair_tables()
        if result5['status'] == 'ok':
            tm.log(f"  状态: 正常")
            tm.log(f"  现有表: {', '.join(result5['existing'])}")
        elif result5['status'] == 'repaired':
            tm.log(f"  状态: 已修复")
            tm.log(f"  修复表: {', '.join(result5['missing'])}")
        else:
            tm.log(f"  状态: 错误 - {result5['message']}")

        tm.log('\n=== 数据库检测完成 ===')

    def _view_data_summary(self, tm):
        """查看数据概览（后台线程）"""
        tm.log("=== 数据概览 ===\n")

        qxc_stats = {}
        p5_stats = {}

        # 七星彩数据
        database = Database()
        if database.connect():
            history_count = database.get_qxc_data_count()

            try:
                database.cursor.execute('SELECT COUNT(*) as count FROM qxc_trend_data')
                trend_count = database.cursor.fetchone()['count']
            except Exception:
                trend_count = 0

            try:
                database.cursor.execute('SELECT COUNT(*) as count FROM qxc_detailed_report')
                detailed_count = database.cursor.fetchone()['count']
                database.cursor.execute('SELECT COUNT(*) as count FROM qxc_final_report')
                final_count = database.cursor.fetchone()['count']
            except Exception:
                detailed_count = 0
                final_count = 0

            try:
                database.cursor.execute('SELECT COUNT(*) as count FROM qxc_head4_report')
                head4_count = database.cursor.fetchone()['count']
                database.cursor.execute('SELECT COUNT(*) as count FROM qxc_head4_top10')
                head4_top10_count = database.cursor.fetchone()['count']
            except Exception:
                head4_count = 0
                head4_top10_count = 0

            database.disconnect()

            qxc_stats = {
                'history': history_count,
                'trend': trend_count,
                'detailed': detailed_count,
                'final': final_count,
                'head4': head4_count,
                'head4_top10': head4_top10_count
            }
        else:
            tm.log('  【七星彩】数据库连接失败')

        # 排列5数据
        database_p5 = P5Database()
        if database_p5.connect():
            try:
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_history_data')
                history_count = database_p5.cursor.fetchone()['count']
            except Exception:
                history_count = 0

            try:
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_trend_data')
                trend_count = database_p5.cursor.fetchone()['count']
            except Exception:
                trend_count = 0

            try:
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_detailed_report')
                detailed_count = database_p5.cursor.fetchone()['count']
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_final_report')
                final_count = database_p5.cursor.fetchone()['count']
            except Exception:
                detailed_count = 0
                final_count = 0

            try:
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_head4_report')
                head4_count = database_p5.cursor.fetchone()['count']
                database_p5.cursor.execute('SELECT COUNT(*) as count FROM p5_head4_top10')
                head4_top10_count = database_p5.cursor.fetchone()['count']
            except Exception:
                head4_count = 0
                head4_top10_count = 0

            database_p5.disconnect()

            p5_stats = {
                'history': history_count,
                'trend': trend_count,
                'detailed': detailed_count,
                'final': final_count,
                'head4': head4_count,
                'head4_top10': head4_top10_count
            }
        else:
            tm.log('  【排列5】数据库连接失败')

        tm.log('  【七星彩数据统计】')
        tm.log(f'    历史开奖数据: {qxc_stats.get("history", 0)} 条')
        tm.log(f'    走势图数据: {qxc_stats.get("trend", 0)} 条')
        tm.log(f'    详细分析报告: {qxc_stats.get("detailed", 0)} 份')
        tm.log(f'    最终最优报告: {qxc_stats.get("final", 0)} 份')
        tm.log(f'    头4分析报告: {qxc_stats.get("head4", 0)} 份')
        tm.log(f'    头4最优组合: {qxc_stats.get("head4_top10", 0)} 组')

        tm.log("")
        tm.log('  【排列5数据统计】')
        tm.log(f'    历史开奖数据: {p5_stats.get("history", 0)} 条')
        tm.log(f'    走势图数据: {p5_stats.get("trend", 0)} 条')
        tm.log(f'    详细分析报告: {p5_stats.get("detailed", 0)} 份')
        tm.log(f'    最优分析报告: {p5_stats.get("final", 0)} 份')
        tm.log(f'    头4分析报告: {p5_stats.get("head4", 0)} 份')
        tm.log(f'    头4最优组合: {p5_stats.get("head4_top10", 0)} 组')

        stats_text = (
            f"七星彩: {qxc_stats.get('history', 0)}条数据 | {qxc_stats.get('detailed', 0)}份报告\n"
            f"排列5: {p5_stats.get('history', 0)}条数据 | {p5_stats.get('detailed', 0)}份报告"
        )
        tm.stats(stats_text)

        tm.log('\n  数据概览查询完成！')


def main():
    """启动GUI程序"""
    root = tk.Tk()
    app = LotteryGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
