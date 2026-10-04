"""env 驱动配置的基底助手:把 PREFIX_* 环境变量读进一个 frozen dataclass。

范式同 ragspine `ServiceConfig.from_env`——集中、声明式、可注入(测试传入自己的
env mapping,不碰进程环境)。但本助手是 domain-neutral 的:不预设任何具体字段,
只提供机制——"按字段名从 PREFIX_<FIELD> 读取 + 按注解类型转换 + 缺失用 dataclass
默认值"。app 声明自己的 frozen dataclass,调一次 load_from_env 即可。

支持的字段类型(其余注解在【对应 env 存在时】fail-closed 抛 ConfigError,绝不静默塞字符串):

- `str`;`int`;`float`;`bool`:{1,true,yes,on} 为真、{0,false,no,off,""} 为假(大小写不敏感);
- `pathlib.Path`;
- `list[str]`:以【逗号】分隔,各项去首尾空白、丢弃空项,空串得 `[]`;
- 以上任一的 `X | None` 可选形式:env 为空串时,`int` / `float` / `Path` / `list[str]` 得
  None(list 得 [])——`str | None` 与 `bool | None` 为兼容旧行为,空串仍分别得 "" / False。

转型失败抛 ConfigError(ValueError 子类),消息与 context 只含【环境变量名】,绝不含取值
(取值可能是密钥),并以 `from None` 断开异常链。未声明默认值的字段若缺对应 env,同样抛
ConfigError。`init=False` 的字段不读 env、交给 dataclass 自身。
"""

from __future__ import annotations

import dataclasses
import os
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Union, get_args, get_origin, get_type_hints

from corespine.errors import ConfigError

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def env_key(prefix: str, field_name: str) -> str:
    """字段名 -> 环境变量名:PREFIX_FIELDNAME(字段名大写,前缀以下划线相连);空前缀不加下划线。"""
    stem = prefix.rstrip("_")
    name = field_name.upper()
    return f"{stem}_{name}" if stem else name


def _unwrap_optional(tp: object) -> tuple[object, bool]:
    """`X | None` / Optional[X] -> (X, True);其余原样 (tp, False)。"""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return tp, False


def _is_str_list(tp: object) -> bool:
    return get_origin(tp) is list and get_args(tp) == (str,)


def _type_name(tp: object) -> str:
    return getattr(tp, "__qualname__", None) or str(tp)


def _coerce(raw: str, tp: object, key: str) -> object:
    """按目标类型把 env 字符串转成值;不支持的类型 / 转型失败抛 ConfigError(只带 env 名)。"""
    base, optional = _unwrap_optional(tp)
    try:
        if base is str:
            return raw
        if base is bool:
            low = raw.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError
        if base is int or base is float or base is Path:
            if optional and raw == "":
                return None
            return base(raw)
        if _is_str_list(base):
            if optional and raw == "":
                return None
            return [item.strip() for item in raw.split(",") if item.strip()]
    except ValueError:
        # 不带取值、不串联原异常:int("abc") 一类的原消息会回显取值,取值可能是密钥。
        raise ConfigError(f"环境变量 {key} 无法解析为 {_type_name(base)}", key=key) from None
    raise ConfigError(
        f"环境变量 {key} 对应字段的注解 {tp!r} 不受支持:仅支持 str / int / float / bool / Path / "
        "list[str] 及其 `X | None` 形式",
        key=key,
    )


def load_from_env[T](cls: type[T], *, prefix: str, env: Mapping[str, str] | None = None) -> T:
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
        if not f.init:
            continue  # init=False 字段由 dataclass 自己负责,不读 env
        key = env_key(prefix, f.name)
        if key in source:
            kwargs[f.name] = _coerce(source[key], hints.get(f.name, f.type), key)
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise ConfigError(f"缺少必填配置 {key}(字段 {f.name!r} 无默认值)", key=key)
        # 否则:留空,交给 dataclass 自身的默认值。
    return cls(**kwargs)
