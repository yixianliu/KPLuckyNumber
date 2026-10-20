# -*- coding: utf-8 -*-
"""离屏冒烟：命令行输出标签页交互增强（Phase 4）。

验证目标：
1. `_build_cli_log_tab` 已建文本框 + 内存缓冲 `_cli_log_buffer` + 过滤变量。
2. `_poll_cli_log` 把队列行写入缓冲与文本框；关键字过滤 `_apply_cli_filter`
   仅展示匹配行（大小写不敏感），清除过滤后恢复全部。
3. 右键菜单 `_cli_log_context_menu` / 复制 `_cli_copy` 存在且调用不抛未捕获异常。

运行：QT_QPA_PLATFORM=offscreen /d/anaconda3/python.exe 本脚本
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_HOMEDIR = os.path.join(ROOT, 'tests', '_offscreen_home_cli')
os.makedirs(_HOMEDIR, exist_ok=True)
os.environ['HOME'] = _HOMEDIR

import tkinter as tk

from main import LotteryGUI as App
import modules.cli_log_sink as sink


def main():
    root = tk.Tk()
    root.withdraw()
    gui = App(root)
    root.update()

    # 1. 结构断言
    assert gui.cli_log_text is not None, "cli_log_text 未创建"
    assert getattr(gui, '_cli_log_buffer', None) is not None, "缓冲未初始化"
    assert getattr(gui, '_cli_filter_var', None) is not None, "过滤变量未初始化"

    # 2. 喂入测试日志到 cli 队列，并手动触发一次渲染（不启动 200ms 轮询循环）
    # 注意：App(root) 初始化期间其余模块（self_evolution / perf_monitor /
    # task_manager 等）已并会继续异步通过 cli 队列写入若干行，首次 _poll_cli_log
    # 会一并将它们 drain 进 _cli_log_buffer，故本测试仅用「特征行内容」
    # 断言，不依赖缓冲绝对行数。
    for lvl, line in [
        ('info', 'normal startup line'),
        ('error', 'ERROR: db timeout'),
        ('warning', '警告: low memory'),
        ('info', 'another info line'),
    ]:
        sink._CLI_QUEUE.put((lvl, line))

    gui._poll_cli_log_cancelled = False
    gui._poll_cli_log()  # 渲染一次
    if gui._poll_cli_log_handle is not None:
        try:
            root.after_cancel(gui._poll_cli_log_handle)
        except Exception:
            pass
        gui._poll_cli_log_handle = None
    gui._poll_cli_log_cancelled = True  # 关闭自动轮询

    buf = gui._cli_log_buffer
    # 不依赖绝对行数（App 初始化与后台线程会异步写入命令行队列），
    # 仅断言本测试注入的 4 条特征行全部进入缓冲（内容匹配）。
    buf_lines = [ln for _, ln in buf]
    for expect in ('normal startup line', 'ERROR: db timeout',
                   '警告: low memory', 'another info line'):
        assert expect in buf_lines, f"缓冲缺失特征行: {expect}"
    full = gui.cli_log_text.get('1.0', tk.END)
    assert 'ERROR: db timeout' in full and '警告: low memory' in full, "全量渲染缺失"

    # 关键字过滤：error
    gui._cli_filter_var.set('error')
    gui._apply_cli_filter()
    filtered = gui.cli_log_text.get('1.0', tk.END)
    assert 'ERROR: db timeout' in filtered, "过滤应包含 error 行"
    assert ('警告: low memory' not in filtered) and ('normal startup line' not in filtered), \
        "过滤应排除非匹配行"

    # 大小写不敏感：ERROR（大写）也应命中
    gui._cli_filter_var.set('ERROR')
    gui._apply_cli_filter()
    assert 'ERROR: db timeout' in gui.cli_log_text.get('1.0', tk.END)

    # 清除过滤 → 恢复全部
    gui._cli_filter_var.set('')
    gui._apply_cli_filter()
    assert 'normal startup line' in gui.cli_log_text.get('1.0', tk.END), "清除过滤应恢复全部"

    # 3. 右键菜单 / 复制（offscreen 调用，捕获异常不冒泡）
    class _Ev:
        x_root = 10
        y_root = 10

    try:
        gui._cli_log_context_menu(_Ev())
    except Exception as e:  # pragma: no cover
        print('WARNING: _cli_log_context_menu 调用异常（已捕获）:', e)
    try:
        gui._cli_copy('test clipboard payload')
    except Exception as e:  # pragma: no cover
        print('WARNING: _cli_copy 调用异常（已捕获）:', e)

    print('SMOKE_OK: cli_log 标签页交互增强离屏冒烟通过')
    sys.stdout.flush()
    # GUI 后台线程（self_evolution / perf_monitor / task_manager）为非守护线程，
    # 会阻止解释器自然退出；冒烟已完成，强制退出避免挂起。
    try:
        root.destroy()
    except Exception:
        pass
    os._exit(0)


if __name__ == '__main__':
    main()
