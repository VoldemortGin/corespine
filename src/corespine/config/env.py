"""env 驱动配置的基底助手:把 PREFIX_* 环境变量读进一个 frozen dataclass。

范式同 ragspine `ServiceConfig.from_env`——集中、声明式、可注入(测试传入自己的
env mapping,不碰进程环境)。但本助手是 domain-neutral 的:不预设任何具体字段,
只提供机制——"按字段名从 PREFIX_<FIELD> 读取 + 按注解类型转换 + 缺失用 dataclass
默认值"。app 声明自己的 frozen dataclass,调一次 load_from_env 即可。

支持的字段类型:str / int / float / bool(及其 `X | None` 可选形式)。bool 解析为
{1,true,yes,on} 为真、{0,false,no,off,""} 为假(均大小写不敏感)。未声明默认值
的字段若缺对应 env,则抛 ValueError(把缺失的 env 名报清楚)。
"""

from __future__ import annotations

import dataclasses
import os
import types
from collections.abc import Mapping
from typing import TypeVar, Union, get_args, get_origin, get_type_hints

T = TypeVar("T")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def env_key(prefix: str, field_name: str) -> str:
    """字段名 -> 环境变量名:PREFIX_FIELDNAME(字段名大写,前缀以下划线相连)。"""
    return f"{prefix.rstrip('_')}_{field_name.upper()}"


def _unwrap_optional(tp: object) -> object:
    """`X | None` / Optional[X] -> X;其余原样返回。"""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return tp


def _coerce(raw: str, tp: object) -> object:
    """按目标类型把 env 字符串转成值;str / 未知类型原样。"""
    base = _unwrap_optional(tp)
    if base is bool:
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"无法把 {raw!r} 解析为 bool")
    if base is int:
        return int(raw)
    if base is float:
        return float(raw)
    return raw


def load_from_env(
    cls: type[T], *, prefix: str, env: Mapping[str, str] | None = None
) -> T:
    """按 dataclass 字段从 PREFIX_* 读取并构造实例(缺失则用字段默认值)。

    env 可注入(默认读 os.environ);get_type_hints 解析注解,故 `from __future__
    import annotations` 下的字符串注解也能正确取到真实类型。
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} 不是 dataclass")
    source = os.environ if env is None else env
    hints = get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        key = env_key(prefix, f.name)
        if key in source:
            kwargs[f.name] = _coerce(source[key], hints.get(f.name, f.type))
        elif (
            f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        ):
            raise ValueError(f"缺少必填配置 {key}(字段 {f.name!r} 无默认值)")
        # 否则:留空,交给 dataclass 自身的默认值。
    return cls(**kwargs)
