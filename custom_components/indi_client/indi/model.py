"""Data model for INDI properties and their elements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATE_IDLE = "Idle"
STATE_OK = "Ok"
STATE_BUSY = "Busy"
STATE_ALERT = "Alert"

PERM_RO = "ro"
PERM_WO = "wo"
PERM_RW = "rw"

RULE_ONE_OF_MANY = "OneOfMany"
RULE_AT_MOST_ONE = "AtMostOne"
RULE_ANY_OF_MANY = "AnyOfMany"


@dataclass
class INDIElement:
    """A single value (oneText/oneNumber/oneSwitch/oneLight/defBLOB, ...)."""

    name: str
    label: str
    value: Any = None
    format: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None


@dataclass
class INDIProperty:
    """A vector property (defTextVector, defNumberVector, ...) of a device."""

    device: str
    name: str
    label: str
    group: str
    ptype: str  # "Text", "Number", "Switch", "Light" or "BLOB"
    state: str
    perm: str  # "ro", "wo" or "rw"
    rule: str | None  # only meaningful for Switch vectors
    timeout: float
    elements: dict[str, INDIElement] = field(default_factory=dict)

    @property
    def writable(self) -> bool:
        """Whether the property accepts new*Vector commands from clients."""
        return self.perm in (PERM_WO, PERM_RW)
