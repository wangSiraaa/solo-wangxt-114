"""测量单位登记与显式换算。

设计原则
--------
1. 每条测量记录必须显式携带单位；缺失单位即错误，不做任何默认假设。
2. 换算只发生在"已登记单位 → 基准单位"这一条显式路径上，
   系统绝不根据数值大小"猜测"单位并静默改写原始记录。
3. 数值与声明单位在量级上矛盾（如把 412 mm 标成 412 cm）时抛出
   :class:`UnitError`，退回数据录入方核实，原始记录保持原样。
"""
from __future__ import annotations

# 基准（规范）单位：胸径 cm，树高 m
DBH_CANONICAL_UNIT = "cm"
HEIGHT_CANONICAL_UNIT = "m"

# 已登记单位 → 基准单位的换算因子
_DBH_FACTOR_TO_CM = {"cm": 1.0, "mm": 0.1}
_HEIGHT_FACTOR_TO_M = {"m": 1.0, "cm": 0.01}

# 换算到基准单位后的合理性区间，超出即拒绝
DBH_PLAUSIBLE_CM = (0.1, 300.0)
HEIGHT_PLAUSIBLE_M = (0.05, 80.0)

# 声明单位下的量级守卫：落在区间外即"疑似单位标错"。
# 例如胸径以 cm 声明却 >200，几乎可定是 mm 误标；以 mm 声明却 <30，几乎可定是 cm 误标。
_DBH_GUARD_BY_UNIT = {"cm": (0.5, 200.0), "mm": (30.0, 3000.0)}
_HEIGHT_GUARD_BY_UNIT = {"m": (0.2, 60.0), "cm": (20.0, 6000.0)}


class UnitError(ValueError):
    """单位缺失、未登记，或数值与声明单位的量级矛盾。"""


def _normalize_unit(unit: object, *, quantity: str, prefix: str) -> str:
    if unit is None or (isinstance(unit, str) and not unit.strip()):
        raise UnitError(f"{prefix}{quantity}单位缺失：每条测量记录必须显式记录单位")
    return str(unit).strip().lower()


def to_canonical_dbh_cm(value: float, unit: str, *, context: str = "") -> float:
    """胸径显式换算到 cm。

    单位未登记、缺失，或数值与声明单位量级矛盾时抛 :class:`UnitError`。
    """
    prefix = f"{context}: " if context else ""
    if value is None:
        raise UnitError(f"{prefix}胸径数值缺失（缺测应显式标记，而不是留空）")
    u = _normalize_unit(unit, quantity="胸径", prefix=prefix)
    if u not in _DBH_FACTOR_TO_CM:
        raise UnitError(
            f"{prefix}胸径单位 {unit!r} 未登记（允许: {sorted(_DBH_FACTOR_TO_CM)}），"
            f"请先在单位登记表中登记并给出换算关系"
        )
    lo, hi = _DBH_GUARD_BY_UNIT[u]
    v = float(value)
    if not lo <= v <= hi:
        raise UnitError(
            f"{prefix}胸径 {v} {u} 超出该声明单位的合理区间 {lo}~{hi} {u}，"
            f"疑似单位标错，请核对原始记录后修正；系统不做静默换算"
        )
    cm = v * _DBH_FACTOR_TO_CM[u]
    if not DBH_PLAUSIBLE_CM[0] <= cm <= DBH_PLAUSIBLE_CM[1]:
        raise UnitError(
            f"{prefix}胸径换算后 {cm:.2f} cm 超出合理性区间 "
            f"{DBH_PLAUSIBLE_CM[0]}~{DBH_PLAUSIBLE_CM[1]} cm"
        )
    return cm


def to_canonical_height_m(value: float, unit: str, *, context: str = "") -> float:
    """树高显式换算到 m。规则同胸径。"""
    prefix = f"{context}: " if context else ""
    if value is None:
        raise UnitError(f"{prefix}树高数值缺失（缺测应显式标记，而不是留空）")
    u = _normalize_unit(unit, quantity="树高", prefix=prefix)
    if u not in _HEIGHT_FACTOR_TO_M:
        raise UnitError(
            f"{prefix}树高单位 {unit!r} 未登记（允许: {sorted(_HEIGHT_FACTOR_TO_M)}）"
        )
    lo, hi = _HEIGHT_GUARD_BY_UNIT[u]
    v = float(value)
    if not lo <= v <= hi:
        raise UnitError(
            f"{prefix}树高 {v} {u} 超出该声明单位的合理区间 {lo}~{hi} {u}，"
            f"疑似单位标错，请核对原始记录后修正；系统不做静默换算"
        )
    m = v * _HEIGHT_FACTOR_TO_M[u]
    if not HEIGHT_PLAUSIBLE_M[0] <= m <= HEIGHT_PLAUSIBLE_M[1]:
        raise UnitError(
            f"{prefix}树高换算后 {m:.2f} m 超出合理性区间 "
            f"{HEIGHT_PLAUSIBLE_M[0]}~{HEIGHT_PLAUSIBLE_M[1]} m"
        )
    return m
