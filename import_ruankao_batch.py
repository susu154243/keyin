#!/usr/bin/env python3
"""
ruankaodaren.com 题库批量导入脚本
导入剩余 19 个科目（跳过已导入的 235_信息系统项目管理师）
用法: python3 /keyin/import_ruankao_batch.py [--dry-run]
"""
import json
import os
import sqlite3
import re
import sys
import time

DB_PATH = '/keyin/database.db'
DATA_DIR = '/root/.openclaw/workspace/ruankao_data/'

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
    if not text:
        return text
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<object[^>]*>.*?</object>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<embed[^>]*>.*?</embed>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*on\w+\s*=\s*\S+', '', text, flags=re.IGNORECASE)
    return text


def parse_chapter_name(name):
    cn_num = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
              '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
              '十一': '11', '十二': '12', '十三': '13', '十四': '14',
              '十五': '15', '十六': '16', '十七': '17', '十八': '18',
              '十九': '19', '二十': '20', '二十一': '21', '二十二': '22',
              '二十三': '23', '二十四': '24', '二十五': '25', '二十六': '26',
              '二十七': '27', '二十八': '28'}
    for cn, ar in cn_num.items():
        name = name.replace(f'第{cn}章', f'第{ar}章', 1)
    name = re.sub(r'\s*（[^）]+）', '', name)
    return name.strip()


def parse_section_name(name):
    cn_num = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
              '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    m = re.match(r'第([一二三四五六七八九十]+)节\s*(.*)', name)
    if m:
        sec_num = cn_num.get(m.group(1), m.group(1))
        return f'{sec_num} {m.group(2).strip()}'
    return name.strip()


def convert_options(raw_options, qtype):
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
    groups = {}
    for q in questions:
        title = q.get('title', '')
        m = re.search(r'【问题\d+】', title)
        if m:
            shuoming = title[:m.start()].strip()
        else:
            shuoming = title
        if shuoming not in groups:
            groups[shuoming] = []
        groups[shuoming].append(q)

    merged = []
    for shuoming, qs in groups.items():
        if len(qs) == 1:
            merged.append(qs[0])
        else:
            combined_stem = shuoming
            parts = []
            answers = []
            for i, q in enumerate(qs):
                title = q.get('title', '')
                m = re.search(r'【问题\d+】', title)
                if m:
                    question_part = title[m.start():]
                else:
                    question_part = title
                parts.append(question_part)
                ans = q.get('answer', '').strip()
                if ans:
                    clean_ans = re.sub(r'<[^>]+>', '', ans).strip()
                    if clean_ans and clean_ans != '无':
                        answers.append(clean_ans)

            combined_stem += '\n\n'.join(parts)
            combined_answer = '\n\n'.join(f'【问题{i+1}答案】\n{a}' for i, a in enumerate(answers)) if answers else '无'

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


def import_single_subject(filepath, dry_run=False):
    """导入单个科目，返回 (subject_name, subject_id, total_questions, type_stats)"""
    basename = os.path.basename(filepath)
    parts = basename.replace('.json', '', 1).split('_', 1)
    if len(parts) != 2:
        return None

    code = parts[0]  # e.g. "236"
    subject_name = parts[1]  # e.g. "信息安全工程师"

    print(f"\n{'='*60}")
    print(f"正在处理: {subject_name} (code={code})")
    print(f"数据文件: {filepath}")
    print(f"{'='*60}")

    with open(filepath, 'r') as f:
        sections = json.load(f)

    print(f"加载数据: {len(sections)} 个章节小节")

    # 统计原始题目数
    raw_total = sum(len(sec.get('questions', [])) for sec in sections)
    print(f"原始题目数: {raw_total}")

    if dry_run:
        print("  [DRY RUN] 跳过实际导入")
        return (subject_name, code, raw_total, {})

    conn = get_db()
    cur = conn.cursor()

    try:
        # 检查是否已存在
        cur.execute("SELECT id FROM subjects WHERE code = ?", (f'ruankao_{code}',))
        existing = cur.fetchone()
        if existing:
            subject_id = existing[0]
            print(f"  ⚠️ 科目已存在, ID={subject_id}, 将清空重建")
            cur.execute("DELETE FROM categories WHERE subject_id = ?", (subject_id,))
            cur.execute("DELETE FROM questions WHERE subject_id = ?", (subject_id,))
            conn.commit()
        else:
            cur.execute(
                "INSERT INTO subjects (name, code, icon, status) VALUES (?, ?, ?, 1)",
                (subject_name, f'ruankao_{code}', '📚')
            )
            subject_id = cur.lastrowid
            conn.commit()
            print(f"  ✅ 新建科目, ID={subject_id}")

        # 重建分类树
        chapters = {}
        chapter_order = []
        for sec in sections:
            ch = sec['chapter']
            if ch not in chapters:
                chapters[ch] = []
                chapter_order.append(ch)
            chapters[ch].append(sec)

        chapter_cat_ids = {}
        sec_cat_map = {}
        total_sections = 0

        for ch_name in chapter_order:
            secs = chapters[ch_name]
            clean_ch = parse_chapter_name(ch_name)

            cur.execute(
                "INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, 0, ?, 2, ?)",
                (subject_id, clean_ch, len(chapter_cat_ids) + 1)
            )
            ch_id = cur.lastrowid
            chapter_cat_ids[ch_name] = ch_id

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
        print(f"  📁 分类树: {len(chapter_cat_ids)} 章, {total_sections} 节")

        # 导入题目
        total_questions = 0
        skipped_questions = 0
        type_stats = {}
        BATCH = 500
        batch_count = 0
        question_counter = {}

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
                merged_delta = original_count - len(questions)
                if merged_delta > 0:
                    print(f"    📋 案例分析合并: {original_count} → {len(questions)} (-{merged_delta})")

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

                # 学习卡片题：无 options 的主观题
                is_card = not options_raw

                if is_card:
                    if '案例分析' in sec.get('chapter', ''):
                        qtype = 'single'
                        qtype_text = '案例分析题'
                    elif '论文' in sec.get('chapter', ''):
                        qtype = 'single'
                        qtype_text = '论文题'
                    elif '计算题' in sec.get('chapter', ''):
                        qtype = QTYPE_MAP.get(qtype_raw, 'single')
                        qtype_text = QTYPE_TEXT_MAP.get(qtype, '单选题')
                    else:
                        qtype = 'single'
                        qtype_text = '论文题'
                else:
                    qtype = QTYPE_MAP.get(qtype_raw, 'single')
                    qtype_text = QTYPE_TEXT_MAP.get(qtype, '单选题')

                question_counter[cat_id] += 1
                seq = question_counter[cat_id]
                qid = f"{sec_id}-{seq}"

                options_json = convert_options(q.get('options', []), qtype)
                stem = sanitize_html(q.get('title', ''))
                explanation = sanitize_html(q.get('explanation', ''))
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

        print(f"  ✅ 导入 {total_questions} 题 (跳过 {skipped_questions} 占位符)")
        print(f"     题型: {type_stats}")

        return (subject_name, subject_id, total_questions, type_stats)

    except Exception as e:
        conn.rollback()
        print(f"  ❌ 导入失败: {e}")
        raise
    finally:
        conn.close()


def main():
    dry_run = '--dry-run' in sys.argv

    print("=" * 60)
    print("KeyIn 题库批量导入 - ruankaodaren.com")
    print(f"数据库: {DB_PATH}")
    print(f"数据目录: {DATA_DIR}")
    if dry_run:
        print("模式: DRY RUN (不写入数据库)")
    print("=" * 60)

    # 获取所有待导入文件
    all_files = sorted([
        os.path.join(DATA_DIR, f)
        for f in os.listdir(DATA_DIR)
        if f.endswith('.json') and not f.startswith('.') and not f.startswith('235_')
    ])

    print(f"\n待导入科目数: {len(all_files)}")

    results = []
    total_all = 0
    t0 = time.time()

    for filepath in all_files:
        result = import_single_subject(filepath, dry_run)
        if result:
            results.append(result)
            total_all += result[2]  # total_questions

    elapsed = time.time() - t0

    # 汇总
    print(f"\n{'='*60}")
    print("导入完成汇总")
    print(f"{'='*60}")
    print(f"成功科目: {len(results)}/{len(all_files)}")
    print(f"总题目数: {total_all}")
    print(f"耗时: {elapsed:.1f} 秒")

    for name, sid, count, stats in results:
        print(f"  {name}: {count} 题 {stats}")


if __name__ == '__main__':
    main()
