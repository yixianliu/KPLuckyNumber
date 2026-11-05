"""
GUI 离屏构建诊断脚本
- withdraw 模式构建 LotteryGUI（不依赖显示设备）
- 遍历控件树、检查 winfo_width/height > 1 验证几何计算
- 输出每个关键容器的 pack/grid 管理状态
- 定位是否存在渲染失败的子面板
"""
import os, sys, traceback, time

# 强制项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

print(f"[DIAG] cwd = {os.getcwd()}")
print(f"[DIAG] sys.path[0] = {sys.path[0]}")

try:
    import tkinter as tk
    from tkinter import ttk
    print(f"[DIAG] Tkinter OK, version={tk.TkVersion}")
except Exception as e:
    print(f"[DIAG] Tkinter 导入失败: {e}")
    sys.exit(1)

errors = []

def safe_get(widget, attr):
    try:
        return getattr(widget, attr, None)
    except Exception:
        return None

def dump_widget_tree(widget, depth=0, max_depth=6):
    """递归输出控件树结构与几何信息"""
    prefix = "  " * depth
    try:
        w = widget.winfo_width()
        h = widget.winfo_height()
        mg = widget.winfo_manager()  # 'pack'/'grid'/'place' or ''
        cls = widget.__class__.__name__
        name = str(widget).split('!')[-1][:40]
        geom_ok = w > 1 and h > 1
        status = "OK" if geom_ok else "GEOM_FAIL"
        print(f"{prefix}[{status}] {cls} width={w} height={h} mgr={mg} name={name}")
        if not geom_ok and depth < 3:
            errors.append(f"GEOM_FAIL: {cls} name={name} w={w} h={h}")
        if depth >= max_depth:
            return
        for child in widget.winfo_children():
            dump_widget_tree(child, depth + 1, max_depth)
    except Exception as e:
        print(f"{prefix}[ERR] dump failed: {e}")

print("\n" + "=" * 60)
print("[DIAG] Phase 1: 创建 root + withdraw")
print("=" * 60)
root = tk.Tk()
root.withdraw()
print(f"[DIAG] root created, id={root.winfo_id()}")

print("\n" + "=" * 60)
print("[DIAG] Phase 2: 构建 LotteryGUI")
print("=" * 60)
try:
    t0 = time.time()
    from main import LotteryGUI
    app = LotteryGUI(root)
    t1 = time.time()
    print(f"[DIAG] LotteryGUI 构建耗时: {t1-t0:.2f}s")
except Exception as e:
    print(f"[DIAG] LotteryGUI 构建失败: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("[DIAG] Phase 3: update_idletasks + deiconify")
print("=" * 60)
try:
    root.update_idletasks()
    root.deiconify()
    root.update()
    time.sleep(0.5)
    root.update()
except Exception as e:
    print(f"[DIAG] deiconify 失败: {e}")
    traceback.print_exc()

print(f"[DIAG] root geometry after deiconify: {root.geometry()}")
print(f"[DIAG] root visibility: {root.winfo_viewable()}")

print("\n" + "=" * 60)
print("[DIAG] Phase 4: 控件树遍历")
print("=" * 60)
dump_widget_tree(root, depth=0, max_depth=5)

print("\n" + "=" * 60)
print("[DIAG] Phase 5: 汇总")
print("=" * 60)
if errors:
    print(f"[DIAG] 发现 {len(errors)} 个几何失败控件:")
    for e in errors[:20]:
        print(f"  - {e}")
else:
    print("[DIAG] 所有控件几何计算正常！")

# 检查关键面板是否存在
print("\n[DIAG] 关键容器检查:")
for attr in ['_left_container', '_right_container', '_status_bar',
             'cli_log_text', '_header_frame']:
    obj = getattr(app, attr, None)
    if obj is None:
        print(f"  [MISS] {attr} = None")
    else:
        try:
            w = obj.winfo_width()
            h = obj.winfo_height()
            print(f"  [OK] {attr}: {obj.__class__.__name__} w={w} h={h}")
        except Exception as e:
            print(f"  [ERR] {attr}: {e}")

root.destroy()
print("\n[DIAG] 诊断完成")
