# -*- coding: utf-8 -*-
"""
Redis到MySQL迁移使用说明

=====================================
一、迁移前准备
=====================================

1. 确保MySQL服务正常运行
   - 检查数据库连接: python -c "from modules.database import P5Database; db = P5Database(); print('Connected' if db.connect() else 'Failed')"
   - 确认数据库名称在 config.py 的 DB_CONFIG['database'] 中已配置

2. 备份Redis数据（可选但推荐）
   redis-cli --raw MIGRATE localhost 6379 "" 0 5000 KEYS kpluckynumber:pl5:* > redis_backup_$(date +%Y%m%d).rdb

3. 检查Redis数据量
   redis-cli -n 0 --scan --pattern 'kpluckynumber:pl5:*' | wc -l

=====================================
二、执行迁移
=====================================

# 方式1: 全量迁移（默认）
python scripts/migrate_redis_to_mysql.py

# 方式2: 干跑模式（只报告，不执行）
python scripts/migrate_redis_to_mysql.py --dry-run

# 方式3: 迁移后验证
python scripts/migrate_redis_to_mysql.py --verify

# 方式4: 干跑+验证
python scripts/migrate_redis_to_mysql.py --dry-run --verify

=====================================
三、迁移后验证
=====================================

1. 检查MySQL中的数据条数
   mysql -u root -p -e "SELECT COUNT(*) FROM lucky_number.p5_kv_store"

2. 随机抽样对比
   mysql -u root -p -e "SELECT key, field, LEFT(value_json, 100) as value_preview FROM lucky_number.p5_kv_store LIMIT 10;"

3. 运行存储管理器测试
   python scripts/test_mysql_storage.py

=====================================
四、配置MySQL存储管理器
=====================================

在应用代码中使用新的MySQL存储管理器替代Redis:

# 原代码
from modules.cache import CacheClient
redis_client = CacheClient()
redis_client.save_raw_data(issue, data)

# 新代码
from modules.mysql_storage_manager import MySQLStorageManager
mysql_storage = MySQLStorageManager()
mysql_storage.save_raw_data(issue, data)

=====================================
五、注意事项
=====================================

1. Redis TTL机制 vs MySQL expire_at
   - Redis: 使用EXPIRE命令设置TTL
   - MySQL: 使用expire_at字段，通过定时任务清理

2. Redis Set/ZSet vs MySQL JSON
   - Redis Set: 直接存储在kv_store表中
   - Redis ZSet: 序列化为JSON存储

3. 性能考虑
   - 首次迁移可能耗时较长（取决于数据量）
   - 建议在生产环境低峰期执行
   - 大文本内容使用MEDIUMTEXT/LONGTEXT类型

4. 兼容性
   - 新表结构与现有p5_系列表完全兼容
   - 不会删除或修改任何现有表
   - 可安全回退（保留Redis数据）

=====================================
六、故障排查
=====================================

1. 连接失败
   - 检查MySQL服务状态: systemctl status mysql
   - 检查网络连通性: telnet <host> 3306
   - 检查配置文件config.py中的DB_CONFIG

2. 权限问题
   - 确保MySQL用户有CREATE TABLE权限
   - 确保有INSERT/UPDATE/DELETE权限

3. 表结构问题
   - 检查字符集: SHOW CREATE TABLE p5_kv_store;
   - 确保使用utf8mb4字符集

=====================================
风险警告
=====================================

本系统仅用于历史数据统计研究与娱乐性模拟，排列五开奖为完全独立的随机事件，
不存在任何可精准预测的规律。请理性购彩，量力而行。
"""

print(__doc__)