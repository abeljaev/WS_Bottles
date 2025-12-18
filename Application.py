import time
from PLC import PLC
import threading
import signal
import sys
from WebSocket import WebSocket
from enum import Enum



class AppState(Enum):
    IDLE = "idle"
    DUMPING_PLASTIC = "dumping_plastic"
    DUMPING_ALUMINUM = "dumping_aluminum"

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
        self.dump_started_time = None
        self.dump_timeout = 3  # секунд

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
                        print("[State] Левый датчик достигнут, обнуляем регистры")
                        self.PLC.cmd_full_clear_register()
                        self.state = AppState.IDLE
                        self.dump_started_time = None
                    elif time.time() - self.dump_started_time > self.dump_timeout:
                        print("[State] ТАЙМАУТ при движении влево!")
                        self.PLC.cmd_full_clear_register()
                        self.state = AppState.IDLE
                        self.dump_started_time = None
                
                elif self.state == AppState.DUMPING_ALUMINUM:
                    # Ждем достижения правого датчика
                    if self.PLC.get_state_right_sensor_carriage() == 1:
                        print("[State] Правый датчик достигнут, обнуляем регистры")
                        self.PLC.cmd_full_clear_register()
                        self.state = AppState.IDLE
                        self.dump_started_time = None
                    elif time.time() - self.dump_started_time > self.dump_timeout:
                        print("[State] ТАЙМАУТ при движении вправо!")
                        self.PLC.cmd_full_clear_register()
                        self.state = AppState.IDLE
                        self.dump_started_time = None

                # ОБРАБОТКА КОМАНД ТОЛЬКО В СОСТОЯНИИ IDLE
                elif self.state == AppState.IDLE:
                    
                    # Vision обработка
                    if self.PLC.get_bottle_exist() == 1:
                        self.websocket_server.send_to_client("vision", "bottle_exist")
                        vision_response = self.websocket_server.get_state("vision")
                        if vision_response == "bottle":
                            # self.state = AppState.DUMPING_PLASTIC
                            self.PLC.cmd_radxa_detected_bottle()
                            
                            

                    elif self.PLC.get_bank_exist() == 1:
                        self.websocket_server.send_to_client("vision", "bank_exist")
                        vision_response = self.websocket_server.get_state("vision")
                        if vision_response == "bank":
                            self.PLC.cmd_radxa_detected_bank()
                            # self.state = AppState.DUMPING_ALUMINUM

                    else:
                        # pass
                        self.websocket_server.send_to_client("vision", "none")

                    # Обработка команд от app
                    app_message = self.websocket_server.get_command("app")
                    
                    # Парсим команду и параметр (формат: "command:param")
                    if ":" in app_message:
                        app_command, app_param = app_message.split(":", 1)
                    else:
                        app_command = app_message
                        app_param = None

                    if app_command == "get_photo":
                        pass
                    elif app_command == "get_device_info":
                        pass
                    elif app_command == "enter_service_mode":
                        pass
                    elif app_command == "exit_service_mode":
                        pass

                    elif app_command == "dump_container":
                        # Тип контейнера приходит как параметр: plastic или aluminium
                        if app_param == "plastic":
                            print("[State] Начинаем выброс ПЛАСТИКА (движение влево)")
                            self.state = AppState.DUMPING_PLASTIC
                            self.dump_started_time = time.time()
                            self.PLC.cmd_force_move_carriage_left()
                        elif app_param == "aluminium":
                            print("[State] Начинаем выброс АЛЮМИНИЯ (движение вправо)")
                            self.state = AppState.DUMPING_ALUMINUM
                            self.dump_started_time = time.time()
                            self.PLC.cmd_force_move_carriage_right()
                        else:
                            print(f"[State] Неизвестный тип контейнера: {app_param}")

                    elif app_command == "restore_device":
                        pass
                    elif app_command == "unlock_door":
                        pass
                    elif app_command == "lock_door":
                        pass

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


    