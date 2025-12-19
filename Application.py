import time
import json
from datetime import datetime
from PLC import PLC
import threading
import signal
import sys
from WebSocket import WebSocket
from enum import Enum
from logging_config import get_logger, setup_logging

# Инициализация логирования
setup_logging()
logger = get_logger(__name__)


class AppState(Enum):
    """Состояния конечного автомата приложения."""
    IDLE = "idle"
    WAITING_VISION = "waiting_vision"
    DUMPING_PLASTIC = "dumping_plastic"
    DUMPING_ALUMINUM = "dumping_aluminum"
    ERROR = "error"

class Application:
    def __init__(self, serial_port, baudrate, slave_address, cmd_register = 25, status_register = 26, update_data_period = 0.1, web_socket_port = 8080, web_socket_host = 'localhost', speed = 500):
        self.PLC = None
        self.websocket_server = None
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.slave_address = slave_address
        self.cmd_register = cmd_register
        self.status_register = status_register
        self.update_data_period = update_data_period
        self.web_socket_port = web_socket_port
        self.web_socket_host = web_socket_host
        self.running = True
        self.speed = speed
        self.flag = False

        self.thread_websocket = None
        self.thread_terminal = None
        self.thread_update_data = None
        
        # State Machine
        self.state = AppState.IDLE
        self.state_lock = threading.Lock()  # Lock для потокобезопасности

        # Таймауты (секунды)
        self.vision_timeout = 2.0           # Таймаут ответа от vision
        self.dump_timeout = 3.0             # Таймаут движения каретки

        # Временные данные для state machine
        self.current_plc_detection = None   # "bottle" или "bank" - что детектировал ПЛК
        self.vision_request_time = None     # Время отправки запроса к vision
        self.dump_started_time = None       # Время начала сброса каретки

        # Отслеживание завесы
        self.prev_veil_state = 0            # Предыдущее состояние завесы
        self.veil_just_cleared = False      # Флаг: завеса только что освободилась

        # Command Registry: команда → (handler, требует_param)
        self._command_handlers = {
            "get_photo": (self.handle_get_photo, False),
            "get_device_info": (self.handle_get_device_info, False),
            "dump_container": (self.handle_container_dump, True),
            "container_unloaded": (self.handle_container_unloaded, True),
            # Заглушки
            "enter_service_mode": (self.handle_stub_command, False),
            "exit_service_mode": (self.handle_stub_command, False),
            "restore_device": (self.handle_stub_command, False),
            "unlock_door": (self.handle_stub_command, False),
            "lock_door": (self.handle_stub_command, False),
            "open_shutter": (self.handle_stub_command, False),
            "reboot_device": (self.handle_stub_command, False),
            # Служебные команды PLC
            "cmd_full_clear_register": (lambda: self.PLC.cmd_full_clear_register(), False),
            "cmd_force_move_carriage_left": (lambda: self.PLC.cmd_force_move_carriage_left(), False),
            "cmd_force_move_carriage_right": (lambda: self.PLC.cmd_force_move_carriage_right(), False),
            "cmd_weight_error_reset": (lambda: self.PLC.cmd_weight_error_reset(), False),
            "cmd_reset_weight_reading": (lambda: self.PLC.cmd_reset_weight_reading(), False),
        }

        # Конфигурация состояний DUMPING для унификации
        self._dumping_config = {
            AppState.DUMPING_PLASTIC: {
                "sensor_getter": lambda: self.PLC.get_state_left_sensor_carriage(),
                "type": "PET",
                "counter_getter": lambda: self.PLC.get_bottle_count(),
                "error_code": "carriage_left_timeout",
                "error_message": "Таймаут движения каретки влево",
                "direction": "влево",
            },
            AppState.DUMPING_ALUMINUM: {
                "sensor_getter": lambda: self.PLC.get_state_right_sensor_carriage(),
                "type": "ALUMINUM",
                "counter_getter": lambda: self.PLC.get_bank_count(),
                "error_code": "carriage_right_timeout",
                "error_message": "Таймаут движения каретки вправо",
                "direction": "вправо",
            },
        }

    def signal_handler(self, sig, frame):
        self.running = False
        sys.exit(0)

    def start_threads(self):
        self.thread_update_data = threading.Thread(target=self.PLC_update_data)
        self.thread_update_data.start()

        # self.websocket_server = WebSocket(self.PLC, self.web_socket_host, self.web_socket_port)
        self.websocket_server.start()

    def stop(self):
        if self.thread_update_data and self.thread_update_data.is_alive():
            self.thread_update_data.join()
        if self.PLC:
            self.PLC.stop()
        if self.websocket_server:
            self.websocket_server.stop()
        logger.info("Application stopped")


    def PLC_update_data(self):
        """Поток непрерывного опроса данных ПЛК."""
        try:
            while self.running:
                self.PLC.update_data()
                time.sleep(self.update_data_period)
        except Exception as e:
            logger.error(f"Ошибка обновления данных PLC: {e}")


    def setup(self):
        try:
            self.PLC = PLC(self.serial_port, self.baudrate, self.slave_address, self.cmd_register, self.status_register, self.speed)
            self.websocket_server = WebSocket(self.PLC, self.web_socket_host, self.web_socket_port)
            time.sleep(1) 
            self.start_threads()

        except Exception as e:
            logger.error(f"Ошибка инициализации: {e}")
            return False
        return True



    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        try:
            while self.running:
                # ОБРАБОТКА СОСТОЯНИЙ STATE MACHINE
                if self.state in (AppState.DUMPING_PLASTIC, AppState.DUMPING_ALUMINUM):
                    self._handle_dumping_state(self.state)

                elif self.state == AppState.WAITING_VISION:
                    # Ждем ответа от vision (одноразовое чтение)
                    vision_response = self.websocket_server.get_command("vision")

                    if vision_response and vision_response != "":
                        # Получен ответ от vision
                        logger.info(f"Vision ответил: {vision_response}")
                        self._handle_vision_response_with_events(vision_response)
                        with self.state_lock:
                            self.state = AppState.IDLE
                        self.vision_request_time = None
                        self.current_plc_detection = None
                    elif time.time() - self.vision_request_time > self.vision_timeout:
                        # Таймаут ожидания vision
                        logger.warning("ТАЙМАУТ ожидания vision → IDLE")
                        with self.state_lock:
                            self.state = AppState.IDLE
                        self.vision_request_time = None
                        self.current_plc_detection = None
                        # Событие: контейнер не распознан
                        self.send_event_to_app("container_not_recognized", {})

                elif self.state == AppState.ERROR:
                    # В состоянии ошибки принимаем команды, но обрабатываем только некоторые
                    self._handle_error_state_commands()

                # ОБРАБОТКА КОМАНД ТОЛЬКО В СОСТОЯНИИ IDLE
                elif self.state == AppState.IDLE:

                    # Отслеживание завесы
                    current_veil = self.PLC.get_state_veil()
                    bottle_exist = self.PLC.get_bottle_exist()
                    bank_exist = self.PLC.get_bank_exist()
                    container_detected = bottle_exist == 1 or bank_exist == 1

                    # Детект перехода завесы: пересечена → свободна (рука убрана)
                    if self.prev_veil_state == 1 and current_veil == 0:
                        self.veil_just_cleared = True
                        logger.debug("Завеса освободилась, ждём контейнер...")

                    # Сброс флага если завеса снова пересечена
                    if current_veil == 1:
                        self.veil_just_cleared = False

                    # Запуск инференса: завеса была освобождена + контейнер появился
                    if self.veil_just_cleared and container_detected:
                        # Определяем тип контейнера по ПЛК
                        if self.PLC.get_bottle_exist() == 1:
                            self.current_plc_detection = "bottle"
                            vision_cmd = "bottle_exist"
                        else:
                            self.current_plc_detection = "bank"
                            vision_cmd = "bank_exist"

                        logger.info(f"Контейнер обнаружен → WAITING_VISION ({self.current_plc_detection})")
                        self.vision_request_time = time.time()
                        self.veil_just_cleared = False  # Сброс флага

                        # Событие: контейнер обнаружен
                        self.send_event_to_app("container_detected", {"plc_type": self.current_plc_detection})
                        # Сброс старых ответов vision перед новым запросом
                        self.websocket_server.get_command("vision")
                        self.websocket_server.send_to_client("vision", vision_cmd)
                        with self.state_lock:
                            self.state = AppState.WAITING_VISION

                    self.prev_veil_state = current_veil

                    # Обработка команд от app через command registry
                    app_message = self.websocket_server.get_command("app")
                    if app_message:
                        app_command, params = self.parse_command(app_message)
                        if app_command:
                            self._dispatch_command(app_command, params)

                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Ошибка в главном цикле: {e}")

    # === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СОБЫТИЙ И КОМАНД ===

    def _handle_dumping_state(self, state: AppState) -> None:
        """
        Унифицированная обработка состояний DUMPING_PLASTIC/DUMPING_ALUMINUM.

        Args:
            state: Текущее состояние (DUMPING_PLASTIC или DUMPING_ALUMINUM).
        """
        config = self._dumping_config[state]

        if config["sensor_getter"]() == 1:
            logger.info(f"Датчик {config['direction']} достигнут, обнуляем регистры")
            self.PLC.cmd_full_clear_register()
            with self.state_lock:
                self.state = AppState.IDLE
            self.dump_started_time = None
            self.send_event_to_app("container_accepted", {
                "type": config["type"],
                "counter": config["counter_getter"]()
            })
        elif time.time() - self.dump_started_time > self.dump_timeout:
            logger.warning(f"ТАЙМАУТ при движении {config['direction']}! → ERROR")
            self.PLC.cmd_full_clear_register()
            with self.state_lock:
                self.state = AppState.ERROR
            self.dump_started_time = None
            self.send_event_to_app("hardware_error", {
                "error_code": config["error_code"],
                "message": config["error_message"]
            })

    def _dispatch_command(self, command: str, params: dict) -> bool:
        """
        Диспетчер команд через command registry.

        Args:
            command: Название команды.
            params: Параметры команды.

        Returns:
            True если команда обработана, False если неизвестная команда.
        """
        if command not in self._command_handlers:
            return False

        handler, requires_param = self._command_handlers[command]

        # Заглушки получают название команды
        if handler == self.handle_stub_command:
            handler(command)
        elif requires_param:
            handler(params.get("param"))
        else:
            handler()

        return True

    def create_event(self, event_name: str, data: dict = None) -> str:
        """
        Создать JSON событие для отправки клиенту app.

        Args:
            event_name: Название события.
            data: Данные события (опционально).

        Returns:
            JSON строка с событием.
        """
        return json.dumps({
            "event": event_name,
            "data": data or {},
            "timestamp": datetime.now().isoformat()
        })

    def send_event_to_app(self, event_name: str, data: dict = None):
        """
        Отправить событие клиенту app.

        Args:
            event_name: Название события.
            data: Данные события (опционально).
        """
        event = self.create_event(event_name, data)
        self.websocket_server.send_to_client("app", event)
        logger.debug(f"Event → app: {event_name}: {data}")

    def parse_command(self, message: str) -> tuple:
        """
        Парсить команду от клиента (JSON или строка).

        Поддерживает форматы:
        - JSON: {"command": "name", "param": "value"}
        - JSON: {"command": "name", "container_type": "plastic"}
        - JSON: {"command": "name", "type": "plastic"}
        - Строка с параметром: "command:param"
        - Простая строка: "command"

        Args:
            message: Сообщение от клиента.

        Returns:
            Tuple (command_name, params_dict).
        """
        if not message:
            return None, {}

        # Попытка парсинга JSON
        try:
            data = json.loads(message)
            command = data.get("command")
            # Нормализация: поддержка альтернативных ключей для параметра
            if "container_type" in data and "param" not in data:
                data["param"] = data["container_type"]
            elif "type" in data and "param" not in data:
                data["param"] = data["type"]
            return command, data
        except json.JSONDecodeError:
            pass

        # Fallback к строковому формату
        if ":" in message:
            cmd, param = message.split(":", 1)
            return cmd, {"param": param}

        return message, {}

    # === ОБРАБОТЧИКИ КОМАНД ОТ APP ===

    def handle_get_device_info(self):
        """
        Обработчик команды get_device_info.

        Собирает информацию с ПЛК и отправляет событие device_info.
        """
        device_info = {
            "bottle_count": self.PLC.get_bottle_count(),
            "bank_count": self.PLC.get_bank_count(),
            "bottle_fill_percent": self.PLC.get_bottle_fill_percent(),
            "bank_fill_percent": self.PLC.get_bank_fill_percent(),
            "state": self.state.value,
            "left_sensor": self.PLC.get_state_left_sensor_carriage(),
            "center_sensor": self.PLC.get_state_center_sensor_carriage(),
            "right_sensor": self.PLC.get_state_right_sensor_carriage(),
            "weight_error": self.PLC.get_state_weight_error(),
        }
        self.send_event_to_app("device_info", device_info)

    def handle_get_photo(self):
        """
        Обработчик команды get_photo.

        Запрашивает фото у vision сервиса и пересылает клиенту app.
        Если vision недоступен, возвращает ошибку.
        """
        # Сброс старых ответов и отправка команды get_photo в vision
        self.websocket_server.get_command("vision")
        self.websocket_server.send_to_client("vision", '{"command": "get_photo"}')

        # Ждём ответа с таймаутом (одноразовое чтение)
        start_time = time.time()
        while time.time() - start_time < 2.0:
            response = self.websocket_server.get_command("vision")
            if not response:
                time.sleep(0.1)
                continue
            if response.startswith("{"):
                try:
                    data = json.loads(response)
                    if "photo_base64" in data:
                        self.send_event_to_app("photo_ready", data)
                        return
                    elif "error" in data:
                        self.send_event_to_app("photo_ready", {"error": data["error"]})
                        return
                except json.JSONDecodeError:
                    pass
            time.sleep(0.1)

        # Таймаут - vision недоступен
        self.send_event_to_app("photo_ready", {"error": "vision_unavailable"})

    def handle_container_dump(self, container_type: str):
        """
        Обработчик команды container_dump.

        Args:
            container_type: Тип контейнера ("plastic" или "aluminium").
        """
        if container_type == "plastic":
            logger.info("Команда: сброс пластика (влево)")
            with self.state_lock:
                self.state = AppState.DUMPING_PLASTIC
            self.dump_started_time = time.time()
            self.PLC.cmd_force_move_carriage_left()
            self.send_event_to_app("container_dumped", {"container_type": "plastic"})
        elif container_type == "aluminium":
            logger.info("Команда: сброс алюминия (вправо)")
            with self.state_lock:
                self.state = AppState.DUMPING_ALUMINUM
            self.dump_started_time = time.time()
            self.PLC.cmd_force_move_carriage_right()
            self.send_event_to_app("container_dumped", {"container_type": "aluminium"})
        else:
            logger.warning(f"Неизвестный тип контейнера: {container_type}")

    def handle_container_unloaded(self, container_type: str):
        """
        Обработчик команды container_unloaded (мешок выгружен).

        Args:
            container_type: Тип контейнера ("plastic" или "aluminium").
        """
        if container_type == "plastic":
            logger.info("Мешок пластика выгружен, сброс счетчика")
            self.PLC.cmd_reset_bottle_counters()
        elif container_type == "aluminium":
            logger.info("Мешок алюминия выгружен, сброс счетчика")
            self.PLC.cmd_reset_bank_counters()
        self.send_event_to_app("container_unloaded_ack", {"container_type": container_type})

    def handle_stub_command(self, command_name: str):
        """
        Заглушка для команд, которые пока не реализованы.

        Args:
            command_name: Название команды.
        """
        logger.debug(f"Заглушка команды: {command_name}")
        self.send_event_to_app(f"{command_name}_ack", {"status": "not_implemented"})

    # === ОБРАБОТЧИКИ VISION И ERROR ===

    def _handle_vision_response(self, vision_response: str):
        """
        Обработка ответа от vision сервиса (без событий, для тестов).

        Args:
            vision_response: Ответ vision ("bottle", "bank", "none").
        """
        if vision_response == "none":
            logger.info("Vision: контейнер не распознан")
            return

        # Проверяем совпадение с детектом ПЛК
        if self.current_plc_detection == "bottle" and vision_response == "bottle":
            logger.info("Vision: подтверждено бутылка → PLC cmd")
            self.PLC.cmd_radxa_detected_bottle()
        elif self.current_plc_detection == "bank" and vision_response == "bank":
            logger.info("Vision: подтверждено банка → PLC cmd")
            self.PLC.cmd_radxa_detected_bank()
        else:
            logger.warning(f"Vision: несовпадение! ПЛК: {self.current_plc_detection}, Vision: {vision_response}")

    def _handle_vision_response_with_events(self, vision_response: str):
        """
        Обработка ответа от vision сервиса с отправкой событий.

        Args:
            vision_response: Ответ vision ("bottle", "bank", "none").
        """
        if vision_response == "none":
            logger.info("Vision: контейнер не распознан")
            # Событие: контейнер не распознан
            self.send_event_to_app("container_not_recognized", {})
            return

        # Проверяем совпадение с детектом ПЛК
        if self.current_plc_detection == "bottle" and vision_response == "bottle":
            logger.info("Vision: подтверждено бутылка → PLC cmd")
            self.PLC.cmd_radxa_detected_bottle()
            # Событие: контейнер распознан
            self.send_event_to_app("container_recognized", {
                "type": "PET",
                "confidence": 1.0  # TODO: получать от vision
            })
        elif self.current_plc_detection == "bank" and vision_response == "bank":
            logger.info("Vision: подтверждено банка → PLC cmd")
            self.PLC.cmd_radxa_detected_bank()
            # Событие: контейнер распознан
            self.send_event_to_app("container_recognized", {
                "type": "ALUMINUM",
                "confidence": 1.0  # TODO: получать от vision
            })
        else:
            logger.warning(f"Vision: несовпадение! ПЛК: {self.current_plc_detection}, Vision: {vision_response}")
            # Событие: несовпадение детекта
            self.send_event_to_app("receiver_not_empty", {
                "plc_type": self.current_plc_detection,
                "vision_type": vision_response
            })

    def _handle_error_state_commands(self):
        """
        Обработка команд в состоянии ERROR.

        Принимает все команды, но обрабатывает только:
        - get_photo
        - get_device_info
        - dump_container
        - restore_device
        """
        app_message = self.websocket_server.get_command("app")
        app_command, params = self.parse_command(app_message)
        if not app_command:
            return

        app_param = params.get("param")

        # В ERROR обрабатываем только эти команды
        if app_command == "get_photo":
            self.handle_get_photo()
        elif app_command == "get_device_info":
            self.handle_get_device_info()
        elif app_command == "dump_container":
            self.handle_container_dump(app_param)
        elif app_command == "restore_device":
            logger.info("ERROR State: восстановление устройства → IDLE")
            with self.state_lock:
                self.state = AppState.IDLE
            self.send_event_to_app("restore_device_ack", {"status": "ok"})
        else:
            logger.debug(f"ERROR State: команда {app_command} игнорируется")


if __name__ == "__main__":
    app = Application(
        serial_port='/dev/ttyUSB0',
        baudrate=115200,
        slave_address=2,
        cmd_register=25,
        status_register=26,
        update_data_period=0.1,
        web_socket_port=8765,
        web_socket_host='localhost',
        speed=500

    )
    app.setup()
    app.run()
    app.stop()


    