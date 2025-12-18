# BottleClassifier

Автомат для приема тары (Reverse Vending Machine) на базе Radxa RK3588.

## Описание

Система классификации контейнеров с помощью нейросети YOLO11n и управления сортировкой через ПЛК по Modbus RTU.

**Классы объектов:**
- **PET** — пластиковые бутылки → сброс влево
- **CAN** — алюминиевые банки → сброс вправо
- **FOREIGN** — посторонний предмет → отклонение

## Установка

### Системные зависимости (Rockchip RK3588)
```bash
sudo apt update
sudo apt install -y rknpu2-rk3588 python3-rknnlite2
```

### Python зависимости
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Переменные окружения
Создайте файл `.env`:
```env
MODEL_PATH=/path/to/best_rknn_model
CAMERA_INDEX=0
WEBSOCKET_HOST=localhost
WEBSOCKET_PORT=8765
SAVE_FRAMES=true
OUTPUT_DIR=real_time
```

## Запуск

### Сервис ПЛК (основной)
```bash
python Application.py
```

### Сервис инференса (отдельный процесс)
```bash
python inference_service.py
```

### Интерактивный режим камеры (тестирование)
```bash
python inference_service.py --camera
```

## Архитектура

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

## Документация

Подробная документация находится в папке [docs/](docs/):
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — архитектура системы
- [BACKLOG.md](docs/BACKLOG.md) — запланированные задачи
- [IN_PROGRESS.md](docs/IN_PROGRESS.md) — задачи в работе
- [DONE.md](docs/DONE.md) — выполненные задачи

## Тестирование

```bash
pytest tests/ -v
```

## Лицензия

MIT
