from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from core.equations import EquationError
from core.estimation import DesignError
from core.matching import DataError
from core.units import UnitError

from . import services
from .models import EquationSet, Plot, SurveyVersion, VerificationTicket
from .serializers import (
    EquationSetSerializer,
    PlotSerializer,
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


class VerificationTicketViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VerificationTicketSerializer

    def get_queryset(self):
        return VerificationTicket.objects.select_related("plot").order_by("-created_at")

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = "resolved"
        ticket.save(update_fields=["status"])
        return Response(self.get_serializer(ticket).data)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})
