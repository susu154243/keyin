#!/usr/bin/env python3
"""
刻印 (KeyIn) - 答题端（重构版 v0.6.0）
支持多科目、权限控制、分类练习。
"""
import os
import csv
import json
import re
import sqlite3
import random
import string
from datetime import datetime

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify, abort)
from urllib.parse import urlparse
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from models import (
    authenticate_user, get_user_by_id, get_user_subjects,
    get_question, get_user_wrong_questions, get_user_favorites,
    toggle_favorite, save_answer, get_all_subjects, get_leaf_categories,
    get_categories_tree, update_user_last_login,
    set_user_session_token, clear_user_session_token,
    get_questions_by_category,
    get_review_progress,
    update_review_schedule, get_review_schedule, is_question_mastered, is_question_reinforce, get_db,
    get_due_today, get_study_progress, infer_quality, get_question_attempt_stats,
    delete_review_schedule, reset_question_schedule, skip_review_interval,
    is_question_in_reinforce, exit_reinforce_mode,
    grant_user_license, revoke_user_license, check_user_license, get_user_licenses, get_all_licenses,
    get_stats_summary, get_daily_trend, get_heatmap_data,
    get_category_mastery, get_retention_curve,
    # 新增封装函数
    get_subject_by_id, get_questions_count, get_real_exam_count, get_exam_years,
    get_user_subject_accuracy, get_next_question_id, get_questions_by_year,
    is_question_favorite, get_question_count_by_category, get_question_position_in_category,
    get_random_questions as get_random_questions_model,
    get_sequential_questions as get_sequential_questions_model,
    get_questions_by_category as get_questions_by_category_model,
    get_unreviewed_questions, get_unreviewed_count, get_mastered_questions,
    hash_password, create_user, get_category, serialize_row,
    get_subject_category_stats,
    get_learning_cards,
    predict_review_result,
    # 邮箱验证
    # 邀请码注册
    validate_invitation_code, use_invitation_code, set_user_security, create_user,
    # 密码找回
    get_user_by_username, create_password_reset_token, verify_and_consume_reset_token,
    check_security_answer, reset_user_password, get_security_question_text,
    # 权限
    set_user_subject_permission,
)
from auth import login_required, get_current_user
from admin import admin_bp

# 题目互动功能：纠错/笔记/留言板
from models import (
    create_question_feedback, get_user_note, save_user_note,
    create_comment, get_question_comments, delete_comment,
)

app = Flask(__name__)
if not os.environ.get('SECRET_KEY'):
    import logging
    logging.getLogger('keyin').warning('未设置 SECRET_KEY 环境变量，每次重启后用户 session 将失效。请在 systemd service 中添加 Environment=SECRET_KEY=<固定值>。')
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'

# 速率限制（内存存储，单机足够）
# 关键：从 X-Forwarded-For 获取真实 IP（Nginx 反代后 remote_address 是 127.0.0.1）
def _get_real_ip():
    return request.headers.get('X-Real-IP', get_remote_address())

limiter = Limiter(
    app=app,
    key_func=_get_real_ip,
    storage_uri="memory://",
)

# 注册管理端 Blueprint
app.register_blueprint(admin_bp)


# ==================== 辅助函数 ====================


def _check_subject_permission(user, subject_id):
    """检查用户是否有科目权限。返回 True 或有权限的 subjects 列表。"""
    if user['role'] == 'admin':
        return True
    subjects = get_user_subjects(user['id'])
    allowed_ids = [s['id'] for s in subjects]
    if subject_id not in allowed_ids:
        return False
    return True


def _check_subject_license(f):
    """装饰器：检查用户是否有科目的有效授权（admin 角色直接放行）"""
    from functools import wraps
    @wraps(f)
    def decorated_function(subject_id, *args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
        if session.get('role') == 'admin':
            return f(subject_id, *args, **kwargs)
        license_info = check_user_license(user_id, subject_id)
        if not license_info['has_license'] or license_info['is_expired']:
            flash('您没有该科目的使用授权，请联系管理员', 'error')
            return redirect(url_for('index'))
        return f(subject_id, *args, **kwargs)
    return decorated_function



def parse_options(options_str):
    """解析选项字符串为字典 {A: 内容, B: 内容, ...}，清理首尾多余标签"""
    if not options_str:
        return {}
    if isinstance(options_str, dict):
        return options_str
    try:
        parsed = json.loads(options_str)
        if isinstance(parsed, dict):
            # 清理包裹选项内容的 <p> 标签和首尾 <br>
            cleaned = {}
            for key, val in parsed.items():
                if isinstance(val, str):
                    val = val.strip()
                    # 去除包裹的 <p>...</p>
                    if val.lower().startswith('<p>') and val.lower().endswith('</p>'):
                        val = val[3:-4].strip()
                    # 去除尾随 <br> / <br/> / <br />
                    while val.lower().endswith(('<br>', '<br/>', '<br />')):
                        val = val[:-4].rstrip() if val.lower().endswith('<br>') else val[:-5].rstrip()
                    # 去除开头 <br>
                    while val.lower().startswith(('<br>', '<br/>', '<br />')):
                        val = val[4:].lstrip() if val.lower().startswith('<br>') else val[5:].lstrip()
                cleaned[key] = val
            return cleaned
        # 数组格式：["内容A", "内容B", ...] -> {A: 内容A, B: 内容B, ...}
        if isinstance(parsed, list):
            labels = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            # Array of objects: [{"label": "A", "text": "..."}, ...]
            if parsed and isinstance(parsed[0], dict) and 'text' in parsed[0]:
                return {item['label']: item['text'] for item in parsed if 'label' in item and 'text' in item}
            # Simple array: ["内容A", "内容B", ...]
            return {labels[i]: parsed[i] for i in range(len(parsed)) if i < len(labels)}
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: 文本格式 "A. 内容\nB. 内容\nC. 内容\nD. 内容"
    lines = options_str.strip().split('\n')
    parsed_options = {}
    for line in lines:
        line = line.strip()
        match = re.match(r'^([A-F])\.[\s]*(.*)', line, re.DOTALL)
        if match:
            key = match.group(1)
            val = match.group(2).strip()
            # 清理尾随的「问题2：」等合并题标记（不属于当前选项）
            val = re.sub(r'[\s\n]*问题\d+[：:].*$', '', val, flags=re.DOTALL)
            parsed_options[key] = val
    if parsed_options:
        return parsed_options

    return {}


@app.context_processor
def inject_site_settings():
    from models import get_all_site_settings as _get_settings, get_unread_notification_count
    settings = _get_settings()
    result = {
        'icp_filing': settings.get('icp_filing', ''),
        'police_filing': settings.get('police_filing', ''),
    }
    # 注入未读通知数（登录用户）
    if 'user_id' in session:
        result['notif_unread_count'] = get_unread_notification_count(session['user_id'])
    else:
        result['notif_unread_count'] = 0
    return result


# ==================== 认证路由 ====================

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 15 minutes")
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate_user(username, password)
        if user:
            # 清除旧 session 数据，防止切换账号后数据泄漏
            session.pop('practice', None)
            session.pop('chapter_progress', None)
            session.pop('reinforce', None)
            import secrets
            token = secrets.token_hex(32)
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['session_token'] = token
            update_user_last_login(user['id'])
            set_user_session_token(user['id'], token)
            next_url = request.args.get('next', url_for('index'))
            parsed = urlparse(next_url)
            if parsed.netloc:
                next_url = url_for('index')
            return redirect(next_url)
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def register():
    if request.method == 'POST':
        import re
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        phone = request.form.get('phone', '').strip()
        invitation_code = request.form.get('invitation_code', '').strip().upper()
        security_question = request.form.get('security_question', '').strip()
        security_answer = request.form.get('security_answer', '').strip()
        
        # 表单验证
        if not username or not password:
            flash('用户名和密码不能为空', 'error')
        elif password != confirm_password:
            flash('两次输入的密码不一致', 'error')
        elif len(username) < 3:
            flash('用户名至少需要3个字符', 'error')
        elif len(password) < 8:
            flash('密码至少需要8个字符', 'error')
        elif not security_question:
            flash('请选择一个安全问题', 'error')
        elif not security_answer:
            flash('请填写安全答案', 'error')
        elif not phone:
            flash('请输入手机号码', 'error')
        elif not re.match(r'^1[3-9]\d{9}$', phone):
            flash('请输入正确的11位手机号', 'error')
        elif not invitation_code:
            flash('请输入邀请码', 'error')
        else:
            # 验证邀请码
            valid, msg, code_record = validate_invitation_code(invitation_code)
            if not valid:
                flash(f'邀请码无效：{msg}', 'error')
            else:
                # 创建用户
                result = create_user(username, password, 'user', email=None, phone=phone)
                if result:
                    # 设置安全问题
                    set_user_security(result, security_question, security_answer)
                    # 通过邀请码授权对应科目（有效期）
                    grant_user_license(result, subject_id=code_record['subject_id'], days=code_record['days'])
                    # 授予科目访问权限（让首页能看到该科目）
                    set_user_subject_permission(result, code_record['subject_id'], can_practice=1, can_mock=1, can_daily=1, can_manage=0)
                    # 标记邀请码已使用
                    use_invitation_code(code_record['id'], result)
                    flash('注册成功！请登录', 'success')
                    return redirect(url_for('login'))
                else:
                    flash('用户名已存在', 'error')
    return render_template('register.html')


@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    if user_id:
        clear_user_session_token(user_id)
    session.clear()
    return redirect(url_for('login'))


# ==================== 密码找回路由 ====================

@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def forgot_password():
    """忘记密码：两步验证——先输入用户名 → 显示安全问题 → 再填写答案"""
    step = request.args.get('step', '1')
    username = request.args.get('username', '')
    
    if request.method == 'POST':
        if step == '1':
            # 第一步：用户输入用户名，查询并展示安全问题
            username = request.form.get('username', '').strip()
            if not username:
                flash('请输入用户名', 'error')
            else:
                user = get_user_by_username(username)
                if not user:
                    flash('用户不存在', 'error')
                elif not user.get('security_question'):
                    flash('该用户未设置安全问题，请联系管理员重置密码', 'error')
                else:
                    # 找到用户，进入第二步
                    return redirect(url_for('forgot_password', step='2', username=username))
        else:
            # 第二步：用户回答安全问题
            security_answer = request.form.get('security_answer', '').strip()
            if not security_answer:
                flash('请填写安全答案', 'error')
            else:
                user = get_user_by_username(username)
                if not user:
                    flash('用户不存在', 'error')
                elif check_security_answer(user['id'], user['security_question'], security_answer):
                    # 答案正确，生成重置 token 并跳转到重置页面
                    token = create_password_reset_token(user['id'])
                    return redirect(url_for('reset_password_page', token=token))
                else:
                    flash('安全答案不正确', 'error')
    
    # 渲染页面
    security_question = None
    if step == '2' and username:
        user = get_user_by_username(username)
        if user:
            security_question = get_security_question_text(user['security_question'])
    
    return render_template('forgot_password.html', step=step, username=username, security_question=security_question)


@app.route('/reset-password', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def reset_password_page():
    """密码重置页面（通过 token 访问）"""
    token = request.args.get('token', '')
    if request.method == 'POST':
        token = request.form.get('token', '')
    
    if not token:
        flash('重置链接无效', 'error')
        return redirect(url_for('forgot_password'))
    
    user_id = verify_and_consume_reset_token(token)
    if not user_id:
        flash('重置链接已过期或无效，请重新申请', 'error')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not new_password or not confirm_password:
            flash('请输入新密码', 'error')
        elif new_password != confirm_password:
            flash('两次输入的密码不一致', 'error')
        elif len(new_password) < 8:
            flash('密码至少需要8个字符', 'error')
        else:
            reset_user_password(user_id, new_password)
            flash('密码重置成功！请登录', 'success')
            return redirect(url_for('login'))
    
    return render_template('reset_password.html')


@app.route('/robots.txt')
def robots_txt():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'robots.txt')


# ==================== 首页（科目选择） ====================

@app.route('/')
@login_required
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    # 所有用户都显示全部启用科目，标记授权状态
    subjects = get_all_subjects()

    # 获取用户有权限的科目 ID 集合
    if user['role'] == 'admin':
        allowed_ids = set(s['id'] for s in subjects)
    else:
        user_subs = get_user_subjects(user['id'])
        allowed_ids = set(s['id'] for s in user_subs)

    # 获取每个科目的授权信息
    subjects_with_license = []
    for s in subjects:
        sid = s['id']
        s_dict = dict(s)
        s_dict['has_permission'] = sid in allowed_ids
        if s_dict['has_permission']:
            if user['role'] == 'admin':
                s_dict['license_info'] = None  # admin不显示授权状态
            else:
                license_info = check_user_license(user['id'], sid)
                s_dict['license_info'] = license_info
        else:
            s_dict['license_info'] = None
        # 获取 level，默认 2（中级）
        s_dict['level'] = s_dict.get('level', 2) or 2
        subjects_with_license.append(s_dict)

    # 按授权 + 级别分组
    authorized = [s for s in subjects_with_license if s['has_permission']]
    not_authorized = [s for s in subjects_with_license if not s['has_permission']]
    
    advanced = [s for s in not_authorized if s['level'] == 1]
    intermediate = [s for s in not_authorized if s['level'] == 2]
    primary = [s for s in not_authorized if s['level'] == 3]

    # 获取站点设置（首页欢迎语）
    from models import get_all_site_settings
    site_settings = get_all_site_settings()

    return render_template('index.html',
                          subjects=authorized,
                          advanced=advanced,
                          intermediate=intermediate,
                          primary=primary,
                          current_year=datetime.now().year,
                          home_guide=site_settings.get('home_guide', ''),
                          home_update=site_settings.get('home_update', ''),
                          welcome_title=site_settings.get('welcome_title', '欢迎使用 刻印'),
                          welcome_subtitle=site_settings.get('welcome_subtitle', '高效的在线刷题系统，助力学习提升'))


# ==================== 科目详情页 ====================

@app.route('/subjects/<int:subject_id>')
@login_required
def subject_detail(subject_id):
    user = get_current_user()
    
    # 检查权限
    if user['role'] != 'admin':
        subjects = get_user_subjects(user['id'])
        allowed_ids = [s['id'] for s in subjects]
        if subject_id not in allowed_ids:
            flash('您没有访问该科目的权限', 'error')
            return redirect(url_for('index'))
    else:
        subjects = []
    
    subject = get_subject_by_id(subject_id)
    if not subject:
        abort(404)
    
    total_questions = get_questions_count(subject_id)
    real_exam_count = get_real_exam_count(subject_id)
    years = get_exam_years(subject_id)
    overall_accuracy = get_user_subject_accuracy(session['user_id'], subject_id)

    perms = None
    if user['role'] != 'admin':
        for s in subjects:
            if s['id'] == subject_id:
                perms = s
                break

    return render_template('subject_detail.html',
                          subject=subject,
                          total_questions=total_questions,
                          real_exam_count=real_exam_count,
                          overall_accuracy=overall_accuracy,
                          years=years,
                          perms=perms,
                          current_year=datetime.now().year)


# ==================== 答题路由 ====================

def get_random_questions(subject_id, category_id=None, count=10):
    """随机获取题目（使用 models.py 封装）"""
    rows = get_random_questions_model(subject_id, category_id, count)
    questions = []
    for r in rows:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        questions.append(q)
    return questions


def get_sequential_questions(subject_id, category_id=None):
    """顺序获取题目（使用 models.py 封装）"""
    rows = get_sequential_questions_model(subject_id, category_id)
    return [serialize_row(r) for r in rows]


@app.route('/subjects/<int:subject_id>/practice')
@login_required
def practice(subject_id):
    """章节练习 - 选择分类"""
    user = get_current_user()
    subject = get_subject_by_id(subject_id)
    category_data = get_subject_category_stats(user['id'], subject_id)
    tree = category_data['tree']
    subject_total = category_data.get('subject_total', {})
    # 过滤历年真题（真题在历史真题入口，不在练习中重复出现）
    tree = [t for t in tree if t.get('name') != '历年真题']
    return render_template('practice.html',
                          subject=subject,
                          tree=tree,
                          subject_total=subject_total)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>')
@login_required
@_check_subject_license
def practice_category(subject_id, category_id):
    """按分类答题入口：重定向到学习设置页"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)

    return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))


# ==================== 章节练习：模式选择 + 考试/练习模式 ====================

def _next_review_label(next_review_str):
    """根据 next_review 字符串生成易读标签"""
    from datetime import datetime as dt
    now = dt.now()
    nr = dt.strptime(next_review_str, '%Y-%m-%d %H:%M:%S')
    diff = nr - now
    days = diff.days
    seconds = diff.seconds
    
    if days < 0:
        return '已过期'
    elif days == 0:
        hours = seconds // 3600
        if hours < 1:
            mins = seconds // 60
            return f'{mins}分钟后可复习' if mins > 0 else '可复习'
        return f'{hours}小时后可复习'
    elif days == 1:
        return '明天可复习'
    else:
        return f'{days}天后可复习'


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/setup')
@login_required
@_check_subject_license
def study_setup(subject_id, category_id):
    """学习设置页：今日复习 + 进度展示 + 模式选择"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    subject = get_subject_by_id(subject_id)
    user_id = session['user_id']

    # 授权检查（admin 直接跳过）
    if session.get('role') != 'admin':
        license_info = check_user_license(user_id, subject_id)
        if not license_info['has_license'] or license_info['is_expired']:
            flash('您没有该科目的使用授权，请联系管理员', 'error')
            return redirect(url_for('index'))

    # 学习进度统计
    progress = get_study_progress(user_id, category_id)
    # 新题数量
    progress['unreviewed'] = get_unreviewed_count(user_id, category_id)
    # 已掌握列表
    mastered_list = get_mastered_questions(user_id, category_id)
    progress['mastered_count'] = len(mastered_list)

    # 今日待复习题目列表
    due_today_list = get_due_today(user_id, category_id)
    # 为每题添加推断评分
    due_today_ids = set()
    for d in due_today_list:
        d['inferred_quality'] = infer_quality(d)
        due_today_ids.add(d['id'])
    
    # 学习/重学中的题目（倒计时）
    learning_cards = get_learning_cards(user_id, category_id)

    # 获取今日复习已完成数（已在本会话中回答的）
    answered_today = set()
    p = session.get('practice', {})
    if p.get('category_id') == category_id:
        answered_today = set(p.get('answered', {}).keys())

    # 做题次数统计
    attempt_stats = get_question_attempt_stats(user_id, category_id)

    # 题目列表：全部分类题目 + 复习记录 + 做题统计
    from datetime import date as date_mod, timedelta
    today_str = date_mod.today().strftime('%Y-%m-%d')
    tomorrow_str = (date_mod.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    due_map = {d['id']: d for d in due_today_list}
    due_map_ids = due_map.keys()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id,
               rs.card_state,
               rs.next_review,
               rs.stability,
               rs.difficulty,
               rs.repetitions,
               rs.interval,
               rs.learning_step,
               rs.ease_factor
        FROM questions q
        LEFT JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.category_id = ?
    """, (user_id, category_id))
    all_questions = cur.fetchall()

    # 做题统计快速查找
    attempt_map = {s['id']: dict(s) for s in attempt_stats}

    review_items = []
    for q in all_questions:
        qid = q['id']
        cs = q['card_state']
        nr = q['next_review']
        stability = q['stability'] or 0
        repetitions = q['repetitions'] or 0

        # 计算新状态（优先级：已掌握 > 未学习 > 待复习 > 即将复习 > 学习中）
        if stability >= 45 and repetitions >= 3:
            filter_state = 'mastered'
        elif cs is None:
            filter_state = 'unlearned'
        elif nr:
            nr_date = nr[:10]  # 'YYYY-MM-DD'
            if nr_date <= today_str:
                filter_state = 'due'
            elif nr_date <= tomorrow_str:
                filter_state = 'soon'
            else:
                filter_state = 'learning'
        else:
            filter_state = 'learning'

        stats = attempt_map.get(qid, {})
        item = {
            'id': qid,
            'card_state': cs or '',
            'next_review': nr or '',
            'next_review_label': _next_review_label(nr) if nr else '-',
            'stability': stability,
            'difficulty': q['difficulty'],
            'repetitions': repetitions,
            'interval': q['interval'],
            'learning_step': q['learning_step'] or 0,
            'ease_factor': q['ease_factor'],
            'is_due_today': qid in due_map_ids,
            'filter_state': filter_state,
            'attempt_count': stats.get('attempt_count', 0),
            'accuracy': stats.get('accuracy', 0),
            'last_quality': stats.get('last_quality'),
            'inferred_quality': stats.get('inferred_quality') or due_map.get(qid, {}).get('inferred_quality'),
        }
        review_items.append(item)

    # 按题目 ID 序号排序
    def _sort_key(item):
        qid = item.get('id', '')
        try:
            return int(qid.rsplit('-', 1)[-1])
        except (ValueError, IndexError):
            return 9999
    review_items.sort(key=_sort_key)

    # 授权状态
    license_info = check_user_license(user_id, subject_id)

    # 检查是否有保存的练习进度
    from models import load_practice_session
    saved = load_practice_session(user_id, category_id)
    has_saved_progress = saved is not None and saved.get('queue')

    return render_template('study_setup.html',
                          subject=subject, category=cat,
                          progress=progress,
                          mastered_list=mastered_list,
                          due_today_list=due_today_list,
                          answered_today=answered_today,
                          attempt_stats=attempt_stats,
                          review_items=review_items,
                          learning_cards=learning_cards,
                          license_info=license_info)


# 旧路由兼容：重定向到新学习设置页
@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/setup')
@login_required
def practice_setup_redirect(subject_id, category_id):
    return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/today')
@login_required
def study_today_review(subject_id, category_id):
    """今日复习入口：只复习今日到期的题目"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    
    due_today_list = get_due_today(session['user_id'], category_id)
    if not due_today_list:
        flash('🎉 今日没有需要复习的题目！', 'success')
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))

    # 初始化会话队列（仅今日待复习题目）
    session['practice'] = {
        'category_id': category_id,
        'subject_id': subject_id,
        'queue': [d['id'] for d in due_today_list],
        'retry_count': {},
        'answered_correct_first': 0,
        'answered_wrong': 0,
        'stubborn': [],
        'total_attempts': 0,
        'initial_count': len(due_today_list),
        'is_today_review': True,
    }
    return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))


def _get_chapter_questions(subject_id, category_id, user_id, count=None):
    """获取章节练习题目列表（仅新题），返回 (dict_list, raw_list)"""
    rows = get_unreviewed_questions(user_id, category_id)
    if count and count > 0 and count < len(rows):
        rows = rows[:count]
    result = []
    for r in rows:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        result.append(q)
    return result, rows


def _get_all_chapter_questions(category_id, count=None):
    """获取分类下全部题目（考试模式用），返回 (dict_list, raw_list)"""
    rows = get_questions_by_category(category_id)
    if count and count > 0 and count < len(rows):
        rows = rows[:count]
    result = []
    for r in rows:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        result.append(q)
    return result, rows


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/exam')
@login_required
@_check_subject_license
def chapter_exam(subject_id, category_id):
    """考试模式：显示分类全部题目"""
    user = get_current_user()
    if not _check_subject_permission(user, subject_id):
        flash('您没有访问该科目的权限', 'error')
        return redirect(url_for('index'))
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    count = request.args.get('count', type=int)
    questions, _ = _get_all_chapter_questions(category_id, count=count)
    random.shuffle(questions)  # 打乱顺序
    if not questions:
        flash('该分类下暂无题目', 'info')
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))
    subject = get_subject_by_id(subject_id)
    return render_template('chapter_exam.html', questions=questions, subject=subject, category=cat)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/exam/submit', methods=['POST'])
@login_required
def chapter_exam_submit(subject_id, category_id):
    """提交考试模式试卷"""
    user = get_current_user()
    if not _check_subject_permission(user, subject_id):
        return jsonify({'success': False, 'error': '无权限'}), 403
    count = request.args.get('count', type=int)
    _, raw = _get_all_chapter_questions(category_id, count=count)
    questions = []
    for r in raw:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        questions.append(q)

    correct_count = 0
    total = len(questions)
    details = []
    user_id = session['user_id']
    for q in questions:
        user_answer = request.form.get(f'answer_{q["id"]}', '')
        if q['qtype_text'] == 'multiple':
            is_correct = set(user_answer) == set(q['answer'])
        else:
            is_correct = user_answer == q['answer']
        if is_correct:
            correct_count += 1
        save_answer(user_id, q['id'], user_answer, 1 if is_correct else 0, subject_id, source='exam')
        # 考试模式不进入复习队列，仅保留答题记录
        details.append({
            'id': q['id'],
            'stem': q['stem'],
            'options': q['options'],
            'correct_answer': q['answer'],
            'user_answer': user_answer,
            'is_correct': is_correct,
            'explanation': q.get('explanation', ''),
        })

    score = round((correct_count / total * 100), 2) if total > 0 else 0
    # 保存考试记录（仅成绩，不进入复习队列）
    from models import save_exam_record
    save_exam_record(user_id, subject_id, category_id, total, correct_count, score)
    return jsonify({
        'success': True,
        'correct_count': correct_count,
        'total': total,
        'score': score,
        'details': details,
    })


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice')
@login_required
@_check_subject_license
def chapter_practice_start(subject_id, category_id):
    """练习模式起始：初始化队列到 session"""
    user = get_current_user()
    if not _check_subject_permission(user, subject_id):
        flash('您没有访问该科目的权限', 'error')
        return redirect(url_for('index'))
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    
    count = request.args.get('count', type=int)
    questions, _ = _get_chapter_questions(subject_id, category_id, session['user_id'], count=count)
    if not questions:
        flash('该分类下暂无新题，请进入复习模式', 'info')
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))

    # 初始化会话队列
    session['practice'] = {
        'category_id': category_id,
        'subject_id': subject_id,
        'queue': [q['id'] for q in questions],       # 当前待做队列
        'retry_count': {},                            # 每道题重试次数
        'answered_correct_first': 0,                  # 首次答对数
        'answered_wrong': 0,                          # 答错数
        'stubborn': [],                               # 3次都错的题
        'total_attempts': 0,                          # 总答题次数（含重试）
        'initial_count': len(questions),              # 初始题量
    }
    return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/next')
@login_required
@_check_subject_license
def chapter_practice_next(subject_id, category_id):
    """练习模式：从队列取下一题"""
    p = session.get('practice', {})
    if not p:
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))

    # 补建复习计划：答完题后未评分直接点"下一题"时，自动创建复习计划
    last_qid = p.get('last_answered_qid')
    if last_qid:
        from models import get_review_schedule
        rs = get_review_schedule(session['user_id'], last_qid)
        if not rs:
            # 该题已答但未创建复习计划，自动补建
            a = p.get('answered', {}).get(last_qid, {})
            quality = 2 if a.get('is_correct') else 0
            update_review_schedule(session['user_id'], last_qid, subject_id, quality)
        p['last_answered_qid'] = None
        session['practice'] = p

    queue = p.get('queue', [])
    if not queue:
        # 队列为空，显示总结页
        return _render_practice_summary(subject_id, category_id)

    qid = queue[0]
    return redirect(url_for('chapter_practice_qid', subject_id=subject_id, category_id=category_id, qid=qid))


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>')
@login_required
def chapter_practice_qid(subject_id, category_id, qid):
    """练习模式：显示指定题目（P0-2: 校验队列）"""
    question = get_question(qid)
    if not question or question['subject_id'] != subject_id:
        abort(404)
    question = dict(question)
    question['options'] = parse_options(question['options'])

    subject = get_subject_by_id(subject_id)
    cat = get_category(category_id)
    p = session.get('practice', {})
    queue = p.get('queue', [])
    initial_count = p.get('initial_count', 0)

    # P0-2: 防止直接URL绕过队列
    if qid not in queue:
        flash('该题目不在当前练习队列中', 'warning')
        return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))

    # 计算当前题在队列中的位置
    try:
        idx = queue.index(qid) + 1
    except ValueError:
        idx = 1

    # 检查是否已经答过（有结果）
    is_answered = qid in p.get('answered', {})
    answer_data = p.get('answered', {}).get(qid, {})
    retry_count = p.get('retry_count', {}).get(qid, 0)

    # P1-4: 进度数据（基于已答题数，而非队列减少）
    answered_count = len(p.get('answered', {}))
    answered_unique = set(p.get('answered', {}).keys()) - set(p.get('stubborn', []))
    remaining = initial_count - len(answered_unique)

    # 预测各评分的调度结果
    review_predictions = predict_review_result(session['user_id'], qid, subject_id)
    # 强化标记
    is_reinforce = is_question_reinforce(session['user_id'], qid)

    is_study_card = not question.get('options') or question['options'] == '{}'
    return render_template('chapter_practice.html',
                          question=question,
                          subject=subject,
                          category=cat,
                          queue=queue,
                          queue_position=idx,
                          total_count=initial_count,
                          is_answered=is_answered,
                          answer_data=answer_data,
                          retry_count=retry_count,
                          retry_counts=p.get('retry_count', {}),
                          answered_count=answered_count,
                          remaining=remaining if remaining > 0 else 0,
                          completed_count=len(answered_unique),
                          review_predictions=review_predictions,
                          is_reinforce=is_reinforce,
                          is_study_card=is_study_card)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>/answer', methods=['POST'])
@login_required
def chapter_practice_answer(subject_id, category_id, qid):
    """练习模式：提交答案"""
    question = get_question(qid)
    if not question:
        abort(404)
    question = dict(question)
    correct_answer = question['answer']

    # 学习卡片模式（案例分析/论文题，无选项）：跳过判断对错
    options_dict = parse_options(question['options'])
    is_study_card = not options_dict

    if is_study_card:
        is_correct = True
        is_partial = False
        result_msg = ''  # 学习卡片不需要"回答正确/错误"提示
        user_answer = ''
    elif question['qtype_text'] == 'multiple':
        user_answer = ','.join(request.form.getlist('answer'))
        is_correct = set(user_answer) == set(correct_answer)
        # P2-10: 部分正确判定
        correct_set = set(correct_answer)
        user_set = set(user_answer)
        if user_set and correct_set:
            overlap = len(user_set & correct_set)
            partial_ratio = overlap / len(correct_set)
            is_partial = (not is_correct) and partial_ratio > 0
        else:
            is_partial = False
        save_answer(session['user_id'], qid, user_answer, 1 if is_correct else 0, subject_id)
        if is_correct:
            result_msg = '回答正确！'
        elif is_partial:
            result_msg = f'部分正确。正确答案是：{correct_answer}'
        else:
            result_msg = f'回答错误。正确答案是：{correct_answer}'
    else:
        user_answer = request.form.get('answer', '')
        if not user_answer:
            # 未选择答案，不允许提交
            result_msg = '请先选择一个答案再提交'
            queue = p.get('queue', [])
            return render_template('practice.html',
                                  question=question,
                                  options=question['options'],
                                  queue=queue,
                                  current_qid=qid,
                                  result_msg=result_msg,
                                  subject_id=subject_id,
                                  category_id=category_id,
                                  progress=p.get('progress', {}),
                                  practice_stats=p.get('practice_stats', {}))
        is_correct = user_answer == correct_answer
        is_partial = False
        save_answer(session['user_id'], qid, user_answer, 1 if is_correct else 0, subject_id)
        if is_correct:
            result_msg = '回答正确！'
        else:
            result_msg = f'回答错误。正确答案是：{correct_answer}'

    # 更新会话统计
    p = session.get('practice', {})
    p['total_attempts'] = p.get('total_attempts', 0) + 1
    # 记录当前答题 qid，用于后续自动补建复习计划
    p['last_answered_qid'] = qid

    answered = p.setdefault('answered', {})
    answered[qid] = {
        'user_answer': user_answer,
        'is_correct': is_correct,
        'is_partial': is_partial,
        'result_msg': result_msg,
    }

    # 首次答对/答错统计
    if is_correct:
        if p.get('retry_count', {}).get(qid, 0) == 0:
            p['answered_correct_first'] = p.get('answered_correct_first', 0) + 1
        # 答对：从队列头部弹出（防止用户跳过评分直接点下一题导致卡住）
        queue = p.get('queue', [])
        if queue and queue[0] == qid:
            queue.pop(0)
    else:
        p['answered_wrong'] = p.get('answered_wrong', 0) + 1
        retry = p.get('retry_count', {}).get(qid, 0)
        max_retries = 5  # "忘了"最多5次重试
        if retry < max_retries:
            # 放回队尾
            p['retry_count'][qid] = retry + 1
            if qid in p['queue']:
                p['queue'].remove(qid)
            p['queue'].append(qid)
            # 清除已答题记录，下次加载视为新题
            p['answered'].pop(qid, None)
        else:
            # 3次重试仍然错，移入顽固题
            if qid in p['queue']:
                p['queue'].remove(qid)
            p.setdefault('stubborn', []).append(qid)

    session['practice'] = p
    question['options'] = parse_options(question['options'])

    cat = get_category(category_id)
    subject = get_subject_by_id(subject_id)
    queue = p.get('queue', [])
    try:
        queue_pos = queue.index(qid) + 1
    except ValueError:
        queue_pos = 1

    # For template: split user_answer into list for `in` check
    user_answer_list = list(user_answer) if user_answer else []
    review_predictions = predict_review_result(session['user_id'], qid, subject_id)
    is_reinforce = is_question_reinforce(session['user_id'], qid)
    is_study_card = not question.get('options')
    is_answered = bool(result_msg) or is_study_card
    return render_template('chapter_practice.html',
                          question=question,
                          is_study_card=is_study_card,
                          user_answer=user_answer,
                          user_answer_list=user_answer_list,
                          result_msg=result_msg,
                          is_correct=is_correct,
                          is_partial=answered.get(qid, {}).get('is_partial', False),
                          subject=subject,
                          category=cat,
                          queue=queue,
                          queue_position=queue_pos,
                          total_count=p.get('initial_count', 0),
                          is_answered=True,
                          answer_data=answered.get(qid, {}),
                          retry_count=p.get('retry_count', {}).get(qid, 0),
                          retry_counts=p.get('retry_count', {}),
                          answered_count=len(answered),
                          remaining=p.get('initial_count', 0) - len(set(answered.keys()) - set(p.get('stubborn', []))),
                          completed_count=len(set(answered.keys()) - set(p.get('stubborn', []))),
                          review_predictions=review_predictions,
                          is_reinforce=is_reinforce)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>/rate', methods=['POST'])
@login_required
def chapter_practice_rate(subject_id, category_id, qid):
    """练习模式：FSRS 评分 + 队列调度"""
    quality = request.form.get('quality', type=int)
    if quality is None:
        return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))

    # 更新 FSRS 复习计划
    update_review_schedule(session['user_id'], qid, subject_id, quality)

    # 根据评分调整队列
    p = session.get('practice', {})
    queue = p.get('queue', [])
    retry = p.get('retry_count', {}).get(qid, 0)

    if quality == 0:  # 忘了
        # 已在 answer 中处理：放回队尾，最多5次重试
        pass
    elif quality == 1:  # 模糊
        # 重做1次（如果还没重做过）
        if retry == 0 and qid in queue:
            queue.remove(qid)
            queue.append(qid)
            p['retry_count'][qid] = 1
    elif quality >= 2:  # 一般/简单/秒答
        # 移出队列
        if qid in queue:
            queue.remove(qid)

    # 移除当前题（如果还在队列头部）
    # 答错时 answer handler 已将其移到队尾，rate 不应再 pop 队首（否则误弹其他题目）
    if queue and queue[0] == qid:
        queue.pop(0)

    session['practice'] = p
    return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>/skip', methods=['POST'])
@login_required
def chapter_practice_skip(subject_id, category_id, qid):
    """练习模式：跳过评分，自动映射（含部分正确处理 P2-10）"""
    p = session.get('practice', {})
    answered = p.get('answered', {}).get(qid, {})
    is_correct = answered.get('is_correct', False)
    is_partial = answered.get('is_partial', False)
    retry = p.get('retry_count', {}).get(qid, 0)

    # 自动映射评分（含部分正确）
    if is_correct:
        quality = 2 if retry > 0 else 3
    elif is_partial:
        quality = 1  # 部分正确 → 模糊
    else:
        quality = 0  # 全错 → 忘了

    update_review_schedule(session['user_id'], qid, subject_id, quality)

    # 队列调度：答对（quality>=2）移出队列
    # 答错（quality 0/1）的队列调度已在 answer 路由处理，skip 不再重复
    queue = p.get('queue', [])
    if quality >= 2:
        if qid in queue:
            queue.remove(qid)

    # 移除当前题（如果还在队列头部）
    # 答错时 answer handler 已将其移到队尾，skip 不应再 pop 队首（否则误弹其他题目）
    if queue and queue[0] == qid:
        queue.pop(0)

    session['practice'] = p
    return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))


def _render_practice_summary(subject_id, category_id):
    """渲染练习总结页"""
    p = session.get('practice', {})
    session.pop('practice', None)  # 清除会话

    subject = get_subject_by_id(subject_id)
    cat = get_category(category_id)

    total_attempts = p.get('total_attempts', 0)
    initial_count = p.get('initial_count', 0)
    first_correct = p.get('answered_correct_first', 0)
    answered_wrong = p.get('answered_wrong', 0)
    stubborn = p.get('stubborn', [])

    # 下次复习安排
    from datetime import datetime
    now = datetime.now()
    review_groups = {'明天': 0, '2天后': 0, '4天后': 0, '7天后': 0, '15天后': 0, '30天后': 0}
    user_id = session['user_id']
    for qid in p.get('answered', {}).keys():
        rs = get_review_schedule(user_id, qid)
        if rs:
            next_rev = datetime.strptime(rs['next_review'], '%Y-%m-%d %H:%M:%S')
            diff = (next_rev - now).days
            if diff <= 1:
                review_groups['明天'] += 1
            elif diff <= 2:
                review_groups['2天后'] += 1
            elif diff <= 4:
                review_groups['4天后'] += 1
            elif diff <= 7:
                review_groups['7天后'] += 1
            elif diff <= 15:
                review_groups['15天后'] += 1
            else:
                review_groups['30天后'] += 1

    first_rate = round(first_correct / initial_count * 100, 1) if initial_count > 0 else 0

    return render_template('chapter_practice_summary.html',
                          subject=subject,
                          category=cat,
                          total_attempts=total_attempts,
                          initial_count=initial_count,
                          first_correct=first_correct,
                          first_rate=first_rate,
                          answered_wrong=answered_wrong,
                          stubborn=stubborn,
                          review_groups=review_groups)


@app.route('/subjects/<int:subject_id>/random')
@login_required
def random_question(subject_id):
    """随机答题"""
    questions = get_random_questions(subject_id, count=1)
    if not questions:
        flash('暂无可答题目', 'info')
        return redirect(url_for('subject_detail', subject_id=subject_id))
    return redirect(url_for('show_question', subject_id=subject_id, qid=questions[0]['id']))


@app.route('/subjects/<int:subject_id>/question/<qid>')
@login_required
def show_question(subject_id, qid):
    """显示题目"""
    question = get_question(qid)
    if not question or question['subject_id'] != subject_id:
        abort(404)
    
    question = dict(question)
    question['options'] = parse_options(question['options'])
    
    is_favorite = is_question_favorite(session['user_id'], qid)
    next_qid = get_next_question_id(subject_id, qid)
    
    category_id = request.args.get('category_id', type=int)
    total = None
    answered = None
    review_progress = None
    
    if category_id:
        total = get_question_count_by_category(category_id)
        answered = get_question_position_in_category(category_id, qid)
        review_progress = get_review_progress(session['user_id'], category_id=category_id)
    
    return render_template('question.html',
                          question=question,
                          is_favorite=is_favorite,
                          next_qid=next_qid,
                          total=total,
                          answered=answered,
                          subject_id=subject_id,
                          category_id=category_id,
                          review_progress=review_progress,
                          current_year=datetime.now().year)


@app.route('/subjects/<int:subject_id>/question/<qid>', methods=['POST'])
@login_required
def submit_answer(subject_id, qid):
    """提交答案"""
    question = get_question(qid)
    if not question:
        abort(404)
    
    question = dict(question)
    correct_answer = question['answer']
    
    # 获取用户答案：多选题用 getlist，单选题用 get
    if question['qtype_text'] == 'multiple':
        user_answer = ','.join(request.form.getlist('answer'))
    else:
        user_answer = request.form.get('answer', '')
    
    # 未选择答案，不允许提交
    if not user_answer:
        flash('请先选择一个答案再提交', 'warning')
        return redirect(url_for('show_question', subject_id=subject_id, qid=qid))
    
    # 判断是否正确（仅计算一次）
    if question['qtype_text'] == 'multiple':
        is_correct = set(user_answer) == set(correct_answer)
    else:
        is_correct = user_answer == correct_answer
    
    save_answer(session['user_id'], qid, user_answer, 1 if is_correct else 0, subject_id)
    
    result_msg = '回答正确！' if is_correct else f'回答错误。正确答案是：{correct_answer}'
    
    next_qid = get_next_question_id(subject_id, qid)
    category_id = request.args.get('category_id', type=int)
    
    question['options'] = parse_options(question['options'])
    
    return render_template('question.html',
                          question=question,
                          user_answer=user_answer,
                          result_msg=result_msg,
                          next_qid=next_qid,
                          subject_id=subject_id,
                          category_id=category_id,
                          current_year=datetime.now().year)


@app.route('/subjects/<int:subject_id>/rate/<qid>', methods=['POST'])
@login_required
def rate_question(subject_id, qid):
    """FSRS 评分：答完题后评分"""
    category_id = request.form.get('category_id', type=int)
    quality = request.form.get('quality', 3, type=int)
    
    update_review_schedule(session['user_id'], qid, subject_id, quality)
    
    next_qid = get_next_question_id(subject_id, qid)
    
    if next_qid:
        return redirect(url_for('show_question', subject_id=subject_id, qid=next_qid))
    else:
        flash('🎉 本分类题目已全部完成！', 'success')
        return redirect(url_for('practice', subject_id=subject_id))


# ==================== 收藏/错题 ====================

@app.route('/subjects/<int:subject_id>/favorites')
@login_required
def show_favorites(subject_id):
    favorites = get_user_favorites(session['user_id'], subject_id)
    subject = get_subject_by_id(subject_id)
    return render_template('favorites.html', favorites=favorites, subject=subject)


@app.route('/subjects/<int:subject_id>/favorite/<qid>', methods=['POST'])
@login_required
def favorite_question(subject_id, qid):
    result = toggle_favorite(session['user_id'], qid, subject_id)
    flash('已收藏' if result else '已取消收藏', 'success')
    fallback = url_for('show_question', subject_id=subject_id, qid=qid)
    ref = request.referrer
    if ref:
        parsed = urlparse(ref)
        if parsed.netloc:
            ref = None
    return redirect(ref or fallback)


# ==================== 题目互动功能（纠错/笔记/留言板） ====================

@app.route('/subjects/<int:subject_id>/question/<qid>/feedback', methods=['POST'])
@login_required
def submit_feedback(subject_id, qid):
    """提交题目纠错反馈"""
    data = request.get_json() if request.is_json else request.form
    content = (data.get('content', '') or '').strip()
    if not content or len(content) > 500:
        return jsonify({'error': '内容不能为空且不超过500字'}), 400
    
    user = get_current_user()
    image_path = data.get('image_path')
    fid = create_question_feedback(str(qid), subject_id, user['id'], content, image_path)
    return jsonify({'success': True, 'id': fid})


@app.route('/subjects/<int:subject_id>/question/<qid>/note', methods=['GET', 'POST'])
@login_required
def manage_note(subject_id, qid):
    """获取/保存笔记"""
    user = get_current_user()
    if request.method == 'GET':
        note = get_user_note(str(qid), user['id'])
        if note:
            return jsonify({'success': True, 'note': note})
        return jsonify({'success': True, 'note': None})
    
    # POST: 保存笔记
    data = request.get_json() if request.is_json else request.form
    content = (data.get('content', '') or '').strip()
    if len(content) > 500:
        return jsonify({'error': '笔记不超过500字'}), 400
    
    image_path = data.get('image_path')
    save_user_note(str(qid), subject_id, user['id'], content, image_path)
    return jsonify({'success': True})


@app.route('/subjects/<int:subject_id>/question/<qid>/comments', methods=['GET', 'POST'])
@login_required
def question_comments(subject_id, qid):
    """获取/创建留言"""
    user = get_current_user()
    if request.method == 'GET':
        page = request.args.get('page', 1, type=int)
        comments, total = get_question_comments(str(qid), page=page)
        return jsonify({
            'success': True,
            'comments': comments,
            'total': total,
            'page': page
        })
    
    # POST: 创建留言
    data = request.get_json() if request.is_json else request.form
    content = (data.get('content', '') or '').strip()
    if not content or len(content) > 500:
        return jsonify({'error': '内容不能为空且不超过500字'}), 400
    
    image_path = data.get('image_path')
    cid = create_comment(str(qid), subject_id, user['id'], user['username'], content, image_path)
    return jsonify({'success': True, 'id': cid})


@app.route('/api/upload-image', methods=['POST'])
@login_required
def upload_image():
    """上传图片（用于笔记/留言/纠错）"""
    if 'image' not in request.files:
        return jsonify({'error': '未上传图片'}), 400
    
    f = request.files['image']
    if f.filename == '':
        return jsonify({'error': '未选择文件'}), 400
    
    # 限制5MB
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({'error': '图片大小不能超过5MB'}), 400
    
    # 验证文件类型（扩展名 + magic 字节双重校验）
    allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
    if ext not in allowed:
        return jsonify({'error': '仅支持 png/jpg/jpeg/gif/webp 格式'}), 400
    
    # Magic 字节校验：读取文件头确认实际内容
    magic_bytes = f.read(12)
    f.seek(0)
    is_image = False
    if magic_bytes[:4] == b'\x89PNG':
        is_image = True
    elif magic_bytes[:3] == b'\xff\xd8\xff':
        is_image = True
    elif magic_bytes[:3] in (b'GIF',):
        is_image = True
    elif magic_bytes[:4] == b'RIFF' and magic_bytes[8:12] == b'WEBP':
        is_image = True
    if not is_image:
        return jsonify({'error': '文件内容不是有效的图片格式'}), 400
    
    import secrets
    filename = f"{secrets.token_hex(8)}.{ext}"
    filepath = os.path.join('/keyin/static/uploads', filename)
    f.save(filepath)
    
    return jsonify({'success': True, 'url': f'/static/uploads/{filename}'})


@app.route('/subjects/<int:subject_id>/question/<qid>/delete-comment', methods=['POST'])
@login_required
def delete_user_comment(subject_id, qid):
    """用户删除自己的留言"""
    data = request.get_json()
    comment_id = data.get('comment_id')
    user = get_current_user()
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM question_comments WHERE id = ?", (comment_id,))
    row = cur.fetchone()
    conn.close()
    
    if row and row[0] == user['id']:
        delete_comment(comment_id)
        return jsonify({'success': True})
    return jsonify({'error': '无权操作'}), 403


@app.route('/subjects/<int:subject_id>/wrong')
@login_required
def wrong_questions(subject_id):
    wrong = get_user_wrong_questions(session['user_id'], subject_id)
    subject = get_subject_by_id(subject_id)
    return render_template('wrong.html', questions=wrong, subject=subject)


# ==================== 历史真题 ====================

@app.route('/subjects/<int:subject_id>/exams')
@login_required
@_check_subject_license
def exam_years(subject_id):
    """历史真题 - 按分类选择（参照章节练习）"""
    subject = get_subject_by_id(subject_id)
    user_id = session.get('user_id')

    # 找到"历年真题"根分类
    from models import get_categories_tree
    tree = get_categories_tree(subject_id)
    exam_root = None
    for node in tree:
        if node.get('name') == '历年真题':
            exam_root = node
            break

    # 构建真题分类树 + 统计
    exam_tree = None
    subject_total = None
    if exam_root:
        # 复用 get_subject_category_stats 但只取真题分支
        stats_data = get_subject_category_stats(user_id, subject_id)
        full_tree = stats_data.get('tree', [])
        subject_total = stats_data.get('subject_total', {})
        # 找到真题分支
        for node in full_tree:
            if node.get('name') == '历年真题':
                exam_tree = [node]
                break

    return render_template('exam_years.html',
                          subject=subject,
                          tree=exam_tree,
                          subject_total=subject_total)


@app.route('/subjects/<int:subject_id>/exams/<int:year>')
@login_required
def exam_by_year(subject_id, year):
    """按年份答题"""
    rows = get_questions_by_year(subject_id, year)
    subject = get_subject_by_id(subject_id)
    
    questions = []
    for r in rows:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        questions.append(q)
    
    if not questions:
        flash('该年份暂无真题', 'info')
        return redirect(url_for('exam_years', subject_id=subject_id))
    
    return render_template('exam.html', questions=questions, subject=subject, year=year)


@app.route('/subjects/<int:subject_id>/exams/<int:year>/submit', methods=['POST'])
@login_required
def submit_exam(subject_id, year):
    """提交考试"""
    if year > 0:
        rows = get_questions_by_year(subject_id, year)
    else:
        rows = get_random_questions_model(subject_id, count=100)
    
    questions = []
    for r in rows:
        q = serialize_row(r)
        q['options'] = parse_options(q.get('options', '{}'))
        questions.append(q)
    
    correct_count = 0
    total = len(questions)
    
    for q in questions:
        user_answer = request.form.get(f'answer_{q["id"]}', '')
        if q['qtype_text'] == 'multiple':
            if set(user_answer) == set(q['answer']):
                correct_count += 1
        else:
            if user_answer == q['answer']:
                correct_count += 1
        is_correct = (set(user_answer) == set(q['answer'])) if q['qtype_text'] == 'multiple' else (user_answer == q['answer'])
        save_answer(session['user_id'], q['id'], user_answer, 1 if is_correct else 0, subject_id)
    
    score = (correct_count / total * 100) if total > 0 else 0
    
    return jsonify({
        'success': True,
        'correct_count': correct_count,
        'total': total,
        'score': round(score, 2),
    })


# ==================== 模拟考试 ====================

@app.route('/subjects/<int:subject_id>/mock')
@login_required
def mock_exam(subject_id):
    """模拟考试"""
    subject = get_subject_by_id(subject_id)
    return render_template('mock_exam.html', subject=subject)


@app.route('/subjects/<int:subject_id>/mock/start', methods=['POST'])
@login_required
def start_mock_exam(subject_id):
    """开始模拟考试"""
    question_count = request.form.get('question_count', 20, type=int)
    questions = get_random_questions(subject_id, count=question_count)
    
    if not questions:
        flash('暂无可考题目', 'info')
        return redirect(url_for('mock_exam', subject_id=subject_id))
    
    subject = get_subject_by_id(subject_id)
    return render_template('exam.html', questions=questions, subject=subject)


# ==================== 已掌握题目 ====================

@app.route('/subjects/<int:subject_id>/study/<int:category_id>/mastered')
@login_required
@_check_subject_license
def mastered(subject_id, category_id):
    """已掌握题目列表页"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    subject = get_subject_by_id(subject_id)
    mastered_list = get_mastered_questions(session['user_id'], category_id)
    # 添加推断评分和选项解析
    for q in mastered_list:
        q['inferred_quality'] = infer_quality(q)
        q['options'] = parse_options(q.get('options', '{}'))
    return render_template('mastered.html',
                          subject=subject, category=cat,
                          mastered_list=mastered_list)


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/mastered/<qid>/unmaster', methods=['POST'])
@login_required
def unmaster_question(subject_id, category_id, qid):
    """取消掌握：删除 review_schedule 记录，题目回到新题池"""
    delete_review_schedule(session['user_id'], qid)
    flash('已取消掌握，题目回到练习池', 'success')
    return redirect(url_for('mastered', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/question/<qid>/reset', methods=['POST'])
@login_required
def reset_question(subject_id, category_id, qid):
    """重置题目：删除复习计划 + 答题历史，回到新题状态"""
    reset_question_schedule(session['user_id'], qid)
    flash(f'题目 {qid} 已重置为新题', 'success')
    return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/question/<qid>/skip', methods=['POST'])
@login_required
def skip_question_interval(subject_id, category_id, qid):
    """直接复习：跳过间隔，立即进入该题的练习模式"""
    # 强化题不能直接复习
    if is_question_in_reinforce(session['user_id'], qid):
        flash('该题目处于强化状态，请进入背题模式', 'warning')
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))
    
    # 将 next_review 设为 now
    skip_review_interval(session['user_id'], qid)
    
    # 初始化单题练习会话
    session['practice'] = {
        'category_id': category_id,
        'subject_id': subject_id,
        'queue': [qid],
        'retry_count': {},
        'answered_correct_first': 0,
        'answered_wrong': 0,
        'stubborn': [],
        'total_attempts': 0,
        'initial_count': 1,
        'is_today_review': True,
        'answered': {},
    }
    return redirect(url_for('chapter_practice_qid', subject_id=subject_id, category_id=category_id, qid=qid))


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/question/<qid>/memorize')
@login_required
def memorize_mode(subject_id, category_id, qid):
    """强化背题模式：显示完整题干+选项+答案+解析"""
    question = get_question(qid)
    if not question or question['subject_id'] != subject_id:
        abort(404)
    question = dict(question)
    question['options'] = parse_options(question.get('options', '{}'))
    
    subject = get_subject_by_id(subject_id)
    cat = get_category(category_id)
    
    return render_template('memorize_mode.html',
                          question=question, subject=subject, category=cat,
                          subject_id=subject_id, category_id=category_id)


@app.route('/subjects/<int:subject_id>/study/<int:category_id>/question/<qid>/exit-reinforce', methods=['POST'])
@login_required
def exit_reinforce(subject_id, category_id, qid):
    """退出强化状态"""
    exit_reinforce_mode(session['user_id'], qid)
    flash(f'题目 {qid} 已退出强化', 'success')
    return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))


# ==================== 统计分析 ====================

@app.route('/subjects/<int:subject_id>/statistics')
@login_required
def statistics(subject_id):
    """统计分析 - 可视化页面"""
    subject = get_subject_by_id(subject_id)
    return render_template('statistics.html', subject=subject)


@app.route('/subjects/<int:subject_id>/stats/api')
@login_required
def stats_api(subject_id):
    """统计分析 - JSON API（P2-7: 增加 FSRS 掌握数据）"""
    user_id = session['user_id']

    summary = get_stats_summary(user_id, subject_id)
    daily = get_daily_trend(user_id, subject_id, days=30)
    from models import get_year_heatmap, get_calendar_stats
    from datetime import datetime as _dt
    current_year = _dt.now().year
    heatmap = get_year_heatmap(user_id, subject_id, current_year)
    calendar_stats = get_calendar_stats(user_id, subject_id, current_year)
    categories = get_category_mastery(user_id, subject_id)
    retention = get_retention_curve(user_id, subject_id)

    # P2-7: 掌握度统计（FSRS）
    from models import _mastered_sql_condition
    conn = get_db()
    cur = conn.cursor()
    now = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute(f"""
        SELECT COUNT(*) as mastered FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ?
        AND {_mastered_sql_condition()}
    """, (user_id, subject_id))
    mastered = cur.fetchone()['mastered']
    cur.execute("""
        SELECT COUNT(*) as due FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ?
        AND rs.next_review <= ?
    """, (user_id, subject_id, now))
    due = cur.fetchone()['due']
    cur.execute("""
        SELECT COUNT(*) as reviewed FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ?
    """, (user_id, subject_id))
    reviewed = cur.fetchone()['reviewed']
    cur.execute("SELECT COUNT(*) as total FROM questions WHERE subject_id = ? AND status = 1", (subject_id,))
    total = cur.fetchone()['total']
    
    # P2-11: 工作负载预测
    from models import predict_review_load, get_daily_learning_time, get_hourly_distribution
    load_forecast = predict_review_load(user_id, subject_id, days=30)
    learning_time = get_daily_learning_time(user_id, subject_id, days=30)
    hourly = get_hourly_distribution(user_id, subject_id, days=30)
    
    conn.close()

    return jsonify({
        'summary': summary,
        'daily_trend': daily,
        'heatmap': heatmap,
        'calendar_stats': calendar_stats,
        'calendar_year': current_year,
        'learning_time': learning_time,
        'hourly_distribution': hourly,
        'category_mastery': categories,
        'retention_curve': retention,
        'mastery_summary': {
            'total': total,
            'reviewed': reviewed,
            'mastered': mastered,
            'due': due,
            'new': total - reviewed,
        },
        'load_forecast': load_forecast,
    })


@app.route('/subjects/<int:subject_id>/stats/calendar/<int:year>')
@login_required
def stats_calendar(subject_id, year):
    """获取指定年份的日历热力图数据"""
    from models import get_year_heatmap, get_calendar_stats
    user_id = session['user_id']
    heatmap = get_year_heatmap(user_id, subject_id, year)
    calendar_stats = get_calendar_stats(user_id, subject_id, year)
    return jsonify({
        'year': year,
        'heatmap': heatmap,
        'calendar_stats': calendar_stats,
    })


# ==================== 名言API ====================

@app.route('/quotes')
def get_quotes():
    """返回名言列表，用于分享卡片"""
    import json
    quotes_path = os.path.join(os.path.dirname(__file__), 'quotes.json')
    try:
        with open(quotes_path, 'r', encoding='utf-8') as f:
            quotes = json.load(f)
        return jsonify(quotes)
    except Exception:
        return jsonify([])


# ==================== 旧路由兼容（重定向） ====================

@app.route('/sequential_start')
@login_required
def sequential_start():
    flash('请使用科目导航进入答题', 'info')
    return redirect(url_for('index'))


@app.route('/random_question')
@login_required
def random_question_old():
    flash('请使用科目导航进入答题', 'info')
    return redirect(url_for('index'))


@app.route('/show_history')
@login_required
def show_history_old():
    flash('请使用科目导航查看历史', 'info')
    return redirect(url_for('index'))


# ==================== 法律条款页面 ====================

DEFAULT_PRIVACY = """<h2>一、信息收集</h2>
<p>keyin心语（以下简称"本平台"）仅收集提供服务所必需的最少信息：</p>
<ul>
<li><strong>账号信息</strong>：用户名、密码（加密存储）、注册时间</li>
<li><strong>学习数据</strong>：答题记录、错题记录、学习笔记、留言内容</li>
<li><strong>设备信息</strong>：IP 地址（用于安全日志，不关联个人身份）</li>
</ul>

<h2>二、信息使用</h2>
<p>收集的信息仅用于以下目的：</p>
<ul>
<li>提供考试学习和刷题服务</li>
<li>生成学习统计和复习计划</li>
<li>维护平台安全和正常运行</li>
</ul>

<h2>三、信息保护</h2>
<ul>
<li>密码采用 PBKDF2/SHA256 算法加密存储</li>
<li>用户数据采用数据库访问控制保护</li>
<li>不向任何第三方出售、出租或分享用户个人数据</li>
</ul>

<h2>四、Cookie 使用</h2>
<p>本平台仅使用必要的登录 Cookie（Session）维持用户登录状态，关闭浏览器即失效。不用于跟踪、分析或广告目的。</p>

<h2>五、数据保留与删除</h2>
<p>用户数据在服务运行期间保留。如用户需要删除个人数据，请联系平台管理员。</p>

<h2>六、未成年人保护</h2>
<p>本平台不面向未成年人提供服务，不主动收集未成年人信息。</p>

<h2>七、隐私政策更新</h2>
<p>本政策可能不定期更新，重大变更将通过网站公告通知用户。</p>
"""

DEFAULT_TERMS = """<h2>一、服务说明</h2>
<p>keyin心语是一个面向软考考生的在线学习平台，提供题库练习、复习计划、学习统计等功能。用户需通过邀请码注册方可使用。</p>

<h2>二、用户行为规范</h2>
<p>用户在使用本平台时需遵守以下规范：</p>
<ul>
<li>遵守中华人民共和国相关法律法规</li>
<li>不得发布含有违法、暴力、色情、歧视等内容的留言或笔记</li>
<li>不得利用本平台从事任何破坏系统安全的行为</li>
<li>不得将账号转让、出售或分享给他人使用</li>
<li>不得利用技术手段爬取、复制平台题库数据</li>
</ul>

<h2>三、内容管理</h2>
<ul>
<li>用户在平台发布的笔记、留言等内容，平台有权进行审核和管理</li>
<li>对于违反规范的內容，平台有权删除并视情况限制账号使用</li>
<li>用户需对自发布的内容承担法律责任</li>
</ul>

<h2>四、免责声明</h2>
<ul>
<li>平台题库内容仅供参考学习使用，不保证与官方考试内容的完全一致性</li>
<li>平台不对因使用本服务导致的任何考试结果承担责任</li>
<li>如因不可抗力或系统维护导致服务中断，平台不承担赔偿责任</li>
</ul>

<h2>五、知识产权</h2>
<p>平台题库内容、界面设计、品牌标识等的知识产权归平台所有，未经授权不得复制、传播或用于商业用途。</p>

<h2>六、协议修改</h2>
<p>平台有权根据运营需要修改本协议，修改后的协议将在网站公示，继续使用即视为接受修改。</p>

<h2>七、联系方式</h2>
<p>如有任何问题或建议，请通过网站留言或联系平台管理员。</p>
"""

@app.route('/privacy')
def privacy_policy():
    from models import get_all_site_settings
    settings = get_all_site_settings()
    return render_template('privacy.html',
                          privacy_content=settings.get('privacy_policy', ''),
                          default_privacy=DEFAULT_PRIVACY,
                          last_updated=settings.get('privacy_updated', '2026-05-06'))

@app.route('/terms')
def terms_of_service():
    from models import get_all_site_settings
    settings = get_all_site_settings()
    return render_template('terms.html',
                          terms_content=settings.get('terms_of_service', ''),
                          default_terms=DEFAULT_TERMS,
                          last_updated=settings.get('terms_updated', '2026-05-06'))


# ==================== 错误处理 ====================

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('error.html', error_code=429,
                          error_message="操作太频繁，请稍后再试"), 429

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error_code=404, error_message="页面不存在"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', error_code=403, error_message="无权访问"), 403


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error_code=500, error_message="服务器错误"), 500


# ==================== 练习进度持久化 ====================

@app.before_request
def _ensure_practice_table():
    """确保 practice_sessions 表已创建（懒初始化）"""
    from models import init_practice_sessions_table
    init_practice_sessions_table()


@app.before_request
def _ensure_exam_records_table():
    """确保 exam_records 表已创建（懒初始化）"""
    from models import init_exam_records_table
    init_exam_records_table()


@app.before_request
def _ensure_notifications_table():
    """确保 notifications 表已创建（懒初始化）"""
    from models import init_notifications_table
    init_notifications_table()


@app.before_request
def _ensure_admin_read_columns():
    """确保留言和笔记表有 read_by_admin_at 列（懒迁移）"""
    from models import get_db
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE question_comments ADD COLUMN read_by_admin_at DATETIME")
    except Exception:
        pass  # 列已存在
    try:
        cur.execute("ALTER TABLE question_notes ADD COLUMN read_by_admin_at DATETIME")
    except Exception:
        pass
    conn.commit()
    conn.close()


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/save-progress', methods=['POST'])
@login_required
def practice_save_progress(subject_id, category_id):
    """保存练习进度到数据库"""
    import json
    from models import save_practice_session
    p = session.get('practice', {})
    if not p:
        return jsonify({'success': False, 'error': '无练习进度可保存'})
    current_qid = request.form.get('current_qid', '')
    save_practice_session(
        session['user_id'], category_id, subject_id,
        p.get('queue', []), p.get('answered', {}),
        p.get('retry_count', {}), p.get('stubborn', []),
        p.get('total_attempts', 0), p.get('answered_correct_first', 0),
        p.get('answered_wrong', 0), p.get('initial_count', 0),
        current_qid
    )
    return jsonify({'success': True})


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/resume-saved', methods=['GET'])
@login_required
def practice_resume_saved(subject_id, category_id):
    """从数据库恢复练习进度"""
    import json
    from models import load_practice_session, get_category
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    saved = load_practice_session(session['user_id'], category_id)
    if not saved:
        flash('未找到保存的进度', 'warning')
        return redirect(url_for('study_setup', subject_id=subject_id, category_id=category_id))
    # 恢复到 session
    session['practice'] = {
        'category_id': saved['category_id'],
        'subject_id': saved['subject_id'],
        'queue': saved['queue'],
        'answered': saved['answered'],
        'retry_count': saved['retry_count'],
        'stubborn': saved['stubborn'],
        'total_attempts': saved['total_attempts'],
        'answered_correct_first': saved['answered_correct_first'],
        'answered_wrong': saved['answered_wrong'],
        'initial_count': saved['initial_count'],
    }
    qid = saved.get('current_qid')
    if qid and qid in saved['queue']:
        return redirect(url_for('chapter_practice_qid', subject_id=subject_id, category_id=category_id, qid=qid))
    return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/clear-saved', methods=['POST'])
@login_required
def practice_clear_saved(subject_id, category_id):
    """清除保存的练习进度"""
    from models import clear_practice_session
    clear_practice_session(session['user_id'], category_id)
    return jsonify({'success': True})


# ==================== 通知中心 ====================

@app.route('/notifications')
@login_required
def notifications():
    """用户通知中心"""
    from models import get_user_notifications, get_unread_notification_count
    page = request.args.get('page', 1, type=int)
    notifs, total = get_user_notifications(session['user_id'], page=page, per_page=20)
    unread = get_unread_notification_count(session['user_id'])
    total_pages = max(1, (total + 20 - 1) // 20)
    return render_template('notifications.html', notifs=notifs, page=page, total_pages=total_pages, total=total, unread=unread)


@app.route('/notifications/<int:nid>/read', methods=['POST'])
@login_required
def mark_notification_read(nid):
    """标记通知为已读"""
    from models import mark_notification_read
    mark_notification_read(nid, session['user_id'])
    return redirect(url_for('notifications'))


@app.route('/notifications/read-all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    """标记所有通知为已读"""
    from models import mark_all_notifications_read
    mark_all_notifications_read(session['user_id'])
    return redirect(url_for('notifications'))


@app.route('/notifications/<int:nid>/delete', methods=['POST'])
@login_required
def delete_notification_route(nid):
    """删除通知"""
    from models import delete_notification
    delete_notification(nid, session['user_id'])
    flash('已删除', 'success')
    return redirect(url_for('notifications'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=32220, debug=False)
