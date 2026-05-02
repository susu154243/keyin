#!/usr/bin/env python3
"""
管理后台增强功能迁移：新增 import_logs 表
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 检查表是否已存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='import_logs'")
    if cur.fetchone():
        print('import_logs 表已存在，跳过')
        conn.close()
        return

    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            operator TEXT DEFAULT 'admin',
            file_name TEXT,
            file_type TEXT,
            subject_id INTEGER,
            subject_name TEXT,
            imported INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success'
        )
    """)
    conn.commit()
    conn.close()
    print('import_logs 表创建成功')

if __name__ == '__main__':
    migrate()
