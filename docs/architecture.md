# 架构设计文档

## 分层架构

```
┌──────────────────────────────────────────────────────┐
│                   表现层 (Templates)                  │
│  Jinja2 模板 (33 个) + Chart.js + CSS                │
├──────────────────────────────────────────────────────┤
│                   路由层 (Routes)                     │
│  app.py (用户端)    admin.py (管理端)                  │
├──────────────────────────────────────────────────────┤
│                   中间件层 (Middleware)               │
│  auth.py: @login_required, @admin_required           │
├──────────────────────────────────────────────────────┤
│                   数据模型层 (Models)                 │
│  models.py: 所有数据库操作封装                         │
├──────────────────────────────────────────────────────┤
│                   持久层 (Database)                   │
│  SQLite (database.db)                                │
└──────────────────────────────────────────────────────┘
```

## 模块职责

### app.py（用户端路由）

| 路由组 | 路径前缀 | 功能 |
|--------|----------|------|
| 认证 | `/login`, `/register`, `/logout` | 用户注册/登录 |
| 科目 | `/` , `/subjects/<id>` | 科目列表、科目详情 |
| 练习 | `/subjects/<id>/practice` | 章节练习入口 |
| 答题 | `/subjects/<id>/question/<qid>` | 单题展示/提交 |
| SM-2 | `/subjects/<id>/rate/<qid>` | 间隔重复评分 |
| 收藏 | `/subjects/<id>/favorites` | 收藏列表 |
| 错题 | `/subjects/<id>/wrong` | 错题回顾 |
| 真题 | `/subjects/<id>/exams` | 历史真题按年份 |
| 模拟 | `/subjects/<id>/mock` | 模拟考试 |
| 统计 | `/subjects/<id>/statistics` | 数据可视化 |

### admin.py（管理端路由）

| 路由组 | 路径前缀 | 功能 |
|--------|----------|------|
| 登录 | `/admin/login` | 管理员登录 |
| 仪表盘 | `/admin/` | 统计数据概览 |
| 用户 | `/admin/users` | 用户 CRUD |
| 科目 | `/admin/subjects` | 科目 CRUD |
| 分类 | `/admin/subjects/<id>/categories` | 分类树管理 |
| 题目 | `/admin/questions` | 题目 CRUD + 搜索 |
| 权限 | `/admin/permissions` | 用户-科目权限分配 |
| 导入 | `/admin/import` | CSV 批量导入 |

### models.py（数据模型）

按功能域分组：

| 域 | 函数 | 说明 |
|----|------|------|
| 用户 | `authenticate_user`, `create_user`, `get_user_by_id`, `get_all_users`, `update_user_status`, `update_user_last_login`, `hash_password` | 认证与用户管理 |
| 科目 | `get_all_subjects`, `get_all_subjects_admin`, `get_subject`, `get_subject_by_id`, `create_subject`, `update_subject`, `get_questions_count`, `get_real_exam_count`, `get_exam_years` | 科目信息 |
| 分类 | `get_categories_tree`, `get_category`, `get_leaf_categories`, `create_category`, `delete_category`, `get_question_count_by_category`, `get_question_position_in_category` | 三级分类树 |
| 题目 | `get_question`, `get_questions_by_category`, `get_questions_by_subject`, `create_question`, `update_question`, `delete_question`, `get_random_questions`, `get_sequential_questions`, `get_questions_by_year`, `get_next_question_id` | 题目 CRUD 与查询 |
| 权限 | `set_user_subject_permission`, `get_user_permissions`, `get_all_subjects_for_permission`, `get_user_subjects`, `get_user_subject_accuracy` | 用户-科目权限 |
| 历史 | `save_answer`, `get_user_history`, `get_user_wrong_questions` | 答题记录 |
| 收藏 | `get_user_favorites`, `toggle_favorite`, `is_question_favorite` | 收藏功能 |
| SM-2 | `sm2_schedule`, `get_due_questions`, `get_new_questions`, `get_review_progress`, `update_review_schedule` | 间隔重复算法 |
| 统计 | `get_stats_summary`, `get_daily_trend`, `get_heatmap_data`, `get_category_mastery`, `get_retention_curve` | 数据分析 |

### auth.py（认证中间件）

| 装饰器 | 功能 |
|--------|------|
| `@login_required` | 检查 session 中是否存在 user_id |
| `@admin_required` | 检查用户角色是否为 admin |
| `@subject_required` | 检查用户是否有科目访问权限（备用） |
| `get_current_user()` | 从 session 获取当前用户对象 |

## 数据流

### 答题流程

```
用户点击分类 → practice_category()
    ↓
get_due_questions() → 获取到期复习题
    ↓ (无到期题)
get_new_questions() → 获取新题
    ↓ (无新题)
get_sequential_questions() → 兜底顺序出题
    ↓
show_question() → 展示题目
    ↓
submit_answer() → 提交答案 + save_answer()
    ↓
rate_question() → SM-2 评分 + update_review_schedule()
    ↓
获取下一题 → 循环
```

### SM-2 算法

```
评分 (0-5) → sm2_schedule(quality, ease_factor, interval, repetitions)
    ↓
计算新的 ease_factor、interval、repetitions
    ↓
next_review = now + interval 天
    ↓
写入 review_schedule 表
```

## 安全设计

- **Session**：Flask 加密 session（需配置 SECRET_KEY 环境变量）
- **密码**：SHA-256 哈希存储，兼容旧版 Werkzeug pbkdf2
- **SQL 注入**：全部使用参数化查询 (`?` 占位符)
- **权限**：路由级装饰器 + 手动权限检查双重保护
