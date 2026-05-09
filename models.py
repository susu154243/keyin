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
    
    # 新版 Werkzeug 支持的所有格式（pbkdf2、scrypt 等）
    if pw_hash.startswith(('pbkdf2:', 'scrypt:', 'argon2:')):
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
    cur.execute("SELECT id, username, phone, role, status, last_login FROM users ORDER BY id")
    result = cur.fetchall()
    conn.close()
    return result


def create_user(username, password, role='user', email=None, phone=None):
    conn = get_db()
    cur = conn.cursor()
    try:
        # 如果提供了邮箱，status=0（待验证），否则 status=1（直接激活）
        status = 0 if email else 1
        cur.execute("INSERT INTO users (username, password_hash, role, status, email, phone) VALUES (?, ?, ?, ?, ?, ?)",
                    (username, hash_password(password), role, status, email, phone))
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


# ==================== 邮箱验证相关 ====================

def set_user_verification(user_id: int, token: str, expires: str):
    """保存用户邮箱验证 token 和过期时间"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET verification_token = ?, verification_expires = ? WHERE id = ?",
                (token, expires, user_id))
    conn.commit()
    conn.close()


def get_user_by_token(token: str):
    """根据验证 token 获取用户"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE verification_token = ?", (token,))
    user = cur.fetchone()
    conn.close()
    return user


def verify_email(user_id: int) -> bool:
    """验证邮箱：设置 status=1, email_verified=1，清除 token"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = 1, email_verified = 1, verification_token = NULL, verification_expires = NULL WHERE id = ?",
                (user_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def update_user_email(user_id: int, email: str, token: str, expires: str) -> bool:
    """更新用户邮箱（用于老用户首次绑定）"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET email = ?, email_verified = 0, verification_token = ?, verification_expires = ? WHERE id = ?",
                    (email, token, expires, user_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_user_email_status(user_id: int):
    """获取用户邮箱状态"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, email_verified FROM users WHERE id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result


def update_user_last_login(user_id):
    import time
    for attempt in range(3):
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
            conn.commit()
            conn.close()
            return
        except Exception:
            conn.close()
            time.sleep(0.2)


def set_user_session_token(user_id, token):
    """设置用户 session token（单设备登录）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET session_token = ? WHERE id = ?", (token, user_id))
    conn.commit()
    conn.close()


def clear_user_session_token(user_id):
    """清除用户 session token"""
    import time
    for attempt in range(3):
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE users SET session_token = NULL WHERE id = ?", (user_id,))
            conn.commit()
            conn.close()
            return
        except Exception:
            conn.close()
            time.sleep(0.2)


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
        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(subject_id)
        cur.execute(f"UPDATE subjects SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    conn.close()


def delete_subject(subject_id):
    """删除科目。返回 (成功bool, 错误信息)"""
    conn = get_db()
    cur = conn.cursor()
    # 检查分类
    cur.execute("SELECT COUNT(*) FROM categories WHERE subject_id = ?", (subject_id,))
    cat_count = cur.fetchone()[0]
    if cat_count > 0:
        conn.close()
        return False, f'该科目下有 {cat_count} 个分类，请先删除分类'
    # 检查授权
    cur.execute("SELECT COUNT(*) FROM user_licenses WHERE subject_id = ?", (subject_id,))
    lic_count = cur.fetchone()[0]
    if lic_count > 0:
        conn.close()
        return False, f'该科目有 {lic_count} 个活跃授权，请先撤销授权'
    cur.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    conn.commit()
    conn.close()
    return True, None


def get_subject_stats(subject_id):
    """获取科目的统计信息：分类数、题目数、授权用户数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM categories WHERE subject_id = ?", (subject_id,))
    cat_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM questions WHERE category_id IN (SELECT id FROM categories WHERE subject_id = ?)", (subject_id,))
    q_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM user_licenses WHERE subject_id = ?", (subject_id,))
    lic_count = cur.fetchone()[0]
    conn.close()
    return {'categories': cat_count, 'questions': q_count, 'licenses': lic_count}


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
    """删除分类（同时删除子分类），迭代实现防止循环引用"""
    conn = get_db()
    cur = conn.cursor()
    to_delete = {category_id}
    visited = set()
    while to_delete - visited:
        current = to_delete - visited
        visited |= current
        for cid in current:
            cur.execute("SELECT id FROM categories WHERE parent_id = ?", (cid,))
            children = [r['id'] for r in cur.fetchall()]
            to_delete.update(children)
    for cid in visited:
        cur.execute("DELETE FROM categories WHERE id = ?", (cid,))
    conn.commit()
    conn.close()


# 允许的 HTML 标签白名单（用于净化题干/解析/选项）
SANITIZE_ALLOWED_TAGS = frozenset({
    'br', 'p', 'b', 'strong', 'i', 'em', 'u', 'sub', 'sup',
    'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'thead', 'tbody',
    'img', 'span', 'div', 'a', 'font', 'center', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'code', 'pre', 'blockquote', 'hr', 'strike',
})


def sanitize_html(text: str) -> str:
    """净化 HTML：移除 <script>、<iframe>、事件属性等危险内容，保留安全标签。
    
    用于题干、选项、解析等用户输入内容的净化。
    """
    if not text:
        return text
    
    import re
    
    # 1. 移除危险标签及其内容
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<object[^>]*>.*?</object>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<embed[^>]*>.*?</embed>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<form[^>]*>.*?</form>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # 2. 移除所有 HTML 标签的事件属性 (onclick, onerror, onload 等)
    text = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*on\w+\s*=\s*\S+', '', text, flags=re.IGNORECASE)
    
    # 3. 移除 javascript: 和 data: 协议的 href/src
    text = re.sub(r'(href|src)\s*=\s*["\']\s*javascript:[^"\']*["\']', r'\1=""', text, flags=re.IGNORECASE)
    text = re.sub(r'(href|src)\s*=\s*["\']\s*data:[^"\']*["\']', r'\1=""', text, flags=re.IGNORECASE)
    text = re.sub(r'(href|src)\s*=\s*javascript:\S+', r'\1=""', text, flags=re.IGNORECASE)
    
    return text


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


def get_questions_by_subject(subject_id, status=1, page=1, per_page=20, search='', category_id=None):
    """分页获取题目（管理端用）"""
    conn = get_db()
    cur = conn.cursor()
    offset = (page - 1) * per_page

    where = "q.status = ? AND q.subject_id = ?"
    params = [status, subject_id]

    if search:
        escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where += " AND (q.stem LIKE ? ESCAPE '\\' OR q.id LIKE ? ESCAPE '\\')"
        params.extend([f"%{escaped}%", f"%{escaped}%"])

    if category_id:
        where += " AND q.category_id = ?"
        params.append(category_id)

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
    """创建题目（自动净化 stem/options/explanation）"""
    import uuid
    conn = get_db()
    cur = conn.cursor()
    qid = data.get('id') or str(uuid.uuid4())[:8]
    
    # 净化 HTML 字段
    stem = sanitize_html(data.get('stem') or '')
    options_raw = data.get('options', '{}')
    try:
        options_dict = json.loads(options_raw) if options_raw else {}
        options_dict = {k: sanitize_html(v) for k, v in options_dict.items()}
        options = json.dumps(options_dict, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        options = options_raw
    explanation = sanitize_html(data.get('explanation') or '')
    
    cur.execute("""
        INSERT INTO questions (
            id, stem, options, answer, explanation, qtype, difficulty,
            subject_id, category_id, is_real_exam, exam_year, source, status, qtype_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        qid,
        stem,
        options,
        data.get('answer'),
        explanation,
        data.get('qtype', 'single'),
        data.get('difficulty', '无'),
        data.get('subject_id'),
        data.get('category_id'),
        data.get('is_real_exam', 0),
        data.get('exam_year'),
        data.get('source', 'practice'),
        {'single': '单选题', 'multiple': '多选题', 'judge': '判断题'}.get(data.get('qtype', 'single'), '单选题'),
    ))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def update_question(qid, data):
    """更新题目（自动净化 stem/options/explanation）"""
    conn = get_db()
    cur = conn.cursor()
    fields = []
    values = []
    for key in ['stem', 'options', 'answer', 'explanation', 'qtype', 'difficulty',
                'category_id', 'is_real_exam', 'exam_year', 'source', 'qtype_text', 'status']:
        if key in data:
            fields.append(f"{key} = ?")
            # 净化 HTML 字段
            if key == 'stem':
                values.append(sanitize_html(data[key]))
            elif key == 'explanation':
                values.append(sanitize_html(data[key]))
            elif key == 'options':
                try:
                    options_dict = json.loads(data[key]) if data[key] else {}
                    options_dict = {k: sanitize_html(v) for k, v in options_dict.items()}
                    values.append(json.dumps(options_dict, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    values.append(data[key])
            else:
                values.append(data[key])
    # 确保 qtype_text 与 qtype 一致
    if 'qtype' in data:
        qtype_text = {'single': '单选题', 'multiple': '多选题', 'judge': '判断题'}.get(data['qtype'], '单选题')
        if 'qtype_text' not in data or data['qtype_text'] != qtype_text:
            fields.append('qtype_text = ?')
            values.append(qtype_text)
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

def save_answer(user_id, question_id, user_answer, correct, subject_id=1, source='practice'):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO history (user_id, question_id, user_answer, correct, subject_id, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, question_id, user_answer, correct, subject_id, source))
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
FSRS_DECAY = 0.9  # 遗忘衰减系数（保留兼容性）
FSRS_GAIN_FACTOR = 2.5  # 增长系数（gain = qf × 2.5 / sqrt(log2(S+1))）
def get_interval(stability, desired_retention=0.9):
    """由稳定性计算下次复习间隔（天）
    
    stability 直接代表间隔天数，四舍五入到整数。
    <2 天保留1位小数。
    保留 desired_retention 参数兼容旧调用（不再使用）。
    """
    if stability < 1:
        return 0  # <1天进入学习状态，本次重复
    if stability < 2:
        return round(stability, 1)
    return round(stability)


def get_retrievability(stability, delta_t):
    """计算当前保留率 R = e^(-decay × t / S)
    
    stability: 记忆稳定性（天），现直接等于间隔
    delta_t: 距上次复习的天数
    返回值: 0~1 之间的保留率
    注：stability 现在是间隔值，不再是半衰期。
    保留此函数用于展示和排序。
    """
    if stability <= 0 or delta_t < 0:
        return 1.0
    # stability 现在是间隔，R(delta_t) = e^(-decay × delta_t / stability)
    # 当 delta_t = stability 时，R = e^(-0.9) ≈ 40%
    # 这是一个近似展示值
    return math.exp(-FSRS_DECAY * delta_t / max(stability, 0.1))


def update_stability(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """根据评分和复习时机更新记忆稳定性
    
    quality:   0~4（忘了/模糊/一般/简单/秒答）
    stability: 当前稳定性（天），现直接等于间隔
    difficulty: 当前难度（1~10）
    delta_t:   距上次复习的天数（>=0）
    
    核心设计：
    - 答对时：gain = qf × 1.5 / sqrt(log2(S+1)) — 缓慢衰减的持续增长
    - 答错时：S 降到 30%（进入重学，间隔由重试次数决定）
    """
    if delta_t <= 0:
        delta_t = 0.01
    
    # 质量因子：0.2 ~ 1.0
    quality_factor = (quality + 1) / 5.0
    
    if quality >= 2:  # 答对（一般/简单/秒答）
        # 带衰减的增长公式：gain = qf × 1.5 / sqrt(log2(S+1))
        # S 越小增长越快，S 越大增长越慢（自然衰减）
        decay = math.sqrt(math.log2(stability + 1)) if stability > 0 else 1.0
        gain = quality_factor * FSRS_GAIN_FACTOR / decay
        new_stability = stability * (1 + gain)
    else:  # 答错（忘了/模糊）
        # 答错后稳定性降至 30%（进入重学阶段）
        new_stability = stability * 0.3
    
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
    """新题首次答对后初始化记忆状态
    
    quality: 0~4
    返回: (stability, difficulty)
    stability 直接等于间隔天数。
    quality < 2 时进入学习阶段，不使用此函数初始化。
    """
    # quality=2(一般) → 3天, quality=3(简单) → 5天, quality=4(秒答) → 8天
    stability_map = {
        2: 3.0,   # 一般 → 3天后复习
        3: 5.0,   # 简单 → 5天后复习
        4: 8.0,   # 秒答 → 8天后复习
    }
    difficulty_map = {
        2: 5.0,  # 一般 → 中等
        3: 4.0,  # 简单 → 较易
        4: 3.0,  # 秒答 → 容易
    }
    return stability_map.get(quality, 3.0), difficulty_map.get(quality, 5.0)


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
    注意：返回的 interval 已经过 fuzz 处理（±10% 随机偏移）。
    预测展示时请使用 base_interval（不加 fuzz）。
    """
    new_stability = update_stability(quality, stability, difficulty, delta_t, desired_retention)
    new_difficulty = update_difficulty(quality, difficulty)
    base_interval = get_interval(new_stability, desired_retention)
    interval = apply_fuzz(base_interval)
    return new_stability, new_difficulty, interval, base_interval


# 学习步骤配置（分钟）
LEARNING_STEPS = [1, 10]  # 第1步: 1分钟后, 第2步: 10分钟后

# 重学机制：复习答错后保留的稳定性比例
RELEARNING_STABILITY_KEEP = 0.3  # 保留30%稳定性，不全部重置


def get_due_questions(user_id, category_id=None, subject_id=None, limit=20):
    """获取到期需要复习的题目
    
    排序策略：按保留率升序（最易忘优先），同保留率按到期时间
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
    
    # 按保留率重新排序
    if due:
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
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # reviewed: 按 subject_id/category_id 过滤
    if category_id:
        cur.execute("""
            SELECT COUNT(DISTINCT rs.question_id) FROM review_schedule rs
            JOIN questions q ON q.id = rs.question_id
            WHERE rs.user_id = ? AND q.category_id = ?
        """, (user_id, category_id))
    elif subject_id:
        cur.execute("""
            SELECT COUNT(DISTINCT rs.question_id) FROM review_schedule rs
            JOIN questions q ON q.id = rs.question_id
            WHERE rs.user_id = ? AND q.subject_id = ?
        """, (user_id, subject_id))
    else:
        cur.execute("SELECT COUNT(*) as reviewed FROM review_schedule WHERE user_id = ?",
                   (user_id,))
    reviewed = cur.fetchone()[0]
    
    # due: 按 subject_id/category_id 过滤
    if category_id:
        cur.execute("""
            SELECT COUNT(DISTINCT rs.question_id) FROM review_schedule rs
            JOIN questions q ON q.id = rs.question_id
            WHERE rs.user_id = ? AND q.category_id = ? AND rs.next_review <= ?
        """, (user_id, category_id, now))
    elif subject_id:
        cur.execute("""
            SELECT COUNT(DISTINCT rs.question_id) FROM review_schedule rs
            JOIN questions q ON q.id = rs.question_id
            WHERE rs.user_id = ? AND q.subject_id = ? AND rs.next_review <= ?
        """, (user_id, subject_id, now))
    else:
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
        'max_interval': 30, 'learning_steps': LEARNING_STEPS,
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


# ==================== 复习调度 ====================

def _mastered_sql_condition():
    """返回 SQL 中的"已掌握"条件子句"""
    return 'rs.stability >= 45 AND rs.repetitions >= 3'


def _reinforce_sql_condition():
    """返回 SQL 中的"强化"条件子句：答题次数 >= 9"""
    return 'rs.repetitions >= 9'


def is_question_reinforce(user_id, question_id):
    """判断题目是否需要强化（答题次数 >= 9）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT repetitions FROM review_schedule
        WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return row[0] >= 9


def is_question_mastered(user_id, question_id):
    """判断题目是否已掌握
    
    标准: stability >= 45 AND repetitions >= 3
    """
    conn = get_db()
    cur = conn.cursor()
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
    return r['repetitions'] >= 3 and s >= 45


def is_question_in_reinforce(user_id, question_id):
    """判断题目是否处于强化状态（card_state = 'reinforce'）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT card_state FROM review_schedule
        WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return row[0] == 'reinforce'


def exit_reinforce_mode(user_id, question_id):
    """退出强化状态：重置为复习，stability=45, repetitions 至少 3（满足已掌握条件）"""
    from datetime import datetime, timedelta, date
    conn = get_db()
    cur = conn.cursor()
    # 30天后复习（最长间隔），stability 设为 45（满足已掌握条件）
    next_review = datetime.combine(date.today() + timedelta(days=30), datetime.min.time())
    cur.execute("""
        UPDATE review_schedule
        SET card_state = 'review', next_review = ?,
            stability = 45.0, repetitions = MAX(repetitions, 3)
        WHERE user_id = ? AND question_id = ?
    """, (next_review.strftime('%Y-%m-%d %H:%M:%S'), user_id, question_id))
    conn.commit()
    conn.close()


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


def predict_review_result(user_id, question_id, subject_id):
    """预测各评分（0~4）的调度结果，供前端展示
    
    返回: {0: '退回学习（需答对2次）', 1: '1天后', 2: '明天复习', ...}
    逻辑与 update_review_schedule 一致，但不修改数据库。
    """
    from datetime import datetime, timedelta, date
    
    results = {}
    
    limits = get_study_limits(user_id, subject_id)
    dr = limits['desired_retention']
    learning_steps = limits['learning_steps']
    
    schedule = get_review_schedule(user_id, question_id)
    
    for q in range(5):
        if schedule:
            card_state = schedule.get('card_state') or 'review'
            learning_step = schedule.get('learning_step') or 0
            stability = schedule.get('stability') or 1.0
            difficulty = schedule.get('difficulty') or 5.0
            
            if card_state == 'learning':
                # 学习阶段
                if q >= 2:
                    # 答对，计数器减 1
                    remaining = max(0, learning_step - 1)
                    if remaining == 0:
                        # 毕业，进入复习
                        if q == 2:
                            results[q] = '明天复习'
                        elif q == 3:
                            results[q] = '2天后'
                        else:
                            results[q] = '6天后'
                    else:
                        results[q] = f'还需答对{remaining}次'
                else:
                    # 答错，回队尾
                    results[q] = '回队尾，重新练习'
            else:
                # 正式复习阶段
                if q == 0:
                    results[q] = '退回学习（需答对2次）'
                elif q == 1:
                    results[q] = '退回学习（需答对1次）'
                else:
                    # 答对 → FSRS
                    last_review_str = schedule.get('last_review')
                    if last_review_str:
                        last_review = datetime.strptime(last_review_str, '%Y-%m-%d %H:%M:%S')
                        delta_t = max(1, (date.today() - last_review.date()).days)
                    else:
                        delta_t = 1
                    
                    new_s, new_d, new_i, base_i = fsrs_schedule(q, stability, difficulty, delta_t, dr)
                    new_i = min(int(base_i), 30)  # 间隔上限 30 天
                    if new_i == 1:
                        results[q] = '明天复习'
                    else:
                        results[q] = f'{new_i}天后'
        else:
            # 新题，无记录
            if q == 0:
                results[q] = '需答对2次进入复习'
            elif q == 1:
                results[q] = '需答对1次进入复习'
            elif q == 2:
                results[q] = '明天复习'
            elif q == 3:
                results[q] = '2天后'
            else:
                results[q] = '6天后'
    
    return results


def update_review_schedule(user_id, question_id, subject_id, quality):
    """根据评分更新复习计划
    
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
    cur.execute("""
        SELECT ease_factor, interval, repetitions, stability, difficulty, last_review,
               card_state, learning_step, consecutive_easy
        FROM review_schedule
        WHERE user_id = ? AND question_id = ?
    """, (user_id, question_id))
    
    existing_row = cur.fetchone()
    
    if existing_row:
        existing = dict(existing_row)
        card_state = existing.get('card_state') or 'review'
        learning_step = existing.get('learning_step') or 0
        consecutive_easy = existing.get('consecutive_easy') or 0

        if card_state == 'learning':
            # ── 学习阶段（learning_step = 还需答对次数）──
            now = datetime.now()
            new_stability = existing.get('stability') or 1.0
            new_difficulty = existing.get('difficulty') or 5.0
            new_ease = existing['ease_factor']
            new_reps = existing['repetitions'] + 1

            if quality >= 2:  # 答对，计数器减 1
                learning_step = max(0, learning_step - 1)
                if learning_step == 0:
                    # 毕业，进入复习
                    card_state = 'review'
                    new_stability, new_difficulty = init_memory_state(quality)
                    if quality == 2:
                        new_interval = 1
                        next_review = datetime.combine(
                            date.today() + timedelta(days=1), datetime.min.time()
                        )
                    elif quality == 3:
                        new_interval = 2
                        next_review = datetime.combine(
                            date.today() + timedelta(days=2), datetime.min.time()
                        )
                    else:
                        new_interval = 6
                        next_review = datetime.combine(
                            date.today() + timedelta(days=6), datetime.min.time()
                        )
                else:
                    # 仍需学习，回队尾
                    new_interval = 0
                    next_review = now
            else:
                # 答错，计数器不变，回队尾
                new_interval = 0
                next_review = now

            # 连续简单/秒答计数器
            if quality >= 3:
                consecutive_easy += 1
            else:
                consecutive_easy = 0
        else:
            # ── 正式复习阶段 ──
            stability = existing.get('stability') or 1.0
            difficulty = existing.get('difficulty') or 5.0
            reps = existing['repetitions']

            # 强化触发条件：复习超过5次且评分非简单/秒答（quality < 3）
            if quality < 3 and reps > 5:
                # 直接进入强化状态
                new_stability = stability * RELEARNING_STABILITY_KEEP
                _, new_difficulty = init_memory_state(quality)
                new_interval = 30
                new_reps = reps + 1
                new_ease = existing['ease_factor']
                card_state = 'reinforce'
                learning_step = 0
                now = datetime.now()
                next_review = datetime.combine(date.today() + timedelta(days=30), datetime.min.time())
            elif quality in (0, 1):
                # 忘了/模糊 → 退回学习
                new_stability = stability * RELEARNING_STABILITY_KEEP
                _, new_difficulty = init_memory_state(quality)
                new_interval = 0
                new_reps = reps + 1
                new_ease = existing['ease_factor']
                card_state = 'learning'
                learning_step = 2 if quality == 0 else 1
                now = datetime.now()
                next_review = now  # 回队尾
            else:
                # 答对（一般/简单/秒答）→ 正常 FSRS 调度
                last_review_str = existing.get('last_review')
                if last_review_str:
                    last_review = datetime.strptime(last_review_str, '%Y-%m-%d %H:%M:%S')
                    delta_t = max(1, (date.today() - last_review.date()).days)
                else:
                    delta_t = 1

                new_stability, new_difficulty, new_interval, base_interval = fsrs_schedule(
                    quality, stability, difficulty, delta_t, dr
                )
                new_interval = min(new_interval, 30)
                new_reps = reps + 1
                new_ease = existing['ease_factor']

                if new_interval < 1:
                    card_state = 'learning'
                    learning_step = 2
                    new_interval = 0
                    now = datetime.now()
                    next_review = now
                else:
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

            # 连续简单/秒答计数器
            if quality >= 3:
                consecutive_easy += 1
            else:
                consecutive_easy = 0
    else:
        # 新题首次答题：按评分分流
        now = datetime.now()
        new_reps = 1
        new_ease = 2.5

        if quality == 0:  # 忘了 → 学习，需答对2次
            new_stability, new_difficulty = init_memory_state(quality)
            card_state = 'learning'
            learning_step = 2
            new_interval = 0
            next_review = now  # 回队尾
        elif quality == 1:  # 模糊 → 学习，需答对1次
            new_stability, new_difficulty = init_memory_state(quality)
            card_state = 'learning'
            learning_step = 1
            new_interval = 0
            next_review = now  # 回队尾
        elif quality == 2:  # 一般 → 复习，1天后
            new_stability, new_difficulty = init_memory_state(quality)
            card_state = 'review'
            learning_step = 0
            new_interval = 1
            next_review = datetime.combine(
                date.today() + timedelta(days=1), datetime.min.time()
            )
        elif quality == 3:  # 简单 → 复习，2天后
            new_stability, new_difficulty = init_memory_state(quality)
            card_state = 'review'
            learning_step = 0
            new_interval = 2
            next_review = datetime.combine(
                date.today() + timedelta(days=2), datetime.min.time()
            )
        else:  # quality == 4 秒答 → 复习，6天后
            new_stability, new_difficulty = init_memory_state(quality)
            card_state = 'review'
            learning_step = 0
            new_interval = 6
            next_review = datetime.combine(
                date.today() + timedelta(days=6), datetime.min.time()
            )

        # 连续简单/秒答计数器
        consecutive_easy = 1 if quality >= 3 else 0

    # 连续 3 次简单/秒答 → 强制已掌握（任何状态都覆盖）
    if consecutive_easy >= 3:
        card_state = 'review'
        learning_step = 0
        new_stability = 45.0
        new_reps = max(new_reps, 3)
        new_interval = 30
        next_review = datetime.combine(date.today() + timedelta(days=30), datetime.min.time())

    if existing_row:
        cur.execute("""
            UPDATE review_schedule
            SET ease_factor = ?, interval = ?, repetitions = ?,
                next_review = ?, last_review = ?, last_quality = ?,
                stability = ?, difficulty = ?,
                card_state = ?, learning_step = ?, consecutive_easy = ?
            WHERE user_id = ? AND question_id = ?
        """, (new_ease, new_interval, new_reps,
              next_review.strftime('%Y-%m-%d %H:%M:%S'),
              now.strftime('%Y-%m-%d %H:%M:%S'),
              quality, round(new_stability, 2), round(new_difficulty, 2),
              card_state, learning_step, consecutive_easy,
              user_id, question_id))
    else:
        cur.execute("""
            INSERT INTO review_schedule (user_id, question_id, subject_id, ease_factor, interval, repetitions, consecutive_easy, next_review, last_review, last_quality, stability, difficulty, card_state, learning_step)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, question_id, subject_id,
              new_ease, new_interval, new_reps, consecutive_easy,
              next_review.strftime('%Y-%m-%d %H:%M:%S'),
              now.strftime('%Y-%m-%d %H:%M:%S'),
              quality, round(new_stability, 2), round(new_difficulty, 2),
              card_state, learning_step))
    
    conn.commit()
    conn.close()
    
    return {
        'ease_factor': new_ease,
        'interval': new_interval,
        'repetitions': new_reps,
        'stability': round(new_stability, 2),
        'difficulty': round(new_difficulty, 2),
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


def reset_question_schedule(user_id, question_id):
    """重置题目为新题：删除复习计划 + 答题历史"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'DELETE FROM review_schedule WHERE user_id = ? AND question_id = ?',
        (user_id, question_id)
    )
    cur.execute(
        'DELETE FROM history WHERE user_id = ? AND question_id = ?',
        (user_id, question_id)
    )
    conn.commit()
    conn.close()


def skip_review_interval(user_id, question_id):
    """跳过复习间隔：将题目设为立即复习状态，保留 FSRS 参数和历史记录
    
    将 next_review 设为当前时间，使题目立即变为"待复习"。
    """
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE review_schedule
        SET next_review = ?
        WHERE user_id = ? AND question_id = ?
    """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id, question_id))
    conn.commit()
    conn.close()


# ==================== 授权管理 ====================

def grant_user_license(user_id, subject_id, days=365):
    """授予用户科目授权，有效期 N 天"""
    from datetime import datetime, timedelta
    conn = get_db()
    cur = conn.cursor()
    expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        INSERT INTO user_licenses (user_id, subject_id, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, subject_id) DO UPDATE SET expires_at = ?
    """, (user_id, subject_id, expires_at, expires_at))
    conn.commit()
    conn.close()


def revoke_user_license(user_id, subject_id):
    """吊销用户科目授权"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_licenses WHERE user_id = ? AND subject_id = ?", (user_id, subject_id))
    conn.commit()
    conn.close()


def check_user_license(user_id, subject_id):
    """检查用户是否有有效授权
    
    返回: {has_license: bool, expires_at: str|None, days_left: int|None, is_expired: bool}
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT expires_at FROM user_licenses
        WHERE user_id = ? AND subject_id = ?
    """, (user_id, subject_id))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return {'has_license': False, 'expires_at': None, 'days_left': None, 'is_expired': True}
    
    from datetime import datetime
    expires_at = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
    days_left = (expires_at - datetime.now()).days
    is_expired = days_left <= 0
    
    return {
        'has_license': True,
        'expires_at': row[0],
        'days_left': days_left,
        'is_expired': is_expired
    }


def get_user_licenses(user_id):
    """获取用户所有授权"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ul.id, ul.subject_id, s.name, ul.expires_at
        FROM user_licenses ul
        JOIN subjects s ON s.id = ul.subject_id
        WHERE ul.user_id = ?
        ORDER BY ul.expires_at
    """, (user_id,))
    licenses = []
    for row in cur.fetchall():
        from datetime import datetime
        expires_at = datetime.strptime(row[3], '%Y-%m-%d %H:%M:%S')
        days_left = (expires_at - datetime.now()).days
        licenses.append({
            'id': row[0],
            'subject_id': row[1],
            'subject_name': row[2],
            'expires_at': row[3],
            'days_left': days_left,
            'is_expired': days_left <= 0
        })
    conn.close()
    return licenses


def get_all_licenses():
    """获取所有用户授权（管理后台用）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ul.id, u.id as user_id, u.username, ul.subject_id, s.name as subject_name,
               ul.expires_at, ul.created_at
        FROM user_licenses ul
        JOIN users u ON u.id = ul.user_id
        JOIN subjects s ON s.id = ul.subject_id
        ORDER BY ul.expires_at
    """)
    licenses = []
    for row in cur.fetchall():
        from datetime import datetime
        expires_at = datetime.strptime(row[5], '%Y-%m-%d %H:%M:%S')
        days_left = (expires_at - datetime.now()).days
        licenses.append({
            'id': row[0],
            'user_id': row[1],
            'username': row[2],
            'subject_id': row[3],
            'subject_name': row[4],
            'expires_at': row[5],
            'created_at': row[6],
            'days_left': days_left,
            'is_expired': days_left <= 0
        })
    conn.close()
    return licenses

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
    
    排序策略：按保留率升序（最易忘优先）
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
              AND rs.card_state != 'reinforce'
              AND NOT (rs.stability >= 45 AND rs.repetitions >= 3)
        ORDER BY rs.next_review ASC
    """, (user_id, category_id, now_str))
    due = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    # 按保留率重新排序
    if due:
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
    from datetime import datetime as dt
    now = dt.now()
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
        LEFT JOIN history h ON h.question_id = q.id AND h.user_id = ? AND (h.source IS NULL OR h.source = 'practice')
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
            next_rev = dt.strptime(row['next_review'], '%Y-%m-%d %H:%M:%S')
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
    """根据历史记录推断上次评分倾向
    
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
    
    # 旧记录（last_quality 为 NULL）：不再猜测，返回未知
    return '未知'


# ==================== 统计模块 ====================

def get_stats_summary(user_id, subject_id):
    """获取学习统计概览，仅统计练习/复习，排除考试"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    seven_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # 总复习数（来自 history，排除考试）
    cur.execute("SELECT COUNT(*) FROM history WHERE user_id = ? AND subject_id = ? AND (source IS NULL OR source = 'practice')", (user_id, subject_id))
    total_reviewed = cur.fetchone()[0]
    
    # 今日复习数
    cur.execute("""
        SELECT COUNT(*) FROM history WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) = ? AND (source IS NULL OR source = 'practice')
    """, (user_id, subject_id, today_str))
    today_reviewed = cur.fetchone()[0]
    
    # 待复习数
    cur.execute("SELECT COUNT(*) FROM review_schedule WHERE user_id = ? AND next_review <= ?",
               (user_id, now.strftime('%Y-%m-%d %H:%M:%S')))
    due_now = cur.fetchone()[0]
    
    # 近7天正确率
    cur.execute("""
        SELECT AVG(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) * 100
        FROM history WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ? AND (source IS NULL OR source = 'practice')
    """, (user_id, subject_id, seven_ago))
    acc_7d = cur.fetchone()[0]
    accuracy_7d = round(acc_7d or 0, 1)
    
    # 连续学习天数
    cur.execute("""
        SELECT DISTINCT DATE(timestamp) as study_date
        FROM history WHERE user_id = ? AND subject_id = ? AND (source IS NULL OR source = 'practice')
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
    """获取每日复习趋势（含新题数），仅统计练习/复习，排除考试"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    # 每日总答题 + 新题数（该用户该科目第一次做此题，仅限练习/复习）
    cur.execute("""
        SELECT DATE(h.timestamp) as date,
               COUNT(*) as reviewed,
               ROUND(AVG(CASE WHEN h.correct = 1 THEN 100.0 ELSE 0.0 END), 1) as accuracy,
               SUM(CASE WHEN h.id = (
                   SELECT MIN(h2.id) FROM history h2
                   WHERE h2.user_id = ? AND h2.subject_id = ? AND h2.question_id = h.question_id
                     AND (h2.source IS NULL OR h2.source = 'practice')
               ) THEN 1 ELSE 0 END) as new_questions
        FROM history h
        WHERE h.user_id = ? AND h.subject_id = ? AND DATE(h.timestamp) >= ?
          AND (h.source IS NULL OR h.source = 'practice')
        GROUP BY DATE(h.timestamp) ORDER BY date
    """, (user_id, subject_id, user_id, subject_id, since))
    
    result = [{
        'date': r[0],
        'reviewed': r[1],
        'accuracy': float(r[2]),
        'new_questions': r[3] or 0,
    } for r in cur.fetchall()]
    conn.close()
    return result


def get_exam_daily_trend(user_id, subject_id, days=30):
    """获取每日考试趋势，仅统计考试数据"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cur.execute("""
        SELECT DATE(h.timestamp) as date,
               COUNT(*) as attempted,
               ROUND(AVG(CASE WHEN h.correct = 1 THEN 100.0 ELSE 0.0 END), 1) as accuracy
        FROM history h
        WHERE h.user_id = ? AND h.subject_id = ? AND DATE(h.timestamp) >= ?
          AND h.source = 'exam'
        GROUP BY DATE(h.timestamp) ORDER BY date
    """, (user_id, subject_id, since))
    
    result = [{
        'date': r[0],
        'attempted': r[1],
        'accuracy': float(r[2]),
    } for r in cur.fetchall()]
    conn.close()
    return result


def get_heatmap_data(user_id, subject_id, days=90):
    """获取热力图数据（近 N 天），仅统计练习/复习"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cur.execute("""
        SELECT DATE(timestamp) as date, COUNT(*) as count
        FROM history WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ?
          AND (source IS NULL OR source = 'practice')
        GROUP BY DATE(timestamp) ORDER BY date
    """, (user_id, subject_id, since))
    
    result = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return result


def get_year_heatmap(user_id, subject_id, year):
    """获取指定年份热力图数据（全年 365/366 天）"""
    conn = get_db()
    cur = conn.cursor()
    since = f'{year}-01-01'
    until = f'{year}-12-31'
    
    cur.execute("""
        SELECT DATE(timestamp) as date, COUNT(*) as count
        FROM history WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) >= ? AND DATE(timestamp) <= ?
        GROUP BY DATE(timestamp) ORDER BY date
    """, (user_id, subject_id, since, until))
    
    result = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return result


def get_calendar_stats(user_id, subject_id, year):
    """获取日历统计摘要：连续打卡、总打卡天数、总答题数、月度出勤率"""
    from datetime import date
    conn = get_db()
    cur = conn.cursor()
    
    # 该科目所有学习日期
    since = f'{year}-01-01'
    until = f'{year}-12-31'
    cur.execute("""
        SELECT DISTINCT DATE(timestamp) as study_date
        FROM history WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) >= ? AND DATE(timestamp) <= ?
        ORDER BY study_date DESC
    """, (user_id, subject_id, since, until))
    all_dates = [row[0] for row in cur.fetchall()]
    
    # 总打卡天数
    total_checkin = len(all_dates)
    
    # 连续打卡天数（从今天往前数）
    streak = 0
    expected = date.today()
    for d in all_dates:
        if d == expected.strftime('%Y-%m-%d'):
            streak += 1
            expected = expected - timedelta(days=1)
        elif streak > 0:
            break
    
    # 总答题数
    cur.execute("""
        SELECT COUNT(*) FROM history WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) >= ? AND DATE(timestamp) <= ?
    """, (user_id, subject_id, since, until))
    total_questions = cur.fetchone()[0]
    
    # 月度出勤率
    month_attendance = {}
    for m in range(1, 13):
        m_start = date(year, m, 1)
        if m == 12:
            m_end = date(year, 12, 31)
        else:
            m_end = date(year, m + 1, 1) - timedelta(days=1)
        # 该月实际过去的天数
        today = date.today()
        if today < m_start:
            total_days = 0
            active_days = 0
        elif today < m_end:
            total_days = (today - m_start).days + 1
            active_days = sum(1 for d in all_dates if m_start.strftime('%Y-%m-%d') <= d <= today.strftime('%Y-%m-%d'))
        else:
            total_days = (m_end - m_start).days + 1
            active_days = sum(1 for d in all_dates if m_start.strftime('%Y-%m-%d') <= d <= m_end.strftime('%Y-%m-%d'))
        month_attendance[m] = {
            'total': total_days,
            'active': active_days,
            'rate': round(active_days / total_days * 100, 0) if total_days > 0 else 0
        }
    
    conn.close()
    return {
        'streak_days': streak,
        'total_checkin': total_checkin,
        'total_questions': total_questions,
        'month_attendance': month_attendance,
    }


def get_daily_learning_time(user_id, subject_id, days=30):
    """获取每日学习时长（按会话计算，间隔>30分钟视为新会话）"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    cur.execute("""
        SELECT DATE(timestamp) as date, timestamp
        FROM history
        WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ? AND (source IS NULL OR source = 'practice')
        ORDER BY DATE(timestamp), timestamp
    """, (user_id, subject_id, since))

    # 按日期分组
    daily = {}  # date -> [datetime1, datetime2, ...]
    for row in cur.fetchall():
        d = row[0]
        if d not in daily:
            daily[d] = []
        daily[d].append(datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S'))
    conn.close()

    SESSION_GAP = timedelta(minutes=30)
    TAIL_MINUTES = 3

    result = {}
    for d in sorted(daily.keys()):
        times = sorted(daily[d])
        total_minutes = 0
        session_start = times[0]

        for i in range(1, len(times)):
            if times[i] - times[i - 1] > SESSION_GAP:
                # 结束上一个会话
                span = (times[i - 1] - session_start).total_seconds() / 60 + TAIL_MINUTES
                total_minutes += max(span, 1)
                session_start = times[i]

        # 最后一个会话
        span = (times[-1] - session_start).total_seconds() / 60 + TAIL_MINUTES
        total_minutes += max(span, 1)
        result[d] = round(total_minutes, 1)

    return result


def get_hourly_distribution(user_id, subject_id, days=30):
    """获取 24 小时答题分布"""
    conn = get_db()
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    cur.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour, COUNT(*) as cnt
        FROM history
        WHERE user_id = ? AND subject_id = ? AND DATE(timestamp) >= ? AND (source IS NULL OR source = 'practice')
        GROUP BY hour ORDER BY hour
    """, (user_id, subject_id, since))

    result = {h: c for h, c in cur.fetchall()}
    conn.close()
    return result


def get_category_mastery(user_id, subject_id):
    """获取分类掌握度，包含所有分类（含未学的），排除考试数据。
    使用子查询避免 history 多行与 review_schedule 的交叉乘积。"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    mastered_cond = _mastered_sql_condition()
    
    cur.execute(f"""
        SELECT c.id as category_id, c.name as category_name,
               (SELECT COUNT(*) FROM questions q WHERE q.category_id = c.id AND q.status = 1 AND q.subject_id = ?) as total,
               (SELECT COUNT(DISTINCT h.question_id) FROM history h JOIN questions q ON q.id = h.question_id WHERE h.question_id IN (SELECT id FROM questions WHERE category_id = c.id AND status = 1 AND subject_id = ?) AND h.user_id = ? AND (h.source IS NULL OR h.source = 'practice')) as reviewed,
               (SELECT ROUND(AVG(CASE WHEN h.correct = 1 THEN 100.0 ELSE 0.0 END), 1) FROM history h JOIN questions q ON q.id = h.question_id WHERE h.question_id IN (SELECT id FROM questions WHERE category_id = c.id AND status = 1 AND subject_id = ?) AND h.user_id = ? AND (h.source IS NULL OR h.source = 'practice')) as accuracy,
               (SELECT COUNT(*) FROM review_schedule rs JOIN questions q ON q.id = rs.question_id WHERE rs.question_id IN (SELECT id FROM questions WHERE category_id = c.id AND status = 1 AND subject_id = ?) AND rs.user_id = ? AND {mastered_cond}) as mastered,
               (SELECT COUNT(*) FROM review_schedule rs JOIN questions q ON q.id = rs.question_id WHERE rs.question_id IN (SELECT id FROM questions WHERE category_id = c.id AND status = 1 AND subject_id = ?) AND rs.user_id = ? AND rs.next_review <= ? AND rs.card_state != 'reinforce') as due
        FROM categories c
        WHERE c.subject_id = ? AND c.name IS NOT NULL AND c.level = 2
        ORDER BY c.id
    """, (subject_id, subject_id, user_id, subject_id, user_id, subject_id, user_id, subject_id, user_id, now, subject_id))
    
    result = []
    for r in cur.fetchall():
        total = r[2] or 0
        reviewed = r[3] or 0
        accuracy = r[4]
        mastered = r[5] or 0
        due = r[6] or 0
        if total == 0:
            continue
        result.append({
            'name': r[1],
            'total': total,
            'reviewed': reviewed,
            'unstudied': total - reviewed,
            'accuracy': float(accuracy) if accuracy is not None else None,
            'mastered': mastered,
            'due': due,
            'mastered_rate': round(mastered / total * 100, 1) if total > 0 else 0,
        })
    conn.close()
    return result


def get_retention_curve(user_id, subject_id):
    """获取保留率曲线（遗忘曲线）"""
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
        JOIN history h ON h.question_id = rs.question_id AND h.user_id = rs.user_id AND (h.source IS NULL OR h.source = 'practice')
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
    """获取已掌握的题目（标准: stability >= 45 AND repetitions >= 3）"""
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

    stats = {lid: {"total": 0, "studied": 0, "unstudied": 0, "due": 0, "mastered": 0} for lid in leaf_ids}

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

    # 各分类未学习题数 = 总题量 - 已学
    for lid in leaf_ids:
        stats[lid]["unstudied"] = max(0, stats[lid]["total"] - stats[lid]["studied"])

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
            s = stats.get(node["id"], {"total": 0, "studied": 0, "unstudied": 0, "due": 0, "mastered": 0})
            node["_stats"] = s
            return s
        total = {"total": 0, "studied": 0, "unstudied": 0, "due": 0, "mastered": 0}
        for child in children:
            child_stats = aggregate(child)
            for k in total:
                total[k] += child_stats[k]
        node["_stats"] = total
        return total

    subject_total = {"total": 0, "studied": 0, "unstudied": 0, "due": 0, "mastered": 0}
    for root in tree:
        root_stats = aggregate(root)
        for k in subject_total:
            subject_total[k] += root_stats[k]

    return {"tree": tree, "subject_total": subject_total}


# ==================== 导入确认库 (Staging) ====================

def get_staging_subject_counts():
    """按科目统计 staging 中的待确认题目数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.name, COUNT(st.id) as count
        FROM subjects s
        LEFT JOIN import_staging st ON st.subject_id = s.id
        WHERE s.status = 1
        GROUP BY s.id
        HAVING count > 0
        ORDER BY s.id
    """)
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def get_staging_by_subject(subject_id, page=1, page_size=20, search=""):
    """获取某科目 staging 中的题目（分页）"""
    conn = get_db()
    cur = conn.cursor()
    
    where = "subject_id = ?"
    params = [subject_id]
    if search:
        where += " AND (stem LIKE ? OR question_id LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    
    cur.execute(f"SELECT COUNT(*) FROM import_staging WHERE {where}", params)
    total = cur.fetchone()[0]
    
    cur.execute(f"""
        SELECT * FROM import_staging 
        WHERE {where}
        ORDER BY id ASC
        LIMIT ? OFFSET ?
    """, params + [page_size, (page - 1) * page_size])
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results, total


def create_staging_record(data):
    """写入 staging 单条记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO import_staging 
        (question_id, subject_id, category_id, category_name, stem, 
         option_a, option_b, option_c, option_d, option_e, option_f,
         correct_answer, explanation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('question_id'),
        data['subject_id'],
        data.get('category_id'),
        data.get('category_name'),
        data['stem'],
        data.get('option_a'),
        data.get('option_b'),
        data.get('option_c'),
        data.get('option_d'),
        data.get('option_e'),
        data.get('option_f'),
        data.get('correct_answer'),
        data.get('explanation'),
    ))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def update_staging_record(staging_id, data):
    """更新 staging 单条记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE import_staging SET
        question_id = ?, subject_id = ?, category_id = ?, category_name = ?,
        stem = ?, option_a = ?, option_b = ?, option_c = ?, option_d = ?,
        option_e = ?, option_f = ?, correct_answer = ?, explanation = ?
        WHERE id = ?
    """, (
        data.get('question_id'),
        data['subject_id'],
        data.get('category_id'),
        data.get('category_name'),
        data['stem'],
        data.get('option_a'),
        data.get('option_b'),
        data.get('option_c'),
        data.get('option_d'),
        data.get('option_e'),
        data.get('option_f'),
        data.get('correct_answer'),
        data.get('explanation'),
        staging_id,
    ))
    conn.commit()
    conn.close()


def delete_staging_record(staging_id):
    """删除 staging 单条记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM import_staging WHERE id = ?", (staging_id,))
    conn.commit()
    conn.close()


def clear_staging_by_subject(subject_id):
    """清空某科目的所有 staging 记录（加重试机制）"""
    import time
    for attempt in range(3):
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM import_staging WHERE subject_id = ?", (subject_id,))
            conn.commit()
            count = cur.rowcount
            conn.close()
            return count
        except Exception:
            conn.close()
            time.sleep(0.3)


def get_staging_record(staging_id):
    """获取单条 staging 记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM import_staging WHERE id = ?", (staging_id,))
    row = cur.fetchone()
    result = dict(row) if row else None
    conn.close()
    return result


# ==================== 邀请码相关 ====================

import random

def generate_invitation_code():
    """生成邀请码：KEYIN-XXXX-XXXX 格式"""
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"KEYIN-{part1}-{part2}"


def create_invitation_code(code, subject_id, days=365, max_uses=1, expires_at=None, created_by=None):
    """创建邀请码"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO invitation_codes (code, subject_id, days, max_uses, expires_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (code, subject_id, days, max_uses, expires_at, created_by)
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        return None
    finally:
        conn.close()


def get_invitation_code(code):
    """根据邀请码获取记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invitation_codes WHERE code = ?", (code,))
    row = cur.fetchone()
    result = dict(row) if row else None
    conn.close()
    return result


def validate_invitation_code(code):
    """
    验证邀请码是否可用。
    返回: (valid, message, code_record)
    """
    record = get_invitation_code(code)
    if not record:
        return False, '邀请码不存在', None
    
    from datetime import datetime
    if record['expires_at']:
        expires = datetime.strptime(record['expires_at'], '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expires:
            return False, '邀请码已过期', None
    
    if record['max_uses'] and record['used_count'] >= record['max_uses']:
        return False, '邀请码已达到使用上限', None
    
    return True, '', record


def use_invitation_code(code_id, user_id):
    """标记邀请码已使用，记录日志"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE invitation_codes SET used_count = used_count + 1 WHERE id = ?", (code_id,))
        cur.execute(
            "INSERT INTO invitation_code_logs (code_id, user_id) VALUES (?, ?)",
            (code_id, user_id)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def list_invitation_codes(page=1, per_page=20, subject_id=None, status='all'):
    """
    分页列出邀请码。
    status: 'all', 'active', 'used_up', 'expired', 'disabled'
    """
    from datetime import datetime
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    where = []
    params = []
    
    if subject_id:
        where.append('ic.subject_id = ?')
        params.append(subject_id)
    
    if status == 'active':
        where.append('(ic.max_uses IS NULL OR ic.used_count < ic.max_uses)')
        where.append('(ic.expires_at IS NULL OR ic.expires_at > ?)')
        params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    elif status == 'used_up':
        where.append('ic.max_uses IS NOT NULL AND ic.used_count >= ic.max_uses')
    elif status == 'expired':
        where.append('ic.expires_at IS NOT NULL AND ic.expires_at <= ?')
        params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    where_sql = ' AND '.join(where) if where else '1=1'
    
    # 总数
    cur.execute(f"SELECT COUNT(*) FROM invitation_codes ic WHERE {where_sql}", params)
    total = cur.fetchone()[0]
    
    # 分页
    offset = (page - 1) * per_page
    cur.execute(
        f"""SELECT ic.*, s.name as subject_name, u.username as creator_name
            FROM invitation_codes ic
            LEFT JOIN subjects s ON s.id = ic.subject_id
            LEFT JOIN users u ON u.id = ic.created_by
            WHERE {where_sql}
            ORDER BY ic.created_at DESC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    )
    rows = [dict(r) for r in cur.fetchall()]
    
    conn.close()
    return rows, total


def disable_invitation_code(code_id):
    """禁用邀请码（将 max_uses 设为 0）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE invitation_codes SET max_uses = 0 WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    return True


def delete_invitation_code(code_id):
    """删除邀请码及其使用记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM invitation_code_logs WHERE code_id = ?", (code_id,))
    cur.execute("DELETE FROM invitation_codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    return True


def get_code_usage_logs(code_id):
    """获取邀请码的使用记录"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """SELECT l.*, u.username
            FROM invitation_code_logs l
            JOIN users u ON u.id = l.user_id
            WHERE l.code_id = ?
            ORDER BY l.used_at DESC""",
        (code_id,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# 预定义安全问题列表
SECURITY_QUESTIONS = {
    '1': '你小学的名字？',
    '2': '你最好的朋友叫什么？',
    '3': '你最喜欢的颜色是什么？',
    '4': '你出生的城市是哪里？',
    '5': '你的第一只宠物叫什么？',
}


def get_security_question_text(question_index):
    """根据问题索引号获取问题文本"""
    return SECURITY_QUESTIONS.get(str(question_index))


# ==================== 安全问题与密码找回 ====================


def set_user_security(user_id, question_index, answer):
    """设置用户安全问题（存索引号）和安全答案（pbkdf2 哈希）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET security_question = ?, security_answer = ? WHERE id = ?",
        (str(question_index), hash_password(answer), user_id)
    )
    conn.commit()
    conn.close()


def check_security_answer(user_id, question_index, answer):
    """验证安全问题答案（支持旧明文兼容，验证后自动升级）"""
    from werkzeug.security import check_password_hash
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT security_question, security_answer FROM users WHERE id = ?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    
    # 问题索引不匹配
    if str(row[0]) != str(question_index):
        return False
    
    stored = row[1]
    # 新版：pbkdf2 哈希
    if stored.startswith('pbkdf2:'):
        return check_password_hash(stored, answer)
    # 兼容旧版：明文比对，验证后自动升级
    if stored == answer:
        # 自动升级
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET security_answer = ? WHERE id = ?",
            (hash_password(answer), user_id)
        )
        conn.commit()
        conn.close()
        return True
    return False


def create_password_reset_token(user_id, hours=1):
    """创建密码重置 token"""
    import secrets
    from datetime import datetime, timedelta
    
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
        (user_id, token, expires)
    )
    conn.commit()
    conn.close()
    return token


def verify_and_consume_reset_token(token):
    """验证密码重置 token 并返回用户 ID，成功则销毁 token"""
    from datetime import datetime
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, expires_at FROM password_reset_tokens WHERE token = ?",
        (token,)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    
    user_id, expires_str = row
    expires = datetime.strptime(expires_str, '%Y-%m-%d %H:%M:%S')
    if datetime.now() > expires:
        cur.execute("DELETE FROM password_reset_tokens WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        return None
    
    # 销毁 token
    cur.execute("DELETE FROM password_reset_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return user_id


def get_user_by_username(username):
    """根据用户名获取用户"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    result = dict(row) if row else None
    conn.close()
    return result


def reset_user_password(user_id, new_password):
    """重置用户密码（管理员操作）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), user_id)
    )
    conn.commit()
    conn.close()
    return True


# ==================== 站点设置 ====================

def get_site_setting(key, default=None):
    """获取站点设置值"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM site_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def get_all_site_settings():
    """获取所有站点设置"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM site_settings")
    result = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return result


def update_site_setting(key, value):
    """更新站点设置值"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    return True


def batch_update_site_settings(settings):
    """批量更新站点设置，settings 为 dict {key: value}"""
    conn = get_db()
    cur = conn.cursor()
    for key, value in settings.items():
        cur.execute("INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    return True


# ==================== 题目纠错、笔记、留言板 ====================

def create_question_feedback(question_id, subject_id, user_id, content, image_path=None):
    """创建题目纠错反馈"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO question_feedbacks (question_id, subject_id, user_id, content, image_path) VALUES (?, ?, ?, ?, ?)",
        (question_id, subject_id, user_id, content[:500], image_path)
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_feedbacks(subject_id=None, status='all', page=1, per_page=20):
    """列出纠错反馈（管理端）"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    where = []
    params = []
    if subject_id:
        where.append('f.subject_id = ?')
        params.append(subject_id)
    if status != 'all':
        where.append('f.status = ?')
        params.append(status)
    
    where_sql = ' AND '.join(where) if where else '1=1'
    
    cur.execute(f"SELECT COUNT(*) FROM question_feedbacks f WHERE {where_sql}", params)
    total = cur.fetchone()[0]
    
    offset = (page - 1) * per_page
    cur.execute(
        f"""SELECT f.*, u.username, s.name as subject_name
            FROM question_feedbacks f
            LEFT JOIN users u ON u.id = f.user_id
            LEFT JOIN subjects s ON s.id = f.subject_id
            WHERE {where_sql}
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total


def resolve_feedback(feedback_id, admin_id):
    """标记纠错反馈为已处理"""
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE question_feedbacks SET status = 'resolved', resolved_at = ?, resolved_by = ? WHERE id = ?",
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), admin_id, feedback_id)
    )
    conn.commit()
    conn.close()
    return True


def dismiss_feedback(feedback_id):
    """标记纠错反馈为已忽略"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE question_feedbacks SET status = 'dismissed' WHERE id = ?", (feedback_id,))
    conn.commit()
    conn.close()
    return True


def delete_feedback(feedback_id):
    """删除纠错反馈"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM question_feedbacks WHERE id = ?", (feedback_id,))
    conn.commit()
    conn.close()
    return True


def get_user_note(question_id, user_id):
    """获取用户对某题的笔记"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM question_notes WHERE question_id = ? AND user_id = ?", (question_id, user_id))
    row = cur.fetchone()
    result = dict(row) if row else None
    conn.close()
    return result


def save_user_note(question_id, subject_id, user_id, content, image_path=None):
    """保存/更新用户笔记（每人每道题只有一条）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM question_notes WHERE question_id = ? AND user_id = ?",
        (question_id, user_id)
    )
    existing = cur.fetchone()
    
    if existing:
        cur.execute(
            "UPDATE question_notes SET content = ?, image_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content[:500], image_path, existing[0])
        )
    else:
        cur.execute(
            "INSERT INTO question_notes (question_id, subject_id, user_id, content, image_path) VALUES (?, ?, ?, ?, ?)",
            (question_id, subject_id, user_id, content[:500], image_path)
        )
    conn.commit()
    conn.close()
    return True


def get_question_comments(question_id, page=1, per_page=20):
    """获取某题的留言板（按时间倒序）"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM question_comments WHERE question_id = ? AND status = 'active'", (question_id,))
    total = cur.fetchone()[0]
    
    offset = (page - 1) * per_page
    cur.execute(
        """SELECT * FROM question_comments 
           WHERE question_id = ? AND status = 'active' 
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (question_id, per_page, offset)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total


def create_comment(question_id, subject_id, user_id, username, content, image_path=None):
    """创建留言"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO question_comments (question_id, subject_id, user_id, username, content, image_path) VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, subject_id, user_id, username, content[:500], image_path)
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def delete_comment(comment_id):
    """删除留言（软删除）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE question_comments SET status = 'deleted' WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return True


def list_comments(subject_id=None, page=1, per_page=20):
    """列出所有留言（管理端）"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    where = "c.status = 'active'"
    params = []
    if subject_id:
        where += ' AND c.subject_id = ?'
        params.append(subject_id)
    
    cur.execute(f"SELECT COUNT(*) FROM question_comments c WHERE {where}", params)
    total = cur.fetchone()[0]
    
    offset = (page - 1) * per_page
    cur.execute(
        f"""SELECT c.*, s.name as subject_name
            FROM question_comments c
            LEFT JOIN subjects s ON s.id = c.subject_id
            WHERE {where}
            ORDER BY c.created_at DESC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total


def admin_delete_comment(comment_id):
    """管理员硬删除留言"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM question_comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return True


def get_feedback_stats():
    """获取纠错反馈统计"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM question_feedbacks GROUP BY status")
    stats = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return stats


def get_comment_stats():
    """获取留言统计"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM question_comments GROUP BY status")
    stats = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return stats


# ==================== 练习进度持久化 ====================

def save_practice_session(user_id, category_id, subject_id, queue, answered, retry_count, stubborn, total_attempts, answered_correct_first, answered_wrong, initial_count, current_qid=None):
    """保存练习进度到数据库"""
    import json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO practice_sessions
        (user_id, category_id, subject_id, queue, answered, retry_count, stubborn,
         total_attempts, answered_correct_first, answered_wrong, initial_count, current_qid, saved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        user_id, category_id, subject_id,
        json.dumps(queue), json.dumps(answered), json.dumps(retry_count), json.dumps(stubborn),
        total_attempts, answered_correct_first, answered_wrong, initial_count, current_qid
    ))
    conn.commit()
    conn.close()
    return True


def load_practice_session(user_id, category_id):
    """加载最近保存的练习进度"""
    import json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM practice_sessions
        WHERE user_id = ? AND category_id = ?
        ORDER BY saved_at DESC LIMIT 1
    """, (user_id, category_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        'category_id': row['category_id'],
        'subject_id': row['subject_id'],
        'queue': json.loads(row['queue']),
        'answered': json.loads(row['answered']),
        'retry_count': json.loads(row['retry_count']),
        'stubborn': json.loads(row['stubborn']),
        'total_attempts': row['total_attempts'],
        'answered_correct_first': row['answered_correct_first'],
        'answered_wrong': row['answered_wrong'],
        'initial_count': row['initial_count'],
        'current_qid': row['current_qid'],
        'saved_at': row['saved_at'],
    }


def clear_practice_session(user_id, category_id):
    """清除保存的练习进度"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM practice_sessions WHERE user_id = ? AND category_id = ?", (user_id, category_id))
    conn.commit()
    conn.close()
    return True


def init_practice_sessions_table():
    """初始化 practice_sessions 表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS practice_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            queue TEXT NOT NULL DEFAULT '[]',
            answered TEXT NOT NULL DEFAULT '{}',
            retry_count TEXT NOT NULL DEFAULT '{}',
            stubborn TEXT NOT NULL DEFAULT '[]',
            total_attempts INTEGER DEFAULT 0,
            answered_correct_first INTEGER DEFAULT 0,
            answered_wrong INTEGER DEFAULT 0,
            initial_count INTEGER NOT NULL DEFAULT 0,
            current_qid TEXT,
            saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, category_id)
        )
    """)
    conn.commit()
    conn.close()


# ==================== 考试记录 ====================

def init_exam_records_table():
    """初始化 exam_records 表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_exam_record(user_id, subject_id, category_id, total, correct_count, score):
    """保存一次考试记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO exam_records (user_id, subject_id, category_id, total, correct_count, score)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, subject_id, category_id, total, correct_count, score))
    conn.commit()
    conn.close()


def get_exam_records(user_id, subject_id=None, category_id=None):
    """获取用户的考试记录列表"""
    conn = get_db()
    cur = conn.cursor()
    if category_id:
        cur.execute("""
            SELECT * FROM exam_records
            WHERE user_id = ? AND category_id = ?
            ORDER BY created_at DESC
        """, (user_id, category_id))
    elif subject_id:
        cur.execute("""
            SELECT * FROM exam_records
            WHERE user_id = ? AND subject_id = ?
            ORDER BY created_at DESC
        """, (user_id, subject_id))
    else:
        cur.execute("""
            SELECT * FROM exam_records
            WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,))
    result = [dict(r) for r in cur.fetchall()]
    conn.close()
    return result


# ==================== 通知系统 ====================

def init_notifications_table():
    """初始化 notifications 表"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            question_id TEXT,
            is_read INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notif_user_read ON notifications(user_id, is_read)")
    conn.commit()
    conn.close()


def create_notification(user_id, notif_type, title, content=None, question_id=None):
    """创建通知"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notifications (user_id, type, title, content, question_id) VALUES (?, ?, ?, ?, ?)",
        (user_id, notif_type, title, content[:500] if content else None, question_id)
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_unread_notification_count(user_id):
    """获取用户未读通知数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_user_notifications(user_id, page=1, per_page=20):
    """获取用户通知列表"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    offset = (page - 1) * per_page
    cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ?", (user_id,))
    total = cur.fetchone()[0]
    cur.execute(
        """SELECT * FROM notifications
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (user_id, per_page, offset)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total


def mark_notification_read(notif_id, user_id):
    """标记通知为已读"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
        (notif_id, user_id)
    )
    conn.commit()
    conn.close()


def mark_all_notifications_read(user_id):
    """标记用户所有通知为已读"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def delete_notification(notif_id, user_id):
    """删除通知"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notif_id, user_id))
    conn.commit()
    conn.close()


# 管理员通知统计

def get_admin_feedback_pending_count():
    """获取待处理纠错数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM question_feedbacks WHERE status = 'pending' OR status IS NULL")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_admin_comment_unread_count():
    """获取未读留言数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM question_comments WHERE read_by_admin_at IS NULL AND status = 'active'")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_admin_note_unread_count():
    """获取未读笔记数"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM question_notes WHERE read_by_admin_at IS NULL")
    count = cur.fetchone()[0]
    conn.close()
    return count


def mark_comments_read():
    """标记所有未读留言为已读"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE question_comments SET read_by_admin_at = CURRENT_TIMESTAMP WHERE read_by_admin_at IS NULL AND status = 'active'")
    conn.commit()
    conn.close()


def mark_notes_read():
    """标记所有未读笔记为已读"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE question_notes SET read_by_admin_at = CURRENT_TIMESTAMP WHERE read_by_admin_at IS NULL")
    conn.commit()
    conn.close()
