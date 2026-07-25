# -*- coding: utf-8 -*-
# --- 路径锚定(B3修复): 向上搜索项目根(modules/+main.py), 注入 sys.path ---
import os
import sys
def _find_project_root(_start):
    _cur = os.path.abspath(_start)
    while True:
        if os.path.isdir(os.path.join(_cur, 'modules')) and \
           os.path.isfile(os.path.join(_cur, 'main.py')):
            return _cur
        _p = os.path.dirname(_cur)
        if _p == _cur:
            return os.path.dirname(os.path.abspath(_start))
        _cur = _p
_PROJECT_ROOT = _find_project_root(__file__)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

"""
【可回滚】p5_prediction_record 数据质量红线修复
步骤:
  1. 全表备份 -> p5_prediction_record_bak_20260719 (可一键还原)
  2. 11 条嵌套旧格式 -> 扁平列表 (号码/置信度无损恢复)
  3. 1 条全空记录 -> 规范空 + verification_status='failed' 隔离 (不编造数据)
  4. 重跑核查确认 0 嵌套 / 0 全空 / 全部扁平可读
Top-N 漂移: 不截断不伪造(避免改历史), 仅统一格式并归一口径(取每位置前5), 报告中透明说明。
"""
import json
import sys
from collections import Counter

import pymysql

POSITIONS = ["wan", "qian", "bai", "shi", "ge"]
DB = dict(host="localhost", port=3306, user="root", password="root",
          database="lucky_number", charset="utf8mb4")
BAK = "p5_prediction_record_bak_20260719"


def to_flat(raw):
    """把任意已知格式转为规范扁平 dict {pos:[int,...]}。
    返回 (flat_dict, confidence_dict_or_None, changed_bool, reason)。"""
    if raw is None or raw == "":
        return None, None, False, "empty_raw"
    try:
        obj = json.loads(raw)
    except Exception:
        return None, None, False, "corrupt_json"

    if not isinstance(obj, dict):
        return None, None, False, "non_dict"

    flat = {}
    conf = {}
    changed = False
    for p in POSITIONS:
        v = obj.get(p)
        if v is None:
            flat[p] = []
            continue
        if isinstance(v, dict):
            # 旧嵌套格式: {numbers:[...], confidence:[...], reason:...}
            nums = v.get("numbers", []) or []
            cf = v.get("confidence", []) or []
            flat[p] = [int(n) for n in nums if n is not None]
            if cf:
                conf[p] = [float(c) for c in cf if c is not None]
            changed = True
        elif isinstance(v, list):
            flat[p] = [int(n) for n in v if n is not None]
            # 置信度若本就在扁平 list 里则不复制
        else:
            flat[p] = []
            changed = True
    return flat, (conf if conf else None), changed, "ok"


def main():
    dry = "--dry" in sys.argv
    conn = pymysql.connect(**DB)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)

        # ---- 1. 备份 ----
        if not dry:
            cur.execute("DROP TABLE IF EXISTS `%s`" % BAK)
            cur.execute(
                "CREATE TABLE `%s` AS SELECT * FROM p5_prediction_record" % BAK)
            conn.commit()
            print("[备份] 已创建 %s (全量快照, 可回滚)" % BAK)
        else:
            print("[DRY-RUN] 不写备份/不修改数据")

        # ---- 2. 读取全部 ----
        cur.execute(
            "SELECT id, report_uuid, target_issue, predicted_numbers, "
            "confidence_scores, actual_numbers, verification_status "
            "FROM p5_prediction_record ORDER BY id")
        rows = cur.fetchall()
        total = len(rows)
        print("[读取] 总记录 %d" % total)

        converted = []      # 嵌套->扁平
        quarantined = []    # 全空隔离
        skipped = 0
        for r in rows:
            rid = r["id"]
            flat, conf, changed, reason = to_flat(r["predicted_numbers"])

            if flat is None:
                # 无法解析 -> 隔离
                quarantined.append((rid, reason))
                if not dry:
                    cur.execute(
                        "UPDATE p5_prediction_record SET predicted_numbers=%s, "
                        "verification_status='failed', deviation_analysis=%s "
                        "WHERE id=%s",
                        (json.dumps({p: [] for p in POSITIONS}, ensure_ascii=False),
                         "数据质量修复(2026-07-19): 原predicted_numbers无法解析(%s), 已隔离, 不编造数据" % reason,
                         rid))
                continue

            all_empty = all(len(flat[p]) == 0 for p in POSITIONS)
            if all_empty:
                quarantined.append((rid, "all_empty"))
                if not dry:
                    cur.execute(
                        "UPDATE p5_prediction_record SET predicted_numbers=%s, "
                        "verification_status='failed', deviation_analysis=%s "
                        "WHERE id=%s",
                        (json.dumps({p: [] for p in POSITIONS}, ensure_ascii=False),
                         "数据质量修复(2026-07-19): 原predicted_numbers五位置全空, 已隔离, 不编造数据",
                         rid))
                continue

            if changed:
                converted.append((rid, r["target_issue"]))
                new_pn = json.dumps(flat, ensure_ascii=False)
                if not dry:
                    if conf is not None:
                        new_conf = json.dumps(conf, ensure_ascii=False)
                        cur.execute(
                            "UPDATE p5_prediction_record SET predicted_numbers=%s, "
                            "confidence_scores=%s WHERE id=%s",
                            (new_pn, new_conf, rid))
                    else:
                        cur.execute(
                            "UPDATE p5_prediction_record SET predicted_numbers=%s "
                            "WHERE id=%s", (new_pn, rid))
            else:
                skipped += 1

        if not dry:
            conn.commit()
        print("[修复] 嵌套->扁平: %d 条 | 全空隔离: %d 条 | 无需改动: %d 条"
              % (len(converted), len(quarantined), skipped))

        # ---- 3. 重跑核查 (红线口径: 不可解析/非dict/仍嵌套 = 违规; 规范空=已隔离, 合法) ----
        cur.execute(
            "SELECT id, predicted_numbers FROM p5_prediction_record ORDER BY id")
        rows2 = cur.fetchall()
        bad = 0          # 不可解析/非dict
        still_nested = 0 # 仍嵌套旧格式
        flat_ok = 0      # 扁平合法
        empty_valid = 0  # 扁平但全空(隔离记录, 合法格式)
        for r in rows2:
            try:
                obj = json.loads(r["predicted_numbers"])
            except Exception:
                bad += 1
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            if any(isinstance(obj.get(p, []), dict) for p in POSITIONS):
                still_nested += 1
                continue
            flat_ok += 1
            if all(len(obj.get(p, [])) == 0 for p in POSITIONS):
                empty_valid += 1

        print("[复核] 不可解析/非dict=%d | 仍嵌套=%d | 扁平合法=%d(其中隔离空=%d)"
              % (bad, still_nested, flat_ok, empty_valid))
        redline_violation = bad + still_nested
        print("[结论] 红线修复%s: 违规数=%d (目标0) | 隔离空记录=%d (合法)"
              % ("(DRY-RUN 未落库)" if dry else "已落库", redline_violation, empty_valid))
        if redline_violation == 0:
            print("[PASS] 数据质量红线已清除 ✅")
        else:
            print("[FAIL] 仍存在 %d 处红线违规, 请检查" % redline_violation)

        if converted:
            print("\n[明细] 嵌套->扁平 id: %s" % [c[0] for c in converted])
        if quarantined:
            print("[明细] 隔离 id: %s" % [q[0] for q in quarantined])

    finally:
        conn.close()


if __name__ == "__main__":
    main()
