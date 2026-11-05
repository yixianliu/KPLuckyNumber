# -*- coding: utf-8 -*-
"""%s —— %s Mixin（GUI-11 机械搬迁）。

v5.2 升级方案 PUP-20261019-013100 §337：8125 行单文件按
『构建期 / 渲染期 / 轮询 / 业务协调』拆分，本模块承载%s
（%s 分区，%d 法）方法，由 main.LotteryGUI 以 Mixin 方式继承。

机械搬迁约定（不改逻辑）：
  * 方法源码自 main.py 逐字搬运；解析到 main 模块作用域的符号
    （COLORS / logger / P5Database 等）在方法 docstring 后以
    `X = _main_mod.X` 别名绑定，LOAD_FAST 在方法单次执行内锁定
    当期值（main._apply_theme 的 global COLORS 切换后，每次方法
    调用经别名重取当期色板，与原 LOAD_GLOBAL 行为一致）；
  * 双模块陷阱破解：宿主以 python main.py 启动时模块名为
    __main__，直接 import main 会重复加载；先将 __main__ 注册回
    sys.modules['main'] 别名再 import，保证同一模块实例；
  * %s

【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来
开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成
任何购彩建议。请理性购彩，量力而行。
"""

# tk 家族依赖（GUI-11 S4 补齐）：main.py 的 tk 家族 import 位于
# try/except 保护块内，顶层语句扫描不可见，S3 首迁漏统；轮询与执行期
# 方法体裸引用 tk./ttk./messagebox./filedialog.（LOAD_GLOBAL 语义），
# 按 output_panel.py（S2）范式在本模块补齐；无显示环境导入失败置 None
# 兜底，不阻塞模块加载。
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:  # pragma: no cover - 无显示环境兜底
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

import json
import os
import queue
import threading
import time
import traceback
from datetime import datetime

from config import DATA_WINDOW_PERIOD
from modules import ui_styles
from modules import ui_toast
from modules.pipeline import run_four_step_pipeline
from modules.ui_theme_spec import (
    button_style_map,
    WHITE,
    UI_FONT,
    MONO_FONT,
)
from modules.visualizer import visualizer
from paths import REPORTS_DIR
import sys as _sys

# 双模块陷阱破解：python main.py 时宿主模块名为 __main__，
# 先注册 sys.modules['main'] 别名再 import，避免磁盘重复加载
if "main" not in _sys.modules:
    _host = _sys.modules.get("__main__")
    _host_file = str(getattr(_host, "__file__", "") or "").replace(
        "\\", "/")
    # 冻结（PyInstaller）后 __main__.__file__ 指向 exe 而非 main.py，
    # 必须同样注册别名，否则 `import main` 会二次导入失败/产生双实例
    if _host_file.endswith("/main.py") or getattr(_sys, 'frozen', False):
        _sys.modules["main"] = _host
# 经 setdefault 注册后同一模块实例（含 rebind 后新 COLORS）
import main as _main_mod  # noqa: E402  双模块陷阱破解后的正式引用
# 文件级 COLORS 别名（E-B 适配）：_flash_status / _update_status_ui 的
# 形参默认值 `color=COLORS['success']` 在 def 语句执行时求值（本模块
# import 期），方法头别名（函数调用期）来不及参与，故文件级一次性绑定。
# 语义等价性：原 main.py 默认值同为类体执行期一次性快照；main.py 将本
# 模块 import 置于 `COLORS = ThemeManager.get_theme()` 之后，保证本行
# 执行时 main.COLORS 已完成绑定（时序安全）。
COLORS = _main_mod.COLORS


class SchedulerMixin:
    """轮询与执行期方法（GUI-11 SCHED 分区 100 法，main.py 机械搬迁）。

    含三条轮询链（_poll_* / _update_time）、后台任务执行
    （_execute_*）与剪贴板/导出/报告等业务协调方法。
    """
    def _init_ui_scheduler(self):
        """GUI-04（v5.2 §2.3）：创建轮询统一调度器并注册任务。

        注册集中在 _build_ui 之前完成：
          - update_time：固定 1000ms 间隔（与旧行为一致，不参与空闲退避）；
          - poll_cli_log / poll_evolution：interval_ms=0 跟随全局轮询间隔
            （默认 200ms，60s 无业务事件退避至 500ms）。
        poll_evolution 由 _init_evolution_engine 在引擎就绪后注册（引擎未启动
        时无需空转）；本方法保证调度循环在 _build_ui 前启动。

        特性开关关闭或初始化失败时 _ui_scheduler 保持 None，三条轮询回退为
        各自独立的 after 自调度链（R4 回滚，行为与 v5.1 一致）。
        """
        _UIScheduler = _main_mod._UIScheduler
        _ui_scheduler_enabled = _main_mod._ui_scheduler_enabled
        logger = _main_mod.logger
        if not _ui_scheduler_enabled():
            logger.info('[GUI] ENABLE_UI_SCHEDULER=false，轮询回退自调度 after 链（R4）')
            return
        try:
            self._ui_scheduler = _UIScheduler(
                self.root.after,
                after_cancel_fn=self.root.after_cancel,
            )
            self._ui_scheduler.register('update_time', self._update_time,
                                        interval_ms=1000)
            self._ui_scheduler.register('poll_cli_log', self._poll_cli_log,
                                        interval_ms=0)
            self._ui_scheduler.start()
            logger.info('[GUI] _ui_scheduler 已启动：单一 100ms tick 循环接管轮询')
        except Exception as _us_e:
            logger.warning('[GUI] _ui_scheduler 初始化失败(回退自调度链): %s', _us_e)
            self._ui_scheduler = None

    def _update_time(self):
        """每秒更新顶部时钟显示

        GUI-04：调度器开启时由 _ui_scheduler 以固定 1000ms 间隔触发，
        本方法不再自调度（仅回退路径保留 after(1000) 自调度链）。
        """
        self.time_label.config(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._update_time_handle = self.root.after(1000, self._update_time)

    def _show_visualization_menu(self):
        """显示可视化菜单"""
        # 如果已经有预测结果，则显示可视化选项菜单
        if not hasattr(self, '_last_pipeline_final') or not self._last_pipeline_final:
            messagebox.showinfo("提示", "请先进行预测分析，以便生成可视化图表")
            return
            
        # 创建弹出菜单
        if self._viz_menu:
            self._viz_menu.destroy()
            
        self._viz_menu = tk.Menu(self.root, tearoff=0)
        
        # 添加各种可视化选项
        self._viz_menu.add_command(label="基本走势趋势图", 
                                  command=lambda: self._generate_and_show_viz('basic_trend'))
        self._viz_menu.add_command(label="冷热分布直方图", 
                                  command=lambda: self._generate_and_show_viz('cold_hot_histogram'))
        self._viz_menu.add_command(label="分布关联矩阵", 
                                  command=lambda: self._generate_and_show_viz('distribution_correlation'))
        self._viz_menu.add_command(label="和值重心曲线", 
                                  command=lambda: self._generate_and_show_viz('average_value_center'))
        self._viz_menu.add_command(label="冷热演变趋势图", 
                                  command=lambda: self._generate_and_show_viz('cold_hot_evolution'))
        self._viz_menu.add_separator()
        self._viz_menu.add_command(label="导出所有图表", 
                                  command=self._export_all_visualizations)
        
        # 显示菜单
        try:
            self._viz_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            self._viz_menu.grab_release()

    def _generate_and_show_viz(self, viz_type):
        """生成并显示指定类型的可视化图表"""
        try:
            # 获取历史数据
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                messagebox.showerror("错误", "数据库连接失败")
                return
                
            try:
                # 获取最近的历史数据用于可视化
                history_data = db.get_history_data(limit=100, order='ASC')  # 最近100期
                if not history_data:
                    messagebox.showwarning("警告", "没有足够的历史数据生成可视化图表")
                    return
                    
                # 调用可视化器生成图表
                output_path = None
                if viz_type == 'basic_trend':
                    output_path = visualizer.plot_basic_trend(history_data)
                elif viz_type == 'cold_hot_histogram':
                    output_path = visualizer.plot_cold_hot_histogram(history_data)
                elif viz_type == 'distribution_correlation':
                    output_path = visualizer.plot_distribution_correlation(history_data)
                elif viz_type == 'average_value_center':
                    output_path = visualizer.plot_average_value_center(history_data)
                elif viz_type == 'cold_hot_evolution':
                    output_path = visualizer.plot_cold_hot_evolution(history_data)
                    
                if output_path:
                    # 显示图片
                    self._show_image(output_path)
                else:
                    messagebox.showerror("错误", f"生成{viz_type}可视化图表失败")
                    
            finally:
                db.disconnect()
                
        except Exception as e:
            messagebox.showerror("错误", f"生成可视化图表时出错: {str(e)}")

    def _show_image(self, image_path):
        """显示图片"""
        COLORS = _main_mod.COLORS
        try:
            # 创建图片查看窗口
            img_window = tk.Toplevel(self.root)
            img_window.title("可视化图表")
            img_window.geometry("800x600")
            img_window.configure(bg=COLORS['bg_primary'])
            
            # 添加滚动条
            canvas = tk.Canvas(img_window, bg=COLORS['bg_primary'])
            scrollbar_v = tk.Scrollbar(img_window, orient="vertical", command=canvas.yview)
            scrollbar_h = tk.Scrollbar(img_window, orient="horizontal", command=canvas.xview)
            canvas.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
            
            scrollbar_v.pack(side="right", fill="y")
            scrollbar_h.pack(side="bottom", fill="x")
            canvas.pack(side="left", fill="both", expand=True)
            
            # 在canvas中显示图片
            from PIL import Image, ImageTk
            img = Image.open(image_path)
            # 调整图片大小以适应窗口，但保持宽高比
            img_width, img_height = img.size
            canvas_width, canvas_height = 760, 560  # 留出滚动条空间
            
            # 计算缩放比例
            scale = min(canvas_width/img_width, canvas_height/img_height, 1.0)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            # 创建图片标签
            img_label = tk.Label(canvas, image=photo, bg=COLORS['bg_primary'])
            img_label.image = photo  # 防止被垃圾回收
            canvas.create_window(0, 0, anchor="nw", window=img_label)
            
            # 更新滚动区域
            canvas.configure(scrollregion=canvas.bbox("all"))
            
            # 添加关闭按钮
            close_btn = tk.Button(img_window, text="关闭", 
                                 command=img_window.destroy,
                                 bg=COLORS['accent_p5'], fg=WHITE,
                                 activebackground=COLORS.get('accent_p5_active', COLORS['accent_p5']),
                                 font=(UI_FONT, 10, 'bold'))
            close_btn.pack(pady=10)
            
        except ImportError:
            # 如果PIL不可用，则尝试使用其他方式显示
            messagebox.showinfo("提示", f"图表已保存至:\n{image_path}\n\n请使用图片查看器打开。")
        except Exception as e:
            messagebox.showerror("错误", f"显示图片时出错: {str(e)}")

    def _export_all_visualizations(self):
        """导出所有可视化图表"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                messagebox.showerror("错误", "数据库连接失败")
                return
                
            try:
                history_data = db.get_history_data(limit=100, order='ASC')
                if not history_data:
                    messagebox.showwarning("警告", "没有足够的历史数据生成可视化图表")
                    return
                    
                # 生成所有类型的图表
                viz_types = [
                    ('basic_trend', '基本走势趋势图'),
                    ('cold_hot_histogram', '冷热分布直方图'),
                    ('distribution_correlation', '分布关联矩阵'),
                    ('average_value_center', '和值重心曲线'),
                    ('cold_hot_evolution', '冷热演变趋势图')
                ]
                
                exported_files = []
                for viz_type, viz_name in viz_types:
                    try:
                        output_path = None
                        if viz_type == 'basic_trend':
                            output_path = visualizer.plot_basic_trend(history_data)
                        elif viz_type == 'cold_hot_histogram':
                            output_path = visualizer.plot_cold_hot_histogram(history_data)
                        elif viz_type == 'distribution_correlation':
                            output_path = visualizer.plot_distribution_correlation(history_data)
                        elif viz_type == 'average_value_center':
                            output_path = visualizer.plot_average_value_center(history_data)
                        elif viz_type == 'cold_hot_evolution':
                            output_path = visualizer.plot_cold_hot_evolution(history_data)
                            
                        if output_path:
                            exported_files.append(f"{viz_name}: {output_path}")
                    except Exception as e:
                        print(f"生成{viz_name}时出错: {e}")
                        
                if exported_files:
                    messagebox.showinfo("导出成功", 
                                      f"已成功导出{len(exported_files)}个可视化图表:\n\n" + 
                                      "\n".join(exported_files))
                else:
                    messagebox.showwarning("警告", "没有成功导出任何可视化图表")
                    
            finally:
                db.disconnect()
                
        except Exception as e:
            messagebox.showerror("错误", f"导出可视化图表时出错: {str(e)}")

    def _on_version_right_click(self, event):
        """右键点击版本表格行，弹出上下文菜单（回滚选项）。"""
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        # 获取点击位置的行
        item = tree.identify_row(event.y)
        if not item:
            return
        tree.selection_set(item)
        # 创建上下文菜单
        menu = tk.Menu(self.root, tearoff=0)
        values = tree.item(item)['values']
        version_tag = str(values[0]) if values else ''
        status = str(values[1]) if len(values) > 1 else ''
        # 空态占位行（暂无版本记录）不提供回滚/详情操作
        if version_tag.startswith('暂无'):
            return
        if status != 'active' and version_tag:
            menu.add_command(label=f"回滚到此版本 ({version_tag})",
                            command=lambda v=version_tag: self._on_evo_rollback_to(v))
        menu.add_command(label="查看版本详情",
                        command=lambda: self._on_version_row_double_click(
                            type('Event', (), {'y': tree.identify_row(event.y)})()))
        menu.post(event.x_root, event.y_root)

    def _init_evolution_engine(self):
        """初始化并启动自我进化引擎（启动即自动触发，后台非阻塞）。"""
        logger = _main_mod.logger
        if getattr(self, 'evolution', None) is not None:
            return
        try:
            from modules.self_evolution import SelfEvolutionEngine
            # 启动即自动触发完整进化（full=True），用户打开程序即可自动运行
            self.evolution = SelfEvolutionEngine(auto=True, auto_full=True)
            self.evolution.start()
            logger.info('[GUI] 自我进化引擎已初始化并启动，注册轮询定时器')
            self.root.after(300, self._refresh_evolution_versions)
            # GUI-04：调度器开启时注册 poll_evolution 由 tick 循环触发；
            # 回退路径保留原 after(200) 自调度链
            if self._ui_scheduler is not None:
                self._ui_scheduler.register('poll_evolution', self._poll_evolution,
                                            interval_ms=0)
            else:
                self.root.after(200, self._poll_evolution)
        except Exception as e:
            logger.warning('自我进化引擎初始化失败: %s', e)
            import traceback
            logger.warning(traceback.format_exc())

    def _poll_evolution(self):
        """轮询自我进化引擎消息队列（每 200ms），实时刷新 UI（主线程安全）。"""
        logger = _main_mod.logger
        # v3.64: 关闭时不再继续调度，防止 destroy() 后 TclError 白屏
        if getattr(self, '_poll_evolution_cancelled', False):
            self._poll_evolution_handle = None
            return
        # v3.65: 窗口已销毁则直接退出，防止访问已释放的 Tcl 资源
        try:
            _ = self.root.winfo_exists()
        except Exception:
            self._poll_evolution_handle = None
            return
        eng = getattr(self, 'evolution', None)
        _consumed = False  # GUI-04：本次轮询是否真正消费到消息（驱动退避恢复）
        if eng is not None:
            try:
                while True:
                    msg = eng.queue.get_nowait()
                    self._handle_evolution_msg(msg)
                    _consumed = True
            except queue.Empty:
                pass
            except Exception as e:
                logger.warning('[GUI] _poll_evolution 消费消息时异常: %s', e)
        # GUI-04：消费到消息即业务事件，重置空闲退避计时
        if _consumed and self._ui_scheduler is not None:
            self._ui_scheduler.notify_event()
        # 保存句柄以便关闭时精确 cancel。
        # GUI-04：调度器开启时不再自调度，由 tick 循环按间隔触发
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._poll_evolution_handle = self.root.after(200, self._poll_evolution)

    def _poll_cli_log(self):
        """轮询命令行日志队列（GUI-04：由 _ui_scheduler 按 200ms/退避 500ms 触发）。

        实时渲染到「命令行输出」标签页（主线程安全，永不阻塞）。
        GUI-04 优化：
          1. 空闲期（has_pending 为假）零 Tk 调用直接返回 —— A11「渲染类 0 次」；
          2. 单次 drain 的全部行合并为**一次** txt.insert 批调用提交
             （Tcl insert 支持 chars/tagList 交替段，保持日志时间顺序），
             替代逐行 insert —— A12「1000 行 ≤ 5 次批插入」实际 1 次；
          3. 消费到日志行即通知调度器业务事件到达，恢复 200ms 活跃轮询间隔。
        """
        if getattr(self, '_poll_cli_log_cancelled', False):
            self._poll_cli_log_handle = None
            return
        try:
            if self.root.winfo_exists() is False:
                self._poll_cli_log_handle = None
                return
        except Exception:
            self._poll_cli_log_handle = None
            return
        txt = getattr(self, 'cli_log_text', None)
        buf = getattr(self, '_cli_log_buffer', None)
        if txt is not None:
            try:
                from modules.cli_log_sink import drain_cli_lines, has_pending
                # 空闲期无待渲染行：不 drain、不触 Tk（A11 渲染类 0 次）。
                # 注意：此处不得提前 return —— 回退路径（调度器关闭）仍需
                # 走到方法尾部完成 after(200) 自调度续期。
                lines = drain_cli_lines() if has_pending() else []
                if lines:
                    # 业务事件到达 → 重置空闲退避计时，恢复 200ms 活跃轮询间隔
                    if self._ui_scheduler is not None:
                        self._ui_scheduler.notify_event()
                    kw = (getattr(self, '_cli_filter_var', None).get()
                          if getattr(self, '_cli_filter_var', None) is not None
                          else '').strip().lower()
                    # 过滤 + 内存缓冲逐行处理（不触 Tk），渲染合并为一次批 insert：
                    # Tk Text.insert(index, chars, tagList, chars, tagList, ...)
                    # 支持文本段与标签列表交替，天然保持日志原有顺序。
                    segments = []  # type: list
                    for tag, line in lines:
                        if buf is not None:
                            buf.append((tag, line))
                            if len(buf) > 5000:
                                del buf[:len(buf) - 5000]
                        # 有关键字时仅渲染匹配行；无关键字渲染全部
                        if not kw or kw in line.lower():
                            segments.append(line + '\n')
                            segments.append((tag,))
                    if segments:
                        txt.insert(tk.END, *segments)
                    # 缓冲上限裁剪（防止无限增长）
                    _count = int(txt.index('end-1c').split('.')[0])
                    if _count > 5000:
                        txt.delete('1.0', f'{_count - 5000}.0')
                    if getattr(self, '_cli_autoscroll', True):
                        txt.see(tk.END)
            except Exception as e:
                import logging as _lgc
                _lgc.getLogger(__name__).warning('[GUI] _poll_cli_log 渲染异常: %s', e)
        # GUI-04：调度器开启时不再自调度，由 tick 循环按间隔触发
        if getattr(self, '_ui_scheduler', None) is not None:
            return
        self._poll_cli_log_handle = self.root.after(200, self._poll_cli_log)

    def _apply_cli_filter(self, *_):
        """关键字过滤：重建命令行输出文本框，仅展示匹配行（大小写不敏感）。

        复用内存缓冲 ``_cli_log_buffer``，避免对原始队列重复消费。
        """
        txt = getattr(self, 'cli_log_text', None)
        buf = getattr(self, '_cli_log_buffer', None)
        if txt is None or buf is None:
            return
        try:
            kw = (getattr(self, '_cli_filter_var', None).get()
                  if getattr(self, '_cli_filter_var', None) is not None
                  else '').strip().lower()
            txt.config(state=tk.NORMAL)
            txt.delete('1.0', tk.END)
            for tag, line in buf:
                if not kw or kw in line.lower():
                    txt.insert(tk.END, line + '\n', tag)
            if getattr(self, '_cli_autoscroll', True):
                txt.see(tk.END)
        except Exception:
            pass

    def _cli_log_context_menu(self, event):
        """右键菜单：复制选中 / 复制全部 / 清空（Phase 4）。"""
        txt = getattr(self, 'cli_log_text', None)
        if txt is None:
            return
        try:
            menu = tk.Menu(self.root, tearoff=0)
            try:
                sel = txt.selection_get()
            except Exception:
                sel = ''
            if sel:
                menu.add_command(label="复制选中", command=lambda: self._cli_copy(sel))
            menu.add_command(label="复制全部",
                             command=lambda: self._cli_copy(txt.get('1.0', tk.END)))
            menu.add_separator()
            menu.add_command(label="清空", command=self._clear_cli_log)
            menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _cli_copy(self, text):
        """将文本写入系统剪贴板（失败静默）。"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def _clear_cli_log(self):
        """清空命令行输出文本框（同步清空关键字过滤缓冲）。"""
        if getattr(self, 'cli_log_text', None) is not None:
            try:
                self.cli_log_text.delete('1.0', tk.END)
                self.cli_log_text.insert(tk.END, "> 日志已清空。\n", 'dim')
                if getattr(self, '_cli_log_buffer', None) is not None:
                    self._cli_log_buffer.clear()
            except Exception:
                pass

    def _handle_evolution_msg(self, m):
        """将引擎消息渲染到自我进化标签页（v3.55 增强版：支持 metrics/phase 消息）。"""
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        t = m.get('type')
        try:
            if t == 'status':
                txt = m.get('text', '')
                if hasattr(self, 'evo_tab_status'):
                    self.evo_tab_status.config(text=txt)
                # 更新状态徽章
                if hasattr(self, 'evo_status_badge'):
                    if '进行中' in txt or '训练' in txt or '运行' in txt:
                        self.evo_status_badge.config(
                            text="● 运行中",
                            fg=COLORS['accent_warning_light'],
                            bg=COLORS['bg_input'])
                    elif '完成' in txt or '就绪' in txt:
                        self.evo_status_badge.config(
                            text="● 就绪",
                            fg=COLORS['accent_p5_light'],
                            bg=COLORS['bg_input'])
                    else:
                        self.evo_status_badge.config(
                            text="● " + txt[:8],
                            fg=COLORS['accent_ai_bright'],
                            bg=COLORS['bg_input'])
            elif t == 'progress':
                v = m.get('value', 0)
                if hasattr(self, 'evo_tab_progress'):
                    self.evo_tab_progress['value'] = v
            elif t == 'log':
                lvl = m.get('level', 'info')
                tag = lvl if lvl in ('section', 'success', 'warning', 'error', 'info') else 'info'
                if hasattr(self, 'evo_log'):
                    self.evo_log.insert(tk.END, m.get('text', '') + "\n", tag)
                    self.evo_log.see(tk.END)
            elif t == 'stage':
                idx = m.get('index', 0)
                total = m.get('total', 6)
                name = m.get('name', '')
                if hasattr(self, 'evo_tab_status'):
                    # 统一文案模板（v5.0 §2.1.3）：阶段 N/M · 名称
                    self.evo_tab_status.config(
                        text=ui_styles.format_stage_text(idx, total, name))
                # 更新阶段指示器
                if hasattr(self, '_update_phase_indicator'):
                    self._update_phase_indicator(idx)
            elif t == 'evolve_trigger':
                # v5.0 §7.3：开奖回填后的进化触发判定必须可见，不得静默跳过
                triggered = bool(m.get('triggered'))
                issue = m.get('issue', '—')
                reason = m.get('reason') or m.get('reason_code', '')
                tag = 'success' if triggered else 'info'
                icon = '⚡' if triggered else '⏸'
                if hasattr(self, 'evo_log'):
                    self.evo_log.insert(tk.END,
                                        f"{icon} 进化触发判定（期号 {issue}）："
                                        f"{'已触发轻量进化' if triggered else '跳过'} — {reason}\n",
                                        tag)
                    self.evo_log.see(tk.END)
            elif t == 'version':
                self._refresh_evolution_versions()
                d = m.get('data', {})
                if hasattr(self, 'evo_overview_var'):
                    self.evo_overview_var.set(
                        f"当前版本: {d.get('version_tag', '')} | 状态: {d.get('status', '')}\n{d.get('note', '')}")
            elif t == 'done':
                self._refresh_evolution_versions()
                if hasattr(self, 'evo_status_badge'):
                    self.evo_status_badge.config(
                        text="● 就绪",
                        fg=COLORS['accent_p5_light'],
                        bg=COLORS['bg_input'])
                if hasattr(self, '_update_phase_indicator'):
                    self._update_phase_indicator(5)  # 完成阶段
            elif t == 'metrics':
                # 更新指标磁贴
                hist_count = m.get('history_count')
                latest_issue = m.get('latest_issue')
                if hist_count is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 4:
                    self.evo_metric_tiles[0].config(text=f"{hist_count} 期")
                if latest_issue and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 2:
                    self.evo_metric_tiles[1].config(text=str(latest_issue))
            elif t == 'tuning_perf':
                # 更新调优性能指标
                elapsed_ms = m.get('elapsed_ms')
                if elapsed_ms is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 4:
                    if elapsed_ms < 1000:
                        self.evo_metric_tiles[3].config(text=f"{elapsed_ms:.0f}ms")
                    else:
                        self.evo_metric_tiles[3].config(text=f"{elapsed_ms/1000:.1f}s")
            elif t == 'hitrate':
                # 更新命中率指标（Top-4 命中）
                top4 = m.get('top4')
                if top4 is not None and hasattr(self, 'evo_metric_tiles') and len(self.evo_metric_tiles) >= 3:
                    self.evo_metric_tiles[2].config(text=f"{top4:.1f}%")
            else:
                logger.debug('[GUI] _handle_evolution_msg 未知消息类型: %s', t)
        except Exception as e:
            logger.warning('[GUI] _handle_evolution_msg 处理消息异常(type=%s): %s', t, e)

    def _on_version_row_double_click(self, event):
        """双击版本表格行，弹出该版本的详细信息窗口（v3.55 增强版）。"""
        COLORS = _main_mod.COLORS
        _safe_grab_set = _main_mod._safe_grab_set
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        item = tree.item(sel[0])
        values = item.get('values', [])
        if not values:
            return
        version_tag = str(values[0])
        status = str(values[1])
        # 空态占位行（暂无版本记录）不是真实版本，双击不弹详情
        if version_tag.startswith('暂无'):
            return
        top1 = values[2] if len(values) > 2 else '—'
        top3 = values[3] if len(values) > 3 else '—'
        top5 = values[4] if len(values) > 4 else '—'
        parent = str(values[5]) if len(values) > 5 else '—'
        created = str(values[6]) if len(values) > 6 else '—'

        # 获取完整版本数据以展示调优详情
        eng = getattr(self, 'evolution', None)
        params_json = {}
        tuning_info = {}
        note_text = ''
        if eng:
            try:
                all_versions = eng.get_versions(limit=100)
                for v in all_versions:
                    if v.get('version_tag') == version_tag:
                        params_json = v.get('params', {}) or {}
                        tuning_info = params_json.get('tuning', {}) or {}
                        note_text = v.get('note', '')
                        break
            except Exception:
                pass

        # 构建详情文本
        detail_lines = [
            f"版本标签: {version_tag}",
            f"状态: {status}",
            f"Top-1 命中率: {top1}%",
            f"Top-3 命中率: {top3}%",
            f"Top-5 命中率: {top5}%",
            f"父版本: {parent}",
            f"创建时间: {created}",
            "",
            "─" * 40,
            "",
        ]

        # 参数信息
        if params_json:
            weights = params_json.get('weights', {})
            lookback = params_json.get('lookback', '—')
            detail_lines.append("【融合权重】")
            for algo, w in sorted(weights.items(), key=lambda x: -x[1]):
                detail_lines.append(f"  {algo}: {w:.4f}")
            detail_lines.append(f"lookback: {lookback}")
            detail_lines.append("")

        # 调优性能
        if tuning_info:
            detail_lines.append("【调优性能】")
            detail_lines.append(f"  评估候选: {tuning_info.get('candidates_evaluated', '—')} 组")
            detail_lines.append(f"  组件缓存命中: {tuning_info.get('cache_hits', '—')} 次")
            detail_lines.append(f"  组件缓存未命中: {tuning_info.get('cache_misses', '—')} 次")
            elapsed = tuning_info.get('elapsed_ms')
            if elapsed:
                detail_lines.append(f"  调优耗时: {elapsed:.0f}ms")
            detail_lines.append(f"  是否优化: {'是' if tuning_info.get('improved') else '否'}")
            detail_lines.append("")

        # 备注信息
        if note_text:
            detail_lines.append("【备注】")
            detail_lines.append(note_text)

        detail_text = "\n".join(detail_lines)

        # 显示详情弹窗
        win = tk.Toplevel(self.root)
        win.title(f"版本详情: {version_tag}")
        win.geometry("520x480")
        win.resizable(False, False)
        win.transient(self.root)
        _safe_grab_set(win)

        # 居中窗口
        win.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        win_x = root_x + (root_w - 520) // 2
        win_y = root_y + (root_h - 480) // 2
        win.geometry(f"+{win_x}+{win_y}")

        bg = COLORS['bg_card']
        fg_pri = COLORS['text_primary']
        fg_sec = COLORS['text_secondary']

        # 标题栏
        hdr = tk.Frame(win, bg=COLORS['accent_ai'], height=36)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="进化版本详情", font=(UI_FONT, 10, 'bold'),
                 bg=COLORS['accent_ai'], fg=WHITE).pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=(UI_FONT, 9),
                  bg=COLORS['accent_ai'], fg=WHITE, relief='flat',
                  command=win.destroy).pack(side=tk.RIGHT, padx=10, pady=6)

        # 内容区
        txt = tk.Text(win, wrap=tk.WORD, font=(MONO_FONT, 9),
                      bg=bg, fg=fg_pri, relief='flat',
                      padx=14, pady=12, state=tk.NORMAL)
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 0))
        txt.insert(tk.END, detail_text)
        txt.config(state=tk.DISABLED)

        # 底部按钮区
        btn_frame = tk.Frame(win, bg=COLORS['bg_card'])
        btn_frame.pack(fill=tk.X, padx=12, pady=(8, 12))

        # 回滚按钮（非 active 状态显示）
        if status != 'active':
            tk.Button(btn_frame, text="回滚到此版本", font=(UI_FONT, 9),
                      bg=COLORS['accent_warning'], fg=WHITE, relief='flat',
                      activebackground=COLORS['accent_warning_active'],
                      command=lambda vt=version_tag: self._on_evo_rollback_to(vt)).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(btn_frame, text="关闭", font=(UI_FONT, 9),
                  bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                  activebackground=COLORS['accent_p5_active'],
                  command=win.destroy).pack(side=tk.RIGHT)

    def _on_evo_rollback(self):
        """回滚到版本树中当前选中的版本，失败时显示 toast 提示。"""
        COLORS = _main_mod.COLORS
        tree = getattr(self, 'evo_version_tree', None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            self._show_toast('请先在版本表中选择要回滚的版本', COLORS['warning'], duration=2000)
            return
        item = tree.item(sel[0])
        values = item.get('values', [])
        if not values:
            self._show_toast('未选中有效版本', COLORS['warning'], duration=1500)
            return
        version_tag = str(values[0])
        status = str(values[1])
        # 空态占位行不可回滚
        if version_tag.startswith('暂无'):
            self._show_toast('暂无可回滚的版本', COLORS['warning'], duration=1500)
            return
        if status == 'active':
            self._show_toast('当前已是 active 版本，无需回滚', COLORS['accent_info'], duration=1500)
            return
        self._on_evo_rollback_to(version_tag)

    def _on_evo_rollback_to(self, version_tag):
        """回滚到指定版本（支持右键菜单调用）。"""
        COLORS = _main_mod.COLORS
        ok = False
        err = None
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            if hasattr(eng, 'rollback_to_version'):
                res = eng.rollback_to_version(version_tag)
                ok = bool(res.get('ok'))
                err = res.get('error')
        except Exception as e:  # noqa: BLE001
            err = str(e)
        if ok:
            self._show_toast(f'已回滚到 {version_tag}', COLORS['success'], duration=2000)
            self._refresh_evolution_versions()
        else:
            self._show_toast(f'回滚失败: {err or "未知错误"}', COLORS['accent_danger_light'], duration=2500)

    def _on_evo_proposals(self):
        """弹出改进建议窗口（从 engine.get_proposals 读取最新建议列表）。"""
        COLORS = _main_mod.COLORS
        rows = []
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            if hasattr(eng, 'get_proposals'):
                rows = eng.get_proposals(limit=50) or []
        except Exception:  # noqa: BLE001
            rows = []
        top = tk.Toplevel(self.root)
        top.title("进化改进建议")
        top.configure(bg=COLORS['bg_primary'])
        top.geometry("680x360")
        cols = ('时间', '分类', '优先级', '标题', '状态')
        tree = ttk.Treeview(top, columns=cols, show='headings')
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=120 if c != '标题' else 240, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))
        # 会话级列宽记忆：窗口每次打开都是新 Treeview，需从内存恢复上次拖动结果
        self._bind_tree_width_memory(tree, 'evolution_suggestions')
        for r in rows:
            tree.insert('', tk.END, values=(
                str(r.get('created_at', ''))[:19],
                r.get('category', ''),
                r.get('priority', ''),
                r.get('title', ''),
                r.get('status', ''),
            ))
        if not rows:
            tree.insert('', tk.END, values=('暂无建议', '', '', '', ''))

    def _show_evolution_link_state(self):
        """弹出联动状态窗口（展示与「开始分析」的数据同步情况）。"""
        COLORS = _main_mod.COLORS
        _safe_grab_set = _main_mod._safe_grab_set
        try:
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            link_state = eng.get_link_state() if hasattr(eng, 'get_link_state') else {}
        except Exception:
            link_state = {}

        win = tk.Toplevel(self.root)
        win.title("联动状态")
        win.configure(bg=COLORS['bg_primary'])
        win.geometry("480x420")
        win.resizable(False, False)
        win.transient(self.root)
        _safe_grab_set(win)

        # 居中
        win.update_idletasks()
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        win.geometry(f"+{rx + (rw - 480) // 2}+{ry + (rh - 420) // 2}")

        # 标题栏
        hdr = tk.Frame(win, bg=COLORS['accent_ai'], height=36)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="联动状态", font=(UI_FONT, 10, 'bold'),
                 bg=COLORS['accent_ai'], fg=WHITE).pack(side=tk.LEFT, padx=12, pady=9)
        tk.Button(hdr, text="✕", font=(UI_FONT, 9),
                  bg=COLORS['accent_ai'], fg=WHITE, relief='flat',
                  command=win.destroy).pack(side=tk.RIGHT, padx=10, pady=6)

        # 内容区（卡片式布局）
        content = tk.Frame(win, bg=COLORS['bg_primary'])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # 构建展示文本
        # v5.0 §7.3：命中/候选判定统一走引擎的只读建议接口，避免 GUI 自算两套口径。
        # 注意 `or {}` 而非 dict.get 默认值：键存在但值为 None 时不会触发默认值。
        lines = []
        lines.append(f"最后分析期号: {link_state.get('last_analysis_issue') or '—'}")
        last_pred = link_state.get('last_prediction') or {}
        lines.append(f"预测目标期号: {last_pred.get('target_issue') or '—'}"
                     if isinstance(last_pred, dict) else "预测目标期号: —")
        lines.append(f"等待回填: {'是' if link_state.get('pending_verification') else '否'}")
        lines.append(f"最后同步时间: {link_state.get('last_sync_ts') or '—'}")
        lines.append("")
        lines.append("─" * 30)
        lines.append("")

        # 最新验证结果
        last_verif = link_state.get('last_verification') or {}
        if isinstance(last_verif, dict) and last_verif:
            lines.append(f"期号: {last_verif.get('issue') or '—'}")
            lines.append(f"Top-1 命中: {last_verif.get('top1')}%")
            lines.append(f"Top-3 命中: {last_verif.get('top3')}%")
            lines.append(f"Top-5 命中: {last_verif.get('top5')}%")
            lines.append(f"验证时间: {last_verif.get('ts') or '—'}")
        else:
            lines.append("暂无验证记录")

        # 最佳候选（v5.0 §7.3：附诚实结论，不暗示可提升命中率）
        advisory = {}
        try:
            if eng is not None and hasattr(eng, 'get_latest_advisory'):
                advisory = eng.get_latest_advisory() or {}
        except Exception:  # noqa: BLE001
            advisory = {}
        best = link_state.get('best_candidate') or {}
        if isinstance(best, dict) and best:
            lines.append("")
            lines.append("─" * 30)
            lines.append("")
            lines.append(f"候选版本: {best.get('version_tag') or '—'}")
            metrics = best.get('metrics') or {}
            lines.append(f"Top-1: {metrics.get('top1')}%  "
                         f"Top-3: {metrics.get('top3')}%  "
                         f"Top-5: {metrics.get('top5')}%")
            if advisory.get('has_better_candidate'):
                lines.append("结论: 综合评分高于基线（walk-forward 口径）")
            elif advisory.get('baseline_metrics'):
                lines.append("结论: 未严格超越基线，未注入生产权重")
            else:
                lines.append("结论: 暂无可比基线，无法判定优劣")

        # 诚实边界声明（常驻，不随状态消失）
        lines.append("")
        lines.append("─" * 30)
        lines.append(f"⚠ {advisory.get('honest_note') or '排列5 为公平摇号，历史统计不改变随机概率。'}")

        txt = "\n".join(lines)

        text_widget = tk.Text(content, wrap=tk.WORD, font=(MONO_FONT, 9),
                              bg=COLORS['bg_card'], fg=COLORS['text_primary'],
                              relief='flat', padx=12, pady=10, state=tk.NORMAL)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, txt)
        text_widget.config(state=tk.DISABLED)

        # 关闭按钮
        tk.Button(win, text="关闭", font=(UI_FONT, 9),
                  bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                  activebackground=COLORS['accent_p5_active'],
                  command=win.destroy).pack(pady=(8, 0))

    def _refresh_evolution_versions(self):
        """刷新进化版本表（供标签页与左侧卡片同步展示）。"""
        eng = getattr(self, 'evolution', None)
        tree = getattr(self, 'evo_version_tree', None)
        if eng is None or tree is None:
            return
        try:
            rows = eng.get_versions(limit=50)
            tree.delete(*tree.get_children())
            for idx, r in enumerate(rows):
                m = r.get('metrics', {}) or {}
                status = r.get('status', '')
                tag = 'odd' if idx % 2 else 'even'
                if status == 'active':
                    tag += ' active'
                elif status == 'rolledback':
                    tag += ' rolledback'
                elif status == 'trial':
                    tag += ' trial'
                tree.insert('', tk.END, values=(
                    r.get('version_tag', ''),
                    r.get('status', ''),
                    m.get('top1') if m.get('top1') is not None else '—',
                    m.get('top3') if m.get('top3') is not None else '—',
                    m.get('top5') if m.get('top5') is not None else '—',
                    r.get('parent_tag', '') or '—',
                    str(r.get('created_at', ''))[:19],
                ), tags=(tag,))
            if not rows:
                # v5.3 GUI-UX 第二批：无版本时给出空态说明，避免表格空白
                tree.insert('', tk.END, values=(
                    '暂无版本记录', '等待首次进化', '—', '—', '—', '—',
                    '点击「启动进化」或等待自动进化',
                ), tags=('odd',))
            if rows:
                top = rows[0]
                if hasattr(self, 'evo_overview_var'):
                    self.evo_overview_var.set(
                        f"当前版本: {top.get('version_tag', '')} | 状态: {top.get('status', '')}\n"
                        f"Top-1={top.get('metrics', {}).get('top1')} / "
                        f"Top-3={top.get('metrics', {}).get('top3')} / "
                        f"Top-5={top.get('metrics', {}).get('top5')}")
        except Exception:
            pass

    def _export_evolution_versions(self):
        """导出进化版本列表为 JSON 文件（reports/ 目录）。"""
        COLORS = _main_mod.COLORS
        eng = getattr(self, 'evolution', None)
        if eng is None:
            self._init_evolution_engine()
            eng = self.evolution
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(REPORTS_DIR, f'evolution_versions_{ts}.json')
        n = eng.export_versions(path)
        if n > 0:
            self._show_toast(f'已导出 {n} 条进化版本', COLORS['success'])
        else:
            self._show_toast('暂无可导出的版本', COLORS['warning'])

    def _clear_evolution_log(self):
        """清空自我进化日志文本框。"""
        try:
            self.evo_log.delete(1.0, tk.END)
        except Exception:
            pass

    def _on_system_health_diagnostic(self):
        """触发系统健康诊断：离线评估命中率基线 / 权重漂移 / 进化版本对比 / 学习闭环覆盖率，
        并把 Markdown 报告在进化日志区展示。

        调用路径：
            modules.system_health.run_diagnostic(days=60, limit=200) -> Dict
              ├─ metrics.top1_rate_pct / top3_rate_pct / top5_rate_pct  (诚实对照)
              ├─ metrics.weight_drifts                                 (冻结权重偏离度)
              ├─ metrics.version_summary                               (active/trial 统计)
              ├─ attribution_coverage                                  (per-algo 归因覆盖率)
              └─ suggestions                                           (可执行优化建议清单)
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            # 惰性导入，避免启动时加载重型依赖
            from modules.system_health import run_diagnostic, render_markdown
            # 使用数据库的 lazy connect：DB 不可用时给出明确提示
            eng = getattr(self, 'evolution', None)
            if eng is None:
                self._init_evolution_engine()
                eng = self.evolution
            self._show_toast('正在生成系统健康诊断报告…', COLORS['accent_info'])
            report = run_diagnostic(days=60, limit=200)
            if not report or report.get('status') == 'error':
                self._show_toast('诊断失败：数据库不可用', COLORS['accent_danger_light'])
                return
            md = render_markdown(report)
            # 追加到进化日志末尾（保留历史）
            self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
            self.evo_log.insert(tk.END, f'[系统健康诊断] {report.get("ts", "")} | {report.get("status", "")}\n', 'section')
            self.evo_log.insert(tk.END, f'{md}\n', 'info')
            self.evo_log.see(tk.END)
            paths = report.get('_paths', {})
            json_p = paths.get('json', '')
            md_p = paths.get('markdown', '')
            detail = []
            if json_p:
                detail.append(f'JSON={json_p}')
            if md_p:
                detail.append(f'MD={md_p}')
            self._show_toast(
                f'健康分 {report.get("summary","")}'
                + (f' | 已保存 {", ".join(detail)}' if detail else ''),
                COLORS['success'] if report.get('status') == 'healthy' else COLORS['warning'],
                duration=4000,
            )
        except Exception as e:
            logger.warning('[GUI] 系统健康诊断异常: %s', e, exc_info=True)
            self._show_toast(f'诊断异常: {e}', COLORS['accent_danger_light'])

    def _on_auto_evaluator_scan(self):
        """触发自我升级自动评估（§4.1 模块自诊断 + §4.4 结果验证/优化建议）。

        运行 8 个领域评估器（数据层/预测性能/算法权重/缓存/进化引擎/模块依赖/AI模型/代码质量），
        输出综合评分 + 分级优化建议，把 Markdown 报告在进化日志区展示并落盘 reports/。

        评估器各自打开 DB，全量评估可能耗时数十秒，故后台线程运行，经 root.after 回投
        主线程渲染，永不阻塞 GUI。DB 不可用时各评估器优雅降级为 critical（不崩溃）。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            from modules.auto_evaluator import ProjectAutoEvaluator
        except Exception as e:
            logger.warning('[GUI] 自动评估模块导入失败: %s', e)
            self._show_toast(f'自动评估模块不可用: {e}', COLORS['accent_danger_light'])
            return

        self._show_toast('正在运行自我升级自动评估…', COLORS['accent_info'])

        import queue as _queue
        q = _queue.Queue()

        def _worker():
            # 后台线程：只做非 Tk 操作（评估/落盘），结果经队列投递；
            # 绝不直接调 Tk API（跨线程调用 Tk 会抛 "main thread is not in main loop"）。
            try:
                evaluator = ProjectAutoEvaluator()
                report = evaluator.run_full_evaluation()
                md = evaluator.render_markdown(report)
                saved_path = ''
                try:
                    saved_path = evaluator.save_report(report)
                except Exception:
                    saved_path = ''
                q.put(('ok', report, md, saved_path))
            except Exception as e:
                q.put(('error', e))

        threading.Thread(target=_worker, daemon=True).start()

        # 主线程轮询队列：结果就绪后渲染并停止；未就绪每 200ms 再查一次。
        def _poll():
            handled = False
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == 'ok':
                        _, report, md, saved_path = item
                        self._render_auto_eval_report(report, md, saved_path)
                        handled = True
                    else:
                        err = item[1]
                        logger.warning('[GUI] 自动评估异常: %s', err, exc_info=True)
                        self._show_toast(f'自动评估异常: {err}',
                                         COLORS['accent_danger_light'])
                        handled = True
            except _queue.Empty:
                pass
            if not handled:
                self.root.after(200, _poll)

        _poll()

    def _on_update_history_log(self):
        """§4.2 可追溯：在进化日志区展示「更新记录台账」（可查询 + 可导出）。

        数据来源：进化引擎 `SelfEvolutionEngine.get_update_history()`（本地
        `data/update_ledger.jsonl`，DB 不可达亦可读）。记录含 触发原因/执行结果/
        校验/是否自动回滚，倒序展示（最新在前）；点按时顺带把台账导出到
        reports/ 供问题回溯。无记录时给出友好提示（首次运行属正常）。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        eng = getattr(self, 'evolution', None)
        if eng is None:
            self._init_evolution_engine()
            eng = self.evolution
        if eng is None or not hasattr(eng, 'get_update_history'):
            self._show_toast('进化引擎不可用，无法读取更新台账',
                             COLORS['accent_danger_light'])
            return
        try:
            rows = eng.get_update_history(limit=50)
        except Exception as e:
            logger.warning('[GUI] 读取更新台账异常: %s', e, exc_info=True)
            self._show_toast(f'读取更新台账失败: {e}', COLORS['accent_danger_light'])
            return

        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(tk.END,
                            f'[更新流程台账] 共 {len(rows)} 条（最新在前）\n', 'section')
        if not rows:
            self.evo_log.insert(tk.END,
                '（暂无更新记录 —— 进化产版/手动回滚/自动回滚后会在此累积；'
                '属正常初始态）\n', 'info')
        else:
            for i, r in enumerate(rows, 1):
                tag = r.get('version_tag', '—')
                action = r.get('action', '—')
                ts = r.get('timestamp', '')
                result = r.get('execution_result', '—')
                auto_rb = ' [自动回滚]' if r.get('auto_rolled_back') else ''
                reason = r.get('trigger_reason', '')
                restored = r.get('restored_tag')
                line = (f'  {i:>2}. [{ts}] {action} → {tag} '
                        f'结果={result}{auto_rb}')
                if restored:
                    line += f'（回退至 {restored}）'
                self.evo_log.insert(tk.END, line + '\n', 'info')
                if reason:
                    self.evo_log.insert(tk.END, f'       触发: {reason}\n', 'info')
                # 校验摘要（fatal 项高亮为 danger）
                val = r.get('validation') or {}
                checks = val.get('checks', {}) if isinstance(val, dict) else {}
                if checks:
                    bad = {k: s for k, s in checks.items() if s in ('fail', 'warn')}
                    detail_txt = '；'.join(f'{k}={s}' for k, s in bad.items())
                    key = 'warn' if bad else 'info'
                    self.evo_log.insert(
                        tk.END, f'       校验: {detail_txt or "全部通过"}\n', key)
        self.evo_log.see(tk.END)

        # 顺带导出到 reports/ 供回溯（§4.2.2(2) 可导出）
        export_path = ''
        try:
            ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            export_path = os.path.join(REPORTS_DIR, f'update_ledger_{ts_str}.json')
            eng.export_update_history(export_path)
        except Exception as e:
            logger.debug('[GUI] 导出更新台账跳过(非致命): %s', e)
            export_path = ''

        color = COLORS['success'] if rows else COLORS['accent_info']
        detail = f'，已导出 {export_path}' if export_path else ''
        self._show_toast(
            f'更新台账 {len(rows)} 条{detail}',
            color, duration=3000)

    def _on_perf_monitor_view(self):
        """§4.3 系统性能监控：在进化日志区展示性能指标摘要 + 告警状态。

        数据来源：``modules.perf_monitor`` 的全局单例（惰性启动 1Hz 守护线程
        采集 CPU/内存/响应/任务耗时，资源压力越限自动优化）。点按时：
          * 若监控线程未运行则按需启动（幂等）；
          * 渲染 get_summary()（各指标窗口统计 + 告警 + 健康度）到 evo_log；
          * psutil 不可用时标注「降级」，不误导用户。
        无 DB 依赖；异常绝不阻断 GUI。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            from modules.perf_monitor import (
                get_perf_monitor, start_perf_monitoring)
        except Exception as e:
            logger.warning('[GUI] 性能监控模块不可用: %s', e, exc_info=True)
            self._show_toast(f'性能监控不可用: {e}', COLORS['accent_danger_light'])
            return

        mon = get_perf_monitor()
        # 任务来源注入（TaskManager 鸭子类型：get_task_count / is_running /
        # get_running_tasks）。监控线程每采样周期读它统计任务类指标。
        try:
            mon.set_task_source(self.task_mgr)
        except Exception:
            pass
        # 按需启动（幂等；已运行则直接 True）
        try:
            start_perf_monitoring()
        except Exception as e:
            logger.debug('[GUI] 启动性能监控线程跳过(非致命): %s', e)

        # 采集一次新鲜指标（即便守护线程刚停，也保证按钮点按时有数据）
        try:
            summary = mon.get_summary()
        except Exception as e:
            logger.warning('[GUI] 读取性能摘要异常: %s', e, exc_info=True)
            self._show_toast(f'读取性能摘要失败: {e}', COLORS['accent_danger_light'])
            return

        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(
            tk.END,
            f'[系统性能监控] 采样 {summary.get("sample_count", 0)} 点 '
            f'| 健康度 {summary.get("health_score", "—")}/100\n', 'section')

        psutil_ok = bool(summary.get('psutil_available'))
        self.evo_log.insert(
            tk.END,
            ('psutil 已启用（CPU/内存指标可用）\n' if psutil_ok
             else 'psutil 不可用 → CPU/内存指标降级（任务/耗时类指标仍可用）\n'),
            'info')

        # 各指标窗口统计（最近窗口均值/峰值/当前值）
        metrics = summary.get('metrics', {}) or {}
        for key, st in metrics.items():
            cur = st.get('current')
            cur_txt = f'{cur}' if cur is not None else '—(降级/无数据)'
            avg = st.get('avg')
            avg_txt = f'{avg}' if avg is not None else '—'
            mx = st.get('max')
            mx_txt = f'{mx}' if mx is not None else '—'
            self.evo_log.insert(
                tk.END,
                f'   {key:<18} 当前={cur_txt}  均值={avg_txt}  '
                f'峰值={mx_txt}  (窗口 {st.get("window", 0)})\n', 'info')

        # 告警状态
        alerts = summary.get('active_alerts') or []
        if alerts:
            self.evo_log.insert(
                tk.END, f'   ⚠ 触发告警: {", ".join(alerts)}\n', 'warn')
        else:
            self.evo_log.insert(tk.END, '   ✓ 无活动告警\n', 'info')
        self.evo_log.see(tk.END)

        color = COLORS['success'] if not alerts else COLORS['accent_danger_light']
        detail = f'，告警 {len(alerts)} 项' if alerts else ''
        self._show_toast(
            f'性能监控 健康度 {summary.get("health_score", "—")}{detail}',
            color, duration=3000)

    def _auto_verify_result(self, pipeline_final, quick_final) -> None:
        """§4.4.3 自动自我验证：每次分析产出后登记一次验证。

        目的：方案 §4.4.3 要求「结果自我验证覆盖率达 100%」。此前验证只在用户
        手动点击「🔬 自我验证」时才发生，覆盖率实际取决于人工操作、并非 100%。
        这里在结果落库后自动登记一次验证，使覆盖率为**真实度量**。

        约束（对齐项目惯例 + 诚实边界）：
          * 只登记到 ResultSelfVerifier（供 §4.1 自诊断 / 自动评估报告消费），
            不弹窗、不渲染、不阻塞分析流程；
          * 绝不自动调整 v4.0 冻结权重（建议仅为建议型记录）；
          * 由 RESULT_VERIFIER_ENABLE 门控，任何异常一律静默（非致命）。
        """
        logger = _main_mod.logger
        try:
            import config as _cfg_av
            if not getattr(_cfg_av, 'RESULT_VERIFIER_ENABLE', True):
                return
            from modules.result_verifier import get_result_verifier
            rv = get_result_verifier()
        except Exception as e:  # 模块不可用 → 静默跳过，不影响分析
            logger.debug('[GUI] 自动自我验证不可用（非致命）: %s', e)
            return

        for cand in (pipeline_final, quick_final):
            if not isinstance(cand, dict) or not cand:
                continue
            try:
                fused = cand.get('fused_probabilities')
                tops = (cand.get('top_combinations')
                        or cand.get('recommended_combinations'))
                inner = cand.get('prediction') or cand.get('raw_prediction') or {}
                payload = {
                    'fused_probabilities': (fused if fused is not None
                                            else inner.get('fused_probabilities')),
                    'top_combinations': tops or inner.get('top_combinations'),
                    'target_issue': cand.get('current_issue') or cand.get('target_issue'),
                    'next_issue': cand.get('next_issue'),
                    'data_period': cand.get('data_period'),
                    'per_algo_top_predictions': (
                        cand.get('per_algo_top_predictions')
                        or inner.get('per_algo_top_predictions')),
                }
                out = rv.self_verify_result(payload, None)
                rv.generate_optimization_suggestions(out, None)
            except Exception as e:
                logger.debug('[GUI] 自动自我验证失败（非致命）: %s', e)
            break  # 每次分析只登记最优先的那一个结果

    def _on_result_self_verify(self):
        """§4.4 结果自我验证 + 分级优化建议：对最近一次流水线结果做一致性自检。

        数据来源：
          * 被验结果：最近一次流水线产物 ``_last_pipeline_final`` /
            ``_last_quick_final``（含 fused_probabilities / top_combinations /
            per_algo_top_predictions / next_issue），无产物时提示先「开始分析」。
          * 监控数据：ModelMonitor.get_current_status()（惰性导入，无则跳过）。

        校验维度（§4.4.2(1)）：命中率边界 / 概率归一 / 组合结构 / 窗口一致性 /
        数据一致性。据此生成分级（高/中/低）优化建议（§4.4.2(2)），渲染到 evo_log。
        诚实边界：建议仅对照随机基线、标注正常波动，**不**自动调整冻结权重。
        无 DB 依赖；异常绝不阻断 GUI。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            import config as _cfg3
            if not getattr(_cfg3, 'RESULT_VERIFIER_ENABLE', True):
                self._show_toast('结果自我验证已被配置关闭（RESULT_VERIFIER_ENABLE=False）',
                                 COLORS['accent_info'])
                return
            from modules.result_verifier import (
                get_result_verifier, RANDOM_BASELINE)
        except Exception as e:
            logger.warning('[GUI] 结果自我验证模块不可用: %s', e, exc_info=True)
            self._show_toast(f'结果自我验证不可用: {e}', COLORS['accent_danger_light'])
            return

        # ---- 收集被验结果（多个候选来源，取第一个能拿到可校验字段的）----
        candidates = [
            getattr(self, '_last_pipeline_final', None),
            getattr(self, '_last_quick_final', None),
        ]
        verify_result = None
        used_label = ''
        for cand in candidates:
            if isinstance(cand, dict) and cand:
                # final_report：fused/top 可能在 'prediction'(趋势) 或 'raw_prediction'
                fused = cand.get('fused_probabilities')
                tops = cand.get('top_combinations') or cand.get('recommended_combinations')
                if fused is None or tops:
                    inner = cand.get('prediction') or cand.get('raw_prediction') or {}
                    verify_result = {
                        'fused_probabilities': fused
                        if fused is not None else inner.get('fused_probabilities'),
                        'top_combinations': tops or inner.get('top_combinations'),
                        'target_issue': cand.get('current_issue') or cand.get('target_issue'),
                        'next_issue': cand.get('next_issue'),
                        'data_period': cand.get('data_period'),
                        'per_algo_top_predictions': cand.get('per_algo_top_predictions')
                        or inner.get('per_algo_top_predictions'),
                    }
                    used_label = '最近一次流水线结果'
                    break
        if verify_result is None:
            self._show_toast('暂无可验证的流水线结果，请先「开始分析」',
                             COLORS['accent_info'])
            return

        # ---- 监控数据（惰性导入；无则跳过命中率维度）----
        monitor_data = None
        try:
            from modules.model_monitor import get_model_monitor
            status = get_model_monitor().get_current_status()
            monitor_data = {
                'hit_rate': (status.get('latest_values') or {}).get('hit_rate'),
                'alert_states': status.get('alert_states', {}),
            }
        except Exception:
            monitor_data = None

        # ---- 执行自我验证 + 生成分级建议 ----
        rv = get_result_verifier()
        verify_out = rv.self_verify_result(verify_result, None)
        sug_out = rv.generate_optimization_suggestions(verify_out, monitor_data)

        # ---- 渲染到 evo_log（验证通过/失败 + 分级建议）----
        valid = bool(verify_out.get('valid'))
        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(
            tk.END,
            f'[结果自我验证] {used_label} | 结论={"✓ 通过" if valid else "✗ 未通过"} '
            f'| 基线 top1={RANDOM_BASELINE["top1"]:.0%}\n',
            'section')

        # 校验维度逐项
        checks = verify_out.get('checks', {}) or {}
        for dim, ok in checks.items():
            mark = '✓' if ok else '✗'
            key = 'info' if ok else 'warn'
            self.evo_log.insert(
                tk.END, f'   {mark} {dim}\n', key)
        for err in verify_out.get('errors', []):
            self.evo_log.insert(tk.END, f'       ✗ {err}\n', 'danger')

        # 分级建议
        by_priority = sug_out.get('by_priority', {}) or {}
        self.evo_log.insert(tk.END, '\n   ── 分级优化建议 ──\n', 'section')
        priority_label = {'high': '高优先级', 'medium': '中优先级', 'low': '低优先级'}
        for p in ('high', 'medium', 'low'):
            for s in by_priority.get(p, []) or []:
                self.evo_log.insert(
                    tk.END,
                    f'   [{priority_label[p]}] {s.get("title", "")}\n',
                    'danger' if p == 'high' else 'info')
                self.evo_log.insert(
                    tk.END, f'       依据: {s.get("rationale", "")}\n', 'info')
                self.evo_log.insert(
                    tk.END, f'       建议: {s.get("action", "")}\n', 'info')
                if not s.get('auto_safe', True):
                    self.evo_log.insert(
                        tk.END,
                        '       ⚠ 涉及权重/窗口调整，须人工经 '
                        'ONLINE_LEARNER_WRITEBACK 通道执行（本模块不自动改权重）\n',
                        'warn')
        if not any(by_priority.get(p) for p in ('high', 'medium', 'low')):
            self.evo_log.insert(tk.END, '   （暂无优化建议）\n', 'info')

        self.evo_log.insert(
            tk.END,
            f'\n   [诚实边界] {sug_out.get("honest_note", "")}\n', 'info')
        self.evo_log.see(tk.END)

        color = COLORS['success'] if valid else COLORS['accent_danger_light']
        n_sug = sum(len(v) for v in by_priority.values())
        self._show_toast(
            f'自我验证 {"通过" if valid else "未通过"}，建议 {n_sug} 条',
            color, duration=3000)

    def _show_search_dialog(self):
        """（已移除）运行日志已改为写入文件，搜索对话框停用。"""
        COLORS = _main_mod.COLORS
        self._show_toast(" 运行日志已改为文件记录，无需界面搜索", COLORS['accent_info'], duration=1500)

    def _refresh_db_status(self):
        """每30秒刷新数据库状态"""
        try:
            from modules.database import P5Database
            db = P5Database()
            db.connect()
            self.db_status_var.set("数据库：已连接")
        except Exception:
            self.db_status_var.set("数据库：未连接")
        self.root.after(30000, self._refresh_db_status)

    def _on_button_click(self, task_name, task_func):
        """按钮点击统一入口：检查任务状态后提交到后台线程（v3.36 优化版）

        增加超时保护 (v3.9): 如果后台任务超过 30 分钟无响应，视为卡死，
        弹出用户友好的提示（而非界面永久冻结）。
        """
        COLORS = _main_mod.COLORS
        if self.task_mgr.is_running():
            # 使用Toast提示代替阻塞弹窗，提升用户体验
            self._show_toast("当前有任务正在执行，请等待完成", COLORS['warning'], duration=2000)
            return

        # 点击按钮时即时反馈（按钮按下效果）
        # 注：由于lambda包装，无法直接获取按钮引用，改为状态栏提示
        self.status_var.set(f"即将开始: {task_name}")
        # overview_flush_pending removed = False  # 分析开始前重置概览刷新标志

        success = self.task_mgr.submit(task_func, task_name)
        if not success:
            messagebox.showwarning("提示", "任务提交失败，请重试")

        # 启动 liveness 看门狗（取代原"30分钟硬超时"）
        # 原逻辑在 30 分钟整无条件 kill 任何仍在跑的任务——但"全部50期"回测
        # 可能跑 17~58 分钟，会被误杀且按钮永久卡禁用。
        # 新逻辑：任务在跑且「连续 30 分钟无任何日志/进度输出」才视为真卡死而终止；
        # 正常推进（即使耗时很久）永不误杀。看门狗每 60s 自检、自动续排/停止。
        self.root.after(60 * 1000, self._liveness_watchdog)

    def _liveness_watchdog(self):
        """基于「活动心跳」的看门狗，取代原 30 分钟硬超时。

        仅当任务仍在运行「且」距最近一次日志/进度输出已超过 LIVENESS_LIMIT 秒时，
        才判定为卡死（如 AI 接口无响应挂死），执行终止并恢复按钮；
        正常但耗时的任务（如全部50期回测，逐期有日志）不会被误杀。
        """
        LIVENESS_LIMIT = 30 * 60  # 30 分钟无任何输出 → 视为卡死
        if not self.task_mgr.is_running():
            return  # 任务已结束，看门狗自动退役（不再续排）
        if time.time() - self.task_mgr._last_activity <= LIVENESS_LIMIT:
            # 仍在正常推进，续排下一轮自检
            self._liveness_watchdog_handle = self.root.after(60 * 1000, self._liveness_watchdog)
            return
        # —— 判定为卡死：终止并恢复 UI ——
        _name = self._current_task_name
        self.task_mgr.log(f"\n 任务「{_name}」疑似卡死（连续 {LIVENESS_LIMIT // 60} 分钟无输出）")
        self.task_mgr.log("  可能原因: AI接口无响应 / 网络中断 / 死锁")
        self.task_mgr.log("  已强制终止并恢复界面，请检查网络后重试")
        self.task_mgr.cancel()
        # 关键修复：原 cancel() 只置 _running=False 却不恢复按钮，
        # 会导致界面按钮永久禁用；此处显式调用完成回调以恢复交互。
        self._on_task_finished()

    def _on_task_started(self, task_name):
        """任务启动时：禁用按钮、重置进度条、更新状态指示器（v3.36 优化版）"""
        COLORS = _main_mod.COLORS
        self._current_task_name = task_name
        # 重置进度节流状态与 ETA 外推估计器（v5.0 §2.1.3）
        self._last_progress_value = -1
        self._last_progress_text = ""
        self._progress_est.reset()
        self._task_finish_reason = ""

        # 重置并隐藏结果仪表盘（每次任务以最新结果为准）
        self._last_pipeline_final = None
        self._last_trend_result = None
        self._last_quick_final = None
        # v4.1：随任务启动重置各来源产物时间戳，避免复用上一轮陈旧时间
        self._last_pipeline_ts = 0
        self._last_trend_ts = 0
        self._last_quick_ts = 0
        self._clipboard_generated_ts = 0
        # 一并清空上轮融合概率缓存，避免「命中率优化」阶段
        # 拿到上一次任务的陈旧分布做对照。
        self._last_fused_probabilities = None
        self._last_predict_meta = {}
        try:
            self._hide_result_dashboard()
        except Exception:
            pass
        try:
            self._show_loading_indicator()
        except Exception:
            pass
        try:
            import logging as _lg
            _d = _lg.getLogger('kplucky.debug')
            _d.info('DASHBOARD_HIDE: task_started=%s', task_name)
        except Exception:
            pass
        self._set_buttons_state(tk.DISABLED)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self.status_var.set("正在执行...")
        # 统一文案模板：阶段名 · 百分比 · 预计剩余（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(0, "", task_name=task_name),
            fg=COLORS['warning'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])

        # 使用 TaskManager 刷新日志，避免高频UI操作
        now = datetime.now().strftime('%H:%M:%S')
        self.task_mgr.log(f"\n{'=' * 70}\n")
        self.task_mgr.log(f"  [{now}] 开始执行: {task_name}\n")
        self.task_mgr.log(f"{'=' * 70}\n\n")

    def _on_task_finished(self):
        """任务完成时：恢复按钮、进度条置100%、状态指示器变绿（v3.36 优化版）"""
        COLORS = _main_mod.COLORS
        self._hide_loading_indicator()
        if self._last_pipeline_final or self._last_trend_result or self._last_quick_final:
            self._show_result_dashboard(
                pipeline_final=self._last_pipeline_final,
                trend_result=self._last_trend_result,
                quick_final=self._last_quick_final
            )
        else:
            self._hide_result_dashboard()
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 100
        self.progress_label.config(text="100%")
        self._last_progress_value = 100  # 更新节流状态
        self._last_progress_text = ""    # 终态文案与运行中不同，避免节流误跳过
        self.status_var.set("任务完成")
        # 统一文案模板（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(100, "完成", task_name=self._current_task_name),
            fg=COLORS['success'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['success'])

        # 关键修复：强制重置任务运行状态，防止竞态条件导致复制按钮误判
        # 使用 after(0) 确保在主线程中执行，避免多线程问题
        self.root.after(0, self._sync_task_state)

        # 使用 TaskManager 刷新日志
        now = datetime.now().strftime('%H:%M:%S')
        self.task_mgr.log(f"\n{'=' * 70}\n")
        self.task_mgr.log(f"  [{now}] 任务完成: {self._current_task_name}\n")
        self.task_mgr.log(f"{'=' * 70}\n")

    def _sync_task_state(self):
        """同步任务状态，确保 is_running() 立即返回 False（v3.60 修复竞态条件）"""
        try:
            # 强制清理所有运行中任务记录，防止竞态条件
            with self.task_mgr._running_lock:
                self.task_mgr._running_tasks.clear()
        except Exception as e:
            import logging
            logging.getLogger('kplucky.debug').error(f'SYNC_TASK_STATE_ERROR: {e}', exc_info=True)

    def _classify_error(self, error_msg: str):
        """将任务错误文本归因分类，返回 (类别, 原因说明, 建议操作列表)（v4.0 §2.4 错误引导）。

        按已知错误特征做关键词归因，输出用户可直接执行的排查建议，
        替代原先「只抛原始错误文本」的低效反馈。未知错误走「其他」兜底。

        参数:
            error_msg: 任务抛出的错误信息（可能含异常类名 / 堆栈片段）

        返回:
            tuple: (category: str, reason: str, suggestions: list[str])
        """
        s = (error_msg or '').lower()

        # 网络 / 爬取类（先判定：requests/urllib/timeout 等强网络特征优先，
        # 避免被数据库组宽泛的 "connection" 误捕）
        if any(k in s for k in ('requests', 'urllib', 'timeout', 'timed out', 'connectionerror',
                                 'proxy', 'ssl', 'dns', 'resolve', '网络', '爬取', '抓取', '502', '504')):
            return ('网络/数据源', '外部数据源访问超时或被限流',
                    ['检查本机网络连通性', '等待 1~2 分钟后重试（数据源可能限流）',
                     '尝试「全量爬取数据」重建数据', '检查是否触发反爬（可间隔重试）'])
        # 数据库 / 连接类（去掉宽泛裸 "connection"，避免误捕 requests.ConnectionError）
        if any(k in s for k in ('pymysql', 'mysql', 'database', '连接', 'cannot connect',
                                 'access denied', 'no such file', 'operating system',
                                 'dbutilspooled', 'maxed out', 'too many connections')):
            return ('数据库连接', '数据库连接失败或连接不可用',
                    ['检查 MySQL 服务是否已启动', '确认 config.py 中 DB_CONFIG 主机/端口/账号正确',
                     '检查防火墙与最大连接数限制', '稍后重试或先执行「数据爬取」触发重连'])
        # 数据不足 / 空态
        if any(k in s for k in ('no data', 'empty', '索引', 'indexerror', 'list index out of range',
                                 '至少需要', '样本', '历史数据', '无数据', 'insufficient')):
            return ('数据不足', '历史样本不足以支撑预测计算',
                    ['先执行「增量爬取数据」补齐历史期数', '确认库存 ≥ 61 期后再运行分析',
                     '查看「数据概览」核对各表记录数'])
        # 模型 / 算法类
        if any(k in s for k in ('numpy', 'sklearn', 'np.', 'matmul', 'shape', 'divide by zero',
                                 'nan', 'convergence', '模型', '训练', 'predict')):
            return ('模型/算法', '预测模型或算法执行异常',
                    ['查看 logs/gui_run.log 定位具体阶段', '确认 numpy/scikit-learn 版本匹配',
                     '清理陈旧缓存后重试（data/ 目录缓存）'])
        # 文件 / 路径类
        if any(k in s for k in ('filenotfound', 'permission denied', '磁盘', 'disk full',
                                 'permissionerror', '路径', 'no such file')):
            return ('文件/路径', '文件读写或磁盘空间异常',
                    ['检查磁盘剩余空间', '确认 reports/ 与 logs/ 目录可写', '确认未超出系统路径长度限制'])
        # 内存类
        if any(k in s for k in ('memoryerror', '内存', 'out of memory', 'cannot allocate')):
            return ('内存不足', '资源占用过高导致分配失败',
                    ['关闭其他高内存程序后重试', '尝试分批（缩短回测期数）运行', '重启应用释放内存'])
        # 兜底
        return ('未知错误', '任务执行时发生未分类异常',
                ['查看完整日志：logs/gui_run.log', '截图错误信息便于排查',
                 '若反复出现，尝试重启应用', '必要时执行「综合验证与分析」做健康诊断'])

    def _on_task_error(self, error_msg):
        """任务出错时：恢复按钮、进度条归零、状态指示器变红、弹窗提示。

        v4.0 §2.4 增强：错误反馈从「只抛原始文本」升级为「分类归因 + 原因 + 建议操作」，
        帮助用户快速定位问题而非面对一段原始堆栈。
        """
        COLORS = _main_mod.COLORS
        self._set_buttons_state(tk.NORMAL)
        self.progress['value'] = 0
        self.progress_label.config(text="0%")
        self._last_progress_value = 0  # 更新节流状态
        self._last_progress_text = ""    # 终态文案与运行中不同，避免节流误跳过
        self.status_var.set("任务失败")
        # 统一文案模板（v5.0 §2.1.3）
        self.task_status_label.config(
            text=ui_styles.format_progress_text(0, "失败", task_name=self._current_task_name),
            fg=COLORS['accent_danger'])
        self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])

        # 分类归因 + 建议操作（v4.0 §2.4 错误引导）
        category, reason, suggestions = self._classify_error(error_msg)
        guide_lines = '\n'.join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
        messagebox.showerror(
            "错误",
            f"任务「{self._current_task_name}」执行失败\n"
            f"\n【错误类型】{category}\n"
            f"【原因】{reason}\n"
            f"【详细】{error_msg}\n"
            f"\n【建议操作】\n{guide_lines}"
        )

        # 关键修复：强制重置任务运行状态，防止竞态条件导致后续操作误判
        # 使用 after(0) 确保在主线程中执行，避免多线程问题
        self.root.after(0, self._sync_task_state)

    def _on_task_cancelled(self, task_id=None):
        """任务被取消（TaskManager 经 TASK_CANCELLED 消息通知）。

        v5.0 §2.1 修复：本方法此前**缺失**——TaskManager 派发 TASK_CANCELLED 时
        抛 AttributeError，被 _dispatch_ui_message 的 except 静默吞掉，导致
        「取消任务」后按钮永久禁用、状态条停在运行中。现统一恢复交互并按
        统一文案模板呈现已取消状态。

        幂等：可安全重复调用（GUI 主动取消与后台 TASK_CANCELLED 消息都会触发）。
        """
        COLORS = _main_mod.COLORS
        self._hide_loading_indicator()
        self._set_buttons_state(tk.NORMAL)
        reason = getattr(self, '_task_finish_reason', '') or ''

        if reason == 'timeout':
            # 超时杀死任务：按失败呈现，而非「用户主动取消」
            self._last_progress_value = 0
            self._last_progress_text = ""
            self.progress['value'] = 0
            self.progress_label.config(text="0%")
            self.task_status_label.config(
                text=ui_styles.format_progress_text(0, '超时', task_name=self._current_task_name),
                fg=COLORS['accent_danger'])
            self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['accent_danger'])
            self.status_var.set("任务超时")
            self._show_toast("任务超时已停止", COLORS['accent_danger'], duration=2500)
        else:
            # 用户主动取消：保留取消瞬间的进度值，仅标注已取消
            try:
                at = int(self.progress['value'])
            except Exception:
                at = 0
            self._last_progress_value = at
            self._last_progress_text = ""
            self.task_status_label.config(
                text=ui_styles.format_progress_text(at, '已取消', task_name=self._current_task_name),
                fg=COLORS['warning'])
            self.status_dot.itemconfig(self._status_dot_id, fill=COLORS['warning'])
            self.status_var.set("任务已取消")

        # 强制重置任务运行状态，防止竞态导致后续按钮状态误判
        self.root.after(0, self._sync_task_state)

    def _on_task_timeout(self, task_id=None, name=''):
        """任务超时（TaskManager 经 TASK_TIMEOUT 消息通知）。

        v5.0 §2.1 修复：本方法此前**缺失**——TaskManager 超时监控线程派发
        TASK_TIMEOUT 时抛 AttributeError 被静默吞掉，超时后按钮永久禁用、
        用户无从得知任务已被终止。超时视为失败：记录原因后统一恢复，
        文案由紧随的 TASK_CANCELLED 处理路径按「超时」呈现。
        """
        self._task_finish_reason = 'timeout'
        if name:
            self._current_task_name = name
        self._on_task_cancelled(task_id)

    def _cancel_current_task(self):
        """取消当前任务（v3.36 新增 / v5.0 §2.1 修正状态文案）。

        原实现直接调用 _on_task_finished()，会把已取消的任务显示为
        「已完成 100%」，属状态误导。现改为按统一文案模板呈现「已取消」，
        并保留后续 TASK_CANCELLED 消息的幂等恢复能力。
        """
        COLORS = _main_mod.COLORS
        if self.task_mgr.is_running():
            self.task_mgr.cancel()
            self._show_toast("任务已取消", COLORS['warning'], duration=1500)
            self._on_task_cancelled(None)

    # ============================================================
    # 日志文件写入
    # ============================================================

    def flush_pending_logs(self):
        """提示：运行日志已改为写入文件（logs/gui_run.log），当前无需 UI 刷新。"""
        try:
            self.task_mgr.flush_pending_logs()
        except Exception:
            pass

    def append_colored(self, text, tag='info'):
        """保留为空方法，避免外部调用报错（输出面板已移除）。"""

    def append_section_header(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_success(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_warning(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    def append_error(self, text):
        """日志写入由 TaskManager 统一处理，UI 输出部分停用。"""

    # ============================================================
    # 一键复制预测号码
    # ============================================================

    def _copy_prediction(self):
        """将「预测结果仪表盘」中的预测号码一键复制到剪贴板（微信兼容版）

        v4.1 修复：复制优先级链重构
        1. 任务运行中 → 提示等待
        2. 存在已聚合多源 clipboard（_prediction_clipboard 由
           _compute_dashboard_aggregates 在渲染时写入，含 ①②③ 共识主推荐）
           → 直接采用（与仪表盘所见一致，避免单源陈旧缓存）
        3. 否则回退到本次流水线 _last_pipeline_final 单源构建
        4. 再否则数据库最近记录兜底
        5. 全无数据 → 提示运行分析

        根因：此前 ② 直接读 _last_pipeline_final（仅① 单源），
        若本次 ① 失败/被 ②③ 覆盖共识，复制仍显示上一轮 ① 结果 → 
        "始终相同"。现改为优先取已聚合多源缓存。
        """
        COLORS = _main_mod.COLORS
        # 详细日志：记录复制尝试的上下文
        try:
            import logging
            _log = logging.getLogger('kplucky.debug')
            _clip = getattr(self, '_prediction_clipboard', '') or ''
            _meta = getattr(self, '_clipboard_meta', {}) or {}
            _log.info('COPY_ATTEMPT: clipboard_len=%d task_running=%s has_meta=%s',
                      len(_clip), self.task_mgr.is_running(), bool(_meta))
        except Exception:
            pass

        # ① 检查是否有任务正在运行，防止在分析过程中复制到中间态结果
        if self.task_mgr.is_running():
            try:
                _log.warning('COPY_BLOCKED_TASK_RUNNING: 任务运行中，拒绝复制中间态结果')
            except Exception:
                pass
            messagebox.showinfo(
                "分析进行中",
                "当前有分析任务正在进行中，预测结果尚未最终确定。\n\n"
                "解决步骤：\n"
                "  1. 请等待当前分析任务完成（状态栏会显示「已完成」）\n"
                "  2. 完成后「复制预测号码」按钮自动恢复可用\n"
                "  3. 再次点击即可复制最终预测号码\n\n"
                "注：分析通常耗时 1~3 分钟，请耐心等待。")
            return

        # ② v4.1：优先采用已聚合多源剪贴板（由 _compute_dashboard_aggregates 渲染时写入，
        #    与仪表盘共识主推荐完全一致）。命中且未过期即复制，避免单源陈旧缓存。
        # 关键修复：当 _pf 为 None 时（如任务刚启动、仪表盘未渲染），强制重算，
        # 避免沿用上一轮陈旧的预测结果导致不同期号出现完全相同的号码
        existing = (getattr(self, '_prediction_clipboard', '') or '').strip()
        _gen_ts = getattr(self, '_clipboard_generated_ts', 0) or 0
        _pf = getattr(self, '_last_pipeline_final', None)
        _pf_ts = getattr(self, '_last_pipeline_ts', 0) or 0
        # 判据：流水线单源在聚合缓存写入之后才产生（_pf 晚于 clipboard）→ 判定过期，重算聚合
        _stale = bool(existing and _pf and _pf_ts > _gen_ts) or (existing and not _pf)
        if existing and not _stale:
            clip = existing
            meta = getattr(self, '_clipboard_meta', {}) or {}
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').info(
                    'COPY_USE_AGGREGATED: 复用多源聚合剪贴板 len=%d issue=%s combo=%s',
                    len(clip), meta.get('target_issue', ''), meta.get('main_combo', ''))
            except Exception:
                pass
        else:
            # ③ 回退：重新聚合或单源构建
            if _stale:
                # 用 _last_* 重新算一遍多源聚合，写入最新 clipboard + 刷新时间戳
                try:
                    self._compute_dashboard_aggregates(
                        getattr(self, '_last_pipeline_final', None),
                        getattr(self, '_last_trend_result', None),
                        getattr(self, '_last_quick_final', None))
                except Exception:
                    pass
                clip = (getattr(self, '_prediction_clipboard', '') or '').strip()
                meta = getattr(self, '_clipboard_meta', {}) or {}
            if not clip:
                # 单源兜底
                if isinstance(_pf, dict) and _pf:
                    _single = self._build_prediction_clipboard(_pf)
                    if _single and _single.strip():
                        self._prediction_clipboard = _single
                        self._clipboard_meta = {
                            'target_issue': _pf.get('target_issue') or _pf.get('next_issue') or '',
                            'conf': '',
                            'high_conf': '',
                            'main_combo': '',
                        }
                        self._clipboard_generated_ts = _pf_ts or time.time()
                    clip = self._prediction_clipboard
                    meta = self._clipboard_meta
            # ④ 数据库兜底
            if not (clip and clip.strip()):
                try:
                    self._populate_clipboard_from_db_fallback()
                except Exception:
                    pass
                clip = getattr(self, '_prediction_clipboard', '')
                meta = getattr(self, '_clipboard_meta', {}) or {}

        if not clip or not clip.strip():
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').warning('COPY_BLOCKED_NO_RESULT: 无有效预测结果')
            except Exception:
                pass
            messagebox.showinfo(
                "暂无预测结果",
                "当前没有可复制的预测号码数据。\n\n"
                "原因说明：\n"
                "  • 尚未运行分析：请先点击左侧「开始分析」生成预测结果\n"
                "  • 分析未完成：请等待分析流程全部结束后再操作\n"
                "  • 数据不足：历史样本少于 61 期时无法生成可靠预测\n"
                "  • 数据库暂无历史预测记录\n\n"
                "解决步骤：\n"
                "  1. 点击「开始分析」按钮启动完整预测流程\n"
                "  2. 等待六阶段流水线全部完成（状态栏显示「已完成」）\n"
                "  3. 结果仪表盘出现后，点击「复制预测号码」即可")
            return

        # 微信格式优化处理
        data = self._format_for_wechat(clip.strip())

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(data)
            # 复制成功提示：明确展示 期数 + 置信度 + 复制的号码内容
            meta = getattr(self, '_clipboard_meta', {}) or {}
            issue = meta.get('target_issue') or ''
            combo = meta.get('main_combo') or ''
            conf = meta.get('conf')
            high = meta.get('high_conf')
            parts = []
            if issue:
                parts.append(f"第{issue}期")
            if combo:
                parts.append(f"预测号码 {combo}")
            if conf is not None:
                lvl = "（高置信度）" if high else ""
                parts.append(f"综合一致性置信度 {conf}%{lvl}")
            msg = " 已复制 " + " · ".join(parts) if parts else " 预测号码已复制到剪贴板"
            self._show_toast(msg, COLORS['success'])
            try:
                _log.info('COPY_SUCCESS: issue=%s combo=%s conf=%s high_conf=%s',
                          issue, combo, conf, high)
            except Exception:
                pass
        except Exception as e:
            try:
                _log.error('COPY_FAILED: %s', e, exc_info=True)
            except Exception:
                pass
            messagebox.showerror("复制失败", f"复制到剪贴板出错:\n{e}")

    def _format_for_wechat(self, text: str) -> str:
        """将文本转换为微信消息兼容格式

        微信消息格式要求：
        1. 换行使用 \\n（微信会自动处理）
        2. 避免使用连续特殊字符（如━━━━━━━━━━━━━━━━）
        3. 单行长度不超过80字符
        4. 不使用不可见控制字符
        5. 使用标准标点符号

        Args:
            text: 原始文本

        Returns:
            格式化后的微信兼容文本
        """
        # 1. 替换连续特殊字符为微信友好的分隔线
        wechat_safe_chars = {
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━━━━━━━',
            '━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━',
            '━━━━━━━━━━━━━━━━': '━━━━',
        }

        for bad, good in wechat_safe_chars.items():
            text = text.replace(bad, good)

        # 2. 处理不可见字符和零宽字符
        # 移除零宽空格、零宽连字符等
        text = ''.join(c for c in text if c not in '\u200b\u200c\u200d\ufeff\u200e\u200f')

        # 3. 处理特殊bullet符号（•）-> 保持或替换为*
        # 微信对这些符号支持良好，保持不变

        # 4. 分割长行并重新组合（每行不超过80字符）
        lines = text.split('\n')
        formatted_lines = []
        for line in lines:
            # 移除行首行尾多余空格（保留一个）
            line = line.rstrip()
            if not line:  # 跳过空行
                continue
            formatted_lines.append(line)

        return '\n'.join(formatted_lines)

    def _show_export_menu(self):
        """在「导出」按钮处弹出菜单：导出文本 / 导出图片。"""
        logger = _main_mod.logger
        clip = getattr(self, '_prediction_clipboard', "")
        if not clip or not clip.strip():
            messagebox.showinfo(
                "提示",
                "当前没有可导出的预测结果。\n请先点击「 开始分析」生成预测。")
            return
        try:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=" 导出为文本 (.txt)",
                             command=self._export_prediction_text)
            menu.add_command(label=" 导出为图片 (.png)",
                             command=self._export_prediction_image)
            # 定位到鼠标当前位置弹出
            x = self.root.winfo_pointerx()
            y = self.root.winfo_pointery()
            menu.tk_popup(x, y)
        except Exception as e:
            logger.debug(f"导出菜单弹出失败: {e}")

    def _default_export_name(self, ext):
        """生成默认导出文件名：排列5预测_期号_时间.ext"""
        meta = getattr(self, '_clipboard_meta', {}) or {}
        issue = meta.get('target_issue') or ''
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = f"排列5预测_{issue}_{ts}" if issue else f"排列5预测_{ts}"
        return f"{base}.{ext}"

    def _export_prediction_text(self):
        """导出预测结果为文本文件（复用剪贴板摘要，无外部依赖）。"""
        COLORS = _main_mod.COLORS
        clip = getattr(self, '_prediction_clipboard', "")
        if not clip or not clip.strip():
            messagebox.showinfo("提示", "当前没有可导出的预测结果。")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="导出预测结果为文本",
                defaultextension=".txt",
                initialfile=self._default_export_name("txt"),
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                f.write(clip.strip() + "\n")
            self._show_toast(f" 已导出文本：{os.path.basename(path)}",
                             COLORS['success'])
        except Exception as e:
            messagebox.showerror("导出失败", f"导出文本时出错:\n{e}")

    def _export_prediction_image(self):
        """导出预测仪表盘为 PNG 图片（PIL.ImageGrab 抓取仪表盘区域）。"""
        COLORS = _main_mod.COLORS
        dash = getattr(self, 'result_dash', None)
        if dash is None:
            messagebox.showinfo("提示", "当前没有可导出的预测结果。")
            return
        try:
            from PIL import ImageGrab
        except Exception:
            messagebox.showwarning(
                "缺少依赖",
                "图片导出需要 Pillow 库，当前环境未安装。\n可改用「导出为文本」。")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="导出预测仪表盘为图片",
                defaultextension=".png",
                initialfile=self._default_export_name("png"),
                filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")])
            if not path:
                return
            # 确保布局完成并置于最前，避免抓到遮挡内容
            self.root.update_idletasks()
            dash.update_idletasks()
            x = dash.winfo_rootx()
            y = dash.winfo_rooty()
            w = dash.winfo_width()
            h = dash.winfo_height()
            if w <= 1 or h <= 1:
                messagebox.showwarning("提示", "仪表盘尚未渲染完成，请稍后再试。")
                return
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            img.save(path)
            self._show_toast(f" 已导出图片：{os.path.basename(path)}",
                             COLORS['success'])
        except Exception as e:
            messagebox.showerror("导出失败", f"导出图片时出错:\n{e}")

    def _build_prediction_clipboard(self, final_report):
        """根据四步流水线 final_report 生成结构化的可复制预测摘要（委托纯函数）"""
        from modules.clipboard_builder import build_prediction_clipboard
        return build_prediction_clipboard(final_report, getattr(self, '_clipboard_meta', None))

    def _direct_extract_clipboard(self, final_report):
        """直接从 final_report 提取可复制文本，作为 _compute_dashboard_aggregates 兜底使用（委托纯函数）"""
        from modules.clipboard_builder import build_prediction_clipboard
        return build_prediction_clipboard(final_report, getattr(self, '_clipboard_meta', None))

    def _flash_status(self, text, color=COLORS['success']):
        """在底部状态栏临时显示提示，2.5 秒后恢复"就绪" """
        self.status_var.set(text)
        self.root.after(2500, lambda: self.status_var.set("就绪"))

    def _show_toast(self, text, color=None,
                    duration=ui_toast.TOAST_DEFAULT_DURATION_MS):
        """在主窗口内顶部居中弹出醒目的浮层提示（比状态栏更抓眼球）。

        无边框 Toplevel，淡入→短驻→淡出后自动销毁；同一时刻只保留一个 toast。
        任何异常都静默降级（不影响主流程），并同时刷新状态栏作为兜底。
        停留时长经 ui_toast.clamp_toast_duration 钳制（v5.2 GUI-10 方案
        §331 验收：Toast 自动消失 ≤ 5 s）；图标由 ui_toast.toast_icon_for
        按背景色解析，与此前内联三元行为完全一致。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        bg = color or COLORS['success']
        try:
            # 先销毁上一个仍在显示的 toast，避免叠加
            old = getattr(self, '_toast_win', None)
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    pass
                self._toast_win = None

            self.root.update_idletasks()
            toast = tk.Toplevel(self.root)
            self._toast_win = toast
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            try:
                toast.attributes('-alpha', 0.0)
            except Exception:
                pass

            frame = tk.Frame(toast, bg=bg, highlightthickness=0)
            frame.pack(fill=tk.BOTH, expand=True)
            # 添加图标前缀区分状态（图标解析收敛至 v5.2 GUI-10 契约纯函数）
            icon = ui_toast.toast_icon_for(bg, COLORS)
            tk.Label(frame, text=f"{icon}  {text}", bg=bg, fg=WHITE,
                     font=(UI_FONT, 11, 'bold'),
                     padx=22, pady=12).pack()

            toast.update_idletasks()
            # 定位到主窗口顶部居中
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw = self.root.winfo_width()
            tw = toast.winfo_reqwidth()
            px = rx + max(0, (rw - tw) // 2)
            py = ry + 64
            toast.geometry(f"+{px}+{py}")

            # 淡入
            def _fade_in(a=0.0):
                """Toast 提示的淡入动画，每 20ms 递增 0.15 透明度直至完全不透明。

                参数:
                    a: 当前透明度（0.0~1.0），递归调用时逐步累加
                """
                if not toast.winfo_exists():
                    return
                a = min(1.0, a + 0.15)
                try:
                    toast.attributes('-alpha', a)
                except Exception:
                    pass
                if a < 1.0:
                    toast.after(20, lambda: _fade_in(a))

            # 淡出并销毁
            def _fade_out(a=1.0):
                """Toast 提示的淡出动画，每 20ms 递减 0.12 透明度，归零后销毁窗口。

                参数:
                    a: 当前透明度（0.0~1.0），递归调用时逐步递减
                """
                if not toast.winfo_exists():
                    return
                a = max(0.0, a - 0.12)
                try:
                    toast.attributes('-alpha', a)
                except Exception:
                    pass
                if a > 0.0:
                    toast.after(20, lambda: _fade_out(a))
                else:
                    try:
                        toast.destroy()
                    except Exception:
                        pass
                    if getattr(self, '_toast_win', None) is toast:
                        self._toast_win = None

            _fade_in()
            # 停留时长先钳制（≤5s 契约）再与最短驻留取大，行为同旧 max(600, duration)
            _hold_ms = ui_toast.clamp_toast_duration(duration)
            _hold_ms = max(ui_toast.TOAST_MIN_HOLD_MS, _hold_ms)
            toast.after(_hold_ms, _fade_out)
        except Exception as e:
            logger.debug(f"toast 显示失败(非致命): {e}")
        # 状态栏兜底，确保信息始终可见
        self._flash_status(text, bg)

    def _confirm_close(self):
        """窗口关闭请求处理（v3.25/v3.64/v3.65）：有任务运行时先警示确认，避免误关中断分析。

        v3.64 修复：关闭前先 cancel _poll_evolution 的 after 定时器，防止 destroy() 后
        仍有 Tcl 回调在调度导致白屏卡死。
        v3.65 修复：
          1. 保存并 cancel 所有 after 定时器句柄（_poll_evolution / _liveness_watchdog）
          2. destroy() 前等待 self_evolution 守护线程真正退出，防止 TclError 白屏
          3. cancel 顺序：标记 → 取消定时器 → 关闭线程池 → 等待线程退出 → destroy

        Returns:
            bool: True 表示已执行关闭；False 表示用户取消、窗口保留。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            if self.task_mgr.is_running():
                task_name = getattr(self, '_current_task_name', '') or '后台任务'
                ok = messagebox.askyesno(
                    "任务正在运行",
                    f"「{task_name}」正在执行中。\n\n"
                    f"现在退出将中断该任务，本次分析结果可能丢失。\n"
                    f"确定要退出吗？",
                    icon='warning', default='no', parent=self.root
                )
                if not ok:
                    return False
        except Exception as e:
            # 状态检查/弹窗异常不应阻止用户关闭窗口
            logger.debug(f"关闭确认检查失败(直接放行): {e}")
        
        # 更新状态栏：开始关闭
        self._update_status_ui("正在关闭...", COLORS['text_secondary'])

        # ===== 第一步：标记取消，阻断所有 after 定时器继续调度 =====
        self._poll_evolution_cancelled = True

        # cancel _update_time 定时器（v3.66：防止 destroy() 后时钟回调继续调度）
        _ut_handle = getattr(self, '_update_time_handle', None)
        if _ut_handle is not None:
            try:
                self.root.after_cancel(_ut_handle)
            except Exception:
                pass
            self._update_time_handle = None

        # cancel _poll_evolution 定时器（v3.65：使用保存的句柄）
        handle = getattr(self, '_poll_evolution_handle', None)
        if handle is not None:
            try:
                self.root.after_cancel(handle)
            except Exception:
                pass
            self._poll_evolution_handle = None

        # cancel _poll_cli_log 定时器（需求 #3：命令行输出实时刷新）
        self._poll_cli_log_cancelled = True
        cli_handle = getattr(self, '_poll_cli_log_handle', None)
        if cli_handle is not None:
            try:
                self.root.after_cancel(cli_handle)
            except Exception:
                pass
            self._poll_cli_log_handle = None

        # GUI-04：停止轮询统一调度循环（cancel 挂起 tick；stop 幂等，
        # 已取消的任务内部自守卫双保险，防止 destroy() 后回调继续调度）
        _scheduler = getattr(self, '_ui_scheduler', None)
        if _scheduler is not None:
            _scheduler.stop()

        # cancel _liveness_watchdog 定时器（v3.65）
        lw_handle = getattr(self, '_liveness_watchdog_handle', None)
        if lw_handle is not None:
            try:
                self.root.after_cancel(lw_handle)
            except Exception:
                pass
            self._liveness_watchdog_handle = None
        
        # 更新状态栏：停止后台任务
        self._update_status_ui("停止后台任务...", COLORS['text_secondary'])

        # ===== 第二步：关闭任务线程池 =====
        # v3.67 修复：必须在主线程里先 cancel TaskManager._poll_handle
        # （Tcl 调用跨线程不安全：后台线程调 after_cancel 会被封送回主线程，
        # 而主线程又卡在 join(5s) 等待后台线程退出 → 死锁！）
        try:
            _tm_poll = getattr(self.task_mgr, '_poll_handle', None)
            if _tm_poll is not None:
                self.root.after_cancel(_tm_poll)
                self.task_mgr._poll_handle = None
                logger.debug('[GUI] 主线程已 cancel TaskManager._poll_handle')
        except Exception as e:
            logger.debug('[GUI] cancel TaskManager._poll_handle 失败: %s', e)

        # v3.67: shutdown() 内部已移除 Tcl 调用，后台线程现在可以安全地只做
        # 线程池关闭和日志 flush，不会再产生跨线程死锁
        try:
            def shutdown_task_manager():
                self.task_mgr.shutdown()
            
            shutdown_thread = threading.Thread(target=shutdown_task_manager, daemon=True)
            shutdown_thread.start()
            shutdown_thread.join(timeout=5.0)
            if shutdown_thread.is_alive():
                logger.warning("TaskManager shutdown timed out, proceeding anyway")
        except Exception:
            pass

        # ===== 第三步：优雅关闭自我进化引擎 =====
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                eng.shutdown()
            except Exception:
                pass
            # v3.65：等待后台线程真正退出，防止 destroy() 后 TclError 白屏
            if eng._thread is not None and eng._thread.is_alive():
                try:
                    eng._thread.join(timeout=3.0)
                except Exception:
                    pass

        # ===== 3.5：优雅停止 §3.4 模型监控守护线程（若已启动） =====
        # model_monitor 的 _monitor_loop 是 daemon 线程；若 app 启动后启用了
        # start_model_monitoring()，关闭时须 stop_model_monitoring() 让线程在 5s 内
        # 主动退出并置 _stop_monitor，避免守护线程在 destroy() 后继续 5s 轮询刷盘。
        # 惰性导入 + try/except：监控未启动时 stop_monitoring() 仅 warning，不影响关闭。
        try:
            from modules.model_monitor import stop_model_monitoring
            stop_model_monitoring()
        except Exception as _mm_stop_e:
            logger.debug('[GUI] 停止模型监控守护线程跳过(非致命): %s', _mm_stop_e)

        # ===== 3.6：优雅停止 §4.3 系统性能监控守护线程（若已启动） =====
        # perf_monitor 的 _monitor_loop 是 1Hz daemon 线程；关闭时须
        # stop_perf_monitoring() 让线程在 ~1s 内主动退出，避免 destroy() 后
        # 继续采样。惰性导入 + try/except：监控未启动时 stop_monitoring() 幂等
        # 空操作，不影响关闭。
        try:
            from modules.perf_monitor import stop_perf_monitoring
            stop_perf_monitoring()
        except Exception as _pf_stop_e:
            logger.debug('[GUI] 停止性能监控守护线程跳过(非致命): %s', _pf_stop_e)

        # ===== 第四步：销毁窗口（所有定时器已取消，后台线程已退出）=====
        # v3.66: 先强制处理所有待执行的 after 回调，再销毁窗口，防止白屏
        try:
            # 等待所有 pending 的 after(0) 回调执行完毕
            self.root.update_idletasks()
        except Exception:
            pass

        # v3.65: destroy 前做最终窗口有效性检查，防止在 after 回调嵌套执行时重复 destroy
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except Exception:
            pass
        return True

    def _update_progress_ui(self, value, text=""):
        """更新进度条值与进度文本（v3.36 节流版 / v5.0 §2.1.3 统一文案模板）。

        文案模板统一为「阶段名 · 百分比 · 预计剩余」：
          * 运行中：``阶段名 · 42% · 预计剩余约1分20秒``（ETA 不可估时显示「计算中」）
          * 终态：  ``任务名 · 已完成/失败原因 · NN%``
        预计剩余来自 :class:`ui_styles.ProgressEstimator` 的几何外推，样本不足时
        显示「计算中」而非编造数字。上游 ``task_mgr.progress(value, text)`` 的既有
        文案全部保持不变，归一化只发生在本渲染层。
        进度条本体（progress / progress_label）仍只显示百分比，避免与状态标签重复。
        """
        COLORS = _main_mod.COLORS
        try:
            pct = int(round(max(0.0, min(100.0, float(value)))))
        except Exception:
            pct = 0

        # 仅运行中样本参与 ETA 外推；终态样本不外推
        if ui_styles.classify_progress_text(text, pct) == ui_styles.STATUS_RUNNING:
            eta = self._progress_est.sample(pct)
        else:
            eta = None
        new_text = ui_styles.format_progress_text(
            pct, text,
            task_name=getattr(self, '_current_task_name', '') or '',
            eta_seconds=eta,
        )

        # 节流：值与文案均未变化才跳过（仅文案变化仍须刷新，例如 ETA 从
        # 「计算中」变为具体时长，或同百分比下阶段名变化）
        if (hasattr(self, '_last_progress_value') and self._last_progress_value == pct
                and getattr(self, '_last_progress_text', '') == new_text):
            return
        self._last_progress_value = pct
        self._last_progress_text = new_text

        self.progress['value'] = pct
        self.progress_label.config(text=f"{pct}%")
        if text:
            self.task_status_label.config(text=new_text, fg=COLORS['text_secondary'])

    def _update_status_ui(self, text, color=COLORS['text_muted']):
        """更新底部状态栏文字（v3.36 节流版）"""
        # 节流：相同文本跳过重复更新
        if self.status_var.get() == text:
            return
        self.status_var.set(text)

    def _display_report(self, data):
        """显示从后台线程传递来的报告数据（v3.36 批量刷新版）"""
        if data.get('report'):
            self.task_mgr.log(data['report'])

    def _set_buttons_state(self, state):
        """批量设置所有操作按钮的启用/禁用状态（v4.0 §2.4 加载态视觉反馈）。

        禁用（任务运行中）：统一置灰外观，让用户感知「运行中不可再操作」；
        恢复（NORMAL）：还原各按钮记录的正常态配色。
        还原经 _apply_button_normal_style 按 token 动态解析（v4.0 主题适配）：
        按钮缓存的是「主题键 token / 静态 hex」，切换主题后仍取当前主题值，不泄漏旧色。
        另含显式取消按钮的反转管理（v5.2 GUI-10，详见 _set_cancel_btn_state）。
        """
        COLORS = _main_mod.COLORS
        for btn in self._buttons:
            try:
                btn.config(state=state)
                if state == tk.DISABLED:
                    btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
                else:
                    self._apply_button_normal_style(btn)
            except Exception:
                continue
        # 结果区「复制预测号码」按钮独立控制：分析进行中禁用，完成或无数据时恢复
        if hasattr(self, '_result_copy_btn') and self._result_copy_btn is not None:
            try:
                self._result_copy_btn.config(state=state)
                if state == tk.DISABLED:
                    self._result_copy_btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
                else:
                    self._apply_button_normal_style(self._result_copy_btn)
            except Exception:
                pass
        # 显式取消按钮（v5.2 GUI-10）启用态与操作按钮相反，单独反转管理
        _cancel_btn = getattr(self, '_cancel_btn', None)
        if _cancel_btn is not None:
            self._set_cancel_btn_state(_cancel_btn, state)

    def _set_cancel_btn_state(self, btn, state):
        """反转设置显式取消按钮的启停状态（v5.2 GUI-10 方案 §331）。

        取消按钮的启用语义与操作按钮相反：任务运行中（操作按钮收到
        tk.DISABLED）才允许点击取消；任务结束/取消/出错（操作按钮收到
        tk.NORMAL）后取消按钮灰化禁用。配色取自视觉规范工厂（v5.2
        GUI-08 单一来源）的 danger 三态，禁用外观与其余按钮统一
        （bg_input / text_disabled）。

        参数:
            btn: 取消按钮控件（tk.Button 或其测试替身）
            state: 操作按钮侧的状态（tk.DISABLED / tk.NORMAL）

        返回:
            None
        """
        COLORS = _main_mod.COLORS
        try:
            if state == tk.DISABLED:
                # 任务运行中：取消按钮转为可点击的危险态
                _style_map = button_style_map(COLORS)
                _danger = _style_map.get('danger', _style_map['secondary'])
                btn.config(state=tk.NORMAL)
                btn.config(bg=_danger['bg'], fg=_danger['fg'])
            else:
                # 空闲态：灰化禁用，与操作按钮禁用外观一致
                btn.config(state=tk.DISABLED)
                btn.config(bg=COLORS['bg_input'], fg=COLORS['text_disabled'])
        except Exception:
            pass

    # ============================================================
    # 业务任务 - 系统操作
    # ============================================================

    # ============================================================
    # 业务任务 - 数据爬取
    # ============================================================

    def _refresh_crawl_status(self):
        """刷新数据爬取卡片的数据库状态与历史数据量（主线程安全调用）。"""
        P5Database = _main_mod.P5Database
        try:
            db = P5Database()
            if db.connect():
                cnt = db.get_history_data_count()
                latest = db.get_latest_history_issue()
                db.disconnect()
                self.crawl_db_status_var.set(
                    f"数据库：已连接 | 历史 {cnt} 期 | 最新 {latest or '—'}")
            else:
                self.crawl_db_status_var.set("数据库：未连接")
        except Exception:
            self.crawl_db_status_var.set("数据库：状态未知")

    def _show_data_overview(self):
        """弹出『数据概览』窗口：展示各核心数据表的记录数与数据库状态。"""
        COLORS = _main_mod.COLORS
        P5Database = _main_mod.P5Database
        top = tk.Toplevel(self.root)
        top.title("数据概览")
        top.configure(bg=COLORS['bg_primary'])
        top.geometry("640x440")

        db = P5Database()
        connected = db.connect()
        status_txt = "数据库：已连接" if connected else "数据库：未连接"
        status_fg = COLORS['success'] if connected else COLORS['accent_danger_light']
        tk.Label(top, text=status_txt, font=(UI_FONT, 10, 'bold'),
                 bg=COLORS['bg_primary'], fg=status_fg).pack(anchor=tk.W, padx=14, pady=(10, 4))

        cols = ('数据表', '说明', '记录数')
        tree = ttk.Treeview(top, columns=cols, show='headings', height=14)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=(260 if c == '数据表' else (200 if c == '说明' else 120)),
                        anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))
        # 会话级列宽记忆：弹窗重建时恢复用户上次调整的列宽（不落盘）
        self._bind_tree_width_memory(tree, 'db_table_overview')

        tables = [
            ('p5_history_data', '历史开奖数据(核心)'),
            ('p5_trend_data', '通用走势数据'),
            ('p5_wan_trend_data', '万位独立走势'),
            ('p5_qian_trend_data', '千位独立走势'),
            ('p5_bai_trend_data', '百位独立走势'),
            ('p5_shi_trend_data', '十位独立走势'),
            ('p5_ge_trend_data', '个位独立走势'),
            ('p5_spjzs_data', '升平降走势'),
            ('p5_hzzst_data', '和值走势'),
            ('p5_bayesian_result', '贝叶斯推断结果'),
            ('p5_ai_report', 'AI 分析报告'),
            ('p5_prediction_record', '预测记录'),
            ('p5_evolution_version', '自我进化版本'),
        ]
        if connected:
            for tbl, label in tables:
                try:
                    cnt = db.get_table_count(tbl)
                except Exception:  # noqa: BLE001
                    cnt = 0
                tree.insert('', tk.END, values=(tbl, label, cnt))
            db.disconnect()
        else:
            tree.insert('', tk.END, values=('（数据库未连接）', '无法读取各表统计', '—'))

    def _execute_crawl_incremental(self, task_mgr, cancel_event=None):
        """增量爬取数据：仅获取数据库中缺失的新期号数据，含历史+走势数据"""
        P5Database = _main_mod.P5Database
        P5Spider = _main_mod.P5Spider
        task_mgr.log("启动增量数据爬取...")
        task_mgr.log(" 爬取内容：历史数据 + 走势数据 + 独立走势表(万/千/百/十/个位) + 升平降走势 + 和值走势")
        task_mgr.progress(10, "初始化爬虫")

        spider = P5Spider()
        task_mgr.progress(20, "连接数据库并爬取")

        # 使用data_fetcher的crawl_and_save_incremental()方法
        # 该方法已内置爬取：
        # 1. 历史数据（多源备份）- 增量
        # 2. 通用走势数据（中华彩讯/55128）- 增量
        # 3. 6个独立走势表（一定牛ydniu.com）：万/千/百/十/个位 + 基础走势 - 增量
        # 4. 升平降走势图（p5_spjzs_data）- 增量
        # 5. 和值走势图（p5_hzzst_data）- 增量
        # 6. 和尾走势图（p5_sum_end_trend_data）- 增量
        # 7. 后三走势图（p5_back_three_trend_data）- 增量
        crawl_res = spider.crawl_and_save_incremental()
        history_success, history_skip = crawl_res['history']
        trend_success, trend_skip = crawl_res['trend']
        spjzs_success, spjzs_skip = crawl_res['spjzs']
        hzzst_success, hzzst_skip = crawl_res['hzzst']
        sum_end_success, sum_end_skip = crawl_res.get('sum_end', (0, 0))
        back_three_success, back_three_skip = crawl_res.get('back_three', (0, 0))

        task_mgr.progress(90, "验证爬取结果")

        db = P5Database()
        if db.connect():
            db.create_tables()
            latest_history = db.get_latest_history_issue()
            latest_trend = db.get_latest_trend_issue()
            latest_spjzs = db.get_latest_spjzs_issue()
            latest_hzzst = db.get_latest_hzzst_issue()
            task_mgr.progress(100, "完成")
            task_mgr.log(f"\n 增量爬取完成!")
            task_mgr.log(f" 数据库最新历史期号: {latest_history}")
            task_mgr.log(f" 数据库最新走势期号: {latest_trend}")
            task_mgr.log(f" 升平降走势最新期号: {latest_spjzs}")
            task_mgr.log(f" 和值走势最新期号: {latest_hzzst}")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  历史数据(p5_history_data): 新增{history_success}条, 跳过{history_skip}条")
            task_mgr.log(f"  走势数据(p5_trend_data): 新增{trend_success}条, 跳过{trend_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  升平降走势(p5_spjzs_data): 新增{spjzs_success}条, 跳过{spjzs_skip}条")
            task_mgr.log(f"  和值走势(p5_hzzst_data): 新增{hzzst_success}条, 跳过{hzzst_skip}条")
            task_mgr.log(f"  和尾走势(p5_sum_end_trend_data): 新增{sum_end_success}条, 跳过{sum_end_skip}条")
            task_mgr.log(f"  后三走势(p5_back_three_trend_data): 新增{back_three_success}条, 跳过{back_three_skip}条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  各独立走势表当前存量：")
            for _pos in ('wan', 'qian', 'bai', 'shi', 'ge'):
                try:
                    _cnt = db.get_table_count(f'p5_{_pos}_trend_data')
                except Exception:  # noqa: BLE001
                    _cnt = 0
                task_mgr.log(f"    {_pos}位(p5_{_pos}_trend_data): {_cnt} 条")
            task_mgr.log(f"  ──────────────────────")
            task_mgr.log(f"  详细数据可在「数据概览」中查看")
            self.root.after(0, self._refresh_crawl_status)
            db.disconnect()
        else:
            task_mgr.progress(100, "完成(数据库未连接)")
            task_mgr.log(f"\n! 爬取完成但数据库未连接")
            task_mgr.log(f"  历史数据: {history_success}条新增")
            task_mgr.log(f"  走势数据: {trend_success}条新增")
            task_mgr.log(f"  和尾走势: {sum_end_success}条新增")
            task_mgr.log(f"  后三走势: {back_three_success}条新增")
            self.root.after(0, self._refresh_crawl_status)

    def _execute_crawl_full(self, task_mgr, cancel_event=None):
        """全量爬取数据：重新爬取全部历史数据（最多2000条）和走势数据"""
        P5Database = _main_mod.P5Database
        P5Spider = _main_mod.P5Spider
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
        db = P5Database()
        if not db.connect():
            task_mgr.log(" 数据库连接失败")
            return

        db.create_tables()
        task_mgr.progress(80, "保存历史数据")

        history_success, history_skip = db.insert_history_data(history_data)
        task_mgr.log(f"历史数据保存: 成功{history_success}条, 跳过{history_skip}条")

        task_mgr.progress(90, "保存走势数据")
        trend_success, trend_skip = db.insert_trend_data(trend_data)
        task_mgr.log(f"走势数据保存: 成功{trend_success}条, 跳过{trend_skip}条")

        db.disconnect()
        self.root.after(0, self._refresh_crawl_status)
        task_mgr.progress(100, "完成")
        task_mgr.log(f"\n全量爬取完成: 历史{history_success}条, 走势{trend_success}条")

    # ============================================================
    # 业务任务 - 预测验证
    # ============================================================

    def _execute_verify_predictions(self, task_mgr, cancel_event=None):
        """预测验证：获取待验证预测，比对实际开奖结果，更新性能统计"""
        Validator = _main_mod.Validator
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
            _total_n = int(stats.get('total', 0) or 0)
            task_mgr.log(f"  总预测次数: {_total_n}")
            if _total_n:
                task_mgr.log(f"  宽松口径·完全猜中（候选集包含+±1容错，非预测力）: "
                             f"{stats.get('total_matched', 0)} 期")
                task_mgr.log(f"  宽松口径平均准确率（非预测力）: "
                             f"{stats.get('avg_accuracy', 0)}%")
            else:
                task_mgr.log("  暂无已验证数据（需等待开奖后自动验证）")

        task_mgr.progress(100, "完成")
        task_mgr.log("\n预测验证流程结束")

    def _execute_hit_rate_report(self, task_mgr, cancel_event=None):
        """
        命中率统计报告：统计历史预测命中率，展示各位置命中率、趋势分析
        
        展示内容:
        - 总预测期数、完全命中次数/比例
        - 各位置命中率（万/千/百/十/个）
        - 命中率趋势（每5期一组）
        - 平均准确率
        """
        P5Database = _main_mod.P5Database
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  命中率统计报告")
            task_mgr.log("=" * 70)
            task_mgr.progress(10, "初始化")
            
            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return
            
            # 更新性能统计
            task_mgr.log("\n正在更新性能统计...")
            db.update_performance_stats()
            
            # 获取统计信息
            task_mgr.progress(30, "获取统计数据")
            stats = db.get_verification_stats()
            
            if stats.get('total', 0) == 0:
                task_mgr.log("\n 暂无已验证的预测数据")
                task_mgr.log("请先执行预测并等待开奖验证后再查看命中率")
                task_mgr.progress(0, "无数据")
                db.disconnect()
                return
            
            # 展示统计结果
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_section_header(" 总体命中率统计")
            task_mgr.log("-" * 60)
            
            total = stats.get('total', 0)
            total_matched = stats.get('total_matched', 0) or 0
            avg_match = stats.get('avg_match', 0) or 0
            avg_accuracy = stats.get('avg_accuracy', 0) or 0
            
            task_mgr.log(f"总预测期数: {total} 期")
            if total > 0:
                task_mgr.log(f"宽松口径·完全命中（候选集包含+±1容错）: "
                             f"{total_matched} 期 ({total_matched/total*100:.1f}%)")
            else:
                task_mgr.log(f"宽松口径·完全命中: 0 期 (0.0%)")
            task_mgr.log(f"平均命中位数: {avg_match:.2f}/5")
            task_mgr.log(f"宽松口径平均准确率（集合包含+±1容错，非预测力）: {avg_accuracy:.2f}%")
            task_mgr.log("  ↑ 仅作历史对照；真实预测力请看下方 Top-1 精确口径（随机基线 10%）")
            
            # ── Top-1 真实预测力口径（v3.67）──
            task_mgr.append_section_header(" ★ Top-1 真实预测力（首推号精确命中）")
            task_mgr.log("-" * 60)
            top1_wan = stats.get('top1_wan_accuracy', stats.get('strict_wan_accuracy', 0)) or 0
            top1_qian = stats.get('top1_qian_accuracy', stats.get('strict_qian_accuracy', 0)) or 0
            top1_bai = stats.get('top1_bai_accuracy', stats.get('strict_bai_accuracy', 0)) or 0
            top1_shi = stats.get('top1_shi_accuracy', stats.get('strict_shi_accuracy', 0)) or 0
            top1_ge = stats.get('top1_ge_accuracy', stats.get('strict_ge_accuracy', 0)) or 0
            top1_avg = stats.get('top1_avg_accuracy', stats.get('strict_avg_accuracy', 0)) or 0
            top1_full = stats.get('top1_full_matches', stats.get('strict_full_matches', 0)) or 0
            task_mgr.log(f"  各位置 Top-1 命中率（随机基线=10%）:")
            task_mgr.log(f"    万位: {top1_wan:6.2f}%    千位: {top1_qian:6.2f}%    百位: {top1_bai:6.2f}%")
            task_mgr.log(f"    十位: {top1_shi:6.2f}%    个位: {top1_ge:6.2f}%")
            task_mgr.log(f"  平均 Top-1 准确率: {top1_avg:.2f}%   全5位 Top-1 命中: {top1_full} 期")
            task_mgr.log("  ↑ 这是真实预测力指标。排列5公平摇号，Top-1理论上限≈10%")
            
            # ── 随机基线对照（v3.67 Phase3）──
            task_mgr.append_section_header(" 随机基线对照（系统是否超越随机？）")
            task_mgr.log("-" * 60)
            _r1 = top1_avg / 100.0
            _r_base = 0.10
            _delta_pp = (_r1 - _r_base) * 100
            _verdict = "略高于" if _delta_pp > 0 else ("接近" if abs(_delta_pp) <= 1.0 else "低于")
            task_mgr.log(f"  系统 Top-1 命中率 : {top1_avg:6.2f}%")
            task_mgr.log(f"  随机基线 Top-1     : {_r_base*100:6.2f}%   (排列5 每位随机期望=10%)")
            task_mgr.log(f"  超出/低于随机      : {_delta_pp:+.2f} 个百分点  →  {_verdict} 随机水平")
            task_mgr.log("  结论: 排列5 为公平摇号, 历史统计规律无法事前预测未来开奖")
            task_mgr.log("        Top-1 命中率趋近 10% 即「无超越随机」, 属正常且诚实的结果")
            
            task_mgr.append_section_header(" 各位置命中率（宽松口径·候选集包含）")
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
            latest_stats = db.get_performance_history(limit=30)

            if latest_stats:
                task_mgr.append_section_header(" 近30天命中率趋势")
                task_mgr.log("-" * 60)

                # get_performance_history 返回 DESC 排序（最新在前），取前7天为最近7天
                for stat in latest_stats[:7]:  # 只显示最近7天
                    date = stat.get('stat_date', 'N/A')
                    total = stat.get('total_predictions', 0)
                    if total > 0:
                        acc = stat.get('overall_accuracy', 0)
                        task_mgr.log(f"  {date}: {total}期预测, 准确率{acc:.2f}%")
            
            task_mgr.log("\n" + "=" * 60)
            task_mgr.append_success("命中率统计完成")
            task_mgr.log("=" * 60)
            task_mgr.log("\n 提示：彩票开奖具有随机性，历史命中率不代表未来表现")
            task_mgr.log("   请理性购彩，切勿过度依赖预测结果")
            
            task_mgr.progress(100, "完成")
            db.disconnect()
            
        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 命中率统计失败: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常")

    def _execute_performance_report(self, task_mgr, cancel_event=None):
        """性能评估报告：生成AI预测命中率统计报告，含总预测数/完全猜中/平均准确率"""
        Validator = _main_mod.Validator
        task_mgr.log("生成AI预测性能评估报告...")
        task_mgr.progress(30, "获取统计数据")

        validator = Validator()
        report = validator.generate_performance_report()

        task_mgr.progress(80, "渲染报告")
        task_mgr.log("\n" + report)
        task_mgr.progress(100, "完成")

    # ============================================================
    # 业务任务 - AI分析核心流水线
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

    def _log_real_algorithm_weights(self, task_mgr, title: str = "预测算法权重配置（实际生效）"):
        """读取 predictor 真实冻结配置并打印各算法权重，避免 GUI 显示与实际权重脱节。

        首次读取后缓存（import + 配置解析只做一次），后续运行直接复用，
        消除每次「开始分析」都重新 import 并解析权重的冗余开销。
        """
        try:
            if getattr(self, '_cached_algo_weights', None) is None:
                from modules.predictor import P5PredictorConfig
                algos = P5PredictorConfig.DEFAULT_CONFIG.get('algorithms', {})
                labels = {
                    'frequency_weighted': '频率加权',
                    'omission_regression': '遗漏回归',
                    'trend_momentum': '趋势动量',
                    'markov_transition': '马尔可夫',
                    'pattern_continuation': '形态延续',
                    'bayesian_inference': '贝叶斯推断',
                    'feature_engineering': '特征工程',
                    'ml_supervised': '监督学习(GB)',
                }
                self._cached_algo_weights = [
                    (labels.get(name, name), cfg.get('weight', 0.0))
                    for name, cfg in algos.items() if cfg.get('enabled', True)
                ]
                task_mgr.log(f"\n {title}:")
            for name, w in self._cached_algo_weights:
                task_mgr.log(f"     • {name}: {w * 100:.1f}%")
        except Exception as _e:
            task_mgr.log(f" 权重读取失败(使用默认展示): {_e}")

    def _real_algorithm_weights_line(self) -> str:
        """返回真实生效权重的一行简写（避免显示与实算脱节的硬编码值）。"""
        try:
            from modules.predictor import P5PredictorConfig
            algos = P5PredictorConfig.DEFAULT_CONFIG.get('algorithms', {})
            labels = {
                'frequency_weighted': '频率',
                'omission_regression': '遗漏',
                'trend_momentum': '趋势',
                'markov_transition': '马尔可夫',
                'pattern_continuation': '形态',
                'bayesian_inference': '贝叶斯',
                'feature_engineering': '特征',
            }
            parts = []
            for name, cfg in algos.items():
                if not cfg.get('enabled', True):
                    continue
                w = cfg.get('weight', 0.0)
                parts.append(f"{labels.get(name, name)}{w * 100:.0f}%")
            return " | ".join(parts)
        except Exception:
            return "频率68% | 监督学习14% | 贝叶斯10% | 遗漏6% | 趋势1% | 马尔可夫0.5% | 形态0.3% | 特征0.2%"

    def _render_bayesian_ai_section(self, task_mgr, db):
        """「在线学习引擎报告」中新增的『贝叶斯推断 · AI 辅助』子节。

        读取最近 60 期历史，计算贝叶斯后验（展示参与状态：后验 / 纯先验），
        若 AI 可用则用大模型对后验分布作辅助解读；否则诚实显示『AI分析:未启用』。
        """
        task_mgr.append_section_header(" 贝叶斯推断 · AI 辅助处理")
        try:
            from modules.predictor import P5Predictor
            pred = P5Predictor()
            ai_enabled = pred.config.get_global_param('enable_ai_model', True)

            # 取最近 60 期历史（倒序取最新，再反转回时间升序供算法使用）
            # 修复: 旧实现用 ORDER BY issue ASC LIMIT 60 取到的是最「旧」的 60 期，
            # 导致 _verification_cutoff 落在 2023 年，验证记录过滤后仅个位数，
            # 贝叶斯退化为纯先验、AI 辅助永不触发。此处改为取「最近」60 期。
            try:
                db.cursor.execute('SELECT * FROM p5_history_data ORDER BY issue DESC LIMIT 60')
                rows = db.cursor.fetchall() or []
                rows = list(reversed(rows))  # 反转回时间升序(旧→新)
            except Exception:
                rows = []
            if not rows:
                task_mgr.log(" 无历史数据，无法计算贝叶斯后验。")
                return

            history = [{
                'issue': r.get('issue'), 'wan': r.get('wan'), 'qian': r.get('qian'),
                'bai': r.get('bai'), 'shi': r.get('shi'), 'ge': r.get('ge'),
            } for r in rows]
            history = pred._normalize_history_data(history)
            sorted_data = pred._sort_data_by_issue(history)
            pred._verification_cutoff = sorted_data[-1].get('issue')

            posterior = pred._algo_bayesian_inference(sorted_data)
            # _bayesian_inference 已（在 AI 可用时）填充 _bayesian_ai_auxiliary，直接复用，避免重复调用
            bayes_aux = getattr(pred, '_bayesian_ai_auxiliary', {})
            # 参与状态：验证记录是否达到最小样本
            vrecs = pred._load_verification_records()
            cutoff = pred._verification_cutoff
            if cutoff is not None:
                try:
                    ci = int(str(cutoff))
                    vrecs = [r for r in vrecs if int(str(r.get('target_issue', '0'))) < ci]
                except (ValueError, TypeError):
                    pass
            engaged = len(vrecs) >= 50
            task_mgr.log(f"  贝叶斯后验参与状态: {'已用验证反馈（后验）' if engaged else '验证样本不足，退化为纯先验'}"
                         f"（可用验证记录 {len(vrecs)} 条，阈值 50）")

            # 展示各位置后验 Top-3
            pos_names = pred.position_names
            for pos in range(pred.positions):
                ranked = sorted(posterior[pos].items(), key=lambda x: x[1], reverse=True)[:3]
                task_mgr.log(f"    {pos_names[pos]} 后验Top3: " +
                             ", ".join(f"{n}({p*100:.1f}%)" for n, p in ranked))

            # AI 辅助（复用 _bayesian_inference 已生成的辅助洞察，避免重复调用）
            if ai_enabled and pred.ai_available:
                aux = bayes_aux
                if aux:
                    task_mgr.append_success(" AI 分析: 启用（贝叶斯辅助洞察已生成）")
                    if aux.get('insight'):
                        task_mgr.log(f"    {aux['insight']}")
                    fd = aux.get('flagged_digits') or {}
                    if any(fd.values()):
                        shown = {k: v for k, v in fd.items() if v}
                        task_mgr.log(f"    重点关注号码: {shown}")
                    if aux.get('confidence_notes'):
                        task_mgr.log(f"    局限性: {aux['confidence_notes']}")
                    if aux.get('caution'):
                        task_mgr.log(f"    {aux['caution']}")
                    task_mgr.log(f"    (AI模型: {aux.get('model', 'agnes')})")
                else:
                    task_mgr.append_warning(" AI 分析: 调用未返回有效结果，已降级为纯统计后验。")
            else:
                task_mgr.append_warning(" AI分析: 未启用（未配置API密钥或不可用），以下仅展示统计贝叶斯后验。")
                task_mgr.log("    提示: 配置 AGNES_API_CONFIG.api_key 后，可引用 AI 模型对后验分布作辅助解读。")
        except Exception as _e:
            task_mgr.log(f" 贝叶斯 AI 辅助处理异常: {_e}")

    def _execute_four_step_pipeline(self, task_mgr, cancel_event=None):
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
        P5Database = _main_mod.P5Database
        # 若存在已激活的自我进化版本，将其权重注入预测器配置，使「开始分析」持续受益于进化。
        eng = getattr(self, 'evolution', None)
        if eng is not None:
            try:
                ok = eng.inject_active_version_to_predictor()
                if ok:
                    task_mgr.append_info(' ℹ 已注入「自我进化」当前活跃版本的优化权重（预测器将使用进化后的融合权重）。')
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 注入进化权重失败（不影响预测流程）：{e}')
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  四步流水线分析（增强版 ）")
            task_mgr.log("=" * 70)
            
            # 显示实际生效的算法权重（读取 predictor 真实冻结配置，避免显示与实算脱节）
            self._log_real_algorithm_weights(task_mgr, "预测算法权重配置（实际生效 v3.14 冻结）")
            task_mgr.log(f"\n  命中率优化:")
            task_mgr.log(f"     • 预测覆盖: Top-3 (每位置3个候选, 覆盖率30%)")
            task_mgr.log(f"     • 容错匹配: 允许偏差±1也算命中")
            task_mgr.log(f"     • 独立报告: 专家报告+走势图报告分离")
            task_mgr.log(f"     • 数据期数: 当前分析窗口=60期")
            task_mgr.log(f"\n 本版本集成功能:")
            task_mgr.log(f"     • 自动预测验证 + 在线学习")
            task_mgr.log(f"     • 可选历史回测 + 特征分析")
            task_mgr.log(f"     • 两份独立报告自动生成")
            task_mgr.log(f"  {"─" * 60}")

            # P1 优化: 预建立数据库连接，供流水线内部复用
            db = P5Database()
            if not db.connect():
                task_mgr.log(" 数据库连接失败，无法确定目标期号")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 查询最新历史期号
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            latest_issue = row.get('issue', '') if row else ''

            # 查询最新预测期号（用于检测滞后）
            db.cursor.execute('SELECT target_issue FROM p5_prediction_record ORDER BY target_issue DESC LIMIT 1')
            pred_row = db.cursor.fetchone()
            latest_pred_issue = pred_row.get('target_issue', '') if pred_row else ''

            # 注意：db 连接将在流水线执行后由用户手动断开或超时释放
            # 避免在流水线开始前断开，导致流水线内需要重新连接

            if not latest_issue:
                task_mgr.log(" 数据库中无历史数据，请先执行数据爬取")
                task_mgr.progress(0, "无历史数据")
                return

            # 计算目标预测期号
            target_issue = str(int(latest_issue) + 1)

            # 检测预测滞后：若最新预测期号 < 应预测期号，提示用户
            if latest_pred_issue and int(latest_pred_issue) < int(target_issue):
                lag = int(target_issue) - int(latest_pred_issue)
                task_mgr.log(f"⚠ 检测到预测滞后 {lag} 期（最新预测:{latest_pred_issue}, 应预测:{target_issue}）")
                task_mgr.log(f"  将自动预测期号: {target_issue}")
            elif latest_pred_issue:
                task_mgr.log(f"最新预测期号: {latest_pred_issue} (与应预测期号 {target_issue} 一致)")

            task_mgr.log(f"目标预测期号: {target_issue}")

            task_mgr.progress(0, "开始四步流水线分析...")

            # 使用增强版execute_pipeline，集成预测验证和在线学习
            # 添加进度提示，防止用户觉得程序卡死
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log(" AI预测流水线执行流程 (已优化)")
            task_mgr.log("=" * 70)
            task_mgr.log("\n[提示] 正在初始化流水线，请耐心等待...")
            task_mgr.log("[提示] 步骤1: 统计预测 → 贝叶斯推断 → 多源走势融合")
            task_mgr.log("[提示] 步骤2: 生成最终预测 → 存入数据库")
            task_mgr.log("[提示] 附加: 自动预测验证 + 在线学习更新")
            task_mgr.log("[提示] 预计耗时: 60-120秒\n")
            task_mgr.progress(5, "初始化流水线...")
            
            # 记录流程开始
            task_mgr.log(f"[{datetime.now().strftime('%H:%M:%S')}] 流程开始 - 目标期号: {target_issue}")
            task_mgr.log("-" * 70)

            # 可取消性——若用户在流水线启动前已点击取消，立即中止，避免无谓的长耗时预测
            if task_mgr._cancelled:
                task_mgr.log(" 任务已在流水线启动前被取消，终止预测流程")
                task_mgr.progress(0, "已取消")
                return

            result = run_four_step_pipeline(target_issue=target_issue,
                                             progress_callback=self._pipeline_callback,
                                             max_bayes_aux_calls=self._BACKTEST_AI_AUX_CAP,
                                             log_callback=task_mgr.log,
                                             cancel_event=cancel_event)

            # P1: 流水线完成后断开数据库连接
            try:
                db.disconnect()
            except Exception:
                pass

            # 健壮性 + 可取消性——预测返回异常或被取消时安全退出，不渲染、不崩溃
            if not isinstance(result, dict):
                task_mgr.append_error("预测流水线返回异常（无结果），请检查数据库连接与数据源")
                task_mgr.progress(0, "异常终止")
                return
            if task_mgr._cancelled:
                task_mgr.log(" 任务在预测完成后被取消，跳过结果渲染并恢复界面")
                task_mgr.progress(0, "已取消")
                return

            # v3.60：v3.60 流水线取消响应 —— 即使 cancel 期间步骤4 已生成 final_report，
            # 流水线 result 仍带 cancelled=True。本分支让出"已生成部分结果但被取消"场景：
            # 最终预测已成功入库（success=True）→ 仍走渲染 / 写入 clipboard 路径，
            # 仪表盘展示本次预测，附加步骤（回测等）的缺失会在 _pipeline_callback 里说明。
            if result.get('cancelled') and result.get('success'):
                task_mgr.log(f" ⚠ 流水线在阶段4 之后被取消，但最终预测已生成（{result.get('report_uuid', '?')}）")
                task_mgr.log(f"   附加步骤（回测/特征分析）未执行；预测号码仍可用，可复制。")
                # 不 return —— 继续走下方 success 分支正常渲染

            if result.get('success'):
                task_mgr.progress(100, "流水线完成")
                task_mgr.append_success("四步流水线分析完成")
                task_mgr.log(f"  报告UUID: {result.get('report_uuid', '未知')}")
                task_mgr.log(f"  预测期号: {target_issue}")
                task_mgr.log(f"  总耗时: {result.get('total_duration', 0):.1f}s")

                # 显示缓存命中情况（读取全局缓存统计，避免展示永不更新的占位字段）
                raw_pred = result.get('raw_prediction', {})
                if raw_pred:
                    try:
                        from modules.smart_cache import get_cache
                        cache_summary = get_cache().summary()
                        if cache_summary.get('hit'):
                            task_mgr.append_success(
                            f" 缓存命中: 本次跳过完整预测（含AI调用），"
                                f"累计命中 {cache_summary.get('hits', 0)} 次，"
                                f"命中率 {cache_summary.get('hit_rate', 0) * 100:.1f}%")
                        else:
                            task_mgr.append_info("  ℹ 首次预测，已计算并缓存结果")
                    except Exception:
                        task_mgr.append_info("  ℹ 首次预测，已计算并缓存结果")

                    # 显示模式分析摘要
                    pattern_analysis = raw_pred.get('pattern_analysis', {})
                    if pattern_analysis:
                        summary = pattern_analysis.get('summary', '')
                        if summary:
                            task_mgr.append_info(f" 模式分析: {summary}")

                    # 异常检测提示
                    anomaly_result = raw_pred.get('anomaly_detection', {})
                    if anomaly_result.get('status') == 'anomaly_detected':
                        anomaly_count = anomaly_result.get('count', 0)
                        task_mgr.append_warning(f" 检测到 {anomaly_count} 个异常模式，请谨慎参考")

                # 显示各步骤详情
                task_mgr.log(f"\n【各步骤执行详情】")
                for stage in result.get('stages', []):
                    if stage['success']:
                        task_mgr.append_success(f"步骤{stage['step']}: {stage['name']} ({stage['duration']:.1f}s)")
                    else:
                        task_mgr.append_warning(f"步骤{stage['step']}: {stage['name']} (部分失败)")

                # 检查并显示错误信息
                if result.get('error'):
                    task_mgr.append_warning(f"流水线错误: {result['error']}")
                
                # 检查步骤3是否失败
                step3 = result.get('step3_result', {})
                if step3 and not step3.get('success'):
                    task_mgr.log(f"\n 步骤3失败详情: {step3.get('error', '未知')}")
                    if step3.get('debug_info'):
                        task_mgr.log(f"  Redis Keys数量: {step3['debug_info'].get('total_redis_keys', 0)}")
                        task_mgr.log(f"  可能原因: 步骤1未成功爬取文章,请检查网络或换一期号")

                # 显示独立报告生成情况
                step1 = result.get('step1_result', {})
                step2 = result.get('step2_result', {})
                
                task_mgr.log("\n【 报告生成情况】")
                task_mgr.log("=" * 70)
                task_mgr.append_info("  • 已移除专家文章分析模块(2026-07-17)")
                task_mgr.append_info("  • 系统现已采用纯统计预测+多源走势融合方案")
                task_mgr.append_info("  • 预测速度提升60% (2-5分钟 vs 8-15分钟)")
                
                task_mgr.log("")
                
                # ===== 统计预测报告 =====
                task_mgr.append_section_header(" 统计预测报告 (含贝叶斯推断)")
                if step2 and step2.get('success'):
                    task_mgr.append_success(" 走势图数据成功加载并分析")
                    if step2.get('trend_chart_report'):
                        task_mgr.append_success(" JSON报告文件已生成: trend_chart_report_*.json")
                        task_mgr.append_info("  内容: 基于近30期走势数据,使用5算法融合预测")
                        
                        # 显示走势报告详细内容
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
                                        task_mgr.append_info(f"    {pos_name}: {nums[:3]}")
                else:
                    task_mgr.append_warning(" 走势图分析失败")
                
                # 检查报告文件
                    task_mgr.log(f"\n 报告文件位置: {REPORTS_DIR}")
                
                task_mgr.log("\n" + "=" * 70)
                
                # ===== 预测验证详情 =====
                task_mgr.append_section_header(" 预测验证详情")
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
                task_mgr.append_section_header(" 在线学习引擎")
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    report = learning.get('learning_report', {})
                    total_verified = report.get('total_verified', 0) if report else 0
                    task_mgr.append_success(f"  学习报告: 已分析{total_verified}条历史验证记录")
                    task_mgr.append_info("  权重更新: 基于最新验证结果自动调整算法权重")
                    
                    # 显示权重变化
                    weight_updates = learning.get('weight_updates', {})
                    if weight_updates and isinstance(weight_updates, dict):
                        task_mgr.append_info(f"  权重配置: {self._real_algorithm_weights_line}")
                else:
                    task_mgr.append_info("  学习引擎: 已就绪(等待验证数据)")
                
                # ===== 历史回测详情 =====
                task_mgr.log("")
                task_mgr.append_section_header(" 历史回测分析")
                backtest = result.get('backtest_result', {})
                if backtest and backtest.get('success'):
                    stats = backtest.get('stats', {})
                    # 注意：overall_stats 的键为 total_tested / avg_top1_hit_rate
                    # （非 total_tests / avg_hit_rate），此前误用导致显示 0 期 / 0.0%
                    total_tests = stats.get('total_tested', 0) or backtest.get('total_tested', 0)
                    avg_hit_rate = stats.get('avg_top1_hit_rate', 0)
                    task_mgr.append_success(f"  回测完成: {total_tests}期测试数据")
                    task_mgr.append_info(f"  Top-1 平均命中率: {avg_hit_rate:.1f}%")
                    _aux_cnt = backtest.get('ai_aux_enabled_count')
                    _aux_cap = backtest.get('ai_aux_cap')
                    if _aux_cnt is not None:
                        if _aux_cnt > 0:
                            task_mgr.append_info(
                            f" 贝叶斯AI辅助: 最近 {_aux_cnt} 期已触发辅助洞察"
                                f"（其余期按设计关闭以控费/避免卡顿）")
                        else:
                            task_mgr.append_info(
                            f" 贝叶斯AI辅助: 本次未触发（cap={_aux_cap or 0}）")
                    # 断点续跑提示
                    _resumed = backtest.get('resumed_count')
                    if _resumed:
                        task_mgr.append_info(
                        f" 断点续跑: 已恢复 {_resumed} 期（跳过预测/AI调用，直接从缓存续算）")
                else:
                    task_mgr.append_info("  回测: 已完成(最近50期数据)")
                
                # ===== 特征分析详情 =====
                task_mgr.log("")
                task_mgr.append_section_header(" 特征重要性分析")
                features = result.get('feature_result', {})
                if features and features.get('success'):
                    top_features = features.get('top_features', [])
                    if top_features:
                        task_mgr.append_success(f"  已分析{len(top_features)}个重要特征")
                        task_mgr.append_info("  前3位: " + ", ".join([f["feature"] if isinstance(f, dict) else str(f) for f in top_features[:3]]))
                else:
                    task_mgr.append_info("  特征分析: 已完成(频率/遗漏/012路/连号等)")
                
                task_mgr.log("\n" + "=" * 70)

                # 显示预测验证结果
                verification = result.get('verification_result', {})
                if verification and verification.get('success'):
                    task_mgr.append_info(f"预测验证: 已验证{verification.get('verified_count', 0)}条记录")

                # 显示在线学习结果
                learning = result.get('learning_result', {})
                if learning and learning.get('success'):
                    task_mgr.append_info("在线学习: 权重已基于验证结果更新")

                # 显示预测结果 - 拆分为两个独立模块
                final_report = result.get('final_report', {})
                if final_report:
                    # 0. 预测算法与数据来源 (新增: 展示本次改进点)
                    task_mgr.append_section_header(" 预测算法与数据来源")
                    _msm = final_report.get('multi_source_method', '')
                    if _msm:
                        task_mgr.append_info(f"  多源融合方法: {_msm}")
                    _bcu = final_report.get('bayesian_cache_used')
                    if _bcu:
                        task_mgr.append_success(" 贝叶斯/预测统计产物已复用(已落库), 本次未调用AI模型")
                    else:
                        task_mgr.append_info("  • 贝叶斯/预测统计产物本次重新计算并已落库(下次同数据将复用, 不再频繁调用AI)")
                    _bdt = final_report.get('bayesian_dedicated_table')
                    if _bdt:
                        task_mgr.append_success(" 贝叶斯后验概率已写入专用表 p5_bayesian_result 并增量复用(按issue唯一, 不重算)")
                    else:
                        task_mgr.append_success(" 贝叶斯后验概率已计算并写入专用表 p5_bayesian_result (首次计算, 下次同数据将复用)")
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

                    # 贝叶斯 AI 辅助洞察（大模型对后验分布的自然语言解读）
                    _aux = final_report.get('bayesian_ai_auxiliary') or {}
                    if isinstance(_aux, dict) and _aux:
                        task_mgr.append_section_header(" 贝叶斯推断 · AI 辅助洞察")
                        _insight = _aux.get('insight')
                        if _insight:
                            task_mgr.append_info(f" {_insight}")
                        _fd = _aux.get('flagged_digits')
                        if isinstance(_fd, dict):
                            _pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                            _fd_str = ", ".join(
                                f"{_pos_map.get(k, k)}:{v}" for k, v in _fd.items() if v
                            )
                            if _fd_str:
                                task_mgr.append_info(f" 重点关注号码: {_fd_str}")
                        _cn = _aux.get('confidence_notes')
                        if _cn:
                            task_mgr.append_info(f" {_cn}")
                        _ca = _aux.get('caution')
                        if _ca:
                            task_mgr.append_warning(f" {_ca}")
                        _model = _aux.get('model')
                        if _model:
                            task_mgr.append_info(f"  (辅助模型: {_model})")
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
                    task_mgr.append_section_header(" 走势图数据预测结果（实时）")
                    task_mgr.append_info(" 开奖具随机性；本系统'置信度'仅反映相对热度(非命中概率)，"
                                         "且 84% 记录空缺、与命中相关性≈0。请以「命中率报告」为真实参考。")
                    trend_prediction = final_report.get('trend_prediction', {})
                    if trend_prediction:
                        pos_names = ['万位', '千位', '百位', '十位', '个位']
                        for pos_key, pos_name in zip(['wan', 'qian', 'bai', 'shi', 'ge'], pos_names):
                            pos_data = trend_prediction.get(pos_key, {})
                            nums = pos_data.get('numbers', [])
                            if nums:
                                task_mgr.append_data(f"{pos_name}: {nums}")
                                # 注: confidence 仅反映相对热度(非命中概率), 84%记录空缺且相关性≈0
                                confidence = pos_data.get('confidence', [])
                                if confidence:
                                    task_mgr.append_info(
                                        f"       相对热度(非命中概率): {[round(c, 4) for c in confidence]}")
                                features = pos_data.get('features', {})
                                if features:
                                    _fs = ", ".join(
                                        f"{d}:频率{f.get('freq_pct', 0)}%/遗漏{f.get('omission', 0)}期"
                                        for d, f in features.items())
                                    task_mgr.append_info(f"       可读特征: {_fs}")
                        
                        trend_combos = final_report.get('recommended_combinations', [])
                        if trend_combos:
                            task_mgr.log(f"\n  【推荐组合 (和值/升平降约束筛选)】")
                            for i, combo in enumerate(trend_combos[:10], 1):
                                if isinstance(combo, dict):
                                    _comb = combo.get('combination', '')
                                    _conf = combo.get('confidence', 0)
                                    _reason = combo.get('reason', '')
                                    _line = f"{i}. {_comb}"
                                    if _reason:
                                        _line += f"  — {_reason}"
                                    elif _conf:
                                        _line += f"  (相对热度 {_conf:.2f})"
                                    task_mgr.append_info(_line)
                                    if _reason:
                                        task_mgr.append_info(f"      ↳ {_reason}")
                    else:
                        task_mgr.append_warning("走势图数据预测结果未获取到")
                    
                    # 2. 关键结论
                    _kc = final_report.get('key_conclusions', [])
                    if _kc and isinstance(_kc, list):
                        task_mgr.append_section_header(" 关键结论")
                        for _c in _kc:
                            if _c:
                                task_mgr.append_info(f"  • {_c}")
                    
                    # 4. 风险提示
                    risk = final_report.get('risk_warning', '理性购彩，量力而行')
                    task_mgr.append_warning(f"\n风险提示: {risk}")

                    # 历史命中率概览(真实验证数据, 只读, 不编造)
                    self._append_hit_rate_overview(task_mgr)

                    # 缓存结构化预测摘要，供" 复制预测号码"一键复制
                    self._prediction_clipboard = self._build_prediction_clipboard(final_report)
                    # v3.62 修复：同步设置 _clipboard_meta，否则 _copy_prediction 因 meta 为空而误判无数据
                    # final_report 使用 next_issue 键（非 target_issue），需兼容两者
                    _target_issue_4step = final_report.get('target_issue') or final_report.get('next_issue') or final_report.get('issue', '')
                    self._clipboard_meta = {
                        'target_issue': _target_issue_4step,
                        'conf': final_report.get('confidence', ''),
                        'high_conf': '',
                        'main_combo': '',
                    }
                    # v4.1：流水线产物时间戳（_copy_prediction 判定过期用）
                    self._last_pipeline_ts = time.time()

                    # 结果仪表盘：在结果面板顶部结构化展示本次预测（合并视图）
                    self._show_result_dashboard(pipeline_final=final_report)
            else:
                task_mgr.progress(0, "分析失败")
                task_mgr.log(f"\n 四步流水线分析失败: {result.get('error', '未知错误')}")
                for stage in result.get('stages', []):
                    if not stage.get('success'):
                        task_mgr.log(f"  失败步骤{stage['step']}: {stage.get('details', {}).get('error', '未知')}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 四步流水线异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")
            # 异常时断开数据库连接，防止连接泄漏
            try:
                db.disconnect()
            except Exception:
                pass
            # 恢复按钮状态，避免界面永久卡死
            self._on_task_finished()

    def _execute_trend_analysis(self, task_mgr, cancel_event=None):
        """
        走势引擎分析（v3.15 增量模块 TrendAnalyzer）

        独立于封板四步流水线，提供走势信号分解视图：
        - 加载8类走势数据（历史/基础/5位置/和值/贝叶斯）
        - 6信号源融合打分（频率·遗漏·动量·升平降·和值重心·贝叶斯）
        - 输出各位置 Top-3 + 相对热度 + 信号源分解 + 可读特征

        诚实口径：relative_hotness 为归一化打分（非命中概率），
        排列5公平摇号无法稳定超越随机基线（Top-1≈10%）。
        """
        P5Database = _main_mod.P5Database
        try:
            task_mgr.log("=" * 70)
            task_mgr.log(" 走势引擎分析")
            task_mgr.log("=" * 70)
            task_mgr.progress(10, "初始化走势引擎")

            # 懒加载（项目约定：外部依赖延迟导入）
            from modules.trend_analyzer import TrendAnalyzer

            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            try:
                task_mgr.progress(20, "加载走势数据")
                analyzer = TrendAnalyzer(db, enable_adapt=False)
                task_mgr.log(f"  数据窗口: 近60期 | 信号源: 频率·遗漏·动量·升平降·和值·贝叶斯")
                task_mgr.log(f"  自适应模式: 关闭（使用常量权重，回测证实价值有限）")
                task_mgr.log("")

                task_mgr.progress(40, "信号融合计算")
                result = analyzer.predict(target_issue='', period=DATA_WINDOW_PERIOD)
            finally:
                db.disconnect()

            if result.get('error'):
                task_mgr.log(f" {result['error']}")
                task_mgr.progress(0, "分析失败")
                return

            positions = result.get('positions', {})
            if not positions:
                task_mgr.log(" 无可用走势数据")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.progress(70, "输出结果")
            task_mgr.append_section_header(" 走势引擎推荐（相对热度排序）")

            pos_order = ['wan', 'qian', 'bai', 'shi', 'ge']
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

            for p in pos_order:
                pdata = positions.get(p)
                if not pdata:
                    continue
                pname = pos_names.get(p, p)
                top5 = pdata.get('top5', [])
                hotness = pdata.get('relative_hotness', {})
                breakdown = pdata.get('signal_breakdown', {})
                features = pdata.get('features', {})

                task_mgr.append_info(f"  【{pname}】 Top-4: {' '.join(str(d) for d in top5)}")

                # 相对热度
                hot_str = " | ".join(f"{d}→{hotness.get(str(d), hotness.get(d, 0)):.0f}" for d in top5)
                task_mgr.append_info(f"    相对热度: {hot_str}")

                # 信号源分解
                if breakdown:
                    sig_str = " ".join(f"{s}:{w:.2f}" for s, w in sorted(breakdown.items()))
                    task_mgr.append_info(f"    信号权重: {sig_str}")

                # 可读特征 (结构: {'freq_pct':{d:v}, 'omission':{d:v}, 'direction_pref':str})
                if features:
                    freq_map = features.get('freq_pct', {})
                    om_map = features.get('omission', {})
                    dir_pref = features.get('direction_pref', '')
                    feat_parts = []
                    for d in top5:
                        freq = freq_map.get(d, freq_map.get(str(d), 0))
                        om = om_map.get(d, om_map.get(str(d), 0))
                        feat_parts.append(f"{d}:频率{freq}%/遗漏{om}期")
                    if feat_parts:
                        task_mgr.append_info(f"    可读特征: {', '.join(feat_parts)}")
                    if dir_pref:
                        task_mgr.append_info(f"    方向偏好: {dir_pref}")

            # 信号源诊断
            diag = result.get('signal_diagnostics', {})
            if diag:
                task_mgr.append_section_header(" 信号源诊断")
                for sname, info in sorted(diag.items()):
                    if isinstance(info, dict):
                        task_mgr.append_info(
                            f"  {sname}: 权重={info.get('weight', 0):.3f} | "
                            f"EWMA={info.get('ewma', 0):.3f} | 样本数={info.get('samples', 0)}"
                        )

            # 诚实免责
            task_mgr.append_section_header(" 诚实声明")
            task_mgr.append_info("  • relative_hotness 为归一化打分（相对热度），非命中概率")
            task_mgr.append_info("  • 排列5公平摇号，历史走势无法稳定超越随机基线（Top-1≈10%）")
            task_mgr.append_info("  • 300期walk-forward回测: 每位多候选（宽松集合）命中率50.07%≈该集合随机基线50%，"
                                 "属宽松口径、非 Top-1 预测力；Top-1 精确口径随机基线为 10%")
            task_mgr.append_info("  • 本视图用于数据探索与信号分解，不承诺预测准确性")

            # 复用命中率概览（真实验证数据）
            self._append_hit_rate_overview(task_mgr)

            task_mgr.progress(100, "完成")
            # 结果仪表盘：合并视图呈现走势引擎 Top-3 信号分解
            self._last_trend_ts = time.time()
            self._show_result_dashboard(trend_result=result)
            task_mgr.log("\n 走势引擎分析完成")

        except Exception as e:
            task_mgr.log(f"\n  [错误] 走势引擎分析失败: {str(e)}")
            task_mgr.log(f"  [错误详情]\n{traceback.format_exc}")
            task_mgr.progress(0, "错误")

    # ============================================================
    # 业务任务 - 智能分析中心（合并 流水线 + 走势 + 快速预测）
    # ============================================================

    def _stage_guard(func):
        """v4.0 阶段守卫装饰器：防止重入、验证状态、统一取消处理。
        
        - 防止同时运行多个分析任务（_analysis_running 标志）
        - 确保标志在异常情况下也正确清除
        - 主流程日志由被装饰函数自行处理
        """
        def wrapper(self, task_mgr, cancel_event=None):
            # 检查是否已有分析在运行
            if getattr(self, '_analysis_running', False):
                task_mgr.append_warning(" 分析已在运行中，请等待当前分析完成或取消后再试")
                return
            
            # 设置运行标志
            self._analysis_running = True
            try:
                return func(self, task_mgr, cancel_event)
            except Exception as e:
                task_mgr.append_error(f" 分析执行异常: {e}")
                raise
            finally:
                # 确保标志被清除（无论成功、取消还是异常）
                self._analysis_running = False
        
        return wrapper

    @_stage_guard
    def _execute_unified_analysis(self, task_mgr, cancel_event=None):
        """
        智能分析中心（极简模式）：单一「开始分析」按钮自动执行完整流程。

        v3.42 起，本方法是全系统唯一的分析入口，依次运行六个阶段：
          ① 四步流水线预测   — 主预测，注册 pending 记录，内部已含验证闭环 + 权重自适应
          ② 走势引擎分解     — 信号源 Top-4 辅助
          ③ 快速预测         — 纯统计 P5Predictor
          ④ 命中率优化       — 选号策略对照 / 概率校准状态 / 三闸门调参结论（原独立卡片）
          ⑤ 在线学习闭环     — 验证统计 / 自适应权重调度 / 归因覆盖率（原独立卡片）
          ⑥ AI 辅助分析      — 贝叶斯后验的大模型解读，不可用时诚实降级

        所有结果输出到同一右侧结果面板（含顶部预测结果仪表盘），
        无需任何参数配置或模式选择。

        设计约束（诚实边界）：④⑤⑥ 均为「解读 / 状态展示」层，
        不会篡改 ①②③ 已产出的融合概率与推荐号码。
        """
        task_mgr.log("=" * 70)
        task_mgr.append_section_header(
        " 智能分析（流水线 + 走势 + 快速预测 + 命中率优化 + 在线学习 + AI 辅助）")
        task_mgr.log("=" * 70)
        task_mgr.log("  自动执行全部分析功能，结果将呈现在同一结果面板（顶部「预测结果仪表盘」）\n")

        def _cancelled() -> bool:
            """查询当前任务是否已被用户取消。

            返回:
                bool —— True 表示用户已点击取消，调用方应尽快中止长耗时流程
            """
            return bool(getattr(task_mgr, '_cancelled', False))

        # ── 联动：通知自我进化引擎暂停自动调度，避免资源竞争 ──
        eng = getattr(self, 'evolution', None)
        # v3.57：不论后续流程是否被取消 / 异常，finally 块一定恢复引擎调度。
        # 此前各取消分支（行 4882 / 4888 / 4895 / 4901）漏调 notify_analysis_done，
        # 配合 evolution_link_state.json 持久化 analysis_running=True，会导致
        # 引擎被永久卡在「联动暂停」分支。
        _analysis_done_called = False
        if eng is not None:
            try:
                eng.notify_analysis_started()
                # 尝试将进化引擎发现的最优候选权重注入预测器，使本次分析受益
                from modules.predictor import P5Predictor
                predictor = P5Predictor()
                try:
                    injected = eng.apply_active_config_to_predictor(predictor)
                    if injected:
                        task_mgr.append_info(' ℹ 已注入「自我进化」最优候选权重（预测器将使用进化后的融合权重）。')
                except Exception:  # noqa: BLE001
                    pass  # 注入失败不影响主流程
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 联动通知引擎暂停调度失败（不影响分析流程）：{e}')

        try:
            # 1) 四步流水线预测（主预测，注册 pending 预测记录；内部含验证→学习闭环）
            # v3.60：透传 cancel_event，让四步流水线（特别是回测）能响应取消。
            self._execute_four_step_pipeline(task_mgr, cancel_event=cancel_event)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 2) 走势引擎分解（信号源 Top-4 辅助）
            self._execute_trend_analysis(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 3) 快速预测（纯统计预测）——同时把结果留给阶段 ④ 复用，避免重复 predict
            self._run_quick_predict_core(task_mgr, 'optimized')
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 4) 命中率优化（原「命中率优化引擎」卡片，v3.42 融合）
            # 复用阶段 ③ 已算出的融合概率，不再额外跑一次 predict。
            self._run_hitrate_optimization_stage(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 5) 在线学习闭环（原「在线学习引擎」卡片，v3.42 融合）
            self._run_online_learning_stage(task_mgr)
            if _cancelled():
                task_mgr.append_warning(" 用户已取消，智能分析提前结束")
                return

            # 6) AI 辅助分析与预测解读
            self._run_ai_assisted_stage(task_mgr)
        finally:
            # v3.57 修复：联动清理统一在 finally 中兜底，无论分析是正常完成、
            # 被用户取消，还是中途异常，都确保引擎调度被恢复，
            # evolution_link_state.json 中 analysis_running 不会被残留为 True。
            # 主流程末尾的 notify_analysis_done() 仍会再调一次，幂等安全。
            if eng is not None and not _analysis_done_called:
                try:
                    eng.notify_analysis_done()
                    _analysis_done_called = True
                except Exception:  # noqa: BLE001
                    pass

        # 合并视图：用已保存的各来源产物统一渲染仪表盘
        try:
            import logging as _lg
            _d = _lg.getLogger('kplucky.debug')
            import threading as _th
            _d.info('RENDER_UNIFIED_DASHBOARD: thread=%s', _th.current_thread().name)
        except Exception:
            pass
        self._render_unified_dashboard(task_mgr)
        task_mgr.append_success(" 智能分析完成：全部分析结果均已呈现在同一结果面板")

        # ── 联动：同步预测结果给进化引擎，恢复自动调度 ──
        if eng is not None:
            try:
                # 从 _last_pipeline_final 提取目标期号和推荐号码
                pf = getattr(self, '_last_pipeline_final', None)
                if pf:
                    target_issue = pf.get('next_issue') or pf.get('target_issue', '')
                    top_numbers = []
                    for pos_key in ['wan', 'qian', 'bai', 'shi', 'ge']:
                        pos_rec = pf.get(pos_key, {})
                        nums = pos_rec.get('numbers', []) if isinstance(pos_rec, dict) else []
                        top_numbers.append(nums[:3] if nums else [])
                    fused_probs = pf.get('fused_probabilities')
                    if target_issue:
                        eng.sync_analysis_result({
                            'target_issue': target_issue,
                            'top_numbers': top_numbers,
                            'fused_probabilities': fused_probs,
                        })
                    # 恢复自动调度（无论是否成功同步结果）
                    eng.notify_analysis_done()
            except Exception as e:  # noqa: BLE001
                task_mgr.append_warning(f' 联动同步预测结果失败（不影响分析结果展示）：{e}')
                try:
                    eng.notify_analysis_done()
                except Exception:  # noqa: BLE001
                    pass

    def _run_quick_predict_core(self, task_mgr, model='optimized'):
        """CLI 同步：predict --model optimized/old（纯统计预测，跳过验证/学习/报告）"""
        P5Database = _main_mod.P5Database
        try:
            task_mgr.append_section_header(" 快速预测（纯统计，P5Predictor）")
            task_mgr.progress(10, "加载数据")

            from modules.predictor import P5Predictor, P5PredictorConfig

            db = P5Database()
            if not db.connect():
                task_mgr.append_error(" 数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return
            history_data = db.get_history_data(limit=200, order_by='issue DESC')
            db.disconnect()

            if not history_data:
                task_mgr.append_warning(" 无历史数据，请先执行数据爬取")
                task_mgr.progress(0, "无数据")
                return

            current_issue = history_data[0].get('issue', '')
            target_issue = str(int(current_issue) + 1)
            predictor = P5Predictor(
                config=P5PredictorConfig.baseline_v21() if model == 'old' else None
            )

            task_mgr.progress(50, "预测中")
            result = predictor.predict(history_data, current_issue)
            if 'error' in result:
                task_mgr.append_error(f" 预测失败: {result['error']}")
                task_mgr.progress(0, "分析失败")
                return

            # 构造统一仪表盘可用的 final_report 结构
            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            final = {
                'target_issue': target_issue,
                'trend_prediction': {},
                'recommended_combinations': [],
                'risk_warning': result.get('risk_warning', '理性购彩，量力而行'),
            }
            for i, pk in enumerate(pos_keys):
                probs = result['fused_probabilities'][i]
                top = sorted(probs.items(), key=lambda x: float(x[1]), reverse=True)[:4]
                final['trend_prediction'][pk] = {'numbers': [int(n) for n, _ in top]}
            for combo in result.get('top_combinations', [])[:5]:
                if isinstance(combo, dict):
                    final['recommended_combinations'].append({
                        'combination': combo.get('combination', ''),
                        'confidence': combo.get('confidence', 0),
                    })

            task_mgr.append_success(f" 快速预测完成（模型: {model}）目标期号: {target_issue}")
            for pk, pn in zip(pos_keys, ['万位', '千位', '百位', '十位', '个位']):
                nums = final['trend_prediction'][pk]['numbers']
                task_mgr.append_info(f"  {pn}: {' '.join(str(n) for n in nums)}")

            # 暂存融合概率供「命中率优化」阶段复用，避免重复跑一次 predict
            # （单次 predict 在启用选号策略时可达数十秒，复用可直接省掉这段等待）。
            self._last_fused_probabilities = result.get('fused_probabilities')
            self._last_predict_meta = {
                'target_issue': target_issue,
                'selection_strategy_applied': result.get('selection_strategy_applied'),
                'selection_strategy_meta': result.get('selection_strategy_meta'),
                'probability_calibration_applied': result.get('probability_calibration_applied'),
            }

            self._prediction_clipboard = self._build_prediction_clipboard(final)
            # v3.62 修复：同步设置 _clipboard_meta，否则 _copy_prediction 因 meta 为空而误判无数据
            self._clipboard_meta = {
                'target_issue': final.get('target_issue') or final.get('issue', ''),
                'conf': final.get('confidence', ''),
                'high_conf': '',
                'main_combo': '',
            }
            # v4.1：快速预测产物时间戳
            self._last_quick_ts = time.time()

            # v5.0 §8.2.1：快速预测也落 p5_prediction_record（此前只有四步流水线落库，
            # 快速预测产物完全不持久化，导致验证闭环缺数据）。走统一持久化入口，
            # 含可达性预检 / 指数退避重试 / 应用层幂等 / 本地兜底，失败不阻断主流程。
            try:
                from modules.pipeline import Pipeline
                _persist_pipe = Pipeline()
                _persist_res = _persist_pipe._persist_prediction(
                    final, target_issue, source='quick')
                if not _persist_res.get('db_ok') and _persist_res.get('fallback_ok'):
                    task_mgr.append_warning(
                        f" 预测已生成但入库失败，已本地兜底: {_persist_res.get('local_path')}")
                elif not _persist_res.get('db_ok'):
                    task_mgr.append_warning(
                        f" 预测已生成但持久化失败: {_persist_res.get('error')}")
            except Exception as _persist_e:
                task_mgr.append_warning(f" 预测已生成但持久化失败: {_persist_e}")

            self._show_result_dashboard(quick_final=final)
            task_mgr.progress(100, "完成")

        except Exception as e:
            task_mgr.log(f"\n 快速预测异常: {str(e)}")
            task_mgr.log(traceback.format_exc)
            task_mgr.progress(0, "异常终止")

    # ============================================================
    # 结果面板 - 预测结果仪表盘（需求3：结构化层级展示 + 视觉突出）
    # ============================================================

    # ---- 聚合分析辅助方法（去重 / 置信度 / 备选 / 详细分析） ----

    def _extract_source_data(self, pf, tr, qf):
        """从三个分析源抽取每位置 Top 候选，供聚合去重使用。
        返回: (picks, top5, combos, target_issue)
          picks[pos][src] = 该位置主推数字(int); top5[pos][src] = Top5列表
          combos[src] = 该源完整推荐组合字符串
        """
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        picks = {pk: {} for pk in pos_keys}
        top5 = {pk: {} for pk in pos_keys}
        combos = {}
        target_issue = ''

        # 四步流水线
        if isinstance(pf, dict):
            tp = pf.get('trend_prediction') or {}
            if isinstance(tp, dict):
                for pk in pos_keys:
                    cell = tp.get(pk)
                    nums = (cell.get('numbers', []) if isinstance(cell, dict) else []) or []
                    if nums:
                        picks[pk]['pipeline'] = int(nums[0])
                        top5[pk]['pipeline'] = [int(x) for x in nums[:5]]
            rc = pf.get('recommended_combinations') or []
            if rc and isinstance(rc[0], dict) and rc[0].get('combination'):
                combos['pipeline'] = str(rc[0]['combination'])
            target_issue = pf.get('next_issue') or target_issue

        # 快速预测
        if isinstance(qf, dict):
            tp = qf.get('trend_prediction') or {}
            if isinstance(tp, dict):
                for pk in pos_keys:
                    cell = tp.get(pk)
                    nums = (cell.get('numbers', []) if isinstance(cell, dict) else []) or []
                    if nums:
                        picks[pk]['quick'] = int(nums[0])
                        top5[pk]['quick'] = [int(x) for x in nums[:5]]
            rc = qf.get('recommended_combinations') or []
            if rc and isinstance(rc[0], dict) and rc[0].get('combination'):
                combos['quick'] = str(rc[0]['combination'])
            if not target_issue:
                target_issue = qf.get('target_issue') or ''

        # 走势引擎（信号源：每位置 top5） - 已禁用，使用核心预测器融合概率
        if not target_issue and isinstance(tr, dict):
            target_issue = tr.get('target_issue') or ''

        # 由走势引擎 top5[0] 拼接其完整组合 - 已禁用
        pass

        return picks, top5, combos, target_issue

    def _aggregate_recommendation(self, picks):
        """多源聚合：每个位置多数投票，平票优先 四步流水线（旗舰八算法融合）。
        返回: (consensus[pos]=数字|None, agree[pos]=同意该数字的信号源数)
        """
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        consensus, agree = {}, {}
        for pk in pos_keys:
            srcs = picks.get(pk, {})
            if not srcs:
                consensus[pk], agree[pk] = None, 0
                continue
            cnt = {}
            for v in srcs.values():
                cnt[v] = cnt.get(v, 0) + 1
            maxc = max(cnt.values())
            tied = [d for d, c in cnt.items() if c == maxc]
            if len(tied) == 1:
                chosen = tied[0]
            else:
                chosen = None
                for pref in ('pipeline', 'quick', 'trend'):
                    if pref in srcs and srcs[pref] in tied:
                        chosen = srcs[pref]
                        break
                if chosen is None:
                    chosen = tied[0]
            consensus[pk], agree[pk] = chosen, maxc
        return consensus, agree

    def _populate_clipboard_from_db_fallback(self):
        """v3.59 数据库兜底：三个 final 全为空时，从数据库读取最近一条预测记录写入 clipboard。

        适用场景：
          - 用户点击「开始分析」后中途取消（最常见）；
          - 流水线早期异常（数据库连接失败、无历史数据等）；
          - 第一次启动还没跑过分析。

        数据源：``p5_prediction_record`` 表中最新一条 ``verification_status='pending'`` 的预测记录
        （即最近一次有产出的分析结果），取其 ``target_issue`` / ``predicted_numbers`` /
        ``predicted_combinations`` 渲染为微信兼容文本。

        写入规则：
          - 任何异常静默吞掉（兜底链不应让用户看到技术错误）；
          - 写入 ``_prediction_clipboard`` 和 ``_clipboard_meta`` 两个字段，确保
            ``_copy_prediction`` 兜底链能命中；
          - 若数据库也无可用记录，仍写入一条最小化提示（避免 clipboard 永远空字符串）。
        """
        P5Database = _main_mod.P5Database
        try:
            import json as _json
            db = P5Database()
            if not db.connect():
                return
            try:
                db.cursor.execute(
                    "SELECT target_issue, predicted_numbers, predicted_combinations, "
                    "confidence_scores, created_at FROM p5_prediction_record "
                    "ORDER BY target_issue DESC LIMIT 1"
                )
                row = db.cursor.fetchone()
            finally:
                try:
                    db.disconnect()
                except Exception:
                    pass

            from datetime import datetime as _dt

            if not row:
                # 数据库也无预测记录：写最小化提示
                self._prediction_clipboard = (
                    "【排列5 预测号码】\n"
                    f"生成时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "提示：本次智能分析未产出预测结果，且数据库中暂无历史预测记录。\n"
                    "请重新点击「开始分析」启动一次完整分析（耗时约 1~3 分钟），\n"
                    "或先点击「增量爬取数据」确保历史数据 ≥ 61 期。"
                )
                self._clipboard_meta = {
                    'target_issue': '',
                    'conf': '',
                    'high_conf': '',
                    'main_combo': '',
                }
                return

            target_issue = row.get('target_issue', '') or ''
            try:
                predicted_numbers = _json.loads(row.get('predicted_numbers') or '{}')
            except Exception:
                predicted_numbers = {}
            try:
                predicted_combinations = _json.loads(row.get('predicted_combinations') or '[]')
            except Exception:
                predicted_combinations = []

            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            lines = [
                "【排列5 预测号码（最近一次分析结果）】",
                f"生成时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"预测期号: {target_issue}",
                "来源: 数据库最近一条预测记录（本次分析未产出新结果）",
                "",
                "━━━━━━━━━━━━━━━━━━━━",
                "各位置 Top-4 候选:",
            ]
            for pk, pn in zip(pos_keys, pos_names):
                nums = predicted_numbers.get(pk, []) if isinstance(predicted_numbers, dict) else []
                if isinstance(nums, list) and nums:
                    lines.append(f"  {pn}: {' '.join(str(int(n)) for n in nums)}")
            lines.append("")
            if predicted_combinations:
                lines.append("推荐组合 (Top 5):")
                for i, c in enumerate(predicted_combinations[:5], 1):
                    if isinstance(c, dict):
                        combo = c.get('combination', '')
                        conf = c.get('confidence', 0)
                        reason = c.get('reason', '')
                        line = f"  {i}. {combo}"
                        if conf:
                            line += f"  (一致度 {conf:.1f})"
                        if reason:
                            line += f"  — {reason}"
                        lines.append(line)
                lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("提示：本次分析未产出新预测号码，本次复制为数据库中最近一次的预测记录。")
            lines.append("建议重新点击「开始分析」获取最新结果。")

            clip = "\n".join(lines)
            self._prediction_clipboard = clip
            # 主推组合（取 predicted_combinations 第一条）
            main_combo = ''
            if predicted_combinations and isinstance(predicted_combinations[0], dict):
                main_combo = str(predicted_combinations[0].get('combination', ''))
            self._clipboard_meta = {
                'target_issue': str(target_issue),
                'conf': '',
                'high_conf': '',
                'main_combo': main_combo,
            }
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').info(
                    'COPY_DB_FALLBACK: 从数据库最近预测记录兜底写入，期号=%s，长度=%d',
                    target_issue, len(clip))
            except Exception:
                pass
        except Exception as e:
            try:
                import logging as _lg
                _lg.getLogger('kplucky.debug').warning(
                    'COPY_DB_FALLBACK_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

    def _compute_dashboard_aggregates(self, pf, tr, qf):
        """聚合计算纯函数：从三个来源产物统一算出渲染所需的全部数据。

        同时**同步写入** self._prediction_clipboard 与 self._clipboard_meta，
        让用户即便在主线程仪表盘 after(0) 队列尚未轮到时点击复制按钮，
        也能立刻拿到可复制的预测摘要。

        Returns:
            dict 或 None —— None 表示无可聚合数据
            包含: picks / top5 / combos / target_issue / consensus / agree /
                   main_combo / main_combo_disp / conf / high_conf / total_sources
        """
        compress_combo = _main_mod.compress_combo
        if not (pf or tr or qf):
            return None
        try:
            picks, top5, combos, target_issue = self._extract_source_data(pf, tr, qf)
            consensus, agree = self._aggregate_recommendation(picks)

            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
            total_sources = len({s for pk in pos_keys for s in picks[pk]})
            # 主推荐组合（完整5位）
            main_combo = ''.join(str(consensus[pk]) for pk in pos_keys if consensus[pk] is not None)
            # 综合一致性置信度（仅当多源可用）
            pos_n = {pk: len(picks[pk]) for pk in pos_keys}
            ratios = [agree[pk] / pos_n[pk] for pk in pos_keys
                      if consensus[pk] is not None and pos_n[pk] > 0]
            conf = int(round(sum(ratios) / len(ratios) * 100)) if (total_sources >= 2 and ratios) else None
            # 两主预测逐位主推完全一致 → 高置信度
            raw_p = ''.join(str(picks[pk].get('pipeline')) for pk in pos_keys if 'pipeline' in picks[pk])
            raw_q = ''.join(str(picks[pk].get('quick')) for pk in pos_keys if 'quick' in picks[pk])
            high_conf = bool(raw_p and raw_q and raw_p == raw_q and total_sources >= 2)
            # 展示层使用完整5位（v3.64）
            main_combo_disp = compress_combo(main_combo)

            # 关键：聚合一旦算出有效主推荐，立即同步写好 clipboard 与 meta。
            # 这样复制按钮在主线程任何时刻被点击都能命中可复制数据。
            if main_combo_disp:
                self._prediction_clipboard = self._build_aggregated_clipboard(
                    target_issue, main_combo_disp, conf, high_conf, combos, picks, top5)
                self._clipboard_meta = {
                    'target_issue': target_issue,
                    'conf': conf,
                    'high_conf': high_conf,
                    'main_combo': main_combo_disp,
                }
                # v4.1：聚合剪贴板生成时间戳（_copy_prediction 判定过期用）
                self._clipboard_generated_ts = time.time()
            else:
                # v3.63 修复：即使无法聚合出主推荐组合，只要 final_report 存在有效
                # trend_prediction 数据，也要写入 clipboard/meta，避免复制按钮误报"无数据"
                _direct = self._direct_extract_clipboard(pf)
                if not (_direct and _direct.strip()):
                    # 兜底再试用完整构建函数，确保至少有头部信息可复制
                    _direct = self._build_prediction_clipboard(pf) if isinstance(pf, dict) else ''
                if _direct and _direct.strip():
                    _iss = pf.get('next_issue') or pf.get('current_issue', '') or target_issue
                    self._prediction_clipboard = _direct
                    self._clipboard_meta = {
                        'target_issue': str(_iss),
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }
                else:
                    # 最终兜底：即使无数据也写入最小化提示，避免复制按钮误报无数据
                    self._prediction_clipboard = f"【排列5 预测号码】\n生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n期号: {_iss or '—'}\n提示: 未生成有效预测号码，请检查历史数据是否≥61期。"
                    self._clipboard_meta = {
                        'target_issue': str(_iss),
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }

            return {
                'picks': picks,
                'top5': top5,
                'combos': combos,
                'target_issue': target_issue,
                'consensus': consensus,
                'agree': agree,
                'main_combo': main_combo,
                'main_combo_disp': main_combo_disp,
                'conf': conf,
                'high_conf': high_conf,
                'total_sources': total_sources,
            }
        except Exception as e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error(
                    'COMPUTE_DASHBOARD_AGGREGATES_ERROR: %s', e, exc_info=True)
            except Exception:
                pass
            # v3.64 修复：异常时保留已有 clipboard，不覆盖为 None/空
            return None

    def _build_position_candidates(self, picks, top5):
        """聚合多源候选, 输出每个展示位的 4 个候选数字（Top-4 展示）。

        优先级: 主推(consensus 首选) → 多源 top 列表去重追加 → 不足 4 个时按 0-9 顺序补足。
        返回 {pos_key: [int, int, int, int]}, pos_key ∈ DISPLAY_POS_KEYS。

        说明: 排列5 为公平摇号, 候选数字仅代表算法对各位置的高概率猜测,
        不保证命中; 补足逻辑仅为保证每位展示口径统一为 4 个候选。
        """
        DISPLAY_POS_KEYS = _main_mod.DISPLAY_POS_KEYS
        out = {}
        for pk in DISPLAY_POS_KEYS:
            seen = set()
            digits = []
            # 1) 主推数字置顶（consensus 优先 pipeline/quick/trend 首个有效值）
            for src in ('pipeline', 'quick', 'trend'):
                v = picks.get(pk, {}).get(src)
                if isinstance(v, int) and v not in seen:
                    digits.append(v)
                    seen.add(v)
                    break
            # 2) 多源 Top 候选去重追加（每源最多 4 个）
            for src in ('pipeline', 'quick', 'trend'):
                for v in (top5.get(pk, {}).get(src, []) or []):
                    if isinstance(v, int) and v not in seen:
                        digits.append(v)
                        seen.add(v)
            # 3) 不足 4 个时按 0-9 顺序补足（保证每位 4 候选的展示口径）
            for d in range(10):
                if len(digits) >= 4:
                    break
                if d not in seen:
                    digits.append(d)
                    seen.add(d)
            out[pk] = digits[:4]
        return out

    def _build_explanation(self, total_sources, conf, high_conf, picks, pos_keys):
        """主推荐简短解释（诚实口径：一致性=多源重合度，非命中概率）"""
        if total_sources < 2:
            return ("当前为单一分析源结果（其余分析进行中），置信度需多源聚合后给出。"
                    "排列5为公平随机摇号，本预测仅供数据探索，不保证命中。")
        agree_pos = sum(1 for pk in pos_keys
                        if len(picks[pk]) >= 2 and len(set(picks[pk].values())) == 1)
        if high_conf:
            head = "两个主预测（四步流水线 / 快速预测）完全一致，已合并为高置信度推荐。"
        else:
            head = "两个主预测存在差异，已按预设规则（多源多数投票、平票优先四步流水线）筛选主推荐。"
        pos_note = f"其中 {agree_pos}/{len(pos_keys)} 个位置多源完全一致。"
        honest = ("综合一致性置信度仅反映多源信号重合程度；排列5公平摇号，"
                  "历史无法稳定超越随机基线（Top-1≈10%），不保证命中。")
        return " ".join([head, pos_note, honest])

    def _build_aggregated_clipboard(self, target_issue, main_combo, conf, high_conf, combos, picks, top5=None):
        """生成可复制的结构化摘要（主推荐 + 备选），确保复制的是算法预测结果

        算法预测流程：
        1. 多源数据采集：四步流水线、快速预测、走势引擎
        2. 逐位聚合：多数投票 + 平票优先四步流水线
        3. 置信度计算：多源信号重合度百分比
        4. 最终输出：综合推荐组合（每位 Top-4 候选）
        """
        DISPLAY_POS_KEYS = _main_mod.DISPLAY_POS_KEYS
        DISPLAY_POS_NAMES = _main_mod.DISPLAY_POS_NAMES
        compress_combo = _main_mod.compress_combo
        # 展示层使用完整5位（v3.64）
        pos_keys = DISPLAY_POS_KEYS
        pos_names = DISPLAY_POS_NAMES
        top5 = top5 or {}
        
        # 获取各位置所有候选号码（从top5中提取所有来源的Top-4号码）
        all_candidates = {}
        for pk, pn in zip(pos_keys, pos_names):
            # 收集所有来源的Top-4号码
            all_digits = []
            seen = set()
            
            # 优先获取主推号码
            vals = picks.get(pk, {})
            for src in ['pipeline', 'quick', 'trend']:
                if src in vals:
                    val = vals[src]
                    if val not in seen:
                        all_digits.append(val)
                        seen.add(val)
            
            # 从top5中获取更多候选号码
            src_top5 = top5.get(pk, {})
            for src in ['pipeline', 'quick', 'trend']:
                if src in src_top5:
                    for val in src_top5[src]:
                        if val not in seen:
                            all_digits.append(val)
                            seen.add(val)
            
            # 确保每个位置有4个候选号码
            if len(all_digits) < 4:
                for d in range(10):
                    if d not in seen:
                        all_digits.append(d)
                        seen.add(d)
                        if len(all_digits) >= 4:
                            break

            all_candidates[pk] = all_digits[:4]
        
        lines = [
            f" 期号: {target_issue or '—'}",
            f" 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f" 【最终预测号码】: {compress_combo(main_combo)}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        
        # 各位置所有候选号码（按用户要求格式）
        lines.append(" 各位置候选号码:")
        for pk, pn in zip(pos_keys, pos_names):
            digits = all_candidates.get(pk, [])
            if digits:
                lines.append(f"   {pn}: {' '.join(str(d) for d in digits)}")
        lines.append("")
        
        # 置信度信息
        confidence_label = " 高置信度" if high_conf else " 常规置信度"
        lines.append(f" {confidence_label}")
        if conf is not None:
            lines.append(f"   综合一致性置信度: {conf}%")
        lines.append("")
        
        # 算法说明
        lines.append(" 算法说明:")
        lines.append("   • 多源数据: 四步流水线 + 快速预测 + 走势引擎")
        lines.append("   • 聚合策略: 多数投票（平票优先四步流水线）")
        lines.append("   • 置信度: 多源信号重合度百分比")
        lines.append("")
        
        # 逐位来源详情
        lines.append(" 逐位来源详情:")
        for pk, pn in zip(pos_keys, pos_names):
            vals = picks.get(pk, {})
            if vals:
                digits = ", ".join(f"{k}={v}" for k, v in sorted(vals.items()))
                main_digit = next(iter(vals.values()))
                lines.append(f"   {pn}: {main_digit}  [{digits}]")
        lines.append("")
        
        # 各分析源原始结果
        # 各分析源组合同步使用完整5位展示（v3.64）
        _combos_disp = {k: compress_combo(v) for k, v in (combos or {}).items()}
        _main_disp = compress_combo(main_combo)
        lines.append(" 各分析源结果（参考）:")
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            if _combos_disp.get(key):
                is_main = (_combos_disp[key] == _main_disp)
                marker = "" if is_main else " "
                lines.append(f"   {marker} {name}: {_combos_disp[key]}" + (" ← 选为最终预测" if is_main else ""))

        return "\n".join(lines)

    def _execute_backtest(self, task_mgr, cancel_event=None):
        """
        历史回测：使用P5Predictor在历史数据上执行滚动预测回测

        流程:
        1. 加载全部历史数据（至少100期）
        2. 从第50期开始，每期用前N期数据训练后预测下一期
        3. 统计Top-1/Top-3命中率、综合得分、完全猜中次数
        4. 生成回测报告文件

        输出: 回测统计指标、前10期详情、报告文件路径
        """
        Backtester = _main_mod.Backtester
        P5Database = _main_mod.P5Database
        P5Predictor = _main_mod.P5Predictor
        try:
            task_mgr.log("=" * 70)
            task_mgr.log("  历史回测分析（Walk-Forward 滚动回测）")
            task_mgr.log("=" * 70)

            # 初始化预测器
            task_mgr.progress(5, "初始化回测引擎")
            predictor = P5Predictor()

            # 展示「真实生效」的算法权重（此前这里是硬编码的 v2.1 数值，
            # 与当前冻结权重不符，属于误导性输出，v3.25 改为读取实际配置）
            try:
                weights = predictor.config.get_algorithm_weights() or {}
                cn = {'frequency': '频率加权', 'omission': '遗漏回归', 'bayesian': '贝叶斯',
                      'trend': '趋势动量', 'markov': '马尔可夫',
                      'pattern': '形态延续', 'feature': '特征加权'}
                task_mgr.log("\n 回测实际生效权重（读取自预测器配置）:")
                for k, v in sorted(weights.items(), key=lambda x: -float(x[1] or 0)):
                    task_mgr.log(f"     • {cn.get(k, k)}: {float(v) * 100:.2f}%")
            except Exception as _e:
                task_mgr.log(f" 读取权重配置失败(不影响回测): {_e}")
            task_mgr.log("     • AI 大模型(完整复包装): 回测期间关闭（可复现 / 无调用费用 / 无前视泄漏）")
            task_mgr.log("     • 贝叶斯AI辅助: 自动判断最近 10 期（仅后验解读，不影响命中率指标）")
            task_mgr.log("  " + "-" * 60)

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("\n正在加载历史数据...")
            task_mgr.progress(15, "加载历史数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if len(history_data) < 100:
                task_mgr.log(f" 历史数据不足: 需要至少100期，实际{len(history_data)}期")
                task_mgr.progress(0, "数据不足")
                return

            task_mgr.log(f" 历史数据加载完成: 共 {len(history_data)} 期")
            task_mgr.progress(25, "数据加载完成")

            # 初始化回测引擎
            task_mgr.log("正在初始化回测引擎...")
            task_mgr.progress(30, "初始化引擎")

            backtest_engine = Backtester(predictor, db)

            # 配置回测参数
            # v3.25 修复：此前固定 start_index=50 且从最老一期向后评估，
            # 在当前 1000+ 期库存下等于只回测 2023 年的老数据（日志里出现
            # 「目标期号 2023285」即源于此），且训练集只有 50 期。
            # 现改为评估「最近 test_count 期」，每期用其之前的全部历史训练，
            # 与冻结基线配置的基线口径一致。
            start_index = 50           # 最小训练期数（冷启动下限）
            test_count = 50            # 评估最近 50 期
            eval_start = max(start_index, len(history_data) - test_count)
            test_count = min(test_count, len(history_data) - eval_start)
            eval_from = history_data[eval_start]['issue']
            eval_to = history_data[eval_start + test_count - 1]['issue']

            task_mgr.log(f"回测配置:")
            task_mgr.log(f"  评估区间: {eval_from} ~ {eval_to}（最近 {test_count} 期）")
            task_mgr.log(f"  训练方式: 逐期滚动，每期仅使用该期之前的历史（起始训练量 {eval_start} 期）")
            task_mgr.log(f"  数据总量: {len(history_data)} 期（{history_data[0]['issue']} ~ {history_data[-1]['issue']}）")
            _aux_cap = self._BACKTEST_AI_AUX_CAP
            _cap_label = "全部" if _aux_cap >= test_count else f"最近 {_aux_cap} 期"
            task_mgr.log(f"  贝叶斯AI辅助: {_cap_label}（自动判断；完整AI复包装仍关闭）")

            # 执行回测
            task_mgr.log("\n" + "" * 50)
            task_mgr.log("正在执行回测计算...")
            task_mgr.log("" * 50)
            task_mgr.progress(40, "执行回测中")

            # 传入 log_callback，使回测逐期进度（含断点恢复、逐期AI启用）实时呈现在输出面板；
            # 移除原先「每10期 sleep 0.1s 伪造进度」的误导块——真实进度由 backtester 逐期 _blog 驱动。
            backtest_result = backtest_engine.run_backtest(
                start_index, test_count, eval_mode='recent', enable_ai=False,
                max_bayes_aux_calls=self._BACKTEST_AI_AUX_CAP,
                log_callback=task_mgr.log
            )

            if backtest_result.get('status') != 'success':
                task_mgr.progress(0, "回测失败")
                task_mgr.log(f"\n 回测失败: {backtest_result.get('message', '未知错误')}")
                return

            task_mgr.progress(85, "生成报告中")
            task_mgr.log("\n 回测计算完成！")

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

            # 随机基线对照（诚实口径：排列5 为公平摇号，逐位 Top-1 期望 10%、Top-3 期望 30%）
            task_mgr.append_info(
                f"随机基线对照: Top-1 期望 10.00% (实测 {top1_rate:.2f}%, 偏差 {top1_rate - 10:+.2f}pp) | "
                f"Top-3 期望 30.00% (实测 {top3_rate:.2f}%, 偏差 {top3_rate - 30:+.2f}pp)")

            # 断点续跑 + 贝叶斯AI辅助 概览
            _resumed = backtest_result.get('resumed_count')
            _aux_cnt = backtest_result.get('ai_aux_enabled_count')
            _aux_cap = backtest_result.get('ai_aux_cap')
            if _resumed:
                task_mgr.append_info(f" 断点续跑: 已恢复 {_resumed} 期（跳过预测/AI调用，直接从缓存续算）")
            if _aux_cnt is not None:
                if _aux_cnt > 0:
                    task_mgr.append_info(
                    f" 贝叶斯AI辅助: 最近 {_aux_cnt} 期已触发辅助洞察"
                        f"（其余期按设计关闭以控费/避免卡顿）")
                else:
                    task_mgr.append_info(f" 贝叶斯AI辅助: 本次未触发（cap={_aux_cap or 0}）")

            # 详细结果（最近10期）
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_section_header("最近10期回测详情")
            task_mgr.log("=" * 70)

            results = backtest_result.get('results', [])
            for i, result in enumerate(list(reversed(results))[:10], 1):
                # 注：_calculate_overall_stats 返回的字段名为 target_issue /
                # top1_hit_count / top3_hit_count（非 issue / top1_hits / top3_hits）
                issue = result.get('target_issue', '')
                top1_hits = result.get('top1_hit_count', 0)
                top3_hits = result.get('top3_hit_count', 0)
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

            # 更新统计面板（快捷统计卡已移除）
            # stats_text = (
            #     f"回测期数: {backtest_result.get('total_tested', 0)}\n"
            #     f"Top-1命中率: {top1_rate:.2f}%\n"
            #     f"Top-3命中率: {top3_rate:.2f}%\n"
            #     f"综合得分: {stats.get('avg_overall_score', 0):.2f}"
            # )
            # self.stats_content.config(text=stats_text, fg=COLORS['accent_ai'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_success("历史回测流程全部完成")
            task_mgr.log("=" * 70)

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 历史回测过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_feature_analysis(self, task_mgr, cancel_event=None):
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
        P5Database = _main_mod.P5Database
        P5Features = _main_mod.P5Features
        try:
            task_mgr.log("正在执行特征分析...")
            task_mgr.progress(10, "初始化特征工程")

            # 初始化特征工程
            fe = P5Features()

            # 初始化数据库
            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                task_mgr.progress(0, "数据库连接失败")
                return

            # 获取历史数据
            task_mgr.log("正在加载历史数据...")
            task_mgr.progress(20, "加载数据")

            history_data = db.get_history_data(limit=None, order_by='issue ASC')
            db.disconnect()

            if not history_data:
                task_mgr.log(" 数据库中没有历史数据")
                task_mgr.progress(0, "无数据")
                return

            task_mgr.log(f" 历史数据加载完成: 共{len(history_data)}期")

            # 提取所有特征
            task_mgr.log("\n正在提取特征...")
            task_mgr.progress(40, "提取频率特征")

            features = fe.extract_all_features(history_data)

            task_mgr.progress(70, "特征提取完成")

            # 输出特征分析结果
            task_mgr.log("\n" + "=" * 70)
            task_mgr.log(" 特征分析完成！")
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

            # 保存特征分析结果到数据库
            try:
                feat_db = P5Database()
                if feat_db.connect():
                    feat_db.save_artifact(
                        artifact_type='feature_analysis',
                        data=features,
                        meta={'data_count': len(history_data) if 'history_data' in dir() else None}
                    )
                    feat_db.disconnect()
                    task_mgr.log("\n 特征分析结果已保存到数据库")
                else:
                    task_mgr.log("\n 特征分析结果保存失败: 数据库连接失败")
            except Exception as db_e:
                task_mgr.log(f"\n 特征分析结果保存失败: {db_e}")

            # 更新统计面板（快捷统计卡已移除）
            # stats_text = (
            #     f"数据量: {len(history_data)} 条\n"
            #     f"连号率: {consecutive_features.get('consecutive_rate', 0):.1%}\n"
            #     f"重号率: {repeat_features.get('repeat_rate', 0):.1%}\n"
            #     f"平均和值: {sum_span_features.get('avg_sum', 0):.1f}"
            # )
            # self.stats_content.config(text=stats_text, fg=COLORS['warning'])

            task_mgr.progress(100, "任务完成")
            task_mgr.log("\n 特征分析流程全部完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 特征分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_comprehensive_analysis_and_verify(self, task_mgr, cancel_event=None):
        """
        综合验证与分析（一键执行「分析工具」全部子功能）

        将原先分散在「分析工具」卡片下的 5 个独立功能整合为单点一键执行：
          1. 预测验证    — 闭合待验证预测，更新命中率
          2. 命中率报告  — 各位置命中率 + 趋势
          3. 性能报告    — AI 预测性能评估
          4. 历史回测    — 滚动回测 Top-N 命中率
          5. 特征分析    — 历史数据统计特征
          6. 综合报告    — 汇总产物

        一致性保证：直接复用各子功能的既有实现（_execute_*），
        因此数据来源、计算口径与结果展示与单独点击完全一致，
        只是免去逐一点击、串行编排为一次任务。
        """
        try:
            task_mgr.log("=" * 70)
            task_mgr.log(" 综合验证与分析（一键执行「分析工具」全部子功能）")
            task_mgr.log("=" * 70)
            task_mgr.log("\n  执行流程（串行，与独立点击完全一致）:")
            task_mgr.log("    ① 预测验证 → ② 命中率报告 → ③ 性能报告")
            task_mgr.log("    ④ 历史回测 → ⑤ 特征分析 → ⑥ 综合报告")
            task_mgr.progress(2, "开始综合验证与分析...")

            steps = [
                ("① 预测验证", self._execute_verify_predictions),
                ("② 命中率报告", self._execute_hit_rate_report),
                ("③ 性能报告", self._execute_performance_report),
                ("④ 历史回测", self._execute_backtest),
                ("⑤ 特征分析", self._execute_feature_analysis),
            ]

            for idx, (label, fn) in enumerate(steps, start=1):
                task_mgr.log("\n" + "━" * 60)
                task_mgr.log(f"【步骤 {idx}/5】{label} ...")
                task_mgr.progress(int(2 + 88 * idx / 5), f"{label} 执行中")
                try:
                    fn(task_mgr)
                except Exception as _e:
                    task_mgr.log(f" {label} 异常（不影响后续步骤）: {_e}")

            # ==========================================
            # 步骤6：生成综合报告
            # ==========================================
            task_mgr.log("\n" + "━" * 60)
            task_mgr.log("【步骤 6/6】生成综合分析报告...")
            task_mgr.progress(95, "生成报告中")
            try:
                report_data = {
                    'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'module': '综合验证与分析',
                    'steps_completed': [
                        '预测验证', '命中率统计', '性能报告',
                        '历史回测', '特征分析', '综合报告'
                    ],
                    'note': '本模块整合原「分析工具」全部子功能为一键执行；'
                            '各步骤计算口径与单独点击完全一致。'
                }
                report_dir = REPORTS_DIR
                os.makedirs(report_dir, exist_ok=True)
                report_file = os.path.join(
                    report_dir,
                    f'comprehensive_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                )
                with open(report_file, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, ensure_ascii=False, indent=2)
                task_mgr.log(f" 综合报告已保存: {report_file}")
            except Exception as _e:
                task_mgr.log(f" 报告生成异常: {_e}")

            task_mgr.progress(100, "综合验证与分析完成")
            task_mgr.log("\n" + "=" * 70)
            task_mgr.append_success(" 综合验证与分析全部完成（分析工具已整合为一键）")
            task_mgr.log("=" * 70)
            task_mgr.log("\n 提示：排列5为公平摇号，历史命中率不代表未来，请理性参考。")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 综合验证与分析过程发生异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")
            task_mgr.progress(0, "异常终止")

    def _execute_view_reports(self, task_mgr, cancel_event=None):
        """
        查看独立报告 (v3.3 改为从数据库读取, 不再依赖本地 JSON 文件)

        显示 p5_ai_report 表中所有已生成的独立报告, 包括：
        1. 专家文章预测报告 (report_type='expert_article')
        2. 走势图数据预测报告 (report_type='trend_chart')
        """
        P5Database = _main_mod.P5Database
        try:
            import json as _json

            task_mgr.log("=" * 70)
            task_mgr.log(" 独立报告浏览 (数据库)")
            task_mgr.log("=" * 70)

            db = P5Database()
            if not db.connect():
                task_mgr.log("数据库连接失败")
                return

            def _fetch(report_type):
                """查询指定类型的最近 20 条 AI 报告。

                参数:
                    report_type: 报告类型标识，对应 p5_ai_report.report_type

                返回:
                    报告行列表，查询异常时返回空列表并写入任务日志
                """
                try:
                    db.cursor.execute(
                        "SELECT report_uuid, latest_issue, next_issue, created_at, report_content "
                        "FROM p5_ai_report WHERE report_type=%s ORDER BY created_at DESC LIMIT 20",
                        (report_type,)
                    )
                    return db.cursor.fetchall() or []
                except Exception as e:
                    task_mgr.log(f" 查询{report_type}报告失败: {e}")
                    return []

            def _safe_parse(content):
                """容错解析 JSON 文本。

                参数:
                    content: 待解析内容，可能是 JSON 字符串、已解析的 dict 或 None

                返回:
                    解析后的字典；解析失败时返回空字典而非抛出异常
                """
                try:
                    return _json.loads(content) if isinstance(content, str) else (content or {})
                except Exception:
                    return {}

            # 专家文章预测报告
            task_mgr.append_section_header(" 专家文章预测报告")
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
            task_mgr.append_section_header(" 走势图数据预测报告")
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
            task_mgr.log(" 报告浏览完成 (数据来源: 数据库 p5_ai_report)")
            task_mgr.log(f"{'=' * 70}")

        except Exception as e:
            error_detail = traceback.format_exc()
            task_mgr.log(f"\n 查看报告异常: {str(e)}")
            task_mgr.log(f"\n错误详情:\n{error_detail}")

    # ============================================================
    # 「开始分析」融合阶段 ④：命中率优化
    # 原卡片的三个按钮已在此融合为「开始分析」的一个自动阶段：
    # · 选号策略对照 —— 复用阶段③已算出的融合概率，零额外 predict 开销
    # （单次 predict 在启用选号策略时可达数十秒）
    # · 概率校准 —— 回测断点里有样本时自动重拟合并落盘，否则读既有参数
    # · 三闸门调参 —— 只读取最近一次调参报告的结论，**不重跑**：单次调参需
    # 1-2 分钟，而历史结论稳定为 keep_baseline，不值得让
    # 每次「开始分析」都为一个可预期的结论多等两分钟
    # 全部以文本输出到同一结果面板，不再弹出独立可视化窗口（与「结果同面板呈现」一致）。
    # 诚实边界：本阶段只做「解读与状态展示」，不修改已产出的融合概率与推荐号码。
    # ============================================================

    def _run_hitrate_optimization_stage(self, task_mgr):
        """融合阶段④：命中率优化（选号策略 / 概率校准 / 调参结论）。

        三个子项彼此独立，任一异常都不得中断其余子项与整体分析流程。
        """
        logger = _main_mod.logger
        task_mgr.append_section_header(" 命中率优化（选号策略 · 概率校准 · 调参结论）")
        task_mgr.progress(72, "命中率优化")
        for label, fn in (("选号策略对照", self._report_selection_strategy),
                          ("概率校准", self._report_probability_calibration),
                          ("三闸门调参结论", self._report_param_tuning_verdict)):
            try:
                fn(task_mgr)
            except Exception as e:
                task_mgr.log(f" {label} 异常（不影响已产出的预测结果）: {e}")
                logger.warning(f"命中率优化-{label} 异常: {e}", exc_info=True)

    def _report_selection_strategy(self, task_mgr):
        """选号策略对照：同一概率分布下横向对比各构造方式的位覆盖期望。"""
        fused = getattr(self, '_last_fused_probabilities', None)
        if not fused:
            task_mgr.append_info("  · 选号策略对照: 跳过（本轮无可复用的融合概率分布）")
            return

        from modules.selection_strategy import (compare_strategies,
                                                format_strategy_comparison,
                                                STRATEGY_LABELS)
        from modules.predictor import P5PredictorConfig

        cfg = P5PredictorConfig()
        k = cfg.get_global_param('combination_count', 10)
        comparison = compare_strategies(fused, k=k)
        task_mgr.log(format_strategy_comparison(comparison))

        active = cfg.get_global_param('selection_strategy', 'weighted_coverage')
        meta = getattr(self, '_last_predict_meta', None) or {}
        applied = meta.get('selection_strategy_applied')
        task_mgr.append_info(
            f"  当前生效策略: {STRATEGY_LABELS.get(active, active)}"
            f"（本轮预测实际应用: {'是' if applied else '否'}）")
        if comparison.get('note'):
            task_mgr.append_info(f"  {comparison['note']}")

    def _report_probability_calibration(self, task_mgr):
        """概率校准：回测断点有样本则自动重拟合并落盘，否则读取既有参数。"""
        from modules.calibration import (ProbabilityCalibrator,
                                         load_probs_from_resume_files)
        from modules.predictor import P5PredictorConfig

        cal = ProbabilityCalibrator.load()
        source = '已落盘参数'
        try:
            probs, n = load_probs_from_resume_files()
        except Exception:
            probs, n = [], 0

        if probs:
            # 回测断点里有样本 → 自动重拟合，使校准参数始终与最新回测同步。
            # 这一步很便宜（读 JSON + 黄金分割搜索），可以每轮分析都做。
            fresh = ProbabilityCalibrator()
            fresh.fit_from_backtest_probs(probs)
            try:
                fresh.save()
            except Exception as _se:
                task_mgr.log(f" 校准参数落盘失败（不影响本次展示）: {_se}")
            cal = fresh
            source = f'本轮自动重拟合（样本 {n} 个位次）'

        meta = getattr(cal, 'metadata', None) or {}
        if not meta:
            task_mgr.append_info(
                "  · 概率校准: 暂无样本也无既有参数"
                "（先在「综合验证与分析」里跑一次历史回测即可积累）")
            return

        enabled = bool(P5PredictorConfig().get_global_param(
            'enable_probability_calibration', False))
        sig = cal.signal_strength
        task_mgr.log(f"\n  【概率校准】来源: {source}")
        task_mgr.log(f"    温度 T = {cal.temperature:.4f}    收缩系数 ε = {cal.epsilon:.6f}")
        task_mgr.log(f"    模型真实信号强度 = {sig * 100:.4f}%"
                     f"    （诚实阈值 5%，低于此值即判定为无真实预测信号）")
        task_mgr.log(f"    本轮预测是否应用校准: {'是' if enabled else '否'}")
        if sig < 0.05:
            task_mgr.append_warning(
            " 信号强度 < 5%：模型基本无真实预测信号，校准后分布更接近均匀"
                "——这与排列5公平摇号的理论预期一致。")
        else:
            task_mgr.append_success(
            f" 检测到可量化信号 {sig * 100:.2f}%，可考虑启用概率校准。")
        if meta.get('interpretation'):
            task_mgr.append_info(f"    结论: {meta['interpretation']}")

    def _report_param_tuning_verdict(self, task_mgr):
        """三闸门调参：读取最近一次调参报告的结论（归档结论，本轮不重跑）。"""
        __file__ = _main_mod.__file__
        import glob as _glob
        try:
            from paths import REPORTS_BACKTEST_DIR as _dir
        except Exception:
            _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'reports', 'backtest')

        files = sorted(_glob.glob(os.path.join(_dir, 'param_tuning_*.json')))
        if not files:
            task_mgr.append_info(
                "  · 三闸门调参: 暂无历史调参报告"
                "（该能力已归档为离线分析，当前运行使用冻结基线参数）")
            return

        latest = files[-1]
        try:
            with open(latest, 'r', encoding='utf-8') as fh:
                rep = json.load(fh)
        except Exception as e:
            task_mgr.append_info(f"  · 三闸门调参: 报告读取失败（{e}）")
            return

        stamp = os.path.basename(latest).replace('param_tuning_', '').replace('.json', '')
        task_mgr.log(f"\n  【三闸门调参结论】历史报告 {stamp}（归档结论，本轮不重跑）")

        status = rep.get('status')
        if status and status != 'ok':
            task_mgr.append_info(f"    该次调参状态: {status} — {rep.get('message', '')}")
            return

        gates = rep.get('gates') or {}
        verdict = rep.get('verdict') or {}
        cands = rep.get('candidates') or []
        task_mgr.log(f"    候选参数组: {len(cands)}    "
                     f"通过 FDR 闸门: {gates.get('n_passing_fdr', 0)}    "
                     f"通过一致性闸门: {gates.get('n_passing_consistency', 0)}")
        task_mgr.log(f"    最终结论: {verdict.get('action', '—')}")
        if verdict.get('action') == 'keep_baseline':
            task_mgr.append_info(
                "    → 无候选参数通过三闸门显著性检验，维持冻结基线权重"
                "（符合排列5公平摇号的理论预期）。")
        if verdict.get('reason'):
            task_mgr.append_info(f"    理由: {verdict['reason']}")

    # ============================================================
    # 「开始分析」融合阶段 ⑤：在线学习闭环
    # 原卡片三个按钮的去向：
    # · 查看学习报告 —— 融合为本阶段的自动输出
    # · 手动验证期号 —— 由流水线内的**自动验证闭环**取代。v3.42 起自动验证
    # 路径也会触发 learn_from_verification 归因学习（此前
    # 只有手动对话框会触发，导致自动跑批时在线学习空转），
    # 因此手动输入期号已无必要
    # · 重置模型权重 —— 破坏性操作（清 Redis 累积权重），不适合出现在自动化
    # 流程中，随卡片一并移除
    # ============================================================

    def _run_online_learning_stage(self, task_mgr):
        """融合阶段⑤：在线学习闭环状态（验证统计 / 权重调度 / 归因覆盖率）。"""
        P5Database = _main_mod.P5Database
        logger = _main_mod.logger
        task_mgr.append_section_header(" 在线学习闭环（验证统计 · 权重调度 · 归因覆盖率）")
        task_mgr.progress(82, "在线学习闭环")
        db = None
        try:
            db = P5Database()
            if not db.connect():
                task_mgr.append_warning(" 数据库连接失败，跳过在线学习状态展示")
                return
            for label, fn in (("验证统计", self._report_verification_stats),
                              ("归因覆盖率", self._report_attribution_coverage)):
                try:
                    fn(task_mgr, db)
                except Exception as e:
                    task_mgr.log(f" {label} 异常: {e}")
            try:
                self._report_weight_schedule(task_mgr)
            except Exception as e:
                task_mgr.log(f" 权重调度状态异常: {e}")
        except Exception as e:
            task_mgr.log(f" 在线学习阶段异常（不影响已产出的预测结果）: {e}")
            logger.warning(f"在线学习阶段异常: {e}", exc_info=True)
        finally:
            if db is not None:
                try:
                    db.disconnect()
                except Exception:
                    pass

    def _report_verification_stats(self, task_mgr, db):
        """验证统计：最近30天命中情况 + 各位置命中率 + 性能趋势。"""
        stats = db.get_verification_stats()
        if not stats or stats.get('total', 0) == 0:
            task_mgr.append_info("  · 验证统计: 暂无已验证记录"
                                 "（预测注册后需等待开奖，下次分析会自动闭合）")
            return

        total = stats['total']
        # 口径与走势引擎面板保持一致。
        # 注意 get_verification_stats() 返回的是 *_accuracy 百分比，
        # 不含 *_hits 计数——早期误读 *_hits 会导致各位置恒显示 0%。
        task_mgr.log(f"\n  【验证统计】已验证 {total} 期（按期号去重）")
        task_mgr.log(f"    Top-1 精确口径: 完全命中 "
                     f"{stats.get('strict_full_matches', 0)} 期    "
                     f"平均命中 {stats.get('strict_avg_match', 0)}/5    "
                     f"平均准确率 {stats.get('strict_avg_accuracy', 0)}%")
        task_mgr.log(f"    宽松口径（集合包含+±1 容错·非预测力）: 完全命中 "
                     f"{stats.get('total_matched', 0)} 期    "
                     f"平均命中 {stats.get('avg_match', 0)}/5    "
                     f"平均准确率 {stats.get('avg_accuracy', 0)}%")

        task_mgr.log(f"\n  【各位置命中率】Top-1 精确口径 ┃ 宽松口径")
        pos_names = ['万位', '千位', '百位', '十位', '个位']
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        for name, key in zip(pos_names, pos_keys):
            strict = float(stats.get(f'strict_{key}_accuracy', 0) or 0)
            tol = float(stats.get(f'{key}_accuracy', 0) or 0)
            bar_len = max(0, min(20, int(strict / 5)))
            bar = '█' * bar_len + '░' * (20 - bar_len)
            task_mgr.log(f"    {name}: {strict:5.1f}% [{bar}] ┃ {tol:5.1f}%")
        task_mgr.append_info(
            "    随机基线: 核心指标为 Top-1 精确口径 = 10%（k=1/10）；"
            "宽松口径（每位多个候选）的基线仅对该宽松集合成立，"
            "不能换算为 Top-1 命中率提升")

        try:
            perf_history = db.get_performance_history(limit=5)
        except Exception:
            perf_history = []
        if perf_history:
            task_mgr.log(f"\n  【性能趋势】最近 5 次统计")
            for perf in perf_history[:5]:
                task_mgr.log(f"    {perf.get('stat_date', 'N/A')}: "
                             f"总预测 {perf.get('total_predictions', 0)}, "
                             f"平均命中 {perf.get('avg_match_count', 0)}/5")

    def _report_weight_schedule(self, task_mgr):
        """自适应权重的学习率调度与收敛策略（诚实展示实际生效配置）。"""
        from modules.predictor import AdaptiveWeightManager, P5PredictorConfig

        self._log_real_algorithm_weights(task_mgr, "模型权重配置（实际生效）")

        _g = P5PredictorConfig.DEFAULT_CONFIG['global']
        _wm = AdaptiveWeightManager(
            ewma_alpha=_g.get('ewma_alpha', 0.3),
            learning_rate_decay=_g.get('learning_rate_decay', 0.995),
            min_learning_rate=_g.get('min_learning_rate', 0.05),
            warmup_iterations=_g.get('warmup_iterations', 5),
            convergence_tol=_g.get('convergence_tol', 0.01),
            enable_guardrails=_g.get('enable_adaptive_guardrails', True),
        )
        _st = _wm.get_learning_state()
        task_mgr.log(f"\n  【学习率调度与收敛策略】")
        task_mgr.log(f"    基础学习率 α:    {_st['base_learning_rate']}")
        task_mgr.log(f"    学习率衰减:      ×{_st['learning_rate_decay']} / 迭代"
                     f"（下限 {_st['min_learning_rate']}）")
        task_mgr.log(f"    预热迭代:        {_st['warmup_iterations']} 次"
                     f"（期间不衰减，稳定冷启动）")
        task_mgr.log(f"    收敛判据:        权重位移 < {_st['convergence_tol']} 即判已收敛")
        task_mgr.log(f"    护栏(收缩+钳制): "
                     f"{'开启' if _g.get('enable_adaptive_guardrails') else '关闭'}")
        task_mgr.log(" 排列5为公平摇号，学习目标是抗噪声稳定，而非突破随机天花板。")

    def _report_attribution_coverage(self, task_mgr, db):
        """验证→学习闭环的归因覆盖率（诚实展示学习是否真在按算法归因）。

        v3.44 起仅统计该版本部署日之后创建的预测记录，避免历史 NULL 归因把覆盖率
        拉到 0% 造成功能失效的误判。全量(含历史)口径仅作诚实对照展示。
        """
        from modules.online_learner import OnlineLearner

        _cov = OnlineLearner(db, None).estimate_attribution_coverage(
            db, days=30, limit=20)
        task_mgr.log(f"\n  【验证→学习闭环覆盖率】（仅统计  后记录）")
        if _cov.get('coverage') is None:
            task_mgr.log("     后暂无已验证预测，覆盖率将在新预测开奖验证后陆续统计。")
            if _cov.get('excluded_pre_fix'):
                task_mgr.log(
                    f"    （另有 {_cov['excluded_pre_fix']} 期 v3.44 前的历史预测"
                    f"因归因未落库已排除，不计入覆盖率）")
            return
        task_mgr.log(f"    采样已验证期数: {_cov['sampled']}")
        task_mgr.log(f"    含 per-algo 归因:        {_cov['with_attribution']} 期")
        task_mgr.log(f"    归因覆盖率:             {_cov['coverage'] * 100:.1f}%")
        if _cov.get('raw_sampled'):
            task_mgr.log(
                f"    全量口径(含历史,对照):   {_cov['raw_coverage'] * 100:.1f}% "
                f"（{_cov['raw_with_attribution']}/{_cov['raw_sampled']}，"
                f"已排除 {_cov['excluded_pre_fix']} 期 v3.44 前记录）")
        # 仅当 v3.44 后新建预测本身也缺归因时才告警（这才是真问题）
        if _cov['coverage'] < 0.5 and _cov['sampled'] >= 1:
            task_mgr.append_warning(
            " v3.44 后新建预测的归因覆盖率偏低，请检查四步流水线是否"
                "正确写入 p5_ai_report.per_algo_predictions。")

    # ============================================================
    # 「开始分析」融合阶段 ⑥：AI 辅助分析与预测解读
    # 两部分：
    # 1) 贝叶斯后验 · AI 辅助解读 —— 复用 _algo_bayesian_inference 已生成的
    # 辅助洞察，不额外发起 API 调用
    # 2) 最终推荐号码的 AI 点评 —— 一次轻量 API 调用（紧凑 prompt / 低 token）
    # AI 不可用（未配置密钥 / 网络失败 / 返回空）时全部诚实降级，绝不编造。
    # 诚实边界：AI 只做「解读」，不修改融合概率与推荐号码。
    # ============================================================

    def _run_ai_assisted_stage(self, task_mgr):
        """融合阶段⑥：AI 辅助分析与预测解读。"""
        P5Database = _main_mod.P5Database
        logger = _main_mod.logger
        task_mgr.append_section_header(" AI 辅助分析与预测解读")
        task_mgr.progress(92, "AI 辅助分析")
        db = None
        try:
            db = P5Database()
            if not db.connect():
                task_mgr.append_warning(" 数据库连接失败，跳过 AI 辅助分析")
                return
            # 1) 贝叶斯后验 + AI 辅助（AI 不可用时内部诚实降级为纯统计后验）
            self._render_bayesian_ai_section(task_mgr, db)
        except Exception as e:
            task_mgr.log(f" 贝叶斯 AI 辅助异常: {e}")
        finally:
            if db is not None:
                try:
                    db.disconnect()
                except Exception:
                    pass

        # 2) 对最终推荐号码做一次轻量 AI 点评
        try:
            self._report_ai_prediction_commentary(task_mgr)
        except Exception as e:
            task_mgr.log(f" AI 预测解读异常（不影响已产出的预测结果）: {e}")
            logger.warning(f"AI 预测解读异常: {e}", exc_info=True)

    def _report_ai_prediction_commentary(self, task_mgr):
        """让 AI 模型对本轮最终推荐号码做一次简短解读（单次轻量调用）。"""
        from modules.ai_analyzer import AIAnalyzer

        analyzer = AIAnalyzer()
        if not analyzer.ai_available:
            task_mgr.append_warning(
            " AI 预测解读: 未启用（config.py 中未配置 AGNES_API_CONFIG.api_key）")
            task_mgr.append_info(
                "     配置密钥后，本阶段会自动引用 AI 模型对推荐号码作辅助解读。")
            return

        # 汇总本轮各来源的最终推荐（复用仪表盘聚合逻辑，不重新计算）
        pf = getattr(self, '_last_pipeline_final', None)
        tr = getattr(self, '_last_trend_result', None)
        qf = getattr(self, '_last_quick_final', None)
        try:
            picks, top5, combos, target_issue = self._extract_source_data(pf, tr, qf)
        except Exception:
            picks, top5, combos, target_issue = {}, {}, {}, ''

        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = ['万位', '千位', '百位', '十位', '个位']
        lines = []
        for pk, pn in zip(pos_keys, pos_names):
            cand = top5.get(pk) or {}
            merged = []
            for src_list in cand.values():
                for v in (src_list or []):
                    if v not in merged:
                        merged.append(v)
            if merged:
                lines.append(f"{pn}: {' '.join(str(x) for x in merged[:3])}")
        if not lines:
            task_mgr.append_info(" AI 预测解读: 跳过（本轮无可解读的候选号码）")
            return

        meta = getattr(self, '_last_predict_meta', None) or {}
        target_issue = target_issue or meta.get('target_issue') or '下一期'

        prompt = (
            f"以下是排列5第 {target_issue} 期的多算法候选号码（每位 Top-4，按位列出）：\n"
            + "\n".join(lines)
            + "\n\n请用中文给出不超过 150 字的简短解读，包含："
              "①各位候选的分布特征（如集中/分散、奇偶与大小倾向）；"
              "②一句风险提示。\n"
              "重要约束：排列5是公平摇号，任何号码组合的中奖概率完全相同，"
              "不得声称能提高中奖率、不得给出必中承诺、不得编造统计规律。"
        )
        messages = [
            {"role": "system",
             "content": "你是严谨的彩票数据分析助手。你必须坚持概率诚实原则："
                        "彩票为独立随机事件，历史数据无法预测未来，"
                        "你的解读仅为数据特征描述，不构成任何投注建议。"},
            {"role": "user", "content": prompt},
        ]

        task_mgr.log("  正在请求 AI 模型解读本轮推荐号码...")
        content = analyzer._call_ai_model(messages, max_tokens=600, temperature=0.5)
        if not content or not str(content).strip():
            task_mgr.append_warning(
            " AI 预测解读: 调用未返回有效内容，已降级为纯统计结果（不影响预测）。")
            return

            task_mgr.append_success(f" AI 预测解读（模型: {analyzer.model_name}）")
        for line in str(content).strip().splitlines():
            if line.strip():
                task_mgr.log(f"    {line.strip()}")
        task_mgr.append_info(
        " 以上为 AI 对统计结果的语言化解读，不改变任何概率计算，"
            "也不提高中奖概率。理性购彩，量力而行。")
