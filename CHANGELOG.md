# 刻印 (KeyIn) 变更日志

## v0.8.1 - 2026-04-30
**全量修复：10项 P0/P1/P2 问题**

### P0 Bug 修复
- 修复"下一题"按钮跳过队列 → 改为通过 /next 路由统一管理
- 防止直接URL绕过队列 → chapter_practice_qid 增加队列校验
- 旧路由 practice_category 重定向到 setup，避免混乱

### P1 体验优化
- 增加进度显示：进度条 + 已完成/剩余计数
- 重做题标识：⚠️ 第X次作答 + 导航橙色标记
- 考试错题自动加入 SM-2 复习计划

### P2 完善
- 会话丢失恢复：setup 页面显示"继续上次练习"按钮
- 统计页面增加 SM-2 掌握度概览（已掌握/待复习/新题）
- practice_setup 显示分类学习进度（已掌握数、待复习数）
- 多选题部分正确判定 → 自动映射 quality=1（模糊）

## v0.8.0 - 2026-04-30
**SM-2 深度集成：队列调度 + 自动评分 + 五档复习策略**

### 核心改进
- 练习模式采用**会话内队列调度**：答对移出队列，答错放回队尾（最多5次重试）
- **五档评分策略**：忘了(0)/模糊(1)/一般(2)/简单(3)/秒答(4)，每档对应不同的会话内处理和 SM-2 间隔
- **跳过评分自动映射**：答对首次=简单(3)，答对重试=一般(2)，答错=模糊(1)
- 新增**练习总结页**：展示正确率、顽固题、下次复习安排
- 修复 `get_due_questions` 的 subject_id 硬编码问题
- 新增 `is_question_mastered()` 和 `get_review_schedule()` 函数

### 五档评分策略
| 评分 | 会话内处理 | SM-2 间隔 | 下次复习 |
|------|-----------|----------|--------|
| 😭 忘了(0) | 重做，最多5次 | 1天 | 明天 |
| 😕 模糊(1) | 重做1次 | 2天 | 2天后 |
| 😐 一般(2) | 移出队列 | 4天 | 4天后 |
| 😊 简单(3) | 移出队列 | 10天 | 10天后 |
| 🤩 秒答(4) | 移出队列 | 30天 | 30天后 |

### 新增路由
- `GET /practice/<cat>/practice/next` - 从队列取下一题
- `POST /practice/<cat>/practice/<qid>/skip` - 跳过评分，自动映射

### 新增模板
- `chapter_practice_summary.html` - 练习总结页（正确率/顽固题/复习安排）

## v0.7.0 - 2026-04-30
**章节练习模式选择：考试模式 + 练习模式 + 题量选择 + 题目导航**

### 新增功能
- 三级分类点击后进入「练习设置页」，可选择模式与题量
- **考试模式**：全部题目一页显示，提交后显示成绩、逐题解析、正确率
- **练习模式**：逐题作答，答完立即显示答案与解析，题干选项不隐藏
- 两种模式均支持题目导航（题号按钮，颜色标识状态）
- 题量选择：支持自定义题量 + 快捷按钮（10/20/30/50/全部）
- 练习模式保留 SM-2 评分（答完题后评分，然后跳下一题）

### 新增路由
- `GET /subjects/<id>/practice/<cat_id>/setup` - 练习设置页
- `GET /subjects/<id>/practice/<cat_id>/exam` - 考试模式答卷
- `POST /subjects/<id>/practice/<cat_id>/exam/submit` - 提交考试
- `GET /subjects/<id>/practice/<cat_id>/practice` - 练习模式起始
- `GET /subjects/<id>/practice/<cat_id>/practice/<qid>` - 练习模式-指定题
- `POST /subjects/<id>/practice/<cat_id>/practice/<qid>/answer` - 练习模式-提交答案
- `POST /subjects/<id>/practice/<cat_id>/practice/<qid>/rate` - 练习模式-SM-2评分

### 新增模板
- `practice_setup.html` - 模式选择+题量选择
- `chapter_exam.html` - 考试模式
- `chapter_practice.html` - 练习模式

### 修改
- `practice.html` / `subject_detail.html`：三级分类链接改指向 setup 页
- 原有 `show_question` / `practice_category` 路由保持不变

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
