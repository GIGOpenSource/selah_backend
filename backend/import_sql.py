#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导入SQL数据脚本
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
        cursor.execute(sql_content)
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
    print("开始导入SQL数据")
    print("=" * 50)

    # 检查SQL目录是否存在
    if not SQL_DIR.exists():
        print(f"错误: SQL目录不存在 - {SQL_DIR}")
        return False

    # 获取所有SQL文件
    sql_files = sorted(SQL_DIR.glob('*.sql'))
    if not sql_files:
        print("错误: 没有找到SQL文件")
        return False

    print(f"找到 {len(sql_files)} 个SQL文件")

    # 连接数据库
    try:
        print(f"连接数据库: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
        conn = psycopg2.connect(**DB_CONFIG)
        print("数据库连接成功")
    except Exception as e:
        print(f"数据库连接失败: {str(e)}")
        return False

    # 执行SQL文件
    success_count = 0
    fail_count = 0

    for sql_file in sql_files:
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