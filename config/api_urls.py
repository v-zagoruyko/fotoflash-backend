from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.bookings.views import BookingViewSet
from apps.catalog.views import ExtraViewSet, LocationViewSet
from apps.documents.views import DocumentViewSet
from apps.payments.views import TBankNotificationView

router = SimpleRouter()
router.register("locations", LocationViewSet, basename="location")
router.register("extras", ExtraViewSet, basename="extra")
router.register("documents", DocumentViewSet, basename="document")
router.register("bookings", BookingViewSet, basename="booking")

urlpatterns = [
    path("payments/tbank/notification/", TBankNotificationView.as_view()),
    path("", include("apps.users.urls")),
    path("", include(router.urls)),
]
