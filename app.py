#!/usr/bin/env python3
"""
ExamMaster - 答题端（重构版 v0.6.0）
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
    update_review_schedule,
    get_stats_summary, get_daily_trend, get_heatmap_data,
    get_category_mastery, get_retention_curve,
    # 新增封装函数
    get_subject_by_id, get_questions_count, get_real_exam_count, get_exam_years,
    get_user_subject_accuracy, get_next_question_id, get_questions_by_year,
    is_question_favorite, get_question_count_by_category, get_question_position_in_category,
    get_random_questions as get_random_questions_model,
    get_sequential_questions as get_sequential_questions_model,
    hash_password, create_user, get_category,
)
from auth import login_required, get_current_user
from admin import admin_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'exam-master-2026-secret-key-change-me')

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
    return redirect(url_for('show_question', subject_id=subject_id, qid=qid))


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


@app.route('/subjects/<int:subject_id>/rate/<int:qid>', methods=['POST'])
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
    """统计分析 - JSON API"""
    user_id = session['user_id']
    
    summary = get_stats_summary(user_id, subject_id)
    daily = get_daily_trend(user_id, subject_id, days=30)
    heatmap = get_heatmap_data(user_id, subject_id, days=90)
    categories = get_category_mastery(user_id, subject_id)
    retention = get_retention_curve(user_id, subject_id)
    
    return jsonify({
        'summary': summary,
        'daily_trend': daily,
        'heatmap': heatmap,
        'category_mastery': categories,
        'retention_curve': retention,
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
