# ExamMaster

> 多科目在线考试系统 · Flask + SQLite + SM-2 间隔重复

**版本**: v0.6.0  
**最后更新**: 2026-04-29

---

## 快速开始

```bash
cd /exam-master
source venv/bin/activate
python3 app.py
# 访问 http://localhost:32220
```

生产部署见 [docs/deployment.md](docs/deployment.md)。

## 项目结构

```
/exam-master/
├── app.py              # 用户端路由（答题、收藏、统计等）
├── admin.py            # 管理端路由（用户/科目/题目/权限管理）
├── models.py           # 数据模型层（数据库操作封装）
├── auth.py             # 认证中间件（登录验证装饰器）
├── migrate.py          # 数据库迁移脚本
├── requirements.txt    # Python 依赖
├── database.db         # SQLite 数据库
├── templates/          # Jinja2 模板（33 个）
│   ├── base.html       # 用户端基础模板
│   ├── base_auth.html  # 认证页面模板
│   ├── error.html      # 错误页面
│   ├── index.html      # 科目选择页
│   ├── question.html   # 答题页
│   ├── exam.html       # 考试页
│   ├── mock_exam.html  # 模拟考试
│   ├── statistics.html # 统计分析
│   └── admin/          # 管理端模板（14 个）
├── static/
│   └── style.css       # 全局样式
└── docs/               # 项目文档
    ├── architecture.md # 架构设计
    ├── database.md     # 数据库文档
    ├── routes.md       # 路由清单
    └── deployment.md   # 部署指南
```

## 核心功能

| 模块 | 功能 |
|------|------|
| 用户系统 | 注册/登录/权限控制，多角色（admin/user） |
| 科目管理 | 多科目支持，用户-科目权限分配 |
| 分类练习 | 三级分类树（科目→章节→知识点） |
| 答题模式 | 章节练习、随机答题、模拟考试、历史真题 |
| SM-2 算法 | 间隔重复刷题，5 级评分，自动排期 |
| 统计分析 | 热力图、趋势图、分类掌握度、遗忘曲线 |
| 收藏/错题 | 题目收藏、错题本、复习 |
| 管理后台 | 用户管理、题目 CRUD、CSV 批量导入、权限分配 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 + Flask 2.3.3 |
| 数据库 | SQLite 3 |
| 部署 | Gunicorn + systemd + Nginx |
| 前端 | Jinja2 模板 + Chart.js (CDN) |
| 算法 | SM-2 间隔重复算法 |

## 架构设计

```
用户请求 → Flask Router (app.py / admin.py)
              ↓
         认证中间件 (auth.py)
              ↓
         数据模型层 (models.py)
              ↓
         SQLite 数据库 (database.db)
```

详细架构见 [docs/architecture.md](docs/architecture.md)。

## 开发规范

- **数据访问**：所有数据库操作必须通过 `models.py` 封装
- **密码哈希**：统一使用 `models.hash_password()` (SHA-256)
- **题目选项**：JSON 字符串存储，使用 `parse_options()` 解析
- **软删除**：题目删除使用 `status = 0`，不物理删除

## 版本历史

见 [CHANGELOG.md](CHANGELOG.md)。
