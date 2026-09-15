# 固定样地两期复测分析系统（生长 / 死亡 / 进界）

林业研究站用于比较固定样地两次调查的**生长量、死亡量与进界量**的最小可用系统：

- **React** 前端：样地位置图、个体复测明细、总体估计与不确定性展示；
- **Django REST Framework** 后端：API 与数据管理，计算委托给独立的 **NumPy** 计算引擎；
- **PostgreSQL/PostGIS**（生产）保存树木编号、测量高度与样地边界；开发环境可用 SQLite + GeoJSON 运行全部功能；
- 胸径单位、异速生长方程及适用树种**显式记录**；编号相同但位置矛盾的个体**先核实**；
- 总体估计按给定抽样设计 **Horvitz–Thompson 加权**，不做"全部树木简单平均 × 面积"。

> 仓库内 `data/fictional_survey.json` 为**虚构数据**（树种、方程、坐标、测量值均为虚构），
> 用于演示与验收测试，不代表任何真实林分。

## 目录结构

```
forestry-psp/
├── core/                     # 计算引擎（纯 Python + NumPy，与 Web 框架解耦）
│   ├── units.py              #   单位登记与显式换算（UnitError，不静默猜单位）
│   ├── equations.py          #   异速生长方程 + 方程集冻结哈希
│   ├── matching.py           #   复测匹配（改号/位置矛盾/缺测/死亡/进界）
│   ├── components.py         #   样地级生长/死亡/进界分量
│   ├── estimation.py         #   设计加权总体估计（Horvitz–Thompson + 分层方差）
│   ├── provenance.py         #   分量来源与不确定性假设文本
│   ├── pipeline.py           #   分析流水线（API 与测试共用）
│   └── dataset.py            #   数据集 JSON 解析与单位校验
├── backend/                  # Django + DRF
│   ├── config/settings.py    #   生产：PostgreSQL/PostGIS
│   ├── config/settings_dev.py#   开发：SQLite + GeoJSON
│   └── forest/               #   模型、序列化、视图、服务层、装载命令、SQL 触发器
├── frontend/                 # React (Vite)：地图 + 复测表 + 估计面板
├── data/fictional_survey.json# 虚构两期调查数据（含全部验收用例）
├── tests/                    # 核心引擎验收测试（pytest）
└── docker-compose.yml        # PostGIS 一键启动
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt          # 或 pip install numpy django djangorestframework django-cors-headers pytest

# 2. 初始化数据库并装载虚构数据（开发默认 SQLite）
cd backend
python3 manage.py makemigrations forest && python3 manage.py migrate
python3 manage.py load_fictional         # 输出单位校验、拒收记录、待核实事项摘要

# 3. 启动 API（默认 8000 端口）
python3 manage.py runserver

# 4. 前端（另开终端）
cd frontend && npm install && npm run dev   # 默认 5173 端口，/api 代理到 8000

# 5. 全部验收测试
make test        # = pytest tests/（核心引擎）+ manage.py test forest（API 层）
```

生产部署（PostGIS）：

```bash
docker compose up -d db
export FORESTRY_GIS_BACKEND=postgis DJANGO_SETTINGS_MODULE=config.settings \
       PGDATABASE=forestry PGUSER=forestry PGPASSWORD="$POSTGRES_PASSWORD"
python3 backend/manage.py migrate
psql $PGDATABASE -f backend/forest/sql/immutable_confirmed.sql   # 已确认记录防篡改触发器
```

> 迁移文件与 GIS 后端相关：切换 PostGIS 后请在生产环境重新 `makemigrations`。

## 关键业务规则

### 单位显式记录（core/units.py）

- 每条观测显式记录 `dbh_unit`（cm/mm）与 `height_unit`（m/cm）；缺单位即错误。
- 换算只走"已登记单位 → 基准单位"显式路径，逐条留痕（`unit_conversions`）。
- 数值与声明单位量级矛盾（如 412 标成 cm）→ **拒收记录并提示核实**，绝不静默换算；
  原始录入值原样保留（`qc_status=rejected`）。

### 复测匹配（core/matching.py）

| 情形 | 类别 | 处理 |
|---|---|---|
| 编号相同、位置 ≤ 容差、ΔD>0.05cm | `survivor` | 计入保留木生长 |
| 编号相同、位置 ≤ 容差、ΔD≤0.05cm | `survivor_zero` | **真实零生长**，计入生长（增量 0） |
| 编号相同、位置 > 容差 | `conflict` | **不认定同株**，挂起待核实，不参与任何分量 |
| 编号不同、位置吻合、树种一致 | `renumbered` | 复测改号，判定同株并显式标记 |
| 仅初测有记录 | `missing` | **缺测 ≠ 死亡**，不参与任何分量 |
| 复测明确死亡记录 | `dead` | 计入死亡（按初测大小） |
| 仅复测有、胸径 ≥ 起测径且 ≤ 进界上限 | `ingrowth` | 计入进界 |
| 仅复测有、胸径 > 进界上限 | `possible_missed` | 疑似漏测，挂起待核实 |

### 分量定义（core/components.py）

- 保留木生长 = Σ（期末 − 期初）（含零生长与改号个体）；
- 进界 = Σ 新进个体期末值；死亡 = Σ 死亡个体期初值；净变化 = 生长 + 进界 − 死亡。
- 响应量：断面积（m²）、地上生物量（kg）、蓄积（m³，需树高）。
- 异速生长方程逐树种显式登记；对未登记树种求值直接报错，不外推。

### 总体估计（core/estimation.py）

- 逐样地分量 → Horvitz–Thompson：总量 = Σ wᵢyᵢ（w = 1/π，由设计给定）；
- 方差按分层有放回近似；单样地层给出"方差被低估"警告；
- 设计与观测不一一对应时**显式报错**，不静默丢样地；
- 样地面积不等：每公顷换算逐块使用各自面积。

### 版本不可变性

- 方程集确认时冻结内容哈希；调查版确认时保存哈希快照；
- 模型层拒绝修改已确认记录（`ConfirmedImmutableModel`、M2M 信号），
  生产库另有 SQL 触发器兜底（`backend/forest/sql/immutable_confirmed.sql`）；
- 即使绕过模型层直接改库，出数前 `verify_equation_set_intact()` 也会拦截；
- 新方程只能以"草稿 + supersedes"形式存在，**不影响已确认调查版**。

## 不确定性假设（随估计结果输出）

- 缺测与待核实个体未做插补，若其与实测个体系统性不同，偏差方向未知；
- 缺测个体若实际死亡，死亡量被低估；
- 疑似漏测个体核实前不计入进界，进界量可能被低估；
- 胸径/树高测量误差（±0.1 cm / ±0.1 m）与方程参数不确定性未在方差中展开，
  报告的不确定性仅含抽样误差；
- 方差为分层有放回近似，未做有限总体修正。

## API 一览

| 端点 | 说明 |
|---|---|
| `GET /api/plots/` | 样地列表（GeoJSON 边界、面积、入样概率、权重） |
| `GET /api/plots/{id}/remeasurements/` | 单块样地两期复测匹配明细 |
| `GET /api/estimates/` | 总体估计（分量 × 响应量）+ 来源与假设 + 单位换算/拒收记录 |
| `GET /api/verification-tickets/` | 待核实事项；`POST …/{id}/resolve/` 标记已核实 |
| `GET /api/equation-sets/`、`POST …/{id}/confirm/` | 方程集与确认 |
| `GET /api/survey-versions/`、`POST …/{id}/confirm/` | 调查版与确认 |

## 验收清单

| 验收项 | 实现位置 | 测试 |
|---|---|---|
| 复测改号判定同株并标记 | `core/matching.py`（renumbered） | `tests/test_matching.py::TestRenumbering` |
| 编号相同位置矛盾先核实 | `core/matching.py`（conflict）+ `VerificationTicket` | `test_matching.py::test_same_id_conflicting_position_not_matched`、`backend/forest/tests/test_api.py::TestTickets` |
| 样地面积不等 | 逐块面积换算 `PlotComponents.per_hectare` | `tests/test_components.py::TestUnequalPlotAreas` |
| 测量单位错误核对 | `core/units.py`（UnitError）+ 拒收留痕 | `tests/test_units.py`、`test_api.py::TestUnitErrorHandling` |
| 真实零生长 / 缺测 / 死亡区分 | `survivor_zero` / `missing` / `dead` 三类别 | `tests/test_matching.py`、`tests/test_components.py` |
| 设计加权而非简单平均×面积 | `core/estimation.py`（Horvitz–Thompson） | `tests/test_estimation.py::test_not_naive_mean_times_area`、`test_differs_from_naive_tree_average` |
| 总体估计手算对照 | — | `tests/test_estimation.py::TestFictionalPopulationEstimates`（断面积三分量手算值） |
| 已确认调查版不可被新方程静默改变 | 模型层守卫 + 哈希快照 + SQL 触发器 | `tests/test_versioning.py`、`test_api.py::TestImmutability`（含绕库篡改拦截） |
| 每个分量的来源与不确定性假设 | `core/provenance.py` + API `provenance` 字段 | `tests/test_estimation.py::test_provenance_complete` |
| 虚构林木数据 | `data/fictional_survey.json` | 全部测试共用 |
