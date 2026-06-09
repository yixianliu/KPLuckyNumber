import schedule
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scheduler.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DataUpdater:
    def __init__(self, spider, cleaner, database, analyzer, report_generator):
        self.spider = spider
        self.cleaner = cleaner
        self.database = database
        self.analyzer = analyzer
        self.report_generator = report_generator
        self.is_running = False
    
    def update_data(self):
        logger.info('=== 开始执行数据更新任务 ===')
        
        try:
            logger.info('1. 爬取数据...')
            raw_data = self.spider.crawl(pages=1)
            logger.info(f'爬取到 {len(raw_data)} 条数据')
            
            if not raw_data:
                logger.warning('未爬取到数据，跳过本次更新')
                return
            
            logger.info('2. 清洗数据...')
            clean_data = self.cleaner.clean(raw_data)
            logger.info(f'清洗后 {len(clean_data)} 条有效数据')
            
            logger.info('3. 存储数据...')
            if self.database.connect():
                count = self.database.insert_or_update(clean_data)
                logger.info(f'成功存储 {count} 条数据')
                self.database.disconnect()
            
            logger.info('4. 生成分析报告...')
            if self.database.connect():
                data = self.database.query_all()
                self.database.disconnect()
                
                if data:
                    result = self.analyzer.calculate_probability(data)
                    self.report_generator.generate_full_report(result, self.analyzer)
                    logger.info('分析报告生成完成')
            
            logger.info('=== 数据更新任务执行完成 ===')
            
        except Exception as e:
            logger.error(f'数据更新任务执行失败: {e}')
    
    def schedule_daily_update(self, time_str='08:00'):
        schedule.every().day.at(time_str).do(self.update_data)
        logger.info(f'已设置每日 {time_str} 自动更新数据')
    
    def run(self):
        self.is_running = True
        logger.info('定时任务调度器已启动')
        
        while self.is_running:
            schedule.run_pending()
            time.sleep(60)
    
    def stop(self):
        self.is_running = False
        logger.info('定时任务调度器已停止')

if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from modules.spider import QXCSpider
    from modules.data_cleaner import DataCleaner
    from modules.database import Database
    from modules.analyzer import ProbabilityAnalyzer
    from modules.report_generator import ReportGenerator
    
    spider = QXCSpider()
    cleaner = DataCleaner()
    database = Database()
    analyzer = ProbabilityAnalyzer()
    report_generator = ReportGenerator()
    
    updater = DataUpdater(spider, cleaner, database, analyzer, report_generator)
    
    print('定时更新任务测试（立即执行一次）')
    updater.update_data()
    print('测试完成')
