from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import mixins, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from core.equations import EquationError
from core.estimation import DesignError
from core.matching import DataError
from core.units import UnitError

from . import services
from .models import (
    EquationSet,
    Plot,
    RevisionBatch,
    RevisionConclusion,
    SurveyVersion,
    VerificationTicket,
)
from .serializers import (
    EquationSetSerializer,
    PlotSerializer,
    RevisionBatchSerializer,
    RevisionConclusionSerializer,
    SurveyVersionSerializer,
    VerificationTicketSerializer,
)

_DOMAIN_ERRORS = (UnitError, DesignError, DataError, EquationError, DjangoValidationError)


def _resolve_version(request) -> SurveyVersion:
    version_id = request.query_params.get("version_id")
    if version_id:
        return get_object_or_404(SurveyVersion, pk=version_id)
    version = services.default_version()
    if version is None:
        raise DjangoValidationError("尚未建立任何调查版，请先装载数据")
    return version


class PlotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PlotSerializer
    lookup_field = "plot_id"

    def get_queryset(self):
        return Plot.objects.select_related("stratum").order_by("plot_id")

    @action(detail=True, methods=["get"])
    def remeasurements(self, request, plot_id=None):
        """单块样地的两期复测匹配明细（含改号/矛盾/缺测等标记）。"""
        try:
            version = _resolve_version(request)
            payload = services.remeasurements_payload(version, plot_id)
        except _DOMAIN_ERRORS as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(payload)


class EstimateView(APIView):
    """生长/死亡/进界总体估计（设计加权），含来源与不确定性假设。"""

    def get(self, request):
        try:
            version = _resolve_version(request)
            payload = services.compute_estimates_payload(version)
        except _DOMAIN_ERRORS as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(payload)


class EquationSetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EquationSetSerializer

    def get_queryset(self):
        return EquationSet.objects.prefetch_related("equations").order_by("set_id")

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        equation_set = self.get_object()
        try:
            equation_set.confirm()
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=409)
        return Response(self.get_serializer(equation_set).data)


class SurveyVersionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SurveyVersionSerializer

    def get_queryset(self):
        return SurveyVersion.objects.select_related(
            "survey_t1", "survey_t2", "equation_set"
        ).order_by("-pk")

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        version = self.get_object()
        try:
            version.confirm()
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=409)
        return Response(self.get_serializer(version).data)

    @action(detail=False, methods=["get"])
    def compare(self, request):
        """按版本比较估计差异：?base=<id>&revision=<id>。"""
        base = get_object_or_404(SurveyVersion, pk=request.query_params.get("base"))
        revision = get_object_or_404(
            SurveyVersion, pk=request.query_params.get("revision")
        )
        try:
            payload = services.compare_versions(base, revision)
        except _DOMAIN_ERRORS as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(payload)


class VerificationTicketViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VerificationTicketSerializer

    def get_queryset(self):
        return (
            VerificationTicket.objects.select_related("plot")
            .prefetch_related("conclusions__batch")
            .order_by("-created_at")
        )

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = "resolved"
        ticket.save(update_fields=["status"])
        return Response(self.get_serializer(ticket).data)


class RevisionBatchViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """核实结论修订批次：创建（幂等）、查看、应用、失败重试。"""

    serializer_class = RevisionBatchSerializer

    def get_queryset(self):
        return (
            RevisionBatch.objects.select_related("base_version", "result_version")
            .prefetch_related("conclusions__ticket")
            .order_by("-created_at")
        )

    def create(self, request):
        try:
            batch, created = services.create_batch(request.data)
        except services.ConclusionConflict as exc:
            return Response({"detail": str(exc)}, status=409)
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages if hasattr(exc, "messages") else str(exc)},
                status=400,
            )
        return Response(
            self.get_serializer(batch).data, status=201 if created else 200
        )

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        try:
            batch = services.apply_batch(int(pk))
        except services.BatchApplyError as exc:
            return Response({"detail": str(exc), "status": "failed"}, status=422)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        batch = self.get_object()
        if batch.status != "failed":
            return Response(
                {"detail": f"仅失败状态的批次可重试（当前 {batch.status}）"}, status=409
            )
        try:
            batch = services.apply_batch(int(pk))
        except services.BatchApplyError as exc:
            return Response({"detail": str(exc), "status": "failed"}, status=422)
        return Response(self.get_serializer(batch).data)


class RevisionConclusionViewSet(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    """单条结论：查看与修正（仅草稿/失败批次可修正 after_value）。"""

    serializer_class = RevisionConclusionSerializer
    queryset = RevisionConclusion.objects.select_related("batch").all()
    http_method_names = ["get", "patch", "head", "options"]

    def partial_update(self, request, *args, **kwargs):
        conclusion = self.get_object()
        if conclusion.batch.status not in ("draft", "failed"):
            return Response(
                {"detail": "仅草稿或失败批次的结论可修正"}, status=409
            )
        new_after = request.data.get("after_value")
        if new_after is None:
            return Response({"detail": "仅支持修正 after_value"}, status=400)
        try:
            services.validate_after_value(conclusion.conclusion_type, new_after)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=400)
        conclusion.after_value = new_after
        conclusion.save(update_fields=["after_value"])
        return Response(self.get_serializer(conclusion).data)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})
