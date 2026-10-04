"""trigger 缝:外部信号源(拉模式 poll -> TriggerEvent,见 source.py 与 docs/adr/0005)。"""

from corespine.trigger.source import (
    DEFAULT_MAX_CATCHUP,
    TRIGGER_MANUAL,
    TRIGGER_REGISTRY,
    TRIGGER_SCHEDULE,
    ManualTrigger,
    ScheduleTrigger,
    TriggerEvent,
    TriggerSource,
    make_trigger,
)

__all__ = [
    "DEFAULT_MAX_CATCHUP",
    "TriggerSource",
    "TriggerEvent",
    "ManualTrigger",
    "ScheduleTrigger",
    "make_trigger",
    "TRIGGER_REGISTRY",
    "TRIGGER_MANUAL",
    "TRIGGER_SCHEDULE",
]
