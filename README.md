# com2mqtt

Serial-to-MQTT bridge in Python.

## Features

- Reads device data from `COM3` with `115200` baud, `8N1`
- Expects JSON per serial line
- Publishes JSON to free public HiveMQ broker (`broker.hivemq.com:1883`)
- Includes second script to test by subscribing to the same MQTT topic

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run bridge

```powershell
python com2mqtt.py
```

or on Windows:

```powershell
.\run_bridge.bat
```

Default settings:

- Serial: `COM3`, `115200`, `8N1`
- Broker: `broker.hivemq.com:1883`
- Topic: `com2mqtt/device/json`

Override example:

```powershell
python com2mqtt.py --serial-port COM3 --baudrate 115200 --broker broker.hivemq.com --broker-port 1883 --topic com2mqtt/device/json
```

## Run test subscriber (second script)

```powershell
python mqtt_test_subscriber.py
```

or on Windows:

```powershell
.\run_subscriber.bat
```

This prints each MQTT payload from the topic so you can confirm the bridge is publishing correctly.
