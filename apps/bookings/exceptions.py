from apps.core.errors import ApiError


class BookingError(ApiError):
    """Базовая ошибка бронирования."""

    code = "booking_error"


class InvalidTimeRange(BookingError):
    code = "invalid_time_range"


class LocationUnavailable(BookingError):
    code = "location_unavailable"


class SlotUnavailable(BookingError):
    code = "slot_unavailable"
    status_code = 409

    def __init__(self, message: str = "Это время уже занято. Выберите другое."):
        super().__init__(message)


class ExtraUnavailable(BookingError):
    code = "extra_unavailable"


class BonusLimitExceeded(BookingError):
    code = "bonus_limit_exceeded"


class InvalidBookingState(BookingError):
    code = "invalid_booking_state"
    status_code = 409


class CancellationNotAllowed(BookingError):
    code = "cancellation_not_allowed"
    status_code = 409
