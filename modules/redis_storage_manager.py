"""
Redis数据存储规范与Key命名空间设计

本文档定义了排列5系统中所有Redis Key的命名规范、数据结构、过期时间等。

核心原则：
1. 统一前缀：kpluckynumber:pl5: 作为所有Key的前缀
2. 语义化命名：{模块}:{用途}:{标识符}
3. 避免覆盖：新数据先检查Key是否存在
4. Hash结构：适合存储结构化数据，方便增量更新
5. Stream结构：适合时间序列数据（如追踪记录）
6. 自动过期：根据数据类型设置合理的TTL
"""

import logging
import json
from typing import Optional, Dict, Any
from datetime import timedelta

logger = logging.getLogger(__name__)


class RedisKeyManager:
    """
    Redis Key管理器
    
    负责：
    - Key命名空间规划
    - 数据结构选择
    - 过期时间管理
    - 数据健康检查
    """
    
    # ==================== Key命名空间定义 ====================
    NAMESPACE_PREFIX = 'kpluckynumber:pl5:'
    
    # 历史开奖数据
    KEY_HISTORY_RAW = NAMESPACE_PREFIX + 'raw:{issue}'
    KEY_HISTORY_LIST = NAMESPACE_PREFIX + 'history:list'
    
    # 专家文章分析
    KEY_EXPERT_REPORT = NAMESPACE_PREFIX + 'expert_report:{article_id}'
    KEY_EXPERT_REPORT_LIST = NAMESPACE_PREFIX + 'expert_report:list:{issue}'
    KEY_EXPERT_CREDIBILITY = NAMESPACE_PREFIX + 'expert_credibility'
    
    # 走势分析
    KEY_TREND_ANALYSIS = NAMESPACE_PREFIX + 'trend_analysis:{issue}'
    KEY_TREND_RAW_DATA = NAMESPACE_PREFIX + 'trend:raw:{issue}'
    
    # 综合报告
    KEY_INTEGRATED_REPORT = NAMESPACE_PREFIX + 'integrated_report:{issue}'
    
    # 预测结果
    KEY_PREDICTION_RESULT = NAMESPACE_PREFIX + 'prediction:{issue}'
    KEY_PREDICTION_HISTORY = NAMESPACE_PREFIX + 'prediction:list'
    
    # 特征数据
    KEY_FEATURES = NAMESPACE_PREFIX + 'features:{issue}'
    KEY_FEATURE_WEIGHTS = NAMESPACE_PREFIX + 'feature_weights:version:{version}'
    
    # 在线学习
    KEY_COUNTER_EXAMPLES = NAMESPACE_PREFIX + 'counter_examples:{issue}'
    KEY_HIT_RATE_STATS = NAMESPACE_PREFIX + 'hit_rate_stats:{issue}'
    KEY_TRACKING_BOARD = NAMESPACE_PREFIX + 'tracking_board:{issue}'
    KEY_TRACKING_LIST = NAMESPACE_PREFIX + 'tracking_list'
    
    # 模型参数
    KEY_MODEL_PARAMS = NAMESPACE_PREFIX + 'model_params:version:{version}'
    KEY_MODEL_VERSION = NAMESPACE_PREFIX + 'model_version:current'
    KEY_MODEL_HISTORY = NAMESPACE_PREFIX + 'model_versions:list'
    
    # 命中率统计
    KEY_HIT_RATE_DAILY = NAMESPACE_PREFIX + 'hit_rate:daily:{date}'
    KEY_HIT_RATE_WEEKLY = NAMESPACE_PREFIX + 'hit_rate:weekly:{week}'
    KEY_HIT_RATE_MONTHLY = NAMESPACE_PREFIX + 'hit_rate:monthly:{month}'
    
    # 用户配置
    KEY_USER_CONFIG = NAMESPACE_PREFIX + 'user_config:{user_id}'
    KEY_ALGORITHM_CONFIG = NAMESPACE_PREFIX + 'algorithm_config:current'
    
    # ==================== Key过期时间 ====================
    TTL_EXPIRY_POLICY = {
        'raw_data': timedelta(days=30),           # 原始数据保留30天
        'expert_report': timedelta(days=7),        # 专家报告保留7天
        'trend_analysis': timedelta(days=7),       # 走势分析保留7天
        'integrated_report': timedelta(days=7),    # 综合报告保留7天
        'prediction': timedelta(days=90),          # 预测结果保留90天
        'features': timedelta(days=30),            # 特征数据保留30天
        'counter_example': timedelta(days=60),     # 反例保留60天
        'tracking': timedelta(days=30),            # 追踪数据保留30天
        'model_params': timedelta(days=365),       # 模型参数保留1年
        'hit_rate': timedelta(days=180),           # 命中率统计保留180天
    }
    
    def __init__(self, redis_client):
        """初始化 Redis 键管理器。

        参数:
            redis_client: 已建立连接的 Redis 客户端实例

        说明:
            统一管理 kpluckynumber:pl5:* 键空间的命名与过期策略。
        """
        self.redis = redis_client
        logger.info('Redis Key管理器初始化完成')
    
    def safe_hset(self, key: str, field: str, value: Any, 
                  ttl: Optional[timedelta] = None) -> bool:
        """
        安全的Hash设置 - 先检查Key是否存在，避免覆盖
        
        Args:
            key: Redis Key
            field: Hash字段
            value: 要存储的值
            ttl: 过期时间（可选）
            
        Returns:
            是否成功
        """
        try:
            # 仅当该 field 已存在时才跳过，避免覆盖已有字段；
            # 其余 field 仍可正常写入，防止流水线(pipeline)静默丢字段（B10）
            if self.redis.client.hexists(key, field):
                logger.warning(f'Field已存在，跳过写入: {key}.{field}')
                return False
            
            # 存储数据
            self.redis.client.hset(key, field, json.dumps(value, ensure_ascii=False) 
                                   if not isinstance(value, str) else value)
            
            # 设置过期时间
            if ttl:
                self.redis.client.expire(key, int(ttl.total_seconds()))
            
            logger.info(f'数据写入成功: {key}')
            return True
            
        except Exception as e:
            logger.error(f'写入Redis失败: {e}')
            return False
    
    def safe_hset_existed(self, key: str, field: str, value: Any,
                           ttl: Optional[timedelta] = None) -> bool:
        """
        安全的Hash设置 - 允许更新已存在的字段（不会覆盖整个Key）
        
        Args:
            key: Redis Key
            field: Hash字段
            value: 要存储的值
            ttl: 过期时间（可选）
            
        Returns:
            是否成功
        """
        try:
            # 若 Key 已作为 string 类型存在，hset 会触发 WRONGTYPE 错误被吞；
            # 写入前校验类型，已是 string 则先删除再写入 hash 字段（B11）
            if self.redis.client.type(key) == 'string':
                logger.warning(f'Key {key} 已是 string 类型，先删除再写入 hash 字段')
                self.redis.client.delete(key)

            # 即使Key不存在也能安全写入（自动创建）
            self.redis.client.hset(key, field, json.dumps(value, ensure_ascii=False)
                                   if not isinstance(value, str) else value)
            
            # 如果设置了TTL，确保Key已设置过期时间
            if ttl:
                self.redis.client.expire(key, int(ttl.total_seconds()))
            
            logger.info(f'字段更新成功: {key}.{field}')
            return True
            
        except Exception as e:
            logger.error(f'更新Redis字段失败: {e}')
            return False
    
    def stream_append(self, stream_key: str, data: Dict[str, Any],
                      max_len: int = 1000) -> bool:
        """
        追加数据到Stream - 用于时间序列数据
        
        Args:
            stream_key: Stream的Key
            data: 要追加的数据（字典形式）
            max_len: Stream最大长度（自动裁剪旧数据）
            
        Returns:
            是否成功
        """
        try:
            self.redis.client.xadd(
                stream_key,
                data,
                maxlen=max_len,
                approximate=True  # 使用近似裁剪提升性能
            )
            logger.info(f'Stream追加成功: {stream_key}')
            return True
            
        except Exception as e:
            logger.error(f'Stream追加失败: {e}')
            return False
    
    def get_ttl_remaining(self, key: str) -> Optional[int]:
        """
        获取Key剩余过期时间（秒）
        
        Args:
            key: Redis Key
            
        Returns:
            剩余秒数，-1表示永不过期，-2表示Key不存在
        """
        try:
            return self.redis.client.ttl(key)
        except Exception as e:
            logger.error(f'获取TTL失败: {e}')
            return None
    
    def health_check(self) -> Dict[str, Any]:
        """
        Redis数据健康检查
        
        Returns:
            健康检查报告
        """
        report = {
            'status': 'healthy',
            'checked_at': datetime.now().isoformat(),
            'keys_checked': 0,
            'keys_expired': 0,
            'keys_near_expiry': 0,
            'warnings': []
        }
        
        try:
            # 检查关键Key的存在性
            critical_keys = [
                self.KEY_EXPERT_CREDIBILITY,
                self.KEY_TRACKING_LIST,
            ]
            
            for key_pattern in critical_keys:
                keys = self.redis.client.keys(key_pattern.replace('*', ''))
                report['keys_checked'] += len(keys)
                
                for key in keys:
                    ttl = self.redis.client.ttl(key)
                    if ttl == -1:
                        report['warnings'].append(f'{key}: 未设置过期时间')
                    elif 0 < ttl < 3600:  # 少于1小时过期
                        report['keys_near_expiry'] += 1
                    elif ttl == 0:
                        report['keys_expired'] += 1
            
            # 检查Redis内存使用情况
            info = self.redis.client.info('memory')
            report['memory_used'] = info.get('used_memory_human', 'N/A')
            report['memory_peak'] = info.get('used_memory_peak_human', 'N/A')
            
            if report['keys_near_expiry'] > 10:
                report['status'] = 'warning'
                report['warnings'].append('大量Key将在1小时内过期，建议清理')
            
            return report
            
        except Exception as e:
            logger.error(f'健康检查失败: {e}')
            report['status'] = 'error'
            report['error'] = str(e)
            return report
    
    def cleanup_expired_data(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        清理过期数据
        
        Args:
            dry_run: True=仅报告，False=实际删除
            
        Returns:
            清理结果
        """
        result = {
            'dry_run': dry_run,
            'cleanup_date': datetime.now().isoformat(),
            'deleted_keys': 0,
            'freed_memory': 0,
            'warnings': []
        }
        
        try:
            # 查找所有过期的Key
            expired_keys = []
            all_keys = self.redis.client.keys(self.NAMESPACE_PREFIX + '*')
            
            for key in all_keys:
                ttl = self.redis.client.ttl(key)
                if ttl == 0:  # 已过期
                    expired_keys.append(key)
            
            result['total_expired'] = len(expired_keys)
            
            if not dry_run and expired_keys:
                # 实际删除
                chunk_size = 100
                for i in range(0, len(expired_keys), chunk_size):
                    chunk = expired_keys[i:i + chunk_size]
                    self.redis.client.delete(*chunk)
                    result['deleted_keys'] += len(chunk)
                    logger.info(f'已清理过期Key: {len(chunk)}个')
            else:
                result['warnings'].append(f'干运行模式：发现{len(expired_keys)}个过期Key未删除')
            
            return result
            
        except Exception as e:
            logger.error(f'清理过期数据失败: {e}')
            result['error'] = str(e)
            return result
