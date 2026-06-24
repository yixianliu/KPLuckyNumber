"""
Redis数据存储模块

实现排列5数据的Redis存储，包含：
- 合理的键名设计
- 过期策略设置
- 数据备份机制
- 高效的数据读写操作
"""

import logging
import os
import json
import redis
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/redis_client.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class RedisClient:
    """
    Redis客户端封装类

    提供排列5数据的存储、读取、备份功能
    """

    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0, password: Optional[str] = None):
        """
        初始化Redis客户端

        Args:
            host: Redis服务器地址
            port: Redis端口
            db: 数据库编号
            password: 密码
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.client = None
        self._connect()

    def _connect(self):
        """连接Redis服务器"""
        try:
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True,
                socket_timeout=10,
                socket_connect_timeout=10
            )
            self.client.ping()
            logger.info(f'Redis连接成功: {self.host}:{self.port}/db{self.db}')
        except Exception as e:
            logger.error(f'Redis连接失败: {e}')
            self.client = None

    def is_connected(self) -> bool:
        """检查Redis连接状态"""
        if self.client is None:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def get_key_prefix(self) -> str:
        """获取键名前缀"""
        return 'kpluckynumber:pl5:'

    def get_raw_data_key(self, issue: Optional[str] = None) -> str:
        """
        获取原始数据键名

        Args:
            issue: 期号，若为None则返回列表键

        Returns:
            键名
        """
        prefix = self.get_key_prefix()
        if issue:
            return f'{prefix}raw:{issue}'
        return f'{prefix}raw:list'

    def get_expert_key(self, expert_name: Optional[str] = None) -> str:
        """
        获取专家分析数据键名

        Args:
            expert_name: 专家名称，若为None则返回列表键

        Returns:
            键名
        """
        prefix = self.get_key_prefix()
        if expert_name:
            return f'{prefix}expert:{expert_name}'
        return f'{prefix}expert:list'

    def get_ai_analysis_key(self, issue: Optional[str] = None) -> str:
        """
        获取AI分析数据键名

        Args:
            issue: 期号，若为None则返回列表键

        Returns:
            键名
        """
        prefix = self.get_key_prefix()
        if issue:
            return f'{prefix}ai:{issue}'
        return f'{prefix}ai:list'

    def get_combined_key(self, issue: str) -> str:
        """
        获取综合分析数据键名

        Args:
            issue: 期号

        Returns:
            键名
        """
        prefix = self.get_key_prefix()
        return f'{prefix}combined:{issue}'

    def get_article_key(self, article_id: Optional[str] = None) -> str:
        """
        获取文章数据键名（按文章ID存储）

        Args:
            article_id: 文章唯一ID，若为None则返回列表键

        Returns:
            键名
        """
        prefix = self.get_key_prefix()
        if article_id:
            return f'{prefix}article:{article_id}'
        return f'{prefix}article:list'

    def generate_article_id(self, url: str, index: int = 0) -> str:
        """
        生成文章唯一ID

        Args:
            url: 文章URL
            index: 文章序号（用于同URL多版本区分）

        Returns:
            文章唯一ID（格式：{url_hash}_{index}）
        """
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        if index > 0:
            return f'{url_hash}_{index}'
        return url_hash

    def save_article_data(self, article_id: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """
        保存单篇文章数据到Redis（按文章ID存储）

        Args:
            article_id: 文章唯一ID
            data: 文章数据字典
            expire_days: 过期天数

        Returns:
            是否保存成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            key = self.get_article_key(article_id)
            data['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['article_id'] = article_id
            
            # 存储文章数据
            self.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=expire_days))
            
            # 添加到文章列表（有序集合，按保存时间排序）
            list_key = self.get_article_key()
            self.client.zadd(list_key, {article_id: time.time()})
            self.client.expire(list_key, timedelta(days=expire_days))
            
            # 如果有期号，也添加到期号文章索引
            if data.get('issue'):
                issue_article_key = f'{self.get_key_prefix()}issue_articles:{data["issue"]}'
                self.client.sadd(issue_article_key, article_id)
                self.client.expire(issue_article_key, timedelta(days=expire_days))
            
            logger.info(f'文章数据已保存: {key} (过期时间: {expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存文章数据失败: {e}')
            return False

    def get_article_data(self, article_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单篇文章数据

        Args:
            article_id: 文章唯一ID

        Returns:
            文章数据字典，失败返回None
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return None

        try:
            key = self.get_article_key(article_id)
            data = self.client.get(key)
            if data:
                return json.loads(data)
            logger.info(f'未找到文章数据: {key}')
            return None
            
        except Exception as e:
            logger.error(f'获取文章数据失败: {e}')
            return None

    def get_all_articles(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取所有文章数据（按保存时间倒序）

        Args:
            limit: 返回数量限制

        Returns:
            文章数据列表
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return []

        try:
            list_key = self.get_article_key()
            article_ids = self.client.zrevrange(list_key, 0, limit - 1)
            
            result = []
            for article_id in article_ids:
                data = self.get_article_data(article_id)
                if data:
                    result.append(data)
            
            logger.info(f'获取到 {len(result)} 条文章数据')
            return result
            
        except Exception as e:
            logger.error(f'获取所有文章数据失败: {e}')
            return []

    def get_articles_by_issue(self, issue: str) -> List[Dict[str, Any]]:
        """
        获取指定期号的所有文章数据

        Args:
            issue: 期号

        Returns:
            文章数据列表
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return []

        try:
            issue_article_key = f'{self.get_key_prefix()}issue_articles:{issue}'
            article_ids = self.client.smembers(issue_article_key)
            
            result = []
            for article_id in article_ids:
                data = self.get_article_data(article_id)
                if data:
                    result.append(data)
            
            logger.info(f'获取期号 {issue} 的 {len(result)} 条文章数据')
            return result
            
        except Exception as e:
            logger.error(f'获取期号文章数据失败: {e}')
            return []

    def save_raw_data(self, issue: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """
        保存原始数据到Redis

        Args:
            issue: 期号
            data: 数据字典
            expire_days: 过期天数

        Returns:
            是否保存成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            key = self.get_raw_data_key(issue)
            data['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['issue'] = issue
            
            self.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=expire_days))
            
            list_key = self.get_raw_data_key()
            self.client.zadd(list_key, {issue: time.time()})
            self.client.expire(list_key, timedelta(days=expire_days))
            
            logger.info(f'原始数据已保存: {key} (过期时间: {expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存原始数据失败: {e}')
            return False

    def get_raw_data(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        获取原始数据

        Args:
            issue: 期号

        Returns:
            数据字典，失败返回None
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return None

        try:
            key = self.get_raw_data_key(issue)
            data = self.client.get(key)
            if data:
                return json.loads(data)
            logger.info(f'未找到原始数据: {key}')
            return None
            
        except Exception as e:
            logger.error(f'获取原始数据失败: {e}')
            return None

    def get_all_raw_data(self, limit: int = 30) -> List[Dict[str, Any]]:
        """
        获取所有原始数据

        Args:
            limit: 返回数量限制

        Returns:
            数据列表
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return []

        try:
            list_key = self.get_raw_data_key()
            issues = self.client.zrevrange(list_key, 0, limit - 1)
            
            result = []
            for issue in issues:
                data = self.get_raw_data(issue)
                if data:
                    result.append(data)
            
            logger.info(f'获取到 {len(result)} 条原始数据')
            return result
            
        except Exception as e:
            logger.error(f'获取所有原始数据失败: {e}')
            return []

    def save_expert_data(self, expert_name: str, data: Dict[str, Any], expire_days: int = 3) -> bool:
        """
        保存专家分析数据

        Args:
            expert_name: 专家名称
            data: 数据字典
            expire_days: 过期天数

        Returns:
            是否保存成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            key = self.get_expert_key(expert_name)
            data['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['expert_name'] = expert_name
            
            self.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=expire_days))
            
            list_key = self.get_expert_key()
            self.client.sadd(list_key, expert_name)
            self.client.expire(list_key, timedelta(days=expire_days))
            
            logger.info(f'专家数据已保存: {key} (过期时间: {expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存专家数据失败: {e}')
            return False

    def get_expert_data(self, expert_name: str) -> Optional[Dict[str, Any]]:
        """
        获取专家分析数据

        Args:
            expert_name: 专家名称

        Returns:
            数据字典，失败返回None
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return None

        try:
            key = self.get_expert_key(expert_name)
            data = self.client.get(key)
            if data:
                return json.loads(data)
            logger.info(f'未找到专家数据: {key}')
            return None
            
        except Exception as e:
            logger.error(f'获取专家数据失败: {e}')
            return None

    def get_all_expert_data(self) -> List[Dict[str, Any]]:
        """
        获取所有专家分析数据

        Returns:
            数据列表
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return []

        try:
            list_key = self.get_expert_key()
            expert_names = self.client.smembers(list_key)
            
            result = []
            for name in expert_names:
                data = self.get_expert_data(name)
                if data:
                    result.append(data)
            
            logger.info(f'获取到 {len(result)} 条专家数据')
            return result
            
        except Exception as e:
            logger.error(f'获取所有专家数据失败: {e}')
            return []

    def save_ai_analysis(self, issue: str, data: Dict[str, Any], expire_days: int = 7) -> bool:
        """
        保存AI分析数据

        Args:
            issue: 期号
            data: 数据字典
            expire_days: 过期天数

        Returns:
            是否保存成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            key = self.get_ai_analysis_key(issue)
            data['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['issue'] = issue
            
            self.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=expire_days))
            
            list_key = self.get_ai_analysis_key()
            self.client.zadd(list_key, {issue: time.time()})
            self.client.expire(list_key, timedelta(days=expire_days))
            
            logger.info(f'AI分析数据已保存: {key} (过期时间: {expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存AI分析数据失败: {e}')
            return False

    def get_ai_analysis(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        获取AI分析数据

        Args:
            issue: 期号

        Returns:
            数据字典，失败返回None
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return None

        try:
            key = self.get_ai_analysis_key(issue)
            data = self.client.get(key)
            if data:
                return json.loads(data)
            logger.info(f'未找到AI分析数据: {key}')
            return None
            
        except Exception as e:
            logger.error(f'获取AI分析数据失败: {e}')
            return None

    def save_combined_analysis(self, issue: str, data: Dict[str, Any], expire_days: int = 14) -> bool:
        """
        保存综合分析数据

        Args:
            issue: 期号
            data: 数据字典
            expire_days: 过期天数

        Returns:
            是否保存成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            key = self.get_combined_key(issue)
            data['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['issue'] = issue
            data['expire_at'] = (datetime.now() + timedelta(days=expire_days)).strftime('%Y-%m-%d %H:%M:%S')
            
            self.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=expire_days))
            
            logger.info(f'综合分析数据已保存: {key} (过期时间: {expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存综合分析数据失败: {e}')
            return False

    def get_combined_analysis(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        获取综合分析数据

        Args:
            issue: 期号

        Returns:
            数据字典，失败返回None
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return None

        try:
            key = self.get_combined_key(issue)
            data = self.client.get(key)
            if data:
                return json.loads(data)
            logger.info(f'未找到综合分析数据: {key}')
            return None
            
        except Exception as e:
            logger.error(f'获取综合分析数据失败: {e}')
            return None

    def backup_data(self, backup_dir: str = 'backups/redis') -> str:
        """
        备份Redis数据到文件

        Args:
            backup_dir: 备份目录

        Returns:
            备份文件路径
        """
        if not self.is_connected():
            logger.error('Redis未连接，无法备份')
            return ''

        try:
            os.makedirs(backup_dir, exist_ok=True)
            
            backup_data = {
                'backup_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'redis_host': self.host,
                'redis_db': self.db,
                'raw_data': {},
                'expert_data': {},
                'ai_analysis': {},
                'combined_analysis': {}
            }
            
            raw_issues = self.client.zrevrange(self.get_raw_data_key(), 0, -1)
            for issue in raw_issues:
                data = self.get_raw_data(issue)
                if data:
                    backup_data['raw_data'][issue] = data
            
            expert_names = self.client.smembers(self.get_expert_key())
            for name in expert_names:
                data = self.get_expert_data(name)
                if data:
                    backup_data['expert_data'][name] = data
            
            ai_issues = self.client.zrevrange(self.get_ai_analysis_key(), 0, -1)
            for issue in ai_issues:
                data = self.get_ai_analysis(issue)
                if data:
                    backup_data['ai_analysis'][issue] = data
            
            pattern = f'{self.get_key_prefix()}combined:*'
            combined_keys = self.client.keys(pattern)
            for key in combined_keys:
                issue = key.split(':')[-1]
                data = self.client.get(key)
                if data:
                    backup_data['combined_analysis'][issue] = json.loads(data)
            
            filename = f'redis_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            filepath = os.path.join(backup_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f'Redis数据备份完成: {filepath}')
            return filepath
            
        except Exception as e:
            logger.error(f'备份Redis数据失败: {e}')
            return ''

    def restore_data(self, backup_file: str) -> bool:
        """
        从备份文件恢复Redis数据

        Args:
            backup_file: 备份文件路径

        Returns:
            是否恢复成功
        """
        if not self.is_connected():
            logger.error('Redis未连接，无法恢复')
            return False

        try:
            with open(backup_file, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            for issue, data in backup_data.get('raw_data', {}).items():
                self.save_raw_data(issue, data)
            
            for name, data in backup_data.get('expert_data', {}).items():
                self.save_expert_data(name, data)
            
            for issue, data in backup_data.get('ai_analysis', {}).items():
                self.save_ai_analysis(issue, data)
            
            for issue, data in backup_data.get('combined_analysis', {}).items():
                self.save_combined_analysis(issue, data)
            
            logger.info(f'Redis数据恢复完成: {backup_file}')
            return True
            
        except Exception as e:
            logger.error(f'恢复Redis数据失败: {e}')
            return False

    def clear_all_data(self) -> bool:
        """
        清空所有数据

        Returns:
            是否清空成功
        """
        if not self.is_connected():
            logger.error('Redis未连接')
            return False

        try:
            pattern = f'{self.get_key_prefix()}*'
            keys = self.client.keys(pattern)
            
            if keys:
                self.client.delete(*keys)
                logger.info(f'已清空 {len(keys)} 条数据')
            
            return True
            
        except Exception as e:
            logger.error(f'清空数据失败: {e}')
            return False


if __name__ == '__main__':
    client = RedisClient()
    if client.is_connected():
        test_data = {
            'test_key': 'test_value',
            'test_number': 12345
        }
        client.save_raw_data('2026001', test_data)
        retrieved = client.get_raw_data('2026001')
        print(f'保存并读取测试数据: {retrieved}')
        backup_file = client.backup_data()
        print(f'备份文件: {backup_file}')