# -*- coding: utf-8 -*-
"""
智能缓存模块

职责：
    为KPLuckyNumber系统提供多层缓存能力：
    1. LFU缓存：存储高频预测结果
    2. 短期缓存：AI响应缓存（同输入同输出）
    3. 结果缓存：避免重复计算

核心策略：
    - LFU (Least Frequently Used)：长期缓存热点预测
    - TTL过期：控制缓存生命周期
    - 内存上限：防止内存泄漏
"""

import time
import hashlib
import json
import logging
from collections import OrderedDict, defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LFULogCache:
    """
    LFU缓存实现 - 用于长期存储高频预测结果

    特点：
    - O(1) 时间复杂度的get/set操作
    - 频率计数器自动更新
    - 支持过期时间
    """

    def __init__(self, max_size: int = 1000):
        """初始化 LFU 缓存。

        参数:
            max_size: 缓存条目上限，超出后淘汰访问频率最低的条目

        说明:
            freq_map 为 频率 -> OrderedDict(键) 的倒排索引，配合 min_freq 实现 O(1) 淘汰。
        """
        self.max_size = max_size
        self.freq_map = defaultdict(OrderedDict)  # freq -> OrderedDict(key)
        self.cache = {}  # key -> {value, freq, exp_at}
        self.min_freq = 0

    def _key_hash(self, key: str) -> str:
        """将任意长度的原始键压缩为 16 位十六进制短键。

        参数:
            key: 原始缓存键字符串

        返回:
            str —— MD5 摘要的前 16 位，用于降低内存占用
        """
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def get(self, key: str, default=None) -> Any:
        """读取缓存值，命中时同步提升该条目的访问频率。

        参数:
            key: 原始缓存键
            default: 未命中或已过期时返回的默认值

        返回:
            缓存的值；未命中、已过期则返回 default

        说明:
            过期条目会被顺带清理；频率提升时若旧频率桶变空且等于 min_freq，则 min_freq 自增。
        """
        hash_key = self._key_hash(key)
        if hash_key not in self.cache:
            return default

        entry = self.cache[hash_key]
        if entry['exp_at'] and time.time() > entry['exp_at']:
            self._remove(hash_key)
            return default

        # 提升频率
        freq = entry['freq']
        if hash_key in self.freq_map[freq]:
            del self.freq_map[freq][hash_key]
            if not self.freq_map[freq]:
                del self.freq_map[freq]
                if self.min_freq == freq:
                    self.min_freq += 1

        entry['freq'] += 1
        self.freq_map[entry['freq']][hash_key] = True

        return entry['value']

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """写入缓存值，容量不足时先淘汰最低频条目。

        参数:
            key: 原始缓存键
            value: 待缓存的值
            ttl: 存活秒数，None 表示不过期

        说明:
            重复写入同一键会先移除旧条目，因此新条目频率从 1 重新计数，min_freq 同步置 1。
        """
        hash_key = self._key_hash(key)

        # 容量检查
        if hash_key in self.cache:
            self._remove(hash_key)
        elif len(self.cache) >= self.max_size:
            self._evict()

        exp_at = (time.time() + ttl) if ttl else None
        self.cache[hash_key] = {
            'value': value,
            'freq': 1,
            'exp_at': exp_at
        }
        self.freq_map[1][hash_key] = True
        self.min_freq = 1

    def _evict(self):
        """淘汰最低频率的条目"""
        if self.min_freq not in self.freq_map:
            return

        # 找到最旧的最低频条目
        hash_key, _ = next(iter(self.freq_map[self.min_freq].items()))
        self._remove(hash_key)

    def _remove(self, hash_key: str):
        """从缓存与频率倒排索引中同时移除指定条目。

        参数:
            hash_key: 经 _key_hash 压缩后的短键

        说明:
            若移除后该频率桶为空，则一并删除该桶，防止 freq_map 无限膨胀。
        """
        if hash_key not in self.cache:
            return
        entry = self.cache.pop(hash_key)
        freq = entry['freq']
        if hash_key in self.freq_map[freq]:
            del self.freq_map[freq][hash_key]
            if not self.freq_map[freq]:
                del self.freq_map[freq]

    def clear(self):
        """清空全部缓存条目与频率索引，并重置最小频率计数。"""
        self.cache.clear()
        self.freq_map.clear()
        self.min_freq = 0

    def stats(self) -> Dict:
        """返回 LFU 缓存的运行时统计。

        返回:
            dict —— 含 size（当前条目数）、max_size（上限）、
            unique_freqs（频率桶数量）、min_freq（当前最小频率）
        """
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'unique_freqs': len(self.freq_map),
            'min_freq': self.min_freq
        }


class PredictionCache:
    """
    预测结果智能缓存

    缓存策略：
    1. 短期缓存（5分钟）：相同历史数据+期号直接返回
    2. 长期缓存（1小时）：高频预测结果持久化
    3. AI响应缓存：相同prompt缓存响应
    """

    def __init__(self):
        """初始化三级预测缓存。

        说明:
            短期 LRU（100 条 / 5 分钟）用于同一次会话内的重复请求；
            长期 LFU（500 条 / 1 小时）沉淀高频热点预测；
            AI 响应 LFU（200 条 / 5 分钟）缓存相同 prompt 的模型回复。
            _hits / _misses 用于在 GUI 侧展示缓存收益。
        """
        self.short_cache = OrderedDict()  # 短期LRU
        self.long_cache = LFULogCache(max_size=500)  # 长期LFU
        self.ai_cache = LFULogCache(max_size=200)
        self.short_ttl = 300  # 5分钟
        self.long_ttl = 3600  # 1小时
        self.max_short = 100
        # 命中统计（用于GUI展示与效果评估）
        self._hits = 0
        self._misses = 0

    def _make_key(self, history_data: List[Dict], issue: str,
                  algorithm_hash: str = None) -> str:
        """生成缓存key"""
        data_hash = hashlib.md5(
            json.dumps(history_data, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        algo_hash = algorithm_hash or 'default'
        return f"{data_hash}:{issue}:{algo_hash}"

    def get_prediction(self, history_data: List[Dict], issue: str,
                       algorithm_hash: str = None) -> Optional[Dict]:
        """按「历史数据 + 期号 + 算法指纹」查询已缓存的预测结果。

        参数:
            history_data: 参与本次预测的历史开奖数据列表
            issue: 目标期号
            algorithm_hash: 算法配置指纹，用于区分不同权重方案的结果

        返回:
            命中时返回缓存的预测结果字典，未命中返回 None

        说明:
            先查长期 LFU 再查短期 LRU；短期命中会把条目移到队尾以维持 LRU 顺序。
        """
        key = self._make_key(history_data, issue, algorithm_hash)

        # 先查长期缓存
        result = self.long_cache.get(key)
        if result:
            logger.debug(f"命中长期缓存: {issue}")
            self._hits += 1
            return result

        # 再查短期缓存
        if key in self.short_cache:
            entry = self.short_cache.pop(key)
            if time.time() < entry['exp_at']:
                self.short_cache[key] = entry
                logger.debug(f"命中短期缓存: {issue}")
                self._hits += 1
                return entry['value']
            else:
                del self.short_cache[key]

        self._misses += 1
        return None

    def set_prediction(self, history_data: List[Dict], issue: str,
                       result: Dict, algorithm_hash: str = None):
        """将预测结果同时写入长期 LFU 与短期 LRU 两级缓存。

        参数:
            history_data: 参与本次预测的历史开奖数据列表
            issue: 目标期号
            result: 待缓存的预测结果字典
            algorithm_hash: 算法配置指纹，用于区分不同权重方案的结果
        """
        key = self._make_key(history_data, issue, algorithm_hash)

        # 写入长期缓存（LFU）
        self.long_cache.set(key, result, ttl=self.long_ttl)

        # 同时写入短期缓存
        if len(self.short_cache) >= self.max_short:
            self.short_cache.popitem(last=False)
        self.short_cache[key] = {
            'value': result,
            'exp_at': time.time() + self.short_ttl
        }

    def get_ai_response(self, prompt: str) -> Optional[str]:
        """按 prompt 查询已缓存的 AI 响应。

        参数:
            prompt: 发送给 AI 的完整提示词

        返回:
            命中时返回缓存的响应文本，未命中返回 None
        """
        key = hashlib.md5(prompt.encode()).hexdigest()[:16]
        return self.ai_cache.get(key)

    def set_ai_response(self, prompt: str, response: str):
        """缓存 AI 响应文本，存活时间与短期缓存一致（5 分钟）。

        参数:
            prompt: 发送给 AI 的完整提示词
            response: AI 返回的响应文本
        """
        key = hashlib.md5(prompt.encode()).hexdigest()[:16]
        self.ai_cache.set(key, response, ttl=self.short_ttl)

    def invalidate(self, issue: str = None):
        """使指定期号或全部缓存失效"""
        if issue is None:
            self.short_cache.clear()
            self.long_cache.clear()
            self.ai_cache.clear()
            logger.info("缓存已清除")
        else:
            # 清除指定期号（需要遍历）
            keys_to_remove = [
                k for k in self.short_cache
                if issue in str(k)
            ]
            for k in keys_to_remove:
                del self.short_cache[k]
            logger.info(f"已清除期号 {issue} 的缓存")

    def stats(self) -> Dict:
        """返回三级缓存各自的容量占用情况。

        返回:
            dict —— 含 short_cache（短期 LRU 占用/上限）、
            long_cache（长期 LFU 统计）、ai_cache（AI 响应缓存统计）
        """
        return {
            'short_cache': {
                'size': len(self.short_cache),
                'max': self.max_short
            },
            'long_cache': self.long_cache.stats(),
            'ai_cache': self.ai_cache.stats()
        }

    def summary(self) -> Dict:
        """缓存命中概览，供 GUI 展示与效果评估"""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total else 0.0
        return {
            'hit': self._hits > 0,
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': round(hit_rate, 4),
            # 每次命中等价于跳过一次完整预测（含AI调用），故 skipped_calls ≈ hits
            'skipped_calls': self._hits,
        }


class ResultCache:
    """
    结果缓存装饰器 - 用于包装预测方法

    使用示例：
        @cached_result(ttl=300, max_size=50)
        def predict(...):
            ...
    """

    def __init__(self, ttl: int = 300, max_size: int = 100):
        """初始化结果缓存装饰器。

        参数:
            ttl: 缓存条目存活秒数
            max_size: 缓存条目上限，超出后按 LRU 淘汰最旧条目
        """
        self.ttl = ttl
        self.max_size = max_size
        self._cache = OrderedDict()

    def __call__(self, func):
        """把目标函数包装为带 TTL + LRU 缓存的版本。

        参数:
            func: 被装饰的原始函数

        返回:
            包装后的函数，额外挂载 cache_clear / cache_stats 两个辅助方法
        """
        import functools
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """缓存代理逻辑：命中且未过期直接返回，否则执行原函数并回填缓存。

            返回:
                原函数的返回值（可能来自缓存）
            """
            cache_key = self._make_key(func, args, kwargs)

            # 检查缓存
            if cache_key in self._cache:
                entry = self._cache[cache_key]
                if time.time() < entry['exp_at']:
                    self._cache.move_to_end(cache_key)
                    return entry['value']
                else:
                    del self._cache[cache_key]

            # 执行函数
            result = func(*args, **kwargs)

            # 写入缓存
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[cache_key] = {
                'value': result,
                'exp_at': time.time() + self.ttl
            }

            return result

        wrapper.cache_clear = self.clear
        wrapper.cache_stats = self.stats
        return wrapper

    def _make_key(self, func, args, kwargs):
        """由函数限定名与调用参数生成稳定的缓存键。

        参数:
            func: 被装饰的原始函数
            args: 位置参数元组
            kwargs: 关键字参数字典

        返回:
            str —— 参数组合的 MD5 摘要

        说明:
            基础标量直接参与拼接；复杂对象先 JSON 序列化再取 8 位摘要，
            关键字参数按键名排序以保证同参不同序时键一致。
        """
        key_parts = [func.__qualname__]
        for arg in args:
            if isinstance(arg, (str, int, float, bool)):
                key_parts.append(str(arg))
            else:
                key_parts.append(hashlib.md5(
                    json.dumps(arg, sort_keys=True, default=str).encode()
                ).hexdigest()[:8])
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}={str(v)[:50]}")
        return hashlib.md5('='.join(key_parts).encode()).hexdigest()

    def clear(self):
        """清空装饰器持有的全部结果缓存。"""
        self._cache.clear()
        logger.info("结果缓存已清除")

    def stats(self) -> Dict:
        """返回结果缓存的运行时统计。

        返回:
            dict —— 含 size（当前条目数）、max_size（上限）、ttl（存活秒数）
        """
        return {
            'size': len(self._cache),
            'max_size': self.max_size,
            'ttl': self.ttl
        }


# 全局缓存实例
_global_cache = PredictionCache()


def get_cache() -> PredictionCache:
    """获取全局缓存实例"""
    return _global_cache


def clear_cache(issue: str = None):
    """清除缓存"""
    _global_cache.invalidate(issue)
