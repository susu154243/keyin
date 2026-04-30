#!/usr/bin/env python3
"""
管理端路由：/admin/* 所有管理功能。
使用 Flask Blueprint 隔离。
"""
import json
import csv
import io
import os
import re
import sqlite3
import zipfile
import hashlib
import zstandard
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from auth import admin_required, login_required

# ==================== apkg 导入工具函数 ====================

def _parse_media_protobuf(data: bytes) -> dict:
    """解析 Anki 2.1.28+ media 文件的 protobuf 格式
    
    返回 {filename: {'size': int, 'sha1': str}}
    """
    result = {}
    pos = 0
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag != 0x0a:  # 0x0a = 字段1, 字符串类型
            break
        entry_len = data[pos]
        pos += 1
        entry_data = data[pos:pos + entry_len]
        pos += entry_len
        
        # 解析条目: [0x0a + name_len + name] [0x10 + varint(size?)] [0x1a + hash_len + sha1]
        epos = 0
        name = None
        sha1_bytes = None
        
        while epos < len(entry_data):
            e_tag = entry_data[epos]
            epos += 1
            if e_tag == 0x0a:  # 文件名
                nlen = entry_data[epos]
                epos += 1
                name = entry_data[epos:epos + nlen].decode('utf-8')
                epos += nlen
            elif e_tag == 0x10:  # 大小/varint
                while epos < len(entry_data) and (entry_data[epos] & 0x80):
                    epos += 1
                epos += 1
            elif e_tag == 0x1a:  # SHA1
                hlen = entry_data[epos]
                epos += 1
                sha1_bytes = entry_data[epos:epos + hlen].hex()
                epos += hlen
            else:
                epos += 1
        
        if name and sha1_bytes:
            result[name] = {'sha1': sha1_bytes}
    
    return result


def _clean_html_stem(stem: str) -> str:
    """清理题干中冗余的 HTML 标签，保留纯文本和必要格式"""
    # 递归移除所有 <div> 标签，保留内部文本
    prev = None
    while prev != stem:
        prev = stem
        stem = re.sub(r'<div[^>]*>(.*?)</div>', r'\1', stem, flags=re.DOTALL)
    
    # 移除 <span> 标签，保留内部文本
    prev = None
    while prev != stem:
        prev = stem
        stem = re.sub(r'<span[^>]*>(.*?)</span>', r'\1', stem, flags=re.DOTALL)
    
    # 清理连续多余的空格和换行
    stem = re.sub(r'\s{2,}', ' ', stem)
    
    return stem.strip()


def _parse_options(options_str: str) -> dict:
    """解析选项字符串为 JSON 字典
    
    输入: A.选项A<br>B.选项B<br>C.选项C<br>D.选项D
    输出: {"A": "选项A", "B": "选项B", ...}
    """
    result = {}
    # 先移除 div/span 标签，保留内部文本
    cleaned = re.sub(r'</div>\s*', '', options_str)
    cleaned = re.sub(r'<div[^>]*>', '', cleaned)
    cleaned = re.sub(r'<span[^>]*>', '', cleaned)
    cleaned = re.sub(r'</span>', '', cleaned)
    
    # 分割 <br> 标签
    parts = re.split(r'<br\s*/?>', cleaned)
    for part in parts:
        part = part.strip()
        m = re.match(r'^([A-F])\.\s*(.*)', part, re.DOTALL)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _clean_answer(answer_str: str) -> str:
    """从答案字段提取纯字母
    
    输入: <span style="color: rgb(39, 200, 65);">B</span> 或 B
    输出: B
    """
    text = re.sub(r'<[^>]+>', '', answer_str).strip()
    letters = re.findall(r'[A-F]', text)
    return ''.join(letters)


def _extract_apkg(apkg_path: str, subject_id: int):
    """解析 apkg 文件并导入数据库
    
    返回: {"imported": int, "errors": int, "images": list, "categories": list}
    """
    from models import get_db, create_category, create_question
    
    result = {"imported": 0, "errors": 0, "images": [], "categories": []}
    
    work_dir = tempfile.mkdtemp(prefix='apkg_import_')
    
    try:
        # 1. 解压 zip
        with zipfile.ZipFile(apkg_path, 'r') as zf:
            zf.extractall(work_dir)
        
        # 2. 解析 media 文件（protobuf 格式）
        media_file = os.path.join(work_dir, 'media')
        media_map = {}  # {filename: sha1}
        if os.path.exists(media_file):
            with open(media_file, 'rb') as f:
                raw = f.read()
            try:
                dctx = zstandard.ZstdDecompressor()
                with dctx.stream_reader(memoryview(raw)) as reader:
                    media_data = reader.read()
                media_map = _parse_media_protobuf(media_data)
            except Exception:
                pass
        
        # 3. 构建 sha1 → 文件名映射
        sha1_to_name = {v['sha1']: k for k, v in media_map.items()}
        
        # 4. 解压媒体文件，用 SHA1 匹配原始文件名
        static_media_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'static', 'media'
        )
        os.makedirs(static_media_dir, exist_ok=True)
        
        # 遍历 zip 中的媒体条目（编号 0, 1, 2, ...）
        with zipfile.ZipFile(apkg_path, 'r') as zf:
            entry_idx = 0
            while True:
                entry_name = str(entry_idx)
                if entry_name not in zf.namelist():
                    break
                
                compressed = zf.read(entry_name)
                try:
                    dctx = zstandard.ZstdDecompressor()
                    with dctx.stream_reader(memoryview(compressed)) as reader:
                        decompressed = reader.read()
                except Exception:
                    entry_idx += 1
                    continue
                
                # 计算 SHA1，匹配原始文件名
                sha1 = hashlib.sha1(decompressed).hexdigest()
                original_name = sha1_to_name.get(sha1)
                
                if original_name:
                    # 只保存图片，跳过字体等
                    ext = os.path.splitext(original_name)[1].lower()
                    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp'):
                        save_path = os.path.join(static_media_dir, original_name)
                        if not os.path.exists(save_path):
                            with open(save_path, 'wb') as f:
                                f.write(decompressed)
                        result["images"].append(original_name)
                
                entry_idx += 1
        
        # 5. 解压 collection.anki21b 数据库
        db_candidates = ['collection.anki21b', 'collection.anki2']
        db_path = None
        for name in db_candidates:
            p = os.path.join(work_dir, name)
            if os.path.exists(p):
                if name.endswith('.anki21b'):
                    decompressed_db = os.path.join(work_dir, 'collection.db')
                    with open(p, 'rb') as f:
                        raw = f.read()
                    try:
                        dctx = zstandard.ZstdDecompressor()
                        with dctx.stream_reader(memoryview(raw)) as reader:
                            db_data = reader.read()
                        with open(decompressed_db, 'wb') as f:
                            f.write(db_data)
                        db_path = decompressed_db
                    except Exception:
                        db_path = p
                else:
                    db_path = p
                break
        
        if not db_path or not os.path.exists(db_path):
            result["errors"] += 1
            return result
        
        # 6. 获取牌组名称（用于分类命名）
        # Anki 2.1.28+ 的 decks 表有 unicase collation 问题，改用原始 sqlite3 连接
        deck_name = ""
        try:
            raw_conn = sqlite3.connect(db_path)
            raw_cur = raw_conn.cursor()
            raw_cur.execute("SELECT id, name FROM decks")
            for row in raw_cur.fetchall():
                did, name = row[0], row[1]
                if name and name != 'Default':
                    deck_name = name
                    break
            raw_conn.close()
        except Exception:
            pass
        
        # 7. 解析牌组名称，自动创建分类
        # 预期格式: "1.1 信息化发展--信息与信息化"
        # 二级分类: "第1章 信息化发展"
        # 三级分类: "1.1 信息与信息化"
        level2_name = ""
        level3_name = ""
        
        if deck_name and '--' in deck_name:
            parts = deck_name.split('--', 1)
            # parts[0] = "1.1 信息化发展", parts[1] = "信息与信息化"
            # 二级分类: 取章号 + 主题
            chapter_match = re.match(r'^(\d+(?:\.\d+)?)\s*(.*)', parts[0].strip())
            if chapter_match:
                chapter_num = chapter_match.group(1)
                chapter_text = chapter_match.group(2).strip()
                level2_name = f"第{chapter_num.split('.')[0]}章 {chapter_text}"
            else:
                level2_name = parts[0].strip()
            level3_name = f"{parts[0].strip().split()[0] if parts[0].strip() else ''} {parts[1].strip()}".strip()
            # 如果三级名称以数字开头，保留；否则添加前缀
            if not re.match(r'^\d', level3_name):
                level3_name = f"{parts[0].strip().split()[0] if parts[0].strip() else ''} {parts[1].strip()}".strip()
        elif deck_name:
            level3_name = deck_name
        
        # 创建分类
        level2_id = None
        level3_id = None
        
        if level2_name:
            # 查找是否已存在
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM categories WHERE subject_id = ? AND name = ? AND level = 2",
                (subject_id, level2_name)
            )
            existing = cur.fetchone()
            conn.close()
            
            if existing:
                level2_id = existing['id']
            else:
                level2_id = create_category(subject_id, 0, level2_name, 2)
                result["categories"].append(f"[二级] {level2_name}")
        
        if level3_name and level2_id:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM categories WHERE subject_id = ? AND name = ? AND level = 3 AND parent_id = ?",
                (subject_id, level3_name, level2_id)
            )
            existing = cur.fetchone()
            conn.close()
            
            if existing:
                level3_id = existing['id']
            else:
                level3_id = create_category(subject_id, level2_id, level3_name, 3)
                result["categories"].append(f"[三级] {level3_name}")
        
        # 8. 解析 notes 并导入
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        cur.execute("SELECT id, mid, flds, sfld FROM notes ORDER BY id")
        notes = cur.fetchall()
        conn.close()
        
        for note in notes:
            try:
                fields = note['flds'].split('\x1f')
                if len(fields) < 4:
                    result["errors"] += 1
                    continue
                
                stem = fields[0]  # 题干
                options_raw = fields[1]  # 选项
                answer_raw = fields[2]  # 答案
                explanation = fields[3]  # 解析
                
                # 清理题干
                stem = _clean_html_stem(stem)
                
                # 清理选项末尾的多余 </div>
                options_raw = re.sub(r'</div>\s*$', '', options_raw)
                
                # 解析选项
                options = _parse_options(options_raw)
                
                # 提取答案
                answer = _clean_answer(answer_raw)
                
                if not stem or not answer:
                    result["errors"] += 1
                    continue
                
                # 判断题型
                qtype = 'multiple' if len(answer) > 1 else 'single'
                qtype_text = '多选题' if qtype == 'multiple' else '单选题'
                
                # 更新解析中的图片路径
                if result["images"]:
                    for img_name in result["images"]:
                        old_ref = f'src="{img_name}"'
                        new_ref = f'src="/static/media/{img_name}"'
                        explanation = explanation.replace(old_ref, new_ref)
                
                # 写入数据库
                qdata = {
                    'stem': stem,
                    'options': json.dumps(options, ensure_ascii=False),
                    'answer': answer,
                    'explanation': explanation,
                    'qtype': qtype,
                    'qtype_text': qtype_text,
                    'difficulty': '无',
                    'subject_id': subject_id,
                    'category_id': level3_id,
                    'is_real_exam': 0,
                    'exam_year': None,
                    'source': 'practice',
                }
                
                create_question(qdata)
                result["imported"] += 1
                
            except Exception as e:
                result["errors"] += 1
                continue
    
    finally:
        # 清理临时目录
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    
    return result
from models import (
    authenticate_user, get_all_users, create_user, update_user_status,
    get_all_subjects_admin, get_subject, create_subject, update_subject,
    get_categories_tree, get_leaf_categories, create_category, delete_category,
    get_questions_by_subject, create_question, update_question, delete_question, get_question,
    get_all_subjects_for_permission, get_user_permissions, set_user_subject_permission,
    get_user_by_id, update_user_last_login, hash_password,
    # 新增
    get_category, get_subject_by_id,
)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ==================== 管理端登录 ====================

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate_user(username, password)
        if user and user['role'] == 'admin':
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = 'admin'
            update_user_last_login(user['id'])
            return redirect(url_for('admin.dashboard'))
        flash('用户名或密码错误，或无管理员权限', 'error')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('role', None)
    return redirect(url_for('admin.login'))


# ==================== 中间件 ====================

@admin_bp.before_request
def check_admin():
    """所有管理路由（除登录外）需要管理员权限"""
    if request.endpoint == 'admin.login':
        return
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('admin.login'))


# ==================== 仪表盘 ====================

@admin_bp.route('/')
@admin_bp.route('')
def dashboard():
    from models import get_db
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM users WHERE status = 1")
    active_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM subjects WHERE status = 1")
    active_subjects = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM questions WHERE status = 1")
    active_questions = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM history")
    total_answers = cur.fetchone()[0]
    
    conn.close()
    
    return render_template('admin/dashboard.html',
                          active_users=active_users,
                          active_subjects=active_subjects,
                          active_questions=active_questions,
                          total_answers=total_answers)


# ==================== 用户管理 ====================

@admin_bp.route('/users')
def users():
    all_users = get_all_users()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/users/create', methods=['GET', 'POST'])
def create_user_page():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        if not username or not password:
            flash('用户名和密码不能为空', 'error')
            return redirect(url_for('admin.create_user_page'))
        result = create_user(username, password, role)
        if result:
            flash(f'用户 {username} 创建成功', 'success')
            return redirect(url_for('admin.users'))
        else:
            flash('用户名已存在', 'error')
    return render_template('admin/user_create.html')


@admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
def toggle_user(user_id):
    user = get_user_by_id(user_id)
    if user:
        new_status = 0 if user['status'] == 1 else 1
        update_user_status(user_id, new_status)
        flash(f'用户 {user["username"]} 已{"启用" if new_status else "禁用"}', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
def reset_password(user_id):
    new_password = request.form.get('new_password', '')
    if not new_password:
        flash('密码不能为空', 'error')
        return redirect(url_for('admin.users'))
    
    from models import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    flash('密码已重置', 'success')
    return redirect(url_for('admin.users'))


# ==================== 科目管理 ====================

@admin_bp.route('/subjects')
def subjects():
    all_subjects = get_all_subjects_admin()
    return render_template('admin/subjects.html', subjects=all_subjects)


@admin_bp.route('/subjects/create', methods=['GET', 'POST'])
def create_subject_page():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '')
        icon = request.form.get('icon', '📚')
        if not name or not code:
            flash('名称和代码不能为空', 'error')
            return redirect(url_for('admin.create_subject_page'))
        result = create_subject(name, code, description, icon)
        if result:
            flash(f'科目 {name} 创建成功', 'success')
            return redirect(url_for('admin.subjects'))
        else:
            flash('科目代码已存在', 'error')
    return render_template('admin/subject_create.html')


@admin_bp.route('/subjects/<int:subject_id>/toggle', methods=['POST'])
def toggle_subject(subject_id):
    subject = get_subject(subject_id)
    if subject:
        new_status = 0 if subject['status'] == 1 else 1
        update_subject(subject_id, status=new_status)
        flash(f'科目 {subject["name"]} 已{"启用" if new_status else "禁用"}', 'success')
    return redirect(url_for('admin.subjects'))


# ==================== 分类管理 ====================

@admin_bp.route('/subjects/<int:subject_id>/categories')
def manage_categories(subject_id):
    tree = get_categories_tree(subject_id)
    subject = get_subject(subject_id)
    return render_template('admin/categories.html', tree=tree, subject=subject)


@admin_bp.route('/subjects/<int:subject_id>/categories/create', methods=['POST'])
def create_category_page(subject_id):
    parent_id = request.form.get('parent_id', 0, type=int)
    name = request.form.get('name', '').strip()
    if not name:
        flash('分类名称不能为空', 'error')
        return redirect(url_for('admin.manage_categories', subject_id=subject_id))
    
    level = 1
    if parent_id > 0:
        parent = get_category(parent_id)
        if parent:
            level = parent['level'] + 1
    
    create_category(subject_id, parent_id, name, level)
    flash(f'分类 {name} 创建成功', 'success')
    return redirect(url_for('admin.manage_categories', subject_id=subject_id))


@admin_bp.route('/categories/<int:category_id>/delete', methods=['POST'])
def delete_category_page(category_id):
    cat = get_category(category_id)
    subject_id = cat['subject_id'] if cat else None
    
    if subject_id:
        delete_category(category_id)
        flash('分类已删除', 'success')
        return redirect(url_for('admin.manage_categories', subject_id=subject_id))
    return redirect(url_for('admin.subjects'))


# ==================== 题目管理 ====================

@admin_bp.route('/questions')
def questions():
    subject_id = request.args.get('subject_id', 1, type=int)
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    per_page = 20
    
    questions, total = get_questions_by_subject(subject_id, page=page, per_page=per_page, search=search)
    total_pages = (total + per_page - 1) // per_page
    
    subjects = get_all_subjects_for_permission()
    
    return render_template('admin/questions.html',
                          questions=questions,
                          page=page,
                          total_pages=total_pages,
                          total=total,
                          search=search,
                          subject_id=subject_id,
                          subjects=subjects)


@admin_bp.route('/questions/create', methods=['GET', 'POST'])
def create_question_page():
    subject_id = request.args.get('subject_id', 1, type=int)
    leaf_cats = get_leaf_categories(subject_id)
    
    if request.method == 'POST':
        data = {
            'stem': request.form.get('stem', ''),
            'options': request.form.get('options', '{}'),
            'answer': request.form.get('answer', ''),
            'explanation': request.form.get('explanation', ''),
            'qtype': request.form.get('qtype', 'single'),
            'qtype_text': request.form.get('qtype_text', '单选题'),
            'difficulty': request.form.get('difficulty', '无'),
            'subject_id': subject_id,
            'category_id': request.form.get('category_id', type=int),
            'is_real_exam': request.form.get('is_real_exam', 0, type=int),
            'exam_year': request.form.get('exam_year', type=int),
            'source': request.form.get('source', 'practice'),
        }
        if not data['stem'] or not data['answer']:
            flash('题干和答案不能为空', 'error')
            return redirect(url_for('admin.create_question_page', subject_id=subject_id))
        qid = create_question(data)
        flash('题目创建成功', 'success')
        return redirect(url_for('admin.questions', subject_id=subject_id))
    
    return render_template('admin/question_create.html', subject_id=subject_id, leaf_cats=leaf_cats)


@admin_bp.route('/questions/<qid>/edit', methods=['GET', 'POST'])
def edit_question_page(qid):
    question = get_question(qid)
    if not question:
        flash('题目不存在', 'error')
        return redirect(url_for('admin.questions'))
    
    leaf_cats = get_leaf_categories(question['subject_id'])
    
    if request.method == 'POST':
        data = {
            'stem': request.form.get('stem', ''),
            'options': request.form.get('options', '{}'),
            'answer': request.form.get('answer', ''),
            'explanation': request.form.get('explanation', ''),
            'qtype': request.form.get('qtype', 'single'),
            'qtype_text': request.form.get('qtype_text', '单选题'),
            'difficulty': request.form.get('difficulty', '无'),
            'category_id': request.form.get('category_id', type=int),
            'is_real_exam': request.form.get('is_real_exam', 0, type=int),
            'exam_year': request.form.get('exam_year', type=int),
            'source': request.form.get('source', 'practice'),
        }
        update_question(qid, data)
        flash('题目更新成功', 'success')
        return redirect(url_for('admin.questions', subject_id=question['subject_id']))
    
    return render_template('admin/question_edit.html', question=question, leaf_cats=leaf_cats)


@admin_bp.route('/questions/<qid>/delete', methods=['POST'])
def delete_question_page(qid):
    question = get_question(qid)
    subject_id = question['subject_id'] if question else None
    
    if subject_id:
        delete_question(qid)
        flash('题目已删除', 'success')
        return redirect(url_for('admin.questions', subject_id=subject_id))
    return redirect(url_for('admin.questions'))


# ==================== 权限分配 ====================

@admin_bp.route('/permissions')
def permissions():
    users = get_all_users()
    subjects = get_all_subjects_for_permission()
    return render_template('admin/permissions.html', users=users, subjects=subjects)


@admin_bp.route('/permissions/<int:user_id>')
def user_permissions(user_id):
    user = get_user_by_id(user_id)
    perms = get_user_permissions(user_id)
    subjects = get_all_subjects_for_permission()
    return render_template('admin/user_permissions.html', user=user, perms=perms, subjects=subjects)


@admin_bp.route('/permissions/<int:user_id>/set', methods=['POST'])
def set_permissions(user_id):
    subject_id = request.form.get('subject_id', type=int)
    if not subject_id:
        flash('请选择科目', 'error')
        return redirect(url_for('admin.user_permissions', user_id=user_id))
    
    set_user_subject_permission(
        user_id, subject_id,
        can_practice=request.form.get('can_practice', 0, type=int),
        can_mock=request.form.get('can_mock', 0, type=int),
        can_daily=request.form.get('can_daily', 0, type=int),
        can_manage=request.form.get('can_manage', 0, type=int),
    )
    flash('权限已更新', 'success')
    return redirect(url_for('admin.user_permissions', user_id=user_id))


# ==================== 批量导入 ====================

@admin_bp.route('/import', methods=['GET', 'POST'])
def import_page():
    subjects = get_all_subjects_for_permission()
    
    if request.method == 'POST':
        subject_id = request.form.get('subject_id', type=int)
        if not subject_id:
            flash('请选择科目', 'error')
            return redirect(url_for('admin.import_page'))
        
        file = request.files.get('csv_file')
        if not file or not file.filename.endswith('.csv'):
            flash('请上传 CSV 文件', 'error')
            return redirect(url_for('admin.import_page'))
        
        # 解析 CSV
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        
        imported = 0
        errors = 0
        
        # 优化：一次加载所有分类到内存，避免每行 N 次 DB 查询
        from models import get_db
        conn = get_db()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, subject_id, parent_id, name, level FROM categories WHERE subject_id = ?",
                   (subject_id,))
        all_cats = {f"{r['level']}_{r['name']}": r['id'] for r in cur.fetchall()}
        conn.close()
        
        for row in reader:
            try:
                category_id = None
                for level in [3, 2, 1]:
                    cat_name = row.get(f'category_l{level}', '').strip()
                    if cat_name:
                        key = f"{level}_{cat_name}"
                        if key in all_cats:
                            category_id = all_cats[key]
                            break
                        elif level == 1:
                            # 一级分类没找到，跳过
                            break
                
                if not category_id:
                    errors += 1
                    continue
                
                data = {
                    'stem': row.get('stem', ''),
                    'options': row.get('options', '{}'),
                    'answer': row.get('answer', ''),
                    'explanation': row.get('explanation', ''),
                    'qtype': 'multiple' if '多选' in row.get('qtype', '') else 'single',
                    'qtype_text': row.get('qtype', '单选题'),
                    'difficulty': row.get('difficulty', '无'),
                    'subject_id': subject_id,
                    'category_id': category_id,
                    'is_real_exam': 1 if row.get('is_real_exam', '0') == '1' else 0,
                    'exam_year': int(row['exam_year']) if row.get('exam_year', '').isdigit() else None,
                    'source': row.get('source', 'practice'),
                }
                
                if data['stem'] and data['answer']:
                    create_question(data)
                    imported += 1
            except Exception:
                errors += 1
                continue
        
        flash(f'导入完成：成功 {imported} 条，失败 {errors} 条', 'success')
        return redirect(url_for('admin.questions', subject_id=subject_id))
    
    return render_template('admin/import.html', subjects=subjects)


# ==================== Anki apkg 导入 ====================

@admin_bp.route('/import-apkg', methods=['GET', 'POST'])
def import_apkg():
    """Anki .apkg 文件导入"""
    subjects = get_all_subjects_for_permission()
    
    if request.method == 'POST':
        subject_id = request.form.get('subject_id', type=int)
        if not subject_id:
            flash('请选择科目', 'error')
            return redirect(url_for('admin.import_apkg'))
        
        file = request.files.get('apkg_file')
        if not file or not file.filename:
            flash('请上传 .apkg 文件', 'error')
            return redirect(url_for('admin.import_apkg'))
        
        # 保存临时文件
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix='.apkg', delete=False)
        file.save(tmp.name)
        tmp.close()
        
        try:
            result = _extract_apkg(tmp.name, subject_id)
        finally:
            os.unlink(tmp.name)
        
        if result['errors'] > 0 and result['imported'] == 0:
            flash(f'导入失败：{result["errors"]} 条错误', 'error')
        else:
            msg = f'导入完成：成功 {result["imported"]} 条'
            if result['errors']:
                msg += f'，失败 {result["errors"]} 条'
            if result['images']:
                msg += f'，图片 {len(result["images"])} 张'
            if result['categories']:
                msg += f'，新建分类：{"、".join(result["categories"])}'
            flash(msg, 'success')
        
        return redirect(url_for('admin.questions', subject_id=subject_id))
    
    return render_template('admin/import_apkg.html', subjects=subjects)
