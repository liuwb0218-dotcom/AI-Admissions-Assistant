"""全局配置：统一从环境变量 / .env 读取，其余层只读本模块的 settings。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# override=False：测试里用 os.environ 覆盖时，.env 不会把它盖回去
load_dotenv(BASE_DIR / ".env", override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_url: str
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_minutes: int
    llm_provider: str
    llm_fallback_to_mock: bool
    local_llm_base_url: str
    local_llm_model: str
    local_llm_timeout: int
    deepseek_base_url: str
    deepseek_model: str
    deepseek_api_key: str
    deepseek_timeout: int
    retrieval_top_k: int
    history_rounds: int
    neu_score_csv: str
    neu_plan_csv: str
    college_intro_dir: str


def _load() -> Settings:
    return Settings(
        app_name="AI招生助手",
        database_url=os.getenv(
            "DATABASE_URL",
            "mysql+pymysql://root:@127.0.0.1:3306/aistudent?charset=utf8mb4",
        ),
        jwt_secret=os.getenv("JWT_SECRET", "please-change-this-to-a-random-string"),
        jwt_algorithm="HS256",
        jwt_expire_minutes=_int("JWT_EXPIRE_MINUTES", 120),
        llm_provider=os.getenv("LLM_PROVIDER", "local").strip().lower(),
        llm_fallback_to_mock=_bool("LLM_FALLBACK_TO_MOCK", True),
        local_llm_base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        local_llm_model=os.getenv("LOCAL_LLM_MODEL", "qwen2.5:3b"),
        local_llm_timeout=_int("LOCAL_LLM_TIMEOUT", 120),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_timeout=_int("DEEPSEEK_TIMEOUT", 15),
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 5),
        history_rounds=_int("HISTORY_ROUNDS", 5),
        neu_score_csv=os.getenv(
            "NEU_SCORE_CSV",
            str(DATA_DIR / "gkcx" / "东北师大_历年分数线_全量.csv"),
        ),
        neu_plan_csv=os.getenv(
            "NEU_PLAN_CSV",
            str(DATA_DIR / "gkcx" / "东北师大_历年招生计划_含学费.csv"),
        ),
        college_intro_dir=os.getenv(
            "COLLEGE_INTRO_DIR",
            str(DATA_DIR / "college_intro"),
        ),
    )


settings = _load()
