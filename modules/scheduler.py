import schedule
import time
import logging
import os
from datetime import datetime

# 确保日志目录存在
os.makedirs('logs', exist_ok=True)

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
    """
    数据更新调度器类
    
    负责定时执行数据爬取、清洗、存储和报告生成任务
    """
    
    def __init__(self, spider, cleaner, database, analyzer, report_generator):
        self.spider = spider
        self.cleaner = cleaner
        self.database = database
        self.analyzer = analyzer
        self.report_generator = report_generator
        self.is_running = False
    
    def update_data(self):
        """执行完整的数据更新流程"""
        logger.info('=== 开始执行数据更新任务 ===')
        
        try:
            logger.info('1. 爬取历史开奖数据...')
            raw_data = self.spider.crawl_history_data()
            logger.info(f'爬取到 {len(raw_data)} 条历史数据')
            
            if not raw_data:
                logger.warning('未爬取到数据，跳过本次更新')
                return
            
            logger.info('2. 爬取走势图数据...')
            trend_data = self.spider.crawl_trend_data(record=120)
            logger.info(f'爬取到 {len(trend_data)} 条走势图数据')
            
            logger.info('3. 清洗数据...')
            clean_data = self.cleaner.clean(raw_data)
            logger.info(f'清洗后 {len(clean_data)} 条有效数据')
            
            logger.info('4. 存储数据...')
            if self.database.connect():
                self.database.create_tables()
                count = self.database.insert_or_update_history_data(clean_data)
                logger.info(f'成功存储 {count} 条开奖数据')
                
                if trend_data:
                    trend_count = self.database.insert_or_update_trend_data(trend_data)
                    logger.info(f'成功存储 {trend_count} 条走势图数据')
                
                self.database.disconnect()
            
            logger.info('5. 生成分析报告...')
            if self.database.connect():
                data = self.database.query_all_qxc_data()
                self.database.disconnect()
                
                if data:
                    result = self.analyzer.calculate_probability(data)
                    
                    # 生成详细报告
                    detailed_report = self.report_generator.generate_detailed_report(result, self.analyzer)
                    logger.info('详细分析报告生成完成')
                    
                    # 生成最优报告
                    optimal_report = self.report_generator.generate_optimal_report(result)
                    logger.info('最终最优报告生成完成')
                    
                    # 存储报告到数据库
                    if self.database.connect():
                        self.database.create_tables()
                        
                        self.database.insert_detailed_report(
                            detailed_report['report_content'],
                            detailed_report.get('total_samples', 0),
                            detailed_report.get('frequency_analysis', ''),
                            detailed_report.get('probability_analysis', ''),
                            detailed_report.get('interval_analysis', ''),
                            detailed_report.get('frequency_chart'),
                            detailed_report.get('probability_chart')
                        )
                        
                        self.database.insert_optimal_report(
                            optimal_report['report_content'],
                            optimal_report.get('recommended_numbers', ''),
                            optimal_report.get('confidence_score', 0.0),
                            optimal_report.get('analysis_summary', ''),
                            optimal_report.get('frequency_chart'),
                            optimal_report.get('probability_chart')
                        )
                        
                        self.database.disconnect()
                        logger.info('报告已成功存储到数据库')
            
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
