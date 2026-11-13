"""ORM 模型：与需求文档第 4 章的 10 张表一一对应。

约定：
- 不使用 MySQL 专有类型（JSON/ENUM），便于测试用 SQLite 跑通；
- 不定义 relationship，跨表查询一律显式 filter，避免隐式懒加载。
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from database.connection import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    province = Column(String(20), nullable=False)
    score = Column(Integer, nullable=True)
    rank_no = Column(Integer, nullable=True)
    subject_combo = Column(String(50), nullable=True)
    interest_tags = Column(String(200), nullable=True)
    career_intent = Column(String(50), nullable=True)
    # 1 接受 / 0 不接受 / NULL 未填
    adjust_accepted = Column(SmallInteger, nullable=True)
    exam_year = Column(Integer, nullable=True)
    special_qualification = Column(String(50), nullable=True)
    preferred_regions = Column(String(200), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class Major(Base):
    """专业维表：身份 = 归一化名称 + 专业属性（师范类/普通类）。"""

    __tablename__ = "majors"
    __table_args__ = (UniqueConstraint("name", "major_attr", name="uq_major_name_attr"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    display_name = Column(String(120), nullable=False)
    major_attr = Column(String(20), nullable=False, default="普通类")
    department = Column(String(100), nullable=True)
    # 系级归属：college_departments.id；department 仍保留学院名
    department_id = Column(
        Integer, ForeignKey("college_departments.id"), nullable=True, index=True
    )
    subject_requirement = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class AdmissionScore(Base):
    """分数线事实表：唯一键必须含 major_type（科类），否则物理类/历史类会互相覆盖。"""

    __tablename__ = "admission_scores"
    __table_args__ = (
        UniqueConstraint(
            "year", "province", "plan_type", "major_type", "major_id", name="uq_score_key"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(SmallInteger, nullable=False, index=True)
    province = Column(String(20), nullable=False, index=True)
    plan_type = Column(String(30), nullable=False)
    major_type = Column(String(30), nullable=False)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=False, index=True)
    min_score = Column(Integer, nullable=True)
    min_rank = Column(Integer, nullable=True)
    baseline = Column(Integer, nullable=True)
    min_culture_score = Column(Integer, nullable=True)
    min_major_score = Column(Integer, nullable=True)
    max_culture_score = Column(Integer, nullable=True)
    max_major_score = Column(Integer, nullable=True)
    max_score = Column(Integer, nullable=True)
    max_rank = Column(Integer, nullable=True)
    avg_culture_score = Column(Integer, nullable=True)
    avg_major_score = Column(Integer, nullable=True)
    avg_score = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class EnrollmentPlan(Base):
    """招生计划事实表：与分数线同键，学费单独拆 raw 字段保留“免费”语义。"""

    __tablename__ = "enrollment_plans"
    __table_args__ = (
        UniqueConstraint(
            "year", "province", "plan_type", "major_type", "major_id", name="uq_plan_key"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(SmallInteger, nullable=False, index=True)
    province = Column(String(20), nullable=False, index=True)
    plan_type = Column(String(30), nullable=False)
    major_type = Column(String(30), nullable=False)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=False, index=True)
    plan_count = Column(Integer, nullable=True)
    edu_len = Column(String(10), nullable=True)
    tuition = Column(Integer, nullable=True)
    tuition_raw = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class SchoolAdmissionScore(Base):
    """院校（专业组）投档线：不含具体专业，唯一键含专业组与计划类型。"""

    __tablename__ = "school_admission_scores"
    __table_args__ = (
        UniqueConstraint(
            "year",
            "province",
            "major_type",
            "batch",
            "group_name",
            "plan_type",
            name="uq_school_score_key",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(SmallInteger, nullable=False, index=True)
    province = Column(String(20), nullable=False, index=True)
    major_type = Column(String(30), nullable=False)
    batch = Column(String(30), nullable=False, default="")
    group_name = Column(String(30), nullable=False, default="")
    plan_type = Column(String(30), nullable=False)
    subject_requirement = Column(String(100), nullable=True)
    min_score = Column(Integer, nullable=True)
    min_rank = Column(Integer, nullable=True)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    __table_args__ = (UniqueConstraint("category", "title", name="uq_knowledge_category_title"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(30), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    source_file = Column(String(255), nullable=True)
    source_type = Column(String(20), nullable=False, default="file")
    source_announcement_id = Column(
        Integer, ForeignKey("announcements.id"), nullable=True, index=True
    )
    sensitivity_level = Column(SmallInteger, nullable=False, default=0)
    keywords = Column(String(255), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    last_active_at = Column(DateTime, nullable=False, default=datetime.now)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True
    )
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    intent = Column(String(50), nullable=True)
    refs = Column(String(500), nullable=True)
    links = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class UserQuestion(Base):
    __tablename__ = "user_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    answer_content = Column(Text, nullable=True)
    answered_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(30), nullable=False)
    related_question_id = Column(Integer, ForeignKey("user_questions.id"), nullable=True)
    is_published = Column(SmallInteger, nullable=False, default=0, index=True)
    published_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    view_count = Column(Integer, nullable=False, default=0)
    knowledge_synced = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class SystemSetting(Base):
    """运行时配置：管理员在前端修改的模型等设置，覆盖 .env 默认值。"""

    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=False)
    match_score = Column(Float, nullable=False, default=0)
    level = Column(String(10), nullable=False)
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class CollegeWebsite(Base):
    """学院官网链接：问答里提到学院时返回，后台可维护。"""

    __tablename__ = "college_websites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    aliases = Column(String(255), nullable=True)  # 逗号分隔别名，如“新闻学院”
    url = Column(String(255), nullable=False)
    is_enabled = Column(SmallInteger, nullable=False, default=1, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ScoreRankSegment(Base):
    """一分一段表：同省同年同科类下，每个分数对应的本段人数与累计人数。"""

    __tablename__ = "score_rank_segments"
    __table_args__ = (
        UniqueConstraint(
            "province", "year", "major_type", "score", name="uq_score_rank_segment"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    province = Column(String(20), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    major_type = Column(String(30), nullable=False)
    score = Column(Integer, nullable=False)
    segment_count = Column(Integer, nullable=False)
    cumulative_count = Column(Integer, nullable=False)
    source_url = Column(String(500), nullable=True)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )


class CollegeDepartment(Base):
    """系级单位：学院下的系/教研室/书院等，majors.department_id 指向这里。"""

    __tablename__ = "college_departments"
    __table_args__ = (
        UniqueConstraint("college_website_id", "name", name="uq_college_department"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    college_website_id = Column(
        Integer, ForeignKey("college_websites.id"), nullable=False, index=True
    )
    college = Column(String(100), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    evidence = Column(String(255), nullable=True)
    source_url = Column(String(255), nullable=True)
    is_enabled = Column(SmallInteger, nullable=False, default=1, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
