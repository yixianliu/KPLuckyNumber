import logging
import os
from datetime import datetime

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/data_cleaner.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

class DataCleaner:
    """
    数据清洗器类
    
    负责验证和清洗爬取的数据，确保数据质量和格式正确性
    """
    
    def __init__(self):
        self.valid_range = set('0123456789')
        self.special_range = set('0123456789')
    
    def _validate_issue(self, issue):
        """
        验证期号格式
        
        Args:
            issue: 期号字符串
        
        Returns:
            True表示有效，False表示无效
        """
        if not issue:
            return False
        if not issue.isdigit():
            return False
        # 期号长度应为6-8位
        if len(issue) < 6 or len(issue) > 8:
            return False
        return True
    
    def _validate_date(self, date):
        """
        验证日期格式
        
        Args:
            date: 日期字符串
        
        Returns:
            True表示有效，False表示无效
        """
        if not date:
            return True
        
        # 支持多种日期格式
        date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d']
        
        for fmt in date_formats:
            try:
                datetime.strptime(date, fmt)
                return True
            except ValueError:
                continue
        
        parts = date.split('-')
        if len(parts) != 3:
            parts = date.split('/')
        
        if len(parts) == 3:
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
        
        return False
    
    def _validate_numbers(self, numbers):
        """
        验证号码格式
        
        Args:
            numbers: 号码列表
        
        Returns:
            True表示有效，False表示无效
        """
        if not numbers or len(numbers) != 7:
            return False
        
        # 验证前6个号码（0-9）
        for i, num in enumerate(numbers[:6]):
            if not str(num).isdigit():
                return False
            num_val = int(num)
            if num_val < 0 or num_val > 9:
                return False
        
        # 验证特别号码（0-14）
        special_num = numbers[6]
        if not str(special_num).isdigit():
            return False
        special_val = int(special_num)
        if special_val < 0 or special_val > 14:
            return False
        
        return True
    
    def _validate_hezhi(self, hezhi):
        """
        验证和值
        
        Args:
            hezhi: 和值字符串
        
        Returns:
            True表示有效，False表示无效
        """
        if not hezhi:
            return True
        return hezhi.isdigit()
    
    def _validate_odd_even_ratio(self, ratio):
        """
        验证奇偶比
        
        Args:
            ratio: 奇偶比字符串
        
        Returns:
            True表示有效，False表示无效
        """
        if not ratio:
            return True
        parts = ratio.split(':')
        if len(parts) != 2:
            return False
        try:
            odd, even = map(int, parts)
            return odd + even == 7
        except ValueError:
            return False
    
    def _validate_span(self, span):
        """
        验证跨度
        
        Args:
            span: 跨度字符串
        
        Returns:
            True表示有效，False表示无效
        """
        if not span:
            return True
        if not span.isdigit():
            return False
        span_val = int(span)
        return 0 <= span_val <= 9
    
    def clean(self, raw_data):
        """
        清洗数据，验证并过滤无效数据
        
        Args:
            raw_data: 原始数据列表
        
        Returns:
            清洗后的有效数据列表
        """
        clean_data = []
        issues_seen = set()
        invalid_count = 0
        
        for item in raw_data:
            try:
                issue = item.get('issue', '')
                date = item.get('date', '')
                numbers = item.get('numbers', [])
                
                # 检查重复期号
                if issue in issues_seen:
                    logger.warning(f'重复期号: {issue}')
                    invalid_count += 1
                    continue
                
                # 验证期号
                if not self._validate_issue(issue):
                    logger.warning(f'无效期号: {issue}')
                    invalid_count += 1
                    continue
                
                # 验证日期
                if not self._validate_date(date):
                    logger.warning(f'无效日期: {date}')
                    invalid_count += 1
                    continue
                
                # 验证号码
                if not self._validate_numbers(numbers):
                    logger.warning(f'无效号码: {numbers}')
                    invalid_count += 1
                    continue
                
                # 创建清洗后的条目，包含扩展字段
                clean_item = {
                    'issue': issue,
                    'date': date,
                    'numbers': [str(n) for n in numbers],
                    'hezhi': item.get('hezhi', ''),
                    'hezhi_type': item.get('hezhi_type', ''),
                    'odd_even_ratio': item.get('odd_even_ratio', ''),
                    'odd_even_pattern': item.get('odd_even_pattern', ''),
                    'span': item.get('span', '')
                }
                
                # 验证扩展字段
                if not self._validate_hezhi(clean_item['hezhi']):
                    clean_item['hezhi'] = ''
                if not self._validate_odd_even_ratio(clean_item['odd_even_ratio']):
                    clean_item['odd_even_ratio'] = ''
                if not self._validate_span(clean_item['span']):
                    clean_item['span'] = ''
                
                clean_data.append(clean_item)
                issues_seen.add(issue)
                
            except Exception as e:
                logger.error(f'处理数据项失败: {e}')
                invalid_count += 1
        
        logger.info(f'数据清洗完成: 原始数据 {len(raw_data)} 条, 有效数据 {len(clean_data)} 条, 无效数据 {invalid_count} 条')
        return clean_data
    
    def remove_duplicates(self, data):
        """
        移除重复数据
        
        Args:
            data: 数据列表
        
        Returns:
            去重后的数据列表
        """
        seen = set()
        result = []
        for item in data:
            key = item['issue']
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from modules.spider import QXCSpider
    
    spider = QXCSpider()
    raw_data = spider.crawl_history_data()
    
    cleaner = DataCleaner()
    clean_data = cleaner.clean(raw_data)
    
    print(f'清洗后数据: {len(clean_data)} 条')
    if clean_data:
        print('前5条清洗后数据:')
        for item in clean_data[:5]:
            print(f"期号: {item['issue']}, 日期: {item['date']}, 号码: {' '.join(item['numbers'])}")
            print(f"  和值: {item['hezhi']}, 奇偶比: {item['odd_even_ratio']}, 跨度: {item['span']}")
