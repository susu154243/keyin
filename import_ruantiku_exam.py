#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将ruantiku爬虫数据导入KeyIn数据库
- 每个科目新建"历年真题"二级分类
- 每套真题为三级分类
- 题目带解析导入
"""
import sqlite3, json, os, re
from datetime import datetime

DATA_DIR = "/keyin/ruantiku_data"
DB_PATH = "/keyin/database.db"

# ruantiku EID → KeyIn subject_id 映射
EID_TO_SUBJECT = {
    1: 7,   # 信息系统项目管理师
    2: 9,   # 系统集成项目管理工程师
    3: 19,  # 信息系统监理师
    4: 11,  # 软件设计师
    5: 13,  # 系统分析师
    6: 8,   # 信息安全工程师
    7: 14,  # 网络规划设计师
    8: 10,  # 网络工程师
    28: 12, # 系统架构设计师
    29: 15, # 系统规划与管理师
    30: 17, # 信息系统管理工程师
    31: 20, # 程序员
    32: 16, # 数据库系统工程师
    34: 23, # 电子商务设计师
    35: 22, # 嵌入式系统设计师
    36: 26, # 多媒体应用设计师
    37: 21, # 软件评测师
    38: 25, # 信息处理技术员
    39: 24, # 信息系统运行管理员
    40: 18, # 网络管理员
}

def import_to_keyin():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    total_questions = 0
    total_papers = 0
    total_subjects = 0
    
    # 按EID遍历
    for eid in sorted(EID_TO_SUBJECT.keys()):
        subject_id = EID_TO_SUBJECT[eid]
        subject_dir_name = None
        
        # 找到对应的数据目录
        for d in os.listdir(DATA_DIR):
            dpath = os.path.join(DATA_DIR, d)
            if os.path.isdir(dpath) and os.path.exists(os.path.join(dpath, "summary.json")):
                with open(os.path.join(dpath, "summary.json"), 'r', encoding='utf-8') as f:
                    summary = json.load(f)
                if summary.get('subject_id') == eid:
                    subject_dir_name = d
                    break
        
        if not subject_dir_name:
            print(f"EID={eid}: 未找到数据目录，跳过")
            continue
        
        subject_dir = os.path.join(DATA_DIR, subject_dir_name)
        
        # 获取科目名
        with open(os.path.join(subject_dir, "summary.json"), 'r', encoding='utf-8') as f:
            summary = json.load(f)
        subject_name = summary['subject_name']
        papers_info = summary['papers_info']
        
        print(f"\n导入: [{subject_id}] {subject_name}")
        
        # 1. 创建"历年真题"二级分类（如果不存在）
        cur.execute(
            "SELECT id FROM categories WHERE subject_id=? AND name='历年真题' AND parent_id=0",
            (subject_id,)
        )
        row = cur.fetchone()
        if row:
            exam_cat_id = row[0]
            print(f"  历年真题分类已存在: id={exam_cat_id}")
        else:
            cur.execute(
                "INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, 0, '历年真题', 2, 999)",
                (subject_id,)
            )
            exam_cat_id = cur.lastrowid
            print(f"  新建历年真题分类: id={exam_cat_id}")
        
        # 2. 导入每套真题
        for paper in papers_info:
            sid = paper['sid']
            title = paper['title']
            
            # 从文件名找JSON
            json_file = os.path.join(subject_dir, f"{sid}.json")
            if not os.path.exists(json_file):
                print(f"  跳过 {sid}: 文件不存在")
                continue
            
            with open(json_file, 'r', encoding='utf-8') as f:
                paper_data = json.load(f)
            
            questions = paper_data.get('questions', [])
            if not questions:
                continue
            
            # 创建三级分类（每套真题）
            cur.execute(
                "SELECT id FROM categories WHERE subject_id=? AND parent_id=? AND name=?",
                (subject_id, exam_cat_id, title)
            )
            row = cur.fetchone()
            if row:
                paper_cat_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, ?, ?, 3, 0)",
                    (subject_id, exam_cat_id, title)
                )
                paper_cat_id = cur.lastrowid
            
            # 导入题目
            q_count = 0
            for q in questions:
                qid = f"rt_{sid}_{q['tid']}"
                
                # 检查是否已存在
                cur.execute("SELECT id FROM questions WHERE id=?", (qid,))
                if cur.fetchone():
                    continue
                
                # 选项
                options = ""
                if q.get('options'):
                    opt_strs = [f"{o['label']}. {o['text']}" for o in q['options']]
                    options = "\n".join(opt_strs)
                
                # 题型判断
                qtype_text = q.get('type', '单选题')
                if '多选' in qtype_text:
                    qtype = 'multiple'
                else:
                    qtype = 'single'
                
                # 提取年份
                year_match = re.search(r'(20\d{2})', title)
                exam_year = int(year_match.group(1)) if year_match else None
                
                # 解析
                explanation = q.get('explanation', '')
                
                cur.execute(
                    """INSERT INTO questions 
                       (id, stem, answer, difficulty, qtype, options, subject_id, category_id, 
                        explanation, is_real_exam, exam_year, source, status, qtype_text)
                       VALUES (?, ?, ?, 'medium', ?, ?, ?, ?, ?, 1, ?, 'exam', 1, ?)""",
                    (qid, q['question'], q.get('answer', ''), qtype, options,
                     subject_id, paper_cat_id, explanation, exam_year, qtype_text)
                )
                q_count += 1
            
            total_questions += q_count
            total_papers += 1
            print(f"  [{title[:40]}] 导入 {q_count}/{len(questions)} 题")
        
        total_subjects += 1
    
    conn.commit()
    
    # 统计
    cur.execute("SELECT COUNT(*) FROM questions WHERE is_real_exam=1 AND source='exam'")
    exam_q_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT subject_id) FROM questions WHERE is_real_exam=1")
    exam_subject_count = cur.fetchone()[0]
    
    print(f"\n{'='*60}")
    print(f"导入完成!")
    print(f"  科目: {total_subjects} 个")
    print(f"  真题套数: {total_papers} 套")
    print(f"  题目: {total_questions} 道")
    print(f"  数据库真题总数: {exam_q_count} 道")
    print(f"  涉及科目: {exam_subject_count} 个")
    
    conn.close()

if __name__ == "__main__":
    import_to_keyin()
