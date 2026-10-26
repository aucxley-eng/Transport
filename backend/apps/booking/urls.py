from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.booking.views import (
    BoardView,
    CheckoutView,
    DepartureViewSet,
    HoldView,
    RouteViewSet,
    SearchView,
    SeatMapView,
    TicketViewSet,
)

router = DefaultRouter()
router.register("routes", RouteViewSet, basename="routes")
router.register("departures", DepartureViewSet, basename="departures")
router.register("tickets", TicketViewSet, basename="tickets")

urlpatterns = [
    path("", include(router.urls)),
    path("booking/search/", SearchView.as_view(), name="booking-search"),
    path("booking/holds/", HoldView.as_view(), name="booking-holds"),
    path("booking/checkout/", CheckoutView.as_view(), name="booking-checkout"),
    path("booking/seats/", SeatMapView.as_view(), name="booking-seats"),
    path("booking/board/", BoardView.as_view(), name="booking-board"),
]
