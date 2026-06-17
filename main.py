import sys
import os
import argparse
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.spider import QXCSpider
from modules.data_cleaner import DataCleaner
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from modules.report_generator import ReportGenerator

# 排列5模块导入
from modules.spider_p5 import P5Spider
from modules.database_p5 import P5Database
from modules.analyzer_p5 import P5Analyzer
from modules.report_generator_p5 import P5ReportGenerator


def crawl_and_store(pages=1, qishu=100, trend=False):
    """爬取数据并存储到数据库"""
    spider = QXCSpider()
    cleaner = DataCleaner()
    database = Database()

    print(f'=== 爬取近 {qishu} 期历史数据 ===')
    raw_data = spider.crawl_history_data()
    print(f'爬取到 {len(raw_data)} 条历史数据')

    if trend:
        print('\n=== 获取走势图数据 ===')
        trend_data = spider.crawl_trend_data(record=120)
        print(f'获取到 {len(trend_data)} 条走势图数据')

    print('\n=== 数据清洗 ===')
    clean_data = cleaner.clean(raw_data)
    print(f'清洗后 {len(clean_data)} 条有效数据')

    print('\n=== 存储到数据库 ===')
    if database.connect():
        database.create_tables()
        count = database.insert_or_update_qxc_data(clean_data)
        total = database.get_qxc_data_count()
        print(f'成功存储 {count} 条数据，数据库共 {total} 条')

        if trend and trend_data:
            trend_count = database.insert_or_update_trend_data(trend_data)
            print(f'成功存储 {trend_count} 条走势图数据')

        database.disconnect()


def generate_report(report_types=['detailed', 'optimal']):
    """生成概率分析报告并存储到数据库（专业版）"""
    database = Database()
    analyzer = ProbabilityAnalyzer()
    generator = ReportGenerator()

    if database.connect():
        # 查询历史数据
        data = database.query_all_qxc_data()
        database.disconnect()

        if data:
            print(f'分析 {len(data)} 期历史数据...')

            # 执行综合分析
            result = analyzer.calculate_probability(data)

            for report_type in report_types:
                print(f'\n=== 生成{report_type}报告 ===')

                if report_type == 'detailed':
                    report_result = generator.generate_detailed_report(result, analyzer)
                    print('详细统计分析报告已生成')
                elif report_type == 'optimal':
                    report_result = generator.generate_optimal_report(result)
                    print('综合统计特征报告已生成')
                else:
                    print(f'不支持的报告类型: {report_type}')
                    continue

                # 显示报告内容预览
                content_preview = report_result['report_content'][:1500]
                print('\n报告预览:')
                print('-' * 80)
                print(content_preview)
                print('...')
                print('-' * 80)

                # 存储到数据库
                if database.connect():
                    database.create_tables()

                    if report_type == 'optimal':
                        # 存储综合报告
                        success = database.insert_optimal_report(
                            report_result['report_content'],
                            report_result.get('recommended_numbers', ''),
                            report_result.get('confidence_score', 0.0),
                            report_result.get('analysis_summary', ''),
                            report_result.get('frequency_chart'),
                            report_result.get('omission_chart')
                        )
                    else:
                        # 存储详细报告
                        success = database.insert_detailed_report(
                            report_result['report_content'],
                            report_result.get('total_samples', 0),
                            report_result.get('frequency_analysis', ''),
                            report_result.get('omission_analysis', ''),
                            report_result.get('interval_analysis', ''),
                            report_result.get('frequency_chart'),
                            report_result.get('omission_chart')
                        )

                    if success:
                        print(f'{report_type}报告已成功存入数据库')
                    database.disconnect()

            print('\n报告生成完成！')
        else:
            print('数据库中没有数据，请先执行爬取操作')


def run_scheduler():
    """启动定时更新调度器"""
    from modules.scheduler import DataUpdater

    spider = QXCSpider()
    cleaner = DataCleaner()
    database = Database()
    analyzer = ProbabilityAnalyzer()
    report_generator = ReportGenerator()

    updater = DataUpdater(spider, cleaner, database, analyzer, report_generator)
    updater.schedule_daily_update('08:00')

    print('定时任务调度器已启动，按 Ctrl+C 停止')
    try:
        updater.run()
    except KeyboardInterrupt:
        updater.stop()
        print('\n调度器已停止')


def view_reports():
    """查看数据库中的报告"""
    database = Database()
    if database.connect():
        reports = database.get_reports()
        database.disconnect()

        if reports:
            print(f'数据库中共有 {len(reports)} 条分析报告')
            for i, report in enumerate(reports):
                print(f'\n--- 报告 {i + 1} ---')
                print(f'日期: {report["report_date"]}')
                print(f'类型: {report.get("report_type", "unknown")}')
                print(f'创建时间: {report["created_at"]}')
                print('内容预览:')
                lines = report['report_content'].split('\n')[:15]
                print('\n'.join(lines))
                print('...')
        else:
            print('数据库中没有分析报告')


def view_data_summary():
    """查看数据概览"""
    database = Database()
    if database.connect():
        # 查询历史数据数量
        history_count = database.get_qxc_data_count()

        # 查询走势图数据数量
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_trend_data')
            trend_result = database.cursor.fetchone()
            trend_count = trend_result['count'] if trend_result else 0
        except:
            trend_count = 0

        # 查询报告数量
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_detailed_report')
            detailed_count = database.cursor.fetchone()['count']
            database.cursor.execute('SELECT COUNT(*) as count FROM qxc_final_report')
            final_count = database.cursor.fetchone()['count']
        except:
            detailed_count = 0
            final_count = 0

        database.disconnect()

        print('=' * 60)
        print('          数据概览')
        print('=' * 60)
        print(f'历史开奖数据: {history_count} 条')
        print(f'走势图数据: {trend_count} 条')
        print(f'详细分析报告: {detailed_count} 份')
        print(f'最终最优报告: {final_count} 份')
        print('=' * 60)


# ==================== 排列5相关功能 ====================

def crawl_and_store_p5(trend_record=120):
    """爬取排列5数据并存储到数据库"""
    spider = P5Spider()
    database = P5Database()

    print('=== 爬取排列5历史数据 ===')
    history_data = spider.crawl_history_data()
    print(f'爬取到 {len(history_data)} 条历史数据')

    print('\n=== 爬取排列5走势图数据 ===')
    trend_data = spider.crawl_trend_data(record=trend_record)
    print(f'爬取到 {len(trend_data)} 条走势图数据')

    print('\n=== 存储到数据库 ===')
    if database.connect():
        database.create_tables()

        # 存储历史数据
        history_count = database.insert_history_data(history_data)
        print(f'成功存储 {history_count} 条历史数据')

        # 存储走势图数据
        trend_count = database.insert_trend_data(trend_data)
        print(f'成功存储 {trend_count} 条走势图数据')

        database.disconnect()

    return history_data, trend_data


def generate_report_p5(report_types=['detailed', 'optimal'], use_trend=True):
    """生成排列5分析报告并存储到数据库"""
    database = P5Database()
    analyzer = P5Analyzer()
    generator = P5ReportGenerator()

    if database.connect():
        # 查询历史数据
        history_data = database.get_history_data()

        # 查询走势图数据
        trend_data = []
        if use_trend:
            trend_data = database.get_trend_data()
            print(f'查询到 {len(trend_data)} 条走势图数据')

        database.disconnect()

        if history_data:
            print(f'分析 {len(history_data)} 期历史数据...')
            if trend_data:
                print(f'整合 {len(trend_data)} 条走势图数据进行综合分析...')

            # 执行综合分析
            result = analyzer.calculate_probability(history_data, trend_data if trend_data else None)

            report_date = datetime.now().strftime('%Y-%m-%d')

            for report_type in report_types:
                print(f'\n=== 生成{report_type}报告 ===')

                if report_type == 'detailed':
                    report_result = generator.generate_detailed_report(result, analyzer)
                    print('详细分析报告已生成')
                elif report_type == 'optimal':
                    report_result = generator.generate_optimal_report(result)
                    print('最终最优报告已生成')
                else:
                    print(f'不支持的报告类型: {report_type}')
                    continue

                # 显示报告内容预览
                content_preview = report_result['report_content'][:1500]
                print('\n报告预览:')
                print('-' * 80)
                print(content_preview)
                print('...')
                print('-' * 80)

                # 存储到数据库
                if database.connect():
                    if report_type == 'optimal':
                        success = database.save_final_report(report_result)
                    else:
                        success = database.save_detailed_report(report_result)

                    if success:
                        print(f'{report_type}报告已成功存入数据库')
                    database.disconnect()

            print('\n报告生成完成！')
        else:
            print('数据库中没有数据，请先执行爬取操作')


def view_data_summary_p5():
    """查看排列5数据概览"""
    database = P5Database()
    if database.connect():
        # 查询历史数据数量
        database.cursor.execute('SELECT COUNT(*) as count FROM p5_history_data')
        history_result = database.cursor.fetchone()
        history_count = history_result['count'] if history_result else 0

        # 查询走势图数据数量
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM p5_trend_data')
            trend_result = database.cursor.fetchone()
            trend_count = trend_result['count'] if trend_result else 0
        except:
            trend_count = 0

        # 查询报告数量
        try:
            database.cursor.execute('SELECT COUNT(*) as count FROM p5_detailed_report')
            detailed_count = database.cursor.fetchone()['count']
            database.cursor.execute('SELECT COUNT(*) as count FROM p5_final_report')
            final_count = database.cursor.fetchone()['count']
        except:
            detailed_count = 0
            final_count = 0

        database.disconnect()

        print('=' * 60)
        print('          排列5数据概览')
        print('=' * 60)
        print(f'历史开奖数据: {history_count} 条')
        print(f'走势图数据: {trend_count} 条')
        print(f'详细分析报告: {detailed_count} 份')
        print(f'最优分析报告: {final_count} 份')
        print('=' * 60)


def run_full_p5(trend_record=120, report_types=['detailed', 'optimal']):
    """执行排列5完整流程（爬取+分析+报告）"""
    print('=' * 60)
    print('          排列5完整分析流程')
    print('=' * 60)

    # 1. 爬取数据
    crawl_and_store_p5(trend_record=trend_record)

    # 2. 生成报告
    generate_report_p5(report_types=report_types, use_trend=True)

    # 3. 显示数据概览
    view_data_summary_p5()


def main():
    parser = argparse.ArgumentParser(description='彩票数字概率统计分析程序')

    # 七星彩参数
    parser.add_argument('-c', '--crawl', action='store_true', help='爬取七星彩数据')
    parser.add_argument('-p', '--pages', type=int, default=1, help='爬取页数')
    parser.add_argument('-q', '--qishu', type=int, default=300, help='获取期数（30/50/100/300）')
    parser.add_argument('-t', '--trend', action='store_true', help='获取走势图数据')
    parser.add_argument('-a', '--analyze', action='store_true', help='生成七星彩分析报告')
    parser.add_argument('-r', '--report-types', nargs='+', default=['detailed', 'optimal'],
                        choices=['detailed', 'optimal'], help='报告类型（detailed/optimal）')
    parser.add_argument('-s', '--schedule', action='store_true', help='启动定时更新')
    parser.add_argument('-f', '--full', action='store_true', help='执行七星彩完整流程（爬取+分析+报告）')
    parser.add_argument('-v', '--view-reports', action='store_true', help='查看数据库中的报告')
    parser.add_argument('-d', '--view-data', action='store_true', help='查看数据概览')
    parser.add_argument('-nt', '--no-trend', action='store_true', help='分析时不使用走势图数据')

    # 排列5参数
    parser.add_argument('--p5-crawl', action='store_true', help='爬取排列5数据')
    parser.add_argument('--p5-analyze', action='store_true', help='生成排列5分析报告')
    parser.add_argument('--p5-full', action='store_true', help='执行排列5完整流程（爬取+分析+报告）')
    parser.add_argument('--p5-view', action='store_true', help='查看排列5数据概览')
    parser.add_argument('--trend-record', type=int, default=120, help='排列5走势图数据记录数')

    args = parser.parse_args()

    # 排列5功能
    if args.p5_full:
        run_full_p5(trend_record=args.trend_record, report_types=args.report_types)
    elif args.p5_crawl:
        crawl_and_store_p5(trend_record=args.trend_record)
    elif args.p5_analyze:
        generate_report_p5(report_types=args.report_types, use_trend=not args.no_trend)
    elif args.p5_view:
        view_data_summary_p5()
    # 七星彩功能
    elif args.full:
        crawl_and_store(qishu=args.qishu, trend=True)
        generate_report(report_types=args.report_types, use_trend=True)
    elif args.crawl:
        crawl_and_store(qishu=args.qishu, trend=args.trend)
    elif args.analyze:
        generate_report(report_types=args.report_types)
    elif args.schedule:
        run_scheduler()
    elif args.view_reports:
        view_reports()
    elif args.view_data:
        view_data_summary()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
