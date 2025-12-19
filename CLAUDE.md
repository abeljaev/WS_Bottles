# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Автомат для приема тары (Reverse Vending Machine) на базе Radxa RK3588. Классифицирует контейнеры с помощью YOLO11n и управляет сортировкой через ПЛК по Modbus RTU.

**Классы объектов:**
- **PET** — пластиковые бутылки → сброс влево
- **CAN** — алюминиевые банки → сброс вправо
- **FOREIGN** — ложный класс (посторонний предмет) → отклонение

## Commands

```bash
# Сервис ПЛК (контроллер + WebSocket сервер)
python Application.py

# Сервис инференса (отдельный процесс)
python inference_service.py

# Интерактивный режим камеры для тестирования
python inference_service.py --camera
```

### Installation
```bash
# Системные зависимости (Rockchip RK3588)
sudo apt install -y rknpu2-rk3588 python3-rknnlite2

# Python зависимости
pip install -r requirements.txt
```

## Architecture

### Сервисная архитектура
```
┌─────────────────────────────────────────────────────────────┐
│                     WebSocket Server                         │
│                    (ws://localhost:8765)                     │
├─────────────────────────────────────────────────────────────┤
│                      Application.py                          │
│  ├── State Machine (5 состояний)                            │
│  ├── Event Manager (события → app клиент)                   │
│  └── PLC Interface (Modbus RTU @ /dev/ttyUSB0)              │
└─────────────────────────────────────────────────────────────┘
           ▲                              ▲
           │ WebSocket                    │ WebSocket
           │ (client: "vision")           │ (client: "app")
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│ inference_service.py│        │   Backend Service   │
│  ├── CameraManager  │        │  backend_simulator  │
│  └── InferenceEngine│        │                     │
└─────────────────────┘        └─────────────────────┘
```

### State Machine

```
IDLE ──(завеса освободилась + контейнер)──► WAITING_VISION
  ▲                                              │
  │                                    (ответ от vision)
  │                                              ▼
  │◄────────────────────────────────── DUMPING_PLASTIC (PET)
  │                                              │
  │◄────────────────────────────────── DUMPING_ALUMINUM (CAN)
  │                                              │
  │                                          (таймаут)
  │                                              ▼
  └───────(cmd_restore_device)─────────────── ERROR
```

**Состояния:**
- `IDLE` — ожидание контейнера
- `WAITING_VISION` — ожидание ответа от vision (таймаут 2с)
- `DUMPING_PLASTIC` — движение каретки влево (PET)
- `DUMPING_ALUMINUM` — движение каретки вправо (CAN)
- `ERROR` — аппаратная ошибка, ограниченный набор команд

### Event система

События отправляются клиенту `app` в формате JSON:
```json
{
  "event": "container_detected",
  "data": {"plc_type": "bottle"},
  "timestamp": "2025-12-19T10:30:00.123456"
}
```

**События:**
- `container_detected` — контейнер обнаружен (data: plc_type)
- `container_recognized` — контейнер распознан (data: type, confidence)
- `container_not_recognized` — не удалось распознать
- `container_accepted` — контейнер принят (data: type, counter)
- `hardware_error` — аппаратная ошибка (data: error_code, message)
- `device_info` — информация об устройстве
- `photo_ready` — фото готово (data: filename)

### WebSocket протокол

**Клиент "vision" (inference_service):**
- Регистрация: отправляет `"vision"` при подключении
- Запрос от сервера: `"bottle_exist"` / `"bank_exist"` / `"none"`
- Ответ: `"bottle"` / `"bank"` / `"none"`
- Мульти-инференс: 3 кадра с голосованием по большинству

**Клиент "app" (backend):**
- Регистрация: отправляет `"app"` при подключении
- Команды в JSON формате:
```json
{"command": "dump_container", "param": "plastic"}
{"command": "get_device_info"}
{"command": "get_photo"}
```
- Поддерживается legacy формат: `"dump_container:plastic"`
- `get_command()` — одноразовое чтение (очищается после)
- `get_state()` — непрерывное чтение состояния

**Команды app клиента:**
- `get_device_info` — получить информацию об устройстве
- `get_photo` — сделать фото с камеры
- `dump_container` — сбросить контейнер (param: plastic/aluminium)
- `container_unloaded` — мешок выгружен (param: plastic/aluminium)
- `cmd_restore_device` — восстановить из ERROR
- `cmd_full_clear_register` — очистить регистр команд

### Key Files
- `Application.py` — сервис ПЛК, state machine, WebSocket сервер
- `inference_service.py` — async WebSocket клиент для классификации
- `camera_manager.py` — потокобезопасная камера с кольцевым буфером
- `inference_engine.py` — обёртка над YOLO моделью
- `PLC.py` — Modbus RTU интерфейс с битовыми регистрами
- `WebSocket.py` — async WebSocket сервер для нескольких клиентов
- `config.py` — настройки из переменных окружения (.env)

### Modbus Registers
- **Command Register (25)**: `radxa_detected_bottle` (bit 7), `radxa_detected_bank` (bit 6)
- **Status Register (26)**: `bottle_exist` (bit 7), `bank_exist` (bit 6), sensors (bits 1-4)

## Configuration

Переменные окружения через `.env`:
```
MODEL_PATH=/path/to/model
CAMERA_INDEX=0
WEBSOCKET_HOST=localhost
WEBSOCKET_PORT=8765
SAVE_FRAMES=true
OUTPUT_DIR=real_time
```

## Camera

**Разрешение:** 2K (2560x1440)
- Камера работает в максимальном разрешении для качественного захвата
- Кадр ресайзится до 1280x1280 перед инференсом

## Model

**Фреймворк:** Ultralytics YOLO11s (classification)

**Production (RKNN):** `weights/best_11s_rknn_model/`
- Папка с RKNN моделью для Rockchip NPU (RK3588)
- Содержит: `best_11s_cls_1280-rk3588.rknn` + `metadata.yaml`
- **Важно:** Ultralytics требует путь к папке, не к файлу .rknn

**Development (PyTorch):** `weights/best_11s.pt`
- Ultralytics YOLO11s для тестирования без NPU
- Использовать: `MODEL_PATH=weights/best_11s.pt`

**Параметры:**
- Input: 1280x1280 RGB
- Classes: CAN (0), FOREIGN (1), PET (2)
- Mapping: PET→"bottle", CAN→"bank", FOREIGN→"none"

## Code Style

**Комментарии:**
- Только на русском языке
- Только в важных местах или где уже были
- Не добавлять избыточные комментарии

**Docstrings:**
- На русском языке
- Google-формат

```python
def example_function(param1: str, param2: int) -> bool:
    """
    Краткое описание функции.

    Args:
        param1: Описание первого параметра.
        param2: Описание второго параметра.

    Returns:
        Описание возвращаемого значения.

    Raises:
        ValueError: Когда param2 отрицательный.
    """
```

## Threading Model

- **Main thread**: State machine в `Application.run()`
- **PLC thread**: Непрерывный опрос Modbus (период 0.1с)
- **WebSocket thread**: Asyncio event loop для сервера
- **Camera thread**: Непрерывный захват кадров в буфер

## Development Workflow

**Перед каждым изменением:**
1. Консультация с Codex агентом для планирования и советов
2. Небольшие инкрементальные изменения
3. Покрытие тестами нового кода
4. Коммит и пуш после каждого логического изменения
5. Обновление docs/IN_PROGRESS.md и DONE.md

**Best Practices:**
- Retry механизмы для Modbus и WebSocket
- Graceful degradation при ошибках
- Отказоустойчивость — система должна продолжать работу при сбоях компонентов

**Тестирование:**
```bash
pytest tests/ -v
```

## Documentation

Документация в папке `docs/`:
- `ARCHITECTURE.md` — архитектура системы
- `BACKLOG.md` — запланированные задачи
- `IN_PROGRESS.md` — задачи в работе
- `DONE.md` — выполненные задачи
- `PLANS.md` — планы развития

## Tests

```bash
pytest tests/ -v
```

**Структура тестов:**
- `tests/test_application.py` — unit-тесты state machine и команд (40 тестов)
- `tests/test_plc.py` — тесты PLC интерфейса
- `tests/conftest.py` — pytest фикстуры и моки

**Покрытие:**
- State machine переходы
- Обработчики команд
- Vision response handlers
- Event создание и отправка

## Legacy Files (не используются в основном потоке)

- `interference.py` — альтернативная реализация инференса
- `InferenceClient.py` — старый TCP клиент (заменён на WebSocket)
- `Application copy.py` — бэкап
- `backend_simulator.py` — симулятор для тестирования WebSocket API
