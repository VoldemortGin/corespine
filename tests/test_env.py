"""config.env 合约:PREFIX_* -> frozen dataclass,类型转换 + 默认值 + 必填校验。"""

from dataclasses import dataclass

import pytest

from corespine.config.env import env_key, load_from_env


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
