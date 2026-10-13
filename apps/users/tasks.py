from celery import shared_task
from django.core.mail import send_mail


@shared_task
def send_login_code(email: str, code: str) -> None:
    send_mail(
        "Код для входа в ФОТОФЛЕШ",
        f"Ваш код для входа: {code}\n\nОн действует 10 минут. Если вы не запрашивали код, просто проигнорируйте это письмо.",
        None,
        [email],
    )
