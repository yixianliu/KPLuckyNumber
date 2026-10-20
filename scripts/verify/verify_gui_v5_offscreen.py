# -*- coding: utf-8 -*-
"""离屏 e2e 守卫：GUI v5 综合验收（Phase 4 整合套件）。

覆盖 Phase 4 验收标准 + GUI-07 响应式验收（方案 §308-318）：
  1. 三标签页（预测结果 / 命令行输出 / 自我进化）存在且可构建，无异常。
  2. 「命令行输出」交互增强：关键字过滤 + 右键复制 + 内存缓冲（复用既有范式，
     完整断言见 verify_cli_log_tab_offscreen.py，此处做结构性 + 一轮过滤冒烟）。
  3. 明暗主题切换无需重启即全控件刷新（无旧主题色残留）—— 复用 verify_ui_offscreen
     的 C 段轻量契约（根窗口 + token 按钮跟随）。
  4. A16（方案 §317）：三档窗口尺寸（1024×720 / 1366×768 / 1920×1080）下
     标签页容器不溢出窗口，且断点档位/磁贴布局模式与面板宽度自洽。
  5. A17（方案 §318）：窄屏 1000×700（被 minsize 钳为 1024×720）下所有
     工具栏按钮 hit area ≥24×24 px 且互不重叠，紧凑档短文案生效，
     概览磁贴呈 2×2 网格落位。

设计要点：
  * offscreen 构造 GUI（withdraw），不依赖显示设备；
  * 备份 / 还原 data/theme_preference.json——_toggle_theme 会写回真实偏好；
  * 不触发真实分析任务（避免分钟级等待与 DB 副作用）；
  * 结束强制 os._exit(0)，避免 GUI 后台非守护线程（self_evolution / perf_monitor /
    task_manager）挂起进程。

用法:
    python scripts/verify/verify_gui_v5_offscreen.py      # 退出码 0 = 全部通过
"""
import os
import sys
import shutil

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gui_v5_test_home')
os.environ['HOME'] = _HOME
os.environ['USERPROFILE'] = _HOME
os.makedirs(_HOME, exist_ok=True)

# 主题偏好落在 PROJECT_ROOT/data 下，_toggle_theme 会写回；守卫备份/还原。
_PREF_FILE = os.path.join(_ROOT, 'data', 'theme_preference.json')
_PREF_BACKUP = _PREF_FILE + '.gui_v5_verify_bak'


def _backup_theme_pref():
    try:
        if os.path.exists(_PREF_FILE) and not os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_FILE, _PREF_BACKUP)
    except Exception:
        pass


def _restore_theme_pref():
    try:
        if os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_BACKUP, _PREF_FILE)
            os.remove(_PREF_BACKUP)
    except Exception:
        pass


import tkinter as tk  # noqa: E402

import main as m  # noqa: E402
import modules.cli_log_sink as sink  # noqa: E402


_PASSED = []


def check(cond, desc):
    assert cond, f'FAIL: {desc}'
    _PASSED.append(desc)
    print(f'[PASS] {desc}')


def _tab_texts(nb):
    try:
        return [nb.tab(t, 'text') for t in nb.tabs()]
    except Exception:
        return []


def run():
    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    root.update_idletasks()

    # ---------------- 1. 三标签页结构 ----------------
    nb = getattr(gui, 'output_nb', None)
    check(nb is not None, 'T1 输出面板 Notebook 已创建')
    texts = [t.strip() for t in _tab_texts(nb)]
    for expect in ('预测结果', '命令行输出', '自我进化'):
        check(any(expect in t for t in texts),
              f'T2 标签页「{expect}」存在（实际标签: {texts}）')

    # 各标签页子控件已构建（非空，证明 _build_*_tab 跑通无异常）
    check(gui.cli_log_text is not None, 'T3 命令行输出文本框已构建')
    check(hasattr(gui, 'evo_log') and gui.evo_log is not None, 'T4 自我进化日志框已构建')
    check(hasattr(gui, '_render_result_tab') or hasattr(gui, '_build_result_tab'),
          'T5 预测结果标签页构建入口存在')

    # ---------------- 2. 命令行输出交互增强（结构 + 一轮过滤冒烟） ----------------
    check(getattr(gui, '_cli_filter_var', None) is not None, 'C1 关键字过滤变量已初始化')
    check(isinstance(getattr(gui, '_cli_log_buffer', None), list), 'C2 内存缓冲已初始化')
    check(callable(getattr(gui, '_cli_log_context_menu', None)), 'C3 右键菜单方法存在')
    check(callable(getattr(gui, '_cli_copy', None)), 'C4 复制方法存在')
    check(callable(getattr(gui, '_apply_cli_filter', None)), 'C5 过滤重建方法存在')

    # 一轮过滤冒烟（完整断言见 verify_cli_log_tab_offscreen.py）
    before = len(gui._cli_log_buffer)
    sink._CLI_QUEUE.put(('info', 'v5 smoke normal line'))
    sink._CLI_QUEUE.put(('error', 'v5 smoke ERROR line'))
    gui._poll_cli_log_cancelled = False
    gui._poll_cli_log()
    if gui._poll_cli_log_handle is not None:
        try:
            root.after_cancel(gui._poll_cli_log_handle)
        except Exception:
            pass
        gui._poll_cli_log_handle = None
    gui._poll_cli_log_cancelled = True
    buf_lines = [ln for _, ln in gui._cli_log_buffer]
    check('v5 smoke normal line' in buf_lines and 'v5 smoke ERROR line' in buf_lines,
          'C6 注入日志进入缓冲（内容匹配）')

    gui._cli_filter_var.set('error')
    gui._apply_cli_filter()
    filtered = gui.cli_log_text.get('1.0', tk.END)
    check('v5 smoke ERROR line' in filtered, 'C7 过滤命中 error 行')
    check('v5 smoke normal line' not in filtered, 'C8 过滤排除非匹配行')
    gui._cli_filter_var.set('')
    gui._apply_cli_filter()
    check('v5 smoke normal line' in gui.cli_log_text.get('1.0', tk.END), 'C9 清除过滤恢复全部')

    # ---------------- 3. 明暗主题切换无需重启 ----------------
    start_theme = gui._current_theme
    check(start_theme in ('dark', 'light'), f'TH1 初始主题合法: {start_theme}')
    root_bg_before = str(gui.root.cget('bg'))
    bg_card_before = m.COLORS['bg_card']

    gui._toggle_theme()
    root.update_idletasks()
    check(gui._current_theme != start_theme, 'TH2 切换后主题标记变化（无需重启）')
    check(str(gui.root.cget('bg')) != root_bg_before, 'TH3 根窗口 bg 跟随新主题')
    check(m.COLORS['bg_card'] != bg_card_before, 'TH4 COLORS.bg_card 已更新')

    token_btns = [b for b in (gui._buttons or [])
                  if isinstance(getattr(b, '_normal_bg', None), str)
                  and b._normal_bg in m.COLORS]
    check(len(token_btns) > 0, 'TH5 存在 token 按钮用于校验跟随')
    check(all(str(b.cget('bg')) == m.COLORS[b._normal_bg] for b in token_btns),
          f'TH6 切换后 {len(token_btns)} 个 token 按钮 bg 跟随新主题')
    check(not any(str(b.cget('bg')) == bg_card_before for b in token_btns),
          'TH7 切换后无按钮残留旧主题 bg_card')

    gui._toggle_theme()  # 切回，避免污染偏好文件的最终落盘态
    root.update_idletasks()
    check(gui._current_theme == start_theme, 'TH8 再次切换回到初始主题')

    # ---------------- 4. A16：三档窗口尺寸不溢出 + 响应式档位自洽 ----------------
    # withdraw 态下子控件几何不计算（winfo_width=1，伪命题），故先 deiconify 触发
    # 真实布局，再设置最小尺寸并强制 update，方可测得真实容器尺寸。
    try:
        root.deiconify()
    except Exception:
        pass
    # A16 三档验收尺寸（方案 §317）。断点以「输出面板宽度」分档而非窗口宽度：
    # 左侧导航常驻约 270px，三尺寸下面板实测约 712 / 1054 / 1608px，
    # 恰好落入 compact / standard / wide 三档（离屏探针实测）。
    profiles = (
        ('1024x720', 'compact'),
        ('1366x768', 'standard'),
        ('1920x1080', 'wide'),
    )
    for idx, (geom, expect_bp) in enumerate(profiles, start=1):
        root.geometry(geom)
        root.update_idletasks()
        root.update()
        win_w = root.winfo_width()
        win_h = root.winfo_height()
        nb_w = nb.winfo_width()
        nb_h = nb.winfo_height()
        tag = geom.replace('x', '×')
        # 容器级不溢出：标签页容器宽度/高度均 <= 窗口（pack fill+expand 结构性保证）
        check(nb_w <= win_w + 2 and nb_w > 1,
              f'L1-{idx} 标签页容器宽度不溢出窗口（{tag}，nb={nb_w} <= win={win_w}）')
        check(nb_h <= win_h + 2 and nb_h > 1,
              f'L2-{idx} 标签页容器高度不溢出窗口（{tag}，nb={nb_h} <= win={win_h}）')
        # 响应式档位与面板宽度自洽 + 期望档位映射（GUI-07 三档）
        bp = getattr(gui, '_active_breakpoint', None)
        check(bp == m.LotteryGUI._classify_breakpoint(nb_w),
              f'R1-{idx} 断点档位与面板宽度自洽（{tag}，bp={bp}）')
        check(bp == expect_bp,
              f'R2-{idx} {tag} 落入「{expect_bp}」档（面板 {nb_w}px，bp={bp}）')
        # 磁贴布局模式与容器宽度阈值自洽
        cont = getattr(gui, '_ov_container', None)
        cont_w = cont.winfo_width() if cont is not None else 0
        expect_mode = 'grid' if cont_w < m._TILE_GRID_MAX_W else 'row'
        mode = getattr(gui, '_active_tile_mode', None)
        check(mode == expect_mode,
              f'R3-{idx} 磁贴布局模式与容器宽度自洽（{tag}，容器 {cont_w}px，mode={mode}）')
        # 1024×720 为 root.minsize 下限，请求须被完整接受（仅首档）
        if idx == 1:
            check(win_w >= 1000 and win_h >= 700,
                  f'L3 窗口接受 1024×720 最小尺寸请求（实际 {win_w}x{win_h}）')

    # ---------------- 5. A17：窄屏（1000×700 请求 → minsize 钳为 1024×720） ----------------
    # 方案 §318：窄屏下所有工具栏按钮 hit area ≥24×24 px 且互不重叠；
    # 紧凑档短文案生效，概览磁贴呈 2×2 网格。
    root.geometry('1000x700')
    root.update_idletasks()
    root.update()
    win_w2 = root.winfo_width()
    win_h2 = root.winfo_height()
    check(win_w2 >= 1000 and win_h2 >= 700,
          f'L4 窗口接受 1000×700 请求（被 minsize 钳为 {win_w2}x{win_h2}）')

    check(getattr(gui, '_active_breakpoint', None) == 'compact',
          'A17a 窄屏落入紧凑档（短文案模式）')
    registry = getattr(gui, '_bp_toolbar_texts', None) or {}
    btns = list(registry.keys())
    check(len(btns) >= 4, f'A17b 工具栏登记按钮数 = {len(btns)}（≥4）')
    # 紧凑档：每个登记按钮均切换为短文案
    for i, (btn, (_full, short)) in enumerate(registry.items(), start=1):
        check(btn.cget('text') == short,
              f'A17c-{i} 紧凑档短文案生效（实际: {btn.cget("text")!r}）')

    # hit area：屏幕绝对坐标矩形，全部 ≥24×24 px
    rects = []
    for b in btns:
        b.update_idletasks()
        rects.append((b.winfo_rootx(), b.winfo_rooty(),
                      b.winfo_width(), b.winfo_height()))
    sizes = [(r[2], r[3]) for r in rects]
    small = [i for i, (w, h) in enumerate(sizes) if w < 24 or h < 24]
    check(not small, f'A17d 所有按钮 hit area ≥24×24（sizes={sizes}）')
    # 互不重叠：矩形两两相交检测
    overlaps = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            x1, y1, w1, h1 = rects[i]
            x2, y2, w2, h2 = rects[j]
            if (x1 < x2 + w2 and x2 < x1 + w1
                    and y1 < y2 + h2 and y2 < y1 + h1):
                overlaps.append((i, j))
    check(not overlaps, f'A17e 工具栏按钮互不重叠（重叠对: {overlaps}）')

    # 概览磁贴 2×2 网格落位（_ov_tiles 存数值标签句柄，master 即磁贴 Frame）
    tiles = [lbl.master for lbl in (getattr(gui, '_ov_tiles', None) or {}).values()]
    check(len(tiles) == 4, f'A17f 磁贴句柄数 = {len(tiles)}（4）')
    expected_pos = [(0, 0), (0, 1), (1, 0), (1, 1)]
    for t, (row_want, col_want) in zip(tiles, expected_pos):
        info = t.grid_info()
        check((info['row'], info['column']) == (row_want, col_want),
              f'A17g 磁贴落位 ({row_want},{col_want})（实际 '
              f'({info["row"]},{info["column"]})）')

    root.destroy()


if __name__ == '__main__':
    _backup_theme_pref()
    try:
        run()
    finally:
        _restore_theme_pref()
    total = len(_PASSED)
    print(f'\n=== verify_gui_v5_offscreen: {total} 项 PASS ===')
    sys.stdout.flush()
    # GUI 后台非守护线程会阻止自然退出，强制退出避免挂起。
    os._exit(0)
