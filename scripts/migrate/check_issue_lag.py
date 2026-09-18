# -*- coding: utf-8 -*-
"""
期号滞后检测验证脚本

验证目标：
1. 检测当前预测是否滞后
2. 验证期号推导逻辑正确性
3. 提供手动触发预测的指引
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_CONFIG
import pymysql


def check_issue_lag():
    """检查预测期号是否滞后"""
    print("=" * 70)
    print("排列5 预测期号滞后检测")
    print("=" * 70)

    try:
        conn = pymysql.connect(**DB_CONFIG)
        cur = conn.cursor(pymysql.cursors.DictCursor)

        # 1. 获取最新历史期号
        cur.execute('SELECT issue, draw_date FROM p5_history_data ORDER BY issue DESC LIMIT 1')
        latest_hist = cur.fetchone()

        if not latest_hist:
            print("❌ 数据库中无历史数据")
            conn.close()
            return False

        latest_issue = latest_hist['issue']
        draw_date = latest_hist['draw_date']
        print(f"✓ 最新历史期号: {latest_issue} (开奖日期: {draw_date})")

        # 2. 计算应预测期号
        expected_prediction = str(int(latest_issue) + 1)
        print(f"✓ 应预测期号: {expected_prediction}")

        # 3. 获取最新预测期号
        cur.execute('SELECT target_issue, verification_status, created_at FROM p5_prediction_record ORDER BY target_issue DESC LIMIT 1')
        latest_pred = cur.fetchone()

        if latest_pred:
            latest_pred_issue = latest_pred['target_issue']
            status = latest_pred['verification_status']
            created_at = latest_pred['created_at']
            print(f"✓ 最新预测期号: {latest_pred_issue} (状态: {status}, 创建时间: {created_at})")

            # 4. 检测滞后
            if int(latest_pred_issue) < int(expected_prediction):
                lag = int(expected_prediction) - int(latest_pred_issue)
                print(f"⚠ 检测到预测滞后 {lag} 期!")
                print(f"  建议: 立即点击「开始分析」预测期号 {expected_prediction}")
                result = False
            elif int(latest_pred_issue) == int(expected_prediction):
                print(f"✓ 预测期号与最新历史期号同步，无滞后")
                result = True
            else:
                print(f"⚠ 预测期号超前于历史期号，数据可能异常")
                result = False
        else:
            print("⚠ 数据库中无预测记录")
            print(f"  建议: 立即点击「开始分析」预测期号 {expected_prediction}")
            result = False

        # 5. 显示最近几期预测情况
        print("\n最近5期预测记录:")
        cur.execute('''
            SELECT target_issue, verification_status, created_at
            FROM p5_prediction_record
            ORDER BY target_issue DESC
            LIMIT 5
        ''')
        records = cur.fetchall()
        for r in records:
            status_icon = "✓" if r['verification_status'] == 'verified' else "○"
            print(f"  {status_icon} 期号 {r['target_issue']}: {r['verification_status']} ({r['created_at']})")

        conn.close()
        return result

    except Exception as e:
        print(f"❌ 检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def suggest_next_action(expected_issue):
    """提供下一步操作建议"""
    print("\n" + "=" * 70)
    print("操作建议")
    print("=" * 70)
    print(f"""
当前状态:
  • 最新历史期号: {expected_issue[:7]} (假设)
  • 应预测期号: {expected_issue}

建议操作:
  1. 打开排列5 AI智能分析系统 GUI
  2. 点击「智能分析中心」卡片
  3. 点击「 开始分析」按钮
  4. 等待 2-5 分钟完成预测
  5. 查看预测结果仪表盘

预期输出:
  • 预测期号: {expected_issue}
  • 各位置 Top-3 候选号码
  • 推荐组合及置信度
  • 命中率统计（如有历史验证数据）
""")


if __name__ == '__main__':
    success = check_issue_lag()

    # 获取数据库最新期号用于建议
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
        row = cur.fetchone()
        conn.close()
        if row:
            expected = str(int(row[0]) + 1)
            suggest_next_action(expected)
    except:
        pass

    print("=" * 70)
    if success:
        print("✓ 检测完成: 预测期号同步正常")
    else:
        print("⚠ 检测完成: 发现预测滞后，请执行上述建议操作")
    print("=" * 70)
