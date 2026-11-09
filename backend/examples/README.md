# Примеры запросов и каталогов

## Для нового фронта

- [russia-request.json](russia-request.json) — основной запрос по населённым пунктам.
- [russia-response.json](russia-response.json) — один полный сохранённый ответ.
- [russia-client.js](russia-client.js) — поиск населённых пунктов и вызов расчёта.
- [auth-client.js](auth-client.js) — регистрация, подтверждение и сессия.

## Дополнительные сценарии

- `chukotavia-request.json`, `chukotavia-response.json` — отдельный авиационный метод:
  опубликованные рейсы/ставки и неполная стоимость.
- `river-request.json`, `river-response.json` — контейнерный речной сценарий.
- `network-*.json` и `network-four-modes.json` — входные проверки низкоуровневого
  симулятора: неизвестный груз, заявленные свойства, цепочки нескольких видов.
- `network.json` — синтетический каталог для симулятора, а не пользовательский ответ.
- `calculation-request.json`, `calculation-stops.json`, `calculation-client.js` и
  `catalog.json` — совместимость с контрактом трёх схем 0.2.

Старый автономный пример 0.1 находится в `../legacy/v01/`.

## Получение остальных ответов

В Git оставлены три опорных ответа: основной, авиационный и речной. Остальные
производные JSON не хранятся параллельными копиями:

```bash
python -m backend.export_examples
```

Ответы появятся в `backend/data/generated/examples/` (игнорируется Git).
Генератор ничего не записывает в БД и не отправляет письма. Для одного запроса
можно воспользоваться соответствующим `backend.run_*`.
