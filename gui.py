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
        self.root.geometry("1200x800")
        self.root.minsize(1000, 700)
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
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLORS['bg_secondary'])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=15)

        icon = tk.Canvas(left, width=36, height=36, bg=COLORS['bg_secondary'],
                         highlightthickness=0)
        icon.pack(side=tk.LEFT, pady=12)
        icon.create_rectangle(2, 2, 34, 16, fill=COLORS['accent_p5'], outline='', width=0)
        icon.create_rectangle(2, 20, 34, 34, fill=COLORS['accent_ai'], outline='', width=0)

        title_box = tk.Frame(left, bg=COLORS['bg_secondary'])
        title_box.pack(side=tk.LEFT, padx=(10, 0), pady=8)

        tk.Label(title_box, text="排列5 AI智能分析系统",
                 font=('微软雅黑', 14, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_primary']).pack(anchor=tk.W)

        tk.Label(title_box, text="多模型综合预测分析平台",
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
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.root.after(1000, self._update_time)

    def _build_content(self, parent):
        content = tk.Frame(parent, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        left = tk.Frame(content, bg=COLORS['bg_primary'], width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        self._build_control_panel(left)

        right = tk.Frame(content, bg=COLORS['bg_primary'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_output_panel(right)

    def _build_control_panel(self, parent):
        # 数据爬取卡片
        crawl_card = self._create_card(parent, "数据爬取", '#f59e0b')
        crawl_card.pack(fill=tk.X, pady=(0, 10))

        self._add_big_button(crawl_card, "增量爬取数据", '#f59e0b',
                             lambda: self._on_button_click("增量爬取数据", self._execute_crawl_incremental))
        self._add_action_button(crawl_card, "全量爬取数据", '#d97706',
                                lambda: self._on_button_click("全量爬取数据", self._execute_crawl_full))

        # AI分析卡片
        p5_card = self._create_card(parent, "排列5 AI分析", COLORS['accent_p5'])
        p5_card.pack(fill=tk.X, pady=(0, 10))

        self._add_big_button(p5_card, "执行AI智能分析", COLORS['accent_p5'],
                             lambda: self._on_button_click("AI智能分析", self._execute_p5_ai))
        self._add_action_button(p5_card, "查看最新报告", '#8b5cf6',
                                lambda: self._on_button_click("查看最新报告", self._view_latest_report))
        self._add_action_button(p5_card, "查看报告列表", '#3b82f6',
                                lambda: self._on_button_click("查看报告列表", self._view_report_list))

        # 预测验证卡片
        verify_card = self._create_card(parent, "预测验证", '#ec4899')
        verify_card.pack(fill=tk.X, pady=(0, 10))

        self._add_action_button(verify_card, "验证待验证预测", '#ec4899',
                                lambda: self._on_button_click("验证预测", self._execute_verify_predictions))
        self._add_action_button(verify_card, "性能评估报告", '#db2777',
                                lambda: self._on_button_click("性能评估", self._execute_performance_report))

        # 系统操作卡片
        common_card = self._create_card(parent, "系统操作", COLORS['accent_ai'])
        common_card.pack(fill=tk.X, pady=(0, 10))

        self._add_action_button(common_card, "数据库检测", COLORS['accent_ai'],
                                lambda: self._on_button_click("数据库检测", self._check_database))
        self._add_action_button(common_card, "更新快捷统计", '#06b6d4',
                                lambda: self._on_button_click("更新统计", self._update_quick_stats))
        self._add_action_button(common_card, "清空输出", COLORS['accent_danger'],
                                self._clear_output)

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
                                       fg=COLORS['accent_p5'])
        self.progress_label.pack(anchor=tk.CENTER, pady=(0, 5))

        self.task_status_label = tk.Label(progress_card, text="就绪",
                                          font=('微软雅黑', 9),
                                          bg=COLORS['bg_secondary'],
                                          fg=COLORS['text_muted'])
        self.task_status_label.pack(anchor=tk.W, padx=12, pady=(0, 10))

        stats_card = tk.Frame(parent, bg=COLORS['bg_secondary'],
                              highlightbackground=COLORS['border'],
                              highlightthickness=1)
        stats_card.pack(fill=tk.X)

        tk.Label(stats_card, text="快捷统计",
                 font=('微软雅黑', 10, 'bold'),
                 bg=COLORS['bg_secondary'],
                 fg=COLORS['text_secondary']).pack(anchor=tk.W, padx=12, pady=(10, 5))

        self.stats_content = tk.Label(stats_card,
                                      text="点击「执行AI智能分析」开始",
                                      font=('微软雅黑', 9),
                                      bg=COLORS['bg_secondary'],
                                      fg=COLORS['text_muted'],
                                      justify=tk.LEFT)
        self.stats_content.pack(anchor=tk.W, padx=12, pady=(0, 10))

    def _create_card(self, parent, title, accent_color):
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
        hex_color = hex_color.lstrip('#')
        r = min(255, int(int(hex_color[0:2], 16) * factor))
        g = min(255, int(int(hex_color[2:4], 16) * factor))
        b = min(255, int(int(hex_color[4:6], 16) * factor))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _build_output_panel(self, parent):
        header = tk.Frame(parent, bg=COLORS['bg_secondary'], height=35)
        header.pack(fill=tk.X, pady=(0, 2))
        header.pack_propagate(False)

        tk.Label(header, text="AI分析报告",
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
                                   insertbackground=COLORS['accent_p5'],
                                   relief='flat',
                                   padx=10, pady=10,
                                   state=tk.NORMAL,
                                   yscrollcommand=scrollbar.set)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar.config(command=self.output_text.yview)

        self._show_welcome()

    def _show_welcome(self):
        welcome = f"""
{'='*70}
  欢迎使用 排列5 AI智能分析系统
{'='*70}

  系统功能:
    [增量爬取数据] 仅获取数据库中缺失的新数据
    [全量爬取数据] 重新爬取全部历史数据和走势数据
    [AI智能分析] 多模型综合分析 + 预测 + 保存报告
    [查看最新报告] 从数据库读取最新的AI分析报告
    [查看报告列表] 查看历史AI分析报告列表
    [验证待验证预测] 自动比对预测与实际开奖结果
    [性能评估报告] 生成AI预测命中率统计报告

  分析模型:
    - 频率统计模型：基于历史数据统计号码出现频率
    - 遗漏分析模型：分析号码遗漏值，预测冷号回补
    - 趋势分析模型：分析近期走势方向
    - 机器学习模型：基于条件概率的智能预测

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

    def _execute_p5_ai(self, task_mgr):
        """执行排列5 AI智能分析"""
        try:
            task_mgr.log("正在初始化AI分析器...")
            task_mgr.progress(10, "初始化分析器")
            
            analyzer = AIAnalyzer()
            
            task_mgr.log("正在从数据库获取历史数据...")
            task_mgr.progress(20, "获取数据")
            
            try:
                data = analyzer.fetch_data(source='database')
                task_mgr.log(f"✓ 数据库获取成功: {len(data)} 条历史数据")
            except Exception as e:
                task_mgr.log(f"✗ 数据库获取失败: {str(e)}")
                task_mgr.log("正在尝试从爬虫获取数据...")
                try:
                    data = analyzer.fetch_data(source='spider')
                    task_mgr.log(f"✓ 爬虫获取成功: {len(data)} 条数据")
                except Exception as e2:
                    task_mgr.log(f"✗ 爬虫获取失败: {str(e2)}")
                    task_mgr.log("\n错误: 无法获取数据，请检查数据库连接或网络状态")
                    task_mgr.progress(0, "数据获取失败")
                    return
            
            task_mgr.progress(35, "数据获取完成")
            
            if not data:
                task_mgr.log("\n错误: 没有获取到任何数据")
                task_mgr.log("建议操作:")
                task_mgr.log("  1. 检查数据库是否有数据")
                task_mgr.log("  2. 执行「增量爬取数据」或「全量爬取数据」")
                task_mgr.log("  3. 检查网络连接状态")
                task_mgr.progress(0, "无数据")
                return
            
            # 数据质量检查
            task_mgr.log("\n正在验证数据质量...")
            task_mgr.progress(45, "验证数据质量")
            
            quality_report = analyzer.validate_data_quality(data)
            
            if quality_report.get('status') == 'error':
                task_mgr.log(f"\n✗ 数据质量不合格: {quality_report.get('message', '')}")
                task_mgr.log(f"  数据总量: {quality_report.get('total_count', 0)}")
                task_mgr.log(f"  有效数据: {quality_report.get('valid_count', 0)}")
                task_mgr.log(f"  有效率: {quality_report.get('valid_rate', 0)}%")
                
                issues = quality_report.get('issues', [])
                if issues:
                    task_mgr.log("\n问题列表:")
                    for issue in issues[:5]:
                        task_mgr.log(f"  - {issue}")
                
                task_mgr.log("\n建议: 请先执行数据爬取以获取更多有效数据")
                task_mgr.progress(0, "数据质量不合格")
                return
            
            if quality_report.get('status') == 'warning':
                task_mgr.log(f"\n⚠ 数据质量警告: {quality_report.get('message', '')}")
                task_mgr.log(f"  有效率: {quality_report.get('valid_rate', 0)}%")
                warnings = quality_report.get('warnings', [])
                if warnings:
                    for warning in warnings[:3]:
                        task_mgr.log(f"  - {warning}")
                task_mgr.log("\n继续执行分析...")
            
            task_mgr.log(f"✓ 数据质量验证通过: 有效率 {quality_report.get('valid_rate', 0)}%")
            
            # 执行AI分析
            task_mgr.log("\n正在执行多模型综合分析...")
            task_mgr.progress(55, "频率统计模型")
            
            result = analyzer.analyze_p5(data)
            
            if result.get('status') != 'success':
                error_msg = result.get('message', '未知错误')
                error_code = result.get('error_code', 'UNKNOWN')
                task_mgr.log(f"\n✗ AI分析失败: {error_msg}")
                task_mgr.log(f"  错误代码: {error_code}")
                
                if 'quality_report' in result:
                    qr = result['quality_report']
                    task_mgr.log(f"  数据质量: {qr.get('message', '')}")
                
                task_mgr.progress(0, "分析失败")
                return
            
            task_mgr.progress(75, "分析完成")
            
            # 显示分析报告
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log("✓ AI分析报告生成完成！")
            task_mgr.log("=" * 70)
            task_mgr.report(result)
            
            # 保存报告到数据库
            task_mgr.log("\n正在保存报告到数据库...")
            task_mgr.progress(85, "保存报告")
            
            try:
                save_result = analyzer.save_report_to_database(result)
                if save_result:
                    task_mgr.log(f"✓ 报告已成功保存到数据库")
                    task_mgr.log(f"  报告UUID: {save_result}")
                else:
                    task_mgr.log("⚠ 报告保存失败，但分析结果已显示")
            except Exception as e:
                task_mgr.log(f"⚠ 报告保存异常: {str(e)}")
                task_mgr.log("  分析结果已显示，可手动保存")
            
            # 更新统计面板
            data_summary = result.get('data_summary', {})
            quality_info = result.get('quality_report', {})
            
            stats_text = (
                f"数据量: {data_summary.get('data_count')} 条\n"
                f"有效率: {data_summary.get('valid_rate')}%\n"
                f"最新期号: {data_summary.get('latest_issue')}\n"
                f"预测期号: {data_summary.get('next_issue')}"
            )
            
            # 根据数据质量设置颜色
            if quality_info.get('status') == 'success':
                fg_color = COLORS['success']
            elif quality_info.get('status') == 'warning':
                fg_color = COLORS['warning']
            else:
                fg_color = COLORS['accent_danger']
            
            self.stats_content.config(text=stats_text, fg=fg_color)
            
            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n✓ AI智能分析流程全部完成")
            
        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n✗ AI分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")
            task_mgr.log("\n建议操作:")
            task_mgr.log("  1. 检查数据库连接配置")
            task_mgr.log("  2. 查看日志文件获取详细错误信息")
            task_mgr.log("  3. 联系技术支持")

    def _view_latest_report(self, task_mgr):
        """查看最新AI分析报告"""
        task_mgr.log("正在查询最新AI分析报告...")
        task_mgr.progress(30, "查询数据库")

        db = P5Database()
        if db.connect():
            report = db.get_latest_ai_report()
            db.disconnect()

            if report:
                task_mgr.log("\n" + "=" * 70)
                task_mgr.log("最新AI分析报告")
                task_mgr.log("=" * 70)
                task_mgr.log(f"报告日期: {report.get('report_date', '')}")
                task_mgr.log(f"数据量: {report.get('data_count', 0)}")
                task_mgr.log(f"最新期号: {report.get('latest_issue', '')}")
                task_mgr.log("\n报告内容:")
                task_mgr.log(report.get('report_content', '无报告内容'))
                task_mgr.log("\n" + "=" * 70)

                stats_text = f"数据量: {report.get('data_count')} | 最新期号: {report.get('latest_issue')}"
                self.stats_content.config(text=stats_text, fg=COLORS['success'])
            else:
                task_mgr.log("未找到AI分析报告，请先执行AI智能分析")
                self.stats_content.config(text="暂无报告数据", fg=COLORS['text_muted'])
        else:
            task_mgr.log("数据库连接失败")

    def _view_report_list(self, task_mgr):
        """查看AI分析报告列表"""
        task_mgr.log("正在查询AI分析报告列表...")

        db = P5Database()
        if db.connect():
            reports = db.get_all_ai_reports(limit=10)
            count = db.get_report_count()
            db.disconnect()

            if reports:
                task_mgr.log(f"\n共找到 {count} 份AI分析报告")
                task_mgr.log("=" * 70)
                for i, report in enumerate(reports, 1):
                    task_mgr.log(f"{i}. 报告日期: {report.get('report_date', '')}")
                    task_mgr.log(f"   数据量: {report.get('data_count', 0)} | 最新期号: {report.get('latest_issue', '')}")
                    task_mgr.log(f"   创建时间: {report.get('created_at', '')}")
                    task_mgr.log("")
            else:
                task_mgr.log("未找到AI分析报告")
        else:
            task_mgr.log("数据库连接失败")

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