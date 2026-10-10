#!/usr/bin/env python3
"""
Test script to reproduce the prediction number duplication issue.

This script tests the _copy_prediction staleness logic by simulating
the flow of:
1. Start analysis (which resets state and runs the pipeline)
2. Copy prediction (which uses _copy_prediction method)

The test verifies that different periods produce different prediction results.
"""

import sys
import os
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Add the project directory to path
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')

from main import LotteryGUI
from modules.predictor import P5Predictor
from modules.database import P5Database

def test_copy_prediction_staleness():
    """
    Test the _copy_prediction staleness logic.
    
    The key issue: when _last_pipeline_final is None (because dashboard
    hasn't been shown or was reset), the staleness check _stale = bool(
    existing and _pf and _pf_ts > _gen_ts) will always be False because
    _pf is None. This causes the old clipboard to be reused instead of
    generating fresh results.
    """
    print("=" * 70)
    print("TEST: _copy_prediction staleness logic")
    print("=" * 70)
    
    # Simulate the state after analysis completion
    # After _on_task_started resets, then pipeline runs and sets:
    # - _last_pipeline_ts = time.time() (at line 5301)
    # - _last_pipeline_final = pipeline_final (in _show_result_dashboard, line 7033)
    # - _prediction_clipboard = built from final_report (line 5290)
    # - _clipboard_generated_ts = time.time() (at lines 5293-5299)
    
    # Test case 1: Both _last_pipeline_final and _last_pipeline_ts are set
    print("\nTest 1: Both _last_pipeline_final and _last_pipeline_ts are set")
    class MockObj1:
        _prediction_clipboard = "期号: 2024001\n预测号码: 01234"
        _clipboard_generated_ts = 1000.0
        _last_pipeline_final = {"target_issue": "2024001", "next_issue": "2024002"}
        _last_pipeline_ts = 2000.0  # Newer than clipboard
    
    obj1 = MockObj1()
    existing = (getattr(obj1, '_prediction_clipboard', '') or '').strip()
    _gen_ts = getattr(obj1, '_clipboard_generated_ts', 0) or 0
    _pf = getattr(obj1, '_last_pipeline_final', None)
    _pf_ts = getattr(obj1, '_last_pipeline_ts', 0) or 0
    _stale = bool(existing and _pf and _pf_ts > _gen_ts)
    
    print(f"  existing='{existing}'")
    print(f"  _gen_ts={_gen_ts}")
    print(f"  _pf={_pf}")
    print(f"  _pf_ts={_pf_ts}")
    print(f"  _stale={_stale}")
    print(f"  Result: {'Will reuse old clipboard' if not _stale else 'Will recompute'}")
    
    # Test case 2: _last_pipeline_final is None (the bug scenario!)
    print("\nTest 2: _last_pipeline_final is None (BUG SCENARIO)")
    class MockObj2:
        _prediction_clipboard = "期号: 2024001\n预测号码: 01234"
        _clipboard_generated_ts = 1000.0
        _last_pipeline_final = None  # NOT set yet!
        _last_pipeline_ts = 2000.0
    
    obj2 = MockObj2()
    existing = (getattr(obj2, '_prediction_clipboard', '') or '').strip()
    _gen_ts = getattr(obj2, '_clipboard_generated_ts', 0) or 0
    _pf = getattr(obj2, '_last_pipeline_final', None)
    _pf_ts = getattr(obj2, '_last_pipeline_ts', 0) or 0
    _stale = bool(existing and _pf and _pf_ts > _gen_ts)
    
    print(f"  existing='{existing}'")
    print(f"  _gen_ts={_gen_ts}")
    print(f"  _pf={_pf}")
    print(f"  _pf_ts={_pf_ts}")
    print(f"  _stale={_stale}")
    print(f"  Result: {'Will reuse old clipboard' if not _stale else 'Will recompute'}")
    print(f"  ** BUG: When _pf is None, _stale is always False, old clipboard is reused! **")
    
    # Test case 3: _last_pipeline_ts is 0 (right after task start reset)
    print("\nTest 3: _last_pipeline_ts is 0 (right after task start reset)")
    class MockObj3:
        _prediction_clipboard = "期号: 2024001\n预测号码: 01234"
        _clipboard_generated_ts = 1000.0
        _last_pipeline_final = {"target_issue": "2024001"}
        _last_pipeline_ts = 0  # Reset, not set yet
    
    obj3 = MockObj3()
    existing = (getattr(obj3, '_prediction_clipboard', '') or '').strip()
    _gen_ts = getattr(obj3, '_clipboard_generated_ts', 0) or 0
    _pf = getattr(obj3, '_last_pipeline_final', None)
    _pf_ts = getattr(obj3, '_last_pipeline_ts', 0) or 0
    _stale = bool(existing and _pf and _pf_ts > _gen_ts)
    
    print(f"  existing='{existing}'")
    print(f"  _gen_ts={_gen_ts}")
    print(f"  _pf={_pf}")
    print(f"  _pf_ts={_pf_ts}")
    print(f"  _stale={_stale}")
    print(f"  Result: {'Will reuse old clipboard' if not _stale else 'Will recompute'}")
    print(f"  ** BUG: When _pf_ts=0, _stale is False, old clipboard is reused! **")
    
    # Test case 4: Normal flow - both set correctly
    print("\nTest 4: Normal flow - both timestamps set correctly (pf_ts > gen_ts)")
    class MockObj4:
        _prediction_clipboard = "期号: 2024001\n预测号码: 01234"
        _clipboard_generated_ts = 1000.0
        _last_pipeline_final = {"target_issue": "2024001", "next_issue": "2024002"}
        _last_pipeline_ts = 3000.0  # Much newer than clipboard
    
    obj4 = MockObj4()
    existing = (getattr(obj4, '_prediction_clipboard', '') or '').strip()
    _gen_ts = getattr(obj4, '_clipboard_generated_ts', 0) or 0
    _pf = getattr(obj4, '_last_pipeline_final', None)
    _pf_ts = getattr(obj4, '_last_pipeline_ts', 0) or 0
    _stale = bool(existing and _pf and _pf_ts > _gen_ts)
    
    print(f"  existing='{existing}'")
    print(f"  _gen_ts={_gen_ts}")
    print(f"  _pf={_pf}")
    print(f"  _pf_ts={_pf_ts}")
    print(f"  _stale={_stale}")
    print(f"  Result: {'Will recompute (correct)' if _stale else 'Will reuse (may be wrong)'}")
    
    print("\n" + "=" * 70)
    print("CONCLUSION:")
    print("The staleness check has a critical flaw: when _last_pipeline_final is None,")
    print("_stale is always False, causing the old clipboard to be reused instead of")
    print("generating fresh results for the current target issue.")
    print("=" * 70)

if __name__ == '__main__':
    test_copy_prediction_staleness()