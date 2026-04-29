#!/usr/bin/env python3
"""
数据模型层：封装数据库操作，供 app.py 和 admin.py 共用。
"""
import sqlite3
import hashlib
import os
import json
from datetime import datetime, timedelta
from functools import lru_cache

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def serialize_row(row):
    """将 sqlite3.Row 转换为 dict"""
    if row is None:
        return None
    return dict(row)


# ==================== 用户相关 ====================

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def authenticate_user(username, password):
    """验证用户登录（兼容旧版 pbkdf2 和新版 sha256 密码格式）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND status = 1", (username,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return None
    
    pw_hash = user['password_hash']
    match = False
    
    # 尝试新版 sha256
    if pw_hash == hash_password(password):
        match = True
    # 尝试旧版 Werkzeug pbkdf2
    elif pw_hash.startswith('pbkdf2:'):
        try:
            from werkzeug.security import check_password_hash
            match = check_password_hash(pw_hash, password)
        except ImportError:
            pass
    
    conn.close()
    return user if match else None


def get_user_by_id(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user


def get_user_subjects(user_id):
    """获取用户有权限的科目列表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.*, us.can_practice, us.can_mock, us.can_daily, us.can_manage
        FROM user_subjects us
        JOIN subjects s ON s.id = us.subject_id
        WHERE us.user_id = ? AND s.status = 1
    """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return result


def get_all_users():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, role, status, last_login FROM users ORDER BY id")
    result = cur.fetchall()
    conn.close()
    return result


def create_user(username, password, role='user'):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username, password_hash, role, status) VALUES (?, ?, ?, 1)",
                    (username, hash_password(password), role))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_user_status(user_id, status):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    conn.commit()
    conn.close()


def update_user_last_login(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# ==================== 科目相关 ====================

def get_all_subjects():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subjects WHERE status = 1 ORDER BY id")
    result = cur.fetchall()
    conn.close()
    return result


def get_all_subjects_admin():
    """管理端：获取所有科目（含禁用）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subjects ORDER BY id")
    result = cur.fetchall()
    conn.close()
    return result


def get_subject(subject_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    result = cur.fetchone()
    conn.close()
    return result


def create_subject(name, code, description='', icon='📚'):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO subjects (name, code, description, icon, status) VALUES (?, ?, ?, ?, 1)",
                    (name, code, description, icon))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_subject(subject_id, name=None, code=None, description=None, icon=None, status=None):
    conn = get_db()
    cur = conn.cursor()
    fields = []
    values = []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if code is not None:
        fields.append("code = ?")
        values.append(code)
    if description is not None:
        fields.append("description = ?")
        values.append(description)
    if icon is not None:
        fields.append("icon = ?")
        values.append(icon)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if fields:
        values.append(subject_id)
        cur.execute(f"UPDATE subjects SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    conn.close()


# ==================== 分类相关 ====================

def get_categories_tree(subject_id):
    """获取科目的完整分类树"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM categories
        WHERE subject_id = ?
        ORDER BY level, sort_order, id
    """, (subject_id,))
    rows = cur.fetchall()
    conn.close()

    # 构建树形结构
    tree = []
    children_map = {}
    for row in rows:
        d = dict(row)
        d['children'] = []
        children_map[d['id']] = d

    root_nodes = []
    for row in rows:
        d = children_map[row['id']]
        if d['parent_id'] == 0:
            root_nodes.append(d)
        else:
            parent = children_map.get(d['parent_id'])
            if parent:
                parent['children'].append(d)

    return root_nodes


def get_category(category_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
    result = cur.fetchone()
    conn.close()
    return result


def get_leaf_categories(subject_id):
    """获取末级分类（三级分类）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE subject_id = ? AND level = 3 ORDER BY sort_order, id",
                (subject_id,))
    result = cur.fetchall()
    conn.close()
    return result


def create_category(subject_id, parent_id, name, level):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories WHERE parent_id = ?", (parent_id,))
    sort_order = cur.fetchone()[0]
    cur.execute("INSERT INTO categories (subject_id, parent_id, name, level, sort_order) VALUES (?, ?, ?, ?, ?)",
                (subject_id, parent_id, name, level, sort_order))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def delete_category(category_id):
    """删除分类（同时删除子分类）"""
    conn = get_db()
    cur = conn.cursor()
    # 递归查找所有子分类
    cur.execute("SELECT id FROM categories WHERE parent_id = ?", (category_id,))
    children = [r['id'] for r in cur.fetchall()]
    for child_id in children:
        delete_category(child_id)
    cur.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()


# ==================== 题目相关 ====================

def get_question(qid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM questions WHERE id = ? AND status = 1", (qid,))
    result = cur.fetchone()
    conn.close()
    return result


def get_questions_by_category(category_id, status=1):
    """按分类获取题目"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM questions WHERE category_id = ? AND status = ? ORDER BY id",
                (category_id, status))
    result = cur.fetchall()
    conn.close()
    return result


def get_questions_by_subject(subject_id, status=1, page=1, per_page=20, search=''):
    """分页获取题目（管理端用）"""
    conn = get_db()
    cur = conn.cursor()
    offset = (page - 1) * per_page

    where = "q.status = ? AND q.subject_id = ?"
    params = [status, subject_id]

    if search:
        where += " AND (q.stem LIKE ? OR q.id LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    cur.execute(f"SELECT COUNT(*) as total FROM questions q WHERE {where}", params)
    total = cur.fetchone()['total']

    cur.execute(f"""
        SELECT q.*, c.name as category_name
        FROM questions q
        LEFT JOIN categories c ON q.category_id = c.id
        WHERE {where}
        ORDER BY q.id
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    questions = cur.fetchall()
    conn.close()
    return questions, total


def create_question(data):
    """创建题目"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO questions (
            stem, options, answer, explanation, qtype, difficulty,
            subject_id, category_id, is_real_exam, exam_year, source, status, qtype_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        data.get('stem'),
        data.get('options', '{}'),
        data.get('answer'),
        data.get('explanation', ''),
        data.get('qtype', 'single'),
        data.get('difficulty', '无'),
        data.get('subject_id'),
        data.get('category_id'),
        data.get('is_real_exam', 0),
        data.get('exam_year'),
        data.get('source', 'practice'),
        data.get('qtype_text', '单选题'),
    ))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def update_question(qid, data):
    """更新题目"""
    conn = get_db()
    cur = conn.cursor()
    fields = []
    values = []
    for key in ['stem', 'options', 'answer', 'explanation', 'qtype', 'difficulty',
                'category_id', 'is_real_exam', 'exam_year', 'source', 'qtype_text', 'status']:
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if fields:
        values.append(qid)
        cur.execute(f"UPDATE questions SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    conn.close()


def delete_question(qid):
    """软删除题目"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE questions SET status = 0 WHERE id = ?", (qid,))
    conn.commit()
    conn.close()


# ==================== 权限相关 ====================

def set_user_subject_permission(user_id, subject_id, can_practice=0, can_mock=0, can_daily=0, can_manage=0):
    """设置用户科目权限"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_subjects WHERE user_id = ? AND subject_id = ?", (user_id, subject_id))
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE user_subjects SET can_practice = ?, can_mock = ?, can_daily = ?, can_manage = ?
            WHERE user_id = ? AND subject_id = ?
        """, (can_practice, can_mock, can_daily, can_manage, user_id, subject_id))
    else:
        cur.execute("""
            INSERT INTO user_subjects (user_id, subject_id, can_practice, can_mock, can_daily, can_manage)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, subject_id, can_practice, can_mock, can_daily, can_manage))
    conn.commit()
    conn.close()


def get_user_permissions(user_id):
    """获取用户所有科目权限"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.name as subject_name, s.id as subject_id,
               us.can_practice, us.can_mock, us.can_daily, us.can_manage
        FROM user_subjects us
        JOIN subjects s ON s.id = us.subject_id
        WHERE us.user_id = ?
    """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return result


def get_all_subjects_for_permission():
    """获取所有科目（用于权限分配页面）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, code FROM subjects WHERE status = 1 ORDER BY id")
    result = cur.fetchall()
    conn.close()
    return result


# ==================== 答题历史 ====================

def save_answer(user_id, question_id, user_answer, correct, subject_id=1):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO history (user_id, question_id, user_answer, correct, subject_id)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, question_id, user_answer, correct, subject_id))
    conn.commit()
    conn.close()


def get_user_history(user_id, subject_id=None, limit=50):
    conn = get_db()
    cur = conn.cursor()
    if subject_id:
        cur.execute("""
            SELECT h.*, q.stem, q.answer
            FROM history h
            JOIN questions q ON q.id = h.question_id
            WHERE h.user_id = ? AND h.subject_id = ?
            ORDER BY h.id DESC
            LIMIT ?
        """, (user_id, subject_id, limit))
    else:
        cur.execute("""
            SELECT h.*, q.stem, q.answer
            FROM history h
            JOIN questions q ON q.id = h.question_id
            WHERE h.user_id = ?
            ORDER BY h.id DESC
            LIMIT ?
        """, (user_id, limit))
    result = cur.fetchall()
    conn.close()
    return result


def get_user_wrong_questions(user_id, subject_id=None):
    conn = get_db()
    cur = conn.cursor()
    if subject_id:
        cur.execute("""
            SELECT q.*, COUNT(*) as wrong_count
            FROM history h
            JOIN questions q ON q.id = h.question_id
            WHERE h.user_id = ? AND h.correct = 0 AND h.subject_id = ?
            GROUP BY q.id
            ORDER BY wrong_count DESC
        """, (user_id, subject_id))
    else:
        cur.execute("""
            SELECT q.*, COUNT(*) as wrong_count
            FROM history h
            JOIN questions q ON q.id = h.question_id
            WHERE h.user_id = ? AND h.correct = 0
            GROUP BY q.id
            ORDER BY wrong_count DESC
        """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return result


def get_user_favorites(user_id, subject_id=None):
    conn = get_db()
    cur = conn.cursor()
    if subject_id:
        cur.execute("""
            SELECT f.*, q.stem
            FROM favorites f
            JOIN questions q ON q.id = f.question_id
            WHERE f.user_id = ? AND f.subject_id = ?
            ORDER BY f.id DESC
        """, (user_id, subject_id))
    else:
        cur.execute("""
            SELECT f.*, q.stem
            FROM favorites f
            JOIN questions q ON q.id = f.question_id
            WHERE f.user_id = ?
            ORDER BY f.id DESC
        """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return result


def toggle_favorite(user_id, question_id, subject_id=1):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM favorites WHERE user_id = ? AND question_id = ?", (user_id, question_id))
    existing = cur.fetchone()
    if existing:
        cur.execute("DELETE FROM favorites WHERE id = ?", (existing['id'],))
        conn.commit()
        conn.close()
        return False
    else:
        cur.execute("INSERT INTO favorites (user_id, question_id, subject_id) VALUES (?, ?, ?)",
                    (user_id, question_id, subject_id))
        conn.commit()
        conn.close()
        return True


# ==================== SM-2 复习计划 ====================

def sm2_schedule(quality, ease_factor, interval, repetitions):
    """SM-2 间隔重复算法"""
    new_ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ease = max(1.3, new_ease)
    
    if quality < 3:
        new_reps = 0
        new_interval = 1
    else:
        new_reps = repetitions + 1
        if new_reps == 1:
            new_interval = 1
        elif new_reps == 2:
            new_interval = 6
        else:
            new_interval = max(1, int(interval * new_ease))
    
    return {
        "ease_factor": round(new_ease, 2),
        "interval": new_interval,
        "repetitions": new_reps,
    }


def get_due_questions(user_id, category_id=None, limit=20):
    """获取到期需要复习的题目"""
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    cur = conn.cursor()
    
    if category_id:
        cur.execute("""
            SELECT q.*, rs.ease_factor, rs.interval, rs.repetitions
            FROM questions q
            JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
            WHERE q.category_id = ? AND q.status = 1 AND rs.next_review <= ?
            ORDER BY rs.next_review ASC
            LIMIT ?
        """, (user_id, category_id, now, limit))
    else:
        # 注意：subject_id 从分类推导（取该用户第一个科目），不再用 category_id 冒充 subject_id
        cur.execute("""
            SELECT q.*, rs.ease_factor, rs.interval, rs.repetitions
            FROM questions q
            JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
            WHERE q.subject_id = (
                SELECT subject_id FROM categories ORDER BY id LIMIT 1
            ) AND q.status = 1 AND rs.next_review <= ?
            ORDER BY rs.next_review ASC
            LIMIT ?
        """, (user_id, now, limit))
    
    due = [serialize_row(r) for r in cur.fetchall()]
    conn.close()
    return due


def get_new_questions(user_id, category_id=None, limit=5):
    """获取用户还未复习过的新题目"""
    conn = get_db()
    cur = conn.cursor()
    
    if category_id:
        cur.execute("""
            SELECT q.* FROM questions q
            WHERE q.category_id = ? AND q.status = 1
            AND q.id NOT IN (SELECT question_id FROM review_schedule WHERE user_id = ?)
            ORDER BY q.id
            LIMIT ?
        """, (category_id, user_id, limit))
    else:
        # 注意：subject_id 从分类推导，不再用 category_id 冒充 subject_id
        cur.execute("""
            SELECT q.* FROM questions q
            WHERE q.subject_id = (
                SELECT subject_id FROM categories ORDER BY id LIMIT 1
            ) AND q.status = 1
            AND q.id NOT IN (SELECT question_id FROM review_schedule WHERE user_id = ?)
            ORDER BY q.id
            LIMIT ?
        """, (user_id, limit))
    
    new_qs = [serialize_row(r) for r in cur.fetchall()]
    conn.close()
    return new_qs


def get_review_progress(user_id, subject_id=None, category_id=None):
    """获取复习进度统计"""
    conn = get_db()
    cur = conn.cursor()
    
    if category_id:
        cur.execute("""
            SELECT COUNT(*) as total FROM questions WHERE category_id = ? AND status = 1
        """, (category_id,))
    elif subject_id:
        cur.execute("SELECT COUNT(*) as total FROM questions WHERE subject_id = ? AND status = 1",
                   (subject_id,))
    else:
        cur.execute("SELECT COUNT(*) as total FROM questions WHERE status = 1")
    total = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) as reviewed FROM review_schedule WHERE user_id = ?",
               (user_id,))
    reviewed = cur.fetchone()[0]
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("SELECT COUNT(*) as due FROM review_schedule WHERE user_id = ? AND next_review <= ?",
               (user_id, now))
    due = cur.fetchone()[0]
    
    conn.close()
    return {"total": total, "reviewed": reviewed, "due": due}


def update_review_schedule(user_id, question_id, subject_id, quality):
    """根据评分更新复习计划"""
    from datetime import datetime, timedelta
    
    conn = get_db()
    cur = conn.cursor()
    
    # 获取或创建复习记录
    cur.execute("""
        SELECT ease_factor, interval, repetitions FROM review_schedule
        WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id))
    existing = cur.fetchone()
    
    if existing:
        ease = existing['ease_factor']
        interval = existing['interval']
        reps = existing['repetitions']
    else:
        ease = 2.5
        interval = 0
        reps = 0
    
    # 计算新参数
    result = sm2_schedule(quality, ease, interval, reps)
    
    now = datetime.now()
    next_review = now + timedelta(days=result['interval'])
    
    if existing:
        cur.execute("""
            UPDATE review_schedule
            SET ease_factor = ?, interval = ?, repetitions = ?,
                next_review = ?, last_review = ?
            WHERE user_id = ? AND question_id = ?
        """, (result['ease_factor'], result['interval'], result['repetitions'],
              next_review.strftime('%Y-%m-%d %H:%M:%S'),
              now.strftime('%Y-%m-%d %H:%M:%S'),
              user_id, question_id))
    else:
        cur.execute("""
            INSERT INTO review_schedule (user_id, question_id, subject_id, ease_factor, interval, repetitions, next_review, last_review)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, question_id, subject_id,
              result['ease_factor'], result['interval'], result['repetitions'],
              next_review.strftime('%Y-%m-%d %H:%M:%S'),
              now.strftime('%Y-%m-%d %H:%M:%S')))
    
    conn.commit()
    conn.close()
    return result


# ==================== 统计模块 ====================

def get_stats_summary(user_id, subject_id):
    """获取学习统计概览"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    seven_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # 总复习数（来自 history）
    cur.execute("SELECT COUNT(*) FROM history WHERE user_id = ? AND subject_id = ?", (user_id, subject_id))
    total_reviewed = cur.fetchone()[0]
    
    # 今日复习数
    cur.execute("""
        SELECT COUNT(*) FROM history WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) = ?
    """, (user_id, subject_id, today_str))
    today_reviewed = cur.fetchone()[0]
    
    # 待复习数
    cur.execute("SELECT COUNT(*) FROM review_schedule WHERE user_id = ? AND next_review <= ?",
               (user_id, now.strftime('%Y-%m-%d %H:%M:%S')))
    due_now = cur.fetchone()[0]
    
    # 近7天正确率
    cur.execute("""
        SELECT AVG(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) * 100
        FROM history WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ?
    """, (user_id, subject_id, seven_ago))
    acc_7d = cur.fetchone()[0]
    accuracy_7d = round(acc_7d or 0, 1)
    
    # 连续学习天数
    cur.execute("""
        SELECT DISTINCT DATE(timestamp) as study_date
        FROM history WHERE user_id = ? AND subject_id = ?
        ORDER BY study_date DESC
    """, (user_id, subject_id))
    dates = [row[0] for row in cur.fetchall()]
    streak = 0
    expected = datetime.now()
    for d in dates:
        if d == expected.strftime('%Y-%m-%d'):
            streak += 1
            expected = expected - timedelta(days=1)
        elif streak == 0:
            continue
        else:
            break
    
    # 累计学习时长（估算：每题平均30秒）
    total_minutes = round(total_reviewed * 0.5, 0)
    
    conn.close()
    return {
        'total_reviewed': total_reviewed,
        'today_reviewed': today_reviewed,
        'due_now': due_now,
        'accuracy_7d': accuracy_7d,
        'streak_days': streak,
        'total_minutes': int(total_minutes),
    }


def get_daily_trend(user_id, subject_id, days=30):
    """获取每日复习趋势"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cur.execute("""
        SELECT DATE(timestamp) as date,
               COUNT(*) as reviewed,
               ROUND(AVG(CASE WHEN correct = 1 THEN 100.0 ELSE 0.0 END), 1) as accuracy
        FROM history WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ?
        GROUP BY DATE(timestamp) ORDER BY date
    """, (user_id, subject_id, since))
    
    result = [{'date': r[0], 'reviewed': r[1], 'accuracy': float(r[2])} for r in cur.fetchall()]
    conn.close()
    return result


def get_heatmap_data(user_id, subject_id, days=90):
    """获取热力图数据（近 N 天）"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cur.execute("""
        SELECT DATE(timestamp) as date, COUNT(*) as count
        FROM history WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ?
        GROUP BY DATE(timestamp) ORDER BY date
    """, (user_id, subject_id, since))
    
    result = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return result


def get_category_mastery(user_id, subject_id):
    """获取分类掌握度"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cur.execute("""
        SELECT c.name as category_name,
               COUNT(DISTINCT h.question_id) as reviewed,
               ROUND(AVG(CASE WHEN h.correct = 1 THEN 100.0 ELSE 0.0 END), 1) as accuracy,
               SUM(CASE WHEN rs.next_review <= ? THEN 1 ELSE 0 END) as due
        FROM history h
        JOIN questions q ON q.id = h.question_id
        JOIN categories c ON c.id = q.category_id
        LEFT JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = h.user_id
        WHERE h.user_id = ? AND h.subject_id = ?
        GROUP BY c.id, c.name
        HAVING reviewed >= 1
        ORDER BY accuracy ASC
    """, (now, user_id, subject_id))
    
    result = [{'name': r[0], 'reviewed': r[1], 'accuracy': float(r[2] or 0), 'due': r[3] or 0} for r in cur.fetchall()]
    conn.close()
    return result


def get_retention_curve(user_id, subject_id):
    """获取保留率曲线（SM-2 遗忘曲线）"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        SELECT 
            CAST(julianday(?) - julianday(rs.last_review) AS INTEGER) as days_since,
            COUNT(*) as total,
            SUM(CASE WHEN rs.ease_factor >= 1.5 THEN 1 ELSE 0 END) as retained
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        JOIN history h ON h.question_id = rs.question_id AND h.user_id = rs.user_id
        WHERE rs.user_id = ? AND q.subject_id = ? AND rs.last_review IS NOT NULL
        GROUP BY days_since
        HAVING total >= 1
        ORDER BY days_since
    """, (now_str, user_id, subject_id))
    
    result = [{'days': r[0], 'total': r[1], 'retained': r[2]} for r in cur.fetchall()]
    conn.close()
    return result


# ==================== 新增封装函数（供 app.py 使用） ====================

def get_subject_by_id(subject_id):
    """获取单个科目（含禁用）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    result = cur.fetchone()
    conn.close()
    return result


def get_questions_count(subject_id):
    """获取科目题目总数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE subject_id = ? AND status = 1", (subject_id,))
    result = cur.fetchone()[0]
    conn.close()
    return result


def get_real_exam_count(subject_id):
    """获取科目真题数量"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE subject_id = ? AND is_real_exam = 1 AND status = 1", (subject_id,))
    result = cur.fetchone()[0]
    conn.close()
    return result


def get_exam_years(subject_id):
    """获取科目真题年份列表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT exam_year FROM questions
        WHERE subject_id = ? AND exam_year IS NOT NULL AND status = 1
        ORDER BY exam_year DESC
    """, (subject_id,))
    result = [r['exam_year'] for r in cur.fetchall()]
    conn.close()
    return result


def get_user_subject_accuracy(user_id, subject_id):
    """获取用户在某科目的整体正确率"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) as correct_count
        FROM history WHERE user_id = ? AND subject_id = ?
    """, (user_id, subject_id))
    row = cur.fetchone()
    conn.close()
    if row['total'] > 0:
        return round(row['correct_count'] / row['total'] * 100, 1)
    return 0


def get_next_question_id(subject_id, current_qid):
    """获取下一题 ID"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM questions
        WHERE subject_id = ? AND id > ? AND status = 1
        ORDER BY id LIMIT 1
    """, (subject_id, current_qid))
    row = cur.fetchone()
    conn.close()
    return row['id'] if row else None


def get_questions_by_year(subject_id, year):
    """按年份获取真题"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM questions
        WHERE subject_id = ? AND is_real_exam = 1 AND exam_year = ? AND status = 1
        ORDER BY id
    """, (subject_id, year))
    result = cur.fetchall()
    conn.close()
    return result


def is_question_favorite(user_id, question_id):
    """检查题目是否已收藏"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM favorites WHERE user_id = ? AND question_id = ?",
               (user_id, question_id))
    result = cur.fetchone() is not None
    conn.close()
    return result


def get_question_count_by_category(category_id):
    """获取分类题目数量"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE category_id = ? AND status = 1", (category_id,))
    result = cur.fetchone()[0]
    conn.close()
    return result


def get_question_position_in_category(category_id, qid):
    """获取题目在分类中的位置"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE category_id = ? AND id <= ? AND status = 1",
               (category_id, qid))
    position = cur.fetchone()[0]
    conn.close()
    return position


def get_random_questions(subject_id, category_id=None, count=10):
    """随机获取题目（封装版）"""
    conn = get_db()
    cur = conn.cursor()
    if category_id:
        cur.execute("""
            SELECT * FROM questions
            WHERE category_id = ? AND status = 1
            ORDER BY RANDOM() LIMIT ?
        """, (category_id, count))
    else:
        cur.execute("""
            SELECT * FROM questions
            WHERE subject_id = ? AND status = 1
            ORDER BY RANDOM() LIMIT ?
        """, (subject_id, count))
    result = cur.fetchall()
    conn.close()
    return result


def get_sequential_questions(subject_id, category_id=None):
    """顺序获取题目（封装版）"""
    conn = get_db()
    cur = conn.cursor()
    if category_id:
        cur.execute("""
            SELECT * FROM questions
            WHERE category_id = ? AND status = 1
            ORDER BY id
        """, (category_id,))
    else:
        cur.execute("""
            SELECT * FROM questions
            WHERE subject_id = ? AND status = 1
            ORDER BY id
        """, (subject_id,))
    result = cur.fetchall()
    conn.close()
    return result
