# -*- coding: utf-8 -*-
"""bench_gui_startup.py 的子进程探针：单轮 GUI 冷启动计时（import/build/visible）。

协议：stdout 打印唯一标记行 '[BENCH] {json}'，父进程解析该行取数；
stderr 保留诊断输出。成功退出码 0，失败 1。不对外暴露其他接口。

说明：本脚本仅测量 GUI 启动工程指标，与排列五开奖预测能力无关。
【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，
本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。
请理性购彩，量力而行。
"""
import json
import os
import shutil
import sys
import time

# ---------------------------------------------------------------------------
# 离屏环境准备（与 verify_dash_flicker_offscreen.py 同一范式）：
# HOME/USERPROFILE 重定向到固定临时目录，使字体缓存等在多次迭代间复用，
# 同时不触碰真实用户配置；该段耗时仅数十毫秒，不计入 t_import。
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

_HOME = os.path.join(_HERE, '_bench_test_home')
os.environ['HOME'] = _HOME
os.environ['USERPROFILE'] = _HOME
os.makedirs(_HOME, exist_ok=True)

# 主题偏好落在 PROJECT_ROOT/data 下，构造 GUI 可能写回，备份/还原保护原值。
_PREF_FILE = os.path.join(_ROOT, 'data', 'theme_preference.json')
_PREF_BAK = _PREF_FILE + '.bench_verify_bak'


def _backup_theme_pref():
    """备份主题偏好文件（存在则复制，不存在则跳过）。"""
    try:
        if os.path.exists(_PREF_FILE) and not os.path.exists(_PREF_BAK):
            shutil.copy2(_PREF_FILE, _PREF_BAK)
    except Exception:
        pass


def _restore_theme_pref():
    """还原主题偏好文件并清理备份副本。"""
    try:
        if os.path.exists(_PREF_BAK):
            shutil.copy2(_PREF_BAK, _PREF_FILE)
            os.remove(_PREF_BAK)
    except Exception:
        pass


def main():
    """执行单轮冷启动计时并以 [BENCH] 标记行输出 JSON。

    返回:
        int —— 进程退出码（0 成功 / 1 失败）。
    """
    _backup_theme_pref()
    result = {'ok': False, 'error': None}
    root = None
    # t_import：import main 的全部模块导入耗时（pandas/numpy/matplotlib 等，
    # 冷启动含首页字体缓存未命中场景，与真实 `python main.py` 一致）
    t0 = time.perf_counter()
    import main as m  # noqa: E402
    t1 = time.perf_counter()

    try:
        import tkinter as tk  # noqa: E402
        root = tk.Tk()
        # t_build：withdraw 态构建完整控件树（不映射真实窗口，隔离 WM 开销）
        root.withdraw()
        gui = m.LotteryGUI(root)
        t2 = time.perf_counter()
        # t_visible：解除离屏 + 布局就绪，对齐「启动到窗口可见」口径
        try:
            root.deiconify()
        except Exception:
            pass
        root.geometry('1024x720')
        root.update()
        time.sleep(0.05)
        root.update()
        t3 = time.perf_counter()
        result.update({
            'ok': True,
            'import_s': round(t1 - t0, 4),
            'build_s': round(t2 - t1, 4),
            'visible_s': round(t3 - t2, 4),
            'total_s': round(t3 - t0, 4),
            'geometry': '{}x{}'.format(root.winfo_width(), root.winfo_height()),
            'nb_tab': (gui.output_nb.select()
                       if hasattr(gui, 'output_nb') else None),
        })
    except Exception as exc:
        result['error'] = '{}: {}'.format(type(exc).__name__, exc)
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        _restore_theme_pref()

    sys.stdout.write('[BENCH] ' + json.dumps(result, ensure_ascii=False) + '\n')
    sys.stdout.flush()
    # 子进程显式硬退出，规避 Tk/atexit 尾流（与闪烁离屏探针同一范式）
    os._exit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
