# -*- coding: utf-8 -*-
"""v3.52 三 bugfix 快速自检脚本（Anaconda python）。"""
import sys, os, traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def check_self_evolution_import():
    """(1) SelfEvolutionEngine 可导入。"""
    try:
        from modules.self_evolution import SelfEvolutionEngine, AutoEvoScheduler
        check("SelfEvolutionEngine 可导入", True)
        return True
    except Exception as e:
        check("SelfEvolutionEngine 可导入", False, str(e))
        return False


def check_ai_strip():
    """(2) main.LotteryGUI._report_ai_prediction_commentary 的 strip 调用语法正确。"""
    try:
        import main as gui_mod
        src = open(os.path.join(PROJECT_ROOT, "main.py"), encoding="utf-8").read()
        has_bug = "line.strip\n" in src or "line.strip\r\n" in src
        # 更精确：查找 for 循环里的 line.strip 调用
        import re
        bad = re.search(r"for\s+line\s+in.*:\s*\n\s*if\s+line\.strip\(\)\s*:\s*\n\s*task_mgr\.log\(\s*f[\"\']\s*\{\s*line\.strip\s*\}",
                        src)
        check("AI 解读 strip 调用已修复", not bad,
              "line.strip 缺少括号仍存在" if bad else "")
        return not bad
    except Exception as e:
        check("AI 解读 strip 调用已修复", False, str(e))
        return False


def check_backtest_issue_param():
    """(3) backtester.generate_backtest_report / generate_comparison_report 传入了 issue 参数。"""
    try:
        src = open(os.path.join(PROJECT_ROOT, "modules", "backtester.py"), encoding="utf-8").read()
        # 直接检测函数体里是否出现了 `issue=`（两处均须有）
        r1 = src.index("def generate_backtest_report")
        r2 = src.index("def generate_comparison_report")
        segment_report = src[r1:r2]
        segment_cmp = src[r2:r2+3000]
        has_issue_report = "issue=" in segment_report
        has_issue_cmp = "issue=" in segment_cmp
        ok = has_issue_report and has_issue_cmp
        check("backtest_report 入库带 issue 参数", ok,
              "" if ok else f"report={has_issue_report}, cmp={has_issue_cmp}")
        return ok
    except Exception as e:
        check("backtest_report 入库带 issue 参数", False, str(e))
        return False


def check_pipeline_two_step():
    """(4) pipeline 两步结构确认（step1-3 注释停用）。"""
    try:
        src = open(os.path.join(PROJECT_ROOT, "modules", "pipeline.py"), encoding="utf-8").read()
        # 两步主路径：execute_pipeline → _calc_statistical_prediction → step4_final_prediction
        has_two_step = ("_calc_statistical_prediction" in src and
                        "step4_final_prediction" in src)
        check("Pipeline 两步结构确认", has_two_step)
        return has_two_step
    except Exception as e:
        check("Pipeline 两步结构确认", False, str(e))
        return False


def main():
    print("=== KPLuckyNumber v3.52 三 bugfix 快速自检 ===\n")
    ok1 = check_self_evolution_import()
    ok2 = check_ai_strip()
    ok3 = check_backtest_issue_param()
    check_pipeline_two_step()
    print("\n=== 汇总 ===")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"通过 {passed}/{total}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())