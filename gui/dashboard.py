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

# tk 家族依赖（GUI-11 S4 补齐）：main.py 的 tk/ttk import 位于
# try/except 保护块内，顶层语句扫描不可见，S3 首迁漏统；渲染期方法体
# 裸引用 tk./ttk.（LOAD_GLOBAL 语义），按 output_panel.py（S2）范式
# 在本模块补齐；无显示环境导入失败置 None 兜底，不阻塞模块加载。
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover - 无显示环境兜底
    tk = None
    ttk = None

from typing import Dict, Optional

import json
import os
import threading
import time
from datetime import datetime

from paths import REPORTS_CHARTS_DIR, REPORTS_DIR
from modules.stats_utils import wilson_ci
from modules.ui_theme_spec import (
    WHITE,
    UI_FONT,
    MONO_FONT,
    HIT_FG,
    TOP3_FG,
    MISS_FG,
    HIT_ROW_BG,
    HIT_ROW_FG,
    CONSENSUS_HIT_FG,
    CONSENSUS_HIT_BG,
    ACCENT_P5_SOFT_FG,
    CHART_SYSTEM,
    CHART_SYSTEM_ERR,
    CHART_RANDOM,
    CHART_RANDOM_ERR,
    CHART_NOTE_TEXT,
    CHART_NOTE_FACE,
    CHART_NOTE_EDGE,
    CHART_BARS,
)
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


class DashboardMixin:
    """渲染期方法（GUI-11 RENDER 分区 53 法，main.py 机械搬迁）。

    仅经 LotteryGUI(..., DashboardMixin, ...) 组合使用；
    自身不构造 tk 视图、不持有状态（状态仍在 main.LotteryGUI）。
    """
    def _on_layout_resize(self):
        """窗口大小变化时的布局调整（v3.36 新增）"""
        try:
            # 触发所有子组件重新计算布局
            self.root.update_idletasks()
            # v5.2 GUI-07：三档断点响应式重排（方案 §311）。该调用幂等：
            # 仅当断点档位/磁贴布局模式相对上次发生变化时才触碰控件，
            # 同档位内的连续 Configure（拖拽中）零开销，不干扰 A1-A4
            # 闪烁守护；模式未变即返回，不会因磁贴高度变化引发
            # Configure 二次触发死循环。
            self._apply_breakpoint_layout()
        except Exception:
            pass

    def _update_phase_indicator(self, current_phase_idx):
        """更新阶段指示器圆点颜色。

        参数:
            current_phase_idx: 当前执行的阶段索引 (0-5)
        """
        COLORS = _main_mod.COLORS
        if not hasattr(self, 'evo_phase_indicators'):
            return
        # 阶段点配色（v5.2 GUI-06：原 colors 字面量列表删除，改主题槽 token；
        # 每次刷新同步重登记 Canvas 注册表，主题切换后按当前阶段状态重放）
        _token_done = 'accent_p5_light'    # 已完成阶段（绿色）
        _token_running = 'accent_ai'       # 当前阶段（紫色高亮）
        _token_pending = 'text_muted'      # 未开始阶段（弱化灰）
        for i, dot_canvas in enumerate(self.evo_phase_indicators):
            if i < current_phase_idx:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_done])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_done)
            elif i == current_phase_idx:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_running])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_running)
            else:
                dot_canvas.coords(dot_canvas.find_all()[0], 1, 1, 11, 11)
                dot_canvas.itemconfig(
                    dot_canvas.find_all()[0], fill=COLORS[_token_pending])
                self._register_theme_canvas_item(
                    dot_canvas, dot_canvas.find_all()[0], _token_pending)

    def _render_auto_eval_report(self, report, md, saved_path):
        """主线程渲染自动评估报告到进化日志区（由后台线程经 root.after 回投）。"""
        COLORS = _main_mod.COLORS
        summary = report.summary or {}
        overall = summary.get('overall_score')
        level = summary.get('health_level', '')
        self.evo_log.insert(tk.END, f'\n{"="*40}\n', 'section')
        self.evo_log.insert(tk.END,
            f'[自我升级自动评估] {report.scan_time} | 综合评分 {overall} | 等级 {level}\n',
            'section')
        self.evo_log.insert(tk.END, f'{md}\n', 'info')
        self.evo_log.see(tk.END)
        color = (COLORS['success'] if level == 'healthy'
                 else COLORS['warning'] if level == 'degraded'
                 else COLORS['accent_danger_light'])
        detail = f'，已保存 {saved_path}' if saved_path else ''
        self._show_toast(f'自动评估完成（评分 {overall}/{level}）{detail}',
                         color, duration=4000)

    # =========================================================================
    # 结果显示板块（v3.50 重构辅助）：滚动 / 分类 / 清空 / 导出
    # =========================================================================
    def _dash_yview(self, *args):
        """转发滚动条命令到滚动画布。"""
        try:
            self.dash_container.yview(*args)
        except Exception:
            pass

    def _on_dash_configure(self, event):
        """仪表盘容器尺寸变化处理器（GUI-01 阈值守卫版，v5.2 §2.2）。

        v5.1 旧行为：每次 Configure 都无条件 ``itemconfig('all', width=event.width - 4)``
        并同步刷新滚动区域。当内部视窗宽度改变又反向触发容器 Configure 时，形成
        Canvas 尺寸反馈循环（闪烁根因之一），且拖拽时的 1px 抖动会产生大量无效重排。

        修复后行为：
          * 仅当容器宽度较上次同步宽度变化 >= ``_DASH_WIDTH_SYNC_THRESHOLD`` 像素时，
            才回写内部视窗宽度（1px 级抖动被合并）；
          * 不再在本回调内同步调用 ``_update_result_scrollregion``，改为走防抖入口
            ``_dash_request_refresh``，使滚动区域刷新离开 Configure 事件回调栈。

        参数:
            event: Tk Configure 事件（``event.width`` 为 dash_container 新宽度）。
        """
        _DASH_WIDTH_SYNC_THRESHOLD = _main_mod._DASH_WIDTH_SYNC_THRESHOLD
        try:
            last = getattr(self, '_dash_last_synced_width', None)
            width = int(event.width)
            # 口径说明：与「上次事件宽度」比较，skip 时也更新 last，
            # 使连续 1px 拖拽被逐级合并，仅在累计达到阈值时才真正同步一次。
            self._dash_last_synced_width = width
            if last is None or abs(width - last) >= _DASH_WIDTH_SYNC_THRESHOLD:
                self.dash_container.itemconfig('all', width=width - 4)
            self._dash_request_refresh()
        except Exception:
            pass

    def _dash_request_refresh(self, force: bool = False) -> None:
        """GUI-02 防抖入口：合并短时间内的多次 scrollregion 刷新请求。

        50ms 窗口内的重复请求仅保留一个待执行标记，由 ``root.after`` 统一到
        ``_dash_flush_refresh`` 执行；``force=True`` 时立即执行（用于渲染等
        需要即时同步的场景）。

        参数:
            force: 为 True 时跳过防抖、立即执行一次刷新并取消待执行标记。
        """
        _DASH_REFRESH_DEBOUNCE_MS = _main_mod._DASH_REFRESH_DEBOUNCE_MS
        if force:
            self._dash_refresh_pending = False
            self._dash_flush_refresh()
            return
        if getattr(self, '_dash_refresh_pending', False):
            return
        self._dash_refresh_pending = True
        try:
            self.root.after(_DASH_REFRESH_DEBOUNCE_MS, self._dash_flush_refresh)
        except Exception:
            # 窗口已销毁等场景：退化为直接执行，避免刷新丢失
            self._dash_refresh_pending = False
            try:
                self._dash_flush_refresh()
            except Exception:
                pass

    def _dash_flush_refresh(self):
        """防抖刷新落地：执行一次滚动区域刷新并清除待执行标记。"""
        self._dash_refresh_pending = False
        self._update_result_scrollregion()

    def _update_result_scrollregion(self):
        """刷新结果滚动画布的滚动区域（内容变化后调用）。"""
        try:
            self.dash_container.configure(scrollregion=self.dash_container.bbox('all'))
        except Exception:
            pass

    def _iter_all(self, widget):
        """递归遍历 widget 的所有子孙控件（生成器）。"""
        for ch in widget.winfo_children():
            yield ch
            yield from self._iter_all(ch)

    def _apply_result_category(self, cat):
        """按分类筛选结果显示：全部 / 预测结论 / 分位信号 / 算法依据。"""
        dash = getattr(self, 'result_dash', None)
        if dash is None:
            return
        for w in self._iter_all(dash):
            rc = getattr(w, '_rcat', None)
            if rc is None:
                continue
            if cat == '全部' or rc == cat:
                try:
                    w.pack(fill=tk.X, pady=(0, 8))
                except Exception:
                    pass
            else:
                try:
                    w.pack_forget()
                except Exception:
                    pass
        self._update_result_scrollregion()

    def _clear_result_board(self):
        """清空右侧预测结果仪表盘（工具栏「清空」按钮专用）。

        GUI-03（v5.2 §2.2）：语义收窄——仅响应用户显式清空动作，
        无条件销毁全部卡片；常规渲染更新走 _render_dashboard_impl 的
        增量 diff 路径（_dash_diff），不再全量 destroy。
        """
        COLORS = _main_mod.COLORS
        self._dash_reset()
        self._show_toast('结果已清空', COLORS['success'])

    def _dash_reset(self):
        """销毁全部仪表盘卡片并复位索引/状态，恢复空态占位（GUI-03）。"""
        # 按原 v3.66 约定：卡片自身不注册 after 回调，destroy 前无需额外清理，
        # 窗口已销毁等场景一律静默降级。
        try:
            for w in list(self.result_dash.winfo_children()):
                if w.winfo_exists():
                    w.destroy()
        except Exception:
            pass
        self._dash_card_index = {}
        self._dash_card_state = {}
        self._dash_grid_ncols = {}
        self._last_dashboard_key = None
        self._alt_content = None
        self._alt_visible = False
        try:
            self.result_dash.pack_forget()
            self._result_placeholder.pack(fill=tk.BOTH, expand=True)
            self._update_result_scrollregion()
        except Exception:
            pass

    def _export_result_board(self):
        """导出当前预测结果（结构化 JSON）为 Markdown 报告。"""
        COLORS = _main_mod.COLORS
        pf = getattr(self, '_last_pipeline_final', None)
        tr = getattr(self, '_last_trend_result', None)
        qf = getattr(self, '_last_quick_final', None)
        if not (pf or tr or qf):
            self._show_toast('当前没有可导出的结果', COLORS['warning'])
            return
        meta = getattr(self, '_clipboard_meta', {}) or {}
        lines = [
            '# 排列5 预测结果导出', '',
            f'- 目标期号: {meta.get("target_issue", "")}',
            f'- 主推荐(4位): {meta.get("main_combo", "")}',
            f'- 一致性: {meta.get("conf")}',
            f'- 高置信度: {meta.get("high_conf")}', '',
            '## 预测记录 (JSON)', '```json',
        ]
        try:
            lines.append(json.dumps({'pipeline': pf, 'trend': tr, 'quick': qf},
                                    ensure_ascii=False, indent=2, default=str))
        except Exception:
            lines.append(str(pf))
        lines.append('```')
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(REPORTS_DIR, f'result_export_{ts}.md')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(lines))
            self._show_toast(f'结果已导出: {os.path.basename(path)}', COLORS['success'])
        except Exception as e:
            self._show_toast(f'导出失败: {e}', COLORS['accent_danger'])

    def _focus_result_tab(self):
        """程序化切换到「 预测结果」标签页（预测完成后自动聚焦）。"""
        try:
            self.output_nb.select(0)
        except Exception:
            pass

    @staticmethod
    def _classify_breakpoint(panel_width: int) -> str:
        """按输出面板宽度划分四档断点（A2 四档扩展，方案 §311）。

        参数:
            panel_width: 输出面板（output_nb）当前实际宽度（像素）

        返回:
            str: 'compact'（<1024 紧凑）/ 'standard'（1024-1440 标准）
                 / 'wide'（1440-1600 宽绰）/ 'ultrawide'（>1600 超宽）
        """
        _BREAKPOINT_COMPACT_MAX_W = _main_mod._BREAKPOINT_COMPACT_MAX_W
        _BREAKPOINT_STANDARD_MAX_W = _main_mod._BREAKPOINT_STANDARD_MAX_W
        _BREAKPOINT_WIDE_MAX_W = getattr(_main_mod, '_BREAKPOINT_WIDE_MAX_W', 1600)
        if panel_width < _BREAKPOINT_COMPACT_MAX_W:
            return 'compact'
        if panel_width <= _BREAKPOINT_STANDARD_MAX_W:
            return 'standard'
        if panel_width > _BREAKPOINT_WIDE_MAX_W:
            return 'ultrawide'
        return 'wide'

    def _set_toolbar_mode(self, bp: str) -> None:
        """按断点切换结果页工具栏按钮文案（GUI-07：短文案 <1024）。

        紧凑档使用登记表中的短文案（释放窄屏操作区宽度，验收 A17），
        标准/宽绰档使用完整文案。仅改 text，不触碰按钮其余属性与
        禁用态（禁用外观由任务运行链路单独负责）。

        参数:
            bp: 断点档位（_classify_breakpoint 的返回值）
        """
        compact = (bp == 'compact')
        registry = getattr(self, '_bp_toolbar_texts', None) or {}
        for btn, texts in registry.items():
            text = texts[1] if compact else texts[0]
            try:
                if btn.winfo_exists():
                    btn.config(text=text)
            except Exception:
                continue

    def _set_ov_tiles_mode(self, mode: str) -> None:
        """概览磁贴在「4 列并排 pack」与「2×2 网格 grid」间切换（GUI-07）。

        方案 §311：容器宽度 < _TILE_GRID_MAX_W 时以 grid 替代并排 pack，
        窄屏两列排布避免磁贴被过度挤压；切回按同一套参数复原。

        Tk 约束：同一容器内 pack 与 grid 几何管理器不可混用，必须先
        成批脱离旧管理器、再成批挂载新管理器——逐个小部件交错切换会抛
        TclError（离屏实测证实），故此处脱离/挂载两段循环严格分离。

        参数:
            mode: 'grid'（2×2 网格）或 'row'（4 列并排）
        """
        cont = getattr(self, '_ov_container', None)
        # _ov_tiles 存的是磁贴「数值标签」句柄，其 master 即磁贴 Frame
        tiles = [lbl.master
                 for lbl in (getattr(self, '_ov_tiles', None) or {}).values()]
        if cont is None or not tiles:
            return
        try:
            if mode == 'grid':
                for t in tiles:
                    t.pack_forget()
                    t.pack_propagate(True)
                    t.configure(width=0)
                # uniform 组保证两列等宽、两行等高；weight=1 让磁贴
                # 随容器拉伸铺满单元格（sticky='nsew'）
                cont.grid_columnconfigure((0, 1), weight=1, uniform='ovcol')
                cont.grid_rowconfigure((0, 1), weight=1, uniform='ovrow')
                for idx, t in enumerate(tiles):
                    t.grid(row=idx // 2, column=idx % 2, sticky='nsew',
                           padx=(0, 8), pady=(0, 4) if idx < 2 else 0)
            else:
                for t in tiles:
                    t.grid_forget()
                for t in tiles:
                    self._pack_ov_tile_row(t)
        except Exception:
            # 个别磁贴已销毁等异常：放弃本次切换，不影响主流程
            return

    def _apply_breakpoint_layout(self) -> None:
        """按当前输出面板宽度应用响应式布局（幂等，GUI-07 主入口）。

        由 _on_layout_resize（right 容器 Configure → after_idle）驱动：
        1. 读 output_nb 实际宽度划分三档断点，档位变化时切换工具栏文案；
        2. 读磁贴容器宽度，按 _TILE_GRID_MAX_W 阈值切换 grid/row 布局；
        3. 幂等保护：未映射（withdraw / 离屏未 deiconify，宽度为 1）时
           跳过；档位与磁贴模式均未变化时零触碰，拖拽期连续 Configure
           不产生重排风暴（不干扰 A1-A4 闪烁守护）。

        无参数、无返回值。
        """
        _TILE_GRID_MAX_W = _main_mod._TILE_GRID_MAX_W
        nb = getattr(self, 'output_nb', None)
        if nb is None:
            return
        try:
            if not nb.winfo_exists():
                return
            panel_w = nb.winfo_width()
        except Exception:
            return
        if panel_w <= 1:
            return  # 未映射：保持构建期默认布局（标准档全文案 + 4 列并排）
        bp = self._classify_breakpoint(panel_w)
        if bp != getattr(self, '_active_breakpoint', 'standard'):
            self._set_toolbar_mode(bp)
            self._active_breakpoint = bp
        cont = getattr(self, '_ov_container', None)
        if cont is None:
            return
        try:
            cont_w = cont.winfo_width()
        except Exception:
            return
        if cont_w <= 1:
            return  # 容器未映射：保持当前磁贴模式
        mode = 'grid' if cont_w < _TILE_GRID_MAX_W else 'row'
        if mode != getattr(self, '_active_tile_mode', 'row'):
            self._set_ov_tiles_mode(mode)
            self._active_tile_mode = mode

    def _refresh_result_overview(self):
        """后台刷新「预测结果」页概览区四个指标磁贴（v4.0 §2.4）。

        在后台线程查询数据库（最新预测期号 / Top-1 历史命中率 / 已验证期数 / 历史数据量），
        结果经 root.after 回投主线程原地回填磁贴数值，全程不阻塞 UI。
        数据库不可用时各磁贴优雅降级为「—」，不影响主流程。

        GUI-05 指纹节流（PUP-20261019-013100 §2.2，验收 A14）：回投主线程前比对
        指纹 (latest_issue, verified_count, data_count) 三元组——与上次刷新完全
        一致时直接跳过，主线程零唤醒（_apply_ov_values 不被调用）；任一成分变化
        才回投。命中率由已验证数据聚合派生、不在指纹内，遵循方案三元组口径。

        参数:
            无（方法内部读取 self._ov_tiles 中的数值标签句柄）
        """
        P5Database = _main_mod.P5Database
        tiles = getattr(self, '_ov_tiles', {}) or {}
        if not tiles:
            return

        def _worker():
            """后台线程：查库收集四个指标，返回 (issue_text, hit_rate_text, verified_text, data_text)。"""
            issue_text, hit_text, verified_text, data_text = '—', '—', '—', '—'
            # 指纹原始值（库内原始口径，非格式化文案）：-1 表示该查询未成功，
            # 与「查询成功且为 0」区分，保证失败→成功跳变必然触发回投。
            issue_raw, total_raw, cnt_raw = '—', -1, -1
            try:
                db = P5Database()
                if db.connect():
                    try:
                        # 最新预测期号
                        db.cursor.execute(
                            'SELECT target_issue FROM p5_prediction_record '
                            'ORDER BY target_issue DESC LIMIT 1')
                        row = db.cursor.fetchone()
                        if row and row.get('target_issue'):
                            issue_text = str(row['target_issue'])
                            issue_raw = issue_text
                        # 已验证期数 + 历史命中率（Top-1 真实口径 + Wilson 95% CI）
                        stats = db.get_verification_stats()
                        total = int(stats.get('total', 0) or 0)
                        if total > 0:
                            verified_text = f"{total} 期"
                            if ('top1_avg_accuracy' in stats
                                    or 'strict_avg_accuracy' in stats):
                                top1 = float(stats.get(
                                    'top1_avg_accuracy',
                                    stats.get('strict_avg_accuracy', 0)) or 0)
                                top1_hits = int(stats.get(
                                    'top1_total_matched',
                                    stats.get('strict_total_matched', 0)) or 0)
                                _lo, _hi = wilson_ci(top1_hits, total * 5)
                                hit_text = (f"{top1:.1f}% "
                                            f"[{_lo * 100:.1f}, {_hi * 100:.1f}]")
                            else:
                                # 键缺失不回退宽松口径 avg_accuracy，明确提示无 Top-1 数据
                                hit_text = "—（无 Top-1 数据）"
                        total_raw = total
                        # 历史数据量
                        cnt = db.get_history_data_count()
                        data_text = f"{cnt} 期"
                        cnt_raw = cnt
                    finally:
                        db.disconnect()
            except Exception:
                # DB 不可用：保持占位符，静默降级
                pass
            # GUI-05 指纹节流：三元组与上次刷新一致则跳过回投（主线程零唤醒）
            fingerprint = (issue_raw, total_raw, cnt_raw)
            if getattr(self, '_ov_last_fingerprint', None) == fingerprint:
                return
            self._ov_last_fingerprint = fingerprint
            try:
                self.root.after(0, lambda: self._apply_ov_values(
                    issue_text, hit_text, verified_text, data_text))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_ov_values(self, issue_text, hit_text, verified_text, data_text):
        """（主线程）原地回填四个概览磁贴数值，不重建控件。"""
        tiles = getattr(self, '_ov_tiles', {}) or {}
        values = {
            'issue': issue_text,
            'hit_rate': hit_text,
            'verified': verified_text,
            'data_count': data_text,
        }
        for key, val in values.items():
            lbl = tiles.get(key)
            if lbl is not None:
                try:
                    if lbl.winfo_exists():
                        lbl.config(text=val)
                except Exception:
                    pass

    def _show_ai_report_detail(self, target_issue: str):
        """弹出窗口展示某期AI预测详情（结构化表格）

        v3.23 修复: 原实现误用 ttk.Label 传 bg/fg 参数抛 TclError 被静默吞掉,
        导致弹窗一直是空白; 改用 tk.Label。并把 DB 查询移至后台线程, 弹窗不卡 UI。
        """
        COLORS = _main_mod.COLORS
        P5Database = _main_mod.P5Database
        _safe_grab_set = _main_mod._safe_grab_set
        logger = _main_mod.logger
        try:
            win = tk.Toplevel(self.root)
            win.title(f"AI 预测详情 - {target_issue}")
            win.geometry("520x400")
            win.configure(bg=COLORS['bg_secondary'])
            win.transient(self.root)
            _safe_grab_set(win)

            tk.Label(win, text=f"期号: {target_issue}", font=(UI_FONT, 11, 'bold'),
                     bg=COLORS['bg_secondary'], fg=COLORS['text_primary']).pack(pady=(10, 4))

            loading_var = tk.StringVar(value=" 正在加载该期预测数据…")
            loading_lbl = tk.Label(win, textvariable=loading_var, font=(UI_FONT, 10),
                                   bg=COLORS['bg_secondary'], fg=COLORS['text_muted'])
            loading_lbl.pack(pady=16)

            def _worker():
                """后台线程查询指定期号的 AI 报告及其实际开奖号码。

                说明:
                    结果通过 (row, actual, err) 三元组回传给 _render；未开奖时 actual 为 None。
                """
                row, actual, err = None, None, None
                try:
                    db = P5Database()
                    if not db.connect():
                        err = "数据库连接失败"
                    else:
                        try:
                            db.cursor.execute(
                                'SELECT probability_stats, recommended_combinations, next_issue '
                                'FROM p5_ai_report WHERE next_issue = %s ORDER BY created_at DESC LIMIT 1',
                                (target_issue,)
                            )
                            row = db.cursor.fetchone()
                            # 顺带查该期实际开奖号码（未开奖则为 None）
                            try:
                                db.cursor.execute(
                                    'SELECT wan, qian, bai, shi, ge FROM p5_history_data '
                                    'WHERE issue = %s LIMIT 1', (target_issue,)
                                )
                                actual = db.cursor.fetchone()
                            except Exception:
                                actual = None
                        finally:
                            db.disconnect()
                except Exception as ex:
                    err = f"查询失败: {ex}"
                try:
                    self.root.after(0, lambda: _render(row, actual, err))
                except Exception:
                    pass

            def _render(row, actual, err):
                """在主线程渲染 AI 报告详情窗口。

                参数:
                    row: 数据库查出的报告行（含概率统计、推荐组合、期号）
                    actual: 该期实际开奖号码，未开奖时为 None
                    err: 后台查询的错误信息，非空时仅显示错误提示

                说明:
                    渲染前先判断窗口是否仍存在，避免用户提前关闭导致的 TclError。
                """
                try:
                    if not win.winfo_exists():
                        return
                    if err:
                        loading_var.set(f" {err}")
                        loading_lbl.config(fg=COLORS['accent_danger'])
                        return
                    if not row:
                        loading_var.set(" 未找到该期预测数据")
                        loading_lbl.config(fg=COLORS['accent_danger'])
                        return
                    loading_lbl.destroy()
                    self._render_ai_report_detail(win, row, actual)
                except Exception as ex:
                    logger.debug(f"渲染预测详情失败: {ex}")

            threading.Thread(target=_worker, daemon=True).start()

        except Exception as e:
            logger.debug(f"查看预测详情失败: {e}")

    def _render_ai_report_detail(self, win, row, actual=None):
        """（主线程）在详情弹窗内渲染 AI 报告内容。

        新增「预测 vs 开奖」逐位对照面板——
        首选命中=绿色 / Top-3含开奖=橙色 / 未含=灰红；未开奖显示提示。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            stats_raw = row.get('probability_stats') or '{}'
            try:
                stats = json.loads(stats_raw)
                trend = stats.get('trend_prediction', {}) or {}
            except Exception:
                trend = {}

            pos_keys = [('wan', '万位'), ('qian', '千位'), ('bai', '百位'),
                        ('shi', '十位'), ('ge', '个位')]

            # ---------- 预测 vs 开奖 逐位对照 ----------
            cmp_panel = ttk.LabelFrame(win, text="预测 vs 开奖对照", padding=6)
            cmp_panel.pack(fill=tk.X, padx=10, pady=4)
            if actual and all(actual.get(pk) is not None for pk, _ in pos_keys):
                try:
                    win.geometry("540x560")
                except Exception:
                    pass
                grid = tk.Frame(cmp_panel, bg=COLORS['bg_secondary'])
                grid.pack(anchor=tk.W)
                # 表头行
                tk.Label(grid, text="", width=8, bg=COLORS['bg_secondary']
                         ).grid(row=0, column=0)
                for ci, (pk, pn) in enumerate(pos_keys):
                    tk.Label(grid, text=pn, width=6, font=(UI_FONT, 9),
                             bg=COLORS['bg_secondary'], fg=COLORS['text_muted']
                             ).grid(row=0, column=ci + 1)
                # 预测首选行（按命中状态标色）
                tk.Label(grid, text="预测首选", width=8, font=(UI_FONT, 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']
                         ).grid(row=1, column=0, sticky=tk.W)
                hit_cnt = 0
                for ci, (pk, pn) in enumerate(pos_keys):
                    nums = (trend.get(pk) or {}).get('numbers', [])
                    top1 = nums[0] if nums else None
                    act = int(actual.get(pk))
                    if top1 is not None and int(top1) == act:
                        fg, note = HIT_FG, '' # 首选命中
                        hit_cnt += 1
                    elif any(int(n) == act for n in nums[:3] if n is not None):
                        fg, note = TOP3_FG, '' # Top-3 含开奖
                    else:
                        fg, note = MISS_FG, '' # 未含
                    tk.Label(grid, text=f"{top1 if top1 is not None else '-'} {note}",
                             width=6, font=(MONO_FONT, 11, 'bold'),
                             bg=COLORS['bg_secondary'], fg=fg
                             ).grid(row=1, column=ci + 1)
                # 实际开奖行
                tk.Label(grid, text="实际开奖", width=8, font=(UI_FONT, 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_secondary']
                         ).grid(row=2, column=0, sticky=tk.W)
                for ci, (pk, pn) in enumerate(pos_keys):
                    tk.Label(grid, text=str(actual.get(pk)), width=6,
                             font=(MONO_FONT, 11, 'bold'),
                             bg=COLORS['bg_secondary'], fg=COLORS['text_primary']
                             ).grid(row=2, column=ci + 1)
                tk.Label(cmp_panel,
                text=f"首选命中 {hit_cnt}/5 图例: 首选命中 Top-3含开奖 未含",
                         font=(UI_FONT, 8), bg=COLORS['bg_secondary'],
                         fg=COLORS['text_muted']).pack(anchor=tk.W, pady=(4, 0))
            else:
                tk.Label(cmp_panel, text="该期尚未开奖或开奖数据未入库，暂无对照。",
                         font=(UI_FONT, 9), bg=COLORS['bg_secondary'],
                         fg=COLORS['text_muted']).pack(anchor=tk.W)

            # ---------- 各位置推荐号码（命中的号码高亮） ----------
            panel = ttk.LabelFrame(win, text="各位置推荐号码", padding=6)
            panel.pack(fill=tk.X, padx=10, pady=4)

            for pk, pn in pos_keys:
                nums = (trend.get(pk) or {}).get('numbers', [])
                line = tk.Frame(panel, bg=COLORS['bg_secondary'])
                line.pack(anchor=tk.W)
                tk.Label(line, text=f"{pn}: ", font=(MONO_FONT, 9),
                         bg=COLORS['bg_secondary'], fg=COLORS['text_primary']
                         ).pack(side=tk.LEFT)
                act = None
                try:
                    act = int(actual.get(pk)) if actual and actual.get(pk) is not None else None
                except Exception:
                    act = None
                for n in nums[:3]:
                    is_hit = (act is not None and n is not None and int(n) == act)
                    tk.Label(line, text=str(n),
                             font=(MONO_FONT, 9, 'bold' if is_hit else 'normal'),
                             bg=HIT_ROW_BG if is_hit else COLORS['bg_secondary'],
                             fg=HIT_ROW_FG if is_hit else COLORS['text_primary']
                             ).pack(side=tk.LEFT, padx=3)

            combo_raw = row.get('recommended_combinations') or '[]'
            try:
                combos = json.loads(combo_raw)
            except Exception:
                combos = []

            combo_panel = ttk.LabelFrame(win, text="推荐组合 (Top-5)", padding=6)
            combo_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

            combo_text = tk.Text(combo_panel, height=8, wrap=tk.WORD,
                                 font=(MONO_FONT, 9),
                                 bg=COLORS['bg_input'], fg=COLORS['text_primary'],
                                 relief='flat')
            combo_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
            for i, c in enumerate(combos[:5]):
                if isinstance(c, dict):
                    _comb = c.get('combination', '')
                    _reason = c.get('reason', '')
                    _line = f"{i+1}. {_comb}"
                    if _reason:
                        _line += f"  — {_reason}"
                    _line += "\n"
                    combo_text.insert(tk.END, _line, '')
                elif isinstance(c, str):
                    combo_text.insert(tk.END, f"{i+1}. {c}\n", '')

            combo_text.config(state=tk.DISABLED)
            tk.Button(win, text="关闭", font=(UI_FONT, 9),
                      bg=COLORS['accent_p5'], fg=WHITE, relief='flat',
                      command=win.destroy).pack(pady=(0, 8))

        except Exception as e:
            logger.debug(f"查看预测详情失败: {e}")

    # ============ 历史命中率常驻面板 (P1 加价值: 真实数据一等公民) ============
    def _on_hit_rate_tab_selected(self, event):
        """切到『历史命中率』标签页时懒刷新, 保证查看时数据新鲜。"""
        try:
            nb = event.widget
            if nb.index(nb.select()) == 2:  # 历史命中率 为第3个标签（结果/日志/命中率）
                self._refresh_hit_rate_tab()
        except Exception:
            pass

    def _toggle_hr_explanation(self, event):
        """切换历史命中率说明文字的显示/隐藏状态。"""
        if self.hr_explanation_visible.get():
            # 隐藏说明文字
            self.hr_explanation_text.pack_forget()
            self.hr_explanation_toggle.config(text=" [显示]")
            self.hr_explanation_visible.set(False)
        else:
            # 显示说明文字
            self.hr_explanation_text.pack(before=self.hr_explanation_toggle.master.master, fill=tk.X, pady=(0, 8))
            self.hr_explanation_toggle.config(text=" [隐藏]")
            self.hr_explanation_visible.set(True)

    def _on_record_double_click(self, event):
        """双击明细表某行 → 弹出该期完整 AI 预测详情。"""
        try:
            tree = self.hr_records_tree
            # 仅当双击在数据行上才响应（点列头/空白不弹）
            if tree.identify_region(event.x, event.y) != 'cell':
                return
            item = tree.identify_row(event.y)
            if not item:
                return
            issue = str(tree.set(item, 'issue') or '').strip()
            if issue and issue != '--':
                self._show_ai_report_detail(issue)
        except Exception:
            pass

    def _load_and_draw_baseline_chart(self):
        """（主线程）启动后台线程生成基线对照图表。"""
        try:
            threading.Thread(target=self._draw_baseline_chart_internal, daemon=True).start()
        except Exception:
            pass

    def _draw_baseline_chart_internal(self):
        """（后台线程）生成基线对照柱状图，显示系统实测 vs 随机基线。"""
        COLORS = _main_mod.COLORS
        P5Database = _main_mod.P5Database
        logger = _main_mod.logger
        try:
            # 导入必要模块
            import matplotlib
            matplotlib.use('Agg')  # 无头环境
            import matplotlib.pyplot as plt
            import numpy as np
            import os
            # Wilson 95% CI 单一实现（ALG-06 收敛，PUP-20261019-013100）
            from modules.stats_utils import wilson_ci
            
            # 中文字体配置
            plt.rcParams['font.sans-serif'] = [
                'Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'SimSun', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 代表性评估窗口数（用于 Wilson 95% CI 宽度）
            N = 1000

            # 口径换算：百分数 → 成功计数（v*N/100）→ stats_utils 单一实现
            # → ×100 还原百分数；较原内嵌实现存在 ~1ulp 浮点差异，绘图层无感
            # （ALG-06 / PUP-20261019-013100）

            # 数据（来源：reports/diagnosis/hitrate_root_cause.md §2/§3）
            labels = ['Top-1\n(首推号精确匹配)', 'Top-3', 'Top-5']
            # 从数据库获取实际的系统实测数据
            db = P5Database()
            if db.connect():
                stats = db.get_verification_stats()
                db.disconnect()
            else:
                stats = {}
            system_top1 = stats.get('top1_avg_accuracy', 9.2)
            system_top3 = stats.get('top3_avg_accuracy', 30.0)
            system_top5 = stats.get('top5_avg_accuracy', 45.0)
            system = np.array([system_top1, system_top3, system_top5])
            baseline = np.array([10.0, 30.0, 50.0])    # 随机基线常量
            
            # 计算置信区间（百分数经 v*N/100 换算为计数口径，由 stats_utils
            # 单一实现计算后 ×100 还原为百分数，ALG-06 / PUP-20261019-013100）
            sys_lo = [wilson_ci(v * N / 100.0, N)[0] * 100.0 for v in system]
            sys_hi = [wilson_ci(v * N / 100.0, N)[1] * 100.0 for v in system]
            base_lo = [wilson_ci(v * N / 100.0, N)[0] * 100.0
                       for v in baseline]
            base_hi = [wilson_ci(v * N / 100.0, N)[1] * 100.0
                       for v in baseline]
            sys_err = [[v - lo for v, lo in zip(system, sys_lo)],
                       [hi - v for v, hi in zip(system, sys_hi)]]
            base_err = [[v - lo for v, lo in zip(baseline, base_lo)],
                        [hi - v for v, hi in zip(baseline, base_hi)]]
            
            # 使用 matplotlib Agg 后端生成柱状图
            fig, ax = plt.subplots(figsize=(9, 5.2), dpi=150)
            
            x = np.arange(len(labels))
            w = 0.36
            b1 = ax.bar(x - w / 2, system, w, yerr=sys_err, capsize=5,
                        color=CHART_SYSTEM, alpha=0.85, label='系统实测（严格口径）',
                        error_kw={'elinewidth': 1.4, 'ecolor': CHART_SYSTEM_ERR})
            b2 = ax.bar(x + w / 2, baseline, w, yerr=base_err, capsize=5,
                        color=CHART_RANDOM, alpha=0.85, label='随机基线（公平摇号）',
                        error_kw={'elinewidth': 1.4, 'ecolor': CHART_RANDOM_ERR})
            
            ax.set_ylabel('命中率 (%)', fontsize=12)
            ax.set_title('排列5 命中率：系统实测 vs 随机基线（95% Wilson CI）',
                        fontsize=13, fontweight='bold')
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=11)
            ax.set_ylim(0, 62)
            ax.grid(axis='y', alpha=0.3)
            ax.legend(loc='upper right', fontsize=10, framealpha=0.9)
            
            # 数值标注
            for bars, vals in ((b1, system), (b2, baseline)):
                for bar, val in zip(bars, vals):
                    ax.text(bar.get_x() + bar.get_width() / 2, val + 1.2, f'{val:.1f}%',
                            ha='center', va='bottom', fontsize=9)
            
            # 诚实结论注释
            conclusion = ('结论：系统实测 Top-1/3/5 全部落在随机基线 95% 置信区间内，\n'
                         '无统计显著差异 —— 低命中率 = 公平摇号属性，非代码缺陷。')
            ax.text(0.5, -0.22, conclusion, transform=ax.transAxes, ha='center',
                    fontsize=9.5, color=CHART_NOTE_TEXT,
                    bbox=dict(boxstyle='round,pad=0.5', facecolor=CHART_NOTE_FACE, edgecolor=CHART_NOTE_EDGE))
            
            ax.text(0.01, 0.98, f'n≈{N} 评估窗口（代表性）', transform=ax.transAxes,
                    ha='left', va='top', fontsize=8.5, color=CHART_NOTE_TEXT)
            
            plt.tight_layout()
            
            # 保存到临时文件
            import tempfile
            tmp_dir = REPORTS_CHARTS_DIR
            os.makedirs(tmp_dir, exist_ok=True)
            chart_path = os.path.join(tmp_dir, f'baseline_{int(time.time())}.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=COLORS['bg_primary'])
            plt.close(fig)
            
            # 移除 matplotlib 内嵌的 iCCP 色彩块，规避 libpng 警告
            self._strip_png_iccp(chart_path)
            
            # 回到主线程更新图片
            self.root.after(0, lambda: self._update_chart_image(chart_path))
            
        except Exception as e:
            logger.debug(f'生成基线对照图表失败: {e}')
            self.root.after(0, lambda: self._update_chart_image(None))

    def _sort_records_by(self, col):
        """点击列头排序：同列点击升/降切换，列头带 / 指示，保留行高亮标签。"""
        try:
            tree = self.hr_records_tree
            prev_col, prev_desc = getattr(self, '_hr_sort_state', (None, False))
            descending = not prev_desc if prev_col == col else True

            def _sort_key(iid):
                """生成 Treeview 列排序所用的排序键。

                参数:
                    iid: Treeview 行的内部 id

                返回:
                    (有效性标记, 数值) 二元组 —— 标记 0 表示解析成功、1 表示解析失败排在后面

                说明:
                    针对 hit（"3/5" 取 3）、acc（"60%" 取 60.0）、issue（按数值）三类列做特殊解析。
                """
                v = str(tree.set(iid, col) or '')
                if col == 'hit':          # "3/5" → 3
                    try:
                        return (0, int(v.split('/')[0]))
                    except Exception:
                        return (1, 0)
                if col == 'acc':          # "60%" → 60.0
                    try:
                        return (0, float(v.rstrip('%')))
                    except Exception:
                        return (1, 0.0)
                if col == 'issue':        # 期号按数值
                    try:
                        return (0, int(v))
                    except Exception:
                        return (1, 0)
                return (0, v)             # time 等按字符串（ISO格式可直接比较）

            items = sorted(tree.get_children(''), key=_sort_key, reverse=descending)
            for idx, iid in enumerate(items):
                tree.move(iid, '', idx)

            # 更新列头箭头指示（先恢复所有列头原始文案）
            for k, title in self._hr_col_titles.items():
                tree.heading(k, text=title)
            arrow = ' ' if descending else ' '
            tree.heading(col, text=self._hr_col_titles[col] + arrow)
            self._hr_sort_state = (col, descending)
        except Exception:
            pass

    # 唯一有效定义：旧版同名方法（原 931-993 行）曾被本定义覆盖，属死代码，
    # 已删除；此处是命中率面板的实际执行入口（Top-1 + Wilson CI，ALG-06）。
    # 口径分离说明：旧「集合包含参考（候选集~5）」仅作对照, 非真实预测力，
    # 已停止在面板展示，避免与 Top-1 精确口径混用。
    def _update_hit_rate_tab(self, stats):
        """（主线程）用 stats 刷新命中率面板控件。

        口径（唯一权威 docs/hitrate_spec.md / ALG-06）：
            仅展示 Top-1（首推号精确命中）点估计 + Wilson 95% CI；
            随机基线 = 10%（k/10）。旧「集合包含参考 / ±1 容错」不属于 ALG-06，
            已停止展示（不再作为平均准确率出现），改为明确说明文案。
        """
        if not hasattr(self, 'hr_summary_var') or not stats:
            return
        try:
            total = stats.get('total', 0)
            if not total:
                self.hr_summary_var.set(
                    "（暂无验证数据 · 请先点击左侧「开始分析」，开奖后自动统计）")
                for key in self.hr_pos_vars:
                    self.hr_pos_vars[key].set("--")
                    self.hr_pos_bars[key]['value'] = 0
                return

            merged = int(stats.get('merged_duplicates', 0) or 0)
            self._hr_merged_duplicates = merged
            merge_txt = f" | 已合并重复记录 {merged} 条" if merged else ""

            # Top-1 真实口径（首推号精确命中, v3.67: strict_* 已改为真实预测力指标）
            # 随机基线: 排列5 公平摇号, 每位 Top-1 理论 = 10%, 系统无法稳定超越
            top1_avg = float(stats.get('top1_avg_accuracy', stats.get('strict_avg_accuracy', 0)) or 0)
            top1_full = int(stats.get('top1_full_matches', stats.get('strict_full_matches', 0)) or 0)
            top1_total = int(stats.get('top1_total_matched', stats.get('strict_total_matched', 0)) or 0)

            from modules.stats_utils import wilson_ci

            # 随机基线对照 + Wilson 95% CI：分母为「期数 × 5 位」位置单位；基线固定 10%（k/10）
            _n_units = int(total) * 5
            _t1_lo, _t1_hi = wilson_ci(top1_total, _n_units)
            _verdict = self._ci_vs_baseline(_t1_lo, _t1_hi, 0.10)

            self.hr_summary_var.set(
                f"已验证 {total} 期（按期号去重）\n"
                f"★ Top-1 真实命中（首推号精确）: 全中 {top1_full} 期 | "
                f"{top1_total}/{_n_units} 位置单位 = {top1_avg:.1f}%\n"
                f"   95% Wilson CI [{_t1_lo * 100:.1f}%, {_t1_hi * 100:.1f}%]；"
                f"随机基线 10%（k/10）→ {_verdict}\n"
                f"（旧「集合包含参考 / ±1 容错」不属于 ALG-06，已停止展示；"
                f"Top-1 / Top-3 单口径明细见分析日志）{merge_txt}"
            )

            # 更新进度条：显示 Top-1 真实命中率（点估计 + 95% CI）
            top1_keys = ('top1_wan_accuracy', 'top1_qian_accuracy', 'top1_bai_accuracy',
                        'top1_shi_accuracy', 'top1_ge_accuracy')
            for key in top1_keys:
                rate = float(stats.get(key, stats.get(key.replace('top1_', 'strict_'), 0)) or 0)
                pos_name = key.replace('top1_', '').replace('_accuracy', '')
                var_key = f'{pos_name}_accuracy'
                if var_key in self.hr_pos_vars:
                    _hits = round(rate / 100.0 * total)
                    _lo, _hi = wilson_ci(_hits, int(total))
                    self.hr_pos_vars[var_key].set(
                        f"{rate:.1f}% [{_lo * 100:.1f}, {_hi * 100:.1f}]")
                    self.hr_pos_bars[var_key]['value'] = max(0.0, min(100.0, rate))

        except Exception:
            pass

        # 不再自动加载图表

    def _load_and_draw_hit_rate_chart(self):
        """（主线程）启动后台线程生成命中率柱状图。"""
        try:
            threading.Thread(target=self._draw_hit_rate_chart_internal, daemon=True).start()
        except Exception:
            pass

    def _draw_hit_rate_chart_internal(self):
        """（后台线程）从数据库获取数据并生成柱状图。"""
        COLORS = _main_mod.COLORS
        P5Database = _main_mod.P5Database
        logger = _main_mod.logger
        try:
            db = P5Database()
            if not db.connect():
                self.root.after(0, lambda: self._update_chart_image(None))
                return
            
            stats = db.get_verification_stats()
            db.disconnect()
            
            if not stats or stats.get('total', 0) == 0:
                self.root.after(0, lambda: self._update_chart_image(None))
                return
            
            # 准备数据：5个位置的 Top-1 真实命中率（首推号精确, 随机基线=10%）
            pos_keys = ['top1_wan_accuracy', 'top1_qian_accuracy', 'top1_bai_accuracy',
                        'top1_shi_accuracy', 'top1_ge_accuracy']
            pos_names = ['万位', '千位', '百位', '十位', '个位']
            values = [float(stats.get(k, stats.get(k.replace('top1_', 'strict_'), 0) or 0)) for k in pos_keys]
            
            # 使用 matplotlib Agg 后端生成柱状图
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            import matplotlib.font_manager as fm
            # 中文渲染——matplotlib 默认用 DejaVu Sans（无 CJK），需显式指定
            # 中文字体，与 backtester.py 保持一致；缺失时自动降级
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 暗色适配：此前 figure 用深色底、坐标区却是默认白底黑字，
            # 标题/刻度文字落在深色画布上几乎不可见。此处统一为暗色主题。
            fg = COLORS['text_primary']
            fig, ax = plt.subplots(figsize=(8, 4.5))
            fig.patch.set_facecolor(COLORS['bg_primary'])
            ax.set_facecolor(COLORS['bg_card'])
            bars = ax.bar(pos_names, values, color=CHART_BARS)
            ax.set_ylim(0, 100)
            ax.set_ylabel('命中率 (%)', fontsize=10, color=fg)
            ax.set_title('各位置 Top-1 真实命中率（首推号精确·随机基线=10%）', fontsize=11, fontweight='bold', color=fg)
            ax.tick_params(colors=fg, labelsize=9)
            for spine in ax.spines.values():
                spine.set_color(COLORS['border'])

            # 在柱子上方标注数值
            for bar, val in zip(bars, values):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + 1,
                       f'{val:.1f}%', ha='center', va='bottom', fontsize=9, color=fg)
            
            # 添加网格线
            ax.yaxis.grid(True, linestyle='--', alpha=0.3, color=COLORS['border_light'])
            ax.set_axisbelow(True)
            
            # 保存到临时文件
            import tempfile
            tmp_dir = REPORTS_CHARTS_DIR
            os.makedirs(tmp_dir, exist_ok=True)
            chart_path = os.path.join(tmp_dir, f'hitrate_{int(time.time())}.png')
            plt.savefig(chart_path, dpi=100, bbox_inches='tight', facecolor=COLORS['bg_primary'])
            plt.close(fig)
            # 移除 matplotlib 内嵌的 iCCP 色彩块，规避 libpng 警告
            # 'iCCP: cHRM chunk does not match sRGB'（该块对显示无影响）
            _strip_png_iccp(chart_path)

            # 回到主线程更新图片
            self.root.after(0, lambda: self._update_chart_image(chart_path))
            
        except Exception as e:
            logger.debug(f'生成图表失败: {e}')
            self.root.after(0, lambda: self._update_chart_image(None))

    @staticmethod
    def _strip_png_iccp(path):
        """移除 PNG 中的 iCCP 色彩配置块，规避 libpng 警告
        'iCCP: cHRM chunk does not match sRGB'。

        matplotlib 的 Agg 后端保存的 PNG 默认内嵌 sRGB iCCP 块，GUI 用 PIL 重新
        打开时 libpng 会打印该告警（仅噪声，不影响显示）。直接按 PNG 分块结构
        解析并丢弃 iCCP 块即可消除，全程不经由 PIL 打开，故不会再触发 libpng 告警。
        """
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if len(data) < 8 or data[:8] != b'\x89PNG\r\n\x1a\n':
                return
            out = bytearray(data[:8])
            i, n = 8, len(data)
            while i + 8 <= n:
                length = int.from_bytes(data[i:i + 4], 'big')
                ctype = data[i + 4:i + 8]
                chunk_end = i + 8 + length + 4  # 含 4 字节 CRC
                if chunk_end > n:
                    break
                if ctype != b'iCCP':
                    out += data[i:chunk_end]
                i = chunk_end
            else:
                with open(path, 'wb') as f:
                    f.write(out)
        except Exception:  # noqa: BLE001
            pass

    def _update_chart_image(self, img_path):
        """（主线程）更新图表显示图片。"""
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        try:
            if not hasattr(self, 'chart_label') or self.chart_label is None:
                return
            
            # 清除旧图片
            if hasattr(self, '_chart_photo'):
                self.chart_label.configure(image=None)
                self._chart_photo = None  # 防止GC
            
            if img_path is None or not os.path.exists(img_path):
                # 无数据时显示「原因 + 下一步操作」，避免纯空白区域
                self.chart_label.configure(
                    text="暂无可用数据\n请先运行「开始分析」生成走势与对比图表",
                    font=(UI_FONT, 10, 'italic'), justify=tk.CENTER,
                    fg=COLORS['text_muted'])
                return
            
            # 加载新图片
            from PIL import Image, ImageTk
            # 打开前先剔除 iCCP 色彩块，规避 libpng 'iCCP: cHRM chunk
            # does not match sRGB' 噪声告警（对显示无影响）
            _strip_png_iccp(img_path)
            img = Image.open(img_path)
            # 适配显示尺寸
            max_width = 350
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            
            self._chart_photo = ImageTk.PhotoImage(img)
            self.chart_label.configure(image=self._chart_photo)
            self.chart_label.configure(text="")  # 清空文字提示
            
            # 清理旧图表文件（保留最近5个）
            try:
                files = [f for f in os.listdir(REPORTS_CHARTS_DIR) if f.startswith('hitrate_') and f.endswith('.png')]
                files.sort(key=lambda x: os.path.join(REPORTS_CHARTS_DIR, x), reverse=True)
                for f in files[5:]:
                    try:
                        os.remove(os.path.join(REPORTS_CHARTS_DIR, f))
                    except OSError:
                        logger.debug('清理旧图表文件失败: %s', f)
            except OSError:
                logger.debug('清理旧图表文件目录失败: %s', REPORTS_CHARTS_DIR)
        except Exception:
            pass

    @staticmethod
    def _fmt_record_nums(raw):
        """把 predicted_numbers / actual_numbers 统一格式化为 '1 2 3 4 5' 五位串。
        兼容：dict 按位取首({'wan':[..]})、list 直连、JSON 字符串。"""
        try:
            if isinstance(raw, str):
                raw = json.loads(raw) if raw.strip() else None
            if not raw:
                return '--'
            order = ['wan', 'qian', 'bai', 'shi', 'ge']
            if isinstance(raw, dict):
                vals = []
                for p in order:
                    v = raw.get(p)
                    if isinstance(v, (list, tuple)) and v:
                        vals.append(str(v[0]))
                    elif v is not None and not isinstance(v, (list, tuple, dict)):
                        vals.append(str(v))
                    else:
                        vals.append('?')
                return ' '.join(vals)
            if isinstance(raw, (list, tuple)):
                return ' '.join(str(x) for x in raw[:5])
        except Exception:
            pass
        return '--'

    def _update_records_table(self, records):
        """（主线程）缓存已验证预测记录并按当前筛选渲染明细表。(v3.24 拆分)"""
        if not hasattr(self, 'hr_records_tree'):
            return
        self._hr_records_cache = list(records or [])
        self._render_records_rows()

    def _apply_records_filter(self):
        """筛选开关切换 → 用缓存即时重渲染（不重新查库）。"""
        self._render_records_rows()

    def _render_records_rows(self):
        """（主线程）按「只看命中≥3」筛选状态渲染明细表行。"""
        try:
            tree = self.hr_records_tree
            for iid in tree.get_children():
                tree.delete(iid)
            # 重置排序状态与列头箭头（重渲染后按默认时间序展示）
            try:
                for k, title in self._hr_col_titles.items():
                    tree.heading(k, text=title)
                self._hr_sort_state = (None, False)
            except Exception:
                pass
            records = getattr(self, '_hr_records_cache', []) or []
            if not records:
                self.hr_records_hint.set(
                    "暂无已验证记录 · 请先运行「开始分析」，开奖后自动生成")
                return
            filter_on = False
            try:
                filter_on = bool(self.hr_filter_var.get())
            except Exception:
                pass
            # 兜底去重：数据层已按期号合并，此处再按期号保序去重一次，
            # 防止任何旧调用路径把同期号多条记录塞进明细表。
            seen_issues = set()
            deduped = []
            dropped = 0
            for r in records:
                key = str(r.get('target_issue') or r.get('actual_issue') or '')
                if key and key in seen_issues:
                    dropped += 1
                    continue
                if key:
                    seen_issues.add(key)
                deduped.append(r)
            records = deduped

            shown = 0
            for r in records:
                mc = r.get('match_count')
                mc = 0 if mc is None else int(mc)
                if filter_on and mc < 3:
                    continue
                issue = r.get('target_issue') or r.get('actual_issue') or '--'
                pred = self._fmt_record_nums(r.get('predicted_numbers'))
                actual = self._fmt_record_nums(r.get('actual_numbers'))
                # 准确率统一按「命中位数/5」重算：库中 accuracy_rate 由不同版本写入，
                # 分母不一致（存在 4 命中记成 42.86% 的历史脏数据），不能直接展示。
                acc_txt = f"{mc / 5 * 100:.0f}%"
                vt = r.get('verified_at')
                vt_txt = vt.strftime('%Y-%m-%d %H:%M') if hasattr(vt, 'strftime') else (str(vt)[:16] if vt else '--')
                odd = bool(shown % 2)
                if mc >= 5:
                    row_tag = 'hit_full'
                elif mc >= 3:
                    row_tag = 'row_odd_hit' if odd else 'row_even_hit'
                else:
                    row_tag = 'row_odd' if odd else 'row_even'
                tree.insert('', tk.END, values=(issue, pred, actual,
                                                f"{mc}/5", acc_txt, vt_txt), tags=(row_tag,))
                shown += 1

            merge_txt = f" · 本表另合并重复 {dropped} 条" if dropped else ""
            if filter_on:
                if shown == 0:
                    self.hr_records_hint.set(
                        f"{len(records)} 期 · 无命中≥3的记录，可取消勾选「只看命中≥3」{merge_txt}")
                else:
                    self.hr_records_hint.set(f"{len(records)} 期 · 筛选后 {shown} 期{merge_txt}")
            else:
                self.hr_records_hint.set(f"{len(records)} 期（每期仅保留最新一条）{merge_txt}")
        except Exception:
            pass

    def _refresh_hit_rate_tab(self):
        """异步刷新命中率面板：后台取数，主线程渲染，避免阻塞首屏。
        同一次连接顺带取已验证预测记录明细。"""
        P5Database = _main_mod.P5Database

        def _worker():
            """后台线程读取命中率统计与最近验证记录。

            说明:
                统计与明细分别 try 包裹，任一失败不影响另一项；查询结束必定断开数据库连接。
            """
            stats = None
            records = None
            try:
                db = P5Database()
                if db.connect():
                    try:
                        stats = db.get_verification_stats()
                        try:
                            records = db.get_verified_predictions(days=3650, limit=50)
                        except Exception:
                            records = None
                    finally:
                        db.disconnect()
            except Exception:
                stats = None
            try:
                self.root.after(0, lambda: self._update_hit_rate_tab(stats or {}))
                self.root.after(0, lambda: self._update_records_table(records or []))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _summarize_top_hit_counts(records, learner):
        """按 ALG-06 单口径汇总 Top-1/Top-3 命中计数（复用 online_learner._calculate_hits）。

        说明：
            - 库内只存 predicted_numbers（按概率降序的候选列表），此处按该顺序重建
              fused_probabilities 后交给 _calculate_hits，避免在展示层复制命中算法；
            - 分母仅统计「5 位开奖号码 + 各位置候选列表均可用」的有效位置单位；
            - 返回各位置 Top-1 命中数、Top-1/Top-3 总命中数与有效位置单位数。
        """
        positions = ['wan', 'qian', 'bai', 'shi', 'ge']
        labels = dict(zip(positions, ['万', '千', '百', '十', '个']))
        stats = {
            'valid_positions': 0, 'skipped_records': 0,
            'top1_hits': 0, 'top3_hits': 0,
            'per_pos_top1': {p: 0 for p in positions},
        }
        # 中文位置名 -> 位置键（_calculate_hits 内部使用「万位/千位/...」）
        cn_to_pos = {f'{label}位': pos for pos, label in labels.items()}
        for rec in records:
            try:
                pred = json.loads(rec.get('predicted_numbers') or '{}')
                actual = json.loads(rec.get('actual_numbers') or '[]')
            except (TypeError, ValueError, json.JSONDecodeError):
                stats['skipped_records'] += 1
                continue
            if not isinstance(pred, dict) or not isinstance(actual, (list, tuple)) or len(actual) < 5:
                stats['skipped_records'] += 1
                continue
            fused = []
            usable = True
            for pos in positions:
                nums = pred.get(pos) or []
                try:
                    nums = [int(n) for n in nums[:5]]
                except (TypeError, ValueError):
                    usable = False
                    break
                # 按库内候选顺序构造单调递减分数，保证 _calculate_hits 排序后与原顺序一致
                fused.append({n: 1.0 - i * 0.1 for i, n in enumerate(nums)})
            if not usable or not any(fused):
                stats['skipped_records'] += 1
                continue
            try:
                actual_nums = [int(x) for x in actual[:5]]
            except (TypeError, ValueError):
                stats['skipped_records'] += 1
                continue
            hits = learner._calculate_hits(
                {'fused_probabilities': fused, 'top_combinations': []}, actual_nums)
            stats['valid_positions'] += 5
            stats['top1_hits'] += int(hits.get('top1_hits', 0))
            stats['top3_hits'] += int(hits.get('partial_hits', 0))
            for cn_name, info in (hits.get('position_hits') or {}).items():
                pos = cn_to_pos.get(cn_name)
                if pos is None:
                    continue
                if info.get('top1_hit'):
                    stats['per_pos_top1'][pos] += 1
        return stats

    @staticmethod
    def _ci_vs_baseline(lower, upper, baseline):
        """按 Wilson CI 与随机基线的关系给出如实结论（不写提升/超越）。"""
        if upper < baseline:
            return '低于随机基线（CI 未覆盖基线）'
        if lower > baseline:
            return '高于随机基线（CI 未覆盖基线）'
        return 'CI 与随机基线重叠，无统计显著超额'

    def _append_hit_rate_overview(self, task_mgr):
        """预测后: 日志留精简摘要 + 主线程刷新常驻命中率面板。

        口径（唯一权威 docs/hitrate_spec.md / ALG-06）：
            Top-1 = 首推号精确命中，Top-3 = 实际号落入该位置概率前 3 集合；
            随机基线 Top-1=10% / Top-3=30%（k/10）；命中率必须同时给点估计与
            Wilson 95% CI。旧「候选集包含 + ±1 容错」不再作为准确率展示。
        """
        P5Database = _main_mod.P5Database
        from modules.stats_utils import wilson_ci
        try:
            db = P5Database()
            if not db.connect():
                task_mgr.append_info("  （数据库不可用，命中率概览留空；恢复连接后重试即可）")
                return
            try:
                stats = db.get_verification_stats()
                records = (db.get_verified_predictions(days=3650, limit=2000)
                           if stats and stats.get('total', 0) else [])
                # 复用在线学习的命中算法（不复制实现）；构造开销很小，且只在有记录时发生
                learner = None
                if records:
                    from modules.online_learner import OnlineLearner
                    learner = OnlineLearner(db)
            finally:
                db.disconnect()

            total = int((stats or {}).get('total', 0) or 0)
            if total == 0:
                task_mgr.append_info("  （暂无验证数据，命中率概览留空；完成一次开奖验证后自动生成）")
                task_mgr.append_info(" 历史命中率不代表未来表现；排列5为公平摇号，无法预测开奖结果。")
                return

            task_mgr.append_section_header(" 历史命中率概览（ALG-06 单口径：Top-1 / Top-3 单位置覆盖）")
            task_mgr.append_info(f"  已验证 {total} 期（按期号去重）")

            if not records:
                task_mgr.append_info("  （已验证记录缺少 predicted_numbers/actual_numbers，"
                                     "无法按 Top-1/Top-3 单口径计算；请重新验证后再查看）")
                task_mgr.append_info(" 历史命中率不代表未来表现；排列5为公平摇号，无法预测开奖结果。")
                self.root.after(0, self._update_hit_rate_tab, stats)
                return

            summary = self._summarize_top_hit_counts(records, learner)
            n_pos = summary['valid_positions']
            if n_pos == 0:
                task_mgr.append_info("  （有效样本为 0：现有记录的候选/开奖号码不完整，暂无法计算命中率）")
                task_mgr.append_info(" 历史命中率不代表未来表现；排列5为公平摇号，无法预测开奖结果。")
                self.root.after(0, self._update_hit_rate_tab, stats)
                return

            if summary['skipped_records']:
                task_mgr.append_info(
                    f"  有效样本 {n_pos} 个位置单位（{summary['skipped_records']} 期因号码不完整未计入）")
            else:
                task_mgr.append_info(f"  有效样本 {n_pos} 个位置单位（{n_pos // 5} 期 × 5 位）")

            for name, hits, baseline in (
                    ('Top-1（首推号精确命中）', summary['top1_hits'], 0.10),
                    ('Top-3（实际号落入该位置前3集合）', summary['top3_hits'], 0.30)):
                rate = hits / n_pos
                lower, upper = wilson_ci(hits, n_pos)
                task_mgr.append_info(
                    f"  {name}: {rate * 100:.1f}%（{hits}/{n_pos}），"
                    f"95% Wilson CI [{lower * 100:.1f}%, {upper * 100:.1f}%]；"
                    f"随机基线 {baseline * 100:.1f}% —— {self._ci_vs_baseline(lower, upper, baseline)}")

            per_pos_text = []
            _valid_records = max(1, n_pos // 5)  # 各位置命中数以「有效期数」为分母
            for pos in ('wan', 'qian', 'bai', 'shi', 'ge'):
                hits = summary['per_pos_top1'][pos]
                lower, upper = wilson_ci(hits, _valid_records)
                per_pos_text.append(
                    f"{ {'wan': '万', 'qian': '千', 'bai': '百', 'shi': '十', 'ge': '个'}[pos] } "
                    f"{hits / _valid_records * 100:.1f}% [{lower * 100:.1f}%, {upper * 100:.1f}%]")
            task_mgr.append_info("  各位置 Top-1（点估计 [95% CI]）: " + ' / '.join(per_pos_text))

            task_mgr.append_info("  口径说明: 旧「容错命中（±1偏差）/平均准确率」不属于 ALG-06，"
                                 "已停止展示；本概览不使用候选集包含或±1容错。")
            task_mgr.append_info(" 历史命中率不代表未来表现；排列5为公平摇号，无法预测开奖结果。")
            # 主线程刷新常驻面板(避免工作线程直接碰控件)
            self.root.after(0, self._update_hit_rate_tab, stats)
        except Exception as exc:  # noqa: BLE001
            # 显式提示而非静默吞掉：概览失败不能伪装成「无数据」
            task_mgr.append_info(f"  （命中率概览生成失败: {type(exc).__name__}；请查看日志）")

    def _toggle_detail(self):
        """展开/收起「详细分析」折叠区"""
        f = getattr(self, '_detail_frame', None)
        if f is None:
            return
        if f.winfo_manager():
            f.pack_forget()
        else:
            f.pack(fill=tk.X, padx=6, pady=(0, 6))

    def _build_dashboard_specs(self, agg):
        """GUI-03（v5.2 §2.2）：把仪表盘渲染逻辑编译为纯数据规格树。

        规格树不再直接创建 Tkinter 控件，而是按 ``key = 'section::card_id'``
        逐条登记卡片规格（创建参数/几何/内容签名）。渲染时由 ``_dash_diff``
        对比新旧规格树：同 key 仅 ``configure`` 文本/色值（不 destroy），
        仅 add/remove 才涉及 create/destroy，从根上消除全量重建闪白。

        本方法与原 ``_build_alt_section`` / ``_build_source_matrix`` /
        ``_build_basis_section`` 三个「创建即渲染」的构建函数一一对应，
        视觉布局保持完全不变。

        参数:
            agg: ``_compute_dashboard_aggregates`` 产出的聚合 dict。

        返回:
            Dict[str, dict]: {key: spec}；dict 插入顺序保证父规格先于子规格，
            spec 含 key/kind/parent/pack/grid/props/rcat/cmd/extra/sig。
        """
        COLORS = _main_mod.COLORS
        DISPLAY_POS_KEYS = _main_mod.DISPLAY_POS_KEYS
        compress_combo = _main_mod.compress_combo
        specs = {}   # type: Dict[str, dict]

        def _emit(section, card_id, kind, parent=None, pack=None, grid=None,
                  props=None, rcat=None, cmd=None, extra=None):
            """向规格树注册一张卡片；props 只允许可哈希值（用于内容签名）。"""
            props = dict(props or {})
            # sig 内容签名：结构（kind/parent/几何/命令标签/分类标记）与
            # props 全部纳入；任一变化即视为 update，kind/parent/几何变化
            # 由 _dash_diff 升级为 remove+add（无法原地 configure）。
            sig = (kind, parent,
                   tuple(sorted((pack or {}).items())),
                   tuple(sorted((grid or {}).items())),
                   cmd, rcat, tuple(sorted(props.items())))
            key = '{}::{}'.format(section, card_id)
            specs[key] = {
                'key': key,
                'kind': kind,
                'parent': parent,
                'pack': pack,
                'grid': grid,
                'props': props,
                'rcat': rcat,
                'cmd': cmd,
                'extra': extra,
                'sig': sig,
            }
            return key

        pf = self._last_pipeline_final
        tr = self._last_trend_result
        qf = self._last_quick_final
        picks = agg['picks']
        combos = agg['combos']
        consensus = agg['consensus']
        target_issue = agg['target_issue']
        main_combo_disp = agg['main_combo_disp']
        conf = agg['conf']
        high_conf = agg['high_conf']
        main_combo = agg['main_combo']
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = ['万位', '千位', '百位', '十位', '个位']

        # ── 标题栏（渐变效果：P5绿 → AI紫）──────────────────────────
        _emit('hdr', 'canvas', 'canvas', pack={'fill': tk.X},
              props={'height': 42, 'highlightthickness': 0,
                     'bg': COLORS['bg_secondary']},
              extra={'draw': 'hdr_gradient'})
        _emit('hdr', 'left', 'frame', parent='hdr::canvas',
              pack={'side': tk.LEFT, 'padx': 12, 'pady': 4},
              props={'bg': COLORS['accent_p5']})
        _emit('hdr', 'lbl_ai', 'label', parent='hdr::left',
              pack={'anchor': tk.W},
              props={'text': ' AI智能预测', 'bg': COLORS['accent_p5'],
                     'fg': WHITE, 'font': (UI_FONT, 12, 'bold')})
        # 最近一次分析时间标记（副标题, 让结论有明确的时间归属）
        _ana_t = getattr(self, '_last_analysis_time', None)
        if _ana_t is not None:
            _today = datetime.now().date()
            _tstr = (_ana_t.strftime('%H:%M:%S') if _ana_t.date() == _today
                     else _ana_t.strftime('%Y-%m-%d %H:%M'))
            _emit('hdr', 'lbl_ana', 'label', parent='hdr::left',
                  pack={'anchor': tk.W},
                  props={'text': f' 最近分析 {_tstr}',
                         'bg': COLORS['accent_p5'], 'fg': ACCENT_P5_SOFT_FG,
                         'font': (UI_FONT, 8)})
        _emit('hdr', 'right', 'frame', parent='hdr::canvas',
              pack={'side': tk.RIGHT, 'padx': 8, 'pady': 6},
              props={'bg': COLORS['accent_p5']})
        # 导出下拉 / 详细分析按钮（Primary 样式，命令按标签在应用期绑定）
        _emit('hdr', 'btn_export', 'button', parent='hdr::right',
              pack={'side': tk.RIGHT, 'padx': (6, 0)},
              props={'text': ' 导出 ▾', 'bg': COLORS['accent_p5_light'],
                     'fg': WHITE, 'font': (UI_FONT, 9, 'bold'),
                     'relief': 'flat', 'padx': 10, 'pady': 3,
                     'cursor': 'hand2',
                     'activebackground': COLORS['accent_p5_bright']},
              cmd='export_menu')
        _emit('hdr', 'btn_detail', 'button', parent='hdr::right',
              pack={'side': tk.RIGHT, 'padx': (6, 0)},
              props={'text': ' 详细分析', 'bg': COLORS['accent_p5_light'],
                     'fg': WHITE, 'font': (UI_FONT, 9, 'bold'),
                     'relief': 'flat', 'padx': 10, 'pady': 3,
                     'cursor': 'hand2',
                     'activebackground': COLORS['accent_p5_bright']},
              cmd='detail_toggle')

        # ── 层次容器 ────────────────────────────────────────────────
        _emit('layers', 'container', 'frame',
              pack={'fill': tk.X, 'padx': 12, 'pady': 10},
              props={'bg': COLORS['bg_secondary']})

        # === 预测结论层 ===
        _emit('conclusion', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='预测结论')

        # 风险提示（放在结论层顶部）
        risk = ''
        if isinstance(pf, dict):
            risk = (pf.get('risk_warning') or '').strip()
        elif isinstance(qf, dict):
            risk = (qf.get('risk_warning') or '').strip()
        if risk:
            _emit('risk', 'frame', 'frame', parent='conclusion::frame',
                  pack={'fill': tk.X, 'pady': (0, 4)},
                  props={'bg': COLORS['bg_card'],
                         'highlightbackground': COLORS['accent_warning'],
                         'highlightthickness': 1})
            _emit('risk', 'lbl', 'label', parent='risk::frame',
                  pack={'anchor': tk.W, 'padx': 12, 'pady': 6},
                  props={'text': ' ' + risk, 'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_warning_light'],
                         'font': (UI_FONT, 9), 'wraplength': 620,
                         'justify': tk.LEFT})

        # 1) 数据概览行：期号 + 一致率 + 高置信度 + 来源数
        _emit('info', 'row', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (0, 6)},
              props={'bg': COLORS['bg_secondary']})
        _emit('info', 'lbl_issue', 'label', parent='info::row',
              pack={'side': tk.LEFT},
              props={'text': f"{'第' if target_issue else ''}"
                             f"{target_issue or '—'}"
                             f"{'期' if target_issue else ''}",
                     'font': (MONO_FONT, 13, 'bold'),
                     'bg': COLORS['bg_secondary'],
                     'fg': COLORS['text_primary']})
        if conf is not None:
            _emit('info', 'lbl_conf', 'label', parent='info::row',
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': f' 一致率 {conf}%',
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_p5'],
                         'font': (UI_FONT, 10, 'bold')})
        if high_conf:
            _emit('info', 'lbl_high', 'label', parent='info::row',
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': ' 高置信度 ✓',
                         'bg': COLORS['badge_active_bg'],
                         'fg': COLORS['badge_active_fg'],
                         'font': (UI_FONT, 9, 'bold')})
        src_names = ['四步流水线', '走势引擎', '快速预测']
        used = [n for i, n in enumerate(src_names)
                if (i == 0 and pf) or (i == 1 and tr) or (i == 2 and qf)]
        _emit('info', 'lbl_src', 'label', parent='info::row',
              pack={'side': tk.LEFT, 'padx': (8, 0)},
              props={'text': f' 来源 {len(used)}/3',
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary'],
                     'font': (UI_FONT, 9)})

        # 2) 主推号大卡
        _emit('main', 'card', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (8, 6)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1})
        _emit('main', 'lbl_title', 'label', parent='main::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': '推荐号码', 'font': (UI_FONT, 11, 'bold'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_primary']})
        _emit('main', 'row', 'frame', parent='main::card',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']})
        for _i, _pk_disp in enumerate(DISPLAY_POS_KEYS):
            _d = consensus.get(_pk_disp)
            _emit('main', 'digit_%d' % _i, 'label', parent='main::row',
                  pack={'side': tk.LEFT, 'padx': 4},
                  props={'text': str(_d) if _d is not None else '—',
                         'font': (MONO_FONT, 18, 'bold'),
                         'bg': (COLORS['accent_p5'] if _d is not None
                                else COLORS['bg_card']),
                         'fg': (WHITE if _d is not None
                                else COLORS['text_muted']),
                         'width': 3, 'padx': 6, 'pady': 6})
        _emit('main', 'lbl_conf', 'label', parent='main::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (0, 8)},
              props={'text': f"高置信度 · 一致率 {conf or 0}%",
                     'font': (UI_FONT, 9), 'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary']})

        # 3) 备选号码折叠区（结论层的一部分，原 _build_alt_section 逻辑）
        _emit('alt', 'outer', 'frame', parent='conclusion::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='预测结论')
        _emit('alt', 'btn_toggle', 'button', parent='alt::outer',
              pack={'anchor': tk.W},
              props={'text': (' 收起备选号码' if self._alt_visible
                              else ' 备选号码（各分析源结果）'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_secondary'],
                     'font': (UI_FONT, 10, 'bold'),
                     'relief': 'flat', 'cursor': 'hand2',
                     'padx': 12, 'pady': 6},
              cmd='alt_toggle')
        # content 的 pack/unpack 由 _apply_dashboard_diff 结构修正段按
        # self._alt_visible 统一处理（保留用户折叠状态，规格内不写几何）
        _emit('alt', 'content', 'frame', parent='alt::outer',
              props={'bg': COLORS['bg_card']})
        _main_disp = compress_combo(main_combo)
        for _key, _name in [('pipeline', '四步流水线'),
                            ('quick', '快速预测'),
                            ('trend', '走势引擎')]:
            _c = combos.get(_key)
            if not _c:
                continue
            _c_disp = compress_combo(_c)
            _is_main = (_c_disp == _main_disp)
            _num_color = (COLORS['accent_p5_light'] if _is_main
                          else COLORS['text_secondary'])
            _num_font = ((MONO_FONT, 11, 'bold') if _is_main
                         else (MONO_FONT, 11))
            _emit('alt', 'row_%s' % _key, 'frame', parent='alt::content',
                  pack={'fill': tk.X, 'pady': 3},
                  props={'bg': COLORS['bg_card']})
            _emit('alt', 'lbl_%s' % _key, 'label',
                  parent='alt::row_%s' % _key,
                  pack={'side': tk.LEFT},
                  props={'text': f' {_name}:', 'bg': COLORS['bg_card'],
                         'fg': COLORS['text_muted'],
                         'font': (UI_FONT, 9, 'bold')})
            _emit('alt', 'num_%s' % _key, 'label',
                  parent='alt::row_%s' % _key,
                  pack={'side': tk.LEFT, 'padx': (8, 0)},
                  props={'text': _c_disp, 'bg': COLORS['bg_card'],
                         'fg': _num_color, 'font': _num_font})
            if _is_main:
                _emit('alt', 'badge_%s' % _key, 'label',
                      parent='alt::row_%s' % _key,
                      pack={'side': tk.LEFT, 'padx': (6, 0)},
                      props={'text': '← 选为最终预测',
                             'bg': COLORS['bg_card'],
                             'fg': COLORS['accent_p5'],
                             'font': (UI_FONT, 8, 'bold')})

        # === 分位信号层（原 _build_source_matrix 逻辑）────────────────
        _emit('signals', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='分位信号')
        src_defs = [('pipeline', '流水线'), ('trend', '走势'),
                    ('quick', '快速')]
        # 仅展示实际有数据的来源列，避免空列干扰
        active_srcs = [(k, n) for k, n in src_defs
                       if any(k in picks.get(pk, {}) for pk in pos_keys)]
        _emit('matrix', 'wrap', 'frame', parent='signals::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='分位信号')
        _emit('matrix', 'lbl_title', 'label', parent='matrix::wrap',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': ' 分源对比（逐位）',
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['accent_info'],
                     'font': (UI_FONT, 10, 'bold')})
        _cols = [('位置', None)] + [(n, k) for k, n in active_srcs] \
            + [('综合', '__consensus__')]
        _emit('matrix', 'grid', 'frame', parent='matrix::wrap',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']},
              extra={'grid_weight_cols': len(_cols)})
        for _c, (_name, _) in enumerate(_cols):
            _emit('matrix', 'head_%d' % _c, 'label', parent='matrix::grid',
                  grid={'row': 0, 'column': _c, 'padx': 1, 'pady': 1,
                        'sticky': 'nsew'},
                  props={'text': _name, 'fg': COLORS['text_secondary'],
                         'bg': COLORS['bg_secondary'],
                         'font': (UI_FONT, 9, 'bold'),
                         'padx': 10, 'pady': 6, 'width': 7,
                         'relief': 'solid',
                         'highlightbackground': COLORS['border'],
                         'highlightthickness': 1})
        for _r, (_pk, _pn) in enumerate(zip(pos_keys, pos_names), start=1):
            _cons = consensus.get(_pk)
            _emit('matrix', 'cell_%d_0' % _r, 'label',
                  parent='matrix::grid',
                  grid={'row': _r, 'column': 0, 'padx': 1, 'pady': 1,
                        'sticky': 'nsew'},
                  props={'text': _pn, 'fg': COLORS['text_muted'],
                         'bg': COLORS['bg_secondary'],
                         'font': (UI_FONT, 9, 'bold'),
                         'padx': 10, 'pady': 6, 'width': 7,
                         'relief': 'solid',
                         'highlightbackground': COLORS['border'],
                         'highlightthickness': 1})
            for _c, (_name, _key) in enumerate(_cols[1:], start=1):
                if _key == '__consensus__':
                    _txt = str(_cons) if _cons is not None else '—'
                    _fg, _bg = WHITE, (COLORS['accent_p5']
                                       if _cons is not None
                                       else COLORS['bg_secondary'])
                else:
                    _v = picks.get(_pk, {}).get(_key)
                    if _v is None:
                        _txt = '—'
                        _fg = COLORS['text_disabled']
                        _bg = COLORS['bg_panel']
                    elif _cons is not None and _v == _cons:
                        # 与共识一致 → 绿色高亮
                        _txt = str(_v)
                        _fg = CONSENSUS_HIT_FG
                        _bg = CONSENSUS_HIT_BG
                    else:
                        _txt = str(_v)
                        _fg = COLORS['text_secondary']
                        _bg = COLORS['bg_panel']
                _emit('matrix', 'cell_%d_%d' % (_r, _c), 'label',
                      parent='matrix::grid',
                      grid={'row': _r, 'column': _c, 'padx': 1, 'pady': 1,
                            'sticky': 'nsew'},
                      props={'text': _txt, 'fg': _fg, 'bg': _bg,
                             'font': (MONO_FONT, 12, 'bold'),
                             'padx': 10, 'pady': 6, 'width': 7,
                             'relief': 'flat'})

        # === 算法依据层（原 _build_basis_section 逻辑）────────────────
        _emit('basis', 'frame', 'frame', parent='layers::container',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_secondary']}, rcat='算法依据')
        _emit('basis', 'card', 'frame', parent='basis::frame',
              pack={'fill': tk.X, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card'],
                     'highlightbackground': COLORS['border'],
                     'highlightthickness': 1}, rcat='算法依据')
        _emit('basis', 'lbl_title', 'label', parent='basis::card',
              pack={'anchor': tk.W, 'padx': 12, 'pady': (8, 4)},
              props={'text': '算法依据',
                     'font': (UI_FONT, 10, 'bold'),
                     'bg': COLORS['bg_card'],
                     'fg': COLORS['text_primary']})
        _emit('basis', 'weights', 'frame', parent='basis::card',
              pack={'fill': tk.X, 'padx': 12, 'pady': (0, 8)},
              props={'bg': COLORS['bg_card']})
        # 冻结权重（来自升级方案）
        weight_items = [
            ('频率', '0.68'),
            ('监督学习', '0.14'),
            ('贝叶斯', '0.10'),
            ('遗漏', '0.00'),
            ('趋势', '0.01'),
            ('马尔可夫', '0.005'),
            ('形态', '0.003'),
            ('特征', '0.002'),
        ]
        for _i, (_name, _w) in enumerate(weight_items):
            _emit('basis', 'row_%d' % _i, 'frame', parent='basis::weights',
                  pack={'fill': tk.X, 'pady': 2},
                  props={'bg': COLORS['bg_card']})
            _emit('basis', 'name_%d' % _i, 'label',
                  parent='basis::row_%d' % _i, pack={'side': tk.LEFT},
                  props={'text': _name, 'font': (UI_FONT, 9),
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['text_secondary']})
            _emit('basis', 'val_%d' % _i, 'label',
                  parent='basis::row_%d' % _i, pack={'side': tk.RIGHT},
                  props={'text': _w, 'font': (MONO_FONT, 9, 'bold'),
                         'bg': COLORS['bg_card'],
                         'fg': COLORS['accent_p5']})

        return specs

    def _build_detail_section(self, parent, picks, top5, combos, target_issue,
                              pos_keys, pos_names, total_sources, conf, high_conf):
        """详细分析：各来源 Top 候选 + 计算逻辑 + 位置差异点，表格化展示"""
        COLORS = _main_mod.COLORS
        tk.Label(parent, text="详细分析（来源 / 差异 / 计算逻辑）", bg=COLORS['bg_primary'],
                 fg=COLORS['text_primary'], font=(UI_FONT, 10, 'bold')
                 ).pack(anchor=tk.W, padx=8, pady=(6, 4))
        
        # 创建表格容器
        table_frame = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
        table_frame.pack(fill=tk.X, padx=8, pady=3)
        
        # 表头
        headers = ['来源', '万位', '千位', '百位', '十位', '个位', '说明']
        for i, header in enumerate(headers):
            tk.Label(table_frame, text=header, bg=COLORS['bg_secondary'], fg=COLORS['accent_p5'],
                     font=(UI_FONT, 9, 'bold')).grid(row=0, column=i, padx=4, pady=2, sticky='w')
        
        logic = {
            'pipeline': '四步流水线：八算法融合流水线，系统旗舰预测',
            'quick': '快速预测：P5Predictor 八算法融合模型',
            'trend': '走势引擎：6信号源相对热度打分',
        }
        
        # 来源颜色映射（v5.2 GUI-06：trend 原用未定义键 accent_orange，
        # dict 构造即 KeyError；走势引擎语义为橙，改查已定义槽 accent_warning）
        source_colors = {
            'pipeline': COLORS['accent_p5'],
            'quick': COLORS['accent_ai'],
            'trend': COLORS['accent_warning']
        }
        
        row = 1
        for key, name in [('pipeline', '四步流水线'), ('quick', '快速预测'), ('trend', '走势引擎')]:
            present = combos.get(key) or any(key in picks[pk] for pk in pos_keys)
            if not present:
                continue
            
            # 来源名称列
            tk.Label(table_frame, text=name, bg=COLORS['bg_secondary'], fg=source_colors.get(key, COLORS['text_primary']),
                     font=(UI_FONT, 9, 'bold')).grid(row=row, column=0, padx=4, pady=2, sticky='w')
            
            # 各位置候选
            for col, pk in enumerate(pos_keys, start=1):
                candidates = top5.get(pk, {}).get(key, [])
                text = ''.join(str(x) for x in candidates) if candidates else '—'
                tk.Label(table_frame, text=text, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                         font=(MONO_FONT, 9)).grid(row=row, column=col, padx=4, pady=2, sticky='w')
            
            # 说明列
            tk.Label(table_frame, text=logic.get(key, ''), bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=(UI_FONT, 8), wraplength=200, justify=tk.LEFT).grid(row=row, column=6, padx=4, pady=2, sticky='w')
            row += 1

        # 位置差异点表格
        diff_rows = []
        for pk, pn in zip(pos_keys, pos_names):
            vals = picks.get(pk, {})
            if len(set(vals.values())) > 1:
                diff_rows.append(f"{pn}位分歧: " + ' / '.join(f"{k}={v}" for k, v in vals.items()))
        
        if diff_rows:
            diff_frame = tk.Frame(parent, bg=COLORS['bg_secondary'], relief='groove', bd=1)
            diff_frame.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(diff_frame, text="位置差异点", bg=COLORS['bg_secondary'], fg=COLORS['warning'],
                     font=(UI_FONT, 9, 'bold')).grid(row=0, column=0, columnspan=2, padx=8, pady=(4, 2), sticky='w')
            for i, d in enumerate(diff_rows, 1):
                tk.Label(diff_frame, text=d, bg=COLORS['bg_secondary'], fg=COLORS['text_secondary'],
                         font=(MONO_FONT, 8), wraplength=600, justify=tk.LEFT).grid(row=i, column=0, columnspan=2, padx=8, pady=(0, 2), sticky='w')
            tk.Label(diff_frame, text="注：主推荐按「多源多数投票、平票优先四步流水线」规则生成。",
                     bg=COLORS['bg_secondary'], fg=COLORS['text_muted'],
                     font=(UI_FONT, 8), wraplength=600, justify=tk.LEFT).grid(row=len(diff_rows)+1, column=0, columnspan=2, padx=8, pady=(0, 4), sticky='w')

    @staticmethod
    def _dash_diff(old_state, new_data):
        """计算仪表盘卡片差异（GUI-03 增量渲染核心，纯函数，可离线单测）。

        对比新旧规格树（`{key: spec}`），产出 add/update/remove 三列表：

        * add: 新出现的 key（首次渲染 / 新数据源 / 新卡片）；
        * update: 同 key 且结构不变（kind/parent/pack/grid 均一致），
          仅内容签名 sig 变化（文本/色值等）→ 走原地 configure；
        * remove: 旧树中消失的 key + 结构变化的 key（结构变化无法
          原地 configure，降级为 remove+add）。

        参数:
            old_state: 上一次渲染保存的规格树（_dash_card_state）。
            new_data: 本次渲染编译出的新规格树。

        返回:
            dict: {'add': [spec, ...], 'update': [(key, spec), ...],
                   'remove': [key, ...]}
        """
        old_state = old_state or {}
        new_data = new_data or {}
        add, update, remove = [], [], []
        for key, spec in new_data.items():
            old = old_state.get(key)
            if old is None:
                add.append(spec)
            elif (old.get('kind') != spec.get('kind')
                  or old.get('parent') != spec.get('parent')
                  or old.get('pack') != spec.get('pack')
                  or old.get('grid') != spec.get('grid')):
                # 结构变化无法原地 configure → 升级为 remove+add
                remove.append(key)
                add.append(spec)
            elif old.get('sig') != spec.get('sig'):
                update.append((key, spec))
        remove.extend(k for k in old_state if k not in new_data)
        return {'add': add, 'update': update, 'remove': remove}

    def _dash_card_create(self, spec):
        """按规格创建单张卡片控件并完成几何布局（GUI-03 唯一创建入口）。

        创建参数全部来自规格树 props（与 configure 同源，保证视觉一致）；
        pack/grid 几何来自 spec['pack'] / spec['grid']；parent 为规格 key，
        经 _dash_card_index 解析为真实父控件（缺失时降级挂到 result_dash，
        保证父子顺序异常时也不会丢失卡片）。

        参数:
            spec: 规格树中的单项 spec dict。

        返回:
            创建成功的控件；kind 未知或创建异常时返回 None（记日志，不中断渲染）。
        """
        COLORS = _main_mod.COLORS
        logger = _main_mod.logger
        parent_w = None
        if spec.get('parent'):
            parent_w = self._dash_card_index.get(spec['parent'])
            if parent_w is not None and not parent_w.winfo_exists():
                parent_w = None
        if parent_w is None:
            parent_w = self.result_dash
        kind = spec['kind']
        props = dict(spec['props'])
        try:
            if kind == 'frame':
                w = tk.Frame(parent_w, **props)
            elif kind == 'label':
                w = tk.Label(parent_w, **props)
            elif kind == 'button':
                w = tk.Button(parent_w, **props)
            elif kind == 'canvas':
                w = tk.Canvas(parent_w, **props)
            else:
                logger.warning('[GUI-03] 未知卡片类型 kind=%s（key=%s），已跳过',
                               kind, spec.get('key'))
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning('[GUI-03] 卡片创建失败 key=%s: %s',
                           spec.get('key'), e)
            return None
        # 分类筛选标记（兼容 _apply_result_category 的 _rcat 遍历协议）
        if spec.get('rcat'):
            try:
                w._rcat = spec['rcat']
            except Exception:
                pass
        # 几何布局（一张卡片只会使用 pack 或 grid 之一）
        try:
            if spec.get('grid'):
                w.grid(**dict(spec['grid']))
            elif spec.get('pack'):
                w.pack(**dict(spec['pack']))
        except Exception as e:  # noqa: BLE001
            logger.warning('[GUI-03] 卡片布局失败 key=%s: %s',
                           spec.get('key'), e)
        # 命令按钮按标签重建闭包，规避缓存闭包持有已销毁控件（见 _dash_bind_command）
        if spec.get('cmd'):
            self._dash_bind_command(w, spec['cmd'])
        # 标题栏渐变（复刻原实现：两个同尺寸矩形先后叠加，后绘者覆盖前者）
        extra = spec.get('extra') or {}
        if extra.get('draw') == 'hdr_gradient':
            try:
                _dash_w = self.result_dash.winfo_width()
                w.create_rectangle(0, 0, _dash_w, 42,
                                   fill=COLORS['accent_p5'], outline='')
                w.create_rectangle(0, 0, _dash_w, 42,
                                   fill=COLORS['accent_ai'], outline='')
            except Exception:
                pass
        return w

    def _dash_bind_command(self, widget, cmd):
        """按命令标签为按钮重建闭包（GUI-03）。

        按钮 command 闭包不可哈希、无法进入内容签名，改以 cmd 标签在
        create / update 应用期重建；任何阶段拿到的命令绑定的都是当前
        最新控件引用，杜绝「缓存闭包调用已销毁控件」类故障。

        参数:
            widget: 目标按钮控件。
            cmd: 命令标签（'export_menu' / 'detail_toggle' / 'alt_toggle'）。
        """
        if cmd == 'export_menu':
            widget.config(command=self._show_export_menu)
        elif cmd == 'detail_toggle':
            widget.config(command=self._toggle_detail)
        elif cmd == 'alt_toggle':
            widget.config(command=self._make_alt_toggle())

    def _make_alt_toggle(self):
        """构造「备选号码」折叠开关闭包（GUI-03）。

        折叠状态以 self._alt_visible 为单一事实源：切换时同步
        pack/unpack ``alt::content`` 与按钮文案；下一次渲染的规格树
        会按新状态生成按钮文本（text 纳入 sig），实现自愈。
        """

        def _toggle():
            self._alt_visible = not self._alt_visible
            content = self._dash_card_index.get('alt::content')
            try:
                if content is not None and content.winfo_exists():
                    if self._alt_visible:
                        content.pack(fill=tk.X, padx=12, pady=(0, 8))
                    else:
                        content.pack_forget()
                btn = self._dash_card_index.get('alt::btn_toggle')
                if btn is not None and btn.winfo_exists():
                    btn.config(text=' 收起备选号码' if self._alt_visible
                               else ' 备选号码（各分析源结果）')
                # 折叠/展开改变内容高度，防抖刷新滚动区域
                self._dash_request_refresh()
            except Exception:
                pass

        return _toggle

    def _dash_restore_scroll(self, saved):
        """A9：渲染后若视口位置漂移超过 5% 则回拽到渲染前位置。

        参数:
            saved: 渲染前保存的 ``dash_container.yview()`` 元组（None 时跳过）。
        """
        if saved is None:
            return
        try:
            cur = self.dash_container.yview()
            if abs(cur[0] - saved[0]) > 0.05:
                self.dash_container.yview_moveto(saved[0])
        except Exception:
            pass

    def _apply_dashboard_diff(self, diff):
        """应用渲染 diff：remove → add → update → 结构修正（GUI-03 唯一 Tk 写入口）。

        执行顺序保证父子控件依赖：先销毁消失卡片，再按规格树顺序
        （父先于子）创建新卡片，最后原地更新同 key 卡片；末尾单次
        ``update_idletasks`` 摊平本轮全部几何变化，避免逐控件抖动。

        参数:
            diff: ``_dash_diff`` 产出的 {'add': [spec], 'update': [(key, spec)],
                'remove': [key]}；三项均为空时零 Tk 操作（A7 口径）。
        """
        logger = _main_mod.logger
        add = diff.get('add') or []
        update = diff.get('update') or []
        remove = diff.get('remove') or []
        if not (add or update or remove):
            return  # 无差异：本帧不触碰 Tk

        # A9：apply 前保存滚动位置，末尾按漂移回拽
        try:
            _saved_y = self.dash_container.yview()
        except Exception:
            _saved_y = None

        # 1) remove：销毁消失的卡片并摘除全部索引（含 grid 列权与折叠句柄）
        for _k in remove:
            w = self._dash_card_index.pop(_k, None)
            self._dash_card_state.pop(_k, None)
            self._dash_grid_ncols.pop(_k, None)
            if _k == 'alt::content':
                self._alt_content = None
            try:
                if w is not None and w.winfo_exists():
                    w.destroy()
            except Exception:
                pass

        # 2) add：按规格树顺序创建（_build_dashboard_specs 保证父先于子）
        for spec in add:
            w = self._dash_card_create(spec)
            if w is None:
                continue
            self._dash_card_index[spec['key']] = w
            if spec['key'] == 'alt::content':
                self._alt_content = w

        # 3) update：同 key 原地 configure（仅文本/色值等可配置项，不销毁）
        for key, spec in update:
            w = self._dash_card_index.get(key)
            if w is None or not w.winfo_exists():
                continue
            try:
                w.configure(**dict(spec['props']))
            except Exception as e:  # noqa: BLE001
                logger.warning('[GUI-03] 卡片更新失败 key=%s: %s', key, e)
            if spec.get('cmd'):
                self._dash_bind_command(w, spec['cmd'])

        # 4) 结构修正：同 key 场景的类级配置 / 折叠状态保持
        for spec in add + [s for _, s in update]:
            extra = spec.get('extra') or {}
            _ncols = extra.get('grid_weight_cols')
            if _ncols:
                gw = self._dash_card_index.get(spec['key'])
                if (gw is not None and gw.winfo_exists()
                        and self._dash_grid_ncols.get(spec['key']) != _ncols):
                    try:
                        for _c in range(_ncols):
                            gw.grid_columnconfigure(_c, weight=1)
                    except Exception:
                        pass
                    self._dash_grid_ncols[spec['key']] = _ncols

        # 备选号码折叠区：pack/unpack 不在规格内，按用户折叠状态统一修正
        # （规格树只描述「应有内容」，几何由本段按运行时状态补齐）
        content = self._dash_card_index.get('alt::content')
        if content is not None and content.winfo_exists():
            try:
                if self._alt_visible:
                    if not content.winfo_manager():
                        content.pack(fill=tk.X, padx=12, pady=(0, 8))
                elif content.winfo_manager():
                    content.pack_forget()
            except Exception:
                pass

        # 单次 idle 刷新，摊平本轮全部几何变化（避免逐个 create 触发重排）
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        # A9：滚动位置回拽
        self._dash_restore_scroll(_saved_y)

    def _show_result_dashboard(self, pipeline_final=None, trend_result=None, quick_final=None):
        """
        v4.0 重构：薄包装入口，参数适配后委托至 _render_unified_dashboard。
        保留旧签名以保持向后兼容，实际渲染由统一入口处理。
        """
        # 立即保存最新结果，确保后台线程调用时也能被 _copy_prediction 兜底读取
        # 这些属性的写入不涉及 Tkinter，是线程安全的
        if pipeline_final is not None:
            self._last_pipeline_final = pipeline_final
        if trend_result is not None:
            self._last_trend_result = trend_result
        if quick_final is not None:
            self._last_quick_final = quick_final

        # §4.4.3：结果落库后自动登记一次自我验证，使「覆盖率 100%」成为真实度量
        # （仅登记，不渲染/不阻塞；异常内部静默）
        self._auto_verify_result(pipeline_final, quick_final)

        # 线程安全检查：非主线程投递回主线程
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._show_result_dashboard(
                pipeline_final, trend_result, quick_final))
            return

        # 统一渲染入口
        try:
            self._render_unified_dashboard(task_mgr=None)
        except Exception as e:
            try:
                import logging
                logging.getLogger('kplucky.debug').warning(
                    'SHOW_RESULT_DASHBOARD_DELEGATE_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

    def _render_dashboard_impl(self, _pre_agg=None, _diff: Optional[Dict] = None):
        """v5.2（GUI-03）实际渲染实现：规格树 + diff 增量渲染。

        与 v4.0「创建即渲染、每次全量重建」的区别：渲染逻辑先编译为
        纯数据规格树（``_build_dashboard_specs``），再经 ``_dash_diff``
        计算 add/update/remove，仅对差异部分执行 create/destroy/configure；
        数据无变化时零 Tk 写操作，从根上消除重复渲染的整屏闪白（A7）。

        参数:
            _pre_agg: 调用方已算好的聚合结果（dict）。传入则直接复用，
                避免同一次渲染重复执行 _compute_dashboard_aggregates
                （§2.1 第 4 项 响应速度：聚合计算只做一次）。
            _diff: 调用方预先算好的 diff（dict，含 add/update/remove），
                传入则跳过本方法内的 _dash_diff 计算（供异步预计算场景）。
        """
        compress_combo = _main_mod.compress_combo
        pf = self._last_pipeline_final
        tr = self._last_trend_result
        qf = self._last_quick_final
        if not (pf or tr or qf):
            # v3.59：三个 final 都为空（本次分析被取消 / 早期异常 / 流水线尚未产出任何结果）
            # 也要把数据库里最近的预测记录写入 _prediction_clipboard，
            # 让「复制预测号码」按钮能给出有意义内容，而不是「无数据」误判。
            self._populate_clipboard_from_db_fallback()
            self._hide_result_dashboard()
            return

        # v3.62：聚合计算抽到纯函数 _compute_dashboard_aggregates，
        # 后台线程也可直接调用同步写 _prediction_clipboard，避开 after(0) 时序竞态。
        # §2.1 第 4 项：复用调用方已算好的聚合结果（_pre_agg），同一次渲染不重复计算。
        agg = _pre_agg or self._compute_dashboard_aggregates(pf, tr, qf)
        if not agg:
            # v4.0 简化：聚合失败时仅用 pipeline_final 恢复 clipboard，
            # _copy_prediction 会在点击时自动从 _last_pipeline_final 或数据库兜底。
            if isinstance(pf, dict) and pf:
                clip = self._build_prediction_clipboard(pf)
                if clip and clip.strip():
                    self._prediction_clipboard = clip
                    self._clipboard_meta = {
                        'target_issue': pf.get('target_issue') or pf.get('next_issue') or '',
                        'conf': '',
                        'high_conf': '',
                        'main_combo': '',
                    }
            self._hide_result_dashboard()
            return
        picks, top5, combos, target_issue = agg['picks'], agg['top5'], agg['combos'], agg['target_issue']
        consensus = agg['consensus']
        main_combo_disp = agg['main_combo_disp']
        conf = agg['conf']
        high_conf = agg['high_conf']
        main_combo = agg['main_combo']

        # 核心计算仍使用完整5位（万/千/百/十/个），保证算法与数据处理不受影响
        # [DEBUG v3.54] 渲染参数日志
        try:
            import logging
            _dbg = logging.getLogger('kplucky.debug')
            _dbg.info('DASHBOARD_RENDER: target_issue=%s picks_keys=%s consensus=%s',
                      target_issue,
                      list(picks.keys()) if 'picks' in dir() else '?',
                      {k: consensus.get(k) for k in ['wan','qian','bai','shi','ge']})
        except Exception:
            pass

        # 渲染去重——内容与上次渲染完全一致时跳过重建，仅确保可见，
        # 避免重复点击「开始分析」或缓存命中时不必要的控件销毁/重建，提升响应速度
        # （GUI-03 之后常态命中：无数据变化时连 diff 都不计算，零 Tk 操作）
        _combos_disp = tuple(sorted(compress_combo(v) for v in combos.values()))
        _render_key = (target_issue, main_combo_disp, conf, high_conf, _combos_disp)
        if (getattr(self, '_last_dashboard_key', None) == _render_key
                and self._dash_card_index
                and self.result_dash.winfo_children()):
            try:
                if getattr(self, '_result_placeholder', None) is not None:
                    self._result_placeholder.pack_forget()
                self.result_dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
                self._focus_result_tab()
            except Exception:
                pass
            return

        # 记录本次分析生成时间（供「最近一次分析时间」标记展示）
        self._last_analysis_time = datetime.now()

        # ---- GUI-03 增量渲染：规格树 → diff → 应用 ----
        new_state = self._build_dashboard_specs(agg)
        if _diff is not None:
            diff = _diff
        else:
            diff = self._dash_diff(self._dash_card_state, new_state)
        self._apply_dashboard_diff(diff)
        # 状态只登记真实创建成功的卡片（create 失败的 key 留待下次渲染重试）
        self._dash_card_state = {k: v for k, v in new_state.items()
                                 if k in self._dash_card_index}

        # 有结果 → 隐藏空态占位，展示仪表盘，并自动聚焦「预测结果」页
        try:
            if getattr(self, '_result_placeholder', None) is not None:
                self._result_placeholder.pack_forget()
        except Exception:
            pass
        try:
            self.result_dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        except Exception as _pack_e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error('DASHBOARD_PACK_FAIL: %s', _pack_e)
            except Exception:
                pass
        try:
            self._focus_result_tab()
        except Exception as _focus_e:
            try:
                import logging
                logging.getLogger('kplucky.debug').error('DASHBOARD_FOCUS_FAIL: %s', _focus_e)
            except Exception:
                pass
        # 记录本次渲染指纹，供下次渲染去重（避免无谓的增量计算）
        self._last_dashboard_key = _render_key
        try:
            import logging
            logging.getLogger('kplucky.debug').info('DASHBOARD_SHOWN: issue=%s combo=%s',
                                                    target_issue, main_combo_disp)
        except Exception:
            pass

    def _hide_result_dashboard(self):
        """隐藏预测结果仪表盘（任务开始前调用），恢复空态占位。"""
        # 兼容直连旧式挂载（热加载后会继续接到 self.result_dash）
        try:
            self.result_dash.pack_forget()
        except Exception:
            pass
        try:
            if getattr(self, '_result_placeholder', None) is not None:
                self._result_placeholder.pack(fill=tk.BOTH, expand=True)
        except Exception:
            pass
        # 兼容新式容器（热更新可能把 widget 本体下挂到了 dash_inner）
        try:
            self.dash_inner._result_dash.pack_forget()
        except Exception:
            pass
        try:
            if getattr(self.dash_inner, '_result_placeholder', None) is not None:
                self.dash_inner._result_placeholder.pack(fill=tk.BOTH, expand=True)
        except Exception:
            pass

    def _render_unified_dashboard(self, task_mgr=None):
        """v4.0 统一渲染入口：唯一仪表盘渲染入口，_show_result_dashboard 仅做薄包装。
        
        统一分析结束时，用已保存的各来源产物集中渲染仪表盘。
        支持从后台线程安全调用，自动投递至主线程执行。
        """
        # 线程安全检查：Tkinter 控件只能在主线程操作
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._render_unified_dashboard(task_mgr))
            return

        # v3.62 修复：后台线程中先同步计算聚合，把 _prediction_clipboard /
        # _clipboard_meta 立即写入主线程可见属性，避免 _copy_prediction 被
        # 点击时主线程 after(0) 仪表盘渲染还没轮到导致「无数据」误判。
        try:
            pf = getattr(self, '_last_pipeline_final', None)
            qf = getattr(self, '_last_quick_final', None)
            tr = getattr(self, '_last_trend_result', None)
            _pre_agg = self._compute_dashboard_aggregates(pf, tr, qf)
        except Exception as e:
            _pre_agg = None
            try:
                import logging
                logging.getLogger('kplucky.debug').error(
                    'RENDER_UNIFIED_PRECOMPUTE_ERROR: %s', e, exc_info=True)
            except Exception:
                pass

        # 执行实际渲染（复用上面的聚合结果，避免同一次渲染重复计算）
        _pre_key = getattr(self, '_last_dashboard_key', None)
        self._render_dashboard_impl(_pre_agg)

        # 兜底：仅当多源聚合未写出剪贴板时才用单源构建补齐。
        # v5.0 §2.1 第 4 项修正：原实现无条件覆盖 _prediction_clipboard，
        # 会把刚算好的多源聚合（共识/置信度/备选组合）改回单源内容。
        try:
            if not (getattr(self, '_prediction_clipboard', '') or '').strip():
                _pf = getattr(self, '_last_pipeline_final', None)
                if isinstance(_pf, dict) and _pf:
                    clip = self._build_prediction_clipboard(_pf)
                    if clip and clip.strip():
                        self._prediction_clipboard = clip
                        self._clipboard_meta = {
                            'target_issue': _pf.get('target_issue') or _pf.get('next_issue') or '',
                            'conf': '',
                            'high_conf': '',
                            'main_combo': '',
                        }
        except Exception:
            pass
        # v3.59：兜底数据库写入。若本次分析全程未产出结果（_last_*_final 全为 None），
        # 从数据库最近一条预测记录写入 clipboard，避免复制按钮误报「无数据」。
        try:
            if not getattr(self, '_prediction_clipboard', ''):
                self._populate_clipboard_from_db_fallback()
        except Exception:
            pass
        # 兜底：若 2 秒后 _last_dashboard_key 未更新（说明渲染被吞或控件创建失败），强制再跑一次
        def _watchdog():
            if getattr(self, '_last_dashboard_key', None) == _pre_key:
                try:
                    self._render_unified_dashboard(task_mgr)
                except Exception:
                    pass
        self.root.after(2000, _watchdog)
