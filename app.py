#!/usr/bin/env python3
"""
刻印 (KeyIn) - 答题端（重构版 v0.6.0）
支持多科目、权限控制、分类练习。
"""
import os
import csv
import json
import sqlite3
import random
import string
from datetime import datetime

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify, abort)

from models import (
    authenticate_user, get_user_by_id, get_user_subjects, get_questions_by_category,
    get_question, get_user_history, get_user_wrong_questions, get_user_favorites,
    toggle_favorite, save_answer, get_all_subjects, get_leaf_categories,
    get_categories_tree, update_user_last_login,
    get_due_questions, get_new_questions, get_review_progress,
    update_review_schedule, get_review_schedule, is_question_mastered, get_db,
    get_stats_summary, get_daily_trend, get_heatmap_data,
    get_category_mastery, get_retention_curve,
    # 新增封装函数
    get_subject_by_id, get_questions_count, get_real_exam_count, get_exam_years,
    get_user_subject_accuracy, get_next_question_id, get_questions_by_year,
    is_question_favorite, get_question_count_by_category, get_question_position_in_category,
    get_random_questions as get_random_questions_model,
    get_sequential_questions as get_sequential_questions_model,
    get_questions_by_category as get_questions_by_category_model,
    hash_password, create_user, get_category,
)
from auth import login_required, get_current_user
from admin import admin_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'keyin-2026-secret-key-change-me')

# 注册管理端 Blueprint
app.register_blueprint(admin_bp)


# ==================== 辅助函数 ====================

def init_db():
    """初始化数据库：如果 questions 表为空，检查 CSV 是否存在"""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM questions WHERE status = 1")
    count = cur.fetchone()[0]
    conn.close()
    if count == 0:
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'maogai_2025.csv')
        if os.path.exists(csv_path):
            print("CSV file exists but no active questions. Run migrate.py first.")


def serialize_row(row):
    """将 sqlite3.Row 转换为 dict"""
    if row is None:
        return None
    return dict(row)


def parse_options(options_str):
    """解析选项字符串为字典"""
    if not options_str:
        return {}
    if isinstance(options_str, dict):
        return options_str
    try:
        parsed = json.loads(options_str)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


# ==================== 认证路由 ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate_user(username, password)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            update_user_last_login(user['id'])
            next_url = request.args.get('next', url_for('index'))
            return redirect(next_url)
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not username or not password:
            flash('用户名和密码不能为空', 'error')
        elif password != confirm_password:
            flash('两次输入的密码不一致', 'error')
        elif len(username) < 3:
            flash('用户名至少需要3个字符', 'error')
        elif len(password) < 6:
            flash('密码至少需要6个字符', 'error')
        else:
            result = create_user(username, password, 'user')
            if result:
                flash('注册成功，请登录', 'success')
                return redirect(url_for('login'))
            else:
                flash('用户名已存在', 'error')
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ==================== 首页（科目选择） ====================

@app.route('/')
@login_required
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    if user['role'] == 'admin':
        subjects = get_all_subjects()
    else:
        subjects = get_user_subjects(user['id'])
    
    if not subjects:
        flash('您暂无可用科目，请联系管理员', 'info')
    
    return render_template('index.html', subjects=subjects, current_year=datetime.now().year)


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
        subjects = get_all_subjects()  # 修复：管理员也需要 subjects 变量
    
    subject = get_subject_by_id(subject_id)
    if not subject:
        abort(404)
    
    tree = get_categories_tree(subject_id)
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
                          tree=tree,
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
    tree = get_categories_tree(subject_id)
    subject = get_subject_by_id(subject_id)
    return render_template('practice.html', subject=subject, tree=tree)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>')
@login_required
def practice_category(subject_id, category_id):
    """按分类答题（SM-2 间隔重复）"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    
    user_id = session['user_id']
    
    questions = get_due_questions(user_id, category_id, limit=20)
    if not questions:
        questions = get_new_questions(user_id, category_id, limit=5)
    
    if not questions:
        questions = get_sequential_questions(subject_id, category_id)
    
    if not questions:
        flash('该分类下暂无题目', 'info')
        return redirect(url_for('practice', subject_id=subject_id))
    
    qid = questions[0]['id']
    return redirect(url_for('practice_setup', subject_id=subject_id, category_id=category_id))


# ==================== 章节练习：模式选择 + 考试/练习模式 ====================

@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/setup')
@login_required
def practice_setup(subject_id, category_id):
    """练习设置页：模式选择 + 题量选择（P2-9: 显示分类进度）"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    total = get_question_count_by_category(category_id)
    subject = get_subject_by_id(subject_id)
    user_id = session['user_id']

    # P2-9: 统计分类进度
    mastered = 0
    due_review = 0
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    import sqlite3
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""SELECT question_id FROM review_schedule WHERE user_id = ?""", (user_id,))
    reviewed_ids = {row['question_id'] for row in cur.fetchall()}
    for qid in reviewed_ids:
        rs = get_review_schedule(user_id, qid)
        if rs:
            if rs['repetitions'] >= 3 and rs['ease_factor'] >= 2.5 and rs['interval'] >= 15:
                mastered += 1
            if rs['next_review'] <= now:
                due_review += 1
    # 分类内的已掌握数
    cur.execute("""
        SELECT COUNT(*) as mastered_count FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ?
        AND rs.repetitions >= 3 AND rs.ease_factor >= 2.5 AND rs.interval >= 15
    """, (user_id, category_id))
    mastered_in_cat = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) as reviewed_in_cat FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.category_id = ?
    """, (user_id, category_id))
    reviewed_in_cat = cur.fetchone()[0]
    conn.close()

    return render_template('practice_setup.html',
                          subject=subject, category=cat, total=total,
                          reviewed_in_cat=reviewed_in_cat,
                          mastered_in_cat=mastered_in_cat,
                          due_review=due_review)


def _get_chapter_questions(subject_id, category_id, count=None):
    """获取章节练习题目列表（顺序），返回 (dict_list, raw_list)"""
    rows = get_questions_by_category_model(category_id)
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
def chapter_exam(subject_id, category_id):
    """考试模式：显示全部题目"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    count = request.args.get('count', type=int)
    questions, _ = _get_chapter_questions(subject_id, category_id, count=count)
    if not questions:
        flash('该分类下暂无题目', 'info')
        return redirect(url_for('practice', subject_id=subject_id))
    subject = get_subject_by_id(subject_id)
    return render_template('chapter_exam.html', questions=questions, subject=subject, category=cat)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/exam/submit', methods=['POST'])
@login_required
def chapter_exam_submit(subject_id, category_id):
    """提交考试模式试卷"""
    count = request.args.get('count', type=int)
    _, raw = _get_chapter_questions(subject_id, category_id, count=count)
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
        save_answer(user_id, q['id'], user_answer, 1 if is_correct else 0, subject_id)
        # P1-8: 考试错题自动加入复习计划
        if not is_correct:
            update_review_schedule(user_id, q['id'], subject_id, 0)
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
    return jsonify({
        'success': True,
        'correct_count': correct_count,
        'total': total,
        'score': score,
        'details': details,
    })


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice')
@login_required
def chapter_practice_start(subject_id, category_id):
    """练习模式起始：初始化队列到 session"""
    cat = get_category(category_id)
    if not cat or cat['subject_id'] != subject_id:
        abort(404)
    count = request.args.get('count', type=int)
    questions, _ = _get_chapter_questions(subject_id, category_id, count=count)
    if not questions:
        flash('该分类下暂无题目', 'info')
        return redirect(url_for('practice', subject_id=subject_id))

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
def chapter_practice_next(subject_id, category_id):
    """练习模式：从队列取下一题"""
    p = session.get('practice', {})
    if not p:
        return redirect(url_for('practice_setup', subject_id=subject_id, category_id=category_id))

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

    # P1-4: 进度数据
    answered_count = len(p.get('answered', {}))
    answered_unique = set()
    for aqid in p.get('answered', {}):
        if aqid not in p.get('stubborn', []):
            answered_unique.add(aqid)
    remaining = len(queue)

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
                          remaining=remaining,
                          completed_count=initial_count - remaining)


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>/answer', methods=['POST'])
@login_required
def chapter_practice_answer(subject_id, category_id, qid):
    """练习模式：提交答案"""
    question = get_question(qid)
    if not question:
        abort(404)
    question = dict(question)
    user_answer = request.form.get('answer', '')
    correct_answer = question['answer']

    if question['qtype_text'] == 'multiple':
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
    else:
        is_correct = user_answer == correct_answer
        is_partial = False

    save_answer(session['user_id'], qid, user_answer, 1 if is_correct else 0, subject_id)
    if is_correct:
        result_msg = '回答正确！'
    elif is_partial:
        result_msg = f'部分正确。正确答案是：{correct_answer}'
    else:
        result_msg = f'回答错误。正确答案是：{correct_answer}'

    # 更新会话统计
    p = session.get('practice', {})
    p['total_attempts'] = p.get('total_attempts', 0) + 1

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
    return render_template('chapter_practice.html',
                          question=question,
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
                          remaining=len(queue),
                          completed_count=p.get('initial_count', 0) - len(queue))


@app.route('/subjects/<int:subject_id>/practice/<int:category_id>/practice/<qid>/rate', methods=['POST'])
@login_required
def chapter_practice_rate(subject_id, category_id, qid):
    """练习模式：SM-2 评分 + 队列调度"""
    quality = request.form.get('quality', type=int)
    if quality is None:
        return redirect(url_for('chapter_practice_next', subject_id=subject_id, category_id=category_id))

    # 更新 SM-2 复习计划
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

    # 队列调度
    queue = p.get('queue', [])
    if quality >= 2:
        if qid in queue:
            queue.remove(qid)
    elif quality in (0, 1) and retry < 5 and qid in queue:
        queue.remove(qid)
        queue.append(qid)
        p['retry_count'][qid] = retry + 1

    # 移除当前题（如果还在队列头部）
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
    user_answer = request.form.get('answer', '')
    correct_answer = question['answer']
    
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
    """SM-2 评分：答完题后评分"""
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
    return redirect(request.referrer or url_for('show_question', subject_id=subject_id, qid=qid))


@app.route('/subjects/<int:subject_id>/wrong')
@login_required
def wrong_questions(subject_id):
    wrong = get_user_wrong_questions(session['user_id'], subject_id)
    subject = get_subject_by_id(subject_id)
    return render_template('wrong.html', questions=wrong, subject=subject)


# ==================== 历史真题 ====================

@app.route('/subjects/<int:subject_id>/exams')
@login_required
def exam_years(subject_id):
    """历史真题 - 按年份选择"""
    subject = get_subject_by_id(subject_id)
    years = get_exam_years(subject_id)
    year_counts = []
    for year in years:
        rows = get_questions_by_year(subject_id, year)
        year_counts.append((year, len(rows)))
    return render_template('exam_years.html', subject=subject, years=year_counts)


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
    """统计分析 - JSON API（P2-7: 增加 SM-2 掌握数据）"""
    user_id = session['user_id']

    summary = get_stats_summary(user_id, subject_id)
    daily = get_daily_trend(user_id, subject_id, days=30)
    heatmap = get_heatmap_data(user_id, subject_id, days=90)
    categories = get_category_mastery(user_id, subject_id)
    retention = get_retention_curve(user_id, subject_id)

    # P2-7: SM-2 掌握度统计
    conn = get_db()
    cur = conn.cursor()
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        SELECT COUNT(*) as mastered FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        WHERE rs.user_id = ? AND q.subject_id = ?
        AND rs.repetitions >= 3 AND rs.ease_factor >= 2.5 AND rs.interval >= 15
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
    conn.close()

    return jsonify({
        'summary': summary,
        'daily_trend': daily,
        'heatmap': heatmap,
        'category_mastery': categories,
        'retention_curve': retention,
        'sm2_summary': {
            'total': total,
            'reviewed': reviewed,
            'mastered': mastered,
            'due': due,
            'new': total - reviewed,
        },
    })


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


# ==================== 错误处理 ====================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error_code=404, error_message="页面不存在"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', error_code=403, error_message="无权访问"), 403


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error_code=500, error_message="服务器错误"), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=32220, debug=True)
