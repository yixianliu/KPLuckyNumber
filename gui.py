"""排列5 AI智能分析系统 - GUI界面

核心功能：
1. 排列5数据爬取与AI分析
2. AI分析报告展示
3. 预测结果可视化
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.ai_analyzer import AIAnalyzer
from modules.database_p5 import P5Database
from modules.spider_p5 import P5Spider
from modules.prediction_validator import P5PredictionValidator
from modules.optimized_p5_predictor import OptimizedP5Predictor, OptimizedP5PredictorConfig
from modules.backtest_engine import P5BacktestEngine
from modules.feature_engineering import P5FeatureEngineering

COLORS = {
    'bg_primary': '#0f172a',
    'bg_secondary': '#1e293b',
    'bg_card': '#334155',
    'bg_input': '#1e1e2e',
    'accent_p5': '#10b981',
    'accent_ai': '#8b5cf6',
    'accent_danger': '#ef4444',
    'text_primary': '#f1f5f9',
    'text_secondary': '#94a3b8',
    'text_muted': '#64748b',
    'border': '#475569',
    'success': '#22c55e',
    'warning': '#f59e0b',
}


class TaskManager:
    def __init__(self, gui_instance):
        self.gui = gui_instance
        self._task_queue = queue.Queue()
        self._running = False
        self._current_future = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._cancelled = False
        self._poll_ui_updates()

    def _poll_ui_updates(self):
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
        self._task_queue.put({'type': 'log', 'text': text})

    def progress(self, value, text=""):
        self._task_queue.put({'type': 'progress', 'value': value, 'text': text})

    def status(self, text, color=COLORS['text_muted']):
        self._task_queue.put({'type': 'status', 'text': text, 'color': color})

    def report(self, data):
        self._task_queue.put({'type': 'report', 'data': data})

    def finished(self):
        self._task_queue.put({'type': 'finished'})

    def error(self, err_text):
        self._task_queue.put({'type': 'error', 'error': err_text})

    def is_running(self):
        with self._lock:
            return self._running

    def submit(self, task_func, task_name="任务"):
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancelled = False

        self.gui._on_task_started(task_name)
        self._current_future = self._executor.submit(self._task_wrapper, task_func)
        return True

    def _task_wrapper(self, task_func):
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
        with self._lock:
            self._running = False
        self.gui._on_task_finished()

    def _on_task_error(self, error_msg):
        with self._lock:
            self._running = False
        self.gui._on_task_error(error_msg)

    def cancel(self):
        self._cancelled = True
        with self._lock:
            self._running = False

    def shutdown(self):
        self._executor.shutdown(wait=False)


class LotteryGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("排列5 AI智能分析系统")
        self.root.geometry("1100x750")
        self.root.minsize(950, 650)
        self.root.configure(bg=COLORS['bg_primary'])

        self.task_mgr = TaskManager(self)
        self._buttons = []
        self._current_task_name = ""

        self._setup_window_style()
        self._build_ui()

    def _setup_window_style(self):
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
        main_container = tk.Frame(self.root, bg=COLORS['bg_primary'])
        main_container.pack(fill=tk.BOTH, expand=True)

        self._build_header(main_container)
        self._build_content(main_container)
        self._build_status_bar(main_container)

    def _build_header(self, parent):
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
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.root.after(1000, self._update_time)

    def _build_content(self, parent):
        content = tk.Frame(parent, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        left = tk.Frame(content, bg=COLORS['bg_primary'], width=260)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)

        self._build_control_panel(left)

        right = tk.Frame(content, bg=COLORS['bg_primary'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_output_panel(right)

    def _build_control_panel(self, parent):
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
        card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                        highlightbackground=COLORS['border'],
                        highlightthickness=1)

        top_bar = tk.Frame(card, bg=accent_color, height=2)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

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

        btn.bind('<Enter>', lambda e, b=btn, c=color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=COLORS['bg_card']))

        self._buttons.append(btn)
        return btn

    @staticmethod
    def _lighten_color(hex_color, factor):
        hex_color = hex_color.lstrip('#')
        r = min(255, int(int(hex_color[0:2], 16) * factor))
        g = min(255, int(int(hex_color[2:4], 16) * factor))
        b = min(255, int(int(hex_color[4:6], 16) * factor))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _build_output_panel(self, parent):
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
        welcome = f"""
{'='*70}
  欢迎使用 排列5 AI智能分析系统 v2.0
{'='*70}

  系统功能:
    [增量爬取数据] 仅获取数据库中缺失的新数据
    [全量爬取数据] 重新爬取全部历史数据和走势数据
    
    [AI智能分析] ✨ 优化后模型分析（推荐）
    [历史回测] ✨ 批量历史回测，验证模型性能
    [特征分析] ✨ 提取和分析历史数据特征
    
    [验证待验证预测] 自动比对预测与实际开奖结果
    [性能评估报告] 生成AI预测命中率统计报告

  优化模型 (v2.0):
    - 修复期号排序Bug（数值排序而非字符串排序）
    - 修复质数定义Bug（1不是质数）
    - 修复遗漏值计算Bug（正确处理从未出现的号码）
    - 新增特征工程（012路、连号、重隔号、区间分布、滑动窗口）
    - 新增概率归一化（确保概率总和为1）
    - 新增边界保护（限制极端输出）
    - 新增多模型融合（统计模型+特征工程交叉校验）

  ⚠️ 重要提示：本系统仅基于历史数据统计分析，无法预测开奖结果，
     不构成任何投资建议。彩票开奖具有随机性，请理性购彩。

  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*70}
"""
        self.output_text.insert(tk.END, welcome)
        self.output_text.see(tk.END)

    def _build_status_bar(self, parent):
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
        if self.task_mgr.is_running():
            messagebox.showwarning("提示", "当前有任务正在执行，请等待完成")
            return

        success = self.task_mgr.submit(task_func, task_name)
        if not success:
            messagebox.showwarning("提示", "任务提交失败，请重试")

    def _on_task_started(self, task_name):
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
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self.status_var.set("任务完成")
        self.task_status_label.config(text=f"{self._current_task_name} 已完成", fg=COLORS['success'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['success'])

        now = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"\n{'='*70}\n")
        self._append_log(f"  [{now}] 任务完成: {self._current_task_name}\n")
        self._append_log(f"{'='*70}\n")

    def _on_task_error(self, error_msg):
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("任务失败")
        self.task_status_label.config(text=f"{self._current_task_name} 失败", fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])

        messagebox.showerror("错误", f"任务执行失败:\n{error_msg}")

    def _append_log(self, text):
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)

    def _clear_output(self):
        self.output_text.delete(1.0, tk.END)
        self._show_welcome()

    def _update_progress_ui(self, value, text=""):
        self.progress['value'] = value
        self.progress_label.config(text=f"{int(value)}%")
        if text:
            self.task_status_label.config(text=text, fg=COLORS['text_secondary'])

    def _update_status_ui(self, text, color=COLORS['text_muted']):
        self.status_var.set(text)

    def _display_report(self, data):
        if data.get('report'):
            self._append_log(data['report'])

    def _set_buttons_state(self, state):
        for btn in self._buttons:
            btn.config(state=state)

    # ============================================================
    # 业务任务
    # ============================================================



    def _check_database(self, task_mgr):
        """数据库检测"""
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

    def _execute_crawl_incremental(self, task_mgr):
        """执行增量爬取数据"""
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
        """执行全量爬取数据"""
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

    def _execute_verify_predictions(self, task_mgr):
        """执行预测验证"""
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
        """生成性能评估报告"""
        task_mgr.log("生成AI预测性能评估报告...")
        task_mgr.progress(30, "获取统计数据")

        validator = P5PredictionValidator()
        report = validator.generate_performance_report()

        task_mgr.progress(80, "渲染报告")
        task_mgr.log("\n" + report)
        task_mgr.progress(100, "完成")

    def _update_quick_stats(self, task_mgr):
        """更新快捷统计面板"""
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
    # 优化后AI分析任务（v2.0）
    # ============================================================

    def _execute_optimized_p5_ai(self, task_mgr):
        """执行优化后AI分析（v2.0）"""
        try:
            task_mgr.log("正在初始化优化后AI预测器...")
            task_mgr.progress(10, "初始化预测器")

            predictor = OptimizedP5Predictor()

            task_mgr.log("正在从数据库获取历史数据...")
            task_mgr.progress(20, "获取数据")

            db = P5Database()
            if not db.connect():
                task_mgr.log("✗ 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            history_data = db.get_history_data(limit=200, order_by='issue DESC')
            db.disconnect()

            if not history_data:
                task_mgr.log("✗ 数据库中没有历史数据")
                task_mgr.log("建议操作: 先执行「增量爬取数据」或「全量爬取数据」")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.log(f"✓ 数据库获取成功: {len(history_data)} 条历史数据")
            current_issue = history_data[0].get('issue', '')
            task_mgr.log(f"当前最新期号: {current_issue}")

            task_mgr.progress(35, "数据获取完成")

            # 执行优化后AI分析
            task_mgr.log("\n正在执行优化后多模型综合分析...")
            task_mgr.progress(50, "多算法预测")

            result = predictor.predict(history_data, current_issue)

            if 'error' in result:
                task_mgr.log(f"\n✗ 预测失败: {result['error']}")
                task_mgr.progress(0, "预测失败")
                return

            task_mgr.progress(75, "分析完成")

            # 显示分析报告（简化版）
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("✓ AI智能分析报告")
            task_mgr.log("=" * 70)

            # 显示AI分析状态
            ai_enabled = result.get('ai_analysis_enabled', False)
            task_mgr.log(f"\n【分析状态】{'AI大模型已启用' if ai_enabled else '统计模型分析'}")
            if not ai_enabled:
                task_mgr.log("  提示：未配置API密钥，当前仅使用统计模型分析")

            # 输出万千百十位预测号码
            task_mgr.log("\n【万千百十位预测号码】")
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            for pos in range(5):
                pos_name = pos_names[pos]
                pos_probs = result['fused_probabilities'][pos]
                sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
                top_3 = sorted_nums[:3]

                task_mgr.log(f"\n{pos_name}:")
                for rank, (num, prob) in enumerate(top_3, 1):
                    task_mgr.log(f"  {rank}. 号码{num} (概率: {prob:.2%})")

            # 输出推荐组合
            task_mgr.log("\n【推荐组合（Top-5）】")
            for combo in result['top_combinations'][:5]:
                task_mgr.log(f"{combo['rank']}. {combo['combination']} (置信度: {combo['confidence']:.2f}%)")

            # 输出风险提示
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log(result['risk_warning'])
            task_mgr.log("=" * 70)

            # 更新统计面板
            stats_text = (
                f"数据量: {result.get('data_samples')} 条\n"
                f"最新期号: {current_issue}\n"
                f"预测期号: {result.get('target_issue')}\n"
                f"算法数量: {len(result.get('algorithm_weights', {}))}"
            )
            self.stats_content.config(text=stats_text, fg=COLORS['success'])

            # 保存预测结果
            os.makedirs('predictions', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'predictions/optimized_prediction_{timestamp}.json'

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)

            task_mgr.log(f"\n✓ 预测结果已保存到: {filename}")

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n✓ 优化后AI分析流程全部完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ 优化后AI分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_backtest(self, task_mgr):
        """执行历史回测"""
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
        """执行特征分析"""
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
    root = tk.Tk()
    app = LotteryGUI(root)

    def on_closing():
        app.task_mgr.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()