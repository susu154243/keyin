# 数据库文档

## 概述

- **数据库类型**: SQLite 3
- **文件路径**: `/exam-master/database.db`
- **外键**: 已启用 (`PRAGMA foreign_keys = ON`)
- **表数量**: 10

## 表结构

### users（用户表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 用户 ID |
| username | TEXT | UNIQUE NOT NULL | 用户名 |
| password_hash | TEXT | NOT NULL | 密码哈希 (SHA-256) |
| role | TEXT | DEFAULT 'user' | 角色: admin / user |
| status | INTEGER | DEFAULT 1 | 状态: 1=启用, 0=禁用 |
| last_login | DATETIME | | 最后登录时间 |
| current_seq_qid | TEXT | | 顺序答题当前位置 |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### subjects（科目表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 科目 ID |
| name | TEXT | NOT NULL | 科目名称 |
| code | TEXT | UNIQUE NOT NULL | 科目代码（唯一标识） |
| description | TEXT | DEFAULT '' | 描述 |
| icon | TEXT | DEFAULT '📚' | 图标 Emoji |
| status | INTEGER | DEFAULT 1 | 状态: 1=启用, 0=禁用 |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### categories（分类表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 分类 ID |
| subject_id | INTEGER | NOT NULL | 所属科目 |
| parent_id | INTEGER | DEFAULT 0 | 父分类 ID（0 表示根级） |
| name | TEXT | NOT NULL | 分类名称 |
| level | INTEGER | NOT NULL DEFAULT 1 | 层级: 1/2/3 |
| sort_order | INTEGER | DEFAULT 0 | 排序序号 |

**层次结构**: 科目 → 一级分类 → 二级分类 → 三级分类

### questions（题目表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | TEXT | PRIMARY KEY | 题目 ID（字符串，兼容旧版） |
| stem | TEXT | NOT NULL | 题干 |
| answer | TEXT | NOT NULL | 正确答案 |
| options | TEXT | | 选项（JSON 字符串） |
| explanation | TEXT | DEFAULT '' | 解析 |
| qtype | TEXT | | 题型代码: single / multiple |
| qtype_text | TEXT | DEFAULT '单选题' | 题型文本 |
| difficulty | TEXT | | 难度 |
| subject_id | INTEGER | DEFAULT 1 | 所属科目 |
| category_id | INTEGER | REFERENCES categories(id) | 所属分类 |
| is_real_exam | INTEGER | DEFAULT 0 | 是否真题 |
| exam_year | INTEGER | | 真题年份 |
| source | TEXT | DEFAULT 'practice' | 来源: practice/exam/mock/daily |
| status | INTEGER | DEFAULT 1 | 状态: 1=启用, 0=禁用 |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### history（答题历史表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 记录 ID |
| user_id | INTEGER | NOT NULL, FK→users(id) | 用户 |
| question_id | TEXT | NOT NULL | 题目 ID |
| user_answer | TEXT | NOT NULL | 用户答案 |
| correct | INTEGER | NOT NULL | 是否正确: 1/0 |
| subject_id | INTEGER | DEFAULT 1 | 科目 ID |
| timestamp | DATETIME | DEFAULT CURRENT_TIMESTAMP | 答题时间 |

### favorites（收藏表）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 记录 ID |
| user_id | INTEGER | NOT NULL, FK→users(id) | 用户 |
| question_id | TEXT | NOT NULL, FK→questions(id) | 题目 ID |
| subject_id | INTEGER | DEFAULT 1 | 科目 ID |
| tag | TEXT | | 标签（预留） |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | 收藏时间 |

**唯一约束**: `UNIQUE(user_id, question_id)`

### review_schedule（SM-2 复习计划表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | INTEGER | 用户 ID |
| question_id | INTEGER | 题目 ID |
| subject_id | INTEGER | 科目 ID |
| ease_factor | REAL | 难度系数（初始 2.5，最小 1.3） |
| interval | INTEGER | 复习间隔天数 |
| repetitions | INTEGER | 连续正确次数 |
| next_review | DATETIME | 下次复习时间 |
| last_review | DATETIME | 上次复习时间 |

### user_subjects（用户-科目权限表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | INTEGER | 用户 ID |
| subject_id | INTEGER | 科目 ID |
| can_practice | INTEGER | 章节练习权限 |
| can_mock | INTEGER | 模拟考试权限 |
| can_daily | INTEGER | 每日练习权限 |
| can_manage | INTEGER | 管理权限 |

### exam_sessions（考试会话表，预留）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| user_id | INTEGER | 用户 ID |
| mode | TEXT | exam / timed |
| question_ids | TEXT | 题目 ID 列表 (JSON) |
| start_time | DATETIME | 开始时间 |
| duration | INTEGER | 时长（秒） |
| completed | BOOLEAN | 是否完成 |
| score | REAL | 得分 |

> ⚠️ 该表已创建但未被代码使用，为后续功能预留。

## ER 关系

```
users ──1:N── history
users ──1:N── favorites
users ──1:N── review_schedule
users ──1:N── user_subjects
users ──1:N── exam_sessions

subjects ──1:N── categories
subjects ──1:N── questions
subjects ──1:N── user_subjects

categories ──1:N── categories (自引用，parent_id)
categories ──1:N── questions

questions ──1:N── history
questions ──1:N── favorites
questions ──1:N── review_schedule
```

## 注意事项

1. **questions.id 类型**: 为 TEXT 类型（兼容旧版 UUID），review_schedule.question_id 为 INTEGER，两者类型不一致。新题目应使用 INTEGER。
2. **软删除**: 题目和科目使用 `status = 0` 标记删除，不物理删除。
3. **JSON 字段**: questions.options 存储为 JSON 字符串，需使用 `json.loads()` 解析。
