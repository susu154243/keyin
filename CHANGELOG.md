# EXAM-MASTER 变更日志

## v0.6.0 - 2026-04-29
**代码质量全面修复 + 文档生成**

### Bug 修复 (P0)
- 修复 `subject_detail` 路由变量作用域 Bug（管理员访问会 500）
- 修复 `show_question` 数据库连接泄漏
- 修复 SM-2 `get_due_questions` / `get_new_questions` 参数传递 Bug（category_id 冒充 subject_id）
- 修复 `submit_answer` 多选题正确性判断冗余（重复计算覆盖）
- 修复 `register` 路由密码加密不一致（直接使用 hashlib 而非 hash_password）

### 代码重构 (P1)
- app.py 中 16+ 处直连数据库全部改为使用 models.py 封装
- 新增 models.py 封装函数 15 个（get_subject_by_id, get_questions_count, get_real_exam_count, get_exam_years, get_user_subject_accuracy, get_next_question_id, get_questions_by_year, is_question_favorite, get_question_count_by_category, get_question_position_in_category, get_random_questions, get_sequential_questions, create_user, get_category）
- admin.py CSV 导入优化：一次性加载分类到内存，避免 N+1 查询
- admin.py `reset_password` / `delete_question_page` / `create_category_page` / `delete_category_page` 统一使用 models.py
- `get_review_progress` 函数签名扩展：新增 subject_id 参数
- `get_retention_curve` 修复：通过 questions 表关联 subject_id

### 文档 (P2)
- 新建 README.md（项目概述、快速开始、技术栈）
- 新建 docs/architecture.md（分层架构、模块职责、数据流、SM-2 算法、安全设计）
- 新建 docs/database.md（10 张表完整结构、ER 关系、注意事项）
- 新建 docs/routes.md（50+ 路由完整清单）
- 新建 docs/deployment.md（生产部署、备份、日志、故障排查）

## v0.5.1 - 2026-04-27
**清理题库与分类树**
- 清空 267 道题目
- 清空 268 条答题历史
- 清空 1 条复习计划
- 清空 22 条分类记录
- 保留科目"软考高项" (subject_id=1)
- 等待重新设计分类树和导入新题库

## v0.5 - 2026-04-27
**Anki 题库导入 + 版本追踪**
- 解析 .colpkg 格式题库 (Zstd + protobuf)
- 导入 267 道单选题 (含 31 张 Base64 图片)
- 清空旧测试题 (881 道)
- 新增 VERSION + CHANGELOG 版本追踪
- 初始化 Git 版本控制

## v0.4 - 2026-04-27
**统计可视化模块**
- 新增统计页面 `/subjects/<id>/statistics`
- 新增统计 API `/subjects/<id>/stats/api`
- 6 张概览卡片：连续天数/今日复习/待复习/7天正确率/累计复习/学习分钟
- 4 种图表：学习热力图 (90 天)/每日趋势双轴图/分类掌握度/保留率曲线
- 新增 models.py 统计函数：get_stats_summary, get_daily_trend, get_heatmap_data, get_category_mastery, get_retention_curve
- 依赖：Chart.js (CDN)

## v0.3 - 2026-04-27
**SM-2 间隔重复刷题**
- 新增 review_schedule 表 (用户 - 题目复习计划)
- SM-2 算法实现 (models.py: sm2_schedule, get_due_questions, get_new_questions)
- 答题页面新增 5 点评分界面 (😭😕😐😊🤩)
- 优先展示到期题目，不足补充新题
- 复习进度条 (总题数/已复习/待复习)
- 评分后自动跳转下一题

## v0.2 - 2026-04-27
**多租户 + 分类树**
- 数据库迁移：新增 subjects, categories, user_subjects 表
- 三级分类树 (科目 → 二级分类 → 三级知识点)
- 用户 - 科目权限控制 (@subject_required 中间件)
- 科目导航页面
- 修复 ON CONFLICT 错误 (改用 SELECT-then-INSERT/UPDATE)

## v0.1 - 2026-04-27
**初始部署**
- Flask + SQLite + Gunicorn + systemd + Nginx
- 管理后台独立页面 (admin Blueprint)
- 用户认证 (Flask session + @login_required, @admin_required)
- 答题模式：章节练习/随机答题/模拟考试/错题回顾
- 收藏/历史记录功能
- 管理功能：用户管理/科目管理/题目管理/导入导出
- 密码双兼容 (Werkzeug pbkdf2 + hashlib sha256)
