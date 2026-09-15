"""方程集版本与'已确认调查版不可被静默改变'的验收测试。"""
import dataclasses

import pytest

from core.equations import (
    AllometricEquation,
    EquationError,
    EquationSet,
)


def _eq(equation_id="BIO-QUMO-1", a=0.152, b=2.31, species=("QUMO",)):
    return AllometricEquation(
        equation_id=equation_id, response="biomass_kg", form="power_dbh",
        params={"a": a, "b": b}, species=species, source="虚构",
    )


class TestEquationSetIntegrity:
    def test_frozen_set_detects_tampering(self):
        """冻结后替换方程（如换用新参数）→ 完整性校验拒绝。"""
        es = EquationSet("EQSET-2020", [_eq()])
        es.freeze()
        # 模拟"新方程静默替换旧方程"
        tampered = EquationSet("EQSET-2020", [_eq(a=0.149, b=2.33)])
        tampered._frozen_hash = es.frozen_hash  # 攻击者沿用了旧哈希标记
        with pytest.raises(EquationError, match="冻结后被修改"):
            tampered.assert_unmodified()

    def test_unfrozen_set_cannot_be_verified(self):
        es = EquationSet("S", [_eq()])
        with pytest.raises(EquationError, match="尚未冻结"):
            es.assert_unmodified()

    def test_hash_stable_across_construction_order(self):
        a = EquationSet("S", [_eq("E1"), _eq("E2", species=("BEPL",))])
        b = EquationSet("S", [_eq("E2", species=("BEPL",)), _eq("E1")])
        assert a.freeze() == b.freeze()


class TestSpeciesApplicability:
    def test_unlisted_species_rejected(self):
        eq = _eq()
        with pytest.raises(EquationError, match="不适用于树种"):
            eq.evaluate(species="PIKO", dbh_cm=20.0)

    def test_equation_set_lookup_by_species(self):
        es = EquationSet("S", [_eq("E1", species=("QUMO",)), _eq("E2", species=("PIKO",))])
        es.freeze()
        assert es.equation_for("biomass_kg", "PIKO").equation_id == "E2"
        with pytest.raises(EquationError, match="没有"):
            es.equation_for("biomass_kg", "BEPL")

    def test_equation_requires_species_list(self):
        with pytest.raises(EquationError, match="适用树种"):
            AllometricEquation(
                equation_id="X", response="biomass_kg", form="power_dbh",
                params={"a": 1, "b": 2}, species=(),
            )


class TestFictionalEquationSet:
    def test_draft_equation_not_in_confirmed_set(self, fictional):
        """2025 修订草稿 BIO-QUMO-2 不在已确认方程集内，不影响确认版。"""
        es = fictional.equation_set("EQSET-2020")
        ids = {e.equation_id for e in es.equations}
        assert "BIO-QUMO-2" not in ids
        assert es.is_frozen

    def test_confirmed_set_hash_reproducible(self, fictional):
        """同一数据两次构造的方程集哈希一致（可复现）。"""
        assert fictional.equation_set("EQSET-2020").freeze() == \
            fictional.equation_set("EQSET-2020").freeze()
