#!/usr/bin/env python3
"""
Backend Simulator - симулятор backend сервиса для тестирования WebSocket API.

Подключается к Application как клиент "app" и позволяет:
- Отправлять команды вручную
- Получать события от автомата
- Тестировать полный цикл работы

Использование:
    python backend_simulator.py              # localhost:8765
    python backend_simulator.py --host HOST --port PORT
"""
import argparse
import asyncio
import json
from datetime import datetime
from typing import List, Optional

import websockets
from websockets.exceptions import ConnectionClosed


class BackendSimulator:
    """
    Симулятор backend сервиса для тестирования WebSocket API.

    Протокол:
        Подключение → отправка "app" (имя клиента)
        Отправка команд в формате "command:param" или JSON
        Получение событий от автомата
    """

    def __init__(self, host: str, port: int):
        """
        Инициализация симулятора.

        Args:
            host: WebSocket хост.
            port: WebSocket порт.
        """
        self.uri = f"ws://{host}:{port}"
        self.events: List[dict] = []
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False

    async def connect(self) -> bool:
        """
        Подключиться к WebSocket серверу.

        Returns:
            True если подключение успешно.
        """
        try:
            print(f"[Simulator] Подключение к {self.uri}...")
            self.ws = await websockets.connect(self.uri)
            await self.ws.send("app")
            print("[Simulator] Подключено, зарегистрирован как 'app'")
            return True
        except Exception as e:
            print(f"[Simulator] Ошибка подключения: {e}")
            return False

    async def send_command(self, command: str, params: dict = None):
        """
        Отправить команду на сервер.

        Args:
            command: Название команды.
            params: Параметры команды.
        """
        if not self.ws:
            print("[Simulator] Нет подключения")
            return

        if params:
            # JSON формат
            msg = json.dumps({"command": command, **params})
        else:
            # Строковый формат
            msg = command

        print(f"[Simulator] Отправка: {msg}")
        await self.ws.send(msg)

    async def listen_events(self, timeout: float = 0.5) -> Optional[dict]:
        """
        Получить событие от сервера.

        Args:
            timeout: Таймаут ожидания.

        Returns:
            Событие или None если таймаут.
        """
        if not self.ws:
            return None

        try:
            message = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            try:
                event = json.loads(message)
                self.events.append(event)
                return event
            except json.JSONDecodeError:
                return {"raw": message}
        except asyncio.TimeoutError:
            return None
        except ConnectionClosed:
            print("[Simulator] Соединение закрыто")
            return None

    async def listen_all_events(self, duration: float = 5.0):
        """
        Слушать все события в течение заданного времени.

        Args:
            duration: Длительность прослушивания в секундах.
        """
        print(f"[Simulator] Прослушивание событий {duration} сек...")
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < duration:
            event = await self.listen_events(timeout=0.5)
            if event:
                self._print_event(event)

    def _print_event(self, event: dict):
        """Вывести событие."""
        if "raw" in event:
            print(f"  [Событие] raw: {event['raw']}")
        else:
            event_name = event.get("event", "unknown")
            data = event.get("data", {})
            timestamp = event.get("timestamp", "")
            print(f"  [Событие] {event_name}: {data} @ {timestamp}")

    def show_event_history(self):
        """Показать историю событий."""
        print(f"\n=== История событий ({len(self.events)}) ===")
        for i, event in enumerate(self.events, 1):
            event_name = event.get("event", event.get("raw", "unknown"))
            data = event.get("data", {})
            print(f"{i}. {event_name}: {data}")
        print()

    async def close(self):
        """Закрыть соединение."""
        if self.ws:
            await self.ws.close()
            print("[Simulator] Соединение закрыто")


async def interactive_mode(simulator: BackendSimulator):
    """
    Интерактивный режим управления симулятором.

    Args:
        simulator: Экземпляр BackendSimulator.
    """
    print("\n=== Backend Simulator ===")
    print("Команды:")
    print("  1. get_device_info   - Получить информацию об устройстве")
    print("  2. get_photo         - Получить фото с камеры")
    print("  3. dump:plastic      - Выгрузить пластик")
    print("  4. dump:aluminium    - Выгрузить алюминий")
    print("  5. unload:plastic    - Мешок пластика выгружен")
    print("  6. unload:aluminium  - Мешок алюминия выгружен")
    print("  7. restore           - Восстановить устройство (из ERROR)")
    print("  8. listen            - Слушать события 5 сек")
    print("  9. history           - Показать историю событий")
    print("  0. clear_register    - Очистить регистр команд ПЛК")
    print("  q. Выход")
    print("-" * 40)

    while True:
        try:
            cmd = input("\n> ").strip().lower()

            if cmd == "q":
                break
            elif cmd == "1":
                await simulator.send_command("get_device_info")
                await asyncio.sleep(0.3)
                event = await simulator.listen_events(timeout=2.0)
                if event:
                    simulator._print_event(event)
            elif cmd == "2":
                await simulator.send_command("get_photo")
                await asyncio.sleep(0.3)
                event = await simulator.listen_events(timeout=3.0)
                if event:
                    if "photo_base64" in event.get("data", {}):
                        b64_len = len(event["data"]["photo_base64"])
                        print(f"  [Событие] photo_ready: base64 ({b64_len} символов)")
                    else:
                        simulator._print_event(event)
            elif cmd == "3":
                await simulator.send_command("dump_container:plastic")
                await simulator.listen_all_events(duration=5.0)
            elif cmd == "4":
                await simulator.send_command("dump_container:aluminium")
                await simulator.listen_all_events(duration=5.0)
            elif cmd == "5":
                await simulator.send_command("container_unloaded:plastic")
                await asyncio.sleep(0.3)
                event = await simulator.listen_events(timeout=2.0)
                if event:
                    simulator._print_event(event)
            elif cmd == "6":
                await simulator.send_command("container_unloaded:aluminium")
                await asyncio.sleep(0.3)
                event = await simulator.listen_events(timeout=2.0)
                if event:
                    simulator._print_event(event)
            elif cmd == "7":
                await simulator.send_command("restore_device")
                await asyncio.sleep(0.3)
                event = await simulator.listen_events(timeout=2.0)
                if event:
                    simulator._print_event(event)
            elif cmd == "8":
                await simulator.listen_all_events(duration=5.0)
            elif cmd == "9":
                simulator.show_event_history()
            elif cmd == "0":
                await simulator.send_command("cmd_full_clear_register")
                print("  Команда отправлена")
            else:
                # Попытка отправить как raw команду
                if cmd:
                    await simulator.send_command(cmd)
                    await asyncio.sleep(0.3)
                    event = await simulator.listen_events(timeout=2.0)
                    if event:
                        simulator._print_event(event)

        except KeyboardInterrupt:
            print("\nПрервано")
            break
        except Exception as e:
            print(f"Ошибка: {e}")


def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Backend Simulator для тестирования WebSocket API"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="WebSocket хост (по умолчанию: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="WebSocket порт (по умолчанию: 8765)"
    )
    return parser.parse_args()


async def main():
    """Точка входа."""
    args = parse_args()
    simulator = BackendSimulator(args.host, args.port)

    if await simulator.connect():
        try:
            await interactive_mode(simulator)
        finally:
            await simulator.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nВыход")
