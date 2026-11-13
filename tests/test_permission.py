"""权限测试：普通用户不得读取他人会话。"""

from tests.conftest import login


def test_cannot_read_others_conversation(client):
    token_a = login(client, "pytest_user_a")
    token_b = login(client, "pytest_user_b")

    created = client.post(
        "/api/chat",
        json={"message": "软件工程专业学什么课程"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    conversation_id = created.json()["data"]["conversation_id"]

    response = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == 4003


def test_chat_requires_token(client):
    response = client.post("/api/chat", json={"message": "计算机专业怎么样"})

    assert response.status_code == 401
    assert response.json()["code"] == 4003
