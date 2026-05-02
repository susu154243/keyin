# FSRS 评分系统迁移设计方案

> 2026-05-02 | 版本 v1.0 | 待审批

---

## 一、现状问题清单

| # | 问题 | 影响 | 根因 |
|---|------|------|------|
| 1 | 复习间隔不合理 | 到期数显示异常、复习节奏不佳 | SM-2 硬编码 `ease_factor`，不反映真实遗忘 |
| 2 | 答错后一刀切重置 | 用户挫败感强，repetitions 归零 | `quality < 2` 时直接 `new_reps = 0, new_interval = 0` |
| 3 | 不考虑复习时机 | 提前复习和 overdue 复习影响相同 | 评分只取 quality，不看 `delta_t`（距上次间隔） |
| 4 | "掌握"标准拍脑袋定 | 达标不代表真掌握 | `reps≥3 AND ease≥2.5 AND interval≥15` 无理论依据 |
| 5 | 复习洪峰 | 大量题目同一天到期 | 精确间隔，无 fuzz 偏移 |

---

## 二、算法设计（轻量版 FSRS）

### 2.1 核心概念

从 SM-2 的三元组 `(ease_factor, interval, repetitions)` 迁移为 FSRS 的二元组 `(stability, difficulty)` + 目标保留率。

| 概念 | 符号 | 范围 | 含义 |
|------|------|------|------|
| **稳定性** | S | 0 ~ ∞（天） | 记忆保持到 90% 保留率所需天数 |
| **难度** | D | 1.0 ~ 10.0 | 题目固有难度，答对降低，答错升高 |
| **目标保留率** | R | 0.7 ~ 0.95 | 用户期望的记忆保持率，默认 0.9 |
| **遗忘衰减** | decay | 固定 0.9 | FSRS 标准参数 |

### 2.2 核心公式

#### 2.2.1 间隔计算（核心公式）

根据遗忘曲线 `R(t) = e^(-decay × t / S)` 反推：

```python
def get_interval(stability, desired_retention=0.9):
    """由稳定性和目标保留率计算下次复习间隔（天）"""
    return stability * (-math.log(desired_retention) / 0.9)
```

**效果**：稳定性越高 → 间隔越长；目标保留率越高 → 间隔越短。

#### 2.2.2 稳定性更新

```python
def update_stability(quality, stability, difficulty, delta_t, desired_retention=0.9):
    """
    quality:   0~4（忘了/模糊/一般/简单/秒答）
    stability: 当前稳定性（天）
    difficulty: 当前难度（1~10）
    delta_t:   距上次复习的天数（>=0）
    """
    if delta_t <= 0:
        delta_t = 0.01  # 防止除零
    
    # 基础影响因子：评分越高影响越大
    quality_factor = (quality + 1) / 5.0  # 0.2 ~ 1.0
    
    if quality >= 2:  # 答对（一般/简单/秒答）
        # FSRS 核心思想：间隔越长，稳定性增长越多
        # 公式：S_new = S × (1 + 增益)
        # 增益 = 质量因子 × sqrt(实际间隔 / 当前稳定性)
        growth_ratio = math.sqrt(delta_t / stability) if stability > 0 else 1.0
        gain = quality_factor * 0.28 * growth_ratio
        new_stability = stability * (1 + gain)
    else:  # 答错（忘了/模糊）
        # 遗忘因子：稳定性越低、间隔越长，遗忘越严重
        forget_factor = (1 - quality / 2.0)  # 0.5(忘了) ~ 1.0(模糊)
        # 稳定性衰减：不是归零，而是按比例降低
        decay_ratio = 1.0 - 0.35 * forget_factor * min(delta_t / stability, 2.0) if stability > 0 else 0.5
        new_stability = stability * max(decay_ratio, 0.15)  # 最低保留 15%
    
    return max(new_stability, 0.1)  # 最低 0.1 天
```

**关键设计决策**：
- 答对时，`delta_t > stability` 表示"超出记忆极限答对"→ 增益大
- 答对时，`delta_t < stability` 表示"还没到遗忘时间"→ 增益小
- 答错时，不重置为 0，而是按比例降低，保留部分记忆基础

#### 2.2.3 难度更新

```python
def update_difficulty(quality, difficulty):
    """
    答对 → 难度降低（题目变"简单"）
    答错 → 难度升高（题目变"难"）
    变化幅度随当前难度递减（已经很简单的题不会再简单太多）
    """
    # 基础变化：-0.4 ~ +0.6
    delta = (2.0 - quality) * 0.3  # quality=4 → -0.6, quality=0 → +0.6
    
    # 边界衰减：接近极值时变化更慢
    if delta < 0:  # 降难度
        delta *= (difficulty - 1.0) / 9.0  # D=1 时不变，D=10 时全变
    else:  # 升难度
        delta *= (10.0 - difficulty) / 9.0  # D=10 时不变，D=1 时全变
    
    return max(1.0, min(10.0, difficulty + delta))
```

#### 2.2.4 首次初始化

新题首次答题时：

```python
def init_memory_state(quality):
    """首次答题后初始化记忆状态"""
    # 初始稳定性：基于评分质量
    stability_map = {
        0: 0.5,   # 忘了 → 0.5天（12小时后复习）
        1: 1.0,   # 模糊 → 1天
        2: 2.0,   # 一般 → 2天
        3: 4.0,   # 简单 → 4天
        4: 7.0,   # 秒答 → 7天
    }
    # 初始难度：基于答题表现
    difficulty_map = {
        0: 8.0,  # 忘了 → 很难
        1: 6.5,  # 模糊 → 较难
        2: 5.0,  # 一般 → 中等
        3: 3.5,  # 简单 → 较易
        4: 2.0,  # 秒答 → 容易
    }
    return stability_map[quality], difficulty_map[quality]
```

#### 2.2.5 Fuzz（复习间隔随机偏移）

```python
def apply_fuzz(interval):
    """给复习间隔添加 ±10% 的随机偏移，避免洪峰"""
    if interval <= 1:
        return interval  # 1天以内不 fuzz
    fuzz_range = max(1, int(interval * 0.1))  # 最小 ±1 天
    return max(1, interval + random.randint(-fuzz_range, fuzz_range))
```

### 2.3 完整调度流程

```
用户答题 → 评分 quality (0~4)
    │
    ├─ 查询 review_schedule
    │   ├─ 有记录 → 读取 stability, difficulty, last_review
    │   │          计算 delta_t = now - last_review (天)
    │   │          update_stability(quality, stability, difficulty, delta_t)
    │   │          update_difficulty(quality, difficulty)
    │   │          interval = get_interval(new_stability)
    │   │          interval = apply_fuzz(interval)
    │   │          next_review = now + interval 天
    │   │
    │   └─ 无记录 → init_memory_state(quality)
    │               interval = get_interval(stability)
    │               next_review = now + interval 天
    │
    └─ 写入 review_schedule（更新 stability, difficulty, interval, next_review）
```

---

## 三、数据库变更

### 3.1 review_schedule 表新增字段

```sql
ALTER TABLE review_schedule ADD COLUMN stability REAL DEFAULT 1.0;
ALTER TABLE review_schedule ADD COLUMN difficulty REAL DEFAULT 5.0;
ALTER TABLE review_schedule ADD COLUMN desired_retention REAL DEFAULT 0.9;
```

**保留旧字段**（向后兼容，不删除）：
- `ease_factor` — 保留，前端仍展示（标注为"历史数据"）
- `interval` — 保留，存储 FSRS 计算出的间隔（语义不变）
- `repetitions` — 保留，记录复习总次数（语义不变，但不再用于掌握判断）

### 3.2 迁移脚本

```python
# migrate_fsrs.py
def migrate():
    """从 history 表重建已有题目的记忆状态"""
    conn = get_db()
    cur = conn.cursor()
    
    # 对每条 review_schedule 记录
    cur.execute("SELECT * FROM review_schedule")
    for rs in cur.fetchall():
        user_id = rs['user_id']
        qid = rs['question_id']
        
        # 从 history 获取该用户的完整答题历史
        cur.execute("""
            SELECT correct, timestamp FROM history
            WHERE user_id = ? AND question_id = ?
            ORDER BY timestamp ASC
        """, (user_id, qid))
        history = cur.fetchall()
        
        if len(history) < 2:
            # 答题记录不足，用当前记录初始化
            quality = rs['last_quality'] if rs['last_quality'] is not None else 2
            stability, difficulty = init_memory_state(quality)
        else:
            # 从历史重建
            stability, difficulty = init_memory_state(
                4 if history[0]['correct'] else 0
            )
            for i in range(1, len(history)):
                quality = 4 if history[i]['correct'] else 0
                delta_t = (history[i]['timestamp'] - history[i-1]['timestamp']) / 86400
                delta_t = max(delta_t, 0.01)
                stability = update_stability(quality, stability, difficulty, delta_t)
                difficulty = update_difficulty(quality, difficulty)
        
        # 计算新的 interval 和 next_review
        interval = get_interval(stability)
        interval = apply_fuzz(interval)
        
        cur.execute("""
            UPDATE review_schedule
            SET stability = ?, difficulty = ?, interval = ?,
                next_review = ?
            WHERE user_id = ? AND question_id = ?
        """, (round(stability, 2), round(difficulty, 2), interval,
              rs['next_review'], user_id, qid))
    
    conn.commit()
    conn.close()
```

---

## 四、代码变更清单

### 4.1 models.py

| 变更类型 | 位置 | 说明 |
|----------|------|------|
| **新增** | 文件头部 import | `import math, random` |
| **新增** | ~line 772 前 | FSRS 核心函数：`update_stability`, `update_difficulty`, `get_interval`, `init_memory_state`, `apply_fuzz`, `fsrs_schedule` |
| **修改** | `sm2_schedule` | 保留函数，但标记为 deprecated（向后兼容） |
| **修改** | `update_review_schedule` (~line 937) | 核心改造：用 FSRS 替代 SM-2 计算 |
| **修改** | `is_question_mastered` (~line 898) | 判断条件改为：`stability >= 21 AND repetitions >= 5 AND difficulty <= 4.0` |
| **修改** | `get_mastered_questions` (~line 1503) | SQL WHERE 条件同步更新 |
| **修改** | `get_study_progress` (~line 1027) | 新增"平均稳定性"统计 |
| **修改** | `get_retention_curve` (~line 1325) | 改用 FSRS 遗忘曲线公式 |
| **修改** | `get_subject_category_stats` (~line 1612) | "已掌握" SQL 条件同步更新 |
| **新增** | 文件末尾 | `migrate_existing_records()` — 数据迁移函数 |

### 4.2 admin.py

| 变更类型 | 说明 |
|----------|------|
| 无需修改 | 新增的 admin 页面不直接引用算法字段 |

### 4.3 模板文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `templates/mastered.html` | 修改文案 | "SM-2 掌握标准" → "FSRS 掌握标准"，新增 stability/difficulty badge |
| `templates/study_setup.html` | 无需修改 | 不展示算法内部字段 |

### 4.4 app.py

| 位置 | 变更 | 说明 |
|------|------|------|
| ~line 423 | 无需修改 | `update_review_schedule` 调用方式不变 |
| ~line 655 | 无需修改 | 同上 |
| ~line 739-741 | 无需修改 | `get_review_schedule` 返回格式兼容 |
| ~line 1054-1064 | 需确认 | dashboard 统计中的"已掌握"SQL 通过 models.py 间接调用，自动适配 |

---

## 五、"掌握"标准重新定义

### 旧标准（SM-2）
```
repetitions >= 3 AND ease_factor >= 2.5 AND interval >= 15
```
问题：间隔 15 天不代表真的记住了，只是算法安排得远。

### 新标准（FSRS）
```
stability >= 21 AND repetitions >= 5 AND difficulty <= 4.0
```
含义：
- `stability >= 21`：记忆稳定性能保持 21 天（3 周不复习仍有 90% 保留率）
- `repetitions >= 5`：至少复习 5 次（比原来的 3 次更可靠）
- `difficulty <= 4.0`：题目难度低（用户对该题确实有把握）

**效果**：达到此标准的题目，下次复习间隔至少 21 天，且题目对用户来说不难。

---

## 六、评分质量映射（保持现有 0-4 体系）

| 评分 | 标签 | 语义 | FSRS 影响 |
|------|------|------|-----------|
| 0 | 忘了 | 完全想不起来 | 稳定性降 35%~65%，难度 +0.6 |
| 1 | 模糊 | 有印象但不确定 | 稳定性降 15%~35%，难度 +0.3 |
| 2 | 一般 | 想起来了 | 稳定性小幅增长，难度 -0.1 |
| 3 | 简单 | 轻松答对 | 稳定性中幅增长，难度 -0.4 |
| 4 | 秒答 | 条件反射 | 稳定性大幅增长，难度 -0.6 |

---

## 七、执行计划

### Phase 1：数据库迁移（~5 分钟）
```bash
cd /keyin
python3 migrate_fsrs.py
# 验证：SELECT stability, difficulty, interval FROM review_schedule LIMIT 5;
```

### Phase 2：算法替换（~30 分钟编码）
1. 在 models.py 中新增 FSRS 核心函数
2. 修改 `update_review_schedule` 改用 FSRS
3. 修改 `is_question_mastered` / `get_mastered_questions` 判断条件
4. 修改 `get_study_progress` 统计
5. 修改 `get_retention_curve`

### Phase 3：前端适配（~10 分钟）
1. 更新 mastered.html 文案
2. 新增 stability / difficulty 展示 badge

### Phase 4：测试验证
1. 模拟答题：检查新题首次评分后的 interval 是否合理
2. 模拟复习：检查 delta_t 对 stability 的影响是否符合预期
3. 检查"掌握"页面：确认新标准下的题目列表正确
4. 检查分类统计：待复习数、已掌握数是否正确

---

## 八、风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 数据迁移出错 | 低 | 中 | 迁移前自动备份 database.db |
| 新算法间隔"感觉奇怪" | 中 | 低 | 添加 Fuzz ±10%，增加自然感 |
| 老用户看到间隔变化 | 中 | 低 | 当前只有 36 条记录，影响极小 |
| 稳定性公式参数不准 | 中 | 中 | 参数可配置，后续可根据 history 数据微调 |
| 前端展示异常 | 低 | 低 | 旧字段保留，前端正常读取 |

---

## 九、与 Anki FSRS 的差异

| 特性 | Anki FSRS | 我们的简化版 |
|------|-----------|-------------|
| 参数数量 | 17~21 个 | 0 个（硬编码公式，不训练） |
| 参数拟合 | 从历史数据 ML 训练 | 不需要（公式直接计算） |
| 记忆状态 | Stability + Difficulty | 同左 |
| 短期记忆 | 单独处理（日内多次复习） | 暂不处理（interval=0 即当天复习） |
| 负载均衡 | LoadBalancer（按周分布） | Fuzz ±10% |
| 最优保留率 | 通过模拟器计算 | 固定 0.9 |
| 遗忘衰减 | 可配置参数 | 固定 0.9 |

**为什么不做完整的 FSRS？**
- 完整 FSRS 需要 Rust 编译的 `fsrs-rs` 库或 Python 的 `fsrs` pip 包
- 参数训练需要大量历史数据（至少 400+ 条复习记录）
- 我们当前只有 228 条 history，不足以训练
- 轻量版用确定性公式，零依赖，开箱即用
- 后续数据积累后，可以接入 `fsrs` pip 包做参数优化

---

## 十、文件变更汇总

| 文件 | 操作 | 行数变化 |
|------|------|----------|
| `/keyin/models.py` | 修改 | +200 行（新增 FSRS 函数），~80 行修改 |
| `/keyin/migrate_fsrs.py` | 新增 | ~80 行（数据迁移脚本） |
| `/keyin/templates/mastered.html` | 修改 | ~15 行（文案 + badge） |
| `/keyin/database.db.bak.*` | 自动备份 | — |

**预计总工时**：45 分钟编码 + 15 分钟测试

---

## 十一、验收标准

1. ✅ 新题首次答题后，`stability` 和 `difficulty` 正确初始化
2. ✅ 已复习题目再次答题时，`delta_t` 正确参与稳定性计算
3. ✅ 答对后 interval 增长，答错后 interval 降低（不归零）
4. ✅ `is_question_mastered` 使用新标准正确判断
5. ✅ 分类统计页面的"待复习"和"已掌握"数据准确
6. ✅ 已掌握页面展示 stability / difficulty badge
7. ✅ 数据库迁移后，已有 36 条记录的 stability/difficulty 非空
8. ✅ 旧字段 `ease_factor` 仍可读（前端兼容）
