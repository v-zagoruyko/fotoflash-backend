from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.bookings.views import BookingViewSet
from apps.catalog.views import ExtraViewSet, LocationViewSet
from apps.core.views import StudioView
from apps.documents.views import DocumentViewSet
from apps.payments.views import SimulatePaymentView, TBankNotificationView

router = SimpleRouter()
router.register("locations", LocationViewSet, basename="location")
router.register("extras", ExtraViewSet, basename="extra")
router.register("documents", DocumentViewSet, basename="document")
router.register("bookings", BookingViewSet, basename="booking")

urlpatterns = [
    path("studio/", StudioView.as_view()),
    path("payments/tbank/notification/", TBankNotificationView.as_view()),
    path("dev/payments/<int:payment_id>/simulate/", SimulatePaymentView.as_view()),
    path("", include("apps.users.urls")),
    path("", include(router.urls)),
]
