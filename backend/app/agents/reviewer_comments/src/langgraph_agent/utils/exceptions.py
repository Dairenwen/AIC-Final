"""包内公共异常类型。

从 backend/app/common/errors.py 与 backend/app/config.py 的 ConfigError 迁移而来，
不依赖 Flask 或任何 Web 框架。
"""

from __future__ import annotations

from typing import Any


class ConfigError(RuntimeError):
    """配置缺失或非法时抛出，附带简体中文说明。"""


class ErrorCode:
    """统一业务错误码。"""

    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ANALYSIS_NOT_READY = "ANALYSIS_NOT_READY"
    REPLY_IN_PROGRESS = "REPLY_IN_PROGRESS"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    PORT_ERROR = "PORT_ERROR"
    AGENT_ERROR = "AGENT_ERROR"


class AppError(Exception):
    """可转换为统一错误响应的业务异常基类。"""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details if details is not None else {}


class InvalidInput(AppError):
    """请求参数校验失败。"""

    def __init__(
        self,
        message: str = "请求参数无效",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.INVALID_INPUT, message, 400, details)


class NotFound(AppError):
    """请求的业务资源不存在。"""

    def __init__(
        self,
        message: str = "请求的资源不存在",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, 404, details)


class VersionConflict(AppError):
    """输入版本不匹配。"""

    def __init__(
        self,
        message: str = "输入版本冲突，请刷新后重试",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.VERSION_CONFLICT, message, 409, details)


class AnalysisNotReady(AppError):
    """共享分析尚未确认，暂不能生成回复。"""

    def __init__(
        self,
        message: str = "分析尚未就绪，请先完成并确认分析",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.ANALYSIS_NOT_READY, message, 409, details)


class ReplyInProgress(AppError):
    """对应来源的回复正在执行，暂不能修改表达设置。"""

    def __init__(
        self,
        message: str = "回复正在生成或等待策略确认，完成当前流程后再修改表达设置。",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.REPLY_IN_PROGRESS, message, 409, details)


class PortError(AppError):
    """端口 / 适配器层错误（数据库、外部存储等）。"""

    def __init__(
        self,
        message: str = "端口适配层错误",
        details: dict[str, Any] | None = None,
        *,
        http_status: int = 500,
    ) -> None:
        super().__init__(ErrorCode.PORT_ERROR, message, http_status, details)


class AgentError(AppError):
    """Agent / 图执行过程中的业务错误。"""

    def __init__(
        self,
        message: str = "Agent 执行错误",
        details: dict[str, Any] | None = None,
        *,
        http_status: int = 500,
    ) -> None:
        super().__init__(ErrorCode.AGENT_ERROR, message, http_status, details)
