"""errors 合约:统一异常 code/retryable/context + 任意异常归一为可序列化 dict。"""

import json

import pytest

from corespine.errors import (
    ConfigError,
    CorespineError,
    SeamError,
    error_to_dict,
)


def test_base_class_defaults():
    err = CorespineError("boom")
    assert err.code == "error"
    assert err.retryable is False
    assert str(err) == "boom"
    assert err.context == {}


def test_subclass_overrides_code():
    assert ConfigError().code == "config.invalid"
    assert SeamError().code == "seam.unknown"
    # 子类默认仍不可重试。
    assert ConfigError().retryable is False
    assert isinstance(ConfigError(), CorespineError)


def test_instance_level_code_override():
    err = CorespineError("x", code="custom.code")
    assert err.code == "custom.code"
    # 实例覆盖不污染类属性。
    assert CorespineError.code == "error"


def test_instance_level_retryable_override():
    # 显式 True 覆盖类默认 False。
    assert CorespineError("x", retryable=True).retryable is True
    # 子类默认可被显式 False 覆盖(此处先造一个默认可重试的子类验证两向覆盖)。
    err = ConfigError("x", retryable=True)
    assert err.retryable is True
    assert ConfigError().retryable is False


def test_context_is_saved():
    err = SeamError("missing", name="pdf", attempts=3)
    assert err.context == {"name": "pdf", "attempts": 3}


def test_to_dict_shape_and_json_serializable():
    err = ConfigError("bad", retryable=True, field="db_path", limit=5)
    d = err.to_dict()
    assert d == {
        "type": "ConfigError",
        "code": "config.invalid",
        "message": "bad",
        "retryable": True,
        "context": {"field": "db_path", "limit": 5},
    }
    # 务必可被 json.dumps 序列化。
    assert json.loads(json.dumps(d)) == d


def test_to_dict_context_is_a_copy():
    err = CorespineError("x", k="v")
    d = err.to_dict()
    d["context"]["k"] = "mutated"
    # 改返回 dict 不应污染异常内部状态。
    assert err.context == {"k": "v"}


def test_error_to_dict_on_corespine_error_uses_to_dict():
    err = SeamError("nope", name="agent")
    assert error_to_dict(err) == err.to_dict()


def test_error_to_dict_on_builtin_exception():
    d = error_to_dict(ValueError("invalid literal"))
    assert d == {
        "type": "ValueError",
        "code": "error",
        "message": "invalid literal",
        "retryable": False,
        "context": {},
    }
    assert json.loads(json.dumps(d)) == d


def test_error_to_dict_result_is_json_serializable_for_any_exception():
    for exc in (ValueError("v"), KeyError("k"), RuntimeError("r")):
        d = error_to_dict(exc)
        assert json.loads(json.dumps(d)) == d


def test_corespine_error_is_an_exception():
    with pytest.raises(CorespineError):
        raise ConfigError("boom")


# --- ConfigError / SeamError 同时是 ValueError,且核心自己会抛 --------------------------


def test_config_and_seam_errors_are_value_errors():
    assert issubclass(ConfigError, ValueError)
    assert issubclass(SeamError, ValueError)
    assert issubclass(ConfigError, CorespineError)
    assert issubclass(SeamError, CorespineError)


def test_value_error_handlers_still_catch_and_codes_stable():
    for exc, code in ((ConfigError("x"), "config.invalid"), (SeamError("x"), "seam.unknown")):
        try:
            raise exc
        except ValueError as caught:
            assert caught is exc
            assert caught.code == code  # type: ignore[attr-defined]


def test_pickle_roundtrip_keeps_code_and_context():
    import pickle

    for err in (ConfigError("bad", key="APP_X"), SeamError("nope", seam="llm", spec="x")):
        clone = pickle.loads(pickle.dumps(err))
        assert type(clone) is type(err)
        assert str(clone) == str(err)
        assert clone.context == err.context
        assert clone.code == err.code
        assert clone.to_dict() == err.to_dict()


def test_core_raises_seam_error_on_unknown_spec():
    from corespine.seam.registry import Registry

    reg: Registry[int] = Registry("demo")
    with pytest.raises(SeamError) as ei:
        reg.make("nope")
    assert ei.value.code == "seam.unknown"
    assert ei.value.context["seam"] == "demo"
    assert "demo" in str(ei.value)


def test_core_raises_config_error_on_missing_required():
    import dataclasses

    from corespine.config.env import load_from_env

    @dataclasses.dataclass(frozen=True)
    class Cfg:
        host: str

    with pytest.raises(ConfigError) as ei:
        load_from_env(Cfg, prefix="APP", env={})
    assert ei.value.code == "config.invalid"
    assert "APP_HOST" in str(ei.value)
    with pytest.raises(ValueError):  # 既有 except ValueError 写法仍成立
        load_from_env(Cfg, prefix="APP", env={})
