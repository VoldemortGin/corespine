"""config.env 合约:PREFIX_* -> frozen dataclass,类型转换 + 默认值 + 必填校验。"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from corespine.config.env import env_key, load_from_env
from corespine.errors import ConfigError


@dataclass(frozen=True)
class DemoConfig:
    db_path: str = "data/x.db"
    workers: int = 1
    debug: bool = False
    rate: float = 0.5
    note: str | None = None


def test_env_key_format():
    assert env_key("DEMO", "db_path") == "DEMO_DB_PATH"
    assert env_key("DEMO_", "workers") == "DEMO_WORKERS"


def test_reads_and_coerces_types():
    cfg = load_from_env(
        DemoConfig,
        prefix="DEMO",
        env={
            "DEMO_WORKERS": "4",
            "DEMO_DEBUG": "true",
            "DEMO_RATE": "1.5",
            "DEMO_NOTE": "hi",
        },
    )
    assert cfg.workers == 4 and isinstance(cfg.workers, int)
    assert cfg.debug is True
    assert cfg.rate == 1.5
    assert cfg.note == "hi"
    # 未提供的字段回落到 dataclass 默认值。
    assert cfg.db_path == "data/x.db"


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("yes", True), ("ON", True), ("0", False), ("no", False), ("off", False)],
)
def test_bool_parsing(raw, expected):
    cfg = load_from_env(DemoConfig, prefix="DEMO", env={"DEMO_DEBUG": raw})
    assert cfg.debug is expected


def test_empty_env_uses_all_defaults():
    cfg = load_from_env(DemoConfig, prefix="DEMO", env={})
    assert cfg == DemoConfig()


def test_missing_required_field_raises():
    @dataclass(frozen=True)
    class Required:
        token: str  # 无默认值

    with pytest.raises(ValueError) as ei:
        load_from_env(Required, prefix="APP", env={})
    assert "APP_TOKEN" in str(ei.value)


def test_non_dataclass_rejected():
    class NotADataclass:
        pass

    with pytest.raises(TypeError):
        load_from_env(NotADataclass, prefix="X", env={})


# --- 类型转换 / 错误信息 / init=False / 空 prefix --------------------------------------


def test_conversion_error_names_env_var_but_not_value():
    with pytest.raises(ConfigError) as ei:
        load_from_env(DemoConfig, prefix="APP", env={"APP_WORKERS": "s3cr3t-abc"})
    assert "APP_WORKERS" in str(ei.value)
    assert "s3cr3t-abc" not in str(ei.value)
    assert "s3cr3t-abc" not in repr(ei.value.context)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    assert isinstance(ei.value, ValueError)  # 既有 except ValueError 仍成立


def test_bool_error_does_not_leak_value():
    with pytest.raises(ConfigError) as ei:
        load_from_env(DemoConfig, prefix="APP", env={"APP_DEBUG": "maybe-secret"})
    assert "APP_DEBUG" in str(ei.value) and "maybe-secret" not in str(ei.value)


@dataclass(frozen=True)
class RichConfig:
    root: Path = Path(".")
    tags: list[str] = field(default_factory=list)
    limit: int | None = None
    ratio: float | None = None
    where: Path | None = None
    label: str | None = None


def test_path_list_and_optional_conversion():
    cfg = load_from_env(
        RichConfig,
        prefix="R",
        env={"R_ROOT": "/tmp/x", "R_TAGS": " a, b ,,c ", "R_LIMIT": "7", "R_WHERE": "rel/p"},
    )
    assert cfg.root == Path("/tmp/x") and isinstance(cfg.root, Path)
    assert cfg.tags == ["a", "b", "c"]
    assert cfg.limit == 7
    assert cfg.where == Path("rel/p")


def test_empty_string_for_optional_non_str_is_none():
    cfg = load_from_env(
        RichConfig, prefix="R", env={"R_LIMIT": "", "R_RATIO": "", "R_WHERE": "", "R_TAGS": ""}
    )
    assert cfg.limit is None and cfg.ratio is None and cfg.where is None
    assert cfg.tags == []


def test_optional_str_empty_stays_empty_string_for_compat():
    assert load_from_env(RichConfig, prefix="R", env={"R_LABEL": ""}).label == ""


def test_empty_string_for_required_int_is_config_error():
    with pytest.raises(ConfigError) as ei:
        load_from_env(DemoConfig, prefix="APP", env={"APP_WORKERS": ""})
    assert "APP_WORKERS" in str(ei.value)


def test_unsupported_annotations_fail_closed():
    @dataclass(frozen=True)
    class Union2:
        v: int | str = 1

    @dataclass(frozen=True)
    class ListInt:
        v: list[int] = field(default_factory=list)

    @dataclass(frozen=True)
    class AnyField:
        v: object = None

    for cls in (Union2, ListInt, AnyField):
        with pytest.raises(ConfigError) as ei:
            load_from_env(cls, prefix="P", env={"P_V": "x"})
        assert "P_V" in str(ei.value)
        # 不设对应 env 时不受影响(默认值照旧)。
        load_from_env(cls, prefix="P", env={})


def test_init_false_fields_are_skipped():
    @dataclass(frozen=True)
    class WithDerived:
        a: int = 1
        derived: int = field(init=False, default=0)

    cfg = load_from_env(WithDerived, prefix="P", env={"P_A": "2", "P_DERIVED": "9"})
    assert cfg.a == 2 and cfg.derived == 0


def test_empty_prefix_has_no_leading_underscore():
    assert env_key("", "x") == "X"
    assert env_key("_", "x") == "X"
    assert env_key("APP", "x") == "APP_X"
    cfg = load_from_env(DemoConfig, prefix="", env={"WORKERS": "3"})
    assert cfg.workers == 3
