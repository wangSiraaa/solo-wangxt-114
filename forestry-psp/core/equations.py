"""异速生长方程与方程集版本管理。

- 每个方程显式记录：响应量、函数形式、参数、胸径/树高单位、适用树种、来源。
- 方程集 ``freeze()`` 后得到内容哈希；已确认的调查版保存该哈希快照，
  之后任何对方程的改动都会导致哈希不一致而被拒绝 ——
  已确认调查版不会被新方程静默改变。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


class EquationError(ValueError):
    """方程定义不完整、树种不适用或方程集被篡改。"""


FORM_POWER_DBH = "power_dbh"                  # y = a * D^b
FORM_POWER_DBH_HEIGHT = "power_dbh_height"    # y = a * D^b * H^c
FORMS = (FORM_POWER_DBH, FORM_POWER_DBH_HEIGHT)

RESPONSE_BIOMASS_KG = "biomass_kg"
RESPONSE_VOLUME_M3 = "volume_m3"

STATUS_DRAFT = "draft"
STATUS_CONFIRMED = "confirmed"


@dataclass(frozen=True)
class AllometricEquation:
    """一条异速生长方程（不可变对象）。

    species 必须显式给出适用树种；对未列出的树种求值会抛错，
    不允许"顺便外推"到未验证树种。
    """

    equation_id: str
    response: str
    form: str
    params: Mapping[str, float]
    species: Tuple[str, ...]
    dbh_unit: str = "cm"
    height_unit: str = "m"
    source: str = ""
    status: str = STATUS_DRAFT

    def __post_init__(self):
        if not self.species:
            raise EquationError(f"方程 {self.equation_id} 未记录适用树种")
        need = {"a", "b"} if self.form == FORM_POWER_DBH else (
            {"a", "b", "c"} if self.form == FORM_POWER_DBH_HEIGHT else None
        )
        if need is None:
            raise EquationError(f"方程 {self.equation_id} 的函数形式 {self.form!r} 未登记")
        missing = need - set(self.params)
        if missing:
            raise EquationError(f"方程 {self.equation_id} 缺少参数 {sorted(missing)}")

    def evaluate(self, *, species: str, dbh_cm: float, height_m: Optional[float] = None) -> float:
        """按方程求值。树种不适用、单位不符或缺树高时显式报错。"""
        if species not in self.species:
            raise EquationError(
                f"方程 {self.equation_id} 不适用于树种 {species!r}"
                f"（适用: {list(self.species)}）；树种适用性必须显式记录，不做外推"
            )
        if self.dbh_unit != "cm":
            raise EquationError(
                f"方程 {self.equation_id} 要求胸径单位 {self.dbh_unit!r}，"
                f"请先用 core.units 显式换算后再求值"
            )
        p = self.params
        if self.form == FORM_POWER_DBH:
            return p["a"] * dbh_cm ** p["b"]
        if height_m is None:
            raise EquationError(f"方程 {self.equation_id} 需要树高，但该记录树高缺测")
        if self.height_unit != "m":
            raise EquationError(
                f"方程 {self.equation_id} 要求树高单位 {self.height_unit!r}，请先显式换算"
            )
        return p["a"] * dbh_cm ** p["b"] * height_m ** p["c"]

    def canonical_dict(self) -> dict:
        """用于内容哈希的规范化表示（与来源、状态无关的部分也纳入，保证可审计）。"""
        return {
            "equation_id": self.equation_id,
            "response": self.response,
            "form": self.form,
            "params": {k: float(v) for k, v in sorted(self.params.items())},
            "species": sorted(self.species),
            "dbh_unit": self.dbh_unit,
            "height_unit": self.height_unit,
            "source": self.source,
        }


class EquationSet:
    """一组方程的集合；冻结后可校验完整性。"""

    def __init__(self, set_id: str, equations, description: str = ""):
        self.set_id = set_id
        self.description = description
        self._equations = tuple(equations)
        ids = [e.equation_id for e in self._equations]
        if len(ids) != len(set(ids)):
            raise EquationError(f"方程集 {set_id} 内方程编号重复")
        self._frozen_hash: Optional[str] = None

    @property
    def equations(self):
        return self._equations

    def _canonical_json(self) -> str:
        payload = {
            "set_id": self.set_id,
            "equations": [
                e.canonical_dict()
                for e in sorted(self._equations, key=lambda x: x.equation_id)
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def content_hash(self) -> str:
        return hashlib.sha256(self._canonical_json().encode("utf-8")).hexdigest()

    def freeze(self) -> str:
        """冻结当前内容并返回哈希。之后应通过 assert_unmodified 校验。"""
        self._frozen_hash = self.content_hash()
        return self._frozen_hash

    @property
    def is_frozen(self) -> bool:
        return self._frozen_hash is not None

    @property
    def frozen_hash(self) -> Optional[str]:
        return self._frozen_hash

    def assert_unmodified(self) -> None:
        if not self.is_frozen:
            raise EquationError(f"方程集 {self.set_id} 尚未冻结，无法校验完整性")
        current = self.content_hash()
        if current != self._frozen_hash:
            raise EquationError(
                f"方程集 {self.set_id} 在冻结后被修改"
                f"（确认时哈希 {self._frozen_hash[:12]}…，当前 {current[:12]}…）。"
                f"已确认调查版不允许被新方程静默改变；请建立新版方程集并重新确认"
            )

    def equation_for(self, response: str, species: str) -> AllometricEquation:
        for e in self._equations:
            if e.response == response and species in e.species:
                return e
        raise EquationError(
            f"方程集 {self.set_id} 中没有响应量 {response} 适用于树种 {species} 的方程"
        )
