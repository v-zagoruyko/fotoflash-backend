from celery import shared_task

from . import services


@shared_task
def reconcile_payments() -> dict:
    return services.reconcile_payments()
