# Бэкенд проекта

Python 3.9+. Все команды выполняются из корня репозитория.

## Установка и сервер

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --reload
```

Swagger: http://localhost:8000/docs. Настройки из backend/.env читаются автоматически;
переменные окружения имеют приоритет. Секреты и база не попадают в Git.

## Для Искандера

- [AUTH.md](AUTH.md): регистрация, подтверждение почты, вход и сессии.
- [MAIL.md](MAIL.md): настоящая отправка и локальные тестовые письма.
- **[CALCULATIONS.md](CALCULATIONS.md)**: POST /calculations, GET /calculation-options,
  поля формы, расписания, единицы, готовый JavaScript-клиент и примеры ответов.
- [FORMULAS.md](FORMULAS.md): реализованные формулы 0.2.
- [EDGE_CASES.md](EDGE_CASES.md): ограничения, краевые случаи и границы модели.

HTTP-расчёт требует сессию подтверждённого пользователя и credentials: "include".
Тарифы берутся на сервере. Демо-каталог **синтетический** — это не реальные ставки.
История расчётов и прогноз по прошлым данным пока не реализованы.

## Расчёт без фронтенда

```bash
python -m backend.run_calculation
python -m backend.run_calculation --request backend/examples/calculation-stops.json
```

Основные модули: calculation_models.py (контракт), schedule.py (расписание),
transport_calculator.py (формулы), app/main.py (HTTP и авторизация).
Готовые данные и ответы находятся в examples/.

## Проверки

```bash
python -m pip install -r backend/requirements-dev.txt
python -m unittest discover -s backend -p 'test_*.py' -v
```

Тесты используют временные базы и тестовую почту, не отправляют письма через Яндекс.

## Старый учебный пример

```bash
python3 backend/run_demo.py
```

calculator.py и examples/request.json — формат 0.1: один груз, целые недели,
без остановок и ж/д расчёта. Он сохранён для учебного примера. Неизвестные поля
теперь отклоняются. Для продукта используйте транспортный модуль и HTTP-контракт 0.2.
