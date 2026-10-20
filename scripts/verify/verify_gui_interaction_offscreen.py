# -*- coding: utf-8 -*-
"""离屏 e2e 守卫：GUI-10 交互体验细节综合验收（方案 §331）。

覆盖 GUI-10 验收标准（PUP-20261019-013100 §331）：
  1. 显式取消按钮入口：真实 LotteryGUI 构造后 _cancel_btn 存在、初始
     灰化禁用态、未混入操作按钮登记表 _buttons（反转管理契约）；
  2. 取消按钮状态机：运行中（DISABLED 侧）转 NORMAL + danger 三态配色
     （视觉规范工厂单一来源），空闲态回灰化禁用；
  3. 取消链路恢复时序（核心验收）：fake task_mgr 编排运行中，
     _cancel_current_task() 全链路墙钟 < 1.0 s 且终态恢复
     （操作按钮 NORMAL + 取消按钮 DISABLED + 任务态同步清空）；
  4. Toast 契约：duration=0 真实窗口 ≤3 s 自动销毁 +
     clamp_toast_duration(99000)==5000（「自动消失 ≤ 5 s」）；
  5. 快捷键：_setup_keyboard_shortcuts() 重入不抛且 Ctrl+C / Ctrl+Return
     / Escape 绑定非空（root.bind 回读 Tcl 命令串）；
  6. 空态恢复：_hide_result_dashboard() 后 placeholder 重新挂载
     （winfo_manager() == 'pack'，withdraw 下唯一可靠判据）；
  7. ui_toast 常量契约：默认 2200 / 上限 5000 / 最短驻留 600。

设计要点（同 verify_gui_v5_offscreen.py 骨架）：
  * offscreen 构造 GUI（withdraw），不依赖显示设备；
  * 备份 / 还原 data/theme_preference.json（防主题污染）；
  * fake task_mgr 用完即还原，不触碰真实 TaskManager/cancel_event 链路；
  * 结束强制 os._exit(0)，避免 GUI 后台非守护线程（self_evolution /
    perf_monitor / task_manager）挂起进程。

用法:
    python scripts/verify/verify_gui_interaction_offscreen.py
    # 退出码 0 = 全部通过；1 = 存在 FAIL
"""
import os
import shutil
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_HOME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '_gui_interaction_test_home')
os.environ['HOME'] = _HOME
os.environ['USERPROFILE'] = _HOME
os.makedirs(_HOME, exist_ok=True)

# 主题偏好落在 PROJECT_ROOT/data 下，_toggle_theme 会写回；守卫备份/还原。
_PREF_FILE = os.path.join(_ROOT, 'data', 'theme_preference.json')
_PREF_BACKUP = _PREF_FILE + '.gui_interaction_verify_bak'


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
from modules import ui_toast  # noqa: E402


_PASSED = []


def check(cond, desc):
    assert cond, f'FAIL: {desc}'
    _PASSED.append(desc)
    print(f'[PASS] {desc}')


class _FakeTaskMgr:
    """TaskManager 替身：编排 is_running=True、记录 cancel、同构锁/集合。

    仅用于计时取消链路；用完由调用方还原 gui.task_mgr，绝不触碰真实
    TaskManager 与 cancel_event 链路。
    """

    def __init__(self, running=True):
        self._running = running
        self.cancel_calls = 0
        self._running_lock = threading.Lock()
        self._running_tasks = set()

    def is_running(self):
        return self._running

    def cancel(self):
        self.cancel_calls += 1
        self._running = False
        return True


def run():
    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    root.update_idletasks()

    # ---------- 1. 取消按钮入口存在性与初始态 ----------
    btn = getattr(gui, '_cancel_btn', None)
    check(btn is not None, 'I1 显式取消按钮 _cancel_btn 已创建')
    check(str(btn.cget('state')) == 'disabled',
          'I2 取消按钮初始为灰化禁用态（空闲不可点）')
    check(str(btn.cget('bg')) == m.COLORS['bg_input'],
          'I3 取消按钮初始 bg 取 bg_input（禁用外观统一）')
    check(str(btn.cget('fg')) == m.COLORS['text_disabled'],
          'I4 取消按钮初始 fg 取 text_disabled（禁用外观统一）')
    ops = getattr(gui, '_buttons', None) or []
    check(btn not in ops,
          f'I5 取消按钮未混入操作按钮登记表 _buttons（{len(ops)} 个操作按钮）')
    check(str(btn.cget('text')) == '取消',
          'I6 取消按钮文案为「取消」')

    # ---------- 2. 状态机反转 + danger 三态 ----------
    gui._set_cancel_btn_state(btn, tk.DISABLED)
    check(str(btn.cget('state')) == 'normal',
          'I7 操作按钮侧 DISABLED → 取消按钮转 NORMAL（运行中可点）')
    danger = m.button_style_map(m.COLORS)['danger']
    check(str(btn.cget('bg')) == danger['bg']
          and str(btn.cget('fg')) == danger['fg'],
          'I8 运行中取消按钮取 danger 三态配色（视觉规范工厂单一来源）')
    gui._set_cancel_btn_state(btn, tk.NORMAL)
    check(str(btn.cget('state')) == 'disabled'
          and str(btn.cget('bg')) == m.COLORS['bg_input'],
          'I9 操作按钮侧 NORMAL → 取消按钮回灰化禁用')

    # ---------- 3. 取消链路恢复时序（核心验收：1 s 内恢复） ----------
    fake = _FakeTaskMgr(running=True)
    with fake._running_lock:
        fake._running_tasks.add('verify_task')
    orig_mgr = gui.task_mgr
    gui.task_mgr = fake
    try:
        # 先制造「运行中」按钮态（与真实任务启动路径一致）
        gui._set_buttons_state(tk.DISABLED)
        check(str(btn.cget('state')) == 'normal',
              'I10 运行中操作按钮禁用、取消按钮启用（启动态自洽）')
        t0 = time.perf_counter()
        gui._cancel_current_task()
        elapsed = time.perf_counter() - t0
        root.update()  # 冲刷 after(0, _sync_task_state) 同步拍
        check(elapsed < 1.0,
              f'I11 取消链路全链路耗时 {elapsed * 1000:.1f}ms < 1000ms'
              '（1s 恢复验收）')
        check(fake.cancel_calls == 1,
              'I12 task_mgr.cancel() 恰被调用 1 次（不重复取消）')
        check(all(str(b.cget('state')) == 'normal' for b in ops),
              'I13 取消后操作按钮全部恢复 NORMAL（UI 可操作态）')
        check(str(btn.cget('state')) == 'disabled',
              'I14 取消后取消按钮回到灰化禁用（反转终态）')
        check(fake._running_tasks == set(),
              'I15 _sync_task_state 已清空运行任务（防竞态误判）')
    finally:
        gui.task_mgr = orig_mgr

    # ---------- 4. Toast 契约：真实销毁 + clamp 上限 ----------
    gui._show_toast('GUI-10 离屏校验', duration=0)
    check(gui._toast_win is not None, 'T1 Toast 窗口已创建（duration=0）')
    deadline = time.time() + 3.0
    while time.time() < deadline and gui._toast_win is not None:
        root.update()
        time.sleep(0.05)
    check(gui._toast_win is None,
          'T2 Toast duration=0 在 ≤3s 内自动销毁（hold 兜底 600ms）')
    check(ui_toast.clamp_toast_duration(99000) == 5000,
          'T3 clamp(99000)==5000（「自动消失 ≤5s」契约上限）')
    check(ui_toast.clamp_toast_duration(0) == 0
          and ui_toast.clamp_toast_duration(-100) == 0
          and ui_toast.clamp_toast_duration(None) == 2200,
          'T4 clamp 零/负/None 落点正确（0/0/2200）')
    check(ui_toast.toast_icon_for(m.COLORS['success'], m.COLORS) == '✓'
          and ui_toast.toast_icon_for(m.COLORS['accent_danger'],
                                      m.COLORS) == '✗',
          'T5 Toast 图标契约（success→✓ / danger→✗）')

    # ---------- 5. 常量契约 ----------
    check(ui_toast.TOAST_DEFAULT_DURATION_MS == 2200
          and ui_toast.TOAST_MAX_DURATION_MS == 5000
          and ui_toast.TOAST_MIN_HOLD_MS == 600,
          'C1 ui_toast 三常量契约（2200/5000/600）')

    # ---------- 6. 快捷键绑定（重入不抛 + 绑定非空） ----------
    gui._setup_keyboard_shortcuts()
    gui._setup_keyboard_shortcuts()  # 重入一次：不得抛异常
    for seq in ('<Control-c>', '<Control-Return>', '<Escape>'):
        bound = bool(gui.root.bind(seq))
        check(bound, f'K1 快捷键 {seq} 绑定非空（重入后仍有效）')

    # ---------- 7. 空态恢复 ----------
    gui._hide_result_dashboard()
    root.update_idletasks()
    placeholder = getattr(gui, '_result_placeholder', None)
    check(placeholder is not None
          and placeholder.winfo_manager() == 'pack',
          'E1 _hide_result_dashboard() 后空态占位重新挂载（pack）')
    gui._hide_result_dashboard()  # 幂等：重复调用不抛
    check(True, 'E2 重复隐藏幂等（无异常）')

    root.destroy()


if __name__ == '__main__':
    _backup_theme_pref()
    try:
        run()
    finally:
        _restore_theme_pref()
    total = len(_PASSED)
    print(f'\n=== verify_gui_interaction_offscreen: {total} 项 PASS ===')
    sys.stdout.flush()
    # GUI 后台非守护线程会阻止自然退出，强制退出避免挂起。
    os._exit(0)
