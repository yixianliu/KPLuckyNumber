"""离屏 e2e 冒烟测试：验证「预测结果」页概览区四个指标磁贴的创建与原地回填。

背景（v4.0 §2.4 交互体验升级）：
  - overview_container 此前只创建容器、从未填充指标磁贴；
  - 本轮新增 _create_ov_tile（返回数值标签句柄）与 _refresh_result_overview /
    _apply_ov_values（后台查库 + 主线程原地回填）。
  本脚本在 offscreen 环境下构造 GUI，断言：
    1. 四个磁贴数值标签句柄齐备（issue/hit_rate/verified/data_count）；
    2. _apply_ov_values 能原地回填数值（不重建控件）；
    3. 占位/空态场景下回填为「—」不抛异常。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 确保 tkinter 使用内存显示
import tkinter as tk

# 隔离用户数据目录，避免测试落盘污染生产配置
os.environ['HOME'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ov_test_home')
os.environ['USERPROFILE'] = os.environ['HOME']

import main as m  # noqa: E402


def run():
    root = tk.Tk()
    root.withdraw()  # 离屏不显示
    gui = m.LotteryGUI(root)

    # ---- 断言 1：四个磁贴数值标签句柄齐备 ----
    tiles = getattr(gui, '_ov_tiles', None)
    assert isinstance(tiles, dict), "_ov_tiles 应为 dict"
    for key in ('issue', 'hit_rate', 'verified', 'data_count'):
        assert key in tiles, f"缺少磁贴句柄: {key}"
        lbl = tiles[key]
        assert lbl.winfo_exists(), f"磁贴 {key} 数值标签未挂载到 widget 树"
    print(f"[PASS] 四个磁贴句柄齐备: {list(tiles.keys())}")

    # ---- 断言 2：原地回填数值 ----
    gui._apply_ov_values("2026001", "10.2%", "152 期", "1024 期")
    root.update_idletasks()
    got = {
        'issue': tiles['issue'].cget('text'),
        'hit_rate': tiles['hit_rate'].cget('text'),
        'verified': tiles['verified'].cget('text'),
        'data_count': tiles['data_count'].cget('text'),
    }
    assert got['issue'] == "2026001", got
    assert got['hit_rate'] == "10.2%", got
    assert got['verified'] == "152 期", got
    assert got['data_count'] == "1024 期", got
    print(f"[PASS] 原地回填成功: {got}")

    # ---- 断言 3：占位「—」回填不抛异常 ----
    gui._apply_ov_values("—", "—", "—", "—")
    root.update_idletasks()
    assert tiles['issue'].cget('text') == "—"
    print("[PASS] 占位降级回填正常")

    # ---- 断言 4：_refresh_result_overview 空 tiles 场景静默返回 ----
    gui._ov_tiles = {}
    gui._refresh_result_overview()  # 不应抛异常
    gui._ov_tiles = tiles
    print("[PASS] 空 tiles 场景静默返回")

    root.destroy()
    print("\n=== verify_ov_tiles_offscreen: ALL PASS ===")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except Exception as e:
        import traceback
        print("\n=== FAIL ===")
        traceback.print_exc()
        raise SystemExit(1)
