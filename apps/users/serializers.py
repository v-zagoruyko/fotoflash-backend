from rest_framework import serializers

from apps.bonuses.models import BonusTransaction

from .models import User


class CodeRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class CodeVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=4, max_length=8)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    consent = serializers.BooleanField(
        required=False, default=False, help_text="Согласие на обработку персональных данных. Обязательно при первом входе."
    )

    def validate_email(self, value):
        return value.strip().lower()


class UserSerializer(serializers.ModelSerializer):
    has_consent = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "name", "phone", "bonus_balance", "has_consent")
        read_only_fields = ("id", "email", "bonus_balance", "has_consent")

    def get_has_consent(self, obj) -> bool:
        return obj.consent_at is not None

    def update(self, instance, validated_data):
        # Пишем только изменённые поля: полная запись затёрла бы баланс баллов, обновлённый параллельно.
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save(update_fields=list(validated_data))
        return instance


class BonusTransactionSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = BonusTransaction
        fields = ("id", "kind", "kind_display", "points", "booking", "comment", "created_at")
