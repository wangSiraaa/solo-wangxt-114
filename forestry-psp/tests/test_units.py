"""单位显式记录与错误核对的验收测试。"""
import pytest

from core.units import UnitError, to_canonical_dbh_cm, to_canonical_height_m


class TestDbhUnits:
    def test_mm_converted_to_cm(self):
        """mm 显式声明 → 换算为 cm（换算行为被记录，见 dataset.conversions）。"""
        assert to_canonical_dbh_cm(143.0, "mm") == pytest.approx(14.3)

    def test_cm_passthrough(self):
        assert to_canonical_dbh_cm(20.0, "cm") == pytest.approx(20.0)

    def test_missing_unit_rejected(self):
        with pytest.raises(UnitError, match="单位缺失"):
            to_canonical_dbh_cm(20.0, "")

    def test_unknown_unit_rejected(self):
        with pytest.raises(UnitError, match="未登记"):
            to_canonical_dbh_cm(20.0, "inch")

    def test_mislabeled_mm_as_cm_rejected(self):
        """412 'cm' 几乎可定是 mm 误标：拒绝而不是静默换算。"""
        with pytest.raises(UnitError, match="疑似单位标错"):
            to_canonical_dbh_cm(412.0, "cm")

    def test_mislabeled_cm_as_mm_rejected(self):
        with pytest.raises(UnitError, match="疑似单位标错"):
            to_canonical_dbh_cm(4.0, "mm")

    def test_missing_value_rejected(self):
        with pytest.raises(UnitError, match="数值缺失"):
            to_canonical_dbh_cm(None, "cm")


class TestHeightUnits:
    def test_m_passthrough(self):
        assert to_canonical_height_m(18.5, "m") == pytest.approx(18.5)

    def test_mislabeled_height_rejected(self):
        with pytest.raises(UnitError, match="疑似单位标错"):
            to_canonical_height_m(1830.0, "m")


class TestFictionalDatasetUnits:
    def test_mm_record_converted(self, fictional):
        """P02/T001 初测胸径 143 mm → 14.3 cm，且换算行为有记录。"""
        conv = [c for c in fictional.conversions if "P02/T001" in c["context"]]
        assert len(conv) == 1
        assert conv[0]["from_unit"] == "mm" and conv[0]["to_unit"] == "cm"
        assert conv[0]["value_out"] == pytest.approx(14.3)

    def test_unit_error_record_rejected(self, fictional):
        """P02/T006 初测 412 'cm' 被拒收，错误原因明示，原始值保留。"""
        rejected = [r for r in fictional.rejected if r["observation"]["tree_no"] == "T006"]
        assert len(rejected) == 1
        assert "疑似单位标错" in rejected[0]["error"]
        assert rejected[0]["observation"]["dbh_value"] == 412.0  # 原始值原样保留
        # 被拒记录不进入任何调查期的可用记录
        assert all(
            r.tree_no != "T006" for r in fictional.records["S2020"]["P02"]
        )
