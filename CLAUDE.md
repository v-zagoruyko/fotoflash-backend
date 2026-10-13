# fotoflash-backend

Бэкенд сайта бронирования фотостудии (переделка старого проекта с нуля). Фронтенд — отдельный репозиторий (Next.js, тот же домен). Старые исходники для справки: `~/Downloads/Projects/Freelance/fotoflash-legacy/`.

## Стек
Python 3.13, Django 5.2 LTS, DRF, drf-spectacular, PostgreSQL 17, Celery + Redis, uv. Вся разработка идёт в Docker.

## Команды (всё через compose, `COMPOSE_FILE` задан в `.env`)
- `docker compose up -d` — web (:8000), worker, beat, db, redis, mailpit (:8025)
- `docker compose run --rm web python manage.py makemigrations <app>` — миграции генерировать по одному приложению, иначе Django дробит `0001_initial`
- `docker compose run --rm web pytest -q`
- `docker compose run --rm web sh -c "ruff format . && ruff check ."`
- `docker compose run --rm web python manage.py createsuperuser`
- Демо-данные (только DEBUG): `docker compose run --rm -v "$HOME/Downloads/Fotoflash(sql)":/legacy/sql:ro -v "<legacy>/fotoflast.studio-api/api/media":/legacy/media:ro web python manage.py seed_demo --legacy-db /legacy/sql/db.sqlite3 --legacy-media /legacy/media` (идемпотентна; `--reset-demo` пересоздаёт демо-клиентов и брони). Создаёт `admin@example.com` / `admin`, залы и допы из дампа, клиентов `clientNN@demo.fotoflash.local` и сгенерированные брони.

## Качество
- pre-commit (ruff check + format, пробелы, YAML, секреты/приватные ключи): один раз `uvx pre-commit install`; вручную `uvx pre-commit run --all-files`. Хуки используют ruff из `uv.lock`, миграции исключены (`--force-exclude`).
- CI (`.github/workflows/ci.yml`) на push в `main` и PR: ruff format/check, `makemigrations --check`, валидация OpenAPI, pytest на Postgres 17 + Redis. Деплоя в CI пока нет.
- После `uv add` в dev: `docker compose build` (у worker/beat свои образы).

## Структура
`config/` — настройки, urls, celery. `apps/`: `core` (StudioSettings), `users`, `documents`, `catalog` (залы, оборудование и услуги), `bookings`, `payments`, `bonuses`.

## Соглашения
- Код и идентификаторы на английском; `verbose_name`, `help_text`, статусы и сообщения для админки/пользователя — на русском. У каждого поля есть `verbose_name`, у неочевидных — `help_text`.
- Деньги: `Decimal` в рублях (в API банка конвертируем в копейки на границе). Баллы: целые, 1 балл = 1 ₽.
- Баланс баллов меняется только через `BonusTransaction` (журнал неизменяемый), не прямой записью в `User.bonus_balance`.
- Пересечение броней запрещено на уровне БД (`ExclusionConstraint`, нужен `btree_gist`); смежные залы (`Location.conflicts_with`) проверяются в сервисном слое.
- Админка — основной интерфейс менеджера: inlines, help_text, русские названия обязательны для новых моделей.
- Изображения: поле `apps.core.fields.OptimizedImageField` при загрузке конвертирует в WebP (q=82) и уменьшает до `max_side` (залы 2000 px, допы 1000 px), лимит файла 20 МБ. Для уже загруженных — `python manage.py optimize_images [--dry-run]`. Новые поля с картинками делать этим типом.
- Часовой пояс — `Asia/Yekaterinburg`, в БД `timestamptz`.

## Решения
- Вход клиентов: email + одноразовый код (код хранить в Redis с TTL), сессионная cookie. Гостевая бронь создаёт аккаунт по ходу.
- Оплата: Т-Банк (Init на бэкенде, вебхук с проверкой Token, чек 54-ФЗ). Запросы к банку идут с отдельным CA-бандлом Минцифры (Russian Trusted Root + Sub CA), глобальное хранилище не трогаем и `verify=False` не используем. Форма оплаты открывается в браузере клиента, ему сертификат может понадобиться — нужна подсказка на фронте.
- Холд слота за неоплаченной бронью — `StudioSettings.hold_minutes` (30 мин), снимает Celery.
- Бонусы: один кешбэк-процент баллами с денежной части брони; `StudioSettings.cashback_on_bonus_bookings` выключает кешбэк, если часть оплачена баллами (как на старом сайте). Новости, фотографы, фотосессии из старого сайта не переносим.
- Документы: загрузка PDF, у каждого `slug`; какие нужны и где ссылки — решим при работе над фронтом.

## Бронирование (`apps/bookings`)
- Вся логика в `services.py` (`quote_booking`, `create_booking`, `confirm_booking`, `confirm_booking_manually`, `cancel_booking`, `expire_pending_bookings`, `get_day_slots`), цена в `pricing.py`. Ошибки — `BookingError` с русским `message` и `code`.
- Статус брони — `FSMField(protected=True)` (django-fsm-2): `pending → confirmed → completed`, из `pending`/`confirmed` в `cancelled`, из `pending` в `expired`. Присваивать `booking.status` нельзя, только переходы (`confirm()`, `cancel(now)`, `expire()`, `complete()`) внутри сервисов. «Завершена» ставит Celery-задача `complete_finished_bookings` в 00:00 по времени студии. Номер брони — её `id`.
- Админка броней: клиент и статус только для чтения, завершённую бронь править нельзя, удаление запрещено всегда; подтверждение/отмена — кнопками на карточке и действиями в списке.
- Порядок в админке — drag-and-drop через `django-admin-sortable2` (`SortableAdminMixin`, поле `sort` не редактируется вручную). Для новой сортируемой модели: поле `sort` (`PositiveIntegerField(default=0, editable=False, db_index=True)`) первым в `Meta.ordering`.
- Статусы и баллы меняем только сервисами. В админке создание брони идёт через сервисы (форма с выбором клиента, допов, баллов, «оплата получена»), а редактирование существующей — осознанно в обход сервисов.
- Снятие холда: когда появится сервис эквайринга, порядок такой: сначала отмена заказа в банке, и только после успеха снимаем холд (`expire_pending_bookings` и `cancel_booking` должны вызывать отмену платежа). Опоздавшая оплата для просроченной брони → возврат денег.

## API `/api/v1` (документация: `/api/docs/`, схема `/api/schema/`)
- Вход: `POST auth/code/request/` → письмо с 6-значным кодом (Redis, TTL 10 мин, пауза 60 с, 5 в час, 5 попыток), `POST auth/code/verify/` открывает сессию-cookie; новый клиент создаётся при первом входе, нужен `consent: true`. Перед POST/PATCH фронт берёт токен через `GET auth/csrf/` и шлёт `X-CSRFToken`.
- Публично: `locations`, `locations/{slug}/availability/?date=&days=`, `extras`, `documents`, `bookings/quote/` (баллы только после входа).
- После входа: `me/`, `me/bonus/`, `bookings/` (создать, список, `{id}/`, `{id}/cancel/`).
- Ошибки предметной области — `ApiError` (`apps/core/errors.py`) → `{"detail": "...", "code": "..."}`; слот занят и неверное состояние → 409, лимит кодов → 429.
- Оплата: `POST bookings/{id}/pay/` создаёт платёж и возвращает `payment_url` (пока ссылка жива, отдаёт ту же), банк шлёт уведомления на `POST payments/tbank/notification/` (подпись Token, ответ `OK`).

## Платежи (`apps/payments`)
- Провайдеры: `tbank` (`providers/tbank.py`, urllib + SSL-контекст с бандлом Минцифры из `certs/russian_trusted_ca_bundle.pem`; корень до 2032, промежуточные до 2027 и 2029) и `fake` (dev). Какой используется для новых платежей — `PAYMENT_PROVIDER`; у каждого `Payment` провайдер записан.
- Токен по спецификации банка (`make_token`: корневые скаляры + Password, сортировка по ключу, SHA-256). Сумма в копейках, `OrderId = <booking>-<payment>`, `RedirectDueDate` = конец холда, чек одной позицией на всю сумму.
- Снятие холда и отмена: сначала банк (`GetState` → `Cancel`/подтверждение, если успели оплатить), только потом меняем бронь. Банк недоступен — бронь не меняем, повторим. Опоздавшая оплата за отменённую/просроченную бронь возвращается автоматически; `reconcile_payments` (Celery, раз в 5 мин) подбирает потерянные уведомления и «осиротевшие» оплаты.
- Dev: `python manage.py simulate_payment <payment_id> [--status CONFIRMED|REJECTED|CANCELED]` имитирует ответ банка для `fake`.
- Ключи терминала — только в `.env` на сервере (`TBANK_TERMINAL_KEY`, `TBANK_PASSWORD`), в репозиторий не коммитим. Пароль из старого фронтенда был в публичном JS: сменить в ЛК банка.
- Сертификат банка обновляется: перед сроком обновить бандл (корень и подкорневой с `gu-st.ru/content/lending`, подкорневой по AIA из сертификата банка) и проверить `openssl verify`.


## Письма (`apps/bookings/emails.py`, шаблоны `templates/bookings/email/`)
- Подтверждение (после перехода в `confirmed`, с PDF правил зала), напоминание за `StudioSettings.reminder_before_hours` (раз в 10 мин `send_due_reminders`; не шлём, если бронь оформлена уже внутри окна; 0 отключает), отмена оплаченной брони (с пометкой про возврат денег и баллов). Письма уходят через Celery после коммита транзакции, при сбое SMTP задача повторяется. Адрес и телефон студии для писем — в «Настройках студии».
- В dev письма видны в mailpit (http://localhost:8025).

## Прод-окружение
- `.env` на сервере собирает CI, руками не правим. Ключи, перечисленные в `.env.example`, берутся оттуда, а всё, чего в нём нет (секреты: `SECRET_KEY`, пароли БД/банка/SMTP и т.п.), CI берёт из GitHub secrets репозитория. Новую переменную нужно добавить в `.env.example` (без секретного значения) и/или в secrets.

## Не сделано / следующие шаги
 прод-compose и CI (нужен домен, `www`-роутер), раздача static/media в проде, миграция данных из старой SQLite.
