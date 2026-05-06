# KeyIn 项目代码质量审查报告

**生成日期**: 2026-05-05
**审查范围**: `/keyin/` 项目全部源代码文件（Python 后端 + JS 前端 + HTML 模板）
**审查文件数**: 5 个 Python 源文件 + 1 个 JS 文件 + ~36 个 HTML 模板

---

## 一、安全漏洞（高危）

### 1. 🔴 安全答案明文存储
- **文件**: `models.py` — `set_user_security()` / `check_security_answer()`
- **严重级别**: 🔴 高
- **问题**: 用户密码找回的安全问题和答案以明文存储在 `users` 表的 `security_question` 和 `security_answer` 列。数据库泄露后攻击者可直接重置任意用户密码。
- **修复建议**: 对 `security_answer` 使用 `hash_password()` 进行哈希存储，验证时比对哈希值。`security_question` 可从预定义选项中选择（存索引号而非明文）。

### 2. 🔴 模板使用 `|safe` 渲染用户可控内容（XSS 风险）
- **文件**: `templates/question.html:62,76,102`, `templates/exam.html:34,45`, `templates/chapter_exam.html:29,39`, `templates/chapter_practice.html:40,54,70,85`, `templates/index.html:11`, `templates/admin/settings.html:46`
- **严重级别**: 🔴 高
- **问题**: 题干（`stem`）、选项值（`opt_val`）、解析（`explanation`）、首页欢迎语（`welcome_title`）均使用 `|safe` 过滤器，跳过了 Jinja2 的自动转义。虽然这些内容目前主要由管理员导入，但若管理员账号被入侵或内部人员恶意操作，可在所有用户浏览器中执行任意 JavaScript。
- **修复建议**: 
  - 移除 `|safe`，改用 `bleach` 库进行白名单 HTML 清理（仅保留 `<br>`, `<img>`, `<b>`, `<i>` 等安全标签）
  - 或在导入时对题干/解析做 HTML 净化处理，确保只保留安全的格式标签

### 3. 🔴 缺失 CSRF 保护
- **文件**: `app.py`, `admin.py`（全部表单提交路由）
- **严重级别**: 🔴 高
- **问题**: 所有 POST 表单（登录、注册、密码重置、题目管理、邀请码管理等）均未使用 CSRF Token。攻击者可构造恶意页面，诱导已登录用户点击后执行非预期操作（如删除题目、修改权限）。
- **修复建议**: 安装 `flask-wtf` 或 `flask-seasurf`，为所有表单添加 CSRF 验证。Flask-Limiter 不替代 CSRF 保护。

### 4. 🟡 图片上传仅验证扩展名，未验证文件内容
- **文件**: `app.py` — `upload_image()` 路由
- **严重级别**: 🟡 中
- **问题**: 仅通过文件扩展名（`.png`, `.jpg` 等）判断文件类型，未使用 `magic` 字节检测。攻击者可上传伪装成图片的恶意脚本文件。
- **修复建议**: 使用 `python-magic` 库验证文件 MIME 类型，确保实际内容为图片格式。

### 5. 🟡 密码重置路由缺失速率限制
- **文件**: `app.py` — `forgot_password()` 和 `reset_password_page()`
- **严重级别**: 🟡 中
- **问题**: 虽然密码重置路由使用了 `@limiter.limit("3 per hour")`，但 `POST` 和 `GET` 共享同一限制。GET 请求消耗配额后，真正的重置请求会被拒绝。
- **修复建议**: 分别为 GET 和 POST 设置独立的速率限制，或仅对 POST 方法应用限制。

### 6. 🟡 SESSION_COOKIE_SECURE 在生产环境外可能导致问题
- **文件**: `app.py` 第 64 行
- **严重级别**: 🟡 低
- **问题**: `SESSION_COOKIE_SECURE = True` 要求 HTTPS。如果 Nginx 未正确配置 HTTPS 或开发环境下使用 HTTP，session cookie 将不会被浏览器发送。
- **修复建议**: 根据环境变量动态设置：`app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'`

---

## 二、潜在缺陷（Bug）

### 7. 🔴 路由名称不匹配导致 BuildError
- **文件**: `admin.py` — `admin_reset_user_password()` 第 941 行, `admin_code_logs()` 第 918 行
- **严重级别**: 🔴 高
- **问题**: 
  - `admin_reset_user_password()` 中 `redirect(url_for('admin.admin_users'))` — 实际路由名为 `admin.users`，会抛出 `BuildError`。
  - `admin_code_logs()` 中同样的错误引用了 `admin.admin_users`。
- **修复建议**: 将 `url_for('admin.admin_users')` 改为 `url_for('admin.users')`。

### 8. 🟡 变量名遮蔽导致逻辑错误
- **文件**: `models.py` — `get_question_attempt_stats()` 第 1661-1666 行
- **严重级别**: 🟡 中
- **问题**: 函数顶部 `from datetime import datetime as dt`，然后在循环内部 `now = dt.now()` 覆盖了外部同名变量。虽然此处使用了 `dt` 别名避免了与模块级 `datetime` 的冲突，但 `now` 变量被重复赋值，在循环内 `now` 的值会随每次迭代变化（但实际差异极小）。
- **修复建议**: 将 `now = dt.now()` 移到循环外部，只需获取一次当前时间。

### 9. 🟡 chapter_exam_submit 重复计算 is_correct
- **文件**: `app.py` — `chapter_exam_submit()` 第 735-738 行
- **严重级别**: 🟡 中
- **问题**: `is_correct` 在第 735 行计算后，第 738 行又重复计算了一次。第二次计算结果覆盖了第一次，虽逻辑相同但浪费性能。
- **修复建议**: 删除第 738 行的重复计算，复用第 735 行的 `is_correct` 变量。

### 10. 🟡 admin_code_logs 低效查询
- **文件**: `admin.py` — `admin_code_logs()` 第 914-922 行
- **严重级别**: 🟡 低
- **问题**: 已有 `get_invitation_code(code_id)` 函数可用，却查询全部 1000 条记录再遍历查找目标，效率低下。
- **修复建议**: 直接使用 `get_invitation_code(code_id)` 或 `list_invitation_codes(page=1, per_page=1)` 替代。

### 11. 🟡 loadCommentWall 在 commentBtn 可能为 null 时调用
- **文件**: `static/question-features.js` 第 124 行
- **严重级别**: 🟡 低
- **问题**: `loadCommentWall(1)` 在 `DOMContentLoaded` 时立即调用，但 `commentBtn` 可能为 `null`（如果页面没有留言按钮）。函数内部使用 `commentBtn.dataset.url` 会抛出 TypeError。
- **修复建议**: 在 `loadCommentWall` 开头添加 `if (!commentBtn) return;` 守卫。

### 12. 🟡 app.py 重复导入
- **文件**: `app.py` 第 44 行和第 48 行
- **严重级别**: 🟡 低
- **问题**: `create_user` 和 `hash_password` 在导入列表中出现了两次。
- **修复建议**: 删除重复的导入行。

---

## 三、代码复杂度与可维护性

### 13. 🟡 update_review_schedule 函数过长（~200 行）
- **文件**: `models.py` — `update_review_schedule()`
- **严重级别**: 🟡 中
- **问题**: 该函数包含了学习阶段、正式复习、新题首次答题、连续简单计数器、已掌握覆盖等多套逻辑，嵌套层次深，难以测试和维护。
- **修复建议**: 拆分为多个子函数：
  - `_handle_learning_stage()` — 处理学习阶段逻辑
  - `_handle_review_stage()` — 处理正式复习逻辑
  - `_handle_new_question()` — 处理新题首次答题
  - `_apply_mastery_override()` — 处理连续简单/已掌握覆盖

### 14. 🟡 _extract_apkg 函数职责过多
- **文件**: `admin.py` — `_extract_apkg()`
- **严重级别**: 🟡 中
- **问题**: 该函数同时负责：解压 zip、解析 protobuf、媒体文件处理、数据库解压、分类创建、题目解析、staging 写入。单一函数承担了 7 个职责。
- **修复建议**: 拆分为：
  - `_extract_zip()` — 解压 apkg
  - `_parse_media()` — 解析媒体文件
  - `_parse_collection()` — 解析 Anki 数据库
  - `_create_categories_from_deck()` — 从牌组名创建分类
  - `_parse_notes_to_staging()` — 解析 notes 并写入 staging

### 15. 🟡 get_category_mastery 使用多个关联子查询
- **文件**: `models.py` — `get_category_mastery()`
- **严重级别**: 🟡 中
- **问题**: 使用 5 个关联子查询（`total`, `reviewed`, `accuracy`, `mastered`, `due`），每行分类数据触发 5 次子查询。分类多时性能显著下降。
- **修复建议**: 使用 `LEFT JOIN` + `GROUP BY` 替代关联子查询，或使用窗口函数优化。

### 16. 🟡 study_setup 路由过长（~150 行）
- **文件**: `app.py` — `study_setup()`
- **严重级别**: 🟡 低
- **问题**: 路由函数包含授权检查、进度统计、题目列表构建、排序逻辑、模板渲染等多个步骤。
- **修复建议**: 将数据准备逻辑提取到 `models.py` 中的独立函数。

---

## 四、代码风格与规范

### 17. 🟢 models.py 存在大量重复的数据库连接模式
- **文件**: `models.py`（几乎所有函数）
- **严重级别**: 🟢 低
- **问题**: 每个函数都重复 `conn = get_db(); cur = conn.cursor(); ...; conn.close()` 模式。未使用上下文管理器，异常时连接可能未正确关闭。
- **修复建议**: 创建 `@with_db` 装饰器或使用 `contextlib.contextmanager` 包装 `get_db()`，确保连接始终正确关闭。

### 18. 🟢 simulate_learning.py print 语句中的变量引用问题
- **文件**: `simulate_learning.py` 第 137 行
- **严重级别**: 🟢 低
- **问题**: `print(f"   最终状态分布: 学习={_}, 复习={_}, 强化={_}")` 中的 `_` 是 Python 交互式环境中的上一个结果占位符，在脚本中无意义。
- **修复建议**: 使用具名变量：`print(f"   最终状态分布: 学习={learning}, 复习={review}, 强化={reinforce}")`

### 19. 🟢 迁移脚本使用旧版 sha256 哈希
- **文件**: `migrate.py` 第 203-215 行
- **严重级别**: 🟢 低
- **问题**: 创建默认管理员和测试用户时使用 `hashlib.sha256()` 而非 `hash_password()`（pbkdf2:sha256）。虽然 `authenticate_user()` 兼容旧格式并会自动升级，但初始密码安全性较低。
- **修复建议**: 使用 `hash_password()` 替代 `hashlib.sha256()`。

---

## 五、依赖与配置

### 20. 🟡 Flask 依赖版本偏旧
- **文件**: `requirements.txt`
- **严重级别**: 🟡 中
- **问题**: 
  - `Flask==2.3.3` — 当前最新稳定版为 3.x 系列
  - `Werkzeug==2.3.7` — 存在已知 CVE（CVE-2023-46136，DoS 漏洞）
- **修复建议**: 升级到 `Flask>=3.0.0` 和 `Werkzeug>=3.0.1`，注意检查 breaking changes（主要是 `Flask.testing` 和 `RequestContext` 的变化）。

### 21. 🟢 未使用 requirements.txt 中的 flask-limiter
- **文件**: `requirements.txt`, `app.py`
- **严重级别**: 🟢 低
- **问题**: `app.py` 使用了 `flask_limiter`，但 `requirements.txt` 中未列出该依赖。如果通过 `pip install -r requirements.txt` 安装，会缺失该模块。
- **修复建议**: 在 `requirements.txt` 中添加 `Flask-Limiter`。

---

## 六、问题汇总统计

| 严重级别 | 数量 | 占比 |
|---------|------|------|
| 🔴 高危 | 4 | 19% |
| 🟡 中等 | 13 | 62% |
| 🟢 低危 | 4 | 19% |
| **合计** | **21** | 100% |

---

## 七、修复优先级建议

### P0（立即修复）
1. **#7** — 路由名称不匹配（`admin.admin_users` → `admin.users`），会导致 500 错误
2. **#1** — 安全答案明文存储，数据库泄露可直接重置密码
3. **#3** — 缺失 CSRF 保护，所有表单操作均可被跨站伪造
4. **#2** — `|safe` 渲染用户可控内容，存在 XSS 风险

### P1（近期修复）
5. **#13** — 拆分 `update_review_schedule` 降低复杂度
6. **#14** — 拆分 `_extract_apkg` 降低复杂度
7. **#8** — 修复变量遮蔽问题
8. **#20** — 升级 Flask/Werkzeug 修复已知 CVE
9. **#4** — 图片上传增加 MIME 类型验证

### P2（后续优化）
10. **#15** — 优化 `get_category_mastery` 查询性能
11. **#21** — 补充 `requirements.txt` 缺失依赖
12. **#17** — 引入数据库连接上下文管理器
13. 其余代码风格和低优先级问题

---

## 附录：审查范围说明

- **已审查文件**: `models.py`, `app.py`, `admin.py`, `auth.py`, `migrate.py`, `simulate_learning.py`, `static/question-features.js`, 全部 HTML 模板
- **未审查内容**: `migrate_admin_features.py`, `migrate_history_to_fsrs.py`, `tools/parse_apkg.py`（迁移脚本，一次性使用）
- **审查工具**: 手动代码审查 + grep 模式匹配
- **SQL 注入评估**: 项目中所有 SQL 查询均使用参数化查询（`?` 占位符），未发现直接字符串拼接注入风险。动态 SQL 构造（如 `_mastered_sql_condition()`）仅返回固定常量字符串，不接受用户输入。
