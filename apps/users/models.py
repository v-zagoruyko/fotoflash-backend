from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email обязателен")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("Email", unique=True)
    name = models.CharField("Имя", max_length=150, blank=True)
    phone = models.CharField(
        "Телефон",
        max_length=16,
        blank=True,
        validators=[RegexValidator(r"^\+?\d{10,15}$", "Укажите номер цифрами, например +79991234567")],
        help_text="Цифры, можно с «+». Например, +79991234567.",
    )
    consent_at = models.DateTimeField(
        "Согласие на обработку ПД",
        null=True,
        blank=True,
        help_text="Когда клиент дал согласие на обработку персональных данных. Заполняется автоматически.",
    )
    bonus_balance = models.PositiveIntegerField(
        "Баланс баллов",
        default=0,
        editable=False,
        help_text="Считается по журналу операций. Чтобы изменить, добавьте операцию в блоке «Бонусы» ниже.",
    )
    is_active = models.BooleanField(
        "Активен",
        default=True,
        help_text="Снимите галочку вместо удаления: клиент не сможет войти, история броней сохранится.",
    )
    is_staff = models.BooleanField(
        "Сотрудник",
        default=False,
        help_text="Даёт доступ в эту админку. Права задаются группами ниже.",
    )
    date_joined = models.DateTimeField("Дата регистрации", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"
        ordering = ("-date_joined",)

    def __str__(self):
        return f"{self.name} ({self.email})" if self.name else self.email
