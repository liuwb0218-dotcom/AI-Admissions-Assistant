# AI招生助手 - 项目需求文档

> 依据《阶段综合项目实战（二）》需求分析五步法与需求文档模板编写。
> 版本：v1.1（新增用户提问、管理员受理统计与公告发布）　编写日期：2026-11-10

---

## 0. 需求分析五步法总览

| 步骤 | 内容 | 本文档对应章节 |
|------|------|----------------|
| 第1步 明确项目要解决什么问题 | 一句话项目定位 | 第1章 项目概述 |
| 第2步 拆解核心功能模块 | 6 个模块，分 P0 / P1 | 第2章 核心功能模块 |
| 第3步 确定数据源或接口 | 8 类数据来源与限制 | 第3章 数据源/接口选择 |
| 第4步 设计数据库表结构 | 10 张表，含外键关联 | 第4章 数据库设计 |
| 第5步 定义模块间接口契约 | 数据流转格式 + 校验规则 | 第5章 接口契约定义 |

---

## 1. 项目概述

**一句话描述：** 面向考生与招生办，构建一个"懂这所学校"的 AI 招生助手——考生侧提供基于校本知识库的多轮招生咨询与专业推荐，AI 答不了的可以转成"向学校提问"，招生办侧受理提问、输出关注点统计并发布公告统一解答，解决通用大模型"答得泛、答不准、答了不可追溯、答不了无处问"的问题。

**核心数据流（对齐培训四模块骨架）：**

```
招生数据采集  →  AI问答/推荐处理  →  咨询数据入库(含去重)  →  查询统计
   M1 采集           M2 处理              M3 入库               M4 统计

         用户提问 ──→ M3 留言入库 ──→ M4 受理统计 ──→ M4 公告发布 ──→ 用户端可见
```

**范围内的角色：** 普通用户（考生/家长）、管理员（招生办）。

- 普通用户：只能访问自己的会话、画像与提问；可查看已发布公告。
- 管理员：查看全量统计、受理并答复全部提问、发布/下架公告。

---

## 2. 核心功能模块

### 2.1 模块清单

| 模块 | 功能描述 | 输入 | 输出 | 优先级 |
|------|----------|------|------|--------|
| **M1 招生数据采集** | 导入招生政策、专业介绍、历年分数线、标准问答对，清洗校验后写入知识库与结构化表，构建 FAISS 向量索引 | CSV / JSON / Markdown 文件 | `List[dict]` 清洗结果 + 入库条数 | P0 |
| **M2 智能问答处理** | 多轮对话：意图识别 → 敏感问题强匹配拦截 → FAISS 检索 → 大模型生成 → 拼接引用来源 | `{conversation_id, message}` | `{answer, refs[], intent}` | P0 |
| **M3 咨询、留言与公告入库** | 用户注册/登录，会话消息、考生画像、用户提问留言、管理员公告统一落库，按唯一键与防刷规则去重 | 结构化业务对象 + 提问/公告表单 | 入库条数 / 幂等跳过数 | P0 |
| **M4 后台统计与公告管理** | 咨询总量、独立用户数、热点问题 TopN、专业咨询热度、用户关注点聚合；提问受理（待处理/已解答/已关闭）与分类统计；公告新增、发布、下架，**发布/编辑/下架时同事务双写回灌知识库** | 时间范围、维度、角色、提问/公告表单 | 统计结果集 + 提问列表 + 公告对象 + 知识库同步状态 | P0 |
| **M5 专业推荐**（扩展1） | 依据分数/位次/省份/选科/兴趣做专业匹配，输出"冲—稳—保"分层与匹配理由 | 考生画像 + 历年录取数据 | 推荐列表（专业、概率区间、层次、理由） | P1 |
| **M6 运营看板与报表导出**（扩展2） | 管理员看板聚合可视化 + 一键导出 CSV/Excel 报表（含咨询明细与提问清单） | 筛选条件 | 报表文件 | P1 |

> 优先级约束：P0 恰为 4 个（对应采集→处理→入库→查询四模块），P1 为 2 个，符合"P0 不超过 4、P1 不超过 2"的实战约束。
> **新增能力的归属**："用户提问 + 管理员受理统计 + 公告发布"不新增第 7 个模块，而是按数据流拆分下沉：**写入侧**并入 M3（`user_questions` / `announcements` 落库），**管理与统计侧**并入 M4（受理、状态流转、分类统计、发布/下架）。

### 2.2 基础支撑能力（不计入 6 个业务模块）

| 能力 | 说明 | 优先级 |
|------|------|--------|
| 用户认证 | 注册 / 登录，签发 JWT；`role` 区分 user / admin | P0 |
| 配置管理 | `.env` + python-dotenv 管理数据库、大模型 Key、FAISS 路径 | P0 |
| 权限隔离 | 普通用户仅能读写 `user_id = 自己` 的会话、画像与提问；管理员可读全量统计、受理全部提问、发布公告 | P0 |
| 异常与日志 | 大模型超时/限流重试、检索为空降级、关键操作 INFO/ERROR 日志 | P0 |

### 2.3 MVP 边界（明确不做什么）

| 类别 | 内容 | 处理 |
|------|------|------|
| 本期实现 | M1~M4 全链路 + 认证 + M5/M6 两个扩展（含用户提问提交、管理员受理答复、公告发布） | P0/P1 |
| 降级实现 | "双 JWT"简化为单 access_token（refresh 机制列为后续优化） | 范围调整 |
| 降级实现 | Redis 缓存改为进程内字典 / MySQL 查询缓存 | 范围调整 |
| 降级实现 | 提问答复不发送邮件/短信/站内信，用户通过"我的提问"列表查看状态与答复 | 范围调整 |
| 本期实现 | 公告发布/编辑/下架时**同事务双写回灌知识库**，并重建 FAISS 索引（见 5.5） | P0 |
| 本期不做 | 官网/公众号/小程序多端接入、人工坐席实时转接、多语言方言、多校区扩展、CRM 对接 | 移出范围 |
| 本期不做 | 置信度阈值模型、录取概率的精算模型（用位次差规则近似） | 移出范围 |

---

## 3. 数据源/接口选择

| 数据项 | 来源 | 获取方式 | 调用频率 | 限制条件 |
|--------|------|----------|----------|----------|
| 招生政策 / 招生简章 | 学校招生网、省考试院公开文件 | 人工整理为 Markdown/CSV 后导入 | 一次性导入，每季度更新 | 需人工核对版本与生效年度；仅使用公开文件 |
| 专业介绍 / 培养方案 | 学校官网专业目录 | 整理为 `majors.csv` | 一次性导入 | 字段需含专业名、院系、选科要求 |
| 历年录取分数线 | 省考试院公开数据、学校公示 | 整理为 `admission_scores.csv` | 每年更新一次 | 必须标注年度 + 省份；缺失年份不臆造 |
| 标准问答对 / 敏感话术 | 招生办历史咨询记录（脱敏） | 整理为 `faq.json` | 按需增量导入 | 需脱敏姓名/手机号；敏感条目走强匹配 |
| 大模型问答生成 | DeepSeek / Qwen 兼容 OpenAI 协议接口 | HTTP API（requests） | 每轮对话 1 次 | 需 API Key、超时 5s、失败重试 2 次、限流保护 |
| 向量检索 | 本地 FAISS 索引（IndexFlatIP） | 本地文件读写 | 每次问答 1 次 | 需先由 M1 构建索引；索引与知识库版本绑定 |
| 用户提问留言 | 用户在问答页"向学校提问"入口提交 | 表单 + REST API | 实时 | 正文 10~500 字；同用户同内容 5 分钟内防重；不得采集超出必要的个人信息 |
| 管理员公告 | 招生办管理员在后台编辑发布 | 表单 + REST API | 按需 | 发布前必填标题、正文、分类；仅 `is_published=1` 对用户端可见 |

> 数据可得性兜底：若真实分数线暂缺，先用 `mock/` 目录下同格式的模拟数据跑通全链路，字段结构与真实数据完全一致，后续直接替换文件即可。

---

## 4. 数据库设计

### 4.1 表清单与关系

| 表名 | 说明 | 优先级 |
|------|------|--------|
| `users` | 用户账号与角色 | P0 |
| `student_profiles` | 考生画像（与用户 1:1） | P0 |
| `majors` | 专业基础信息 | P0 |
| `admission_scores` | 分年度分省份录取数据 | P0 |
| `knowledge_docs` | 知识库文档（政策/专业/问答/敏感/公告回灌） | P0 |
| `conversations` | 会话 | P0 |
| `messages` | 会话消息 | P0 |
| `user_questions` | 用户向学校提交的提问与答复 | P0 |
| `announcements` | 管理员发布的公告 | P0 |
| `recommendations` | 专业推荐记录 | P1 |

**表间关系：**

```
users 1 ──── 1 student_profiles
users 1 ──── N conversations
users 1 ──── N recommendations
conversations 1 ──── N messages
majors 1 ──── N admission_scores
majors 1 ──── N recommendations
users 1 ──── N user_questions（提问人）
users(admin) 1 ──── N user_questions（答复人）
users(admin) 1 ──── N announcements（发布人）
user_questions 1 ──── 0..1 announcements（related_question_id，可空）
announcements 1 ──── 0..1 knowledge_docs（source_announcement_id，仅公告回灌生成的条目）
```

### 4.2 表1：users

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 登录名 |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt 哈希，禁止存明文 |
| role | VARCHAR(20) | NOT NULL, DEFAULT 'user' | user / admin |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 注册时间 |

### 4.3 表2：student_profiles

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| user_id | INT | FK → users.id, UNIQUE, NOT NULL | 一人一画像 |
| province | VARCHAR(20) | NOT NULL | 生源省份 |
| score | INT | NULL | 高考分数 |
| rank_no | INT | NULL | 全省位次（`rank` 为保留字，故用 rank_no） |
| subject_combo | VARCHAR(50) | NULL | 选科组合，如"物理+化学+生物" |
| interest_tags | VARCHAR(200) | NULL | 兴趣标签，逗号分隔 |
| career_intent | VARCHAR(50) | NULL | 考公 / 考研 / 就业 |
| updated_at | DATETIME | NOT NULL, DEFAULT NOW | 更新时间 |

### 4.4 表3：majors

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| name | VARCHAR(100) | UNIQUE, NOT NULL | 专业名称 |
| department | VARCHAR(100) | NOT NULL | 所属院系 |
| subject_requirement | VARCHAR(100) | NULL | 选科要求，如"物理+化学" |
| description | TEXT | NULL | 培养方向 / 核心课程摘要 |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 创建时间 |

### 4.5 表4：admission_scores

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| major_id | INT | FK → majors.id, NOT NULL | 关联专业 |
| year | SMALLINT | NOT NULL | 录取年度 |
| province | VARCHAR(20) | NOT NULL | 生源省份 |
| min_score | INT | NULL | 最低录取分 |
| min_rank | INT | NULL | 最低录取位次 |
| plan_count | INT | NULL | 计划数 |
| actual_count | INT | NULL | 实际录取数 |
| — | — | UNIQUE(major_id, year, province) | **去重唯一键** |

### 4.6 表5：knowledge_docs

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| category | VARCHAR(30) | NOT NULL | policy / major / faq / sensitive / **announcement** |
| title | VARCHAR(200) | NOT NULL | 文档或问答标题；公告回灌时 = 公告标题 |
| content | TEXT | NOT NULL | 正文 / 标准答案 |
| source_file | VARCHAR(255) | NULL | 来源文件名，用于回答引用溯源 |
| source_type | VARCHAR(20) | NOT NULL, DEFAULT 'file' | file 文件导入 / announcement 公告回灌 |
| source_announcement_id | INT | FK → announcements.id, NULL | 回灌来源公告；为空表示非公告来源 |
| sensitivity_level | TINYINT | NOT NULL, DEFAULT 0 | 0 普通；1 敏感（命中即强匹配直出） |
| keywords | VARCHAR(255) | NULL | 敏感词 / 检索关键词，逗号分隔 |
| updated_at | DATETIME | NOT NULL, DEFAULT NOW | 更新时间 |
| — | — | UNIQUE(category, title) | **去重唯一键** |

### 4.7 表6：conversations

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| user_id | INT | FK → users.id, NOT NULL | 所属用户 |
| title | VARCHAR(200) | NULL | 会话标题（取首条提问截断） |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 创建时间 |
| last_active_at | DATETIME | NOT NULL, DEFAULT NOW | 最后活跃时间 |

### 4.8 表7：messages

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| conversation_id | INT | FK → conversations.id, NOT NULL | 所属会话 |
| role | VARCHAR(16) | NOT NULL | user / assistant |
| content | TEXT | NOT NULL | 消息正文 |
| intent | VARCHAR(50) | NULL | 识别出的意图，如 score_query / major_intro |
| refs | VARCHAR(500) | NULL | 引用来源，如"招生简章2026#第3条" |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 发送时间 |

### 4.9 表8：recommendations（P1）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| user_id | INT | FK → users.id, NOT NULL | 考生 |
| major_id | INT | FK → majors.id, NOT NULL | 推荐专业 |
| match_score | FLOAT | NOT NULL, DEFAULT 0 | 匹配度 0~100 |
| level | VARCHAR(10) | NOT NULL | 冲 / 稳 / 保 |
| reason | VARCHAR(500) | NULL | 匹配理由（展示给考生） |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 生成时间 |

### 4.10 表9：user_questions

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| user_id | INT | FK → users.id, NOT NULL | 提问人 |
| category | VARCHAR(30) | NOT NULL | policy / major / score / other |
| content | TEXT | NOT NULL | 问题正文，10~500 字 |
| status | VARCHAR(16) | NOT NULL, DEFAULT 'pending' | pending 待处理 / answered 已解答 / closed 已关闭 |
| answer_content | TEXT | NULL | 管理员答复正文；`status=answered` 时必填 |
| answered_by | INT | FK → users.id, NULL | 答复管理员（业务校验其 `role='admin'`） |
| answered_at | DATETIME | NULL | 答复时间 |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 提交时间 |

**状态流转（只能单向推进，仅管理员可操作）：**

```
pending ──答复──→ answered ──关闭──→ closed
   └────────────关闭──────────────→ closed
```

### 4.11 表10：announcements

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, AUTO | 主键 |
| title | VARCHAR(200) | NOT NULL | 公告标题 |
| content | TEXT | NOT NULL | 公告正文 / 统一解答 |
| category | VARCHAR(30) | NOT NULL | 与提问分类同口径，便于分类展示 |
| related_question_id | INT | FK → user_questions.id, NULL | 由某条提问触发的公告，可反查来源 |
| is_published | TINYINT | NOT NULL, DEFAULT 0 | 0 草稿 / 1 已发布 |
| published_at | DATETIME | NULL | 首次发布时间 |
| created_by | INT | FK → users.id, NOT NULL | 发布管理员 |
| view_count | INT | NOT NULL, DEFAULT 0 | 用户端浏览次数 |
| knowledge_synced | TINYINT | NOT NULL, DEFAULT 0 | 0 未同步 / 1 已同步 / 2 同步失败待补偿 |
| created_at | DATETIME | NOT NULL, DEFAULT NOW | 创建时间 |

### 4.12 去重规则汇总

| 表 | 去重键 | 冲突处理 |
|----|--------|----------|
| `knowledge_docs` | `UNIQUE(category, title)` | 文件导入冲突：跳过并计入 `skipped`，不覆盖已有正文（避免误改）；**公告回灌例外**：按 `source_announcement_id` 幂等更新（见 5.5.2） |
| `admission_scores` | `UNIQUE(major_id, year, province)` | 跳过并计入 `skipped` |
| `majors` | `UNIQUE(name)` | 已存在则复用该 id，不重复插入 |
| `messages` | 同上一条比较（同会话 + 同角色 + 同内容 60s 内） | 视为重复请求，直接返回上次结果 |
| `user_questions` | 同 `user_id` + 同 `content` 且创建时间在 5 分钟内 | 返回 `4008` 请勿重复提交，不新增记录 |
| `announcements` | 同 `created_by` + 同 `title` | 覆盖更新草稿，不重复插入 |

> 验收对应：集成测试中"重复入库返回 0 条"即针对 `knowledge_docs` 与 `admission_scores` 的唯一键。

---

## 5. 接口契约定义

### 5.1 模块间数据流转

| 数据流 | 格式定义 | 校验规则 |
|--------|----------|----------|
| 文件 → M1 采集 | `List[dict]`，字段随文件类型而定 | 文件编码 UTF-8；必含 `title`/`content`（知识库）或 `major_name`/`year`/`province`（分数线） |
| M1 → M1 处理 | `List[dict]` | `category` 必须在白名单 `{policy, major, faq, sensitive}` 内（公告回灌走 M4，不经此白名单）；`content` 去空白后长度 ≥ 10；数值字段可转 int；不合法条目丢弃并计数 |
| M1 → M3 入库 | `List[KnowledgeDoc]` / `List[AdmissionScore]` ORM 对象 | 唯一键冲突跳过；空列表返回 0 而非报错 |
| 客户端 → M2 问答 | `ChatRequest{conversation_id:int, message:str}` | `message` 非空、长度 1~500；`conversation_id` 必须属于当前登录用户 |
| M2 内部：检索 → 生成 | `retrieved: List[dict]` + `prompt:str` | 检索结果为空时不得调用大模型编造，返回"暂无依据，建议查阅官方文件" |
| M2 → 客户端应答 | `ChatResponse{answer:str, refs:List[str], intent:str}` | `refs` 至少 1 条，或 `answer` 明确标注无依据 |
| M2 → M3 入库 | `Message` ORM 对象 | `role ∈ {user, assistant}`；`conversation_id` 外键必须存在 |
| M3 → M4 统计 | SQL 聚合结果 `List[dict]` | 普通用户强制注入 `user_id = 当前用户` 过滤条件；管理员不过滤 |
| 客户端 → M3 提问提交 | `QuestionCreate{category:str, content:str}` | `content` 去空白后 10~500 字；`category ∈ {policy, major, score, other}`；5 分钟内同内容重复提交返回 `4008` |
| M4 → 客户端（提问列表） | `List[QuestionItem{id, category, content, status, answer_content, answered_at}]` | 普通用户强制 `user_id=自己`；管理员可按 `status` / `category` 过滤，默认按 `created_at` 倒序 |
| M4 答复 → 用户端 | `QuestionAnswer{answer_content:str}` | 仅 `role=admin` 可调用；`answer_content` 非空且 ≥ 5 字；落库同时写 `answered_by` / `answered_at` 并置 `answered` |
| M4 公告发布 → 用户端 | `Announcement{id, title, content, category, published_at}` | 发布前 `title` / `content` / `category` 必填；仅 `is_published=1` 对普通用户可见，草稿仅管理员可见 |

### 5.2 对外 API 端点（FastAPI）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/auth/register` | 注册 | 公开 |
| POST | `/api/auth/login` | 登录，返回 JWT | 公开 |
| GET | `/api/majors` | 专业列表 / 详情 | 登录用户 |
| POST | `/api/chat` | 一轮问答 | 登录用户 |
| GET | `/api/conversations` | 我的会话列表 | 登录用户（仅自己） |
| GET | `/api/conversations/{id}/messages` | 会话消息 | 登录用户（仅自己） |
| PUT | `/api/profile` | 更新考生画像 | 登录用户（仅自己） |
| POST | `/api/questions` | 向学校提交提问 | 登录用户 |
| GET | `/api/questions/mine` | 我的提问及答复状态 | 登录用户（仅自己） |
| GET | `/api/announcements` | 公告列表（仅已发布） | 登录用户 |
| GET | `/api/admin/questions` | 提问受理列表（按状态/分类筛选） | 管理员 |
| PUT | `/api/admin/questions/{id}/answer` | 答复提问并置为 answered | 管理员 |
| PUT | `/api/admin/questions/{id}/close` | 关闭提问（answered/pending → closed） | 管理员 |
| GET | `/api/admin/stats/questions` | 提问总量 / 待处理数 / 平均响应时长 / 分类分布 | 管理员 |
| POST | `/api/admin/announcements` | 新建公告（默认草稿） | 管理员 |
| PUT | `/api/admin/announcements/{id}/publish` | 发布 / 下架公告（含知识库双写） | 管理员 |
| POST | `/api/admin/knowledge/rebuild-index` | 重建索引 + 补偿同步失败的公告 | 管理员 |
| GET | `/api/stats/overview` | 咨询总量 / 独立用户 / 热点 TopN | 管理员 |
| GET | `/api/stats/majors` | 专业咨询热度 | 管理员 |
| POST | `/api/recommend` | 生成专业推荐（P1） | 登录用户 |
| GET | `/api/export/consultations` | 导出咨询报表（P1） | 管理员 |

**统一响应包：**

```json
{ "code": 0, "message": "ok", "data": {} }
```

错误码：`0` 成功；`4001` 参数校验失败；`4003` 无权限访问该资源；`4004` 资源不存在；`4008` 重复提交；`5001` 大模型调用失败；`5002` 检索服务异常；`5003` 知识库双写失败（事务已回滚）。

### 5.3 关键处理链路契约（M2）

| 步骤 | 输入 | 处理 | 输出 |
|------|------|------|------|
| 1 鉴权 | JWT | 解析 `user_id` / `role` | 上下文用户 |
| 2 敏感强匹配 | `message` | 与 `knowledge_docs.sensitivity_level=1` 的 `keywords` 做包含匹配 | 命中 → 直接返回标准答案 + 来源，跳过 3~5 |
| 3 意图识别 | `message` + 最近 N 轮消息 | 规则优先（分数/专业/政策关键词），失败则调用大模型分类 | `intent` |
| 4 检索 | `message` | FAISS TopK=5 | `retrieved`（含 `source_file`） |
| 5 生成 | `retrieved` + 历史 | 拼 prompt 调大模型，超时 5s、重试 2 次 | `answer` |
| 6 组装应答 | `answer` + `retrieved` | 拼接引用来源 | `ChatResponse` |
| 7 落库 | 问题 + 应答 | 写 `messages`（含 intent / refs） | 入库条数 |

### 5.4 提问与公告链路契约（M3 ↔ M4）

| 步骤 | 输入 | 处理 | 输出 |
|------|------|------|------|
| 1 用户提交 | `category` + `content` | 长度/白名单校验 → 5 分钟内防重校验 → 写 `user_questions(status=pending)` | `question_id` |
| 2 管理员受理 | `status=pending` 列表 | 按 `created_at` 升序（先来先处理），支持按分类筛选 | 提问列表 |
| 3 管理员答复 | `question_id` + `answer_content` | 校验角色 → 写 `answer_content` / `answered_by` / `answered_at` → 置 `answered` | 更新后的提问 |
| 4 用户查看 | 自己的 `question_id` | 查 `user_questions`，强制 `user_id=自己` | 状态 + 答复正文 |
| 5 发布公告 | `title` + `content` + `category`（可带 `related_question_id`） | 写 `announcements(is_published=0)` → 发布时置 1 并写 `published_at` | `announcement_id` |
| 6 用户浏览公告 | 无 / 分类 | 仅查 `is_published=1`，按 `published_at` 倒序，`view_count+1` | 公告列表 |
| 7 双写回灌知识库 | 已发布公告 `{id, title, content, category}` | 同事务写 `knowledge_docs(category='announcement', source_announcement_id=公告id)` → 提交后重建 FAISS 索引 → 回写 `knowledge_synced` | 知识库同步条数 + 同步状态 |

> 权限红线：第 2~3、5 步必须校验 `role='admin'`，否则返回 `4003`；第 4 步必须校验 `user_id` 归属，越权同样返回 `4003`。这两条是集成测试的必测异常场景。

### 5.5 公告 → 知识库双写规则（P0）

**核心约束：** 公告与知识库条目在**同一个数据库事务**内写入，要么都成功、要么都回滚；FAISS 索引属于派生产物，允许异步重建与补偿。

#### 5.5.1 双写矩阵

| 场景 | 同一 DB 事务内（原子） | 事务提交后（可补偿） | 失败处理 |
|------|------------------------|----------------------|----------|
| 发布公告 | ① `announcements` 置 `is_published=1`、写 `published_at`；② INSERT/UPDATE `knowledge_docs(category='announcement', title=公告标题, content=公告正文, source_type='announcement', source_announcement_id=公告id)` | ③ 重建 FAISS 索引；④ `knowledge_synced=1` | ①②任一失败 → 整体回滚，公告仍为草稿，接口返回 `5003`；③失败 → DB 不回滚，置 `knowledge_synced=2` 并记 ERROR |
| 编辑已发布公告 | ① 更新 `announcements.title` / `content`；② 同步更新对应 `knowledge_docs` 的 title / content | ③ 重建索引；④ `knowledge_synced=1` | 同上；若知识条目已被误删，事务内按 5.5.2 补建，属幂等修复 |
| 下架公告 | ① `announcements.is_published=0`；② DELETE `knowledge_docs WHERE source_announcement_id=公告id`；③ `knowledge_synced=0` | ④ 重建索引 | ②③失败**不回滚下架**（优先保证用户端不可见），记 ERROR，由 5.5.3 检索侧兜底 |

#### 5.5.2 幂等规则

- 定位键：`UNIQUE(category='announcement', title)`，即"公告分类 + 公告标题"。
- 命中 → UPDATE（覆盖 content、source_announcement_id、updated_at）；未命中 → INSERT。
- 重复执行发布动作不产生第二条知识条目。
- **标题变更必须按 `source_announcement_id` 定位旧条目再改 title，禁止用 title 定位**，否则一改标题就会多出一条。

#### 5.5.3 一致性兜底（M2 检索侧）

检索命中 `knowledge_docs` 后，若 `source_announcement_id` 非空，必须联查 `announcements.is_published`：

- `is_published=1` → 保留该片段；
- `is_published=0` 或公告已被删除 → 丢弃该片段，并计入 `stale_chunks` 日志。

即使索引重建失败或滞后，也不会把已下架公告当作回答依据。

#### 5.5.4 分类与敏感级别映射

| 公告 category | 回灌 `knowledge_docs.category` | `sensitivity_level` |
|---------------|-------------------------------|---------------------|
| policy | announcement | 0（可由 AI 概括引用） |
| major | announcement | 0 |
| score | announcement | 1（录取分数属强约束信息，命中即直出原文） |
| other | announcement | 0 |

> 统一落在 `announcement`，不与既有 policy / major 条目争抢 `UNIQUE(category, title)`。

#### 5.5.5 失败补偿接口

`POST /api/admin/knowledge/rebuild-index`：重建 FAISS 索引，并把 `knowledge_synced=2` 的公告按 5.5.1 重新回灌，成功后置 1。返回 `{rebuilt_docs:int, repaired_announcements:int}`。

#### 5.5.6 必测用例

| 编号 | 场景 | 断言 |
|------|------|------|
| T1 | 发布公告 | `knowledge_docs` 新增 1 条且 `source_announcement_id` 正确 |
| T2 | 同一公告重复发布 | `knowledge_docs` 条数不变 |
| T3 | 修改公告标题 | 仍只有 1 条，且 title 已更新 |
| T4 | 下架公告 | 对应知识条目被删除，M2 检索不再返回该公告片段 |
| T5 | 索引重建抛异常 | 公告仍为已发布、`knowledge_synced=2`；调用补偿接口后修复为 1 |
| T6 | 知识库写入抛异常 | DB 事务回滚，公告仍为草稿、`knowledge_docs` 无新增 |

---

## 6. 技术选型

| 项 | 选型 | 说明 |
|----|------|------|
| 语言 | Python 3.10 | 与前期课程一致 |
| Web 框架 | FastAPI + Uvicorn | 提供 REST API 与自动文档 |
| ORM / 数据库 | SQLAlchemy + MySQL | 建表与事务管理；测试可降级 SQLite |
| 向量检索 | FAISS（IndexFlatIP，本地文件） | 无需额外服务，索引落盘 |
| 缓存 | 进程内字典 | 替代 Redis，降低环境依赖 |
| 大模型 | DeepSeek / Qwen（OpenAI 兼容协议） | 通过 `requests` 封装 `LLMClient`，便于替换 |
| 配置管理 | python-dotenv + `.env` | 提供 `.env.example`，`.env` 不入库 |
| 测试 | pytest + pytest-cov | 单元测试 + 集成测试，核心逻辑覆盖率 ≥ 80% |
| 版本控制 | Git | 提交 ≥ 10 条，commit message 规范 |

### 6.1 目录结构（对齐培训推荐结构）

```
AI招生助手/
├── api/               # RobustAPIClient、LLMClient
├── collector/         # M1 招生数据采集
├── processor/         # M2 清洗/校验、AI 问答、推荐、FAISS 索引
├── database/          # engine / SessionLocal / Base / models.py
├── services/          # M3 入库（含留言/公告）、M4 统计与公告管理、认证服务
├── config/            # settings.py（.env 加载）
├── tests/             # test_collector / test_processor / test_services / test_integration
├── data/              # majors.csv / admission_scores.csv / faq.json / mock/
├── main.py            # FastAPI 入口
├── demo.py            # 一键演示核心 + 扩展功能
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 7. 需求追溯表（需求 → 模块 → 表 → 验收）

| 需求条目 | 模块 | 涉及表 | 验收证据 |
|----------|------|--------|----------|
| 招生知识库构建 | M1 | `knowledge_docs` | 导入后表内条数 > 0，重复导入为 0 |
| 多轮对话与上下文 | M2 | `conversations` / `messages` | 追问"那软件工程呢"能继承上一轮意图 |
| 敏感问题强匹配 | M2 | `knowledge_docs(sensitivity_level=1)` | 命中关键词时答案与库内标准文本完全一致 |
| 咨询记录沉淀 | M3 | `messages` | 每轮问答新增 2 条消息 |
| 用户关注点统计 | M4 | `messages` / `student_profiles` | 管理员看板返回 TopN 热点与关注点分布 |
| 权限隔离 | M3/M4 | `users.role` | 普通用户越权访问他人会话返回 4003 |
| 用户向学校提问 | M3 | `user_questions` | 提交后 `status=pending`，管理员列表可见；5 分钟内重复提交返回 4008 |
| 提问受理与答复 | M4 | `user_questions` | 答复后 `status=answered`，用户端"我的提问"可见 `answer_content` |
| 提问统计 | M4 | `user_questions` | 返回待处理数、平均响应时长、分类分布 TopN |
| 公告发布与展示 | M4 | `announcements` | 发布后普通用户 `GET /api/announcements` 可见；草稿不可见 |
| 公告可追溯提问 | M4 | `announcements.related_question_id` | 由提问生成的公告能反查原提问 |
| 公告回灌知识库（双写） | M4 → M1/M2 | `announcements` / `knowledge_docs` | 发布后知识条目新增 1 条；重复发布仍为 1 条；下架后条目删除且检索不再命中 |
| 专业推荐（P1） | M5 | `recommendations` / `admission_scores` | 返回冲/稳/保三档且带匹配理由 |
| 报表导出（P1） | M6 | 统计结果 | 导出 CSV 可被 Excel 正常打开 |

---

## 8. 工程验收对照（对齐培训验收标准）

| 验收项 | 本项目落点 |
|--------|-----------|
| 需求文档五部分齐全 | 第1~5章 + 技术选型，含接口契约 |
| 核心数据流可运行 | M1→M2→M3→M4 全链路，`demo.py` 一键演示 |
| 数据库 ≥ 3 表且有外键 | 10 张表，11 组外键关系，含测试数据 |
| 至少 2 个扩展功能 | M5 专业推荐、M6 运营看板与报表导出 |
| `.env` 配置管理 | `config/settings.py` + `.env.example`，`.env` 入 `.gitignore` |
| 单元测试 + 集成测试 | `tests/` 下按模块拆分，`pytest -v` 全绿 |
| 入库前去重 | 见 4.12 去重规则汇总 |
| `demo.py` 演示 | 覆盖 M1~M6 |
| README + requirements | 含配置与运行步骤 |
| Git 提交 ≥ 10 条 | 按模块分批提交，message 遵循 Conventional Commits |
