import argparse
import logging
import signal
import sys
import threading

import paho.mqtt.client as mqtt


DEFAULT_TOPIC = "com2mqtt/device/json"
DEFAULT_BROKER = "broker.hivemq.com"
DEFAULT_BROKER_PORT = 1883


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Subscribe and print MQTT messages for testing")
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

    stop_event = threading.Event()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=args.client_id,
        clean_session=True,
    )

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("Connected to MQTT broker %s:%s", args.broker, args.broker_port)
            client.subscribe(args.topic)
            logging.info("Subscribed to topic: %s", args.topic)
        else:
            logging.error("MQTT connect failed with reason code: %s", reason_code)

    def on_message(client, userdata, message):
        payload = message.payload.decode("utf-8", errors="replace")
        print(payload)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        if not stop_event.is_set():
            logging.warning("Disconnected from broker (reason=%s)", reason_code)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    def _handle_signal(signum, frame):
        logging.info("Signal %s received, shutting down...", signum)
        stop_event.set()
        client.disconnect()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    try:
        client.connect(args.broker, args.broker_port, keepalive=60)
        client.loop_start()

        while not stop_event.is_set():
            stop_event.wait(0.2)

        client.loop_stop()
    except KeyboardInterrupt:
        stop_event.set()
        client.disconnect()
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
