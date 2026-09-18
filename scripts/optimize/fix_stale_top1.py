# -*- coding: utf-8 -*-
"""全量扫描 + 修复 stale top1_match_count 记录"""
import pymysql, json

conn = pymysql.connect(host='localhost', port=3306, user='root', password='root',
                        database='lucky_number', charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

def top1_recount(pred, actual):
    """Top-1 精确重算"""
    cnt = 0
    for i, pos in enumerate(['wan','qian','bai','shi','ge']):
        nums = pred.get(pos, [])
        if isinstance(nums, dict): nums = nums.get('numbers', [])
        if nums and int(nums[0]) == actual[i]: cnt += 1
    return cnt

cur.execute("SELECT id, target_issue, predicted_numbers, actual_numbers, top1_match_count "
            "FROM p5_prediction_record WHERE verification_status='verified'")
rows = cur.fetchall()
print(f'total verified: {len(rows)}')

stale = []
for r in rows:
    pred = json.loads(r['predicted_numbers']) if r['predicted_numbers'] else {}
    actual = json.loads(r['actual_numbers']) if r['actual_numbers'] else []
    if not actual: continue
    actual = [int(x) for x in actual]
    stored = int(r['top1_match_count'] or 0)
    recalced = top1_recount(pred, actual)
    if stored != recalced:
        stale.append((r['id'], r['target_issue'], stored, recalced))

print(f'stale records (stored != recalced): {len(stale)}')
for s in stale[:30]:
    print(f'  id={s[0]} issue={s[1]} stored={s[2]} recalced={s[3]}')

# 修复
if stale:
    for sid, iss, s, c in stale:
        cur.execute("UPDATE p5_prediction_record SET top1_match_count=%s, top1_accuracy_rate=%s WHERE id=%s",
                    (c, round(c / 5 * 100, 2), sid))
    conn.commit()
    print(f'\n[FIXED] 已修复 {len(stale)} 条 stale 记录')

# 验证
cur.execute("SELECT SUM(CASE WHEN top1_match_count=0 THEN 1 ELSE 0 END) AS zero_cnt, "
            "SUM(CASE WHEN top1_match_count>0 THEN 1 ELSE 0 END) AS gt0_cnt, COUNT(*) AS total "
            "FROM p5_prediction_record WHERE verification_status='verified'")
after = cur.fetchone()
print(f"\n[验证] 修复后分布: 0命中={after['zero_cnt']} (>0={after['gt0_cnt']}) total={after['total']}")
conn.close()
