#!/usr/bin/env python3
"""
从 practice 来源（软考达人）的解析数据中，为 exam/ruantiku.com 来源的无解析题目补充解析。
按科目精确匹配题干文本。
"""
import sqlite3
import re
import os

DB_PATH = '/keyin/database.db'

def clean_text(html):
    """去掉HTML标签，规范化文本"""
    text = re.sub(r'<[^>]+>', '', html)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def similarity(a, b):
    """计算两段文本的相似度（基于字符集重叠）"""
    if not a or not b:
        return 0
    set_a = set(a)
    set_b = set(b)
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0
    return len(intersection) / len(union)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 获取所有有科目ID的单选单选题
c.execute('''
    SELECT id, subject_id, stem, explanation
    FROM questions
    WHERE qtype_text = '单选题' AND source = 'practice'
    AND explanation IS NOT NULL AND explanation != ''
''')
practice_qs = {}
for row in c.fetchall():
    sid = row['subject_id']
    if sid not in practice_qs:
        practice_qs[sid] = {}
    clean_stem = clean_text(row['stem'])
    if len(clean_stem) > 10:  # 忽略太短的题干
        practice_qs[sid][clean_stem] = row['explanation']

print(f'Practice来源有解析的单选题: {sum(len(v) for v in practice_qs.values())} 条')
print(f'覆盖科目数: {len(practice_qs)}')

# 查找无解析的 exam/ruantiku.com 单选题
c.execute('''
    SELECT id, subject_id, stem
    FROM questions
    WHERE qtype_text = '单选题' 
    AND source IN ('exam', 'ruantiku.com')
    AND (explanation IS NULL OR explanation = '')
''')
exam_qs = c.fetchall()
print(f'需要补充解析的单选题: {len(exam_qs)} 条')

# 匹配
matched = 0
exact_match = 0
fuzzy_match = 0
updates = []

for eq in exam_qs:
    sid = eq['subject_id']
    clean_stem = clean_text(eq['stem'])
    
    if sid not in practice_qs:
        continue
    
    # 精确匹配
    if clean_stem in practice_qs[sid]:
        updates.append((practice_qs[sid][clean_stem], eq['id']))
        exact_match += 1
        matched += 1
        continue
    
    # 快速模糊匹配：先按长度过滤
    best_sim = 0
    best_exp = None
    stem_len = len(clean_stem)
    for p_stem, p_exp in practice_qs[sid].items():
        p_len = len(p_stem)
        # 长度差异超过30%直接跳过
        if abs(stem_len - p_len) / max(stem_len, p_len) > 0.3:
            continue
        s = similarity(clean_stem, p_stem)
        if s > best_sim:
            best_sim = s
            best_exp = p_exp
    
    # 阈值 0.95
    if best_sim >= 0.95 and best_exp:
        updates.append((best_exp, eq['id']))
        fuzzy_match += 1
        matched += 1

print(f'\n匹配结果:')
print(f'  精确匹配: {exact_match}')
print(f'  模糊匹配 (≥95%): {fuzzy_match}')
print(f'  总匹配: {matched}/{len(exam_qs)} ({matched/len(exam_qs)*100:.1f}%)')

if matched > 0:
    print(f'\n执行更新: {matched} 条')
    # 备份
    from datetime import datetime
    bak = f'{DB_PATH}.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    os.system(f'cp {DB_PATH} {bak}')
    print(f'备份: {bak}')
    
    c.executemany('UPDATE questions SET explanation = ? WHERE id = ?', updates)
    conn.commit()
    print(f'更新完成: {c.rowcount} 条')

conn.close()
