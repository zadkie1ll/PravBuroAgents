# PravBuro Agents

Веб-приложение для агентской реферальной программы: регистрация с email-кодом, кабинет агента, QR и реферальная ссылка, публичная форма заявки, Telegram-модерация и отправка одобренных лидов в Bitrix24.

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

Приложение откроется на `http://localhost:8020`.

По умолчанию приложение поднимается с PostgreSQL из `docker-compose.yml`. Если SMTP не настроен, код регистрации выводится в логи приложения. Для Telegram-модерации укажите `PRAVBURO_AGENTS_TELEGRAM_BOT_TOKEN` и `PRAVBURO_AGENTS_TELEGRAM_ADMIN_IDS`.

Для отладки можно включить кнопку моментального входа через `PRAVBURO_AGENTS_DEBUG_LOGIN_ENABLED=1`. Она создаёт тестового агента из `PRAVBURO_AGENTS_DEBUG_AGENT_*` и сразу открывает кабинет.

Суммы выплат задаются через `.env`:

- `PRAVBURO_AGENTS_FIRST_LEVEL_REWARD_RUB` - первая выплата за прямого клиента
- `PRAVBURO_AGENTS_SECOND_LEVEL_REWARD_RUB` - выплата за повторную рекомендацию клиента

## Telegram

Команды бота:

- `/pending` - показать новые заявки
- `/approve ID` - принять и отправить в Bitrix24
- `/reject ID` - отклонить
- `/won ID` - отметить, что клиент начал работу, и начислить выплату
