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
【只读】p5_prediction_record 数据质量红线核查
不写任何数据，仅统计并输出损坏记录清单 + Top-N 格式分布。
"""
import json
import sys
from collections import Counter, defaultdict

try:
    import pymysql
except ImportError:
    print("ERROR: pymysql not available")
    sys.exit(2)

POSITIONS = ["wan", "qian", "bai", "shi", "ge"]

DB = dict(host="localhost", port=3306, user="root", password="root",
          database="lucky_number", charset="utf8mb4")


def classify_predicted_numbers(raw):
    """返回 (format_label, detail, error)"""
    if raw is None or raw == "" or raw.strip() == "":
        return ("EMPTY", {}, "empty")
    try:
        obj = json.loads(raw)
    except Exception as e:
        return ("CORRUPT_JSON", {}, "json_error: %s" % str(e)[:80])

    if not isinstance(obj, dict):
        return ("NON_DICT", {"type": type(obj).__name__}, "non_dict")

    keys = set(obj.keys())
    pos_keys = keys & set(POSITIONS)
    if not pos_keys:
        return ("NO_POS_KEYS", {"keys": sorted(list(keys))[:10]}, "no_pos_keys")

    # 每个位置的元素类型与长度
    elem_types = set()
    lengths = {}
    bad_pos = []
    for p in POSITIONS:
        v = obj.get(p)
        if v is None:
            lengths[p] = 0
            elem_types.add("missing")
            continue
        if not isinstance(v, list):
            bad_pos.append(p)
            elem_types.add("non_list:%s" % type(v).__name__)
            lengths[p] = -1
            continue
        lengths[p] = len(v)
        if len(v) == 0:
            elem_types.add("empty_list")
        else:
            et = type(v[0]).__name__
            elem_types.add(et)
    if bad_pos:
        return ("BAD_POS_STRUCT", {"bad_pos": bad_pos, "lengths": lengths}, "bad_pos")

    # 归一化元素类型描述
    if elem_types <= {"str", "int"}:
        fmt = "list_scalar"
    elif elem_types <= {"dict"}:
        fmt = "list_dict"
    elif elem_types <= {"str", "int", "float"}:
        fmt = "list_scalar"
    else:
        fmt = "MIXED:%s" % ",".join(sorted(elem_types))

    detail = {"lengths": lengths, "elem_types": sorted(elem_types)}
    return (fmt, detail, None)


def classify_confidence(raw):
    if raw is None or raw == "" or (isinstance(raw, str) and raw.strip() == ""):
        return "EMPTY"
    try:
        obj = json.loads(raw)
    except Exception:
        return "CORRUPT_JSON"
    if isinstance(obj, dict):
        return "dict(%d keys)" % len(obj)
    if isinstance(obj, list):
        return "list(%d)" % len(obj)
    return type(obj).__name__


def main():
    conn = pymysql.connect(**DB)
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT id, report_uuid, target_issue, predicted_numbers, "
            "predicted_combinations, confidence_scores, actual_numbers, "
            "verification_status, created_at FROM p5_prediction_record "
            "ORDER BY id"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    total = len(rows)
    print("=" * 70)
    print("p5_prediction_record 数据质量核查 | 总记录数 = %d" % total)
    print("=" * 70)

    fmt_counter = Counter()
    conf_counter = Counter()
    corrupted = []          # 完全无法解析 / 结构错的记录
    topn_length_dist = Counter()   # 各记录的 Top-N 长度签名
    length_by_pos = defaultdict(Counter)  # 每个位置的长度分布
    no_actual = 0
    empty_pred = 0

    for r in rows:
        rid = r["id"]
        raw_pn = r["predicted_numbers"]
        fmt, detail, err = classify_predicted_numbers(raw_pn)
        fmt_counter[fmt] += 1

        if fmt in ("CORRUPT_JSON", "NON_DICT", "NO_POS_KEYS", "BAD_POS_STRUCT", "EMPTY"):
            if fmt == "EMPTY":
                empty_pred += 1
            corrupted.append({
                "id": rid,
                "target_issue": r["target_issue"],
                "report_uuid": r["report_uuid"],
                "format": fmt,
                "error": err,
                "raw_head": (raw_pn[:120] if isinstance(raw_pn, str) else str(raw_pn)[:120]),
                "verification_status": r["verification_status"],
                "created_at": str(r["created_at"]),
            })

        if fmt in ("list_scalar", "list_dict") or fmt.startswith("MIXED"):
            lengths = detail.get("lengths", {})
            sig = tuple(lengths.get(p, -1) for p in POSITIONS)
            topn_length_dist[sig] += 1
            for p in POSITIONS:
                length_by_pos[p][lengths.get(p, -1)] += 1

        conf = classify_confidence(r["confidence_scores"])
        conf_counter[conf] += 1

        if r["actual_numbers"] is None or str(r["actual_numbers"]).strip() == "":
            no_actual += 1

    # ---- 输出 ----
    print("\n[1] predicted_numbers 格式分布:")
    for k, v in fmt_counter.most_common():
        print("    %-14s %5d  (%.1f%%)" % (k, v, 100.0 * v / total))

    print("\n[2] confidence_scores 结构分布:")
    for k, v in conf_counter.most_common():
        print("    %-22s %5d" % (k, v))

    print("\n[3] Top-N 长度签名分布 (wan,qian,bai,shi,ge):")
    for sig, cnt in topn_length_dist.most_common():
        print("    %-22s %5d" % (str(sig), cnt))

    print("\n[4] 每个位置的长度分布:")
    for p in POSITIONS:
        dist = dict(length_by_pos[p])
        print("    %-5s: %s" % (p, dist))

    print("\n[5] 损坏/异常记录数 = %d" % len(corrupted))
    print("    其中 EMPTY = %d, 无 actual_numbers = %d" % (empty_pred, no_actual))
    print("\n[5a] 损坏记录明细 (前 30 条):")
    for c in corrupted[:30]:
        print("    id=%-6d issue=%-10s fmt=%-14s status=%-9s err=%s" % (
            c["id"], c["target_issue"], c["format"], c["verification_status"], c["error"]))
        print("        raw: %s" % c["raw_head"].replace("\n", " "))

    if len(corrupted) > 30:
        print("    ... 共 %d 条，详见 JSON" % len(corrupted))

    # 写出 JSON 供后续使用
    out = {
        "total": total,
        "fmt_counter": dict(fmt_counter),
        "conf_counter": dict(conf_counter),
        "topn_length_dist": {str(k): v for k, v in topn_length_dist.items()},
        "length_by_pos": {p: dict(length_by_pos[p]) for p in POSITIONS},
        "corrupted_count": len(corrupted),
        "empty_pred": empty_pred,
        "no_actual": no_actual,
        "corrupted": corrupted,
    }
    with open("reports/diagnostic/dq_inspect.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[6] 核查 JSON 已写出: reports/diagnostic/dq_inspect.json")


if __name__ == "__main__":
    main()
