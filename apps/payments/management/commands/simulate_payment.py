from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payments import services
from apps.payments.models import Payment


class Command(BaseCommand):
    help = "Dev: имитирует ответ банка для тестового платежа (provider=fake)"

    def add_arguments(self, parser):
        parser.add_argument("payment_id", type=int)
        parser.add_argument("--status", default="CONFIRMED", choices=["CONFIRMED", "REJECTED", "CANCELED"])

    def handle(self, *args, payment_id, status, **opts):
        if not settings.DEBUG:
            raise CommandError("Только при DEBUG=True.")
        payment = Payment.objects.filter(pk=payment_id, provider=Payment.Provider.FAKE).first()
        if payment is None:
            raise CommandError("Тестовый платёж не найден.")
        payment.raw = {**payment.raw, "fake_status": status}
        payment.save(update_fields=["raw"])
        payment = services.apply_status(payment, status)
        payment.booking.refresh_from_db()
        self.stdout.write(f"Платёж {payment.pk}: {payment.get_status_display()}, бронь №{payment.booking_id}: {payment.booking.status}")
