from django.urls import include, path
from rest_framework.routers import DefaultRouter

from forest import views

router = DefaultRouter()
router.register("plots", views.PlotViewSet, basename="plot")
router.register("equation-sets", views.EquationSetViewSet, basename="equationset")
router.register("survey-versions", views.SurveyVersionViewSet, basename="surveyversion")
router.register(
    "verification-tickets", views.VerificationTicketViewSet, basename="verificationticket"
)
router.register("revision-batches", views.RevisionBatchViewSet, basename="revisionbatch")
router.register(
    "revision-conclusions", views.RevisionConclusionViewSet, basename="revisionconclusion"
)

urlpatterns = [
    path("api/", include(router.urls)),
    path("api/estimates/", views.EstimateView.as_view(), name="estimates"),
    path("api/health/", views.health, name="health"),
]
