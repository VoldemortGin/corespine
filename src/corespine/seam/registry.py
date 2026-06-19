"""缝(seam)注册表:把"选哪个实现"从改代码降为一个 spec 字符串。

这是 ragspine `make_vector_store` 的泛化形式——domain-neutral,不绑任何具体缝。
一个 Registry 实例代表一条缝(如 "vector_store" / "llm" / "queue"):

    - register(name, factory):登记一个 名字->工厂 的内置实现;
    - make(spec, **kwargs):大小写/留白/连字符不敏感地把 spec 解析到工厂并构造实例;
    - 内置名找不到时,回落到 importlib.metadata 的 entry-point 自动发现
      (group 形如 "corespine.<seam>"),让第三方装包即扩展,无需改核心代码;
    - 仍找不到则抛 ValueError,把【当前可用的全部名字】列清楚,绝不让人猜。

lazy_extra_import 是配套助手:延迟 import 一个可选依赖,缺失时把裸 ImportError
翻译成"pip install <pkg>[<extra>]"的友好提示——支撑"离线精简默认 + 可选重依赖"
的范式(核心默认路径零重依赖,真实后端的 SDK 仅在选用时才 import)。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from importlib import metadata
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# 一个工厂:任意关键字参数 -> 一个实现实例。
Factory = Callable[..., T]


def _normalize(spec: str) -> str:
    """名字归一:去首尾留白 + 转小写 + 把连字符/空格统一成下划线。

    使 "In-Process" / " in_process " / "IN PROCESS" 都解析到同一个键。
    """
    return spec.strip().lower().replace("-", "_").replace(" ", "_")


class Registry(Generic[T]):
    """一条缝的 名字->工厂 注册表 + spec 解析 + entry-point 自动发现。"""

    def __init__(self, seam: str) -> None:
        # seam 名既用于 entry-point group("corespine.<seam>"),也用于报错信息。
        self._seam = seam
        self._factories: dict[str, Factory[T]] = {}

    @property
    def seam(self) -> str:
        return self._seam

    @property
    def group(self) -> str:
        """entry-point 自动发现使用的 group 名。"""
        return f"corespine.{self._seam}"

    def register(self, name: str, factory: Factory[T]) -> None:
        """登记一个内置实现;名字归一后入表(同名重复登记则后者覆盖)。"""
        self._factories[_normalize(name)] = factory

    def names(self) -> list[str]:
        """当前【全部可用】名字:内置 + entry-point 发现,去重后按字典序。"""
        return sorted(set(self._factories) | set(self._discover()))

    def _discover(self) -> dict[str, metadata.EntryPoint]:
        """从 importlib.metadata entry points 发现第三方实现(group=corespine.<seam>)。

        延迟到解析时才扫,不在 import 期付出代价;每个 ep 的 load() 也按需触发。
        返回 归一名 -> EntryPoint(其 load() 给出真正的工厂可调用对象)。
        """
        eps = metadata.entry_points(group=self.group)
        return {_normalize(ep.name): ep for ep in eps}

    def make(self, spec: str, **kwargs: Any) -> T:
        """把 spec 解析到工厂并构造实例(大小写/留白/连字符不敏感)。

        解析顺序:内置注册 -> entry-point 发现 -> 抛 ValueError(列清可用名)。
        """
        key = _normalize(spec)
        factory = self._factories.get(key)
        if factory is not None:
            return factory(**kwargs)
        # 回落:entry-point 自动发现(第三方装包即扩展,无需改核心)。
        discovered = self._discover()
        ep = discovered.get(key)
        if ep is not None:
            return ep.load()(**kwargs)
        raise ValueError(self._unknown_message(spec, discovered))

    def _unknown_message(self, spec: str, discovered: dict[str, metadata.EntryPoint]) -> str:
        available = sorted(set(self._factories) | set(discovered))
        listed = ", ".join(available) if available else "(无)"
        return (
            f"未知的 {self._seam} spec:{spec!r}。当前可用:{listed}"
            f"(内置注册或经 entry-point group {self.group!r} 发现;"
            "第三方实现可装包扩展)。"
        )


def lazy_extra_import(module: str, *, pkg: str, extra: str) -> Any:
    """延迟 import 一个可选依赖;缺失时给出"pip install <pkg>[<extra>]"友好提示。

    用于"离线精简默认 + 可选重依赖"的范式:核心默认路径零重依赖,真实后端的 SDK
    仅在选用该 adapter 时才 import。把裸 ImportError 翻译成可直接照做的安装指引,
    而不是让调用方对着 ModuleNotFoundError 自己猜该装哪个 extra。
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"缺少可选依赖 {module!r}:请先 `pip install {pkg}[{extra}]` 再重试。"
        ) from exc
