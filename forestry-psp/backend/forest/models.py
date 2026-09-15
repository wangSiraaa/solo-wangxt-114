"""数据模型。

- 生产环境（FORESTRY_GIS_BACKEND=postgis）用 PostgreSQL/PostGIS 的
  PolygonField 保存样地边界；开发演示环境退化为 GeoJSON JSONField，
  对外接口形状一致。
- 胸径/树高单位逐条显式记录（``dbh_unit`` / ``height_unit``），
  换算与校验由 core.units 完成，数据库中保留原始录入值。
- EquationSet / SurveyVersion 确认后冻结：任何受控字段的修改都会在
  模型层被拒绝，保证"已确认调查版不可被新方程静默改变"。
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone

_GIS = getattr(settings, "FORESTRY_GIS_BACKEND", "postgis") == "postgis"
if _GIS:
    from django.contrib.gis.db import models as gis_models


def _boundary_field():
    if _GIS:
        return gis_models.PolygonField(srid=32650, help_text="样地边界多边形 (EPSG:32650)")
    return models.JSONField(help_text="样地边界 GeoJSON Polygon，EPSG:32650 坐标（开发后端）")


class ConfirmedImmutableModel(models.Model):
    """确认后冻结的模型基类。

    confirmed=True 之后，tracked_fields 中任何字段的变更都会在 save()
    时抛出 ValidationError；生产库另由 SQL 触发器兜底（见 forest/sql/）。
    """

    confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    tracked_fields: tuple = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            old = type(self).objects.filter(pk=self.pk).first()
            if old is not None and old.confirmed:
                changed = [
                    f for f in self.tracked_fields
                    if getattr(old, f) != getattr(self, f)
                ]
                if changed:
                    raise ValidationError(
                        f"{type(self).__name__}「{old}」已确认，禁止修改字段 {changed}。"
                        f"已确认调查版不可被静默改变，请建立新版本"
                    )
        super().save(*args, **kwargs)


class Stratum(models.Model):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=64)
    area_ha = models.FloatField(help_text="层总面积 (ha)，由抽样设计给定")

    def __str__(self):
        return f"{self.code} {self.name}"


class Species(models.Model):
    code = models.CharField(max_length=8, unique=True)
    name_cn = models.CharField(max_length=32)
    name_latin = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.code} {self.name_cn}"


class Plot(models.Model):
    plot_id = models.CharField(max_length=16, unique=True)
    stratum = models.ForeignKey(Stratum, on_delete=models.PROTECT, related_name="plots")
    boundary = _boundary_field()
    area_ha = models.FloatField(help_text="样地面积 (ha)，样地间可不等，估计时逐块换算")
    inclusion_probability = models.FloatField(help_text="入样概率 π，由抽样设计给定")

    @property
    def weight(self) -> float:
        """Horvitz–Thompson 权重 1/π。"""
        return 1.0 / self.inclusion_probability

    def __str__(self):
        return self.plot_id


class AllometricEquation(models.Model):
    """异速生长方程：形式、参数、单位、适用树种全部显式记录。"""

    RESPONSES = [("biomass_kg", "地上生物量 (kg)"), ("volume_m3", "蓄积 (m³)")]
    FORMS = [("power_dbh", "y = a·D^b"), ("power_dbh_height", "y = a·D^b·H^c")]

    equation_id = models.CharField(max_length=32, unique=True)
    response = models.CharField(max_length=16, choices=RESPONSES)
    form = models.CharField(max_length=32, choices=FORMS)
    params = models.JSONField(help_text="方程参数，如 {\"a\":…, \"b\":…}")
    dbh_unit = models.CharField(max_length=8, default="cm", help_text="方程要求的胸径单位")
    height_unit = models.CharField(max_length=8, default="m", help_text="方程要求的树高单位")
    species = models.ManyToManyField(Species, related_name="equations", help_text="适用树种（显式）")
    source = models.CharField(max_length=200, help_text="方程来源/文献")
    status = models.CharField(
        max_length=16,
        choices=[("draft", "草稿"), ("confirmed", "已确认")],
        default="draft",
    )
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by",
        help_text="本方程替代的上一版（旧版保留，不可改）",
    )

    #: 被已确认方程集引用后禁止变更的字段
    tracked_fields = ("equation_id", "response", "form", "params", "dbh_unit", "height_unit", "source")

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            old = AllometricEquation.objects.filter(pk=self.pk).first()
            if old is not None and old.equation_sets.filter(confirmed=True).exists():
                changed = [
                    f for f in self.tracked_fields
                    if getattr(old, f) != getattr(self, f)
                ]
                if changed:
                    raise ValidationError(
                        f"方程 {old.equation_id} 已被已确认方程集引用，禁止修改 {changed}；"
                        f"请新建方程版本（supersedes 指向旧版），旧版保持可用"
                    )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.equation_id


class EquationSet(ConfirmedImmutableModel):
    """一组方程的集合；确认时冻结内容哈希。"""

    set_id = models.CharField(max_length=32, unique=True)
    description = models.CharField(max_length=200, blank=True)
    equations = models.ManyToManyField(AllometricEquation, related_name="equation_sets")
    frozen_hash = models.CharField(max_length=64, blank=True, help_text="确认时的内容哈希")

    tracked_fields = ("set_id", "description", "frozen_hash", "confirmed")

    def build_core_set(self):
        """由 ORM 行构造 core 计算引擎的 EquationSet（并冻结）。"""
        from core.equations import AllometricEquation as CoreEq
        from core.equations import EquationSet as CoreSet

        eqs = [
            CoreEq(
                equation_id=e.equation_id,
                response=e.response,
                form=e.form,
                params=e.params,
                species=tuple(sorted(s.code for s in e.species.all())),
                dbh_unit=e.dbh_unit,
                height_unit=e.height_unit,
                source=e.source,
                status=e.status,
            )
            for e in self.equations.prefetch_related("species")
        ]
        core_set = CoreSet(self.set_id, eqs, description=self.description)
        core_set.freeze()
        return core_set

    def compute_hash(self) -> str:
        return self.build_core_set().content_hash()

    def confirm(self):
        if self.confirmed:
            raise ValidationError(f"方程集 {self.set_id} 已确认，不可重复确认")
        self.frozen_hash = self.compute_hash()
        self.confirmed = True
        self.confirmed_at = timezone.now()
        self.save()

    def __str__(self):
        return self.set_id


@receiver(m2m_changed, sender=EquationSet.equations.through)
def _guard_confirmed_equation_set_members(sender, instance, action, **kwargs):
    if action in ("pre_add", "pre_remove", "pre_clear") and instance.pk and instance.confirmed:
        raise ValidationError(f"方程集 {instance.set_id} 已确认，其成员不可变更")


@receiver(m2m_changed, sender=AllometricEquation.species.through)
def _guard_confirmed_equation_species(sender, instance, action, **kwargs):
    if action in ("pre_add", "pre_remove", "pre_clear") and instance.pk:
        if instance.equation_sets.filter(confirmed=True).exists():
            raise ValidationError(
                f"方程 {instance.equation_id} 已被已确认方程集引用，适用树种不可变更"
            )


class Survey(models.Model):
    survey_id = models.CharField(max_length=16, unique=True)
    year = models.PositiveIntegerField()

    def __str__(self):
        return self.survey_id


class SurveyVersion(ConfirmedImmutableModel):
    """调查版：两期调查 + 方程集 + 匹配参数的固定组合。

    确认时对方程集内容哈希做快照；之后方程集内容若被改动，
    verify_equation_set_intact() 会拒绝继续出数。

    修订版通过 base_version 指向来源（已确认）版，形成可追溯链；
    修订版本身为草稿，确认后才可作为新批次的基线。
    """

    name = models.CharField(max_length=64)
    survey_t1 = models.ForeignKey(Survey, on_delete=models.PROTECT, related_name="+")
    survey_t2 = models.ForeignKey(Survey, on_delete=models.PROTECT, related_name="+")
    equation_set = models.ForeignKey(EquationSet, on_delete=models.PROTECT)
    dbh_threshold_cm = models.FloatField(default=5.0, help_text="起测胸径 (cm)")
    position_tolerance_m = models.FloatField(default=1.0, help_text="复测位置容差 (m)")
    equation_set_hash = models.CharField(
        max_length=64, blank=True, help_text="确认时方程集内容哈希快照"
    )
    base_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="revisions", help_text="修订来源版（基线）；空表示原始调查版",
    )

    tracked_fields = (
        "name", "survey_t1_id", "survey_t2_id", "equation_set_id",
        "dbh_threshold_cm", "position_tolerance_m", "confirmed", "equation_set_hash",
        "base_version_id",
    )

    @property
    def interval_years(self) -> float:
        return float(self.survey_t2.year - self.survey_t1.year)

    def confirm(self):
        if self.confirmed:
            raise ValidationError(f"调查版 {self.name} 已确认，不可重复确认")
        if not self.equation_set.confirmed:
            raise ValidationError("请先确认方程集，再确认调查版")
        self.equation_set_hash = self.equation_set.frozen_hash
        self.confirmed = True
        self.confirmed_at = timezone.now()
        self.save()

    def verify_equation_set_intact(self):
        """确认后每次出数前调用：方程集内容必须与确认时一致。"""
        if not self.confirmed:
            return
        current = self.equation_set.compute_hash()
        if current != self.equation_set_hash:
            raise ValidationError(
                f"调查版 {self.name} 的方程集内容与确认时不一致"
                f"（确认时 {self.equation_set_hash[:12]}…，当前 {current[:12]}…）："
                f"已确认调查版不可被新方程静默改变"
            )

    def __str__(self):
        return self.name


class TreeObservation(models.Model):
    """单株单次调查记录；原始录入值与单位原样保存。"""

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="observations")
    plot = models.ForeignKey(Plot, on_delete=models.CASCADE, related_name="observations")
    tree_no = models.CharField(max_length=16, help_text="样地内树木编号")
    species = models.ForeignKey(Species, on_delete=models.PROTECT)
    dbh_value = models.FloatField(null=True, blank=True)
    dbh_unit = models.CharField(max_length=8, blank=True, help_text="显式记录：cm 或 mm")
    height_value = models.FloatField(null=True, blank=True)
    height_unit = models.CharField(max_length=8, blank=True, help_text="显式记录：m 或 cm")
    x_m = models.FloatField(help_text="样地内相对坐标 x (m)")
    y_m = models.FloatField(help_text="样地内相对坐标 y (m)")
    vital_status = models.CharField(
        max_length=8, choices=[("alive", "存活"), ("dead", "死亡")], default="alive"
    )
    qc_status = models.CharField(
        max_length=8, choices=[("accepted", "通过"), ("rejected", "拒收")], default="accepted"
    )
    qc_note = models.TextField(blank=True, help_text="单位校验/质检说明")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["survey", "plot", "tree_no"], name="uniq_observation_per_survey"
            )
        ]

    def __str__(self):
        return f"{self.survey.survey_id}/{self.plot.plot_id}/{self.tree_no}"


class VerificationTicket(models.Model):
    """待人工核实事项（位置矛盾、疑似漏测等），核实前相关记录不参与估计。"""

    CATEGORIES = [
        ("conflict", "编号相同但位置矛盾"),
        ("possible_missed", "疑似初测漏测"),
        ("dead_without_t1", "死亡记录无初测对应"),
    ]

    plot = models.ForeignKey(Plot, on_delete=models.CASCADE, related_name="tickets")
    category = models.CharField(max_length=24, choices=CATEGORIES)
    tree_no_t1 = models.CharField(max_length=16, null=True, blank=True)
    tree_no_t2 = models.CharField(max_length=16, null=True, blank=True)
    detail = models.TextField()
    status = models.CharField(
        max_length=8, choices=[("open", "待核实"), ("resolved", "已核实")], default="open"
    )
    resolved_by_batch = models.ForeignKey(
        "RevisionBatch", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="resolved_tickets", help_text="核实本工单的修订批次",
    )
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.plot.plot_id} {self.category} {self.tree_no_t1 or ''}/{self.tree_no_t2 or ''}"


class RevisionBatch(models.Model):
    """核实结论修订批次。

    状态机：draft（草稿）→ applying（应用中）→ applied（已应用）
                                         ↘ failed（失败可重试）↺
    应用过程单事务执行：任何结论不合法都会整体回滚，
    不会留下半套观测或半套估计。
    """

    STATUSES = [
        ("draft", "草稿"),
        ("applying", "应用中"),
        ("applied", "已应用"),
        ("failed", "失败可重试"),
    ]

    request_id = models.CharField(
        max_length=64, unique=True, help_text="客户端请求标识（幂等键）：重复提交返回同一批次"
    )
    payload_fingerprint = models.CharField(
        max_length=64, help_text="请求内容指纹：同一 request_id 提交不同内容时拒绝"
    )
    base_version = models.ForeignKey(
        SurveyVersion, on_delete=models.PROTECT, related_name="revision_batches",
        help_text="基线调查版（必须为已确认版）",
    )
    status = models.CharField(max_length=16, choices=STATUSES, default="draft")
    created_by = models.CharField(max_length=64, help_text="批次创建人")
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, help_text="应用失败原因（供修正后重试）")
    result_version = models.OneToOneField(
        SurveyVersion, null=True, blank=True, on_delete=models.PROTECT,
        related_name="produced_by_batch", help_text="应用成功生成的可追溯新草稿版",
    )
    result_estimates = models.JSONField(
        null=True, blank=True, help_text="应用时的估计结果快照（审计用，原估计永不覆盖）"
    )

    def __str__(self):
        return f"批次#{self.pk} {self.request_id[:8]} [{self.get_status_display()}]"


class RevisionConclusion(models.Model):
    """一条核实结论（修订批次的组成单元）。

    记录操作者、时间、前后值、来源版本与请求标识，全程可审计。
    """

    TYPES = [
        ("confirm_renumbered", "同株改号"),
        ("confirm_missing", "确认漏测"),
        ("confirm_dead", "确认死亡"),
        ("keep_excluded", "保留排除"),
        ("correct_observation", "更正观测记录"),
    ]

    batch = models.ForeignKey(
        RevisionBatch, on_delete=models.CASCADE, related_name="conclusions"
    )
    ticket = models.ForeignKey(
        VerificationTicket, null=True, blank=True, on_delete=models.PROTECT,
        related_name="conclusions", help_text="关联工单（更正记录类可无工单）",
    )
    conclusion_type = models.CharField(max_length=24, choices=TYPES)
    operator = models.CharField(max_length=64, help_text="结论操作者")
    request_id = models.CharField(max_length=64, help_text="本条结论的请求标识")
    before_value = models.JSONField(default=dict, help_text="结论前状态快照")
    after_value = models.JSONField(default=dict, help_text="结论内容/更正后值")
    source_version = models.ForeignKey(
        SurveyVersion, on_delete=models.PROTECT, related_name="+",
        help_text="结论依据的来源调查版",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_conclusion_type_display()} 工单#{self.ticket_id or '—'}"

