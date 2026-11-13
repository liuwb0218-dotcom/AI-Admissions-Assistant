"""考生画像：字段校验、科类联动与完整度计算。"""

from tests.conftest import login


def _headers(client, username):
    return {"Authorization": f"Bearer {login(client, username)}"}


def test_empty_profile_reports_missing(client):
    headers = _headers(client, "profile_user_a")
    data = client.get("/api/profile", headers=headers).json()["data"]

    assert data["completeness"] == 0
    assert data["is_complete"] is False
    assert len(data["missing"]) == 5


def test_comprehensive_province_forces_subject(client):
    headers = _headers(client, "profile_user_b")
    data = client.put(
        "/api/profile",
        json={"province": "上海", "subject_combo": "物理类"},
        headers=headers,
    ).json()["data"]

    assert data["subject_combo"] == "综合改革"


def test_legacy_province_maps_subject(client):
    headers = _headers(client, "profile_user_c")
    data = client.put(
        "/api/profile",
        json={"province": "新疆", "subject_combo": "物理类"},
        headers=headers,
    ).json()["data"]

    assert data["subject_combo"] == "理工"


def test_invalid_province_rejected(client):
    headers = _headers(client, "profile_user_d")
    response = client.put("/api/profile", json={"province": "火星"}, headers=headers)

    assert response.status_code == 400


def test_score_out_of_range_rejected(client):
    headers = _headers(client, "profile_user_e")
    response = client.put(
        "/api/profile", json={"province": "河南", "score": 900}, headers=headers
    )

    assert response.status_code == 400


def test_complete_profile_roundtrip(client):
    headers = _headers(client, "profile_user_f")
    payload = {
        "province": "河南",
        "subject_combo": "物理类",
        "score": 599,
        "rank_no": 8000,
        "adjust_accepted": 1,
        "exam_year": 2026,
        "interest_tags": "师范,计算机",
        "career_intent": "教师",
        "preferred_regions": "吉林,北京",
    }
    data = client.put("/api/profile", json=payload, headers=headers).json()["data"]

    assert data["completeness"] == 100
    assert data["is_complete"] is True
    assert data["missing"] == []

    saved = client.get("/api/profile", headers=headers).json()["data"]
    assert saved["rank_no"] == 8000
    assert saved["adjust_accepted"] == 1
    assert saved["interest_tags"] == "师范,计算机"
    assert saved["subject_combo"] == "物理类"
