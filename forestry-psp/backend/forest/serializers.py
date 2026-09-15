import json

from rest_framework import serializers

from .models import (
    AllometricEquation,
    EquationSet,
    Plot,
    Stratum,
    SurveyVersion,
    VerificationTicket,
)


class StratumSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stratum
        fields = ["code", "name", "area_ha"]


class PlotSerializer(serializers.ModelSerializer):
    stratum = StratumSerializer(read_only=True)
    weight = serializers.ReadOnlyField()
    boundary = serializers.SerializerMethodField()

    class Meta:
        model = Plot
        fields = [
            "plot_id", "stratum", "area_ha", "inclusion_probability", "weight", "boundary",
        ]

    def get_boundary(self, obj):
        """两种后端统一输出 GeoJSON dict。"""
        boundary = obj.boundary
        if isinstance(boundary, dict):
            return boundary
        return json.loads(boundary.geojson)  # PostGIS GEOSGeometry


class AllometricEquationSerializer(serializers.ModelSerializer):
    species = serializers.SlugRelatedField(
        slug_field="code", many=True, queryset=AllometricEquation.species.rel.model.objects.all()
    )

    class Meta:
        model = AllometricEquation
        fields = [
            "equation_id", "response", "form", "params", "dbh_unit", "height_unit",
            "species", "source", "status",
        ]


class EquationSetSerializer(serializers.ModelSerializer):
    equations = serializers.SlugRelatedField(slug_field="equation_id", many=True, read_only=True)

    class Meta:
        model = EquationSet
        fields = ["id", "set_id", "description", "equations", "confirmed", "confirmed_at", "frozen_hash"]


class SurveyVersionSerializer(serializers.ModelSerializer):
    interval_years = serializers.ReadOnlyField()

    class Meta:
        model = SurveyVersion
        fields = [
            "id", "name", "survey_t1", "survey_t2", "equation_set",
            "dbh_threshold_cm", "position_tolerance_m", "interval_years",
            "confirmed", "confirmed_at", "equation_set_hash",
        ]


class VerificationTicketSerializer(serializers.ModelSerializer):
    plot_id = serializers.CharField(source="plot.plot_id", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = VerificationTicket
        fields = [
            "id", "plot_id", "category", "category_label",
            "tree_no_t1", "tree_no_t2", "detail", "status", "created_at",
        ]
