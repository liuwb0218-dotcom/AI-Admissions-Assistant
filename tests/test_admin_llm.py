"""管理端手动添加模型：写入 system_settings，并出现在该后端的可选模型列表。"""

from database.connection import SessionLocal
from processor.model_config import get_llm_config


def _admin_headers(client, username: str) -> dict:
    from services import user_service

    db = SessionLocal()
    try:
        user_service.register(db, username, "pass123456", role="admin")
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    client.post("/api/auth/register", json={"username": username, "password": "pass123456"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_add_model_persists_and_appears_in_options(client):
    headers = _admin_headers(client, "llm_admin_add")

    created = client.post(
        "/api/admin/llm/models",
        json={"provider": "deepseek", "model": "deepseek-chat-preview"},
        headers=headers,
    ).json()
    assert created["code"] == 0
    assert "deepseek-chat-preview" in created["data"]["models"]

    # 重复添加不产生重复项
    again = client.post(
        "/api/admin/llm/models",
        json={"provider": "deepseek", "model": "deepseek-chat-preview"},
        headers=headers,
    ).json()
    assert again["data"]["models"].count("deepseek-chat-preview") == 1

    db = SessionLocal()
    try:
        config = get_llm_config(db)
    finally:
        db.close()
    assert "deepseek-chat-preview" in config["custom_models"]["deepseek"]


def test_add_model_rejects_empty_name(client):
    headers = _admin_headers(client, "llm_admin_empty")
    response = client.post(
        "/api/admin/llm/models",
        json={"provider": "local", "model": "   "},
        headers=headers,
    )
    assert response.status_code == 400


def test_add_model_rejects_mock_provider(client):
    headers = _admin_headers(client, "llm_admin_mock")
    response = client.post(
        "/api/admin/llm/models",
        json={"provider": "mock", "model": "anything"},
        headers=headers,
    )
    assert response.status_code == 400


def test_create_custom_provider_and_switch(client):
    headers = _admin_headers(client, "llm_admin_provider")

    created = client.post(
        "/api/admin/llm/providers",
        json={
            "name": "通义千问",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "api_key": "sk-test",
            "model": "qwen-plus",
        },
        headers=headers,
    ).json()
    assert created["code"] == 0
    provider = created["data"]["provider"]
    assert provider.startswith("custom_")
    assert created["data"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert created["data"]["models"] == ["qwen-plus"]

    listed = client.get("/api/admin/llm", headers=headers).json()["data"]
    entry = next(item for item in listed["providers"] if item["value"] == provider)
    assert entry["label"] == "通义千问"
    assert entry["custom"] is True
    assert "api_key" not in entry

    switched = client.put(
        "/api/admin/llm",
        json={"provider": provider, "model": "qwen-plus"},
        headers=headers,
    ).json()
    assert switched["data"]["provider"] == provider

    # 还原成离线兜底，避免影响后续用例
    client.put("/api/admin/llm", json={"provider": "mock"}, headers=headers)


def test_create_custom_provider_validates_input(client):
    headers = _admin_headers(client, "llm_admin_provider_bad")

    bad_url = client.post(
        "/api/admin/llm/providers",
        json={"name": "测试", "base_url": "ftp://example.com", "model": "m"},
        headers=headers,
    )
    assert bad_url.status_code == 400

    no_model = client.post(
        "/api/admin/llm/providers",
        json={"name": "测试2", "base_url": "https://example.com/v1", "model": "  "},
        headers=headers,
    )
    assert no_model.status_code == 400


def test_llm_client_calls_custom_provider(monkeypatch):
    from processor import llm_client

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "自定义后端回答"}}]}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "body": json})
        return FakeResponse()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.LLMClient(
        provider="custom_demo",
        model="qwen-plus",
        provider_config={
            "custom_demo": {
                "name": "通义千问",
                "base_url": "https://example.com/v1/",
                "api_key": "sk-test",
            }
        },
    )
    answer = client.chat([{"role": "user", "content": "你好"}], mock_answer="mock")

    assert answer == "自定义后端回答"
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "qwen-plus"
