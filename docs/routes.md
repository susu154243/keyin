# 路由清单

## 用户端 (app.py)

### 认证

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET/POST | `/login` | `login()` | 否 | 登录 |
| GET/POST | `/register` | `register()` | 否 | 注册 |
| GET | `/logout` | `logout()` | 是 | 登出 |

### 科目

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/` | `index()` | 是 | 科目选择页 |
| GET | `/subjects/<subject_id>` | `subject_detail()` | 是 | 科目详情页 |

### 练习

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/practice` | `practice()` | 是 | 章节练习入口 |
| GET | `/subjects/<subject_id>/practice/<category_id>` | `practice_category()` | 是 | 按分类答题 |
| GET | `/subjects/<subject_id>/random` | `random_question()` | 是 | 随机答题 |

### 答题

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/question/<qid>` | `show_question()` | 是 | 展示题目 |
| POST | `/subjects/<subject_id>/question/<qid>` | `submit_answer()` | 是 | 提交答案 |
| POST | `/subjects/<subject_id>/rate/<qid>` | `rate_question()` | 是 | SM-2 评分 |

### 收藏/错题

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/favorites` | `show_favorites()` | 是 | 收藏列表 |
| POST | `/subjects/<subject_id>/favorite/<qid>` | `favorite_question()` | 是 | 收藏/取消 |
| GET | `/subjects/<subject_id>/wrong` | `wrong_questions()` | 是 | 错题本 |

### 历史真题

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/exams` | `exam_years()` | 是 | 年份列表 |
| GET | `/subjects/<subject_id>/exams/<year>` | `exam_by_year()` | 是 | 按年份答题 |
| POST | `/subjects/<subject_id>/exams/<year>/submit` | `submit_exam()` | 是 | 提交真题 |

### 模拟考试

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/mock` | `mock_exam()` | 是 | 模拟考试入口 |
| POST | `/subjects/<subject_id>/mock/start` | `start_mock_exam()` | 是 | 开始模拟考 |

### 统计分析

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET | `/subjects/<subject_id>/statistics` | `statistics()` | 是 | 统计页面 |
| GET | `/subjects/<subject_id>/stats/api` | `stats_api()` | 是 | 统计 JSON API |

### 兼容路由（重定向到首页）

| 方法 | 路径 | 函数 | 说明 |
|------|------|------|------|
| GET | `/sequential_start` | `sequential_start()` | 旧路由重定向 |
| GET | `/random_question` | `random_question_old()` | 旧路由重定向 |
| GET | `/show_history` | `show_history_old()` | 旧路由重定向 |

## 管理端 (admin.py)

URL 前缀: `/admin`

### 认证

| 方法 | 路径 | 函数 | 需要登录 | 说明 |
|------|------|------|----------|------|
| GET/POST | `/admin/login` | `login()` | 否 | 管理员登录 |
| GET | `/admin/logout` | `logout()` | 是 | 登出 |

### 仪表盘

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/` | `dashboard()` | 管理员 | 统计概览 |

### 用户管理

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/users` | `users()` | 管理员 | 用户列表 |
| GET/POST | `/admin/users/create` | `create_user_page()` | 管理员 | 创建用户 |
| POST | `/admin/users/<user_id>/toggle` | `toggle_user()` | 管理员 | 启用/禁用 |
| POST | `/admin/users/<user_id>/reset-password` | `reset_password()` | 管理员 | 重置密码 |

### 科目管理

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/subjects` | `subjects()` | 管理员 | 科目列表 |
| GET/POST | `/admin/subjects/create` | `create_subject_page()` | 管理员 | 创建科目 |
| POST | `/admin/subjects/<subject_id>/toggle` | `toggle_subject()` | 管理员 | 启用/禁用 |

### 分类管理

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/subjects/<subject_id>/categories` | `manage_categories()` | 管理员 | 分类树 |
| POST | `/admin/subjects/<subject_id>/categories/create` | `create_category_page()` | 管理员 | 创建分类 |
| POST | `/admin/categories/<category_id>/delete` | `delete_category_page()` | 管理员 | 删除分类 |

### 题目管理

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/questions` | `questions()` | 管理员 | 题目列表（分页+搜索） |
| GET/POST | `/admin/questions/create` | `create_question_page()` | 管理员 | 创建题目 |
| GET/POST | `/admin/questions/<qid>/edit` | `edit_question_page()` | 管理员 | 编辑题目 |
| POST | `/admin/questions/<qid>/delete` | `delete_question_page()` | 管理员 | 删除题目 |

### 权限管理

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET | `/admin/permissions` | `permissions()` | 管理员 | 权限概览 |
| GET | `/admin/permissions/<user_id>` | `user_permissions()` | 管理员 | 用户权限详情 |
| POST | `/admin/permissions/<user_id>/set` | `set_permissions()` | 管理员 | 设置权限 |

### 导入

| 方法 | 路径 | 函数 | 权限 | 说明 |
|------|------|------|------|------|
| GET/POST | `/admin/import` | `import_page()` | 管理员 | CSV 批量导入 |

## 错误处理

| 状态码 | 路径 | 说明 |
|--------|------|------|
| 404 | 任意 | 页面不存在 |
| 403 | 任意 | 无权访问 |
| 500 | 任意 | 服务器错误 |
