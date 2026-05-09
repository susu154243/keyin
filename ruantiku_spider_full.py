#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ruantiku.com 全量爬虫 v2 - 轻量版（不用BeautifulSoup，省内存）
"""
import requests, pickle, json, re, time, os, sys

BASE = "http://www.ruantiku.com"
COOKIE_PATH = "/tmp/rt_session_loggedin.pkl"
DATA_DIR = "/keyin/ruantiku_data"
PROGRESS_FILE = os.path.join(DATA_DIR, "crawl_progress.json")

SUBJECTS = {
    2: "系统集成项目管理工程师",
    3: "信息系统监理师",
    4: "软件设计师",
    5: "系统分析师",
    6: "信息安全工程师",
    7: "网络规划设计师",
    8: "网络工程师",
    28: "系统架构设计师",
    29: "系统规划与管理师",
    30: "信息系统管理工程师",
    31: "程序员",
    32: "数据库系统工程师",
    34: "电子商务设计师",
    35: "嵌入式系统设计师",
    36: "多媒体应用设计师",
    37: "软件评测师",
    38: "信息处理技术员",
    39: "信息系统运行管理员",
    40: "网络管理员",
}

def load_session():
    with open(COOKIE_PATH, "rb") as f:
        cookies_dict = pickle.load(f)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    })
    for k, v in cookies_dict.items():
        session.cookies.set(k, v)
    return session

def get_paper_list(session, eid):
    r = session.get(f"{BASE}/xg/TrueExam.aspx?type=5&eid={eid}", timeout=10)
    papers = []
    # 用正则提取 CreatExam(type, 'sid', 'title')
    for m in re.finditer(r"CreatExam\(\s*\d+\s*,\s*'(\d+)'\s*,\s*'([^']+)'", r.text):
        papers.append({"sid": m.group(1), "title": m.group(2)})
    return papers

def create_exam(session, sid, title):
    r = session.post(f"{BASE}/xg/exam/createxam.aspx", data={
        "menu": 4, "sid": sid, "title": title
    }, timeout=10)
    new_sid = r.text.strip()
    return new_sid if new_sid.isdigit() else None

def get_exam_data(session, exam_sid):
    r = session.post(f"{BASE}/ajax/getexamdata.aspx", data={
        "sid": exam_sid, "sjtype": 4,
        "useranswerrecord": "", "studytimer": "0"
    }, timeout=15)
    return r.json()

def parse_questions(content_html):
    """纯正则解析题目"""
    questions = []
    blocks = re.split(r'(?=<li data-Ttype=)', content_html)
    for block in blocks:
        if not block.strip().startswith('<li'):
            continue
        q = {}
        m = re.search(r'data-tid="(\d+)"', block)
        if m: q['tid'] = int(m.group(1))
        m = re.search(r'data-rid="(\d+)"', block)
        if m: q['rid'] = int(m.group(1))
        
        # 题干
        tm = re.search(r'<div class="answer_tm">(.*?)</div><div class="option', block, re.DOTALL)
        if tm:
            p_matches = re.findall(r'<p>(.*?)</p>', tm.group(1), re.DOTALL)
            if len(p_matches) > 1:
                q['question'] = re.sub(r'<[^>]+>', '', p_matches[1]).strip()
        
        # 题型
        m = re.search(r'<em>\[([^\]]+)\]</em>', block)
        if m: q['type'] = m.group(1)
        
        # 选项
        opt = re.search(r'<div class="option[^"]*"[^>]*>(.*?)</div><div class="hide', block, re.DOTALL)
        if opt:
            opts = re.findall(r'<span class="abcd">([A-F])</span>(.*?)(?=<label|$)', opt.group(1), re.DOTALL)
            q['options'] = [{"label": l, "text": re.sub(r'<[^>]+>', '', t).strip()} for l, t in opts]
        
        # 答案（支持合并题答案如 C、B）
        m = re.search(r'<p class="ckdaan">参考答案：<em>([A-F、]+)</em>', block)
        if m: q['answer'] = m.group(1)
        
        if q.get('question') and q.get('options'):
            questions.append(q)
    return questions

def get_explanations(session, questions):
    for q in questions:
        if not q.get('tid'):
            continue
        try:
            r = session.post(f"{BASE}/Ajax/GetShitiExplain.aspx",
                           data={"sid": q['tid']}, timeout=10)
            if len(r.text) > 100:
                text = re.sub(r'<[^>]+>', '', r.text).strip()
                text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                text = re.sub(r'\s+', ' ', text).strip()
                if text and '来源错误' not in text and '无标题' not in text[:20]:
                    q['explanation'] = text
            time.sleep(0.3)
        except Exception as e:
            print(f"  获取解析失败 tid={q.get('tid')}: {e}")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"subjects": {}, "total_crawled": 0, "total_papers": 0}

def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    session = load_session()
    progress = load_progress()
    
    start_time = __import__('datetime').datetime.now()
    print(f"开始爬取: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"待爬科目: {len(SUBJECTS)} 个")
    print("=" * 60)
    sys.stdout.flush()
    
    for eid, name in SUBJECTS.items():
        eid_str = str(eid)
        if eid_str in progress.get("subjects", {}):
            print(f"\n跳过 {name} (已爬取)")
            sys.stdout.flush()
            continue
        
        print(f"\n[{eid}] {name}")
        sys.stdout.flush()
        
        try:
            papers = get_paper_list(session, eid)
        except Exception as e:
            print(f"  获取列表失败: {e}")
            sys.stdout.flush()
            continue
        
        if not papers:
            print(f"  无真题数据")
            sys.stdout.flush()
            continue
        
        print(f"  真题: {len(papers)} 套")
        sys.stdout.flush()
        
        subject_dir = os.path.join(DATA_DIR, name)
        os.makedirs(subject_dir, exist_ok=True)
        
        subject_questions = []
        subject_papers_done = []
        
        for j, paper in enumerate(papers):
            sid = paper['sid']
            title = paper['title']
            
            print(f"  [{j+1}/{len(papers)}] {title[:50]}")
            sys.stdout.flush()
            
            try:
                exam_sid = create_exam(session, sid, title)
                if not exam_sid:
                    print(f"    创建考试失败")
                    sys.stdout.flush()
                    continue
                
                exam_data = get_exam_data(session, exam_sid)
                questions = parse_questions(exam_data['content'])
                print(f"    题目: {len(questions)} 道")
                sys.stdout.flush()
                
                get_explanations(session, questions)
                
                paper_data = {
                    "sid": sid, "exam_sid": exam_sid, "title": title,
                    "subject_id": eid, "subject_name": name,
                    "total_questions": len(questions), "questions": questions
                }
                
                paper_file = os.path.join(subject_dir, f"{sid}.json")
                with open(paper_file, 'w', encoding='utf-8') as f:
                    json.dump(paper_data, f, ensure_ascii=False)
                
                subject_questions.extend(questions)
                subject_papers_done.append(paper)
                print(f"    已保存 {len(questions)} 题")
                sys.stdout.flush()
                
                time.sleep(3)
            except Exception as e:
                print(f"    爬取失败: {e}")
                sys.stdout.flush()
                continue
        
        if subject_questions:
            summary_file = os.path.join(subject_dir, "summary.json")
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "subject_id": eid, "subject_name": name,
                    "papers": len(subject_papers_done),
                    "total_questions": len(subject_questions),
                    "papers_info": [{"sid": p['sid'], "title": p['title']} for p in subject_papers_done]
                }, f, ensure_ascii=False, indent=2)
            print(f"  科目完成: {len(subject_papers_done)} 套, {len(subject_questions)} 题")
            sys.stdout.flush()
        
        progress["subjects"][eid_str] = {
            "name": name, "papers": len(subject_papers_done),
            "questions": len(subject_questions), "done": True
        }
        progress["total_crawled"] += len(subject_questions)
        progress["total_papers"] += len(subject_papers_done)
        save_progress(progress)
        
        time.sleep(5)
    
    elapsed = __import__('datetime').datetime.now() - start_time
    print(f"\n{'='*60}")
    print(f"爬取完成! 耗时: {elapsed}")
    print(f"总计: {progress['total_papers']} 套, {progress['total_crawled']} 题")
    sys.stdout.flush()

if __name__ == "__main__":
    main()
