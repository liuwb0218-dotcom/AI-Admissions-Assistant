# AI招生助手 · 交接文档

> 更新日期：2026-11-12　｜　当前版本：问答链路 + 分数推荐 + 管理端闭环
> 适用对象：接手本项目的开发同学

---

## 1. 项目定位与技术栈

面向考生/家长的东北师范大学 AI 招生咨询系统：知识库检索问答、按分数/位次推荐专业、专业查询、计划变动、用户提问与公告。

- 后端：FastAPI + SQLAlchemy 2.0 + PyMySQL + PyJWT + bcrypt
- 前端：原生 HTML/CSS/JS，由 FastAPI `StaticFiles` 托管（无构建步骤）
- 数据库：MySQL 8.0，库名 `aistudent`，字符集 `utf8mb4`
- 大模型：本地 Ollama（qwen2.5:3b）→ DeepSeek → mock 三级降级，管理员可在页面切换
- 分层：`api → services → processor / collector → database`，`config` 只读引用
- 统一响应：`{"code":0,"message":"ok","data":...}`

---

## 2. 运行方式与重要端口

| 服务 | 地址 / 端口 | 说明 |
|------|-------------|------|
| Web 前端 + 后端 | `http://127.0.0.1:8000` | `uvicorn main:app --host 127.0.0.1 --port 8000`，登录后按角色显示导航 |
| 接口文档 | `http://127.0.0.1:8000/docs` | Swagger，可直接调试 |
| 健康检查 | `http://127.0.0.1:8000/health` | 返回 200 即服务正常 |
| MySQL | `127.0.0.1:3306`，库 `aistudent` | 首次启动自动建表，账号见 `.env` 的 `DATABASE_URL` |
| 本地大模型 Ollama | `http://127.0.0.1:11434/v1` | 模型 `qwen2.5:3b`，超时 120s；需先 `ollama serve` |
| DeepSeek（备选） | `https://api.deepseek.com/v1` | 模型 `deepseek-chat`，需 `.env` 配 `DEEPSEEK_API_KEY` |
| Python 环境 | conda 环境 `ai_3.12`（Python 3.12） | 依赖见 `requirements.txt` |

常用命令（在项目根目录执行）：

```powershell
# 启动（不带 --reload，改代码后需重启）
uvicorn main:app --host 127.0.0.1 --port 8000

# 探活
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing

# 测试（当前 117 条，全部通过）
python -m pytest -q

# 创建管理员
python -c "from database.connection import SessionLocal; from services import user_service; db=SessionLocal(); user_service.register(db,'admin','admin123456',role='admin'); db.commit(); db.close()"
```

---

## 3. 已经完成的工作

### 3.1 基础闭环（阶段 1~2）
- 用户注册/登录（JWT）、角色区分 user / admin、考生画像 CRUD。
- 多轮问答：敏感问题强匹配 → 分数直答 → 意图识别 → 关键词检索（IDF 加权）→ LLM 生成 → 引用来源 → 消息落库。
- 用户提问（10~500 字、5 分钟防重）、管理员受理（答复/关闭/状态统计）。
- 公告新增/编辑/发布/下架，发布时同事务回灌知识库。
- 管理端统计（咨询量、用户数、热点问题、专业热度）。
- 原生前端单页：登录注册、智能咨询、专业查询、计划变动、我的提问、公告；管理端概览、提问受理、公告管理、模型切换。

### 3.2 数据建设
- 2025/2026 年专业级录取数据：`admission_scores` 2291 条（2025 年 1275、2026 年 1016）。
- 2025 年院校专业组投档线：`school_admission_scores` 392 条。
- 招生计划：`enrollment_plans` 2054 条（含学费，支撑“计划变动”页）。
- 专业：79 个；学院：21 个学院全部入库（简介按 600 字分块，`knowledge_docs.category='major'` 111 条）。
- 政策文档 24 条：2026 招生章程（8 块）、转专业实施办法（3 块）、2026 届推免通知（3 块）、本科生奖助政策（5 块）、教育学部大类分流方案（2 块）等。
- 采集器：`collector/nenu_importer.py`（CSV）、`college_importer.py`（学院简介）、`gaokao_json_importer.py`（掌上高考 JSON）、`doc_importer.py`（`data/raw_docs/**/*.txt`，按 category+title 幂等 upsert）。全部可重复执行。

### 3.3 问答能力增强
- **分数直答**（`processor/score_query.py`）：问句命中专业 + 分数意图时直接查库作答，不经过大模型，避免“资料未提供具体分数”。
- **院校投档线兜底**：问“东北师大多少分”时按省份 + 科类给院校最低投档线、两年对比、冲稳保判断。
- **按分数推荐专业**：问“我 598 分有哪些专业选择”时列出最低分在考生分数/位次之下的专业，含分差与冲稳保提示。
- **先问省份**：问句里没有省份时先追问“你是哪个省份的考生”，不用画像省份静默代替；同一会话内会记住省份，回复“河南”“物理类”即可续答。
- **按省份适配科类**：上海/浙江/北京/天津/山东/海南 统一“综合改革”；新疆/西藏 物理类、历史类自动映射“理工”“文史”。
- **分数以问句为准**：问句写了分数优先用问句，画像只兜底。
- **学院官网链接**：提到具体学院时，回答附该学院官网按钮（`processor/college_links.py` + `data/college_urls.json`，21 个学院）。
- **检索排序**：关键词按 IDF 加权（越稀有权重越高），标题 ×3、关键词 ×4，修复“转专业办法压过招生章程”的问题。
- **回答排版**：前端 `formatAnswer` / `formatRef` 去 Markdown 符号、统一圆点与引用样式。
- **提示词**：重写 SYSTEM_PROMPT，约束只能依据参考资料、政策按原文表述、不编造网址与分数、不承诺录取。

### 3.4 管理端
- 数据概览 + 模型切换（local / deepseek / mock，写 `system_settings`，即时生效）。
- 提问受理、公告管理（发布/下架同步知识库）。

---

## 4. 核心数据现状（2026-11-12 实查）

| 表 | 条数 | 说明 |
|----|------|------|
| `admission_scores` | 2291 | 专业级最低分/位次，2025 年 1275、2026 年 1016 |
| `school_admission_scores` | 392 | 院校专业组投档线（2025） |
| `enrollment_plans` | 2054 | 招生计划（含学费） |
| `majors` | 79 | 专业（含中外合作、专项等） |
| `knowledge_docs` | 139 | `major` 111（学院简介）、`policy` 24、`faq` 2、`sensitive` 2 |
| `users` | 2 | 管理员 + 测试用户 |
| `user_questions` | 1 | 测试数据 |
| `announcements` | 0 | 尚未发布正式公告 |

数据库共 13 张表：users、student_profiles、majors、admission_scores、enrollment_plans、school_admission_scores、knowledge_docs、conversations、messages、user_questions、announcements、system_settings、recommendations。

---

## 5. 关键文件索引

| 路径 | 作用 |
|------|------|
| `main.py` | 建表、静态托管、注册路由 |
| `config/settings.py` | 全部配置项（数据库、JWT、LLM、检索参数） |
| `processor/chat_service.py` | 问答主链路、SYSTEM_PROMPT、会话与消息落库 |
| `processor/retriever.py` | 关键词召回 + IDF 加权、敏感问题匹配 |
| `processor/score_query.py` | 分数直答、院校投档线、按分数推荐、省份/科类解析 |
| `processor/college_links.py` | 学院名称 → 官网链接 |
| `processor/llm_client.py` | LLM 三级降级客户端 |
| `api/majors.py` | 专业列表/详情/筛选项（`/options`），三筛选齐全时按分数聚合 |
| `api/school.py` | 院校投档线 `/api/school/scores`、计划变动 `/api/school/plan-diff` |
| `api/admin.py` | 统计概览、模型配置读写 |
| `collector/doc_importer.py` | 政策/资料 txt 批量入库（幂等） |
| `data/raw_docs/` | 政策原文 txt（含来源与抓取日期文件头） |
| `static/app.js` | 单页前端全部交互与渲染 |
| `tests/` | 30 条测试（检索排序、学院链接、分数推荐等） |

---

## 6. 当前问答链路（便于排查）

```
用户提问
  → 敏感问题强匹配（命中直接返回标准答案）
  → 分数直答 build_score_answer
       ├─ 无省份 → 追问省份（同会话记住）
       ├─ 命中具体专业 → 该专业最低分/位次 + 冲稳保判断
       ├─ 院校类 → 院校最低投档线（近两年对比）
       └─ 推荐类 → 按分数/位次筛选可报专业（最多 8 个）
  → 意图识别 → 关键词检索 top_k=5（IDF 加权）
       ├─ 无结果 → “资料未检索到” + 学院官网（若提及学院）
       └─ 有结果 → LLM（local → deepseek → mock）→ 回答 + refs + links
  → 用户消息与助手消息落库（含 intent、refs）
```

可调参数（`.env`）：`RETRIEVAL_TOP_K=5`、`HISTORY_ROUNDS=5`、`LLM_PROVIDER`、`LOCAL_LLM_TIMEOUT=120`。

---

## 7. 已知问题与限制

1. **需求文档过时**：`AI招生助手-项目需求文档.md` 仍写 10 张表、FAISS 向量检索，与现有 13 张表、关键词检索不一致，需同步 v1.2。
2. **专业查询卡片口径**：`api/majors.py` 取最低分时可能取到少数民族预科班/专项，建议普通类优先、预科与专项单独标注。
3. **同义词缺失**：检索没有同义词表，“保研”命中不了“推免”，已在提示词外待补映射。
4. **资料缺口**：宿舍/食堂/军训/校区等生活类无官方材料；招生章程附件《体检要求》PDF 未抓取（需 pypdf）；全校性大类分流方案无公开文件（仅教育学部 2021 版）；推免修订版 PDF 有验证码。
5. **就业数据**：仅有约 895 个推免名额等零散指标，无官方保研率/去向统计、无就业质量报告。
6. **检索不覆盖结构化分数**：分数类问题已由 `score_query` 直答覆盖，但模糊表述（如“分低一点的专业”）仍走关键词检索。
7. **数据年限**：录取明细只有 2025、2026 两年，院校投档线只有 2025，趋势判断样本偏少。

---

## 8. 接下来的任务

### P0（建议优先）
1. 同步需求文档到 v1.2：13 张表结构、检索实现、分数直答与省份追问规则。
2. 专业查询口径修正：普通类优先，预科/专项/中外合作单独标注（涉及 `api/majors.py` 与前端卡片）。
3. 检索补同义词表：保研↔推免、强基↔基础学科、分数线↔投档线等。
4. 补抓招生章程附件《体检要求》PDF，以及新生入学须知。
5. 生活类资料补齐：宿舍、食堂、军训、校区通勤（优先官网/官微，标注非官方来源）。

### P1
6. 补 2023/2024 录取数据与 2026 专业组投档线，扩大趋势样本。
7. 就业质量报告、保研去向统计入库（政策/就业类知识块）。
8. 全校大类分流方案与各学院分流细则。
9. 运营看板图表与报表导出（原 M6 扩展）。
10. 若召回仍不足，评估向量检索或混合检索（embedding + 关键词）。

### P2
11. 部署上线：域名、HTTPS、进程守护（NSSM/systemd）、数据库备份策略。
12. 管理端增加知识库维护页（上传 txt/PDF、查看分块与命中情况）。
13. 埋点与日志：记录未命中问题，定期回流成知识块。

---

## 9. 交接检查清单

- [ ] `.env` 已配置 `DATABASE_URL`、`JWT_SECRET`、`LLM_PROVIDER`（local 需启动 Ollama）。
- [ ] `python -m pytest -q` 全部通过（当前 30 passed）。
- [ ] `GET /health` 返回 200，首页能登录。
- [ ] 管理员账号可登录，模型切换可用。
- [ ] 数据导入脚本可重复执行（`--dry-run` 先看统计）。
