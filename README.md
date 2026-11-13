# AI招生助手

面向考生与招生办的 AI 招生咨询系统。当前进度：**问答链路 + 分数直答/推荐 + 前端/管理端闭环**，已导入东北师大 2025/2026 真实分数线、2025 专业组投档线与招生计划，支持普通用户咨询/查专业/提问/看公告，以及管理员受理提问、发布公告并回灌知识库。

需求文档见 [AI招生助手-项目需求文档.md](./AI招生助手-项目需求文档.md)，数据库 16 张表的逐字段说明见 [AI招生助手-数据库表结构.md](./AI招生助手-数据库表结构.md)，接手请先看 [AI招生助手-交接文档.md](./AI招生助手-交接文档.md)。

## 环境

- Python 3.12（使用 conda 环境 `ai_3.12`）
- MySQL 8.x

```powershell
conda activate ai_3.12
pip install -r requirements.txt
```

## 配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少改这三项：

| 配置 | 说明 |
|------|------|
| `DATABASE_URL` | MySQL 连接串，首次启动会自动建库 `aistudent` |
| `LLM_PROVIDER` | `local`（Ollama）/ `deepseek` / `mock` |
| `DEEPSEEK_API_KEY` | 选 `deepseek` 时必填 |
| `NEU_SCORE_CSV` | 历年分数线 CSV，默认指向仓库内 `data/gkcx/东北师大_历年分数线_全量.csv`，一般无需修改 |
| `NEU_PLAN_CSV` | 历年招生计划（含学费）CSV，默认指向仓库内 `data/gkcx/东北师大_历年招生计划_含学费.csv` |
| `COLLEGE_INTRO_DIR` | 学院简介 txt 目录，默认 `data/college_intro` |

数据文件已随仓库提供（约 7 MB），无需额外准备；`.env.example` 里的 `DEEPSEEK_API_KEY` 需填自己的 Key。

本地 Ollama 需要先启动并拉取模型：

```powershell
ollama pull qwen2.5:3b
ollama serve
```

## 运行

```powershell
python demo.py                  # 一键演示：建表 → 导入 → 注册 → 提问 → 查会话
uvicorn main:app --reload       # 启动服务
```

- 前端页面：<http://127.0.0.1:8000>（登录后按角色显示不同导航）
- 接口文档：<http://127.0.0.1:8000/docs>

首个管理员账号需要手动创建（`admin123456` 仅为本地演示密码，部署前请替换）：

```powershell
python -c "from database.connection import SessionLocal; from services import user_service; db=SessionLocal(); user_service.register(db,'admin','admin123456',role='admin'); db.commit(); db.close()"
```

## 导入数据

```powershell
python demo.py                                    # 建表 + 导入分数线/计划/FAQ + 演示问答
python -m collector.college_importer              # 导入学院简介（可反复执行）
python -m collector.college_importer --dry-run    # 只看统计不写库
python -m collector.gaokao_json_importer          # 导入掌上高考院校 JSON 的专业级最低分/位次
python -m collector.gaokao_json_importer --path "D:\...\00142_东北师范大学.json" --dry-run
python -m collector.doc_importer                  # 导入 data/raw_docs 下的政策/资料 txt（幂等）
python -m collector.doc_importer --dir policy --dry-run
```

掌上高考 JSON（如 `00142_东北师范大学.json`）只需专业级记录：按「专业名 + 招生类型」映射到 `majors`，写入 `admission_scores`；同一专业多条计划按最低分取并集，预科类等映射不到的专业会跳过并打印清单。

`data/gkcx/` 下的 `东北师大_历年分数线_全量.csv` 与 `东北师大_历年招生计划_含学费.csv` 是权威数据源，导入器默认读取它们；同目录的 `plan_2025_2026.csv`、`plan_2026.csv` 是早期整理稿，仅作参考。

学院简介按 600 字分块写入 `knowledge_docs`（category=major），文件增删改后重跑即可：内容未变计入 `skipped`，变短产生的多余分块会被删除。目录默认取 `COLLEGE_INTRO_DIR`，同级 `college_urls.json` 用于校准学院名称并统计缺失学院。

## 测试

```powershell
python -m pytest -v
```

测试使用临时 SQLite 文件与 `mock` 模型后端，不依赖 MySQL 和网络。

## 目录结构

```
config/       配置与统一异常
database/     连接、会话、14 张表 ORM
api/          路由与依赖（鉴权、响应包装）
services/     业务服务（用户、画像）
processor/    校验、关键词检索、分数直答、LLM 客户端、问答链路
collector/    采集导入（CSV / 掌上高考 JSON / 学院简介 / 政策 txt，均幂等可重跑）
data/         知识库 FAQ、政策原文 raw_docs、学院官网链接
static/       原生单页前端（index.html / app.js / style.css）
tests/        单元与集成测试
```

依赖方向单向：`api → services → processor / collector → database`，`config` 被各层只读引用。

## 已实现接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET / PUT | `/api/profile` | 考生画像 |
| POST | `/api/chat` | 一轮问答 |
| GET | `/api/majors` | 专业列表（年份/省份/科类齐全时按分数聚合） |
| GET | `/api/majors/options` | 专业查询筛选项（年份、省份、科类） |
| GET | `/api/majors/{id}` | 专业详情 |
| GET | `/api/school/scores` | 院校专业组投档线 |
| GET | `/api/school/plan-diff` | 两年招生计划变动 |
| GET | `/api/conversations` | 我的会话 |
| GET | `/api/conversations/{id}/messages` | 会话消息 |
| POST | `/api/questions` | 用户提交提问（10~500 字，5 分钟防重） |
| GET | `/api/questions/my` | 我的提问 |
| GET | `/api/questions` | 管理员查看提问列表与状态统计 |
| POST | `/api/questions/{id}/answer` | 管理员答复 |
| POST | `/api/questions/{id}/close` | 管理员关闭提问 |
| GET | `/api/announcements` | 公告列表（默认仅已发布） |
| POST / PUT | `/api/announcements` | 管理员新建 / 编辑公告 |
| POST | `/api/announcements/{id}/publish` | 发布并回灌知识库 |
| POST | `/api/announcements/{id}/unpublish` | 下架并移除回灌条目 |
| GET | `/api/admin/stats` | 管理端统计概览 |
| GET / PUT | `/api/admin/llm` | 查看 / 切换问答模型 |
| GET | `/health` | 健康检查 |

统一响应：`{"code": 0, "message": "ok", "data": ...}`；错误码见需求文档 5.2。

## 已知限制

- 检索只覆盖 `knowledge_docs`；分数类问题由 `processor/score_query.py` 直查 `admission_scores` / `school_admission_scores` 作答，模糊表述仍走关键词检索。
- 关键词召回不支持同义词（如“保研”↔“推免”），待补同义词表；向量检索尚未接入。
- 专业查询卡片的最低价可能取到预科班/专项，待改为普通类优先并单独标注。
- 部分省份（新疆、西藏、内蒙古等）部分计划不公布位次，库内位次为 0，前端会显示"最低位次 0"。
- 课程/培养方案数据未采集，课程类问题只能返回学院简介中的概述并提示查看官网。
- 生活类（宿舍/食堂/军训）、就业质量报告、全校大类分流方案暂无官方资料。
- 录取明细仅 2025、2026 两年，院校投档线仅 2025，趋势判断样本偏少。
- 更完整的现状与后续任务见 [交接文档](./AI招生助手-交接文档.md)。
