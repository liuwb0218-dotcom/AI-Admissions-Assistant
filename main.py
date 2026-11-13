"""FastAPI 入口：只做路由挂载、异常转换与启动初始化。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import admin, announcements, auth, chat, conversations, majors, questions, school
from config.errors import AppError
from config.settings import BASE_DIR, settings
from database.connection import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[-1]}"


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        init_db()
        logger.info("数据库初始化完成: %s", _mask_url(settings.database_url))
    except Exception as exc:
        logger.error("数据库初始化失败: %s", exc)
        logger.error("请检查 .env 中的 DATABASE_URL，并确认 MySQL 已启动")
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"code": 4001, "message": "参数校验失败", "data": jsonable_encoder(exc.errors())},
    )


app.include_router(auth.router)
app.include_router(auth.profile_router)
app.include_router(chat.router)
app.include_router(majors.router)
app.include_router(school.router)
app.include_router(conversations.router)
app.include_router(questions.router)
app.include_router(announcements.router)
app.include_router(admin.router)

_STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health", tags=["运维"])
def health():
    return {"code": 0, "message": "ok", "data": {"app": settings.app_name}}
