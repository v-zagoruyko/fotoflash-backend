# Контракт API для фронтенда

Единый источник правды о том, как фронтенд общается с бэкендом. Подтягивается в сессию фронтенда импортом из его `CLAUDE.md`. Если этот файл расходится с OpenAPI-схемой, прав схема (`/api/schema/`), а файл надо поправить.

Меняешь API на бэкенде → обнови этот файл в том же коммите.

## Где что

| Что | Адрес (dev) |
|---|---|
| API | `http://localhost:8000/api/v1/` |
| Swagger | `http://localhost:8000/api/docs/` |
| OpenAPI-схема (YAML) | `http://localhost:8000/api/schema/` |
| Админка | `http://localhost:8000/admin/` (`admin@example.com` / `admin`) |
| Письма (mailpit) | `http://localhost:8025` |
| Файлы (фото, PDF) | `http://localhost:8000/media/...`, в ответах API это уже абсолютные URL |

Поднять бэкенд: в `../fotoflash-backend` выполнить `docker compose up -d`. Демо-данные: залы, оборудование, клиенты, брони (см. `CLAUDE.md` бэкенда, команда `seed_demo`).

Типы для фронтенда можно генерировать из схемы (например `openapi-typescript http://localhost:8000/api/schema/`).

## Общие правила

- **Сессия и CSRF.** Вход по cookie (`sessionid`, httpOnly). Фронтенд и API должны быть на одном origin (в dev через proxy/rewrites Next.js `/api/* → :8000`, в проде один домен). Запросы с `credentials: "include"`.
  Перед первым небезопасным запросом (POST/PATCH) вызвать `GET /auth/csrf/`: ответ `{"csrfToken": "..."}` и cookie `csrftoken`; токен передавать в заголовке `X-CSRFToken`.
- **Деньги** приходят строками с копейками: `"3000.00"` (рубли). Баллы целые, 1 балл = 1 ₽. Не парсить как float для расчётов.
- **Время** в ISO 8601 со смещением, по времени студии (`Asia/Yekaterinburg`, `+05:00`). В запросах время без смещения трактуется как время студии. Бронь только на целые часы.
- **Ошибки предметной области**: `{"detail": "текст для пользователя", "code": "machine_code"}` (текст на русском, его можно показывать). Ошибки валидации полей стандартные DRF: `{"field": ["сообщение"]}`.
- **Списки каталога** (`locations`, `extras`, `documents`) отдаются массивом без пагинации. `bookings/` пагинируется: `{count, next, previous, results}`, `?page=`.
- Публичные эндпоинты можно вызывать без входа, остальные вернут `403`, если сессии нет.

### Коды ошибок (`code`)

| code | HTTP | Когда |
|---|---|---|
| `consent_required` | 400 | новому клиенту нужен `consent: true` |
| `invalid_code` | 400 | код неверный, просрочен или исчерпаны попытки |
| `account_disabled` | 403 | аккаунт отключён |
| `too_many_requests` | 429 | слишком частые запросы кода (пауза 60 с, 5 в час на email) |
| `invalid_time_range` | 400 | не целые часы, в прошлом, вне рабочих часов, короче минимума, дальше горизонта бронирования |
| `slot_unavailable` | 409 | слот занят (в т.ч. смежным залом) |
| `extra_unavailable` | 400 | допа нет, он занят на это время или часы вне диапазона |
| `bonus_limit_exceeded` | 400 | баллов больше доступного (баланс, доля от стоимости) |
| `location_unavailable` | 400 | зал отключён |
| `invalid_booking_state` | 409 | действие неприменимо к статусу (оплата не нужна, уже отменена и т.п.) |
| `cancellation_not_allowed` | 409 | позже срока отмены |
| `payment_provider_error` | 502 | банк недоступен, повторить позже |

## Вход по email-коду

1. `POST /auth/code/request/` `{ "email" }` → `204` (ответ одинаков, есть такой клиент или нет). Письмо с 6-значным кодом, код живёт 10 минут.
2. `POST /auth/code/verify/` `{ "email", "code", "name"?, "consent"? }` → `200` с пользователем, устанавливает сессию.
   Новому клиенту (и существующему без согласия) обязателен `consent: true`, иначе `400 consent_required`. Показать чекбокс согласия на обработку персональных данных со ссылкой на документ.
3. `POST /auth/logout/` → `204`.

Пароля нет. «Гостевая бронь»: клиент выбирает зал и время без входа, вход по коду происходит на шаге оформления, дальше создаётся бронь.

Пользователь: `{ id, email, name, phone, bonus_balance, has_consent }`.

## Эндпоинты

Публичные:

| Метод и путь | Ответ |
|---|---|
| `GET /locations/` | залы: `slug, title, description, cover, images[], rules_document{slug,title,url}, hour_price, late_from, late_surcharge, weekend_surcharge, open_time, close_time, min_hours` |
| `GET /locations/{slug}/` | один зал |
| `GET /locations/{slug}/availability/?date=YYYY-MM-DD&days=1..14` | `[{date, slots:[{starts_at, ends_at, is_available, price}]}]`, `price` — цена этого часа с доплатами |
| `GET /locations/{slug}/availability/?date=YYYY-MM-DD&days=1..93&summary=1` | лёгкий режим: `[{date, free_hours}]`, без списка слотов. Для отметки дней без свободных часов в календаре; слоты запрашивать только для выбранного дня. Без `summary` `days` больше 14 вернёт `400` |
| `GET /extras/` | оборудование и услуги: `id, title, description, cover, category, hour_price`, где `category` — `{id, title}` или `null`. Список уже отсортирован: по порядку категорий, затем по порядку позиций, позиции без категории в конце. Порядок задаёт менеджер в админке, на фронтенде не пересортировывать |
| `GET /documents/`, `GET /documents/{slug}/` | `{slug, title, url, updated_at}`, `url` — PDF |
| `POST /bookings/quote/` | расчёт цены без создания брони |

Для вошедшего клиента:

| Метод и путь | Что делает |
|---|---|
| `GET /me/`, `PATCH /me/` | профиль; менять можно `name`, `phone` (формат `+79991234567`) |
| `GET /me/bonus/` | `{balance, transactions[{id, kind, kind_display, points, booking, comment, created_at}]}` (последние 50) |
| `POST /bookings/` | создаёт бронь в статусе `pending` и держит слот |
| `GET /bookings/?status=confirmed,pending` | брони клиента (пагинация) |
| `GET /bookings/{id}/` | одна бронь, `id` — номер брони |
| `POST /bookings/{id}/pay/` | ссылка на оплату в `payment_url` |
| `POST /bookings/{id}/cancel/` | отмена |

Тело `quote` и создания брони одинаковое:

```json
{
  "location": "studiya-a",
  "starts_at": "2030-01-07T10:00:00+05:00",
  "ends_at": "2030-01-07T12:00:00+05:00",
  "extras": [{ "extra": 12, "hours": 1 }],
  "bonus_points": 300
}
```

`extras[].hours` не обязателен (по умолчанию на всё время брони). `bonus_points` по умолчанию 0.

Ответ `quote`: `hours, location_amount, extras[{extra,title,hours,unit_price,amount}], subtotal, bonus_points, total, max_bonus_points, breakdown{base,late,weekend,...}`. Для гостя `max_bonus_points = 0`. `max_bonus_points` показывать как предел ползунка/поля списания баллов.

Бронь: `id, status, status_display, location{slug,title}, starts_at, ends_at, expires_at, subtotal, bonus_spent, total, extras[], payment_url, can_cancel, cancel_deadline, created_at, cancelled_at`.

## Жизненный цикл брони и оплата

Статусы: `pending` (ждёт оплаты, слот удерживается до `expires_at`, по умолчанию 30 минут) → `confirmed` (оплачена) → `completed` (прошла, переводится ночью). Из `pending`/`confirmed` в `cancelled`, неоплаченная по таймеру в `expired`.

Сценарий оплаты:

1. `POST /bookings/` → бронь `pending`.
2. `POST /bookings/{id}/pay/` → в ответе `payment_url`. Перенаправить клиента на него (`window.location.assign`). Повторный вызов отдаёт ту же ссылку, пока она жива.
3. Банк возвращает клиента на `{FRONTEND_URL}/profile/bookings/{id}?payment=success` или `?payment=fail`. **Параметр не доказывает оплату**: статус обновляется по уведомлению банка, поэтому на странице брони опрашивать `GET /bookings/{id}/` (например, раз в 2 с до 30 с), пока `status` не станет `confirmed` или `payment=fail`.
4. Если сумма к оплате 0 (всё оплачено баллами), бронь сразу `confirmed`, `pay` не нужен (вернёт `invalid_booking_state`).
5. Время на оплату показывать по `expires_at`; после истечения бронь станет `expired`, оформить заново.

Отмена: `can_cancel` и `cancel_deadline` приходят готовыми, фронтенд ничего не считает. Неоплаченную клиент отменяет всегда, оплаченную до `cancel_deadline`. При отмене оплаченной брони деньги возвращаются на карту, баллы возвращаются, кешбэк снимается.

Баллы: тратятся при создании брони, возвращаются при отмене и истечении. Кешбэк начисляется после оплаты (процент задаёт студия).

### Форма оплаты банка и сертификаты Минцифры

Страница оплаты Т-Банка подписана сертификатом Минцифры. У клиентов без этого корня в системе браузер может показать ошибку сертификата. Нужна подсказка рядом с кнопкой оплаты и на странице неудачи:

- если страница оплаты не открылась или показала ошибку сертификата, это не проблема сайта;
- варианты: открыть ссылку в Яндекс Браузере, или в приложении банка, или установить российские сертификаты (инструкция Т-Банка: https://tbank.ru/bank/help/certificates/);
- ссылка на оплату остаётся действительной до `expires_at`, её можно открыть с другого устройства (показать `payment_url` и кнопку «скопировать»).

## Контакты, реквизиты и горизонт бронирования

`GET /studio/` без входа:

```json
{
  "address": "", "phone": "", "email": "", "map_url": "",
  "booking_horizon_days": 92,
  "legal_name": "", "inn": "", "ogrnip": "", "legal_address": ""
}
```

Все поля, кроме `booking_horizon_days`, строки; заполняются менеджером в «Настройках студии», пустая строка значит «не задано», блок на фронтенде в этом случае скрывать. Контакты и реквизиты на фронтенде не хардкодить.

- `legal_name`, `inn`, `ogrnip`, `legal_address` — реквизиты исполнителя для футера (для ИП: «ИП Фамилия Имя Отчество»). `ogrnip` пока пустой, его нужно внести владельцу.
- `booking_horizon_days` — на сколько дней вперёд можно бронировать (по умолчанию 92). Последний доступный день: сегодня (по времени студии) плюс это число. Календарь заканчивать на этой дате; для времени дальше `quote` и `POST /bookings/` вернут `400 invalid_time_range` с понятным `detail`.

## Документы

PDF загружаются в админке, у каждого `slug`. Фронтенд ссылается на документы по `slug`. Сейчас есть `studio-rules` (правила студии). Итоговый перечень (оферта, политика конфиденциальности и т.д.) и места ссылок определяются при работе над фронтендом: нужные `slug` согласовать и добавить в `docs/backend-requests.md` фронтенда.

## Ограничения MVP-стенда (`fotoflash.zagoruykov.site`)

- **Сброс кеша каталога не автоматический.** Бэкенд пока не вызывает `POST /revalidate` фронтенда: правки в админке (залы, допы, категории, контакты) и перенос данных видны на сайте после истечения кеша (до часа) или ручного вызова `POST /revalidate` с секретом. Автоматический вызов в бэклоге бэкенда (`docs/backlog.md`).
- **Оплата фейковая.** `PAYMENT_PROVIDER=fake`, `PAYMENT_SIMULATION=True` (эндпоинт `simulate` работает при `DEBUG=False`). Страница `/dev/pay/[id]` во фронтенде сейчас отдаёт 404 в production-сборке: на стенде её нужно включать отдельным флагом.
- **Коды входа** приходят не на почту, а в логи сервера (`docker compose logs worker`), пока не подключён SMTP.

## Только для разработки (DEBUG)

- Провайдер платежей `fake`: `payment_url` ведёт на `{FRONTEND_URL}/dev/pay/fake-{paymentId}`. Фронтенд в dev делает там страницу «оплатить / отклонить», которая вызывает
  `POST /api/v1/dev/payments/{paymentId}/simulate/` `{ "status": "CONFIRMED" | "REJECTED" | "CANCELED" }` → `{payment_status, booking_status}`. Только для своих платежей, в проде вернёт `404`. Эту страницу в продакшн-сборку не включать.
- Коды входа смотреть в mailpit (`http://localhost:8025`).
