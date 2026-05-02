#!/usr/bin/env python3
"""
历史数据 FSRS 迁移脚本

根据 history 表的答题记录，重新计算 review_schedule 中每道题的
stability/difficulty，使其反映真实的记忆状态。

算法：
1. 按时间排序每道题的答题记录
2. 从初始状态开始，按 FSRS 公式逐步模拟
3. 更新 review_schedule 的最终状态
"""
import sqlite3
from datetime import datetime

DB_PATH = 'database.db'

# 导入 FSRS 核心公式（复用 models.py 中的逻辑）
import math
import sys
sys.path.insert(0, '/keyin')
from models import fsrs_schedule, init_memory_state

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # 获取所有有 history 记录且需要更新的 (user_id, question_id)
    cur.execute("""
        SELECT DISTINCT h.user_id, h.question_id, h.subject_id,
               rs.stability as old_stability
        FROM history h
        LEFT JOIN review_schedule rs ON rs.user_id = h.user_id AND rs.question_id = h.question_id
        WHERE rs.stability IS NULL OR rs.stability < 1.0
           OR EXISTS (
               SELECT 1 FROM history h2 
               WHERE h2.user_id = h.user_id AND h2.question_id = h.question_id
               AND h2.id > (SELECT MAX(h3.id) FROM history h3 WHERE h3.question_id = rs.question_id AND h3.user_id = rs.user_id AND h3.timestamp <= rs.last_review)
           )
        ORDER BY h.user_id, h.question_id
    """)
    
    pairs = cur.fetchall()
    print(f"📋 待迁移题目: {len(pairs)} 题\n")
    
    updated = 0
    skipped = 0
    
    for pair in pairs:
        user_id = pair['user_id']
        question_id = pair['question_id']
        subject_id = pair['subject_id']
        old_s = pair['old_stability']
        
        # 读取该题的所有 history 记录（按时间排序）
        cur.execute("""
            SELECT correct, timestamp
            FROM history
            WHERE user_id = ? AND question_id = ?
            ORDER BY timestamp ASC
        """, (user_id, question_id))
        
        history = cur.fetchall()
        if len(history) < 1:
            skipped += 1
            continue
        
        # 模拟 FSRS 调度过程
        stability, difficulty = init_memory_state(history[0]['correct'])
        last_ts = None
        
        for h in history:
            quality = 1 if h['correct'] else 0
            
            if last_ts:
                # 计算距上次的天数
                ts = datetime.strptime(h['timestamp'], '%Y-%m-%d %H:%M:%S')
                last = datetime.strptime(last_ts, '%Y-%m-%d %H:%M:%S')
                delta_t = (ts - last).total_seconds() / 86400
                
                # FSRS 更新
                stability, difficulty, _ = fsrs_schedule(
                    quality + 2, stability, difficulty, delta_t
                )
            else:
                # 首次答题
                stability, difficulty = init_memory_state(quality + 2)
            
            last_ts = h['timestamp']
        
        # 更新 review_schedule
        cur.execute("""
            UPDATE review_schedule
            SET stability = ?, difficulty = ?,
                last_review = ?,
                last_quality = ?
            WHERE user_id = ? AND question_id = ?
        """, (
            round(stability, 2),
            round(difficulty, 2),
            history[-1]['timestamp'],
            1 if history[-1]['correct'] else 0,
            user_id,
            question_id
        ))
        
        if cur.rowcount > 0:
            updated += 1
            print(f"  ✅ {user_id}/{question_id}: {len(history)}条记录 → s={stability:.1f}, d={difficulty:.1f}")
        else:
            skipped += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n📊 迁移完成: 更新 {updated} 题, 跳过 {skipped} 题")

if __name__ == '__main__':
    migrate()
