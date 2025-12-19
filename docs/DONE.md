# Done

Выполненные задачи.

## 2025-12-19

### Исправление багов
- [x] Заменён bare except на logging в Application.py
- [x] Исправлен race condition в WebSocket.py (добавлен _clients_lock)
- [x] Закомментирован DEBUG print датчиков

### Документация
- [x] Обновлён CLAUDE.md (State Machine, Events, WebSocket API)
- [x] Обновлён docs/ARCHITECTURE.md (5 состояний, триггер завесы, мульти-инференс)
- [x] Актуализированы BACKLOG.md и DONE.md

### Функциональность
- [x] Мульти-инференс (3 кадра с голосованием по большинству)
- [x] Триггер инференса по освобождению завесы
- [x] Event система для backend (JSON события)
- [x] backend_simulator.py для тестирования WebSocket API
- [x] Потокобезопасность PLC (добавлен _modbus_lock)

### Тесты
- [x] Unit-тесты для Application.py (40 тестов)
- [x] Unit-тесты для PLC.py

## 2025-12-18

### Инициализация проекта
- [x] Создан репозиторий
- [x] Базовая структура сервисов (Application, inference_service)
- [x] WebSocket коммуникация между сервисами
- [x] Интеграция с ПЛК через Modbus RTU
- [x] YOLO11n модель для классификации

### Документация
- [x] README.md с описанием и инструкциями
- [x] CLAUDE.md для AI-ассистента
- [x] Структура docs/ (ARCHITECTURE, BACKLOG, IN_PROGRESS, DONE)

---

*Последнее обновление: 2025-12-19*
