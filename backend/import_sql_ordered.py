#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按照依赖关系顺序导入SQL数据
"""
import os
import sys
import psycopg2
from pathlib import Path

# 数据库连接配置（优先环境变量）
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', '127.0.0.1'),
    'port': os.getenv('POSTGRES_PORT', '5432'),
    'database': os.getenv('POSTGRES_DB', 'uivsbe'),
    'user': os.getenv('POSTGRES_USER', 'uivsbe'),
    'password': os.getenv('POSTGRES_PASSWORD', ''),
}

# SQL文件目录
SQL_DIR = Path(__file__).parent / 'sql文本'

# 按照依赖关系排序的SQL文件执行顺序
# 先执行被依赖的表，再执行依赖其他表的表
SQL_ORDER = [
    # 1. 基础表（没有外键依赖）
    'product_category.sql',  # 产品分类表，被merchant和product依赖
    'tag.sql',  # 标签表
    'wx_user.sql',  # 微信用户表
    'system_config.sql',  # 系统配置表

    # 2. 依赖基础表的表
    'brand.sql',  # 品牌表
    'merchant.sql',  # 商户表（依赖product_category）
    'product.sql',  # 产品表（依赖product_category）

    # 3. 依赖商户表的表
    'merchant_application.sql',  # 商户申请表
    'business_district_group.sql',  # 商圈分组表
    'district_merchant_relation.sql',  # 商圈商户关系表（依赖merchant）
    'inquiry.sql',  # 询价表（依赖merchant）
    'promotion_code.sql',  # 优惠码表（依赖merchant）

    # 4. 依赖多个表的表
    'procurement_requirement.sql',  # 采购需求表
    'conversation.sql',  # 会话表
    'message.sql',  # 消息表

    # 5. Django内部表（通常不需要导入）
    # 'django_migrations.sql',  # Django迁移记录（跳过）
]

def execute_sql_file(conn, sql_file):
    """执行单个SQL文件"""
    print(f"正在执行: {sql_file.name}")
    try:
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()

        # 跳过空文件
        if not sql_content.strip():
            print(f"  跳过空文件: {sql_file.name}")
            return True

        cursor = conn.cursor()

        # 禁用外键约束检查（临时）
        cursor.execute("SET session_replication_role = 'replica';")

        # 将INSERT语句转换为INSERT ... ON CONFLICT DO NOTHING
        # 这样可以跳过已存在的记录，避免主键冲突
        modified_sql = sql_content.replace(
            'INSERT INTO',
            'INSERT INTO'
        ).replace(
            ') VALUES',
            ') VALUES'
        )

        # 逐行执行SQL语句
        for line in sql_content.split('\n'):
            line = line.strip()
            if line and line.startswith('INSERT'):
                # 在INSERT语句末尾添加ON CONFLICT DO NOTHING
                if line.endswith(';'):
                    line = line[:-1] + ' ON CONFLICT DO NOTHING;'
                try:
                    cursor.execute(line)
                except Exception as e:
                    print(f"    跳过行（可能已存在）: {str(e)[:50]}...")

        # 恢复外键约束检查
        cursor.execute("SET session_replication_role = 'origin';")

        conn.commit()
        cursor.close()
        print(f"  成功执行: {sql_file.name}")
        return True
    except Exception as e:
        print(f"  错误: {sql_file.name} - {str(e)}")
        conn.rollback()
        return False

def main():
    """主函数"""
    print("=" * 50)
    print("开始按顺序导入SQL数据")
    print("=" * 50)

    # 检查SQL目录是否存在
    if not SQL_DIR.exists():
        print(f"错误: SQL目录不存在 - {SQL_DIR}")
        return False

    # 连接数据库
    try:
        print(f"连接数据库: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
        conn = psycopg2.connect(**DB_CONFIG)
        print("数据库连接成功")
    except Exception as e:
        print(f"数据库连接失败: {str(e)}")
        return False

    # 按照顺序执行SQL文件
    success_count = 0
    fail_count = 0

    for sql_filename in SQL_ORDER:
        sql_file = SQL_DIR / sql_filename
        if not sql_file.exists():
            print(f"跳过不存在的文件: {sql_filename}")
            continue

        if execute_sql_file(conn, sql_file):
            success_count += 1
        else:
            fail_count += 1

    # 关闭连接
    conn.close()

    # 输出结果
    print("=" * 50)
    print(f"导入完成: 成功 {success_count}, 失败 {fail_count}")
    print("=" * 50)

    return fail_count == 0

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
