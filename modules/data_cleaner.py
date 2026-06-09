import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_cleaner.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DataCleaner:
    def __init__(self):
        self.valid_range = set('0123456789')
        self.special_range = set('0123456789')
    
    def _validate_issue(self, issue):
        if not issue:
            return False
        if not issue.isdigit():
            return False
        if len(issue) != 7:
            return False
        return True
    
    def _validate_date(self, date):
        if not date:
            return False
        parts = date.split('-')
        if len(parts) != 3:
            return False
        try:
            year, month, day = map(int, parts)
            if year < 2000 or year > 2100:
                return False
            if month < 1 or month > 12:
                return False
            if day < 1 or day > 31:
                return False
            return True
        except ValueError:
            return False
    
    def _validate_numbers(self, numbers):
        if not numbers or len(numbers) != 7:
            return False
        
        for i, num in enumerate(numbers[:6]):
            if not num.isdigit() or int(num) < 0 or int(num) > 9:
                return False
        
        special_num = numbers[6]
        if not special_num.isdigit():
            return False
        
        return True
    
    def clean(self, raw_data):
        clean_data = []
        issues_seen = set()
        invalid_count = 0
        
        for item in raw_data:
            try:
                issue = item.get('issue', '')
                date = item.get('date', '')
                numbers = item.get('numbers', [])
                
                if issue in issues_seen:
                    logger.warning(f'重复期号: {issue}')
                    invalid_count += 1
                    continue
                
                if not self._validate_issue(issue):
                    logger.warning(f'无效期号: {issue}')
                    invalid_count += 1
                    continue
                
                if not self._validate_date(date):
                    logger.warning(f'无效日期: {date}')
                    invalid_count += 1
                    continue
                
                if not self._validate_numbers(numbers):
                    logger.warning(f'无效号码: {numbers}')
                    invalid_count += 1
                    continue
                
                clean_item = {
                    'issue': issue,
                    'date': date,
                    'numbers': [str(n) for n in numbers]
                }
                
                clean_data.append(clean_item)
                issues_seen.add(issue)
                
            except Exception as e:
                logger.error(f'处理数据项失败: {e}')
                invalid_count += 1
        
        logger.info(f'数据清洗完成: 原始数据 {len(raw_data)} 条, 有效数据 {len(clean_data)} 条, 无效数据 {invalid_count} 条')
        return clean_data
    
    def remove_duplicates(self, data):
        seen = set()
        result = []
        for item in data:
            key = item['issue']
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

if __name__ == '__main__':
    from modules.spider import QXCSpider
    
    spider = QXCSpider()
    raw_data = spider.crawl(pages=1)
    
    cleaner = DataCleaner()
    clean_data = cleaner.clean(raw_data)
    
    print(f'清洗后数据: {len(clean_data)} 条')
    if clean_data:
        print('前5条清洗后数据:')
        for item in clean_data[:5]:
            print(f"期号: {item['issue']}, 日期: {item['date']}, 号码: {' '.join(item['numbers'])}")
