# KeyIn 完整改造设计方案

> 2026-05-02 | 版本 v2.0 | 基于 Anki FSRS 源码分析 | 待审批

---

## 零、改造背景

当前系统基于 SM-2 算法，存在 6 个核心问题：
1. 复习间隔不合理（硬编码 ease_factor）
2. 答错后一刀切重置（reps=0）
3. 不考虑复习时机（提前/overdue 影响相同）
4. "掌握"标准拍脑袋定
5. 复习洪峰（精确间隔导致扎堆）
6. 无每日上限（可无限答题）

通过分析 Anki 开源源码，提取出 **14 个可借鉴模块**，分三阶段实施。

---

## 一、改造优先级分层

### P0 — 本次改造（直接影响体验，6 项）

| # | 模块 | 说明 |
|---|------|------|
| 1 | FSRS 调度算法 | 替换 SM-2，基于遗忘曲线计算间隔 |
| 2 | Stability + Difficulty 记忆模型 | 双维度替代单一 ease_factor |
| 3 | delta_t 复习时机感知 | 评分时考虑距上次间隔 |
| 4 | 复习优先级排序 | 保留率排序，最易忘优先 |
| 5 | Fuzz 间隔模糊化 | ±10% 随机偏移，防洪峰 |
| 6 | 每日学习/复习上限 | 防疲劳 |

### P1 — 后续迭代（系统健壮性，4 项）

| # | 模块 | 说明 |
|---|------|------|
| 7 | 学习步骤 | 新题→学习步骤→正式复习 |
| 8 | 重学机制 | 复习答错→重学步骤→重置 |
| 9 | 历史数据打通 | history → 重建记忆状态 |
| 10 | 可配置目标保留率 | Per-科目 DR 配置 |

### P2 — 长期规划（高级功能，4 项）

| # | 模块 | 说明 |
|---|------|------|
| 11 | 工作负载预测 | 模拟器预测未来 N 天复习量 |
| 12 | 负载均衡 | 按周分布，避免某天题量暴增 |
| 13 | 最优保留率计算 | 基于历史数据计算最佳 DR |
| 14 | 科目独立预设 | 每科目独立配置 |

---

## 二、P0 详细设计（本次改造）

### 2.1 FSRS 调度算法 + 记忆模型 + delta_t

#### 2.1.1 核心概念

| 概念 | 符号 | 范围 | 含义 |
|------|------|------|------|
| 稳定性 | S | 0 ~ ∞（天） | 记忆保持到 90% 保留率所需天数 |
| 难度 | D | 1.0 ~ 10.0 | 题目固有难度 |
| 目标保留率 | R | 0.7 ~ 0.95 | 默认 0.9 |
| 遗忘衰减 | decay | 固定 0.9 | FSRS 标准参数 |

#### 2.1.2 核心公式

```python
import math, random

# ── 间隔计算（核心公式） ──
def get_interval(stability, desired_retention=0.9):
    """由稳定性和目标保留率计算下次复习间隔（天）
    遗忘曲线: R(t) = e^(-decay × t / S)
    反推: t = S × (-ln(R) / decay)
    """
    return stability * (-math.log(desired_retention) / 0.9)


# ── 稳定性更新（含 delta_t 感知） ──
def update_stability(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """
    quality:   0~4（忘了/模糊/一般/简单/秒答）
    stability: 当前稳定性（天）
    difficulty: 当前难度（1~10）
    delta_t:   距上次复习的天数（>=0）
    
    关键设计：
    - 答对时：delta_t > stability → 增益大（超出记忆极限答对）
    - 答对时：delta_t < stability → 增益小（还没到遗忘时间）
    - 答错时：按比例衰减，不归零
    """
    if delta_t <= 0:
        delta_t = 0.01
    
    quality_factor = (quality + 1) / 5.0  # 0.2 ~ 1.0
    
    if quality >= 2:  # 答对
        growth_ratio = math.sqrt(delta_t / stability) if stability > 0 else 1.0
        gain = quality_factor * 0.28 * growth_ratio
        new_stability = stability * (1 + gain)
    else:  # 答错
        forget_factor = (1 - quality / 2.0)  # 0.5(忘了) ~ 1.0(模糊)
        decay_ratio = 1.0 - 0.35 * forget_factor * min(delta_t / stability, 2.0) if stability > 0 else 0.5
        new_stability = stability * max(decay_ratio, 0.15)  # 最低保留 15%
    
    return max(new_stability, 0.1)


# ── 难度更新 ──
def update_difficulty(quality, difficulty):
    """
    答对 → 难度降低
    答错 → 难度升高
    边界衰减：接近极值时变化更慢
    """
    delta = (2.0 - quality) * 0.3  # quality=4 → -0.6, quality=0 → +0.6
    
    if delta < 0:
        delta *= (difficulty - 1.0) / 9.0
    else:
        delta *= (10.0 - difficulty) / 9.0
    
    return max(1.0, min(10.0, difficulty + delta))


# ── 首次初始化 ──
def init_memory_state(quality):
    """新题首次答题后初始化记忆状态"""
    stability_map = {0: 0.5, 1: 1.0, 2: 2.0, 3: 4.0, 4: 7.0}
    difficulty_map = {0: 8.0, 1: 6.5, 2: 5.0, 3: 3.5, 4: 2.0}
    return stability_map[quality], difficulty_map[quality]


# ── Fuzz（间隔模糊化） ──
def apply_fuzz(interval):
    """±10% 随机偏移，避免洪峰"""
    if interval <= 1:
        return interval
    fuzz_range = max(1, int(interval * 0.1))
    return max(1, interval + random.randint(-fuzz_range, fuzz_range))


# ── 保留率计算（用于排序） ──
def get_retrievability(stability, delta_t):
    """计算当前保留率 R = e^(-decay × t / S)"""
    if stability <= 0 or delta_t < 0:
        return 1.0
    return math.exp(-0.9 * delta_t / stability)


# ── 完整调度流程 ──
def fsrs_schedule(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """FSRS 完整调度：输入当前状态 + 评分，输出新状态 + 下次间隔"""
    new_stability = update_stability(quality, stability, difficulty, delta_t, desired_retention)
    new_difficulty = update_difficulty(quality, difficulty)
    interval = get_interval(new_stability, desired_retention)
    interval = apply_fuzz(interval)
    return new_stability, new_difficulty, interval
```

### 2.2 复习优先级排序（保留率排序）

**当前**：
```sql
ORDER BY next_review ASC  -- 先到期的先做
```

**改为**：
```sql
ORDER BY 
  CASE 
    WHEN next_review <= NOW() THEN 0  -- 到期题目优先
    ELSE 1
  END,
  -- 到期题目中，保留率最低的先做
  (SELECT exp(-0.9 * julianday(NOW()) - julianday(last_review) / stability) FROM review_schedule ...) ASC,
  next_review ASC  -- 同保留率按时间排
```

**Python 实现**（在 `get_due_questions` 中）：
```python
def get_due_questions_fsrs(user_id, limit=20):
    """获取到期题目，按保留率排序（最易忘优先）"""
    from datetime import datetime
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT q.*, rs.stability, rs.difficulty, rs.last_review, rs.ease_factor,
               rs.interval, rs.repetitions, rs.next_review
        FROM questions q
        JOIN review_schedule rs ON rs.question_id = q.id AND rs.user_id = ?
        WHERE q.status = 1 AND rs.next_review <= ?
        ORDER BY rs.next_review ASC
    """, (user_id, now_str))
    
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    # Python 侧按保留率排序
    for row in rows:
        if row['last_review'] and row['stability']:
            last_rev = datetime.strptime(row['last_review'], '%Y-%m-%d %H:%M:%S')
            delta_t = (now - last_rev).total_seconds() / 86400
            row['retrievability'] = math.exp(-0.9 * delta_t / row['stability'])
        else:
            row['retrievability'] = 0.0  # 无数据视为易忘
    
    rows.sort(key=lambda x: x['retrievability'])
    return rows[:limit]
```

### 2.3 每日学习/复习上限

#### 新增配置表
```sql
CREATE TABLE IF NOT EXISTS study_limits (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    daily_new_limit INTEGER DEFAULT 10,      -- 每日新题上限
    daily_review_limit INTEGER DEFAULT 50,   -- 每日复习上限
    UNIQUE(user_id, subject_id)
);
```

#### 应用层限制
```python
def get_daily_count(user_id, subject_id):
    """获取用户今日已答题数"""
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db()
    cur = conn.cursor()
    
    # 今日新题数（不在 review_schedule 中的题）
    cur.execute("""
        SELECT COUNT(*) FROM history
        WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) = ?
        AND question_id NOT IN (SELECT question_id FROM review_schedule WHERE user_id = ?)
    """, (user_id, subject_id, today, user_id))
    new_count = cur.fetchone()[0]
    
    # 今日复习数
    cur.execute("""
        SELECT COUNT(*) FROM history
        WHERE user_id = ? AND subject_id = ?
        AND DATE(timestamp) = ?
        AND question_id IN (SELECT question_id FROM review_schedule WHERE user_id = ?)
    """, (user_id, subject_id, today, user_id))
    review_count = cur.fetchone()[0]
    
    conn.close()
    return new_count, review_count


def can_do_new_question(user_id, subject_id):
    """是否可以继续做新题"""
    limits = get_study_limits(user_id, subject_id)
    new_count, _ = get_daily_count(user_id, subject_id)
    return new_count < limits['daily_new_limit']


def can_do_review(user_id, subject_id):
    """是否可以继续复习"""
    limits = get_study_limits(user_id, subject_id)
    _, review_count = get_daily_count(user_id, subject_id)
    return review_count < limits['daily_review_limit']
```

### 2.4 数据库变更

```sql
-- review_schedule 新增字段
ALTER TABLE review_schedule ADD COLUMN stability REAL DEFAULT 1.0;
ALTER TABLE review_schedule ADD COLUMN difficulty REAL DEFAULT 5.0;
ALTER TABLE review_schedule ADD COLUMN desired_retention REAL DEFAULT 0.9;

-- 每日上限配置表
CREATE TABLE IF NOT EXISTS study_limits (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    daily_new_limit INTEGER DEFAULT 10,
    daily_review_limit INTEGER DEFAULT 50,
    UNIQUE(user_id, subject_id)
);
```

### 2.5 代码变更清单

| 文件 | 变更类型 | 说明 | 行数 |
|------|----------|------|------|
| `models.py` | 新增 | FSRS 核心函数（7 个函数） | +150 |
| `models.py` | 修改 | `update_review_schedule` 改用 FSRS | ~60 改 |
| `models.py` | 修改 | `get_due_questions` 按保留率排序 | ~30 改 |
| `models.py` | 修改 | `is_question_mastered` 新标准 | ~20 改 |
| `models.py` | 修改 | `get_mastered_questions` SQL 条件 | ~20 改 |
| `models.py` | 新增 | `get_daily_count`, `can_do_new_question` | +50 |
| `models.py` | 修改 | `get_study_progress` 新增稳定性统计 | ~30 改 |
| `models.py` | 修改 | `get_retention_curve` 改用 FSRS 公式 | ~30 改 |
| `models.py` | 修改 | `get_subject_category_stats` 掌握条件 | ~20 改 |
| `migrate_fsrs.py` | 新增 | 数据迁移脚本 | +80 |
| `admin.py` | 新增 | 每日上限管理路由 | +60 |
| `templates/mastered.html` | 修改 | 文案 + badge 调整 | ~15 改 |
| `templates/study_setup.html` | 新增 | 每日上限提示 | ~20 新增 |

**总计**：~550 行代码变更。

### 2.6 "掌握"标准

| 旧标准（SM-2） | 新标准（FSRS） |
|---|---|
| `reps ≥ 3 AND ease ≥ 2.5 AND interval ≥ 15` | `stability ≥ 21 AND reps ≥ 5 AND difficulty ≤ 4.0` |

含义：
- `stability ≥ 21`：记忆稳定 21 天（3 周不复习仍有 90% 保留率）
- `reps ≥ 5`：至少复习 5 次
- `difficulty ≤ 4.0`：题目难度低

### 2.7 数据迁移

```python
# migrate_fsrs.py
def migrate():
    """从 history 表重建已有题目的记忆状态"""
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM review_schedule")
    for rs in cur.fetchall():
        user_id = rs['user_id']
        qid = rs['question_id']
        
        # 从 history 获取完整答题历史
        cur.execute("""
            SELECT correct, timestamp FROM history
            WHERE user_id = ? AND question_id = ?
            ORDER BY timestamp ASC
        """, (user_id, qid))
        history = cur.fetchall()
        
        if len(history) < 2:
            quality = rs['last_quality'] if rs['last_quality'] is not None else 2
            stability, difficulty = init_memory_state(quality)
        else:
            # 逐次复习重建
            stability, difficulty = init_memory_state(
                4 if history[0]['correct'] else 0
            )
            for i in range(1, len(history)):
                quality = 4 if history[i]['correct'] else 0
                delta_t = (history[i]['timestamp'] - history[i-1]['timestamp']) / 86400
                delta_t = max(delta_t, 0.01)
                stability = update_stability(quality, stability, difficulty, delta_t)
                difficulty = update_difficulty(quality, difficulty)
        
        interval = get_interval(stability)
        interval = apply_fuzz(interval)
        
        cur.execute("""
            UPDATE review_schedule
            SET stability = ?, difficulty = ?, interval = ?
            WHERE user_id = ? AND question_id = ?
        """, (round(stability, 2), round(difficulty, 2), interval,
              user_id, qid))
    
    conn.commit()
    conn.close()
```

---

## 三、P1 设计概要（后续迭代）

### 3.1 学习步骤（Learning Steps）

新题首次接触时进入学习阶段：

```
新题 → 第1步（答对）→ 第2步（答对）→ 进入正式复习
       ↓答错           ↓答错
      重做第1步        重做第2步
```

配置：
```python
LEARNING_STEPS = [1, 10]  # 1分钟, 10分钟
# 新题答对 → 1分钟后第2步 → 第2步答对 → 进入正式复习
# 任一步骤答错 → 重做当前步骤
```

### 3.2 重学机制（Relearning）

复习题答错后进入重学阶段：

```
复习题 → 答错 → 重学步骤（10分钟）→ 答对 → 重新进入正式复习
                                     ↓答错
                                    重做重学步骤
```

### 3.3 历史数据打通

将 `history` 表从"仅统计"升级为"调度数据源"：

```python
def rebuild_memory_state(user_id, question_id):
    """从 history 重建题目的记忆状态"""
    # 读取完整答题历史
    # 逐次应用 FSRS 更新
    # 返回最终 stability, difficulty
    pass
```

### 3.4 可配置目标保留率

```sql
ALTER TABLE subjects ADD COLUMN desired_retention REAL DEFAULT 0.9;
-- 难科目：0.95（间隔短，复习频繁）
-- 简单科目：0.8（间隔长，复习少）
```

---

## 四、P2 设计概要（长期规划）

### 4.1 工作负载预测

```python
def predict_workload(user_id, subject_id, days=30):
    """预测未来 N 天的每日复习量"""
    # 基于当前所有题目的 stability + interval
    # 模拟每天的复习 → 更新 stability → 计算下次到期
    # 返回 {date: review_count}
    pass
```

前端展示：日历热力图，显示未来 30 天每天的预期复习量。

### 4.2 负载均衡

Anki 的 LoadBalancer 思路：
- 维护 `due_cnt_per_day` 映射
- 调度时选择负载最低的日期
- 结合星期几的权重（"轻松日"分配更多）

简化版：
```python
def load_balanced_interval(interval, due_map):
    """在 [interval*0.9, interval*1.1] 范围内选择负载最低的日期"""
    best_day = None
    min_load = float('inf')
    for d in range(int(interval * 0.9), int(interval * 1.1) + 1):
        load = due_map.get(d, 0)
        if load < min_load:
            min_load = load
            best_day = d
    return best_day
```

### 4.3 最优保留率计算

基于历史数据计算最佳 DR：

```python
def compute_optimal_retention(user_id, subject_id):
    """
    基于历史答题数据，计算最优目标保留率
    - DR 太高 → 复习太多，效率低
    - DR 太低 → 遗忘太多，效果差
    找到平衡点
    """
    pass
```

### 4.4 科目独立预设

```sql
CREATE TABLE IF NOT EXISTS subject_presets (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL,
    desired_retention REAL DEFAULT 0.9,
    daily_new_limit INTEGER DEFAULT 10,
    daily_review_limit INTEGER DEFAULT 50,
    max_interval INTEGER DEFAULT 365,
    learning_steps TEXT DEFAULT '1,10',
    relearning_steps TEXT DEFAULT '10',
    UNIQUE(subject_id)
);
```

---

## 五、文件变更汇总

| 文件 | 操作 | 行数变化 | 阶段 |
|------|------|----------|------|
| `models.py` | 修改 | +350 新增, ~150 修改 | P0 |
| `migrate_fsrs.py` | 新增 | +80 | P0 |
| `admin.py` | 新增 | +60 | P0 |
| `templates/mastered.html` | 修改 | ~15 | P0 |
| `templates/study_setup.html` | 新增 | ~20 | P0 |
| `models.py` (P1) | 新增 | +200 | P1 |
| `models.py` (P2) | 新增 | +150 | P2 |
| `admin.py` (P2) | 新增 | +100 | P2 |

---

## 六、执行计划

### Phase 1: P0 实施（预计 1.5 小时）

| 步骤 | 内容 | 时间 |
|------|------|------|
| 1 | 数据库迁移（新增字段 + study_limits 表） | 5 分钟 |
| 2 | 备份数据库（自动） | 1 分钟 |
| 3 | models.py：新增 FSRS 核心函数 | 20 分钟 |
| 4 | models.py：改造 update_review_schedule | 15 分钟 |
| 5 | models.py：改造 get_due_questions（保留率排序） | 10 分钟 |
| 6 | models.py：改造 is_question_mastered + get_mastered_questions | 10 分钟 |
| 7 | models.py：新增每日上限函数 | 10 分钟 |
| 8 | models.py：改造统计函数 | 15 分钟 |
| 9 | admin.py：每日上限管理路由 | 10 分钟 |
| 10 | 模板文件调整 | 10 分钟 |
| 11 | 数据迁移脚本 | 10 分钟 |
| 12 | 重启服务 + 验证 | 10 分钟 |

### Phase 2: P1 实施（后续迭代，预计 2 小时）
- 学习步骤系统
- 重学机制
- 历史数据打通
- 可配置目标保留率

### Phase 3: P2 实施（长期规划，预计 3 小时）
- 工作负载预测
- 负载均衡
- 最优保留率计算
- 科目独立预设

---

## 七、验收标准

### P0 验收
1. ✅ 新题首次答题后，stability 和 difficulty 正确初始化
2. ✅ 已复习题目再次答题时，delta_t 正确参与稳定性计算
3. ✅ 答对后 interval 增长，答错后 interval 降低（不归零）
4. ✅ 到期题目按保留率排序（最易忘优先）
5. ✅ 每日新题/复习数达到上限后提示
6. ✅ is_question_mastered 使用新标准正确判断
7. ✅ 数据库迁移后，已有记录的 stability/difficulty 非空
8. ✅ 旧字段 ease_factor 仍可读（前端兼容）

### P1 验收
9. ✅ 新题经过学习步骤后才进入正式复习
10. ✅ 复习答错进入重学步骤，不是直接重置
11. ✅ 历史数据可用于重建记忆状态
12. ✅ 每科目可配置独立 DR

### P2 验收
13. ✅ 可预测未来 30 天每日复习量
14. ✅ 复习量分布均匀（无单日暴增）
15. ✅ 可计算最优保留率
16. ✅ 每科目有独立预设配置

---

## 八、风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 数据迁移出错 | 低 | 中 | 迁移前自动备份 |
| 新算法间隔"感觉奇怪" | 中 | 低 | Fuzz ±10% + 合理初始值 |
| 每日上限影响用户体验 | 中 | 中 | 默认值宽松，可随时调整 |
| 保留率排序导致"提前复习" | 低 | 低 | 到期题目优先，未到期的仅影响排序 |
| 前端展示异常 | 低 | 低 | 旧字段保留，前端兼容 |

---

## 九、与 Anki FSRS 的差异说明

| 特性 | Anki FSRS | 我们的实现 |
|------|-----------|-----------|
| 参数数量 | 17~21 个 | 0 个（硬编码公式，不训练） |
| 参数训练 | ML 训练 | 不需要（确定性公式） |
| 记忆状态 | Stability + Difficulty | 同左 |
| 短期记忆 | 单独处理 | P1 实现（学习步骤） |
| 负载均衡 | LoadBalancer（按周分布） | P2 实现（简化版） |
| 最优保留率 | 模拟器计算 | P2 实现 |
| 遗忘衰减 | 可配置参数 | 固定 0.9 |

**为什么不做完整的 FSRS？**
- 完整 FSRS 需要 Rust 编译的 `fsrs-rs` 库或 Python 的 `fsrs` pip 包
- 参数训练需要至少 400+ 条复习记录（我们当前只有 36 条）
- 轻量版用确定性公式，零依赖，开箱即用
- 后续数据积累后，可接入 `fsrs` pip 包做参数优化
