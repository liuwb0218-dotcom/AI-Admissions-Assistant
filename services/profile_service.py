"""考生画像：字段校验、完整度计算与序列化。"""

from typing import Dict, Optional

from config.errors import bad_request
from database.models import StudentProfile
from processor.score_query import COMPREHENSIVE_PROVINCES, LEGACY_PROVINCES, PROVINCES

SPECIAL_CHOICES = ("无", "国家专项", "地方专项", "高校专项", "少数民族预科")

# 必填项决定资料完整度，推荐所需的省份 / 科类 / 分数 / 位次 / 调剂都在这里
REQUIRED_FIELDS = {
    "province": "省份",
    "subject_combo": "选科类别",
    "score": "高考分数",
    "rank_no": "位次",
    "adjust_accepted": "是否服从调剂",
}

PROFILE_FIELDS = (
    "province",
    "score",
    "rank_no",
    "subject_combo",
    "interest_tags",
    "career_intent",
    "adjust_accepted",
    "exam_year",
    "special_qualification",
    "preferred_regions",
)


def normalize_subject(province: str, subject: Optional[str]) -> Optional[str]:
    """科类跟随省份：综改省份固定“综合改革”，新疆/西藏用理工、文史。"""
    value = (subject or "").strip()
    if not value:
        return None
    if province in COMPREHENSIVE_PROVINCES:
        return "综合改革"
    if province in LEGACY_PROVINCES:
        mapping = {
            "物理类": "理工",
            "理工": "理工",
            "物理": "理工",
            "历史类": "文史",
            "文史": "文史",
            "历史": "文史",
        }
        allowed = "理工 / 文史"
    else:
        mapping = {
            "物理类": "物理类",
            "物理": "物理类",
            "理工": "物理类",
            "历史类": "历史类",
            "历史": "历史类",
            "文史": "历史类",
        }
        allowed = "物理类 / 历史类"
    if value not in mapping:
        raise bad_request(f"选科类别请选择{allowed}")
    return mapping[value]


def _check_text(field: str, value: str, limit: int) -> str:
    value = str(value).strip()
    if len(value) > limit:
        raise bad_request(f"{field}不能超过 {limit} 字")
    return value


def validate(payload: Dict, province: Optional[str] = None) -> Dict:
    """校验并规整画像字段；payload 可以是部分字段，province 传入已有画像的省份。"""
    clean: Dict = {}

    if payload.get("province") is not None:
        value = str(payload["province"]).strip()
        if value and value not in PROVINCES:
            raise bad_request("省份不在可选范围内")
        clean["province"] = value or None
        province = value

    if payload.get("score") is not None:
        score = int(payload["score"])
        if not (0 < score <= 750):
            raise bad_request("分数需在 1~750 之间")
        clean["score"] = score

    if payload.get("rank_no") is not None:
        rank_no = int(payload["rank_no"])
        if not (0 < rank_no <= 2_000_000):
            raise bad_request("位次需在 1~2000000 之间")
        clean["rank_no"] = rank_no

    if payload.get("exam_year") is not None:
        exam_year = int(payload["exam_year"])
        if not (2000 <= exam_year <= 2100):
            raise bad_request("高考年份不合法")
        clean["exam_year"] = exam_year

    if payload.get("subject_combo") is not None:
        value = str(payload["subject_combo"]).strip()
        if value:
            if not province:
                raise bad_request("请先选择省份再填写选科类别")
            clean["subject_combo"] = normalize_subject(province, value)
        else:
            clean["subject_combo"] = None

    if payload.get("adjust_accepted") is not None:
        clean["adjust_accepted"] = 1 if int(payload["adjust_accepted"]) else 0

    if payload.get("special_qualification") is not None:
        value = str(payload["special_qualification"]).strip()
        if value and value not in SPECIAL_CHOICES:
            raise bad_request("专项资格不在可选范围内")
        clean["special_qualification"] = value or None

    for field, limit in (("interest_tags", 200), ("career_intent", 50), ("preferred_regions", 200)):
        if payload.get(field) is not None:
            clean[field] = _check_text(
                {"interest_tags": "兴趣标签", "career_intent": "职业意向", "preferred_regions": "意向地区"}[
                    field
                ],
                payload[field],
                limit,
            ) or None

    return clean


def evaluate(profile: Optional[StudentProfile]) -> Dict:
    """资料完整度：按必填项等权计分，缺失项给出中文名。"""
    missing = [
        label
        for field, label in REQUIRED_FIELDS.items()
        if profile is None or getattr(profile, field, None) in (None, "")
    ]
    total = len(REQUIRED_FIELDS)
    return {
        "completeness": int(round((total - len(missing)) / total * 100)),
        "missing": missing,
        "is_complete": not missing,
    }


def serialize(profile: Optional[StudentProfile]) -> Dict:
    data = {field: (getattr(profile, field, None) if profile else None) for field in PROFILE_FIELDS}
    data.update(evaluate(profile))
    return data
