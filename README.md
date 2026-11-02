# Тайби и поддержка

В репозитории два бота в отдельных каталогах:

- `app/` — основной бот заказов Тайби;
- `support_bot/` — бот сотрудников поддержки.

У них **один общий `.env` в корне** и **один контейнер для обоих ботов**. PostgreSQL —
общий сервис с двумя базами: `taibi` для заказов и `tybee_support` для работы поддержки.

## Возможности основного бота

- заказ по артикулу, Mystery Box и персональная заявка;
- оплата и уведомления о статусах;
- личный кабинет;
- диалоги с поддержкой прямо в основном боте;
- опциональная интеграция Avito;
- каталог, статистика и блокировки через `/admin`;
- массовая рассылка с предпросмотром и отчётом о доставке через `/admin`.

## Возможности поддержки

- назначение обращения первому сотруднику, нажавшему «Взять в работу»;
- переписка с клиентом и история в собственной БД;
- счета `/order`, решения по оплате и статусы заказов;
- `/ban` и `/pardon`;
- управление сотрудниками через `/admin`.

Боты обмениваются данными через `support_inbox` и `support_outbox` в основной БД.
Подробности — в [`support_bot/README.md`](support_bot/README.md).

## Настройка

Создайте один файл окружения:

```bash
cp .env.example .env
```

Заполните в нём:

- `TYBEE_BOT_TOKEN` и `TYBEE_ADMIN_IDS`;
- `SUPPORT_BOT_TOKEN`, `SUPPORT_ADMIN_IDS` и при необходимости `SUPPORT_STAFF_IDS`;
- `PAYMENT_REQUISITES` и настройки Avito, если они нужны.

Настройки `POSTGRES_*` и `SUPPORT_POSTGRES_*` уже соответствуют Docker Compose.
Основной бот и поддержка используют `POSTGRES_*` для обмена обращениями и заказами.
Отдельные `.env` внутри каталогов ботов не нужны.

## Robokassa

Для ссылок на оплату через `/order` заполните `ROBOKASSA_SHOP_ID`,
`ROBOKASSA_PASSWORD1`, `ROBOKASSA_PASSWORD2` и `ROBOKASSA_HASH_ALGORITHM`.
В кабинете Robokassa выберите тот же алгоритм. `ROBOKASSA_TEST=true` добавляет `IsTest=1`;
для боевых платежей установите `false`.

ResultURL: `{AVITO_WEBHOOK_URL}/robokassa/result`. Например,
`https://bot.example.com/robokassa/result`. Второй пароль проверяется на сервере,
после успешной оплаты заказ автоматически подтверждается, а поддержка получает
панель дальнейших статусов. HTTP-сервер запускается на `AVITO_WEBHOOK_PORT` и при
выключенном Avito. Штраф за разблокировку также оплачивается через Robokassa;
его сумма в рублях задаётся через `UNBAN_FINE_RUB`.

## Запуск

Перед запуском задайте уникальный сильный `POSTGRES_PASSWORD`; слабый пароль по умолчанию
больше не используется. Порт PostgreSQL доступен только на `127.0.0.1`.

```bash
docker compose up --build
```

Контейнер `bot` запускает оба бота через `python -m scripts.run_bots`. Контейнер `db`
создаёт обе базы на новом volume. Для уже существующего volume создайте базу поддержки
один раз, если её ещё нет:

```bash
docker compose exec db psql -U taibi -d taibi -c 'CREATE DATABASE tybee_support'
```

## Запуск без Docker

Нужны обе PostgreSQL на одном сервере. Установите зависимости и запустите общий launcher:

```bash
pip install --require-hashes -r requirements-lock.txt
python -m scripts.run_bots
```

Перед этим укажите в `.env` локальные адреса баз, обычно `localhost` вместо `db`.

## Проверка

```bash
python -m compileall app scripts support_bot
python -m scripts.smoke_flow
```

Smoke использует две временные SQL-базы и проверяет диалоги, назначение, счета, оплату,
статусы, блокировки, Avito и закрытие обращения. Для него нужна dev-зависимость `aiosqlite`
из `requirements-dev.txt`.

## CI/CD

Workflow компилирует оба проекта и собирает один образ с двумя ботами. Для ручного запуска
основного бота используются secrets `BOT_TOKEN` и `ADMIN_IDS`.
