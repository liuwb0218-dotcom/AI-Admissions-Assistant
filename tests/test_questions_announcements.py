"""提问受理、公告发布与知识库双写的接口测试。"""

from database.connection import SessionLocal
from database.models import KnowledgeDoc


def _auth(client, username: str, password: str = "pass123456", role: str = "user") -> dict:
    if role == "admin":
        from services import user_service

        db = SessionLocal()
        try:
            user_service.register(db, username, password, role="admin")
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    client.post("/api/auth/register", json={"username": username, "password": password})
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_question_submit_answer_and_close(client):
    user_headers = _auth(client, "q_user_1")
    admin_headers = _auth(client, "q_admin_1", role="admin")

    created = client.post(
        "/api/questions",
        json={"category": "score", "content": "请问吉林省物理类 600 分可以报哪些专业？"},
        headers=user_headers,
    ).json()
    assert created["code"] == 0
    question_id = created["data"]["id"]
    assert created["data"]["status"] == "pending"

    # 普通用户不能受理
    denied = client.post(
        f"/api/questions/{question_id}/answer",
        json={"answer_content": "越权答复"},
        headers=user_headers,
    )
    assert denied.status_code == 403

    answered = client.post(
        f"/api/questions/{question_id}/answer",
        json={"answer_content": "可以参考数学类、统计学类等专业，具体以当年计划为准。"},
        headers=admin_headers,
    ).json()
    assert answered["data"]["status"] == "answered"

    mine = client.get("/api/questions/my", headers=user_headers).json()["data"]
    assert mine[0]["id"] == question_id
    assert mine[0]["answer_content"].startswith("可以参考数学类")

    # 重复提交同内容 5 分钟内只保留一条
    duplicated = client.post(
        "/api/questions",
        json={"category": "score", "content": "请问吉林省物理类 600 分可以报哪些专业？"},
        headers=user_headers,
    ).json()
    assert duplicated["data"]["id"] == question_id
    assert duplicated["data"]["duplicated"] is True

    closed = client.post(
        f"/api/questions/{question_id}/close", headers=admin_headers
    ).json()
    assert closed["data"]["status"] == "closed"

    denied_answer = client.post(
        f"/api/questions/{question_id}/answer",
        json={"answer_content": "关闭后不应再答复"},
        headers=admin_headers,
    )
    assert denied_answer.status_code == 400


def test_announcement_publish_syncs_and_unpublish_removes(client):
    admin_headers = _auth(client, "a_admin_1", role="admin")
    payload = {
        "title": "2026 年招生章程已发布",
        "content": "2026 年本科招生章程已发布，请考生登录本科招生网查看完整内容与录取规则。",
        "category": "policy",
    }

    created = client.post("/api/announcements", json=payload, headers=admin_headers).json()
    announcement_id = created["data"]["id"]
    assert created["data"]["is_published"] == 0

    published = client.post(
        f"/api/announcements/{announcement_id}/publish", headers=admin_headers
    ).json()
    assert published["data"]["is_published"] == 1
    assert published["data"]["knowledge_synced"] == 1

    db = SessionLocal()
    try:
        doc = (
            db.query(KnowledgeDoc)
            .filter(KnowledgeDoc.source_announcement_id == announcement_id)
            .first()
        )
        assert doc is not None
        assert doc.source_type == "announcement"
        assert doc.category == "announcement"
        assert doc.content == payload["content"]
    finally:
        db.close()

    unpublished = client.post(
        f"/api/announcements/{announcement_id}/unpublish", headers=admin_headers
    ).json()
    assert unpublished["data"]["is_published"] == 0

    db = SessionLocal()
    try:
        remaining = (
            db.query(KnowledgeDoc)
            .filter(KnowledgeDoc.source_announcement_id == announcement_id)
            .count()
        )
        assert remaining == 0
    finally:
        db.close()


def test_draft_announcement_hidden_from_normal_user(client):
    admin_headers = _auth(client, "a_admin_2", role="admin")
    user_headers = _auth(client, "q_user_2")

    client.post(
        "/api/announcements",
        json={
            "title": "尚未发布的草稿公告",
            "content": "这条公告还处于草稿状态，普通用户不应该看到它。",
            "category": "other",
        },
        headers=admin_headers,
    )

    visible = client.get("/api/announcements", headers=user_headers).json()["data"]
    assert all(item["is_published"] == 1 for item in visible)

    denied = client.get(
        "/api/announcements?include_draft=true", headers=user_headers
    )
    assert denied.status_code == 403


def test_admin_stats_requires_admin(client):
    user_headers = _auth(client, "q_user_3")
    assert client.get("/api/admin/stats", headers=user_headers).status_code == 403

    admin_headers = _auth(client, "a_admin_3", role="admin")
    stats = client.get("/api/admin/stats", headers=admin_headers).json()["data"]
    assert stats["users"] >= 1
    assert set(stats["questions"]) == {"pending", "answered", "closed"}
