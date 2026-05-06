#!/usr/bin/env python3
"""
管理端路由：/admin/* 所有管理功能。
使用 Flask Blueprint 隔离。
"""
import json
import csv
import io
import logging
import os
import re
import sqlite3
import zipfile
import hashlib
import zstandard
import tempfile

logger = logging.getLogger(__name__)
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from auth import admin_required, login_required
from models import get_db, verify_session_token, sanitize_html
from models import (
    update_question_id, health_check_questions, batch_delete_by_category,
    batch_move_questions, batch_update_questions, create_import_log,
    get_import_logs, delete_import_log,
    authenticate_user, get_all_users, create_user, update_user_status,
    get_all_subjects_admin, get_subject, create_subject, update_subject, delete_subject,
    get_subject_stats,
    get_categories_tree, get_leaf_categories, create_category, delete_category,
    get_questions_by_subject, create_question, update_question, delete_question, get_question,
    get_all_subjects_for_permission, get_user_permissions, set_user_subject_permission,
    get_user_by_id, update_user_last_login, hash_password,
    set_user_session_token, clear_user_session_token,
    get_category, get_subject_by_id,

    grant_user_license, revoke_user_license, get_all_licenses,
)

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


def _generate_question_id(category_name: str, stem: str) -> str | None:
    """根据分类名和题干生成有意义的题目 ID，如 '1.2-01'。
    
    规则:
    - 从分类名提取前缀 (如 '1.2' from '1.2 现代化基础设施')
    - 从题干提取题号 (如 '01' from '01.以下关于信息化描述')
    - 组合为 'prefix-num' (如 '1.2-01')
    - 如果 ID 已存在或无法提取，返回 None（回退到 UUID）
    """
    # 提取分类前缀
    cat_match = re.match(r'^(\d+\.\d+)', category_name or '')
    if not cat_match:
        return None
    prefix = cat_match.group(1)

    # 提取题干序号 (支持 "01." "3、" "第3题." 等格式)
    num_match = re.match(r'^(?:第)?(\d+)(?:题)?[\.、．]', stem or '')
    if not num_match:
        return None
    num = int(num_match.group(1))
    num_str = f'{num:02d}'

    qid = f'{prefix}-{num_str}'

    # 检查是否已存在
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id FROM questions WHERE id = ?', (qid,))
    exists = cur.fetchone()
    conn.close()
    if exists:
        return None  # 已存在，回退 UUID
    return qid


def _extract_apkg(apkg_path: str, subject_id: int):
    """解析 apkg 文件并导入数据库
    
    返回: {"imported": int, "errors": int, "images": list, "categories": list}
    """
    from models import get_db, create_category, create_staging_record, clear_staging_by_subject
    
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
            except Exception as e:
                logger.debug(f"解析 media protobuf 失败: {e}")
        
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
                except Exception as e:
                    logger.debug(f"解压媒体条目 {entry_idx} 失败: {e}")
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
                    except Exception as e:
                        logger.debug(f"zstd 解压数据库失败，使用原始文件: {e}")
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
        except Exception as e:
            logger.debug(f"读取 Anki decks 表失败: {e}")
        
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

                # 构造有意义的 ID: "1.2-01"
                q_id = _generate_question_id(level3_name, stem)
                
                # 从题干提取编号前缀 (如 "01.", "13.") 作为 question_id
                q_num = ""
                num_match = re.match(r'^(\d+)[.、]\s*', stem)
                if num_match:
                    q_num = num_match.group(1)

                # 写入 staging 确认库
                sdata = {
                    'question_id': q_num,
                    'subject_id': subject_id,
                    'category_id': level3_id,
                    'category_name': level3_name,
                    'stem': stem,
                    'option_a': options.get('A', ''),
                    'option_b': options.get('B', ''),
                    'option_c': options.get('C', ''),
                    'option_d': options.get('D', ''),
                    'option_e': options.get('E', ''),
                    'option_f': options.get('F', ''),
                    'correct_answer': answer,
                    'explanation': explanation,
                }

                create_staging_record(sdata)
                result["imported"] += 1
                
            except Exception as e:
                result["errors"] += 1
                continue
    
    finally:
        # 清理临时目录
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    
    return result

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ==================== 管理端登录 ====================

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate_user(username, password)
        if user and user['role'] == 'admin':
            # 清除旧 session 数据
            session.pop('practice', None)
            session.pop('chapter_progress', None)
            session.pop('reinforce', None)
            import secrets
            token = secrets.token_hex(32)
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = 'admin'
            session['session_token'] = token
            update_user_last_login(user['id'])
            set_user_session_token(user['id'], token)
            return redirect(url_for('admin.dashboard'))
        flash('用户名或密码错误，或无管理员权限', 'error')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    user_id = session.get('user_id')
    if user_id:
        clear_user_session_token(user_id)
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('role', None)
    session.pop('session_token', None)
    return redirect(url_for('admin.login'))


# ==================== 中间件 ====================

@admin_bp.before_request
def check_admin():
    """所有管理路由（除登录外）需要管理员权限"""
    if request.endpoint == 'admin.login':
        return
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('admin.login'))
    if not verify_session_token(session['user_id'], session.get('session_token')):
        session.clear()
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


# ==================== 授权管理 ====================

@admin_bp.route('/licenses')
def licenses():
    """授权管理页面"""
    all_licenses = get_all_licenses()
    all_users = get_all_users()
    all_subjects = get_all_subjects_admin()
    return render_template('admin/licenses.html', 
                          licenses=all_licenses, 
                          users=all_users, 
                          subjects=all_subjects)


@admin_bp.route('/licenses/grant', methods=['POST'])
def grant_license():
    """授予/延长授权"""
    user_id = request.form.get('user_id', type=int)
    subject_id = request.form.get('subject_id', type=int)
    days = request.form.get('days', type=int, default=365)
    
    if not user_id or not subject_id:
        flash('用户和科目不能为空', 'error')
        return redirect(url_for('admin.licenses'))
    
    grant_user_license(user_id, subject_id, days)
    flash(f'已授予用户 {user_id} 科目 {subject_id} {days} 天授权', 'success')
    return redirect(url_for('admin.licenses'))


@admin_bp.route('/licenses/revoke/<int:license_id>', methods=['POST'])
def revoke_license(license_id):
    """吊销授权"""
    from models import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, subject_id FROM user_licenses WHERE id = ?", (license_id,))
    row = cur.fetchone()
    if row:
        revoke_user_license(row[0], row[1])
        flash(f'已吊销用户 {row[0]} 科目 {row[1]} 的授权', 'success')
    conn.close()
    return redirect(url_for('admin.licenses'))


# ==================== 科目管理 ====================

@admin_bp.route('/subjects')
def subjects():
    all_subjects = get_all_subjects_admin()
    stats = {s['id']: get_subject_stats(s['id']) for s in all_subjects}
    return render_template('admin/subjects.html', subjects=all_subjects, stats=stats)


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


@admin_bp.route('/subjects/<int:subject_id>/edit', methods=['GET', 'POST'])
def edit_subject_page(subject_id):
    subject = get_subject(subject_id)
    if not subject:
        flash('科目不存在', 'error')
        return redirect(url_for('admin.subjects'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '')
        icon = request.form.get('icon', '📚')
        if not name or not code:
            flash('名称和代码不能为空', 'error')
            return redirect(url_for('admin.edit_subject_page', subject_id=subject_id))
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', code):
            flash('代码只能包含字母、数字和下划线', 'error')
            return redirect(url_for('admin.edit_subject_page', subject_id=subject_id))
        result = update_subject(subject_id, name=name, code=code, description=description, icon=icon)
        flash(f'科目 {name} 更新成功', 'success')
        return redirect(url_for('admin.subjects'))
    return render_template('admin/subject_edit.html', subject=subject)


@admin_bp.route('/subjects/<int:subject_id>/delete', methods=['POST'])
def delete_subject_page(subject_id):
    subject = get_subject(subject_id)
    if not subject:
        flash('科目不存在', 'error')
        return redirect(url_for('admin.subjects'))
    ok, err = delete_subject(subject_id)
    if ok:
        flash(f'科目 {subject["name"]} 已删除', 'success')
    else:
        flash(f'无法删除：{err}', 'error')
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
    category_id = request.args.get('category_id', 0, type=int)
    per_page = 20
    
    questions, total = get_questions_by_subject(subject_id, page=page, per_page=per_page, search=search, category_id=category_id or None)
    total_pages = (total + per_page - 1) // per_page
    
    subjects = get_all_subjects_for_permission()
    categories = get_leaf_categories(subject_id)
    
    return render_template('admin/questions.html',
                          questions=questions,
                          page=page,
                          total_pages=total_pages,
                          total=total,
                          search=search,
                          subject_id=subject_id,
                          category_id=category_id,
                          subjects=subjects,
                          categories=categories)


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
    old_id = qid
    
    if request.method == 'POST':
        # 处理 ID 修改
        new_id = request.form.get('question_id', '').strip()
        if new_id and new_id != old_id:
            ok, msg = update_question_id(old_id, new_id)
            if not ok:
                flash(f'ID 修改失败: {msg}', 'error')
                return render_template('admin/question_edit.html', question=question, leaf_cats=leaf_cats)
            qid = new_id
        
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
            'status': request.form.get('status', 1, type=int),
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
            except Exception as e:
                logger.debug(f"导入题目失败: {e}")
                errors += 1
                continue
        
        flash(f'导入完成：成功 {imported} 条，失败 {errors} 条', 'success')
        
        # 记录导入日志
        subject = get_subject_by_id(subject_id)
        create_import_log({
            'operator': session.get('username', 'admin'),
            'file_name': file.filename,
            'file_type': 'csv',
            'subject_id': subject_id,
            'subject_name': subject['name'] if subject else '',
            'imported': imported,
            'errors': errors,
            'status': 'success' if errors == 0 else 'partial',
        })
        
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
        
        # 记录导入日志
        subject = get_subject_by_id(subject_id)
        status = 'failed' if result['imported'] == 0 else ('partial' if result['errors'] else 'success')
        create_import_log({
            'operator': session.get('username', 'admin'),
            'file_name': file.filename,
            'file_type': 'apkg',
            'subject_id': subject_id,
            'subject_name': subject['name'] if subject else '',
            'imported': result['imported'],
            'errors': result['errors'],
            'status': status,
        })
        
        if result['errors'] > 0 and result['imported'] == 0:
            flash(f'导入失败：{result["errors"]} 条错误', 'error')
        else:
            msg = f'已放入确认库：{result["imported"]} 条待确认'
            if result['errors']:
                msg += f'，失败 {result["errors"]} 条'
            if result['images']:
                msg += f'，图片 {len(result["images"])} 张'
            if result['categories']:
                msg += f'，新建分类：{"、".join(result["categories"])}'
            flash(msg, 'success')
        
        return redirect(url_for('admin.import_staging', subject_id=subject_id))
    
    return render_template('admin/import_apkg.html', subjects=subjects)


# ==================== 导入确认库 (Staging) ====================

@admin_bp.route('/import-staging')
def import_staging():
    """导入确认库列表页"""
    from models import get_staging_subject_counts
    staging_counts = get_staging_subject_counts()
    return render_template('admin/import_staging.html',
                          staging_counts=staging_counts,
                          subjects=get_all_subjects_for_permission())


@admin_bp.route('/import-staging/<int:subject_id>')
def import_staging_detail(subject_id):
    """某科目的 staging 详情"""
    from models import get_staging_by_subject
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    staging, total = get_staging_by_subject(subject_id, page, page_size=20, search=search)
    
    # 获取该科目的所有分类供选择
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, level, parent_id FROM categories WHERE subject_id = ? ORDER BY level, id", (subject_id,))
    categories = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    total_pages = (total + 19) // 20
    
    return render_template('admin/import_staging_detail.html',
                          staging=staging,
                          categories=categories,
                          subject_id=subject_id,
                          page=page,
                          total_pages=total_pages,
                          total=total,
                          search=search)


@admin_bp.route('/import-staging/<int:subject_id>/confirm-all', methods=['POST'])
def import_staging_confirm_all(subject_id):
    """将 staging 全部导入正式题库（批量单事务，避免数据库锁竞争）"""
    from models import get_db, get_staging_by_subject, clear_staging_by_subject, get_subject_by_id, create_import_log
    from datetime import datetime
    import uuid
    
    staging, total = get_staging_by_subject(subject_id, page=1, page_size=10000)
    
    # 批量收集 INSERT 数据
    rows = []
    for item in staging:
        try:
            options = []
            for key in ['option_a', 'option_b', 'option_c', 'option_d', 'option_e', 'option_f']:
                if item.get(key):
                    options.append(sanitize_html(item[key]))
            
            q_id = ''
            cat_name = item.get('category_name', '')
            stem = item.get('stem', '')
            category_id = item.get('category_id')
            
            # 防御：先按 stem 检查同一分类是否已存在题目（防止重复导入生成UUID垃圾数据）
            if stem and category_id:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT id FROM questions WHERE stem = ? AND category_id = ?", (stem, category_id))
                existing = cur.fetchone()
                conn.close()
                if existing:
                    logger.debug(f"题目已存在，跳过: {existing['id']} stem={stem[:20]}...")
                    continue  # 跳过重复题目
            
            # 优先使用 _generate_question_id 从分类名提取前缀拼接 (如 3.1-01)
            if cat_name and stem:
                q_id = _generate_question_id(cat_name, stem)
            # 回退：旧格式 subject_id.question_id → 改为从分类名提取前缀
            if not q_id and item.get('question_id') and cat_name:
                import re
                cat_match = re.match(r'^(\d+\.\d+)', cat_name)
                if cat_match:
                    q_id = f"{cat_match.group(1)}-{item['question_id']}"
            # 最终回退：UUID（理论上不应该到达这里）
            if not q_id:
                q_id = str(uuid.uuid4())[:8]
            
            answer = item.get('correct_answer', '').upper()
            qtype = 'multiple' if len(answer) > 1 else 'single'
            qtype_text = '多选题' if qtype == 'multiple' else '单选题'
            
            rows.append((
                q_id,
                sanitize_html(item['stem']),
                json.dumps(options, ensure_ascii=False),
                answer,
                sanitize_html(item.get('explanation', '')),
                qtype,
                '无',
                subject_id,
                item.get('category_id'),
                0,
                None,
                'practice',
                qtype_text,
            ))
        except Exception as e:
            logger.debug(f"导入 staging 记录 {item['id']} 失败: {e}")
            continue
    
    # 单事务批量 INSERT
    imported = 0
    errors = 0
    if rows:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.executemany("""
                INSERT INTO questions (
                    id, stem, options, answer, explanation, qtype, difficulty,
                    subject_id, category_id, is_real_exam, exam_year, source, qtype_text, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, rows)
            conn.commit()
            imported = len(rows)
        except Exception as e:
            logger.error(f"批量导入题目失败: {e}")
            errors = len(rows)
            conn.rollback()
        finally:
            conn.close()
    
    # 清空 staging（clear_staging_by_subject 自带重试）
    clear_staging_by_subject(subject_id)
    
    status = 'failed' if imported == 0 else ('partial' if errors else 'success')
    subject = get_subject_by_id(subject_id)
    create_import_log({
        'operator': session.get('username', 'admin'),
        'file_name': f'确认库导入 (科目{subject_id})',
        'file_type': 'staging',
        'subject_id': subject_id,
        'subject_name': subject['name'] if subject else '',
        'imported': imported,
        'errors': errors,
        'status': status,
    })
    
    if imported == 0:
        flash(f'确认库导入失败：{errors} 条错误', 'error')
    else:
        msg = f'确认库导入完成：成功 {imported} 条'
        if errors:
            msg += f'，失败 {errors} 条'
        flash(msg, 'success')
    
    return redirect(url_for('admin.questions', subject_id=subject_id))


@admin_bp.route('/import-staging/<int:subject_id>/clear', methods=['POST'])
def import_staging_clear(subject_id):
    """清空某科目的 staging"""
    from models import clear_staging_by_subject
    count = clear_staging_by_subject(subject_id)
    flash(f'已清空 {count} 条待确认题目', 'info')
    return redirect(url_for('admin.import_staging'))


@admin_bp.route('/import-staging/<int:staging_id>/edit', methods=['GET', 'POST'])
def import_staging_edit(staging_id):
    """编辑 staging 单条记录"""
    from models import get_staging_record, update_staging_record
    
    if request.method == 'POST':
        data = {
            'question_id': request.form.get('question_id', '').strip(),
            'subject_id': request.form.get('subject_id', type=int),
            'category_id': request.form.get('category_id', type=int) or None,
            'category_name': request.form.get('category_name', '').strip(),
            'stem': request.form.get('stem', '').strip(),
            'option_a': request.form.get('option_a', '').strip(),
            'option_b': request.form.get('option_b', '').strip(),
            'option_c': request.form.get('option_c', '').strip(),
            'option_d': request.form.get('option_d', '').strip(),
            'option_e': request.form.get('option_e', '').strip(),
            'option_f': request.form.get('option_f', '').strip(),
            'correct_answer': request.form.get('correct_answer', '').strip().upper(),
            'explanation': request.form.get('explanation', '').strip(),
        }
        update_staging_record(staging_id, data)
        subject_id = data['subject_id']
        flash('题目已更新', 'success')
        return redirect(url_for('admin.import_staging_detail', subject_id=subject_id))
    
    record = get_staging_record(staging_id)
    if not record:
        flash('记录不存在', 'error')
        return redirect(url_for('admin.import_staging'))
    
    # 获取分类
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, level, parent_id FROM categories WHERE subject_id = ? ORDER BY level, id", (record['subject_id'],))
    categories = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return render_template('admin/import_staging_edit.html',
                          record=record,
                          categories=categories)


@admin_bp.route('/import-staging/<int:staging_id>/delete', methods=['POST'])
def import_staging_delete(staging_id):
    """删除 staging 单条记录"""
    from models import get_staging_record, delete_staging_record
    record = get_staging_record(staging_id)
    if record:
        subject_id = record['subject_id']
        delete_staging_record(staging_id)
        flash('已删除', 'info')
        return redirect(url_for('admin.import_staging_detail', subject_id=subject_id))
    flash('记录不存在', 'error')
    return redirect(url_for('admin.import_staging'))


# ==================== 数据健康检查 ====================

@admin_bp.route('/health')
def health_check():
    """数据健康检查页面"""
    subjects = get_all_subjects_for_permission()
    subject_id = request.args.get('subject_id', type=int)
    
    results = None
    if subject_id:
        results = health_check_questions(subject_id)
    
    return render_template('admin/health.html', subjects=subjects, subject_id=subject_id, results=results)


# ==================== 批量操作 ====================

@admin_bp.route('/batch', methods=['GET', 'POST'])
def batch_ops():
    """批量操作页面"""
    subjects = get_all_subjects_for_permission()
    subject_id = request.args.get('subject_id', type=int)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'delete_by_category':
            category_id = request.form.get('category_id', type=int)
            if not category_id:
                flash('请选择分类', 'error')
                return redirect(url_for('admin.batch_ops', subject_id=subject_id))
            count = batch_delete_by_category(category_id)
            flash(f'已软删除 {count} 道题目', 'success')
        
        elif action == 'move_questions':
            from_cat = request.form.get('from_category_id', type=int)
            to_cat = request.form.get('to_category_id', type=int)
            if not from_cat or not to_cat:
                flash('请选择源分类和目标分类', 'error')
                return redirect(url_for('admin.batch_ops', subject_id=subject_id))
            count, msg = batch_move_questions(from_cat, to_cat)
            if msg != 'OK':
                flash(f'迁移失败: {msg}', 'error')
            else:
                flash(f'已迁移 {count} 道题目', 'success')
        
        elif action == 'batch_update':
            category_id = request.form.get('category_id', type=int)
            if not category_id:
                flash('请选择分类', 'error')
                return redirect(url_for('admin.batch_ops', subject_id=subject_id))
            
            # 获取该分类下所有题目 ID
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM questions WHERE category_id = ? AND status = 1", (category_id,))
            qids = [r['id'] for r in cur.fetchall()]
            conn.close()
            
            if not qids:
                flash('该分类下没有启用的题目', 'warning')
                return redirect(url_for('admin.batch_ops', subject_id=subject_id))
            
            update_data = {}
            if request.form.get('difficulty'):
                update_data['difficulty'] = request.form.get('difficulty')
            if request.form.get('source'):
                update_data['source'] = request.form.get('source')
            is_real = request.form.get('is_real_exam')
            if is_real is not None:
                update_data['is_real_exam'] = 1 if is_real == '1' else 0
            status_val = request.form.get('status')
            if status_val is not None:
                update_data['status'] = int(status_val)
            
            if update_data:
                count = batch_update_questions(qids, update_data)
                flash(f'已更新 {count} 道题目', 'success')
            else:
                flash('没有需要更新的字段', 'warning')
        
        return redirect(url_for('admin.batch_ops', subject_id=subject_id))
    
    # GET: 显示分类列表供选择
    leaf_cats = []
    all_cats = []
    if subject_id:
        leaf_cats = get_leaf_categories(subject_id)
        # 获取所有分类（含父级）用于迁移
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, level FROM categories WHERE subject_id = ? ORDER BY level, name", (subject_id,))
        all_cats = [dict(r) for r in cur.fetchall()]
        conn.close()
    
    return render_template('admin/batch_ops.html', subjects=subjects, subject_id=subject_id,
                          leaf_cats=leaf_cats, all_cats=all_cats)


# ==================== 导入日志 ====================

@admin_bp.route('/import-logs')
def import_logs_page():
    """导入日志列表"""
    page = request.args.get('page', 1, type=int)
    logs, total = get_import_logs(page=page)
    total_pages = (total + 20 - 1) // 20
    return render_template('admin/import_logs.html', logs=logs, page=page, total_pages=total_pages, total=total)


@admin_bp.route('/import-logs/<int:log_id>/delete', methods=['POST'])
def delete_import_log_page(log_id):
    """删除单条导入日志"""
    delete_import_log(log_id)
    flash('日志已删除', 'success')
    return redirect(url_for('admin.import_logs_page'))




# ==================== 邀请码管理 ====================

from models import (
    generate_invitation_code, create_invitation_code, get_invitation_code,
    list_invitation_codes, disable_invitation_code, delete_invitation_code,
    get_code_usage_logs, get_subject_by_id, hash_password,
    reset_user_password, get_user_by_id, get_all_subjects_admin,
)
from datetime import datetime, timedelta


@admin_bp.route('/codes')
@admin_required
def admin_codes():
    """邀请码列表"""
    page = request.args.get('page', 1, type=int)
    subject_id = request.args.get('subject_id', type=int)
    status = request.args.get('status', 'all')
    
    codes, total = list_invitation_codes(page=page, subject_id=subject_id, status=status)
    total_pages = max(1, (total + 20 - 1) // 20)
    
    subjects = get_all_subjects_admin()
    
    return render_template('admin/codes.html', codes=codes, page=page,
                          total_pages=total_pages, total=total,
                          subjects=subjects, current_subject=subject_id,
                          current_status=status,
                          current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@admin_bp.route('/codes/generate', methods=['POST'])
@admin_required
def admin_codes_generate():
    """批量生成邀请码"""
    subject_id = request.form.get('subject_id', type=int)
    days = request.form.get('days', 365, type=int)
    max_uses = request.form.get('max_uses', 1, type=int)
    count = request.form.get('count', 1, type=int)
    expires_days = request.form.get('expires_days', type=int)  # None = 永不过期
    
    if not subject_id:
        flash('请选择科目', 'error')
        return redirect(url_for('admin.admin_codes'))
    
    subject = get_subject_by_id(subject_id)
    if not subject:
        flash('科目不存在', 'error')
        return redirect(url_for('admin.admin_codes'))
    
    if count < 1 or count > 100:
        flash('生成数量必须在 1-100 之间', 'error')
        return redirect(url_for('admin.admin_codes'))
    
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = (datetime.now() + timedelta(days=expires_days)).strftime('%Y-%m-%d %H:%M:%S')
    
    max_uses_val = max_uses if max_uses > 0 else None
    created_by = session.get('user_id')
    
    generated = []
    for _ in range(count):
        code = generate_invitation_code()
        cid = create_invitation_code(code, subject_id, days, max_uses_val, expires_at, created_by)
        if cid:
            generated.append(code)
        else:
            # 如果重复则重试（极少发生）
            for _ in range(5):
                code = generate_invitation_code()
                cid = create_invitation_code(code, subject_id, days, max_uses_val, expires_at, created_by)
                if cid:
                    generated.append(code)
                    break
    
    if generated:
        flash(f'成功生成 {len(generated)} 个邀请码', 'success')
    else:
        flash('生成失败，请重试', 'error')
    
    # 返回 JSON 以便前端显示
    if request.headers.get('Accept') == 'application/json':
        return jsonify({
            'success': True,
            'codes': generated,
            'subject': subject['name'],
            'days': days,
        })
    
    return redirect(url_for('admin.admin_codes'))


@admin_bp.route('/codes/<int:code_id>/disable', methods=['POST'])
@admin_required
def admin_codes_disable(code_id):
    """禁用邀请码"""
    disable_invitation_code(code_id)
    flash('邀请码已禁用', 'success')
    return redirect(url_for('admin.admin_codes'))


@admin_bp.route('/codes/<int:code_id>/delete', methods=['POST'])
@admin_required
def admin_codes_delete(code_id):
    """删除邀请码"""
    delete_invitation_code(code_id)
    flash('邀请码已删除', 'success')
    return redirect(url_for('admin.admin_codes'))


@admin_bp.route('/codes/<int:code_id>/logs')
@admin_required
def admin_code_logs(code_id):
    """查看邀请码使用记录"""
    logs = get_code_usage_logs(code_id)
    code = get_invitation_code(code_id)
    # 通过 code_id 获取
    from models import list_invitation_codes
    codes, _ = list_invitation_codes(page=1, per_page=1000)
    for c in codes:
        if c['id'] == code_id:
            code = c
            break
    
    return render_template('admin/code_logs.html', logs=logs, code=code)


# ==================== 管理员重置用户密码 ====================

@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_reset_user_password(user_id):
    """管理员重置用户密码"""
    user = get_user_by_id(user_id)
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin.users'))
    
    # 生成随机密码
    import secrets
    import string
    new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
    reset_user_password(user_id, new_password)
    
    flash(f'用户 {user["username"]} 的密码已重置为: {new_password}', 'success')
    return redirect(url_for('admin.users'))


# ==================== 站点设置 ====================

from models import get_all_site_settings, batch_update_site_settings


@admin_bp.route('/settings')
@admin_required
def admin_settings():
    """站点设置页面"""
    settings = get_all_site_settings()
    return render_template('admin/settings.html', settings=settings)


@admin_bp.route('/settings/save', methods=['POST'])
@admin_required
def admin_settings_save():
    """保存站点设置"""
    settings = {}
    for key, value in request.form.items():
        if key.startswith('setting_'):
            settings[key[8:]] = value.strip()
    batch_update_site_settings(settings)
    flash('设置已保存', 'success')
    return redirect(url_for('admin.admin_settings'))


# ==================== 题目互动管理 ====================

from models import (
    list_feedbacks, resolve_feedback, dismiss_feedback, delete_feedback,
    list_comments, admin_delete_comment, get_feedback_stats, get_comment_stats,
)


@admin_bp.route('/feedbacks')
@admin_required
def admin_feedbacks():
    """纠错反馈管理"""
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'all')
    subject_id = request.args.get('subject_id', type=int)
    
    feedbacks, total = list_feedbacks(subject_id=subject_id, status=status, page=page)
    total_pages = max(1, (total + 20 - 1) // 20)
    stats = get_feedback_stats()
    
    subjects = get_all_subjects_admin()
    return render_template('admin/feedbacks.html', feedbacks=feedbacks,
                          page=page, total_pages=total_pages, total=total,
                          status=status, subjects=subjects, stats=stats)


@admin_bp.route('/feedbacks/<int:fid>/resolve', methods=['POST'])
@admin_required
def admin_resolve_feedback(fid):
    resolve_feedback(fid, session.get('user_id'))
    flash('已标记为已处理', 'success')
    return redirect(url_for('admin.admin_feedbacks'))


@admin_bp.route('/feedbacks/<int:fid>/dismiss', methods=['POST'])
@admin_required
def admin_dismiss_feedback(fid):
    dismiss_feedback(fid)
    flash('已忽略', 'success')
    return redirect(url_for('admin.admin_feedbacks'))


@admin_bp.route('/feedbacks/<int:fid>/delete', methods=['POST'])
@admin_required
def admin_delete_feedback(fid):
    delete_feedback(fid)
    flash('已删除', 'success')
    return redirect(url_for('admin.admin_feedbacks'))


@admin_bp.route('/comments-manage')
@admin_required
def admin_comments():
    """留言管理"""
    page = request.args.get('page', 1, type=int)
    subject_id = request.args.get('subject_id', type=int)
    
    comments, total = list_comments(subject_id=subject_id, page=page)
    total_pages = max(1, (total + 20 - 1) // 20)
    stats = get_comment_stats()
    
    subjects = get_all_subjects_admin()
    return render_template('admin/comments.html', comments=comments,
                          page=page, total_pages=total_pages, total=total,
                          subjects=subjects, stats=stats)


@admin_bp.route('/comments-manage/<int:cid>/delete', methods=['POST'])
@admin_required
def admin_delete_comment_page(cid):
    admin_delete_comment(cid)
    flash('已删除', 'success')
    return redirect(url_for('admin.admin_comments'))


# ==================== 笔记管理 ====================

from models import get_db


@admin_bp.route('/notes')
@admin_required
def admin_notes():
    """笔记管理"""
    page = request.args.get('page', 1, type=int)
    subject_id = request.args.get('subject_id', type=int)
    
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    where = "1=1"
    params = []
    if subject_id:
        where += ' AND n.subject_id = ?'
        params.append(subject_id)
    
    cur.execute(f"SELECT COUNT(*) FROM question_notes n WHERE {where}", params)
    total = cur.fetchone()[0]
    
    offset = (page - 1) * 20
    cur.execute(
        f"""SELECT n.*, u.username, s.name as subject_name
            FROM question_notes n
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN subjects s ON s.id = n.subject_id
            WHERE {where}
            ORDER BY n.updated_at DESC
            LIMIT 20 OFFSET ?""",
        params + [offset]
    )
    notes = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    total_pages = max(1, (total + 20 - 1) // 20)
    subjects = get_all_subjects_admin()
    
    return render_template('admin/notes.html', notes=notes,
                          page=page, total_pages=total_pages, total=total,
                          subjects=subjects, current_subject=subject_id)


@admin_bp.route('/notes/<int:nid>/delete', methods=['POST'])
@admin_required
def admin_delete_note(nid):
    """删除笔记"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM question_notes WHERE id = ?", (nid,))
    conn.commit()
    conn.close()
    flash('已删除', 'success')
    return redirect(url_for('admin.admin_notes'))
