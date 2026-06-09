import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.spider import QXCSpider
from modules.data_cleaner import DataCleaner
from modules.database import Database
from modules.analyzer import ProbabilityAnalyzer
from modules.report_generator import ReportGenerator

def crawl_and_store(pages=1):
    """爬取数据并存储到数据库"""
    spider = QXCSpider()
    cleaner = DataCleaner()
    database = Database()
    
    print(f'=== 爬取 {pages} 页数据 ===')
    raw_data = spider.crawl(pages=pages)
    print(f'爬取到 {len(raw_data)} 条数据')
    
    print('\n=== 数据清洗 ===')
    clean_data = cleaner.clean(raw_data)
    print(f'清洗后 {len(clean_data)} 条有效数据')
    
    print('\n=== 存储到数据库 ===')
    if database.connect():
        database.create_tables()
        count = database.insert_or_update_qxc_data(clean_data)
        total = database.get_qxc_data_count()
        print(f'成功存储 {count} 条数据，数据库共 {total} 条')
        database.disconnect()

def generate_report():
    """生成概率分析报告并存储到数据库"""
    database = Database()
    analyzer = ProbabilityAnalyzer()
    generator = ReportGenerator()
    
    if database.connect():
        data = database.query_all_qxc_data()
        database.disconnect()
        
        if data:
            print(f'分析 {len(data)} 期数据...')
            result = analyzer.calculate_probability(data)
            report_text = analyzer.generate_report(result)
            print(report_text)
            
            print('\n=== 生成报告文件 ===')
            report_result = generator.generate_full_report(result, analyzer)
            
            print('\n=== 将报告存入数据库 ===')
            report_date = datetime.now().strftime('%Y-%m-%d')
            
            if database.connect():
                database.create_tables()
                
                frequency_chart = None
                freq_path = os.path.join('reports', report_result['frequency_chart'])
                if os.path.exists(freq_path):
                    with open(freq_path, 'rb') as f:
                        frequency_chart = f.read()
                
                probability_chart = None
                prob_path = os.path.join('reports', report_result['probability_chart'])
                if os.path.exists(prob_path):
                    with open(prob_path, 'rb') as f:
                        probability_chart = f.read()
                
                success = database.insert_report(report_date, report_text, frequency_chart, probability_chart)
                if success:
                    print('报告已成功存入数据库')
                database.disconnect()
            
            print('报告生成完成！')
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
                print(f'\n--- 报告 {i+1} ---')
                print(f'日期: {report["report_date"]}')
                print(f'创建时间: {report["created_at"]}')
                print('内容预览:')
                lines = report['report_content'].split('\n')[:10]
                print('\n'.join(lines))
                print('...')
        else:
            print('数据库中没有分析报告')

def main():
    parser = argparse.ArgumentParser(description='七星彩数字概率统计分析程序')
    parser.add_argument('-c', '--crawl', action='store_true', help='爬取数据')
    parser.add_argument('-p', '--pages', type=int, default=1, help='爬取页数')
    parser.add_argument('-a', '--analyze', action='store_true', help='生成分析报告')
    parser.add_argument('-s', '--schedule', action='store_true', help='启动定时更新')
    parser.add_argument('-f', '--full', action='store_true', help='执行完整流程（爬取+分析+报告）')
    parser.add_argument('-v', '--view-reports', action='store_true', help='查看数据库中的报告')
    
    args = parser.parse_args()
    
    if args.full:
        crawl_and_store(pages=args.pages)
        generate_report()
    elif args.crawl:
        crawl_and_store(pages=args.pages)
    elif args.analyze:
        generate_report()
    elif args.schedule:
        run_scheduler()
    elif args.view_reports:
        view_reports()
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
