#!/usr/bin/env python3
"""KeyIn 学习模拟器 v5 — 修复首次答题用 INSERT，后续用 UPDATE"""
import sys, os
sys.path.insert(0, '/keyin')
os.environ['SECRET_KEY'] = 'test123'

import random
import sqlite3
from datetime import datetime, timedelta, date

DB_PATH = '/keyin/database.db'

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.row_factory = sqlite3.Row
    return conn

random.seed(42)

TEST_USER = 'sim_v5_20260503'
SUBJECT_ID = 1

conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT id FROM users WHERE username = ?", (TEST_USER,))
old = cur.fetchone()
if old:
    uid = old[0]
    for t in ['review_schedule','favorites','history','user_subjects','user_licenses']:
        cur.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    cur.execute("DELETE FROM users WHERE id=?", (uid,))
conn.commit()

from models import hash_password, grant_user_license
cur.execute("INSERT INTO users (username, password_hash, role, status) VALUES (?,?,?,1)",
            (TEST_USER, hash_password('SimPass123'), 'user'))
user_id = cur.lastrowid
conn.commit()
grant_user_license(user_id, SUBJECT_ID, days=365)

cur.execute("""INSERT OR REPLACE INTO study_limits 
    (user_id, subject_id, daily_new_limit, daily_review_limit, desired_retention, max_interval, learning_steps)
    VALUES (?,?,?,?,?,?,?)""",
    (user_id, SUBJECT_ID, 10, 50, 0.9, 30, '[1, 10]'))
conn.commit()

cur.execute("SELECT id FROM questions WHERE status=1 AND subject_id=? ORDER BY id", (SUBJECT_ID,))
all_q = [r[0] for r in cur.fetchall()]
total = len(all_q)
print(f"👤 {TEST_USER} id={user_id} | 📊 {total} 题")

from models import fsrs_schedule, init_memory_state, RELEARNING_STABILITY_KEEP

def q_new():
    r = random.random()
    return 0 if r<0.08 else 1 if r<0.15 else 2 if r<0.45 else 3 if r<0.75 else 4

def q_review():
    r = random.random()
    return 0 if r<0.03 else 1 if r<0.05 else 2 if r<0.25 else 3 if r<0.60 else 4

today = date.today()
now = datetime.now()

# ── 新题首次答题（INSERT）──
print(f"\n📝 Day 0 - 首次答题")
new_stats = {0:0,1:0,2:0,3:0,4:0}
for qid in all_q:
    q = q_new()
    new_stats[q] += 1
    ns, nd = init_memory_state(q)
    nr, ne, rf = 1, 2.5, 0
    if q in (0, 1):
        cs, ls, ni = 'learning', (2 if q==0 else 1), 0
        nxt = now
    else:
        cs, ls = 'review', 0
        ni = {2:1, 3:2, 4:6}.get(q, 1)
        nxt = datetime.combine(today + timedelta(days=ni), datetime.min.time())
    
    cur.execute("""INSERT INTO review_schedule 
        (user_id, question_id, subject_id, ease_factor, interval, repetitions, review_fails,
         next_review, last_review, last_quality, stability, difficulty, card_state, learning_step)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, qid, SUBJECT_ID, ne, ni, nr, rf,
         nxt.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S'),
         q, round(ns,2), round(nd,2), cs, ls))
conn.commit()
print(f"   Q0={new_stats[0]} Q1={new_stats[1]} Q2={new_stats[2]} Q3={new_stats[3]} Q4={new_stats[4]}")

# ── 按天推进 ──
def process_day(sim_date):
    sim_now = datetime.combine(sim_date, datetime.min.time()) + timedelta(hours=9)
    sim_str = sim_now.strftime('%Y-%m-%d %H:%M:%S')
    
    cur.execute("""SELECT question_id, ease_factor, interval, repetitions, stability, difficulty,
                          last_review, card_state, learning_step, review_fails
                   FROM review_schedule 
                   WHERE user_id=? AND next_review<=? AND card_state!='reinforce'
                   ORDER BY next_review""", (user_id, sim_str))
    due_rows = [(r[0], dict(r)) for r in cur.fetchall()]
    
    if not due_rows:
        return 0
    
    for qid, ex in due_rows:
        q = q_review() if ex['repetitions'] >= 1 else q_new()
        cs = ex.get('card_state') or 'review'
        ls = ex.get('learning_step') or 0
        rf = ex.get('review_fails') or 0
        nr = ex['repetitions'] + 1
        ne = ex['ease_factor']
        
        if cs == 'learning':
            s = ex.get('stability') or 1.0
            d = ex.get('difficulty') or 5.0
            if q >= 2:
                ls = max(0, ls - 1)
                if ls == 0:
                    cs = 'review'
                    ns, nd = init_memory_state(q)
                    ni = {2:1, 3:2, 4:6}.get(q, 1)
                    nxt = datetime.combine(sim_date + timedelta(days=ni), datetime.min.time())
                else:
                    ni, nxt = 0, sim_now
            else:
                ni, nxt = 0, sim_now
            ns, nd = s, d
        elif q in (0, 1):
            s = ex.get('stability') or 1.0
            ns = s * RELEARNING_STABILITY_KEEP
            _, nd = init_memory_state(q)
            ni = 0
            cs, ls = 'learning', (2 if q==0 else 1)
            rf += 1
            if rf >= 5:
                cs, ni = 'reinforce', 30
                nxt = datetime.combine(sim_date + timedelta(days=30), datetime.min.time())
            else:
                nxt = sim_now
        else:
            s = ex.get('stability') or 1.0
            d = ex.get('difficulty') or 5.0
            lr_str = ex.get('last_review')
            if lr_str:
                lr = datetime.strptime(lr_str, '%Y-%m-%d %H:%M:%S')
                dt = max(1, (sim_date - lr.date()).days)
            else:
                dt = 1
            ns, nd, ni, _ = fsrs_schedule(q, s, d, dt, 0.9)
            ni = min(ni, 30)
            rf = 0
            if ni < 1:
                cs, ls, ni = 'learning', 2, 0
                nxt = sim_now
            else:
                cs, ls = 'review', 0
                nxt = datetime.combine(sim_date + timedelta(days=max(1, ni)), datetime.min.time())
        
        cur.execute("""UPDATE review_schedule SET ease_factor=?, interval=?, repetitions=?, review_fails=?,
                     next_review=?, last_review=?, last_quality=?, stability=?, difficulty=?,
                     card_state=?, learning_step=? WHERE user_id=? AND question_id=?""",
            (ne, ni, nr, rf, nxt.strftime('%Y-%m-%d %H:%M:%S'), sim_now.strftime('%Y-%m-%d %H:%M:%S'),
             q, round(ns,2), round(nd,2), cs, ls, user_id, qid))
    
    conn.commit()
    return len(due_rows)

def get_state(sim_str):
    cur.execute("SELECT card_state, COUNT(*) FROM review_schedule WHERE user_id=? GROUP BY card_state", (user_id,))
    states = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM review_schedule WHERE user_id=? AND stability>=45 AND repetitions>=3", (user_id,))
    mastered = cur.fetchone()[0]
    return states.get('learning',0), states.get('review',0), states.get('reinforce',0), mastered

day, max_days, mastered = 0, 365, 0
while day < max_days:
    day += 1
    sim_date = today + timedelta(days=day)
    sim_str = (datetime.combine(sim_date, datetime.min.time()) + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')
    
    due_count = process_day(sim_date)
    if due_count == 0:
        continue
    
    learning, review, reinforce, mastered = get_state(sim_str)
    pct = mastered / total * 100
    
    if mastered >= 5 or day <= 5 or day % 5 == 0:
        print(f"Day {day:3d} ({sim_date.strftime('%m/%d')}): {due_count:3d}题 | 学习:{learning:3d} 复习:{review:3d} 强化:{reinforce:3d} 已掌握:{mastered:3d}({pct:.1f}%)")
    
    if mastered == total:
        print(f"\n✅ 全部 {total} 题已掌握！耗时 {day} 天")
        break

if mastered < total:
    print(f"\n⚠️ {day}天后：已掌握 {mastered}/{total} ({mastered/total*100:.1f}%)")
    _, _, _, m = get_state('2099-01-01')
    print(f"   最终状态分布: 学习={_}, 复习={_}, 强化={_}")
