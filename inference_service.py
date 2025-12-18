#!/usr/bin/env python3
"""
Inference Service - WebSocket клиент для классификации объектов.

Протокол:
    Подключение → отправка "vision" (имя клиента)
    Получение "bottle_exist" → выполнение инференса → отправка "bottle" или "bank"
    Получение "bank_exist" → выполнение инференса → отправка "bottle" или "bank"
    Получение "none" → отправка "none"

Использование:
    python inference_service.py              # Запуск WebSocket клиента
    python inference_service.py --camera     # Интерактивный режим камеры
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Подавляем предупреждения OpenCV
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "0")

import cv2
import websockets
from websockets.exceptions import ConnectionClosed

from camera_manager import CameraManager
from config import Settings, get_settings
from inference_engine import InferenceEngine


class InferenceClient:
    """
    WebSocket клиент для обработки запросов на инференс.

    Протокол:
        Подключение → отправка "vision" (имя клиента)
        Получение "bottle_exist" → инференс → отправка "bottle" или "bank"
        Получение "bank_exist" → инференс → отправка "bottle" или "bank"
        Получение "none" → отправка "none"
    """

    def __init__(self, settings: Settings):
        """
        Инициализация клиента.

        Args:
            settings: Настройки приложения.
        """
        self._settings = settings
        self._camera = CameraManager(settings)
        self._engine = InferenceEngine(settings)
        self._running = False
        self._websocket = None

    def initialize(self) -> bool:
        """
        Инициализация: загрузка и прогрев модели.

        Returns:
            True если инициализация успешна.
        """
        print("[InferenceClient] Инициализация...")

        if not self._engine.load_model():
            return False

        if not self._engine.warmup():
            return False

        # Создаём директорию для сохранения кадров
        if self._settings.save_frames:
            self._settings.output_dir.mkdir(parents=True, exist_ok=True)

        print("[InferenceClient] Инициализация завершена")
        return True

    async def start(self) -> None:
        """Запустить WebSocket клиент с автоматическим переподключением."""
        if not self._engine.is_ready():
            print("[InferenceClient] Ошибка: модель не готова")
            return

        uri = f"ws://{self._settings.websocket_host}:{self._settings.websocket_port}"
        self._running = True

        while self._running:
            try:
                print(f"[InferenceClient] Подключение к {uri}...")
                async with websockets.connect(uri) as websocket:
                    self._websocket = websocket
                    print("[InferenceClient] Подключено, отправка имени клиента 'vision'...")
                    
                    # Отправляем имя клиента
                    await websocket.send("vision")
                    print("[InferenceClient] Зарегистрирован как 'vision', ожидание запросов...")

                    # Открываем камеру и запускаем захват (с попыткой разных индексов)
                    if not self._camera.is_open():
                        camera_opened = False
                        camera_idx_used = None
                        # Пробуем разные индексы камеры
                        for camera_idx in range(5):  # Пробуем индексы 0-4
                            print(f"[InferenceClient] Попытка открыть камеру с индексом {camera_idx}...")
                            if self._camera.open(camera_index=camera_idx):
                                camera_opened = True
                                camera_idx_used = camera_idx
                                # Обновляем индекс в настройках для дальнейшего использования
                                self._settings.camera_index = camera_idx
                                print(f"[InferenceClient] Камера успешно открыта с индексом {camera_idx}")
                                break
                            else:
                                # Сбрасываем состояние камеры перед следующей попыткой
                                self._camera.close()
                        
                        if not camera_opened:
                            print("[InferenceClient] Ошибка: не удалось открыть камеру ни с одним индексом (0-4)")
                            await asyncio.sleep(self._settings.websocket_reconnect_delay)
                            continue

                    if not self._camera.start_capture():
                        print("[InferenceClient] Ошибка: не удалось запустить захват кадров")
                        self._camera.close()
                        await asyncio.sleep(self._settings.websocket_reconnect_delay)
                        continue

                    print("[InferenceClient] Камера открыта, захват запущен")

                    # Основной цикл обработки сообщений
                    while self._running:
                        try:
                            # Получаем сообщение от сервера
                            message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=1.0
                            )
                            
                            response = await self._handle_message(message)
                            if response:
                                await websocket.send(response)

                        except asyncio.TimeoutError:
                            # Таймаут - это нормально, продолжаем слушать
                            continue
                        except ConnectionClosed:
                            print("[InferenceClient] Соединение закрыто сервером")
                            break
                        except Exception as e:
                            print(f"[InferenceClient] Ошибка обработки сообщения: {e}")
                            continue

            except ConnectionRefusedError:
                print(f"[InferenceClient] Не удалось подключиться к {uri}, повтор через {self._settings.websocket_reconnect_delay} сек...")
            except Exception as e:
                print(f"[InferenceClient] Ошибка подключения: {e}")
            
            # Закрываем камеру при разрыве соединения
            self._camera.stop_capture()
            self._camera.close()

            if self._running:
                await asyncio.sleep(self._settings.websocket_reconnect_delay)

        self._cleanup()

    def stop(self) -> None:
        """Остановить клиент."""
        self._running = False

    async def _handle_message(self, message: str) -> Optional[str]:
        """
        Обработка сообщения от сервера.

        Args:
            message: Сообщение от сервера ("bottle_exist", "bank_exist", "none").

        Returns:
            Ответ клиенту ("bottle", "bank", "none") или None если ответ не требуется.
        """
        message = message.strip().lower()
        print(f"[InferenceClient] Получено сообщение: {message}")

        if message == "none":
            return "none"

        if message in ("bottle_exist", "bank_exist"):
            # Выполняем инференс
            if not self._camera.is_open():
                print("[InferenceClient] Камера не открыта")
                return "none"

            # Получаем последний кадр
            frame = self._camera.get_frame()
            if frame is None:
                # Пробуем захватить напрямую
                frame = self._camera.capture_single_frame()
                if frame is None:
                    print("[InferenceClient] Не удалось получить кадр")
                    return "none"

            # Сохраняем кадр если нужно
            if self._settings.save_frames:
                self._save_frame(frame)

            # Выполняем инференс
            class_name, confidence = self._engine.predict(frame)
            
            # Маппим результат модели на формат ответа:
            # PET (пластиковая бутылка) -> "bottle"
            # CAN (алюминиевая банка) -> "bank"
            # FOREIGN/NONE -> "none"
            if class_name == "PET":
                result = "bottle"
            elif class_name == "CAN":
                result = "bank"
            else:
                result = "none"
            
            print(f"[InferenceClient] Результат инференса: {class_name} ({confidence:.3f}) -> {result}")
            return result

        print(f"[InferenceClient] Неизвестное сообщение: {message}")
        return None

    def _save_frame(self, frame) -> None:
        """Сохранить кадр на диск."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = self._settings.output_dir / f"{timestamp}.jpg"
            cv2.imwrite(str(filename), frame)
        except Exception as e:
            print(f"[InferenceClient] Ошибка сохранения кадра: {e}")

    def _cleanup(self) -> None:
        """Освободить ресурсы."""
        self._camera.stop_capture()
        self._camera.close()
        print("[InferenceClient] Остановлен")


def run_interactive_camera(settings: Settings) -> None:
    """
    Интерактивный режим камеры для тестирования.

    Args:
        settings: Настройки приложения.
    """
    engine = InferenceEngine(settings)
    camera = CameraManager(settings)

    if not engine.load_model():
        print("Не удалось загрузить модель")
        return

    if not engine.warmup():
        print("Не удалось прогреть модель")
        return

    if not camera.open():
        print("Не удалось открыть камеру")
        return

    settings.output_dir.mkdir(parents=True, exist_ok=True)

    print("\nИнтерактивный режим камеры")
    print("Команды: c - захват и инференс, q - выход")
    print("-" * 40)

    try:
        while True:
            cmd = input("\n> ").strip().lower()

            if cmd == "q":
                break
            elif cmd == "c":
                frame = camera.capture_single_frame()
                if frame is None:
                    print("Не удалось захватить кадр")
                    continue

                class_name, confidence = engine.predict(frame)
                print(f"Результат: {class_name} ({confidence:.3f})")

                # Сохраняем кадр
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = settings.output_dir / f"{timestamp}_{class_name}.jpg"
                cv2.imwrite(str(filename), frame)
                print(f"Сохранено: {filename}")
            else:
                print("Неизвестная команда. Используйте 'c' или 'q'")

    except KeyboardInterrupt:
        print("\nПрервано")
    finally:
        camera.close()


def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Inference Service для классификации объектов"
    )
    parser.add_argument(
        "--camera",
        action="store_true",
        help="Интерактивный режим камеры (для тестирования)"
    )
    parser.add_argument(
        "--host",
        type=str,
        help="WebSocket хост (переопределяет .env)"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="WebSocket порт (переопределяет .env)"
    )
    return parser.parse_args()


def main():
    """Точка входа."""
    args = parse_args()
    settings = get_settings()

    # Переопределяем настройки если указаны
    if args.host:
        settings.websocket_host = args.host
    if args.port:
        settings.websocket_port = args.port

    if args.camera:
        run_interactive_camera(settings)
    else:
        client = InferenceClient(settings)
        if client.initialize():
            try:
                asyncio.run(client.start())
            except KeyboardInterrupt:
                print("\nПрервано пользователем")
                client.stop()


if __name__ == "__main__":
    main()
