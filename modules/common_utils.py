"""
公共工具模块

提供通用的工具函数，减少代码重复
"""

import os
import logging
import time
import random
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import hashlib

# 配置日志
logger = logging.getLogger(__name__)


class CommonUtils:
    """通用工具类"""
    
    @staticmethod
    def setup_logger(name: str, log_file: str, level: int = logging.INFO) -> logging.Logger:
        """
        配置日志记录器
        
        Args:
            name: 日志记录器名称
            log_file: 日志文件路径
            level: 日志级别
        
        Returns:
            配置好的日志记录器
        """
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(level)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            
            # 确保日志目录存在
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        return logger
    
    @staticmethod
    def ensure_dir(directory: str) -> bool:
        """
        确保目录存在
        
        Args:
            directory: 目录路径
        
        Returns:
            是否成功创建
        """
        try:
            if not os.path.exists(directory):
                os.makedirs(directory)
            return True
        except Exception as e:
            logger.error(f'创建目录失败 {directory}: {e}')
            return False
    
    @staticmethod
    def generate_uuid() -> str:
        """生成唯一标识符"""
        import uuid
        return str(uuid.uuid4())
    
    @staticmethod
    def calculate_hash(data: str) -> str:
        """
        计算数据的哈希值
        
        Args:
            data: 输入数据
        
        Returns:
            MD5哈希值
        """
        return hashlib.md5(data.encode('utf-8')).hexdigest()
    
    @staticmethod
    def format_datetime(dt: Optional[datetime] = None, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
        """
        格式化日期时间
        
        Args:
            dt: datetime对象，默认为当前时间
            fmt: 格式字符串
        
        Returns:
            格式化后的字符串
        """
        if dt is None:
            dt = datetime.now()
        return dt.strftime(fmt)
    
    @staticmethod
    def parse_datetime(date_str: str, fmt: str = '%Y-%m-%d %H:%M:%S') -> Optional[datetime]:
        """
        解析日期时间字符串
        
        Args:
            date_str: 日期时间字符串
            fmt: 格式字符串
        
        Returns:
            datetime对象
        """
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            return None


class NetworkUtils:
    """网络工具类"""
    
    # 常用User-Agent列表
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36'
    ]
    
    @staticmethod
    def get_random_user_agent() -> str:
        """获取随机User-Agent"""
        return random.choice(NetworkUtils.USER_AGENTS)
    
    @staticmethod
    def random_delay(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        """
        随机延迟
        
        Args:
            min_seconds: 最小延迟秒数
            max_seconds: 最大延迟秒数
        """
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
    
    @staticmethod
    def build_request_headers(base_headers: Optional[Dict] = None) -> Dict[str, str]:
        """
        构建请求头
        
        Args:
            base_headers: 基础请求头
        
        Returns:
            完整的请求头字典
        """
        headers = {
            'User-Agent': NetworkUtils.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        if base_headers:
            headers.update(base_headers)
        
        return headers


class DataUtils:
    """数据处理工具类"""
    
    @staticmethod
    def safe_int(value: Any, default: int = 0) -> int:
        """
        安全转换为整数
        
        Args:
            value: 输入值
            default: 默认值
        
        Returns:
            整数值
        """
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        """
        安全转换为浮点数
        
        Args:
            value: 输入值
            default: 默认值
        
        Returns:
            浮点数值
        """
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def safe_str(value: Any, default: str = '') -> str:
        """
        安全转换为字符串
        
        Args:
            value: 输入值
            default: 默认值
        
        Returns:
            字符串值
        """
        try:
            return str(value).strip()
        except Exception:
            return default
    
    @staticmethod
    def normalize_numbers(numbers: List[Any]) -> List[int]:
        """
        标准化号码列表
        
        Args:
            numbers: 号码列表
        
        Returns:
            标准化后的整数列表
        """
        result = []
        for num in numbers:
            result.append(DataUtils.safe_int(num, 0))
        return result
    
    @staticmethod
    def calculate_sum(numbers: List[int]) -> int:
        """
        计算和值
        
        Args:
            numbers: 号码列表
        
        Returns:
            和值
        """
        return sum(numbers)
    
    @staticmethod
    def calculate_span(numbers: List[int]) -> int:
        """
        计算跨度
        
        Args:
            numbers: 号码列表
        
        Returns:
            跨度值
        """
        if not numbers:
            return 0
        return max(numbers) - min(numbers)
    
    @staticmethod
    def count_odd_even(numbers: List[int]) -> Tuple[int, int]:
        """
        统计奇偶数
        
        Args:
            numbers: 号码列表
        
        Returns:
            (奇数个数, 偶数个数)
        """
        odd_count = sum(1 for n in numbers if n % 2 == 1)
        even_count = len(numbers) - odd_count
        return odd_count, even_count
    
    @staticmethod
    def count_big_small(numbers: List[int], threshold: int = 5) -> Tuple[int, int]:
        """
        统计大小数
        
        Args:
            numbers: 号码列表
            threshold: 大小分界值
        
        Returns:
            (大数个数, 小数个数)
        """
        big_count = sum(1 for n in numbers if n >= threshold)
        small_count = len(numbers) - big_count
        return big_count, small_count
    
    @staticmethod
    def has_consecutive(numbers: List[int]) -> bool:
        """
        检查是否有连号
        
        Args:
            numbers: 号码列表
        
        Returns:
            是否有连号
        """
        sorted_nums = sorted(numbers)
        for i in range(len(sorted_nums) - 1):
            if sorted_nums[i + 1] - sorted_nums[i] == 1:
                return True
        return False
    
    @staticmethod
    def count_repeats(current: List[int], previous: List[int]) -> int:
        """
        统计重号数量
        
        Args:
            current: 当前期号码
            previous: 上期号码
        
        Returns:
            重号数量
        """
        return len(set(current) & set(previous))


class ValidationUtils:
    """验证工具类"""
    
    @staticmethod
    def validate_issue(issue: str) -> bool:
        """
        验证期号格式
        
        Args:
            issue: 期号字符串
        
        Returns:
            是否有效
        """
        if not issue:
            return False
        
        # 期号格式：年份+期数，如2026001
        pattern = r'^\d{7}$'
        return bool(re.match(pattern, issue)) if 're' in dir() else len(issue) == 7 and issue.isdigit()
    
    @staticmethod
    def validate_numbers(numbers: List[int], count: int = 5, min_val: int = 0, max_val: int = 9) -> bool:
        """
        验证号码列表
        
        Args:
            numbers: 号码列表
            count: 期望数量
            min_val: 最小值
            max_val: 最大值
        
        Returns:
            是否有效
        """
        if not numbers or len(numbers) != count:
            return False
        
        return all(min_val <= n <= max_val for n in numbers)
    
    @staticmethod
    def validate_date(date_str: str) -> bool:
        """
        验证日期格式
        
        Args:
            date_str: 日期字符串
        
        Returns:
            是否有效
        """
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except ValueError:
            return False


# 导入re模块用于验证
import re


def test_utils():
    """测试工具函数"""
    print('=== 测试通用工具 ===')
    
    # 测试日期格式化
    print(f'当前时间: {CommonUtils.format_datetime()}')
    
    # 测试号码处理
    numbers = [1, 2, 3, 4, 5]
    print(f'和值: {DataUtils.calculate_sum(numbers)}')
    print(f'跨度: {DataUtils.calculate_span(numbers)}')
    print(f'奇偶比: {DataUtils.count_odd_even(numbers)}')
    print(f'大小比: {DataUtils.count_big_small(numbers)}')
    
    # 测试User-Agent
    print(f'随机UA: {NetworkUtils.get_random_user_agent()[:50]}...')
    
    print('\n测试完成!')


if __name__ == '__main__':
    test_utils()
