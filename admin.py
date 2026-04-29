#!/usr/bin/env python3
"""
管理端路由：/admin/* 所有管理功能。
使用 Flask Blueprint 隔离。
"""
import json
import csv
import io
import sqlite3
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from auth import admin_required, login_required
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


@admin_bp.route('/questions/<int:qid>/edit', methods=['GET', 'POST'])
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


@admin_bp.route('/questions/<int:qid>/delete', methods=['POST'])
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
