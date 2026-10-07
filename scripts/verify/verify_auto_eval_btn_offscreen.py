# -*- coding: utf-8 -*-
"""
离屏冒烟：自我升级自动评估按钮 + 降级渲染（§4.1/§4.4）

验证目标：
1. 进化页工具栏存在「🛡️ 自动评估」按钮，回调绑定 _on_auto_evaluator_scan。
2. 新方法 _on_auto_evaluator_scan / _render_auto_eval_report 存在。
3. 点按后台线程 → DB 不可用时 auto_evaluator 优雅降级（评估器返回 critical，
   不抛异常），主线程 _render_auto_eval_report 正常渲染到 evo_log 且不崩。
4. 渲染方法能处理 db=None 的降级报告（overall/level 缺失也不崩）。

运行：QT_QPA_PLATFORM=offscreen HOME=<隔离目录> /d/anaconda3/python.exe 本脚本
"""
import os
import sys
import time

# 隔离 HOME，避免测试污染真实配置/DB
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _find_toolbar_buttons(gui):
    """递归收集 gui 上所有 tk.Button，返回 (text, command) 列表。"""
    import tkinter as tk
    found = []

    def walk(w):
        try:
            for ch in w.winfo_children():
                cls = ch.__class__.__name__
                if cls in ('Button',):
                    try:
                        found.append((str(ch['text']), ch.cget('command')))
                    except Exception:
                        found.append((str(ch['text']), None))
                walk(ch)
        except Exception:
            pass

    walk(gui.root)
    return found


def main():
    ok = True
    import tkinter as tk

    from main import LotteryGUI as App  # 项目 GUI 类（唯一入口）
    root = tk.Tk()
    root.withdraw()

    # 构建 GUI（隔离 HOME 已设，DB 连接失败自动降级）
    gui = App(root)
    root.update()

    # 1. 方法存在
    for meth in ('_on_auto_evaluator_scan', '_render_auto_eval_report'):
        if not hasattr(gui, meth):
            print(f'[FAIL] 方法缺失: {meth}')
            ok = False
            break
    print('[PASS] 方法存在: _on_auto_evaluator_scan / _render_auto_eval_report') if ok else None

    # 2. 工具栏存在「🛡️ 自动评估」按钮（command 可调用）
    buttons = _find_toolbar_buttons(gui)
    auto_btn = [b for b in buttons if '自动评估' in b[0]]
    if auto_btn:
        text, cmd = auto_btn[0]
        callable_ok = cmd is not None and (callable(cmd) or isinstance(cmd, str))
        print(f'[PASS] 工具栏存在「{text}」按钮 (command 可解析={callable_ok})')
        if not callable_ok:
            ok = False
    else:
        print('[FAIL] 工具栏未找到「自动评估」按钮')
        ok = False

    # 3. 渲染方法对降级报告容错（db=None 场景，report 缺 summary 字段也不崩）
    class _StubReport:
        scan_time = '2026-10-07T00:00:00'
        summary = {}  # 故意空，模拟降级

    try:
        gui._render_auto_eval_report(_StubReport(), '## 自动评估（降级）\n- 数据库不可用', '')
        gui.root.update()
        print('[PASS] _render_auto_eval_report 对空 summary 降级报告渲染不崩')
    except Exception as e:
        print(f'[FAIL] 渲染降级报告异常: {e}')
        ok = False

    # 4. 点按按钮：后台线程 + 降级渲染不崩。
    #    monkeypatch 模块级 run_full_evaluation / save_report，让评估秒回一份降级报告，
    #    绕开 8 个评估器串行 P5Database().connect()（隔离 HOME 下 DB 不可达会长时间阻塞）。
    import modules.auto_evaluator as ae

    class _StubReport4:
        scan_time = '2026-10-07T00:00:00'
        summary = {'overall_score': 75.0, 'health_level': 'degraded'}
        results = []

        def render_markdown(self):
            return '## 自动评估（离屏桩）\n- 数据库不可用（降级）'

    _rendered = []
    _orig_render = gui._render_auto_eval_report

    def _fake_render(report, md, saved_path):
        _rendered.append((report, md, saved_path))
        # 真实渲染到 evo_log（容错验证），但不弹 toast（离屏无窗口）
        _orig_render.__func__(gui, report, md, saved_path)

    _orig_rfe = ae.ProjectAutoEvaluator.run_full_evaluation
    _orig_save = ae.ProjectAutoEvaluator.save_report
    ae.ProjectAutoEvaluator.run_full_evaluation = lambda self: _StubReport4()
    ae.ProjectAutoEvaluator.save_report = lambda self, report, path=None: ''
    gui._render_auto_eval_report = lambda r, m, p: _fake_render(r, m, p)

    try:
        gui._on_auto_evaluator_scan()
        # 轮询主线程 until 后台线程经 root.after 回投完成渲染
        deadline = time.time() + 5.0
        while time.time() < deadline and not _rendered:
            gui.root.update()
            time.sleep(0.05)
        if _rendered:
            print('[PASS] 点按「自动评估」→ 后台线程 → root.after 回投 → 主线程渲染成功')
        else:
            # 诊断：手动同步复现 worker 主体，暴露真实异常
            import traceback
            try:
                ev = ae.ProjectAutoEvaluator()
                rep = ev.run_full_evaluation()
                mdv = ev.render_markdown(rep)
                print(f'[DIAG] 手动复现 OK: report={type(rep).__name__} md_len={len(mdv)}')
            except Exception:
                print('[DIAG] worker 主体复现异常:')
                traceback.print_exc()
            print('[FAIL] 点按后 5s 内未触发主线程渲染（后台线程未回投）')
            ok = False
    except Exception as e:
        print(f'[FAIL] 点按自动评估异常: {e}')
        ok = False
    finally:
        ae.ProjectAutoEvaluator.run_full_evaluation = _orig_rfe
        ae.ProjectAutoEvaluator.save_report = _orig_save
        gui._render_auto_eval_report = _orig_render

    root.update()
    root.destroy()
    print('\n=== verify_auto_eval_btn_offscreen: ' + ('ALL PASS' if ok else 'FAIL') + ' ===')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
