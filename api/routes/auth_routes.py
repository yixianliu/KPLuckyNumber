"""
用户认证与付费管理路由
适用于微信小程序场景
"""

from fastapi import APIRouter, Depends, Header
from typing import Optional
from datetime import datetime
import uuid

from modules.database import Database
from api.schemas import ApiResponse
from api.exceptions import DatabaseException, ValidationException

router = APIRouter()


# ==================== 辅助函数 ====================

def get_db():
    """获取数据库连接"""
    db = Database()
    if not db.connect():
        raise DatabaseException("数据库连接失败")
    try:
        yield db
    finally:
        db.disconnect()


def ensure_user_tables(db: Database):
    """确保用户相关表已创建"""
    db.create_user_tables()


# ==================== 用户登录/注册 ====================

@router.post("/login", summary="用户登录/注册", description="微信小程序用户登录，不存在则自动注册")
async def wx_login(
    wx_openid: str,
    wx_unionid: Optional[str] = None,
    nickname: Optional[str] = None,
    avatar_url: Optional[str] = None,
    db: Database = Depends(get_db)
):
    """
    微信小程序用户登录/注册
    
    Args:
        wx_openid: 微信用户OpenID（必填）
        wx_unionid: 微信用户UnionID（可选）
        nickname: 用户昵称（可选）
        avatar_url: 用户头像URL（可选）
    
    Returns:
        用户信息（包含user_id和token）
    """
    try:
        if not wx_openid:
            raise ValidationException("wx_openid不能为空")
        
        ensure_user_tables(db)
        
        # 查询用户是否已存在
        db.cursor.execute(
            "SELECT * FROM users WHERE wx_openid = %s",
            (wx_openid,)
        )
        user = db.cursor.fetchone()
        
        if user:
            # 用户已存在，更新登录信息
            token = str(uuid.uuid4())
            db.cursor.execute(
                """
                UPDATE users 
                SET last_login_at = NOW(), 
                    login_count = login_count + 1,
                    access_token = %s,
                    token_expire_at = DATE_ADD(NOW(), INTERVAL 7 DAY)
                WHERE id = %s
                """,
                (token, user['id'])
            )
            db.connection.commit()
            
            return ApiResponse(
                success=True,
                code=200,
                message="登录成功",
                data={
                    "user_id": user['id'],
                    "wx_openid": user['wx_openid'],
                    "nickname": user['nickname'],
                    "avatar_url": user['avatar_url'],
                    "is_new_user": False,
                    "access_token": token,
                    "token_expire_at": datetime.now().isoformat(),
                    "created_at": user['created_at'].isoformat() if user.get('created_at') else None
                }
            )
        else:
            # 用户不存在，自动注册
            token = str(uuid.uuid4())
            db.cursor.execute(
                """
                INSERT INTO users 
                (wx_openid, wx_unionid, nickname, avatar_url, access_token, token_expire_at, last_login_at, login_count)
                VALUES (%s, %s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL 7 DAY), NOW(), 1)
                """,
                (wx_openid, wx_unionid, nickname, avatar_url, token)
            )
            db.connection.commit()
            
            # 获取新创建的用户
            db.cursor.execute(
                "SELECT * FROM users WHERE wx_openid = %s",
                (wx_openid,)
            )
            new_user = db.cursor.fetchone()
            
            return ApiResponse(
                success=True,
                code=201,
                message="注册并登录成功",
                data={
                    "user_id": new_user['id'],
                    "wx_openid": new_user['wx_openid'],
                    "nickname": new_user['nickname'],
                    "avatar_url": new_user['avatar_url'],
                    "is_new_user": True,
                    "access_token": token,
                    "token_expire_at": datetime.now().isoformat(),
                    "created_at": new_user['created_at'].isoformat() if new_user.get('created_at') else None
                }
            )
    
    except ValidationException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"登录/注册失败: {str(e)}", str(e))


@router.get("/profile", summary="获取用户信息", description="根据用户ID或Token获取用户信息")
async def get_user_profile(
    user_id: Optional[int] = None,
    x_token: Optional[str] = Header(None, alias="X-Token"),
    db: Database = Depends(get_db)
):
    """
    获取用户信息
    
    Args:
        user_id: 用户ID（可选，与token二选一）
        x_token: 用户访问令牌（可选，与user_id二选一）
    
    Returns:
        用户信息
    """
    try:
        ensure_user_tables(db)
        
        if user_id:
            db.cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        elif x_token:
            db.cursor.execute(
                "SELECT * FROM users WHERE access_token = %s AND token_expire_at > NOW()",
                (x_token,)
            )
        else:
            raise ValidationException("请提供user_id或X-Token")
        
        user = db.cursor.fetchone()
        
        if not user:
            return ApiResponse(
                success=False,
                code=404,
                message="用户未找到或token已过期",
                data=None
            )
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "user_id": user['id'],
                "wx_openid": user['wx_openid'],
                "nickname": user['nickname'],
                "avatar_url": user['avatar_url'],
                "login_count": user['login_count'],
                "last_login_at": user['last_login_at'].isoformat() if user.get('last_login_at') else None,
                "created_at": user['created_at'].isoformat() if user.get('created_at') else None
            }
        )
    
    except ValidationException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"查询用户信息失败: {str(e)}", str(e))


# ==================== 付费记录管理 ====================

@router.post("/payment", summary="创建付费记录", description="记录用户付费信息")
async def create_payment(
    user_id: int,
    amount: float,
    payment_type: str,
    order_no: Optional[str] = None,
    transaction_id: Optional[str] = None,
    description: Optional[str] = None,
    db: Database = Depends(get_db)
):
    """
    创建用户付费记录
    
    Args:
        user_id: 用户ID
        amount: 付费金额（元）
        payment_type: 付费类型（如：report_view, vip_month, vip_year）
        order_no: 商户订单号（可选）
        transaction_id: 微信支付交易号（可选）
        description: 付费描述（可选）
    
    Returns:
        创建的付费记录
    """
    try:
        if amount <= 0:
            raise ValidationException("付费金额必须大于0")
        
        if not payment_type:
            raise ValidationException("付费类型不能为空")
        
        ensure_user_tables(db)
        
        # 检查用户是否存在
        db.cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not db.cursor.fetchone():
            return ApiResponse(
                success=False,
                code=404,
                message="用户不存在",
                data=None
            )
        
        # 生成订单号
        if not order_no:
            order_no = f"QXC{datetime.now().strftime('%Y%m%d%H%M%S')}{user_id}"
        
        db.cursor.execute(
            """
            INSERT INTO payment_records 
            (user_id, order_no, transaction_id, amount, payment_type, status, description, paid_at)
            VALUES (%s, %s, %s, %s, %s, 'success', %s, NOW())
            """,
            (user_id, order_no, transaction_id, amount, payment_type, description)
        )
        db.connection.commit()
        
        # 获取刚插入的记录
        db.cursor.execute(
            "SELECT * FROM payment_records WHERE order_no = %s",
            (order_no,)
        )
        record = db.cursor.fetchone()
        
        return ApiResponse(
            success=True,
            code=201,
            message="付费记录创建成功",
            data={
                "id": record['id'],
                "user_id": record['user_id'],
                "order_no": record['order_no'],
                "transaction_id": record['transaction_id'],
                "amount": float(record['amount']),
                "payment_type": record['payment_type'],
                "status": record['status'],
                "description": record['description'],
                "paid_at": record['paid_at'].isoformat() if record.get('paid_at') else None
            }
        )
    
    except ValidationException as e:
        raise e
    except Exception as e:
        raise DatabaseException(f"创建付费记录失败: {str(e)}", str(e))


@router.get("/payments", summary="查询用户付费记录", description="查询指定用户的所有付费记录")
async def get_user_payments(
    user_id: int,
    payment_type: Optional[str] = None,
    db: Database = Depends(get_db)
):
    """
    查询用户付费记录
    
    Args:
        user_id: 用户ID
        payment_type: 付费类型过滤（可选）
    
    Returns:
        付费记录列表
    """
    try:
        ensure_user_tables(db)
        
        sql = "SELECT * FROM payment_records WHERE user_id = %s"
        params = [user_id]
        
        if payment_type:
            sql += " AND payment_type = %s"
            params.append(payment_type)
        
        sql += " ORDER BY paid_at DESC"
        
        db.cursor.execute(sql, tuple(params))
        records = db.cursor.fetchall()
        
        formatted_records = []
        for record in records:
            formatted_records.append({
                "id": record['id'],
                "order_no": record['order_no'],
                "transaction_id": record['transaction_id'],
                "amount": float(record['amount']),
                "payment_type": record['payment_type'],
                "status": record['status'],
                "description": record['description'],
                "paid_at": record['paid_at'].isoformat() if record.get('paid_at') else None
            })
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "total": len(formatted_records),
                "records": formatted_records
            }
        )
    
    except Exception as e:
        raise DatabaseException(f"查询付费记录失败: {str(e)}", str(e))


@router.get("/payment-status", summary="检查用户付费状态", description="检查用户是否已付费（可查看报告）")
async def check_payment_status(
    user_id: int,
    payment_type: str = "report_view",
    db: Database = Depends(get_db)
):
    """
    检查用户付费状态
    
    Args:
        user_id: 用户ID
        payment_type: 付费类型（默认report_view）
    
    Returns:
        付费状态信息
    """
    try:
        ensure_user_tables(db)
        
        # 检查用户是否存在
        db.cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not db.cursor.fetchone():
            return ApiResponse(
                success=False,
                code=404,
                message="用户不存在",
                data=None
            )
        
        # 查询该用户是否有成功的付费记录
        db.cursor.execute(
            """
            SELECT * FROM payment_records 
            WHERE user_id = %s AND payment_type = %s AND status = 'success'
            ORDER BY paid_at DESC LIMIT 1
            """,
            (user_id, payment_type)
        )
        record = db.cursor.fetchone()
        
        is_paid = record is not None
        
        return ApiResponse(
            success=True,
            code=200,
            message="查询成功",
            data={
                "user_id": user_id,
                "payment_type": payment_type,
                "is_paid": is_paid,
                "last_payment": {
                    "id": record['id'],
                    "amount": float(record['amount']),
                    "paid_at": record['paid_at'].isoformat() if record.get('paid_at') else None
                } if record else None
            }
        )
    
    except Exception as e:
        raise DatabaseException(f"检查付费状态失败: {str(e)}", str(e))
