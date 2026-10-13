import pytest

from apps.bonuses.models import BonusTransaction


def test_transaction_updates_user_balance(client_user):
    BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.ACCRUAL, points=100)
    BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.SPEND, points=-30)
    client_user.refresh_from_db()
    assert client_user.bonus_balance == 70


def test_transactions_are_immutable(client_user):
    tx = BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.ADJUST, points=10)
    tx.points = 999
    with pytest.raises(ValueError):
        tx.save()
