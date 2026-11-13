"""一键演示：建表 → 导入 → 注册 → 提问 → 查看会话记录。

运行： python demo.py
退出码 0 表示全链路通过。
"""

import sys

from collector.importer import import_all
from config.settings import settings
from database.connection import SessionLocal, init_db
from database.models import Message, User
from processor import chat_service
from services import user_service

DEMO_USER = "demo_student"
DEMO_PASSWORD = "demo123456"
DEMO_QUESTION = "计算机科学与技术去年在广东的录取分数是多少？"


def main() -> int:
    print(f"[1/5] 初始化数据库：{settings.database_url}")
    try:
        init_db()
    except Exception as exc:
        print(f"  数据库连接失败：{exc}")
        print("  请检查 .env 中的 DATABASE_URL，并确认 MySQL 已启动")
        return 1

    db = SessionLocal()
    try:
        print("[2/5] 导入招生数据")
        summary = import_all(db)
        for name, stat in summary.items():
            print(
                f"  - {name}: 新增 {stat['inserted']} / 跳过 {stat['skipped']} / 非法 {stat['invalid']}"
            )
        db.commit()

        print("[3/5] 准备演示用户")
        user = db.query(User).filter(User.username == DEMO_USER).first()
        if user is None:
            user = user_service.register(db, DEMO_USER, DEMO_PASSWORD)
            db.commit()
            print(f"  - 已创建 {DEMO_USER} / {DEMO_PASSWORD}")
        else:
            print(f"  - 复用已有用户 {DEMO_USER}")

        print("[4/5] 发起咨询")
        result = chat_service.handle_chat(db, user, DEMO_QUESTION)
        db.commit()
        print(f"  问：{DEMO_QUESTION}")
        print(f"  答：{result['answer']}")
        print(f"  引用：{result['refs']}")
        print(f"  意图：{result['intent']} / 模型后端：{result['provider']}")

        print("[5/5] 会话记录")
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == result["conversation_id"])
            .order_by(Message.id)
            .all()
        )
        for row in rows:
            preview = (row.content or "").replace("\n", " ")[:60]
            print(f"  [{row.role}] {preview}")

        print("演示完成。")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"演示失败：{exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
