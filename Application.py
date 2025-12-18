import time
import json
from datetime import datetime
from PLC import PLC
import threading
import signal
import sys
from WebSocket import WebSocket
from enum import Enum


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
        print("Application stopped")


    def PLC_update_data(self):
        try:
            while self.running:
                # print(self.PLC.status_register.get_value())
                self.PLC.update_data()
                # print(self.PLC.get_state_veil(), self.PLC.get_state_left_sensor_carriage(), \
                # self.PLC.get_state_center_sensor_carriage(), self.PLC.get_state_right_sensor_carriage(), \
                # self.PLC.get_state_unknown_sensor_carriage(), self.PLC.get_state_weight_error(), \
                # self.PLC.get_bank_exist(), self.PLC.get_bottle_exist(), self.PLC.get_weight_too_small(), \
                # self.PLC.get_bottle_weight_ok(), self.PLC.get_bank_weight_ok(), self.PLC.get_status_work(), \
                # self.PLC.get_left_movement_error(), self.PLC.get_right_movement_error())
                # time.sleep(self.update_data_period)
                message = ""
                if (self.PLC.get_bank_exist() == 1):
                    message += "bank "
                if (self.PLC.get_bottle_exist() == 1):
                    message += "bottle "
                if (self.PLC.get_weight_too_small() == 1):
                    message += "weight_too_small "
                if (self.PLC.get_bottle_weight_ok() == 1):
                    message += "bottle_weight_ok "
                if (self.PLC.get_bank_weight_ok() == 1):
                    message += "bank_weight_ok "
                # 
                
                # print(message)
                # print(self.PLC.modbus_register_counter.get_value())
                # print(self.PLC.get_state_left_sensor_carriage())
                # print(self.state)
                # print("AAA")

                time.sleep(self.update_data_period)

        except Exception as e:
            pass


    def setup(self):
        try:
            self.PLC = PLC(self.serial_port, self.baudrate, self.slave_address, self.cmd_register, self.status_register, self.speed)
            self.websocket_server = WebSocket(self.PLC, self.web_socket_host, self.web_socket_port)
            time.sleep(1) 
            self.start_threads()

        except Exception as e:
            print(f"Error: {e}")
            return False
        return True



    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        try:
            while self.running:
                # print("Vision flag is True", flush=True)

                # if self.PLC.get_vision_flag() == 1 and self.flag == False:
                #     self.flag = True
                # else:
                #     self.flag = False

                # if self.flag == True:
                    
                #     self.websocket_server.send_to_client("vision", "bottle_exist")
                #     vision_response = self.websocket_server.get_state("vision")
                #     if vision_response == "bottle":
                #         self.PLC.cmd_radxa_detected_bottle()
                #     elif vision_response == "bank":
                #         self.PLC.cmd_radxa_detected_bank()
                
                # ОБРАБОТКА СОСТОЯНИЙ STATE MACHINE
                if self.state == AppState.DUMPING_PLASTIC:
                    # Ждем достижения левого датчика
                    if self.PLC.get_state_left_sensor_carriage() == 1:
                        print("[State] Левый датчик достигнут, обнуляем регистры", flush=True)
                        self.PLC.cmd_full_clear_register()
                        with self.state_lock:
                            self.state = AppState.IDLE
                        self.dump_started_time = None
                        # Событие: контейнер принят
                        self.send_event_to_app("container_accepted", {
                            "type": "PET",
                            "counter": self.PLC.get_bottle_count()
                        })
                    elif time.time() - self.dump_started_time > self.dump_timeout:
                        print("[State] ТАЙМАУТ при движении влево! → ERROR", flush=True)
                        self.PLC.cmd_full_clear_register()
                        with self.state_lock:
                            self.state = AppState.ERROR
                        self.dump_started_time = None
                        # Событие: аппаратная ошибка
                        self.send_event_to_app("hardware_error", {
                            "error_code": "carriage_left_timeout",
                            "message": "Таймаут движения каретки влево"
                        })

                elif self.state == AppState.DUMPING_ALUMINUM:
                    # Ждем достижения правого датчика
                    if self.PLC.get_state_right_sensor_carriage() == 1:
                        print("[State] Правый датчик достигнут, обнуляем регистры", flush=True)
                        self.PLC.cmd_full_clear_register()
                        with self.state_lock:
                            self.state = AppState.IDLE
                        self.dump_started_time = None
                        # Событие: контейнер принят
                        self.send_event_to_app("container_accepted", {
                            "type": "ALUMINUM",
                            "counter": self.PLC.get_bank_count()
                        })
                    elif time.time() - self.dump_started_time > self.dump_timeout:
                        print("[State] ТАЙМАУТ при движении вправо! → ERROR", flush=True)
                        self.PLC.cmd_full_clear_register()
                        with self.state_lock:
                            self.state = AppState.ERROR
                        self.dump_started_time = None
                        # Событие: аппаратная ошибка
                        self.send_event_to_app("hardware_error", {
                            "error_code": "carriage_right_timeout",
                            "message": "Таймаут движения каретки вправо"
                        })

                elif self.state == AppState.WAITING_VISION:
                    # Ждем ответа от vision (одноразовое чтение)
                    vision_response = self.websocket_server.get_command("vision")

                    if vision_response and vision_response != "":
                        # Получен ответ от vision
                        print(f"[State] Vision ответил: {vision_response}", flush=True)
                        self._handle_vision_response_with_events(vision_response)
                        with self.state_lock:
                            self.state = AppState.IDLE
                        self.vision_request_time = None
                        self.current_plc_detection = None
                    elif time.time() - self.vision_request_time > self.vision_timeout:
                        # Таймаут ожидания vision
                        print("[State] ТАЙМАУТ ожидания vision → IDLE", flush=True)
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

                    # DEBUG: показать состояние датчиков
                    print(f"[DEBUG] veil={current_veil} prev={self.prev_veil_state} bottle={bottle_exist} bank={bank_exist} cleared={self.veil_just_cleared}", flush=True)

                    # Детект перехода завесы: пересечена → свободна (рука убрана)
                    if self.prev_veil_state == 1 and current_veil == 0:
                        self.veil_just_cleared = True
                        print("[Veil] Завеса освободилась, ждём контейнер...", flush=True)

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

                        print(f"[Veil] Контейнер обнаружен → WAITING_VISION ({self.current_plc_detection})", flush=True)
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

                    # Обработка команд от app
                    app_message = self.websocket_server.get_command("app")
                    app_command, params = self.parse_command(app_message)
                    app_param = params.get("param")

                    if app_command == "get_photo":
                        self.handle_get_photo()
                    elif app_command == "get_device_info":
                        self.handle_get_device_info()
                    elif app_command == "dump_container":
                        self.handle_container_dump(app_param)
                    elif app_command == "container_unloaded":
                        self.handle_container_unloaded(app_param)

                    # Заглушки для будущих команд
                    elif app_command in ("enter_service_mode", "exit_service_mode",
                                         "restore_device", "unlock_door", "lock_door",
                                         "open_shutter", "reboot_device"):
                        self.handle_stub_command(app_command)

                    # Служебные команды (работают только в IDLE)
                    elif app_command == "cmd_full_clear_register":
                        self.PLC.cmd_full_clear_register()
                    elif app_command == "cmd_force_move_carriage_left":
                        self.PLC.cmd_force_move_carriage_left()
                    elif app_command == "cmd_force_move_carriage_right":
                        self.PLC.cmd_force_move_carriage_right()
                    elif app_command == "cmd_weight_error_reset":
                        self.PLC.cmd_weight_error_reset()
                    elif app_command == "cmd_reset_weight_reading":
                        self.PLC.cmd_reset_weight_reading()

                # if self.websocket_server.request == "cmd_lock_and_block_carriage":
                #     self.PLC.cmd_lock_and_block_carriage()
                # elif self.websocket_server.request == "cmd_weight_error_reset":
                #     self.PLC.cmd_weight_error_reset()
                # elif self.websocket_server.request == "cmd_reset_bank_counters":
                #     self.PLC.cmd_reset_bank_counters()
                # elif self.websocket_server.request == "cmd_reset_bottle_counters":
                #     self.PLC.cmd_reset_bottle_counters()
                # elif self.websocket_server.request == "cmd_force_move_carriage_left":
                #     self.PLC.cmd_force_move_carriage_left()
                # elif self.websocket_server.request == "cmd_force_move_carriage_right":
                #     self.PLC.cmd_force_move_carriage_right()
                # elif self.websocket_server.request == "cmd_radxa_detected_bank":
                #     self.PLC.cmd_radxa_detected_bank()
                # elif self.websocket_server.request == "cmd_radxa_detected_bottle":
                #     self.PLC.cmd_radxa_detected_bottle()
                # elif self.websocket_server.request == "cmd_reset_weight_reading":
                #     self.PLC.cmd_reset_weight_reading()
                # elif self.websocket_server.request == "cmd_full_clear_register":
                #     self.PLC.cmd_full_clear_register()

                
                # print(self.PLC.status_register.get_value())
                time.sleep(0.1)
                
        except Exception as e:
            pass

    # === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СОБЫТИЙ И КОМАНД ===

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
        print(f"[App→] {event_name}: {data}", flush=True)

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
            print("[App] Команда: сброс пластика (влево)", flush=True)
            with self.state_lock:
                self.state = AppState.DUMPING_PLASTIC
            self.dump_started_time = time.time()
            self.PLC.cmd_force_move_carriage_left()
            self.send_event_to_app("container_dumped", {"container_type": "plastic"})
        elif container_type == "aluminium":
            print("[App] Команда: сброс алюминия (вправо)", flush=True)
            with self.state_lock:
                self.state = AppState.DUMPING_ALUMINUM
            self.dump_started_time = time.time()
            self.PLC.cmd_force_move_carriage_right()
            self.send_event_to_app("container_dumped", {"container_type": "aluminium"})
        else:
            print(f"[App] Неизвестный тип контейнера: {container_type}", flush=True)

    def handle_container_unloaded(self, container_type: str):
        """
        Обработчик команды container_unloaded (мешок выгружен).

        Args:
            container_type: Тип контейнера ("plastic" или "aluminium").
        """
        if container_type == "plastic":
            print("[App] Мешок пластика выгружен, сброс счетчика", flush=True)
            self.PLC.cmd_reset_bottle_counters()
        elif container_type == "aluminium":
            print("[App] Мешок алюминия выгружен, сброс счетчика", flush=True)
            self.PLC.cmd_reset_bank_counters()
        self.send_event_to_app("container_unloaded_ack", {"container_type": container_type})

    def handle_stub_command(self, command_name: str):
        """
        Заглушка для команд, которые пока не реализованы.

        Args:
            command_name: Название команды.
        """
        print(f"[App] Заглушка команды: {command_name}", flush=True)
        self.send_event_to_app(f"{command_name}_ack", {"status": "not_implemented"})

    # === ОБРАБОТЧИКИ VISION И ERROR ===

    def _handle_vision_response(self, vision_response: str):
        """
        Обработка ответа от vision сервиса (без событий, для тестов).

        Args:
            vision_response: Ответ vision ("bottle", "bank", "none").
        """
        if vision_response == "none":
            print("[Vision] Контейнер не распознан", flush=True)
            return

        # Проверяем совпадение с детектом ПЛК
        if self.current_plc_detection == "bottle" and vision_response == "bottle":
            print("[Vision] Подтверждено: бутылка → PLC cmd", flush=True)
            self.PLC.cmd_radxa_detected_bottle()
        elif self.current_plc_detection == "bank" and vision_response == "bank":
            print("[Vision] Подтверждено: банка → PLC cmd", flush=True)
            self.PLC.cmd_radxa_detected_bank()
        else:
            print(f"[Vision] Несовпадение! ПЛК: {self.current_plc_detection}, Vision: {vision_response}", flush=True)

    def _handle_vision_response_with_events(self, vision_response: str):
        """
        Обработка ответа от vision сервиса с отправкой событий.

        Args:
            vision_response: Ответ vision ("bottle", "bank", "none").
        """
        if vision_response == "none":
            print("[Vision] Контейнер не распознан", flush=True)
            # Событие: контейнер не распознан
            self.send_event_to_app("container_not_recognized", {})
            return

        # Проверяем совпадение с детектом ПЛК
        if self.current_plc_detection == "bottle" and vision_response == "bottle":
            print("[Vision] Подтверждено: бутылка → PLC cmd", flush=True)
            self.PLC.cmd_radxa_detected_bottle()
            # Событие: контейнер распознан
            self.send_event_to_app("container_recognized", {
                "type": "PET",
                "confidence": 1.0  # TODO: получать от vision
            })
        elif self.current_plc_detection == "bank" and vision_response == "bank":
            print("[Vision] Подтверждено: банка → PLC cmd", flush=True)
            self.PLC.cmd_radxa_detected_bank()
            # Событие: контейнер распознан
            self.send_event_to_app("container_recognized", {
                "type": "ALUMINUM",
                "confidence": 1.0  # TODO: получать от vision
            })
        else:
            print(f"[Vision] Несовпадение! ПЛК: {self.current_plc_detection}, Vision: {vision_response}", flush=True)
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
            print("[ERROR State] Восстановление устройства → IDLE", flush=True)
            with self.state_lock:
                self.state = AppState.IDLE
            self.send_event_to_app("restore_device_ack", {"status": "ok"})
        else:
            print(f"[ERROR State] Команда {app_command} игнорируется", flush=True)


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


    