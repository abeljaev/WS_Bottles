# Архитектура системы

## Обзор

BottleClassifier — система классификации контейнеров для автомата приема тары, построенная на двухпроцессной архитектуре с WebSocket-коммуникацией.

## Компоненты

### 1. Application.py — Сервис ПЛК

**Ответственность:**
- Управление state machine (IDLE → DUMPING_PLASTIC/DUMPING_ALUMINUM → IDLE)
- Коммуникация с ПЛК через Modbus RTU
- WebSocket сервер для клиентов (vision, app)
- Координация процесса сортировки

**Потоки:**
- Main thread — state machine loop
- PLC thread — непрерывный опрос Modbus (0.1с)
- WebSocket thread — asyncio event loop

### 2. inference_service.py — Сервис инференса

**Ответственность:**
- Захват кадров с камеры
- Классификация объектов (YOLO11n)
- Отправка результатов в Application

**Компоненты:**
- `CameraManager` — потокобезопасная камера с кольцевым буфером
- `InferenceEngine` — обёртка над YOLO моделью

### 3. Backend Service (будущий)

**Ответственность:**
- Пользовательский интерфейс
- Статистика и отчёты
- Удалённое управление

## Протоколы

### WebSocket (ws://localhost:8765)

**Клиент "vision":**
```
→ "vision"              # регистрация
← "bottle_exist"        # запрос классификации
→ "bottle" | "bank" | "none"  # результат
```

**Клиент "app":**
```
→ "app"                 # регистрация
→ "dump_container:plastic"    # команда
← "ok" | "error"        # ответ
```

### Modbus RTU (/dev/ttyUSB0, 115200 baud)

**Command Register (25):**
| Bit | Назначение |
|-----|------------|
| 7 | radxa_detected_bottle |
| 6 | radxa_detected_bank |
| 5 | force_move_carriage_right |
| 4 | force_move_carriage_left |

**Status Register (26):**
| Bit | Назначение |
|-----|------------|
| 7 | bottle_exist |
| 6 | bank_exist |
| 3 | right_sensor_carriage |
| 2 | center_sensor_carriage |
| 1 | left_sensor_carriage |

## Поток данных

```
1. ПЛК детектирует объект → bottle_exist/bank_exist = 1
2. Application читает статус → отправляет "bottle_exist" в vision
3. inference_service:
   - Захватывает кадр с камеры
   - Выполняет инференс YOLO
   - Возвращает "bottle"/"bank"/"none"
4. Application:
   - Устанавливает radxa_detected_bottle/bank
   - Переходит в DUMPING_PLASTIC/DUMPING_ALUMINUM
5. ПЛК перемещает каретку влево/вправо
6. Датчик фиксирует завершение → возврат в IDLE
```

## Модель

- **Архитектура:** YOLO11n classification
- **Вход:** 1024×1024 RGB
- **Классы:** CAN (0), FOREIGN (1), PET (2)
- **Платформа:** RKNN (Rockchip NPU)
