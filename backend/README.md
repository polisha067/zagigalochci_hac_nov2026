# Бэкенд: запуск и документы команды

Все команды выполняются из корня репозитория. Python 3.9+; контейнер использует 3.12.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --reload
```

Swagger: `http://localhost:8000/docs`. Настройки читаются из `backend/.env`;
переменные окружения имеют приоритет. Секреты и локальные базы исключены из Git.

## Актуальные документы

| Задача | Документ |
|---|---|
| Искандер: форма, населённые пункты, остановки, расчёт | **[RUSSIA_NETWORK.md](RUSSIA_NETWORK.md)** |
| Даша: параметры запроса, уточнения и тот же ответ для объяснения | **[RUSSIA_NETWORK.md](RUSSIA_NETWORK.md)**, [свойства груза](CARGO_INPUT.md) |
| Полина: формулы, допущения, границы покрытия | **[RUSSIA_NETWORK.md](RUSSIA_NETWORK.md)**, [карта источников](data_sources/README.md) |
| Регистрация, подтверждение почты, сессия | [AUTH.md](AUTH.md), [MAIL.md](MAIL.md) |
| Устройство симулятора и правил транспорта | [NETWORK.md](NETWORK.md), [NETWORK_FORMULAS.md](NETWORK_FORMULAS.md) |
| Детали авиационных и речных данных | [AVIATION.md](AVIATION.md), [RIVER.md](RIVER.md) |
| Совместимость со старыми экранами | [legacy/README.md](legacy/README.md) |
| Размещение | [deploy/README.md](../deploy/README.md) |

## Основной API для нового фронта

- `GET /network/russia/options` — поиск населённых пунктов и покрытие данных.
- `GET /network/russia/hubs?settlement=Москва` — привязанные хабы.
- `POST /network/russia/calculations` — расчёт цепочек, `engine: "russia"`.
- `GET /network/pipeline/options` — опубликованные трубопроводные компоненты.

HTTP-методы требуют сессию подтверждённого пользователя. Браузер передаёт
`credentials: "include"`. Общий `/calculations` тоже принимает `engine: "russia"`.
Пример запроса: [russia-request.json](examples/russia-request.json).
Готовая обёртка: [russia-client.js](examples/russia-client.js).

Проверка расчёта без HTTP:

```bash
python -m backend.run_russia backend/examples/russia-request.json
```

## Примеры и проверки

[Каталог примеров](examples/README.md) объясняет, какие JSON актуальны, а какие
нужны для проверки низкоуровневых правил. Производные ответы можно получить
командой `python -m backend.export_examples`; они сохраняются локально в `backend/data/generated/`.

```bash
python -m pip install -r backend/requirements-dev.txt
python -m unittest discover -s backend -p 'test_*.py' -q
```

Проверки используют временные базы и почту. Реальные письма через SMTP не отправляются.
