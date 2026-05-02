#!/usr/bin/env python3
"""
数据模型层：封装数据库操作，供 app.py 和 admin.py 共用。
"""
import sqlite3
import hashlib
import os
import json
import time
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # 写入冲突时等 5 秒
    return conn


def serialize_row(row):
    """将 sqlite3.Row 转换为 dict"""
    if row is None:
        return None
    return dict(row)


# ==================== 用户相关 ====================

def hash_password(password):
    """使用 Werkzeug pbkdf2:sha256 加密密码（有盐值、60万次迭代）"""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


def authenticate_user(username, password):
    """验证用户登录（兼容新版 pbkdf2 和旧版 sha256 密码格式）"""
    from werkzeug.security import check_password_hash
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND status = 1", (username,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return None
    
    pw_hash = user['password_hash']
    match = False
    
    # 尝试新版 Werkzeug pbkdf2
    if pw_hash.startswith('pbkdf2:'):
        match = check_password_hash(pw_hash, password)
    # 兼容旧版 sha256（无盐值），验证后自动升级
    elif len(pw_hash) == 64:
        match = pw_hash == hashlib.sha256(password.encode()).hexdigest()
        if match:
            # 自动升级到 pbkdf2
            new_hash = hash_password(password)
            cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user['id']))
            conn.commit()
    
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


def set_user_session_token(user_id, token):
    """设置用户 session token（单设备登录）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET session_token = ? WHERE id = ?", (token, user_id))
    conn.commit()
    conn.close()


def clear_user_session_token(user_id):
    """清除用户 session token"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET session_token = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def verify_session_token(user_id, token):
    """验证 session token 是否匹配"""
    if not token:
        return False
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT session_token FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return row[0] == token


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
    import uuid
    conn = get_db()
    cur = conn.cursor()
    qid = data.get('id') or str(uuid.uuid4())[:8]
    cur.execute("""
        INSERT INTO questions (
            id, stem, options, answer, explanation, qtype, difficulty,
            subject_id, category_id, is_real_exam, exam_year, source, status, qtype_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        qid,
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


def update_question_id(old_id, new_id):
    """修改题目 ID（需确保新 ID 不冲突）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM questions WHERE id = ?", (new_id,))
    if cur.fetchone():
        conn.close()
        return False, '新 ID 已存在'
    cur.execute("UPDATE questions SET id = ? WHERE id = ?", (new_id, old_id))
    # 同步更新 favorites 和 history
    cur.execute("UPDATE favorites SET question_id = ? WHERE question_id = ?", (new_id, old_id))
    cur.execute("UPDATE history SET question_id = ? WHERE question_id = ?", (new_id, old_id))
    cur.execute("UPDATE review_schedule SET question_id = ? WHERE question_id = ?", (new_id, old_id))
    conn.commit()
    conn.close()
    return True, 'OK'


def health_check_questions(subject_id=None):
    """数据健康检查：异常 ID、断号、孤儿记录"""
    conn = get_db()
    cur = conn.cursor()
    results = {'abnormal_ids': [], 'gaps': [], 'orphans': []}

    where = ''
    where_q = ''
    params = []
    if subject_id:
        where = 'WHERE subject_id = ?'
        where_q = 'WHERE q.subject_id = ?'
        params = [subject_id]

    # 1. 异常 ID：不符合 '数字.数字-数字' 格式的
    cur.execute(f"SELECT id, stem, subject_id, category_id FROM questions {where}", params)
    import re
    for row in cur.fetchall():
        qid = row['id']
        if not re.match(r'^\d+\.\d+-\d+$', str(qid)):
            results['abnormal_ids'].append({
                'id': qid,
                'stem': str(row['stem'])[:80],
                'subject_id': row['subject_id'],
                'category_id': row['category_id'],
            })

    # 2. 按分类检测 ID 断号
    cur.execute(f"SELECT DISTINCT category_id FROM questions {where}", params)
    cat_ids = [r['category_id'] for r in cur.fetchall() if r['category_id']]
    for cat_id in cat_ids:
        cur.execute("SELECT id FROM questions WHERE category_id = ? ORDER BY id", (cat_id,))
        ids = [r['id'] for r in cur.fetchall()]
        # 提取同一前缀的序号
        prefix_nums = {}
        for qid in ids:
            m = re.match(r'^(\d+\.\d+)-(\d+)$', str(qid))
            if m:
                prefix = m.group(1)
                num = int(m.group(2))
                prefix_nums.setdefault(prefix, []).append(num)
        for prefix, nums in prefix_nums.items():
            if not nums:
                continue
            full = list(range(min(nums), max(nums) + 1))
            missing = [n for n in full if n not in nums]
            if missing:
                results['gaps'].append({
                    'category_id': cat_id,
                    'prefix': prefix,
                    'missing': missing,
                    'count': len(missing),
                })

    # 3. 孤儿记录：questions 引用不存在的 category
    cur.execute(f"""
        SELECT q.id, q.stem, q.category_id
        FROM questions q
        LEFT JOIN categories c ON q.category_id = c.id
        {where_q} AND c.id IS NULL AND q.category_id IS NOT NULL
    """, params)
    for row in cur.fetchall():
        results['orphans'].append({
            'id': row['id'],
            'stem': str(row['stem'])[:80],
            'category_id': row['category_id'],
        })

    conn.close()
    return results


def batch_delete_by_category(category_id):
    """按分类软删除所有题目，返回影响数量"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE questions SET status = 0 WHERE category_id = ? AND status = 1", (category_id,))
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def batch_move_questions(from_category_id, to_category_id):
    """批量迁移题目到目标分类，返回影响数量"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM categories WHERE id = ?", (to_category_id,))
    if not cur.fetchone():
        conn.close()
        return 0, '目标分类不存在'
    cur.execute("UPDATE questions SET category_id = ? WHERE category_id = ? AND status = 1",
                (to_category_id, from_category_id))
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count, 'OK'


def batch_update_questions(question_ids, data):
    """批量更新题目属性，返回影响数量"""
    if not question_ids:
        return 0
    conn = get_db()
    cur = conn.cursor()
    fields = []
    values = []
    for key in ['difficulty', 'is_real_exam', 'source', 'status']:
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if fields:
        placeholders = ','.join(['?'] * len(question_ids))
        values.extend(question_ids)
        cur.execute(f"UPDATE questions SET {', '.join(fields)} WHERE id IN ({placeholders})", values)
        count = cur.rowcount
        conn.commit()
    else:
        count = 0
    conn.close()
    return count


def create_import_log(data):
    """记录导入日志"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO import_logs (
            operator, file_name, file_type, subject_id, subject_name,
            imported, errors, skipped, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('operator', 'admin'),
        data.get('file_name', ''),
        data.get('file_type', ''),
        data.get('subject_id', 0),
        data.get('subject_name', ''),
        data.get('imported', 0),
        data.get('errors', 0),
        data.get('skipped', 0),
        data.get('status', 'success'),
    ))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_import_logs(page=1, per_page=20):
    """获取导入日志列表"""
    conn = get_db()
    cur = conn.cursor()
    offset = (page - 1) * per_page
    cur.execute("SELECT * FROM import_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, offset))
    logs = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM import_logs")
    total = cur.fetchone()[0]
    conn.close()
    return logs, total


def delete_import_log(log_id):
    """删除导入日志记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM import_logs WHERE id = ?", (log_id,))
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


# ==================== FSRS 核心函数 ====================
import math
import random

# FSRS 常量
FSRS_DECAY = 0.9  # 遗忘衰减系数

def get_interval(stability, desired_retention=0.9):
    """由稳定性和目标保留率计算下次复习间隔（天）
    
    遗忘曲线: R(t) = e^(-decay × t / S)
    反推: t = S × (-ln(R) / decay)
    """
    return stability * (-math.log(desired_retention) / FSRS_DECAY)


def get_retrievability(stability, delta_t):
    """计算当前保留率 R = e^(-decay × t / S)
    
    stability: 记忆稳定性（天）
    delta_t: 距上次复习的天数
    返回值: 0~1 之间的保留率
    """
    if stability <= 0 or delta_t < 0:
        return 1.0
    return math.exp(-FSRS_DECAY * delta_t / stability)


def update_stability(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """根据评分和复习时机更新记忆稳定性
    
    quality:   0~4（忘了/模糊/一般/简单/秒答）
    stability: 当前稳定性（天）
    difficulty: 当前难度（1~10）
    delta_t:   距上次复习的天数（>=0）
    
    核心设计：
    - 答对时：delta_t > stability → 增益大（超出记忆极限答对）
    - 答对时：delta_t < stability → 增益小（还没到遗忘时间）
    - 答错时：按比例衰减，不归零（保留 15% 最低值）
    """
    if delta_t <= 0:
        delta_t = 0.01
    
    # 质量因子：0.2 ~ 1.0
    quality_factor = (quality + 1) / 5.0
    
    if quality >= 2:  # 答对（一般/简单/秒答）
        # 增益 = 质量因子 × sqrt(实际间隔 / 当前稳定性)
        growth_ratio = math.sqrt(delta_t / stability) if stability > 0 else 1.0
        gain = quality_factor * 0.28 * growth_ratio
        new_stability = stability * (1 + gain)
    else:  # 答错（忘了/模糊）
        # 遗忘因子：0.5(忘了) ~ 1.0(模糊)
        forget_factor = (1 - quality / 2.0)
        # 稳定性按比例衰减，最低保留 15%
        decay_ratio = 1.0 - 0.35 * forget_factor * min(delta_t / stability, 2.0) if stability > 0 else 0.5
        new_stability = stability * max(decay_ratio, 0.15)
    
    return max(new_stability, 0.1)  # 最低 0.1 天


def update_difficulty(quality, difficulty):
    """根据评分更新题目难度
    
    答对 → 难度降低
    答错 → 难度升高
    边界衰减：接近极值时变化更慢
    """
    # 基础变化：-0.6 ~ +0.6
    delta = (2.0 - quality) * 0.3
    
    # 边界衰减
    if delta < 0:  # 降难度
        delta *= (difficulty - 1.0) / 9.0
    else:  # 升难度
        delta *= (10.0 - difficulty) / 9.0
    
    return max(1.0, min(10.0, difficulty + delta))


def init_memory_state(quality):
    """新题首次答题后初始化记忆状态
    
    quality: 0~4
    返回: (stability, difficulty)
    """
    stability_map = {
        0: 0.5,   # 忘了 → 0.5天（12小时后复习）
        1: 1.0,   # 模糊 → 1天
        2: 2.0,   # 一般 → 2天
        3: 4.0,   # 简单 → 4天
        4: 7.0,   # 秒答 → 7天
    }
    difficulty_map = {
        0: 8.0,  # 忘了 → 很难
        1: 6.5,  # 模糊 → 较难
        2: 5.0,  # 一般 → 中等
        3: 3.5,  # 简单 → 较易
        4: 2.0,  # 秒答 → 容易
    }
    return stability_map.get(quality, 2.0), difficulty_map.get(quality, 5.0)


def apply_fuzz(interval):
    """给复习间隔添加 ±10% 的随机偏移，避免洪峰
    
    interval <= 1 天不 fuzz
    返回值向上取整到至少 1 天（数据库 interval 是整数）
    """
    if interval <= 1:
        return 1
    fuzz_range = max(1, int(interval * 0.1))
    return max(1, int(interval + random.randint(-fuzz_range, fuzz_range)))


def fsrs_schedule(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """FSRS 完整调度：输入当前状态 + 评分，输出新状态 + 下次间隔
    
    quality:   0~4
    stability: 当前稳定性（天）
    difficulty: 当前难度（1~10）
    delta_t:   距上次复习的天数
    desired_retention: 目标保留率
    
    返回: (new_stability, new_difficulty, interval)
    """
    new_stability = update_stability(quality, stability, difficulty, delta_t, desired_retention)
    new_difficulty = update_difficulty(quality, difficulty)
    interval = get_interval(new_stability, desired_retention)
    interval = apply_fuzz(interval)
    return new_stability, new_difficulty, interval


# FSRS 开关：设为 True 启用 FSRS，False 使用 SM-2
# ⚠️ 默认关闭，待全部改造完成并测试通过后再手动开启
USE_FSRS = True

# 学习步骤配置（分钟）
LEARNING_STEPS = [1, 10]  # 第1步: 1分钟后, 第2步: 10分钟后

# 重学机制：复习答错后保留的稳定性比例
RELEARNING_STABILITY_KEEP = 0.3  # 保留30%稳定性，不全部重置


# ==================== SM-2 复习计划 ====================

def sm2_schedule(quality, ease_factor, interval, repetitions):
    """SM-2 间隔重复算法（适配 0-4 评分）
    
    quality: 0=忘了, 1=模糊, 2=一般, 3=简单, 4=秒答
    原版公式基于 0-5 评分，直接套用到 0-4 会导致 quality=4 增量反常（+0.02 < quality=3 的 +0.10）。
    改用查表法，确保 ease 增量随质量单调递增。
    """
    delta_map = {
        0: -0.50,  # 忘了：大幅下降
        1: -0.30,  # 模糊：适度下降
        2: 0.00,   # 一般：不变
        3: +0.10,  # 简单：小幅增长
        4: +0.25,  # 秒答：较大增长
    }
    new_ease = ease_factor + delta_map.get(quality, 0)
    new_ease = max(1.3, new_ease)
    
    if quality < 2:
        # 失败：重置（interval=0 表示立即复习）
        new_reps = 0
        new_interval = 0 if quality == 0 else 1
    else:
        new_reps = repetitions + 1
        if new_reps == 1:
            new_interval = 1
        elif new_reps == 2:
            new_interval = 3
        else:
            new_interval = max(1, int(interval * new_ease))
    
    return {
        "ease_factor": round(new_ease, 2),
        "interval": new_interval,
        "repetitions": new_reps,
    }


def get_due_questions(user_id, category_id=None, subject_id=None, limit=20):
    """获取到期需要复习的题目
    
    排序策略：
    - FSRS 模式：按保留率升序（最易忘优先），同保留率按到期时间
    - SM-2 模式：按到期时间升序（保持原行为）
    """
    from datetime import datetime
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    cur = conn.cursor()
    
    # SQL 统一查询，加入 last_review 和 stability 字段
    base_sql = """
        SELECT q.*, rs.ease_factor, rs.interval, rs.repetitions,
               rs.next_review, rs.last_review, rs.last_quality,
               rs.stability, rs.difficulty
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.status = 1 AND rs.next_review <= ?
    """
    
    if category_id:
        sql = base_sql + " AND q.category_id = ? ORDER BY rs.next_review ASC LIMIT ?"
        params = [user_id, now_str, category_id, limit]
    elif subject_id:
        sql = base_sql + " AND q.subject_id = ? ORDER BY rs.next_review ASC LIMIT ?"
        params = [user_id, now_str, subject_id, limit]
    else:
        sql = base_sql + " ORDER BY rs.next_review ASC LIMIT ?"
        params = [user_id, now_str, limit]
    
    cur.execute(sql, params)
    due = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    # FSRS 模式：按保留率重新排序
    if USE_FSRS and due:
        for row in due:
            last_review_str = row.get('last_review')
            stability = row.get('stability') or 1.0
            
            if last_review_str:
                last_review = datetime.strptime(last_review_str, '%Y-%m-%d %H:%M:%S')
                delta_t = (now - last_review).total_seconds() / 86400
                row['retrievability'] = get_retrievability(stability, delta_t)
            else:
                row['retrievability'] = 0.0  # 无记录视为易忘
        
        due.sort(key=lambda x: x['retrievability'])
    
    return due


def get_new_questions(user_id, category_id=None, limit=5):
    """获取用户还未复习过的新题目
    
    参数:
        category_id: 必填。按分类取新题。
    注意:
        不传 category_id 时无法确定科目范围，返回空列表。
    """
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
        # 不传 category_id 时无法确定科目范围，返回空
        return []
    
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


# ==================== 每日学习上限 ====================

def get_study_limits(user_id, subject_id):
    """获取用户某科目的每日学习上限和配置
    
    返回: {daily_new_limit, daily_review_limit, desired_retention, max_interval, learning_steps}
    """
    import json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT daily_new_limit, daily_review_limit, desired_retention, max_interval, learning_steps
        FROM study_limits WHERE user_id = ? AND subject_id = ?
    """, (user_id, subject_id))
    row = cur.fetchone()
    conn.close()
    if row:
        try:
            ls = json.loads(row[4]) if row[4] else None
        except (json.JSONDecodeError, TypeError):
            ls = None
        return {
            'daily_new_limit': row[0],
            'daily_review_limit': row[1],
            'desired_retention': row[2] if row[2] is not None else 0.9,
            'max_interval': row[3] if row[3] is not None else 365,
            'learning_steps': ls or LEARNING_STEPS,
        }
    return {
        'daily_new_limit': 10, 'daily_review_limit': 50, 'desired_retention': 0.9,
        'max_interval': 365, 'learning_steps': LEARNING_STEPS,
    }


def set_study_limits(user_id, subject_id, daily_new_limit=None, daily_review_limit=None, desired_retention=None, max_interval=None, learning_steps=None):
    """设置用户某科目的每日学习上限和配置
    
    仅传入要修改的值，未传入的使用默认值。
    """
    import json
    defaults = get_study_limits(user_id, subject_id)
    new_limit = daily_new_limit if daily_new_limit is not None else defaults['daily_new_limit']
    review_limit = daily_review_limit if daily_review_limit is not None else defaults['daily_review_limit']
    dr = desired_retention if desired_retention is not None else defaults['desired_retention']
    mi = max_interval if max_interval is not None else defaults['max_interval']
    ls = json.dumps(learning_steps) if learning_steps is not None else json.dumps(defaults['learning_steps'])
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO study_limits (user_id, subject_id, daily_new_limit, daily_review_limit, desired_retention, max_interval, learning_steps)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, subject_id) DO UPDATE SET
            daily_new_limit = excluded.daily_new_limit,
            daily_review_limit = excluded.daily_review_limit,
            desired_retention = excluded.desired_retention,
            max_interval = excluded.max_interval,
            learning_steps = excluded.learning_steps
    """, (user_id, subject_id, new_limit, review_limit, dr, mi, ls))
    conn.commit()
    conn.close()
    return {
        'daily_new_limit': new_limit, 'daily_review_limit': review_limit,
        'desired_retention': dr, 'max_interval': mi, 'learning_steps': learning_steps or defaults['learning_steps'],
    }


def get_daily_study_count(user_id, subject_id):
    """获取用户今日已答题数（区分新题和复习）
    
    新题：答题时不在 review_schedule 中的题目
    复习：答题时已在 review_schedule 中的题目
    
    返回: {new_count, review_count, date}
    """
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db()
    cur = conn.cursor()
    
    # 今日新题数：答题时不在 review_schedule 中
    # 用子查询近似：首次答题记录（该题第一条 history）且今日产生
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT h.question_id
            FROM history h
            WHERE h.user_id = ? AND h.subject_id = ?
            AND DATE(h.timestamp) = ?
            AND h.id = (
                SELECT MIN(h2.id) FROM history h2
                WHERE h2.user_id = h.user_id AND h2.question_id = h.question_id
            )
        )
    """, (user_id, subject_id, today))
    new_count = cur.fetchone()[0]
    
    # 今日复习数：今日答题总数 - 新题数
    cur.execute("""
        SELECT COUNT(*) FROM history
        WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) = ?
    """, (user_id, subject_id, today))
    total_count = cur.fetchone()[0]
    review_count = total_count - new_count
    
    conn.close()
    return {'new_count': new_count, 'review_count': review_count, 'total_count': total_count, 'date': today}


def can_do_new_question(user_id, subject_id):
    """检查是否还能做新题（未达上限）
    
    返回: {can_do: bool, current: int, limit: int}
    """
    limits = get_study_limits(user_id, subject_id)
    count = get_daily_study_count(user_id, subject_id)
    remaining = limits['daily_new_limit'] - count['new_count']
    return {
        'can_do': remaining > 0,
        'current': count['new_count'],
        'limit': limits['daily_new_limit'],
        'remaining': max(0, remaining),
    }


def can_do_review(user_id, subject_id):
    """检查是否还能复习（未达上限）
    
    返回: {can_do: bool, current: int, limit: int}
    """
    limits = get_study_limits(user_id, subject_id)
    count = get_daily_study_count(user_id, subject_id)
    remaining = limits['daily_review_limit'] - count['review_count']
    return {
        'can_do': remaining > 0,
        'current': count['review_count'],
        'limit': limits['daily_review_limit'],
        'remaining': max(0, remaining),
    }


# ==================== 复习调度 ====================

def _mastered_sql_condition():
    """返回 SQL 中的"已掌握"条件子句
    
    FSRS 模式: stability >= 21 AND repetitions >= 5 AND difficulty <= 4.0
    SM-2 模式: repetitions >= 3 AND ease_factor >= 2.5 AND interval >= 15
    """
    if USE_FSRS:
        return 'rs.stability >= 21 AND rs.repetitions >= 5 AND rs.difficulty <= 4.0'
    else:
        return 'rs.repetitions >= 3 AND rs.ease_factor >= 2.5 AND rs.interval >= 15'


def is_question_mastered(user_id, question_id):
    """判断题目是否已掌握
    
    FSRS 模式: stability >= 21 AND repetitions >= 5 AND difficulty <= 4.0
    SM-2 模式: repetitions >= 3 AND ease_factor >= 2.5 AND interval >= 15
    """
    conn = get_db()
    cur = conn.cursor()
    
    if USE_FSRS:
        cur.execute("""
            SELECT repetitions, stability, difficulty FROM review_schedule
            WHERE user_id = ? AND question_id = ?
        """, (user_id, question_id))
        row = cur.fetchone()
        conn.close()
        if not row:
            return False
        r = dict(row)
        s = r.get('stability') or 0
        d = r.get('difficulty') or 10
        return r['repetitions'] >= 5 and s >= 21 and d <= 4.0
    else:
        cur.execute("""
            SELECT repetitions, ease_factor, interval FROM review_schedule
            WHERE user_id = ? AND question_id = ?
        """, (user_id, question_id))
        row = cur.fetchone()
        conn.close()
        if not row:
            return False
        return row['repetitions'] >= 3 and row['ease_factor'] >= 2.5 and row['interval'] >= 15


def get_review_schedule(user_id, question_id):
    """获取题目的复习计划记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM review_schedule
        WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_review_schedule(user_id, question_id, subject_id, quality):
    """根据评分更新复习计划
    
    双路径设计：
    - USE_FSRS=True: 使用 FSRS 算法（stability + difficulty + delta_t）
    - USE_FSRS=False: 使用 SM-2 算法（ease_factor + interval + repetitions）
    
    FSRS 学习步骤：新题先进入学习阶段（1min → 10min），全部通过后进入正式复习
    """
    from datetime import datetime, timedelta, date
    
    # 获取用户的科目配置
    limits = get_study_limits(user_id, subject_id)
    dr = limits['desired_retention']
    learning_steps = limits['learning_steps']
    max_interval = limits['max_interval']
    
    conn = get_db()
    cur = conn.cursor()
    
    # 获取复习记录
    if USE_FSRS:
        cur.execute("""
            SELECT ease_factor, interval, repetitions, stability, difficulty, last_review,
                   card_state, learning_step
            FROM review_schedule
            WHERE user_id = ? AND question_id = ?
        """, (user_id, question_id))
    else:
        cur.execute("""
            SELECT ease_factor, interval, repetitions FROM review_schedule
            WHERE user_id = ? AND question_id = ?
        """, (user_id, question_id))
    
    existing_row = cur.fetchone()
    
    if USE_FSRS:
        # ── FSRS 路径 ──
        if existing_row:
            existing = dict(existing_row)
            card_state = existing.get('card_state') or 'review'
            learning_step = existing.get('learning_step') or 0
            
            if card_state in ('learning', 'relearning'):
                # ── 学习/重学阶段 ──
                if quality >= 2:  # 答对，进入下一步
                    next_step = learning_step + 1
                    if next_step >= len(learning_steps):
                        # 所有步骤通过
                        if card_state == 'relearning':
                            # 重学完成：保留30%稳定性基础上，基于评分小幅增长
                            base_s = existing.get('stability') or 1.0
                            new_stability = base_s * (1.0 + quality * 0.3)  # 根据评分增长
                            new_difficulty = existing.get('difficulty') or 5.0
                        else:
                            # 新题学习完成：初始化
                            new_stability, new_difficulty = init_memory_state(quality)
                        new_interval = get_interval(new_stability, dr)
                        new_interval = apply_fuzz(max(1, round(new_interval)))
                        new_interval = min(new_interval, max_interval)  # 不超过最大间隔
                        new_reps = existing['repetitions'] + 1
                        new_ease = existing['ease_factor']
                        card_state = 'review'
                        learning_step = 0
                        # 下次复习按 FSRS 间隔（天）
                        now = datetime.now()
                        base_review = datetime.combine(
                            date.today() + timedelta(days=1), datetime.min.time()
                        )
                        if new_interval > 1:
                            base_review += timedelta(days=new_interval - 1)
                        next_review = _balance_review_date(
                            user_id, subject_id, base_review, new_interval
                        )
                    else:
                        # 进入下一步学习（分钟级间隔）
                        new_stability = existing.get('stability') or 1.0
                        new_difficulty = existing.get('difficulty') or 5.0
                        new_interval = 0  # 学习阶段用分钟，interval=0
                        new_reps = existing['repetitions'] + 1
                        new_ease = existing['ease_factor']
                        # 保持当前状态（relearning 保持 relearning）
                        learning_step = next_step
                        # 下次复习在 N 分钟后
                        now = datetime.now()
                        next_review = now + timedelta(minutes=learning_steps[next_step])
                else:
                    # 答错，重做当前步骤
                    new_stability = existing.get('stability') or 1.0
                    new_difficulty = existing.get('difficulty') or 5.0
                    new_interval = 0
                    new_reps = existing['repetitions'] + 1
                    new_ease = existing['ease_factor']
                    # 保持当前状态
                    # 重做当前步骤（保持 learning_step 不变）
                    now = datetime.now()
                    next_review = now + timedelta(minutes=learning_steps[learning_step])
            else:
                # ── 正式复习阶段 ──
                stability = existing.get('stability') or 1.0
                difficulty = existing.get('difficulty') or 5.0
                reps = existing['repetitions']
                
                if quality < 2:
                    # 答错 → 进入重学阶段
                    new_stability = stability * RELEARNING_STABILITY_KEEP
                    _, new_difficulty = init_memory_state(quality)
                    new_interval = 0
                    new_reps = reps + 1
                    new_ease = existing['ease_factor']
                    card_state = 'relearning'
                    learning_step = 0
                    now = datetime.now()
                    next_review = now + timedelta(minutes=learning_steps[0])
                else:
                    # 答对 → 正常 FSRS 调度
                    # 计算 delta_t（距上次复习的天数）
                    last_review_str = existing.get('last_review')
                    if last_review_str:
                        last_review = datetime.strptime(last_review_str, '%Y-%m-%d %H:%M:%S')
                        delta_t = (datetime.now() - last_review).total_seconds() / 86400
                    else:
                        delta_t = 0.01
                    
                    new_stability, new_difficulty, new_interval = fsrs_schedule(
                        quality, stability, difficulty, delta_t, dr
                    )
                    new_reps = reps + 1
                    new_ease = existing['ease_factor']
                    card_state = 'review'
                    learning_step = 0
                    
                    now = datetime.now()
                    if new_interval == 0:
                        next_review = now
                    else:
                        base_review = datetime.combine(
                            date.today() + timedelta(days=1), datetime.min.time()
                        )
                        if new_interval > 1:
                            base_review += timedelta(days=new_interval - 1)
                        next_review = _balance_review_date(
                            user_id, subject_id, base_review, new_interval
                        )
        else:
            # 新题首次进入学习阶段
            new_stability, new_difficulty = init_memory_state(quality)
            if quality >= 2:  # 答对，进入下一步学习
                new_interval = 0
                new_reps = 1
                new_ease = 2.5
                card_state = 'learning'
                learning_step = 0
                now = datetime.now()
                if len(learning_steps) > 1:
                    # 有多步学习，进入第2步
                    next_review = now + timedelta(minutes=learning_steps[1])
                    learning_step = 1
                else:
                    # 只有1步学习，直接进入复习
                    new_stability, new_difficulty = init_memory_state(quality)
                    new_interval = get_interval(new_stability, dr)
                    new_interval = apply_fuzz(max(1, round(new_interval)))
                    new_interval = min(new_interval, max_interval)  # 不超过最大间隔
                    card_state = 'review'
                    learning_step = 0
                    next_review = datetime.combine(
                        date.today() + timedelta(days=1), datetime.min.time()
                    )
                    if new_interval > 1:
                        next_review += timedelta(days=new_interval - 1)
            else:
                # 答错，重做第1步
                new_interval = 0
                new_reps = 1
                new_ease = 2.5
                card_state = 'learning'
                learning_step = 0
                now = datetime.now()
                next_review = now + timedelta(minutes=learning_steps[0])
    else:
        # ── SM-2 路径（原逻辑，不变） ──
        if existing_row:
            ease = existing_row['ease_factor']
            interval = existing_row['interval']
            reps = existing_row['repetitions']
        else:
            ease = 2.5
            interval = 0
            reps = 0
        
        result = sm2_schedule(quality, ease, interval, reps)
        new_ease = result['ease_factor']
        new_interval = result['interval']
        new_reps = result['repetitions']
        new_stability = None
        new_difficulty = None
        # SM-2 的 next_review 在后续统一计算

    # 计算下次复习时间（仅 SM-2 路径，FSRS 路径已自行处理）
    if not USE_FSRS:
        now = datetime.now()
        if new_interval == 0:
            next_review = now
        else:
            next_review = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
            if new_interval > 1:
                next_review += timedelta(days=new_interval - 1)
    # FSRS 路径的 next_review 已在上面分支中设置
    
    if existing_row:
        if USE_FSRS:
            cur.execute("""
                UPDATE review_schedule
                SET ease_factor = ?, interval = ?, repetitions = ?,
                    next_review = ?, last_review = ?, last_quality = ?,
                    stability = ?, difficulty = ?,
                    card_state = ?, learning_step = ?
                WHERE user_id = ? AND question_id = ?
            """, (new_ease, new_interval, new_reps,
                  next_review.strftime('%Y-%m-%d %H:%M:%S'),
                  now.strftime('%Y-%m-%d %H:%M:%S'),
                  quality, round(new_stability, 2), round(new_difficulty, 2),
                  card_state, learning_step,
                  user_id, question_id))
        else:
            cur.execute("""
                UPDATE review_schedule
                SET ease_factor = ?, interval = ?, repetitions = ?,
                    next_review = ?, last_review = ?, last_quality = ?
                WHERE user_id = ? AND question_id = ?
            """, (new_ease, new_interval, new_reps,
                  next_review.strftime('%Y-%m-%d %H:%M:%S'),
                  now.strftime('%Y-%m-%d %H:%M:%S'),
                  quality, user_id, question_id))
    else:
        if USE_FSRS:
            cur.execute("""
                INSERT INTO review_schedule (user_id, question_id, subject_id, ease_factor, interval, repetitions, next_review, last_review, last_quality, stability, difficulty, card_state, learning_step)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, question_id, subject_id,
                  new_ease, new_interval, new_reps,
                  next_review.strftime('%Y-%m-%d %H:%M:%S'),
                  now.strftime('%Y-%m-%d %H:%M:%S'),
                  quality, round(new_stability, 2), round(new_difficulty, 2),
                  card_state, learning_step))
        else:
            cur.execute("""
                INSERT INTO review_schedule (user_id, question_id, subject_id, ease_factor, interval, repetitions, next_review, last_review, last_quality)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, question_id, subject_id,
                  new_ease, new_interval, new_reps,
                  next_review.strftime('%Y-%m-%d %H:%M:%S'),
                  now.strftime('%Y-%m-%d %H:%M:%S'),
                  quality))
    
    conn.commit()
    conn.close()
    
    # 返回兼容两种算法的结果
    return {
        'ease_factor': new_ease,
        'interval': new_interval,
        'repetitions': new_reps,
        'stability': round(new_stability, 2) if USE_FSRS else None,
        'difficulty': round(new_difficulty, 2) if USE_FSRS else None,
    }


def delete_review_schedule(user_id, question_id):
    """删除复习计划记录（取消掌握）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'DELETE FROM review_schedule WHERE user_id = ? AND question_id = ?',
        (user_id, question_id)
    )
    conn.commit()
    conn.close()


def predict_review_load(user_id, subject_id, days=30):
    """预测未来 N 天每日复习量
    
    根据 review_schedule 中每道题的 next_review 和 interval，
    推算未来 N 天内每天的到期题目数量。
    
    返回: {"2026-05-03": 5, "2026-05-04": 8, ...}
    """
    from datetime import datetime, timedelta
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id as question_id, rs.next_review, rs.interval, rs.card_state
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ? AND rs.card_state = 'review'
    """, (user_id, subject_id))
    schedules = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    load = {}
    today = datetime.now().date()
    
    for s in schedules:
        try:
            next_review = datetime.strptime(s['next_review'], '%Y-%m-%d %H:%M:%S').date()
        except (ValueError, TypeError):
            continue
        
        interval = max(1, int(s['interval']) if s['interval'] else 1)
        
        # 找到未来 N 天内的所有到期日
        current = next_review
        while current <= today + timedelta(days=days):
            if current >= today:
                key = current.strftime('%Y-%m-%d')
                load[key] = load.get(key, 0) + 1
            current += timedelta(days=interval)
    
    return load


def calculate_optimal_dr(user_id, subject_id):
    """基于历史数据计算推荐目标保留率
    
    算法：
    - 分析该科目历史正确率
    - 分析平均间隔和稳定性
    - 正确率 > 90% → DR 0.85（可放宽，减少复习量）
    - 正确率 70-90% → DR 0.90（默认）
    - 正确率 < 70% → DR 0.95（需加强巩固）
    
    返回: {recommended_dr, accuracy, avg_stability, total_answers, reason}
    """
    conn = get_db()
    cur = conn.cursor()
    
    # 历史正确率
    cur.execute("""
        SELECT h.correct, COUNT(*) as cnt
        FROM history h
        JOIN questions q ON q.id = h.question_id
        WHERE h.user_id = ? AND q.subject_id = ?
        GROUP BY h.correct
    """, (user_id, subject_id))
    counts = dict(cur.fetchall())
    total = counts.get(1, 0) + counts.get(0, 0)
    
    if total < 10:
        conn.close()
        return {
            'recommended_dr': 0.90,
            'accuracy': 0,
            'avg_stability': 0,
            'total_answers': total,
            'reason': f'数据不足（仅 {total} 条），建议默认 DR 0.90'
        }
    
    accuracy = counts.get(1, 0) / total
    
    # 平均稳定性
    cur.execute("""
        SELECT AVG(stability) FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ? AND rs.card_state = 'review'
    """, (user_id, subject_id))
    avg_stability = cur.fetchone()[0] or 0
    
    # 推荐 DR
    if accuracy >= 0.90:
        recommended_dr = 0.85
        reason = '正确率很高，可适当放宽 DR 以减少复习量'
    elif accuracy >= 0.70:
        recommended_dr = 0.90
        reason = '正确率中等，建议保持默认 DR'
    else:
        recommended_dr = 0.95
        reason = '正确率较低，建议提高 DR 以加强巩固'
    
    conn.close()
    return {
        'recommended_dr': recommended_dr,
        'accuracy': round(accuracy * 100, 1),
        'avg_stability': round(avg_stability, 1),
        'total_answers': total,
        'reason': reason
    }


def _balance_review_date(user_id, subject_id, preferred_date, interval):
    """负载均衡：如果目标日期已满，将复习日期偏移到附近低负载日
    
    Args:
        user_id: 用户ID
        subject_id: 科目ID
        preferred_date: datetime 首选日期
        interval: 间隔天数（用于确定最大偏移范围）
    
    Returns:
        datetime 平衡后的日期
    """
    if interval < 1:
        return preferred_date
    
    # 最大偏移：interval 的 20%，最少 1 天，最多 3 天
    max_offset = max(1, min(3, int(interval * 0.2)))
    
    conn = get_db()
    cur = conn.cursor()
    
    # 统计每天的到期题数（仅 review 状态）
    preferred_str = preferred_date.strftime('%Y-%m-%d')
    date_range = [preferred_date + timedelta(days=d) for d in range(-max_offset, max_offset + 1)]
    
    cur.execute("""
        SELECT DATE(next_review) as date, COUNT(*) as cnt
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ? AND rs.card_state = 'review'
          AND DATE(next_review) BETWEEN ? AND ?
        GROUP BY DATE(next_review)
    """, (
        user_id, subject_id,
        (preferred_date - timedelta(days=max_offset)).strftime('%Y-%m-%d'),
        (preferred_date + timedelta(days=max_offset)).strftime('%Y-%m-%d')
    ))
    load = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    
    # 找出负载最低的日期
    best_date = preferred_date
    best_load = load.get(preferred_str, 0)
    
    for d in date_range:
        key = d.strftime('%Y-%m-%d')
        current_load = load.get(key, 0)
        if current_load < best_load:
            best_load = current_load
            best_date = d
    
    return best_date


def get_due_today(user_id, category_id):
    """获取今日待复习的题目（next_review <= now）
    
    排序策略：
    - FSRS 模式：按保留率升序（最易忘优先）
    - SM-2 模式：按到期时间升序（保持原行为）
    """
    from datetime import datetime
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.*, rs.ease_factor, rs.interval, rs.repetitions,
               rs.next_review, rs.last_review, rs.last_quality,
               rs.stability, rs.difficulty, rs.card_state, rs.learning_step
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.category_id = ? AND q.status = 1 AND rs.next_review <= ?
        ORDER BY rs.next_review ASC
    """, (user_id, category_id, now_str))
    due = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    # FSRS 模式：按保留率重新排序
    if USE_FSRS and due:
        for row in due:
            last_review_str = row.get('last_review')
            stability = row.get('stability') or 1.0
            
            if last_review_str:
                last_review = datetime.strptime(last_review_str, '%Y-%m-%d %H:%M:%S')
                delta_t = (now - last_review).total_seconds() / 86400
                row['retrievability'] = get_retrievability(stability, delta_t)
            else:
                row['retrievability'] = 0.0
        
        due.sort(key=lambda x: x['retrievability'])
    
    return due


def get_learning_cards(user_id, category_id):
    """获取处于学习/重学阶段的题目
    
    返回: [{id, card_state, learning_step, next_review, minutes_left, ...}]
    """
    from datetime import datetime
    now = datetime.now()
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id, q.stem, rs.card_state, rs.learning_step, rs.next_review
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ? 
          AND rs.card_state IN ('learning', 'relearning')
        ORDER BY rs.next_review ASC
    """, (user_id, category_id))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    for row in rows:
        try:
            next_review = datetime.strptime(row['next_review'], '%Y-%m-%d %H:%M:%S')
            delta = (next_review - now).total_seconds() / 60
            row['minutes_left'] = max(0, round(delta))
            row['state_label'] = '🟡 学习中' if row['card_state'] == 'learning' else '🔴 重学中'
            row['badge_class'] = 'badge-learning' if row['card_state'] == 'learning' else 'badge-relearning'
        except (ValueError, TypeError):
            row['minutes_left'] = 0
    
    return rows


def get_study_progress(user_id, category_id):
    """获取分类学习进度统计（已学习/已掌握/待复习/评分分布）"""
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    cur = conn.cursor()

    # 总题目数
    cur.execute("SELECT COUNT(*) FROM questions WHERE category_id = ? AND status = 1", (category_id,))
    total = cur.fetchone()[0]

    # 已复习（有复习记录）
    cur.execute("""
        SELECT COUNT(DISTINCT q.id) FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.category_id = ?
    """, (user_id, category_id))
    reviewed = cur.fetchone()[0]

    # 已掌握
    cur.execute(f"""
        SELECT COUNT(*) FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ?
        AND {_mastered_sql_condition()}
    """, (user_id, category_id))
    mastered = cur.fetchone()[0]

    # 今日待复习
    cur.execute("""
        SELECT COUNT(*) FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ?
        AND rs.next_review <= ?
    """, (user_id, category_id, now))
    due_today = cur.fetchone()[0]

    # 评分分布（根据 last_quality 直接读取，旧记录兜底推断）
    cur.execute("""
        SELECT rs.ease_factor, rs.repetitions, rs.interval, rs.last_quality FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ?
    """, (user_id, category_id))
    records = [dict(r) for r in cur.fetchall()]

    # 统计已答题总数（含未进入复习计划的）
    cur.execute("""
        SELECT COUNT(DISTINCT h.question_id) FROM history h
        JOIN questions q ON q.id = h.question_id
        WHERE h.user_id = ? AND q.category_id = ?
    """, (user_id, category_id))
    answered_total = cur.fetchone()[0]

    # 已评分 = review_schedule 中有有效 last_quality 的记录
    # 未评分 = 答过题但 review_schedule 中 last_quality 为 NULL
    rated_count = sum(1 for r in records if r.get('last_quality') is not None)
    unrated_count = answered_total - rated_count

    dist = {'忘了': 0, '模糊': 0, '一般': 0, '简单': 0, '秒答': 0, '未评分': 0}
    for r in records:
        dist[infer_quality(r)] += 1
    dist['未评分'] = max(0, unrated_count)

    conn.close()
    return {
        'total': total,
        'reviewed': reviewed,
        'mastered': mastered,
        'due_today': due_today,
        'distribution': dist,
        'new': total - reviewed,
    }


def get_question_attempt_stats(user_id, category_id):
    """获取分类下每道题的做题次数统计"""
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            q.id,
            q.stem,
            COUNT(h.id) as attempt_count,
            CASE WHEN COUNT(h.id) > 0
                THEN ROUND(SUM(h.correct) * 100.0 / COUNT(h.id), 1)
                ELSE 0
            END as accuracy,
            rs.ease_factor,
            rs.repetitions,
            rs.interval,
            rs.next_review,
            rs.last_quality
        FROM questions q
        LEFT JOIN history h ON h.question_id = q.id AND h.user_id = ?
        LEFT JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.category_id = ? AND q.status = 1
        GROUP BY q.id
    """, (user_id, user_id, category_id))
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        row = dict(r)
        row['inferred_quality'] = infer_quality(row) if row['repetitions'] is not None else None
        # 下次复习时间描述（增强：显示小时级倒计时）
        if row['next_review']:
            from datetime import datetime as dt
            next_rev = dt.strptime(row['next_review'], '%Y-%m-%d %H:%M:%S')
            now = dt.now()
            diff_seconds = (next_rev - now).total_seconds()
            diff_days = (next_rev - now).days
            if diff_seconds < 0:
                # 已过期
                if diff_seconds > -86400:
                    hours_ago = int(abs(diff_seconds) / 3600)
                    row['next_review_label'] = f'已过期 {hours_ago}h' if hours_ago > 0 else '已过期'
                else:
                    row['next_review_label'] = f'{abs(diff_days)}天前'
            elif diff_seconds < 3600:
                # 1小时内到期
                mins = int(diff_seconds / 60)
                row['next_review_label'] = f'{mins}分钟后可复习'
            elif diff_seconds < 86400:
                # 今天到期
                hours = int(diff_seconds / 3600)
                row['next_review_label'] = f'{hours}小时后可复习'
            elif diff_days == 1:
                row['next_review_label'] = '明天可复习'
            elif diff_days < 7:
                row['next_review_label'] = f'{diff_days}天后可复习'
            else:
                row['next_review_label'] = f'{diff_days}天后可复习'
        else:
            row['next_review_label'] = '未开始'
        results.append(row)
    return results


def infer_quality(record):
    """根据SM-2记录推断上次评分倾向
    
    优先读取 last_quality（原始评分值），旧记录兜底推断。
    兼容 dict 和 sqlite3.Row 两种类型。
    """
    quality_map = {0: '忘了', 1: '模糊', 2: '一般', 3: '简单', 4: '秒答'}
    
    # 直接读取 last_quality（兼容 dict 和 sqlite3.Row）
    try:
        data = dict(record)
    except (TypeError, ValueError):
        data = {}
    last_quality = data.get('last_quality')
    if last_quality is not None:
        return quality_map.get(last_quality, '一般')
    
    # 旧记录（last_quality 为 NULL）：根据 reps 兜底推断
    reps = data.get('repetitions', 0)
    if reps >= 3:
        return '秒答'
    elif reps >= 2:
        return '简单'
    elif reps >= 1:
        return '一般'
    else:
        return '模糊'


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
        WHERE subject_id = ? AND status = 1
          AND ROWID > (SELECT ROWID FROM questions WHERE id = ?)
        ORDER BY ROWID LIMIT 1
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
    cur.execute("""
        SELECT COUNT(*) FROM questions
        WHERE category_id = ? AND status = 1
          AND ROWID <= (SELECT ROWID FROM questions WHERE id = ?)
    """, (category_id, qid))
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


def get_unreviewed_questions(user_id, category_id, count=None):
    """获取分类下用户尚未练习过的题目（不在 review_schedule 中）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.* FROM questions q
        WHERE q.category_id = ? AND q.status = 1
          AND q.id NOT IN (
              SELECT rs.question_id FROM review_schedule rs WHERE rs.user_id = ?
          )
        ORDER BY q.id
    """, (category_id, user_id))
    rows = cur.fetchall()
    if count and count > 0 and count < len(rows):
        rows = rows[:count]
    conn.close()
    return rows


def get_unreviewed_count(user_id, category_id):
    """获取分类下新题数量"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM questions q
        WHERE q.category_id = ? AND q.status = 1
          AND q.id NOT IN (
              SELECT rs.question_id FROM review_schedule rs WHERE rs.user_id = ?
          )
    """, (category_id, user_id))
    result = cur.fetchone()[0]
    conn.close()
    return result


def get_mastered_questions(user_id, category_id):
    """获取已掌握的题目
    
    FSRS 模式: stability >= 21 AND repetitions >= 5 AND difficulty <= 4.0
    SM-2 模式: repetitions >= 3 AND ease_factor >= 2.5 AND interval >= 15
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT q.*, rs.ease_factor, rs.interval, rs.repetitions,
               rs.next_review, rs.last_review, rs.last_quality,
               rs.stability, rs.difficulty
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ? AND q.status = 1
          AND {_mastered_sql_condition()}
        ORDER BY rs.interval DESC, rs.repetitions DESC
    """, (user_id, category_id))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_subject_category_stats(user_id, subject_id):
    """
    获取科目下各分类的学习统计。
    返回 {"tree": tree, "subject_total": {...}}
    叶子分类直接查询，父分类从子分类递归聚合。
    """
    tree = get_categories_tree(subject_id)
    if not tree:
        return {"tree": [], "subject_total": {"total": 0, "studied": 0, "due": 0, "mastered": 0}}

    # 递归收集叶子节点（无子节点的分类）
    def collect_leaves(nodes):
        leaves = []
        for n in nodes:
            children = n.get("children", [])
            if not children:
                leaves.append(n["id"])
            else:
                leaves.extend(collect_leaves(children))
        return leaves

    leaf_ids = collect_leaves(tree)
    if not leaf_ids:
        return {"tree": tree, "subject_total": {"total": 0, "studied": 0, "due": 0, "mastered": 0}}

    placeholders = ",".join(["?"] * len(leaf_ids))
    conn = get_db()
    cur = conn.cursor()

    stats = {lid: {"total": 0, "studied": 0, "due": 0, "mastered": 0} for lid in leaf_ids}

    # 各分类总题量
    cur.execute(f"""
        SELECT category_id, COUNT(*) as cnt
        FROM questions
        WHERE category_id IN ({placeholders}) AND status = 1
        GROUP BY category_id
    """, leaf_ids)
    for row in cur.fetchall():
        stats[row["category_id"]]["total"] = row["cnt"]

    # 各分类已学习题数（已纳入复习系统）
    cur.execute(f"""
        SELECT q.category_id, COUNT(DISTINCT q.id) as cnt
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id
        WHERE q.category_id IN ({placeholders}) AND q.status = 1 AND rs.user_id = ?
        GROUP BY q.category_id
    """, leaf_ids + [user_id])
    for row in cur.fetchall():
        stats[row["category_id"]]["studied"] = row["cnt"]

    # 各分类待复习题数
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute(f"""
        SELECT q.category_id, COUNT(DISTINCT q.id) as cnt
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id
        WHERE q.category_id IN ({placeholders}) AND q.status = 1
          AND rs.user_id = ? AND rs.next_review <= ?
        GROUP BY q.category_id
    """, leaf_ids + [user_id, now_str])
    for row in cur.fetchall():
        stats[row["category_id"]]["due"] = row["cnt"]

    # 各分类已掌握题数
    cur.execute(f"""
        SELECT q.category_id, COUNT(DISTINCT q.id) as cnt
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id
        WHERE q.category_id IN ({placeholders}) AND q.status = 1
          AND rs.user_id = ? AND {_mastered_sql_condition()}
        GROUP BY q.category_id
    """, leaf_ids + [user_id])
    for row in cur.fetchall():
        stats[row["category_id"]]["mastered"] = row["cnt"]

    conn.close()

    # 递归聚合
    def aggregate(node):
        children = node.get("children", [])
        if not children:
            s = stats.get(node["id"], {"total": 0, "studied": 0, "due": 0, "mastered": 0})
            node["_stats"] = s
            return s
        total = {"total": 0, "studied": 0, "due": 0, "mastered": 0}
        for child in children:
            child_stats = aggregate(child)
            for k in total:
                total[k] += child_stats[k]
        node["_stats"] = total
        return total

    subject_total = {"total": 0, "studied": 0, "due": 0, "mastered": 0}
    for root in tree:
        root_stats = aggregate(root)
        for k in subject_total:
            subject_total[k] += root_stats[k]

    return {"tree": tree, "subject_total": subject_total}
