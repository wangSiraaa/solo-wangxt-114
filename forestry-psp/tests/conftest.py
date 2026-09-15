import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.dataset import parse_dataset_json, validate_observations  # noqa: E402
from core.pipeline import run_analysis  # noqa: E402

DATA_PATH = REPO_ROOT / "data" / "fictional_survey.json"


@pytest.fixture(scope="session")
def fictional():
    """解析并完成单位校验的虚构数据集。"""
    dataset = parse_dataset_json(DATA_PATH)
    return validate_observations(dataset)


@pytest.fixture(scope="session")
def analysis(fictional):
    """对虚构数据集跑完整分析流水线（EQSET-2020，S2020→S2025）。"""
    equation_set = fictional.equation_set("EQSET-2020")
    return run_analysis(
        records_t1=fictional.records["S2020"],
        records_t2=fictional.records["S2025"],
        design=fictional.design(),
        stratum_areas_ha=fictional.stratum_areas(),
        equation_set=equation_set,
        interval_years=5.0,
        dbh_threshold_cm=5.0,
        position_tolerance_m=1.0,
    )
