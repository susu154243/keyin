#!/usr/bin/env python3
"""
ruankaodaren.com 题库导入脚本 — 首批只导入"信息系统项目管理师"(235)
用法: python3 /keyin/import_ruankao.py
"""
import json
import os
import sqlite3
import re

DB_PATH = '/keyin/database.db'
DATA_PATH = '/root/.openclaw/workspace/ruankao_data/235_信息系统项目管理师.json'

# 题型映射
QTYPE_MAP = {
    '单选题': 'single',
    '多选题': 'multiple',
    '判断题': 'judge',
}

QTYPE_TEXT_MAP = {
    'single': '单选题',
    'multiple': '多选题',
    'judge': '判断题',
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def sanitize_html(text):
    """净化 HTML（复用 models.py 逻辑）"""
    if not text:
        return text
    # 移除危险标签
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<object[^>]*>.*?</object>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<embed[^>]*>.*?</embed>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # 移除事件属性
    text = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*on\w+\s*=\s*\S+', '', text, flags=re.IGNORECASE)
    return text


def parse_chapter_name(name):
    """从 '第一章 信息化发展（新第4版）' 提取 '第1章 信息化发展'"""
    # 中文数字转阿拉伯数字
    cn_num = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
              '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
              '十一': '11', '十二': '12', '十三': '13', '十四': '14',
              '十五': '15', '十六': '16', '十七': '17', '十八': '18',
              '十九': '19', '二十': '20', '二十一': '21', '二十二': '22',
              '二十三': '23', '二十四': '24', '二十五': '25', '二十六': '26',
              '二十七': '27', '二十八': '28'}
    for cn, ar in cn_num.items():
        name = name.replace(f'第{cn}章', f'第{ar}章', 1)
    # 去掉括号内容（如"（新第4版）"）
    name = re.sub(r'\s*（[^）]+）', '', name)
    return name.strip()


def parse_section_name(name):
    """从 '第一节 信息与信息化' 提取 '1.1 信息与信息化'"""
    cn_num = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
              '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    # 提取节号
    m = re.match(r'第([一二三四五六七八九十]+)节\s*(.*)', name)
    if m:
        sec_num = cn_num.get(m.group(1), m.group(1))
        return f'{sec_num} {m.group(2).strip()}'
    return name.strip()


def convert_options(raw_options, qtype):
    """
    将 ruankao options [{label, content, isCorrect}] 转为 KeyIn {"A": "内容"}
    判断题: {"A": "正确", "B": "错误"}
    """
    if qtype == 'judge':
        return json.dumps({"A": "正确", "B": "错误"}, ensure_ascii=False)

    result = {}
    for opt in raw_options:
        label = opt.get('label', '')
        content = sanitize_html(opt.get('content', ''))
        if label:
            result[label] = content
    return json.dumps(result, ensure_ascii=False)


def merge_case_questions(questions):
    """将案例分析中同一案例的多个子题合并为一道题。
    
    输入：[q1(问题1), q2(问题2), q3(问题3), ...]
    输出：[merged_q, merged_q2, ...]
    
    合并规则：
    - 题干 = 【说明】(只一次) + 所有【问题N】
    - 答案 = 所有子题答案按序号拼接
    - options 保持为空
    """
    import re
    
    # 按 【说明】 内容分组
    groups = {}  # shuoming_text -> [questions]
    for q in questions:
        title = q.get('title', '')
        # 提取 【说明】 部分（【问题1】之前的内容）
        m = re.search(r'【问题\d+】', title)
        if m:
            shuoming = title[:m.start()].strip()
        else:
            # 没有 【问题N】 标记，单独作为一组
            shuoming = title
        
        if shuoming not in groups:
            groups[shuoming] = []
        groups[shuoming].append(q)
    
    merged = []
    for shuoming, qs in groups.items():
        if len(qs) == 1:
            # 只有一个子题，不需要合并
            merged.append(qs[0])
        else:
            # 合并多个子题
            # 合并题干：【说明】+ 所有【问题N】
            combined_stem = shuoming
            parts = []
            answers = []
            for i, q in enumerate(qs):
                title = q.get('title', '')
                # 提取【问题N】及其后面的内容
                m = re.search(r'【问题\d+】', title)
                if m:
                    question_part = title[m.start():]
                else:
                    question_part = title
                parts.append(question_part)
                
                # 收集答案
                ans = q.get('answer', '').strip()
                if ans:
                    # 提取纯文本答案
                    clean_ans = re.sub(r'<[^>]+>', '', ans).strip()
                    if clean_ans and clean_ans != '无':
                        answers.append(clean_ans)
            
            combined_stem += '\n\n'.join(parts)
            combined_answer = '\n\n'.join(f'【问题{i+1}答案】\n{a}' for i, a in enumerate(answers)) if answers else '无'
            
            # 构建合并后的题目
            merged_q = {
                'id': qs[0]['id'],
                'type': qs[0].get('type', '单选题'),
                'title': combined_stem,
                'options': [],
                'answer': combined_answer,
                'explanation': qs[0].get('explanation', ''),
            }
            merged.append(merged_q)
    
    return merged


def main():
    print("=== KeyIn 题库导入脚本 ===")
    print(f"数据源: {DATA_PATH}")
    print(f"数据库: {DB_PATH}")
    print()

    # 加载数据
    with open(DATA_PATH, 'r') as f:
        sections = json.load(f)

    print(f"加载数据: {len(sections)} 个章节小节")

    conn = get_db()
    cur = conn.cursor()

    try:
        # ==================== 步骤 1: 创建科目 ====================
        print("\n[1/4] 创建科目...")
        cur.execute("SELECT id FROM subjects WHERE name = ?", ('信息系统项目管理师',))
        existing = cur.fetchone()
        if existing:
            subject_id = existing[0]
            print(f"  ⚠️ 科目已存在, ID={subject_id}, 将清空重建")
            # 清空该科目的旧分类和题目
            cur.execute("DELETE FROM categories WHERE subject_id = ?", (subject_id,))
            cur.execute("DELETE FROM questions WHERE subject_id = ?", (subject_id,))
            conn.commit()
            print(f"  已清空旧数据")
        else:
            cur.execute(
                "INSERT INTO subjects (name, code, icon, status) VALUES (?, ?, ?, 1)",
                ('信息系统项目管理师', 'ruankao_235', '📚')
            )
            subject_id = cur.lastrowid
            conn.commit()
            print(f"  ✅ 新建科目, ID={subject_id}")

        # ==================== 步骤 2: 重建分类树 ====================
        print("\n[2/4] 重建分类树...")

        # 按章分组
        chapters = {}
        chapter_order = []
        for sec in sections:
            ch = sec['chapter']
            if ch not in chapters:
                chapters[ch] = []
                chapter_order.append(ch)
            chapters[ch].append(sec)

        chapter_cat_ids = {}  # chapter_name -> level2 category_id
        sec_cat_map = {}      # sectionId -> level3 category_id
        total_sections = 0

        for ch_name in chapter_order:
            secs = chapters[ch_name]
            clean_ch = parse_chapter_name(ch_name)

            # Level 2 (章)
            cur.execute(
                "INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, 0, ?, 2, ?)",
                (subject_id, clean_ch, len(chapter_cat_ids) + 1)
            )
            ch_id = cur.lastrowid
            chapter_cat_ids[ch_name] = ch_id
            print(f"  📁 {clean_ch} (ID={ch_id})")

            for sec in secs:
                clean_sec = parse_section_name(sec['section'])
                cur.execute(
                    "INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, ?, ?, 3, ?)",
                    (subject_id, ch_id, clean_sec, total_sections + 1)
                )
                sec_id = cur.lastrowid
                sec_cat_map[sec['sectionId']] = sec_id
                total_sections += 1

        conn.commit()
        print(f"  ✅ 共 {len(chapter_cat_ids)} 章, {total_sections} 节")

        # ==================== 步骤 3: 导入题目 ====================
        print("\n[3/4] 导入题目...")

        total_questions = 0
        type_stats = {}
        BATCH = 500
        batch_count = 0

        # 按 section 顺序导入
        question_counter = {}  # category_id -> counter for {cat_num}-{seq} format

        for sec in sections:
            sec_id = sec['sectionId']
            cat_id = sec_cat_map[sec_id]
            questions = sec.get('questions', [])
            if not questions:
                continue

            # 案例分析：合并同一案例的子题
            if '案例分析' in sec.get('chapter', ''):
                original_count = len(questions)
                questions = merge_case_questions(questions)
                print(f"  📋 案例分析：{original_count} 个子题 → {len(questions)} 个案例")

            if cat_id not in question_counter:
                question_counter[cat_id] = 0

            for q in questions:
                qtype_raw = q.get('type', '单选题')
                options_raw = q.get('options', [])

                # 跳过论文区的占位符题目
                title_clean = re.sub(r'<[^>]+>', '', q.get('title', '')).strip()
                if '请点击' in title_clean or ('作答' in title_clean and len(title_clean) < 30):
                    skipped_questions += 1
                    continue

                # 学习卡片题：无 options 的主观题（案例分析、论文）
                is_card = not options_raw

                if is_card:
                    # 根据章节名判断题型
                    if '案例分析' in sec.get('chapter', ''):
                        qtype = 'single'
                        qtype_text = '案例分析题'
                    elif '论文' in sec.get('chapter', ''):
                        qtype = 'single'
                        qtype_text = '论文题'
                    elif '计算题' in sec.get('chapter', ''):
                        # 计算题有选项，走正常流程
                        qtype = QTYPE_MAP.get(qtype_raw, 'single')
                        qtype_text = QTYPE_TEXT_MAP.get(qtype, '单选题')
                    else:
                        qtype = 'single'
                        qtype_text = '论文题'
                else:
                    qtype = QTYPE_MAP.get(qtype_raw, 'single')
                    qtype_text = QTYPE_TEXT_MAP.get(qtype, '单选题')

                question_counter[cat_id] += 1

                # 题目 ID: 用 section 内序号
                seq = question_counter[cat_id]
                qid = f"{sec_id}-{seq}"

                # 选项转换
                options_json = convert_options(q.get('options', []), qtype)

                # 题干/解析净化
                stem = sanitize_html(q.get('title', ''))
                explanation = sanitize_html(q.get('explanation', ''))

                # 答案
                answer = q.get('answer', '')

                cur.execute(
                    """INSERT INTO questions
                       (id, stem, options, answer, explanation, qtype, difficulty,
                        subject_id, category_id, is_real_exam, exam_year, source, status, qtype_text)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (qid, stem, options_json, answer, explanation, qtype,
                     '无', subject_id, cat_id, 0, None, 'practice', qtype_text)
                )

                total_questions += 1
                batch_count += 1
                type_stats[qtype_text] = type_stats.get(qtype_text, 0) + 1

                if batch_count >= BATCH:
                    conn.commit()
                    batch_count = 0

        conn.commit()
        print(f"  ✅ 共导入 {total_questions} 题")
        print(f"     题型分布: {type_stats}")

        # ==================== 步骤 4: 统计验证 ====================
        print("\n[4/4] 统计验证...")

        cur.execute("SELECT COUNT(*) FROM questions WHERE subject_id = ? AND status = 1", (subject_id,))
        q_count = cur.fetchone()[0]
        print(f"  题目总数: {q_count}")

        cur.execute("SELECT COUNT(*) FROM categories WHERE subject_id = ?", (subject_id,))
        c_count = cur.fetchone()[0]
        print(f"  分类总数: {c_count}")

        cur.execute("SELECT qtype_text, COUNT(*) FROM questions WHERE subject_id = ? GROUP BY qtype_text", (subject_id,))
        for row in cur.fetchall():
            print(f"    {row[0]}: {row[1]}")

        # 验证分类下的题目数
        cur.execute("""
            SELECT c.name, COUNT(q.id) as cnt
            FROM categories c
            LEFT JOIN questions q ON q.category_id = c.id
            WHERE c.subject_id = ? AND c.level = 3
            GROUP BY c.id
            ORDER BY c.id
        """, (subject_id,))
        print(f"\n  各节题目数:")
        for row in cur.fetchall():
            print(f"    {row[0]}: {row[1]} 题")

        print("\n✅ 导入完成!")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ 导入失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
