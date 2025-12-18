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
│  ├── State Machine (IDLE/DUMPING_PLASTIC/DUMPING_ALUMINUM)  │
│  └── PLC Interface (Modbus RTU @ /dev/ttyUSB0)              │
└─────────────────────────────────────────────────────────────┘
           ▲                              ▲
           │ WebSocket                    │ WebSocket
           │ (client: "vision")           │ (client: "app")
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│ inference_service.py│        │   Backend Service   │
│  ├── CameraManager  │        │     (будущий)       │
│  └── InferenceEngine│        │                     │
└─────────────────────┘        └─────────────────────┘
```

### WebSocket протокол

**Клиент "vision" (inference_service):**
- Регистрация: отправляет `"vision"` при подключении
- Запрос: `"bottle_exist"` / `"bank_exist"` / `"none"`
- Ответ: `"bottle"` / `"bank"` / `"none"`

**Клиент "app" (backend, будущий):**
- Регистрация: отправляет `"app"` при подключении
- Команды: `"dump_container:plastic"`, `"dump_container:aluminium"`, `"cmd_full_clear_register"`, etc.
- `get_command()` — одноразовое чтение (очищается после)
- `get_state()` — непрерывное чтение состояния

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

## Model

YOLO11n classification model: `/home/abelyaev/Documents/CODE/BottlesClassifier/best_rknn_model`
- Input: 1024x1024 RGB
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

## Legacy Files (не используются в основном потоке)

- `interference.py` — альтернативная реализация инференса
- `InferenceClient.py` — старый TCP клиент (заменён на WebSocket)
- `Application copy.py` — бэкап
