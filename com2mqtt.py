import argparse
import json
import logging
import signal
import sys
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt
import serial


DEFAULT_SERIAL_PORT = "COM3"
DEFAULT_BAUDRATE = 115200
DEFAULT_TOPIC = "com2mqtt/device/json"
DEFAULT_BROKER = "broker.hivemq.com"
DEFAULT_BROKER_PORT = 1883


class SerialToMqttBridge:
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        topic: str,
        broker_host: str,
        broker_port: int,
        client_id: Optional[str] = None,
    ) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.topic = topic
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.stop_event = threading.Event()

        self.mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("Connected to MQTT broker %s:%s", self.broker_host, self.broker_port)
        else:
            logging.error("MQTT connect failed with reason code: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self.stop_event.is_set():
            logging.info("MQTT client disconnected")
            return

        logging.warning("MQTT disconnected (reason=%s). Reconnecting...", reason_code)
        while not self.stop_event.is_set():
            try:
                self.mqtt_client.reconnect()
                logging.info("MQTT reconnect successful")
                return
            except Exception as exc:
                logging.warning("Reconnect failed: %s", exc)
                time.sleep(2)

    def start(self) -> None:
        self._connect_mqtt()
        self._run_serial_loop()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass

    def _connect_mqtt(self) -> None:
        self.mqtt_client.connect(self.broker_host, self.broker_port, keepalive=60)
        self.mqtt_client.loop_start()

    def _run_serial_loop(self) -> None:
        logging.info(
            "Opening serial port %s at %d bps, 8N1",
            self.serial_port,
            self.baudrate,
        )
        with serial.Serial(
            port=self.serial_port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
        ) as ser:
            while not self.stop_event.is_set():
                try:
                    raw_line = ser.readline()
                    if not raw_line:
                        continue

                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    payload_obj = json.loads(line)
                    payload = json.dumps(payload_obj, separators=(",", ":"))

                    result = self.mqtt_client.publish(self.topic, payload=payload, qos=0, retain=False)
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        logging.info("Published to %s: %s", self.topic, payload)
                    else:
                        logging.error("Publish failed with code %s", result.rc)

                except json.JSONDecodeError:
                    logging.warning("Skipping non-JSON serial line")
                except serial.SerialException as exc:
                    logging.error("Serial error: %s", exc)
                    time.sleep(1)
                except Exception as exc:
                    logging.exception("Unexpected error: %s", exc)
                    time.sleep(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read JSON lines from serial and publish to MQTT")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Serial port (default: COM3)")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="Baudrate (default: 115200)")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="MQTT topic")
    parser.add_argument("--broker", default=DEFAULT_BROKER, help="MQTT broker hostname")
    parser.add_argument("--broker-port", type=int, default=DEFAULT_BROKER_PORT, help="MQTT broker port")
    parser.add_argument("--client-id", default=None, help="Optional MQTT client ID")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    bridge = SerialToMqttBridge(
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        topic=args.topic,
        broker_host=args.broker,
        broker_port=args.broker_port,
        client_id=args.client_id,
    )

    def _handle_signal(signum, frame):
        logging.info("Signal %s received, shutting down...", signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        bridge.stop()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
