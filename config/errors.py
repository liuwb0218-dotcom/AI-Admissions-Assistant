"""统一业务异常：各层抛 AppError，main.py 统一转成 {code,message,data}。"""


class AppError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def bad_request(message: str) -> AppError:
    return AppError(4001, message, 400)


def forbidden(message: str = "无权限访问该资源") -> AppError:
    return AppError(4003, message, 403)


def not_found(message: str = "资源不存在") -> AppError:
    return AppError(4004, message, 404)
