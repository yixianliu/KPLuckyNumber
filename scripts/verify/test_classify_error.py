"""单元测试：_classify_error 错误归因分类逻辑（v4.0 §2.4 错误引导）。

_classify_error 是纯字符串归因方法，不访问任何实例属性，
故用 LotteryGUI.__new__ 绕过 __init__（避免重量级 GUI 构建），直接调用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 离屏 + 隔离 HOME
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['HOME'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ce_test_home')
os.environ['USERPROFILE'] = os.environ['HOME']

import main as m  # noqa: E402


def _make_stub():
    """绕过 __init__ 构造 LotteryGUI 实例（仅用于调用无状态方法）。"""
    return m.LotteryGUI.__new__(m.LotteryGUI)


CASES = [
    # (错误文本, 期望类别)
    ("pymysql.err.OperationalError: Can't connect to MySQL server", "数据库连接"),
    ("数据库连接失败", "数据库连接"),
    ("Access denied for user 'root'", "数据库连接"),
    ("requests.exceptions.ConnectionTimeout: Read timed out", "网络/数据源"),
    ("爬取数据源超时", "网络/数据源"),
    ("ConnectionError: 网络中断", "网络/数据源"),
    ("List index out of range", "数据不足"),
    ("历史数据不足（需≥61期）", "数据不足"),
    ("numpy.linalg.LinAlgError: array must not contain infs", "模型/算法"),
    ("模型训练未收敛", "模型/算法"),
    ("FileNotFoundError: reports dir", "文件/路径"),
    ("Permission denied: /logs", "文件/路径"),
    ("MemoryError: unable to allocate", "内存不足"),
    ("some totally unknown error xyz123", "未知错误"),
]


def run():
    stub = _make_stub()
    failures = []
    for msg, expected in CASES:
        category, reason, suggestions = stub._classify_error(msg)
        ok = (category == expected)
        assert isinstance(suggestions, list) and len(suggestions) >= 1, \
            f"建议列表异常: {msg!r} -> {suggestions}"
        assert reason, f"原因为空: {msg!r}"
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {msg[:40]!r:44} -> {category} (期望 {expected})")
        if not ok:
            failures.append((msg, expected, category))

    # 空输入不崩溃
    c, r, s = stub._classify_error("")
    assert c == "未知错误", f"空输入应兜底未知错误, got {c}"
    print(f"[PASS] 空输入兜底 -> {c}")

    if failures:
        print(f"\n=== {len(failures)} FAIL ===")
        for f in failures:
            print("  ", f)
        return 1
    print("\n=== test_classify_error: ALL PASS ===")
    return 0


if __name__ == '__main__':
    try:
        code = run()
        # 清理临时隔离目录
        import shutil
        shutil.rmtree(os.environ.get('HOME', ''), ignore_errors=True)
        raise SystemExit(code)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
