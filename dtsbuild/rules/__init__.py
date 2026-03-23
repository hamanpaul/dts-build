"""Public Reference Rule Library — subsystem DTS generation rules.

Rules are derived ONLY from public BCM68575 BDK reference material
(kernel/dts/68375/968575REF1.dts and associated dtsi includes).
"""

from .base import SubsystemRule, RuleMatch
from .registry import get_all_rules, get_rule, auto_match

__all__ = [
    "SubsystemRule",
    "RuleMatch",
    "get_all_rules",
    "get_rule",
    "auto_match",
]
