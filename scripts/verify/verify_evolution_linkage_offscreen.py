"""离屏 GUI 守卫：进化联动面板 + evolve_trigger 事件呈现（v5.0 §7.5）。

与 `verify_evolution_linkage.py`（引擎层链路核验）配套：本脚本在 **真实 Tk 控件树**
上确认三件事——

  1. 「联动状态」面板能正常打开并渲染全部字段（空状态也不崩）；
  2. 面板常驻诚实边界声明（公平摇号），且不随状态变化消失；
  3. 候选未超基线时给出「未严格超越基线，未注入生产权重」的诚实结论；
  4. `evolve_trigger` 事件（触发 / 跳过两种形态）都能进入进化日志，不静默。

设计要点：
  * offscreen 构造 GUI（withdraw），不依赖显示设备；
  * 不触发真实分析 / 不连 DB / 不触 sklearn；
  * 直接注入联动状态字典，绕过 `sync_analysis_result` 的真实数据依赖。

用法：
    python scripts/verify/verify_evolution_linkage_offscreen.py      # 退出码 0 = 全部通过
"""

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_evo_link_test_home')
os.environ.setdefault('HOME', _BASE)
os.environ.setdefault('USERPROFILE', _BASE)
os.makedirs(_BASE, exist_ok=True)

import tkinter as tk  # noqa: E402

import main as m  # noqa: E402

PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")


def _find_texts(widget, out=None):
    """递归收集控件树里所有 tk.Text。"""
    if out is None:
        out = []
    if isinstance(widget, tk.Text):
        out.append(widget)
    for child in getattr(widget, 'winfo_children', lambda: [])():
        _find_texts(child, out)
    return out


def _panel_text(window):
    """取指定「联动状态」弹窗里的正文文本。"""
    for txt in _find_texts(window):
        try:
            content = txt.get('1.0', tk.END)
        except Exception:  # noqa: BLE001
            continue
        if '最后分析期号' in content:
            return content
    return ''


def _open_link_panel(gui, root):
    """调用 _show_evolution_link_state，返回新建的 Toplevel。

    离屏环境下无法遍历主窗口完整子树，这里在调用前后对比
    root 的直接子控件，差集即本次新建的弹窗。
    """
    before = {id(c) for c in root.winfo_children()}
    gui._show_evolution_link_state()
    new = [c for c in root.winfo_children() if id(c) not in before]
    return new[0] if new else None


def _evo_log_text(gui):
    return gui.evo_log.get('1.0', tk.END)


def run():
    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    root.update_idletasks()
    check(True, 'S1 GUI 离屏构造成功')

    # 手动注入引擎：绕开 _init_evolution_engine 的 auto=True + start()，
    # 避免离屏测试触发真实进化流水线（DB / sklearn / 分钟级等待）。
    import tempfile
    from modules.self_evolution import SelfEvolutionEngine
    gui.evolution = SelfEvolutionEngine(
        data_dir=tempfile.mkdtemp(prefix='kpl_gui_link_'), auto=False)
    check(gui.evolution is not None,
          'S1b 进化引擎已注入（独立 data 目录，不触发自动流水线）')

    _panels = []  # 每次打开的面板，便于逐项读取内容与最后清理

    # ---------------- 1. 空状态不崩 + 诚实边界常驻 ----------------
    gui.evolution._link_state.clear()
    gui.evolution._checkpoint = {}
    try:
        panel = _open_link_panel(gui, root)
        _panels.append(panel)
        root.update_idletasks()
        check(panel is not None, 'S2 空状态下面板可打开（不抛异常）')
    except Exception as exc:  # noqa: BLE001
        panel = None
        check(False, f'S2 空状态下面板可打开（不抛异常）→ {exc}')

    txt = _panel_text(panel) if panel is not None else ''
    check('最后分析期号' in txt, 'S3 面板渲染最后分析期号')
    check('预测目标期号' in txt, 'S4 面板渲染预测目标期号')
    check('等待回填' in txt, 'S5 面板渲染等待回填标志')
    check('暂无验证记录' in txt, 'S6 空状态给出「暂无验证记录」而非异常空白')
    check('公平摇号' in txt, 'S7 诚实边界声明常驻（空状态也不消失）')

    # ---------------- 2. 有验证记录时渲染命中数据 ----------------
    gui.evolution._link_state.update({
        'last_analysis_issue': '2026901',
        'last_prediction': {
            'target_issue': '2026901',
            'top_numbers': [[3], [7], [1], [5], [9]],
        },
        'pending_verification': True,
        'last_sync_ts': '2026-10-12 09:47:56',
        'last_verification': {
            'issue': '2026901', 'top1': 40.0, 'top3': 80.0,
            'top5': 80.0, 'ts': '2026-10-12 09:47:56'},
    })
    _panels.append(_open_link_panel(gui, root))
    root.update_idletasks()
    txt = _panel_text(_panels[-1])
    check('2026901' in txt, 'S8 期号正确呈现')
    check('等待回填: 是' in txt, 'S9 待回填标志正确呈现为「是」')
    check('Top-3 命中: 80.0%' in txt, 'S10 Top-3 命中值正确呈现')
    check('公平摇号' in txt, 'S11 诚实边界声明在有数据时仍常驻')

    # ---------------- 3. 候选未超基线 → 诚实结论 ----------------
    gui.evolution._checkpoint = {
        'history_count': 900,
        'baseline': {'metrics': {'top1': 10.5, 'top3': 30.5, 'top5': 50.5}},
    }
    gui.evolution._link_state['best_candidate'] = {
        'version_tag': 'trial-001', 'lookback': 60,
        'metrics': {'top1': 10.0, 'top3': 30.0, 'top5': 50.0},
    }
    _panels.append(_open_link_panel(gui, root))
    root.update_idletasks()
    txt = _panel_text(_panels[-1])
    check('trial-001' in txt, 'S12 候选版本呈现')
    check('未严格超越基线' in txt, 'S13 未超基线时结论诚实（不夸大为提升）')
    check('未注入生产权重' in txt, 'S14 明确说明未注入生产权重')

    # ---------------- 4. 候选确实超基线 → 反向结论 ----------------
    gui.evolution._link_state['best_candidate']['metrics'] = {
        'top1': 13.0, 'top3': 33.0, 'top5': 53.0}
    _panels.append(_open_link_panel(gui, root))
    root.update_idletasks()
    txt = _panel_text(_panels[-1])
    check('综合评分高于基线' in txt, 'S15 确实超基线时结论正确翻转')

    # ---------------- 5. evolve_trigger 事件呈现 ----------------
    log0 = _evo_log_text(gui)
    gui._handle_evolution_msg({
        'type': 'evolve_trigger', 'issue': '2026901',
        'triggered': True, 'reason_code': 'scheduled_due',
        'reason': '调度到期且样本量充足，触发轻量进化'})
    root.update_idletasks()
    log1 = _evo_log_text(gui)
    check('⚡' in log1 and '⚡' not in log0, 'S16 触发事件写入进化日志')
    check('已触发轻量进化' in log1, 'S17 触发文案可读')

    gui._handle_evolution_msg({
        'type': 'evolve_trigger', 'issue': '2026902',
        'triggered': False, 'reason_code': 'already_triggered_for_issue',
        'reason': '期号 2026902 已触发过进化，本次跳过（去重）'})
    root.update_idletasks()
    log2 = _evo_log_text(gui)
    check('⏸' in log2 and '跳过' in log2, 'S18 跳过事件写入进化日志（不静默）')
    check('去重' in log2, 'S19 跳过原因（去重）可读')

    # 缺 reason 时的兜底文案
    gui._handle_evolution_msg({
        'type': 'evolve_trigger', 'issue': '2026903',
        'triggered': False, 'reason_code': 'insufficient_samples'})
    root.update_idletasks()
    log3 = _evo_log_text(gui)
    check('insufficient_samples' in log3, 'S20 reason 缺失时回退展示 reason_code')

    # ---------------- 6. 清理 ----------------
    for win in _panels:
        try:
            if win is not None and win.winfo_exists():
                win.destroy()
        except Exception:  # noqa: BLE001
            pass
    check(True, f'S21 弹窗清理完成（共打开 {len(_panels)} 个面板）')

    print('\n' + '=' * 60)
    print(f'通过 {len(PASS)}/{len(PASS) + len(FAIL)} 项')
    print('=' * 60)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(run())
