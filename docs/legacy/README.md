# Предыдущие прототипы и совместимость

Новый фронт и ИИ-ассистент используют [контракт russia](../calculations/RUSSIA_NETWORK.md).
Эта страница нужна для поддержки ранее сделанных экранов и проверки старых формул.

| Версия | Назначение | Где находится |
|---|---|---|
| 0.1 | Первый автономный учебный расчёт авто, без HTTP | [`backend/legacy/v01`](../../backend/legacy/v01), запуск `python -m backend.legacy.v01.run_demo` |
| 0.2 | Сравнение трёх фиксированных схем; сохранён рабочий HTTP-контракт | [API](../calculations/pilot/CALCULATIONS.md), [формулы](../calculations/pilot/FORMULAS.md), [краевые случаи](../calculations/pilot/EDGE_CASES.md) |
| 0.3, engine network | Низкоуровневый запрос по точным ID хабов и услуг; симулятор используется актуальной сетью | [устройство](../calculations/network/NETWORK.md), [API](../calculations/network/NETWORK_API.md), [формулы](../calculations/network/NETWORK_FORMULAS.md) |

Модули 0.2 и сетевой симулятор используются API, расписаниями и проверками.
Учебный каталог [`backend/examples/network.json`](../../backend/examples/network.json)
нужен при запуске низкоуровневого метода, это не каталог общей сети РФ.

Старую команду `python3 backend/run_demo.py` заменяет
`python -m backend.legacy.v01.run_demo`. Код и исходные JSON 0.1 перенесены вместе.
