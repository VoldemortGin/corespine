"""家族统一异常 + 错误归一(domain-neutral 原语)。

证据(rule of three):ragspine 的 `JobError(stage, retryable)` 与 `_error_dict_from_exc`、
pdfspine 的 `error.kind()` / `LimitKind`——两个消费者都在各自重造"带机器可判别码的异常 +
把异常拍成可序列化 dict"这同一块稳定面。这里只把【恰好那块】提上来:

- 一个统一基类 `CorespineError`:带稳定、可 grep 的 `code` 与 `retryable` 标志;
- 一个归一函数 `error_to_dict`:把【任意】异常拍成可被 `json.dumps` 序列化的 dict。

这里是【机制】:基类与归一形状。具体有哪些 code、哪条可重试,由各 app 自己绑
(ADR 0001 D6)。core 不预设任何业务语义,只给两个自然子类做示范。
"""

from __future__ import annotations


class CorespineError(Exception):
    """Spine 家族统一异常基类。

    `code` 是稳定、机器可 grep 的判别符(子类覆盖,如 "config.invalid"/"seam.unknown");
    `retryable` 标记此错是否值得重试。两者都既可在子类作类属性覆盖,也可在构造时实例级覆盖。
    任意关键字参数收进 `self.context`(普通 dict),用于携带可序列化的诊断上下文。
    """

    code: str = "error"
    retryable: bool = False

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        retryable: bool | None = None,
        **context: object,
    ) -> None:
        super().__init__(message)
        # 仅在显式传入时做实例级覆盖,否则沿用类属性默认。
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.context: dict[str, object] = dict(context)

    def to_dict(self) -> dict[str, object]:
        """归一为可被 json.dumps 序列化的 dict。"""
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "context": dict(self.context),
        }


def error_to_dict(exc: BaseException) -> dict[str, object]:
    """把【任意】异常归一为可序列化 dict(统一错误形状)。

    CorespineError 走其 `to_dict()`;其余异常给出同形状的保守默认
    (code="error"、retryable=False、context={}),便于跨进程/跨缝统一处理与日志。
    """
    if isinstance(exc, CorespineError):
        return exc.to_dict()
    return {
        "type": type(exc).__name__,
        "code": "error",
        "message": str(exc),
        "retryable": False,
        "context": {},
    }


class ConfigError(CorespineError):
    """配置非法(缺必填、类型不符等)。"""

    code = "config.invalid"


class SeamError(CorespineError):
    """缝相关错误(未知实现、工厂构造失败等)。"""

    code = "seam.unknown"
