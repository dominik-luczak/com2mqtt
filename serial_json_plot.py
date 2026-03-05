import argparse
import json
import logging
import threading
import time
from collections import deque
from typing import Dict, List, Optional

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import serial


DEFAULT_SERIAL_PORT = "COM3"
DEFAULT_BAUDRATE = 115200
DEFAULT_WINDOW_SIZE = 200
DEFAULT_REFRESH_MS = 250
DEFAULT_MAX_SERIES = 4


class SerialJsonPlotter:
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        x_field: str,
        frame_field: str,
        window_size: int,
        refresh_ms: int,
        max_series: int,
    ) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.x_field = x_field
        self.frame_field = frame_field
        self.window_size = window_size
        self.refresh_ms = refresh_ms
        self.max_series = max_series

        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        self.x_values: deque[float] = deque(maxlen=window_size)
        self.series_data: Dict[str, deque[float]] = {}
        self.sample_index = 0

        self.last_frame_value: Optional[float] = None
        self.valid_frames = 0
        self.invalid_frames = 0

    def start(self) -> None:
        reader_thread = threading.Thread(target=self._read_serial_loop, daemon=True)
        reader_thread.start()

        fig, ax = plt.subplots(figsize=(10, 5))
        fig.canvas.manager.set_window_title("Serial JSON Plot")

        def update(_frame_number: int):
            with self.lock:
                if not self.x_values or not self.series_data:
                    ax.clear()
                    ax.set_title("Waiting for JSON frames on serial...")
                    ax.set_xlabel(self.x_field)
                    ax.set_ylabel("Value")
                    return

                ax.clear()
                x = list(self.x_values)
                for key, values in self.series_data.items():
                    if len(values) == len(x):
                        ax.plot(x, list(values), label=key)

                ax.set_xlabel(self.x_field)
                ax.set_ylabel("Value")
                ax.set_title(
                    f"Live JSON Plot | valid frames: {self.valid_frames} | invalid frames: {self.invalid_frames}"
                )
                ax.grid(True, alpha=0.3)
                ax.legend(loc="upper left")

        animation = FuncAnimation(fig, update, interval=self.refresh_ms, cache_frame_data=False)

        try:
            plt.show()
        finally:
            self.stop_event.set()
            # Keep animation referenced until window closes.
            _ = animation

    def run_check_only(self, duration_s: int) -> None:
        logging.info("Starting frame check-only mode for %d second(s)", duration_s)
        reader_thread = threading.Thread(target=self._read_serial_loop, daemon=True)
        reader_thread.start()

        end_time = time.time() + duration_s
        while time.time() < end_time:
            if self.stop_event.is_set():
                break
            time.sleep(0.1)

        self.stop_event.set()
        reader_thread.join(timeout=2.0)

        logging.info(
            "Frame check summary: valid=%d invalid=%d",
            self.valid_frames,
            self.invalid_frames,
        )

    def _read_serial_loop(self) -> None:
        logging.info(
            "Opening serial port %s at %d bps, 8N1",
            self.serial_port,
            self.baudrate,
        )
        while not self.stop_event.is_set():
            try:
                with serial.Serial(
                    port=self.serial_port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1.0,
                ) as ser:
                    self._consume_serial(ser)
            except serial.SerialException as exc:
                logging.error("Serial error: %s", exc)
                time.sleep(1)
            except Exception as exc:
                logging.exception("Unexpected reader error: %s", exc)
                time.sleep(1)

    def _consume_serial(self, ser: serial.Serial) -> None:
        while not self.stop_event.is_set():
            raw_line = ser.readline()
            if not raw_line:
                continue

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self._mark_invalid("Non-JSON line")
                continue

            if not isinstance(payload, dict):
                self._mark_invalid("JSON frame is not an object")
                continue

            scalar_fields = self._extract_numeric_scalar_fields(payload)
            array_fields = self._extract_numeric_array_fields(payload)

            if not scalar_fields and not array_fields:
                self._mark_invalid("No numeric fields in JSON frame")
                continue

            if array_fields:
                lengths = {len(values) for values in array_fields.values()}
                if len(lengths) != 1:
                    self._mark_invalid("Numeric arrays have different lengths")
                    continue
                sample_count = next(iter(lengths))
                if sample_count == 0:
                    self._mark_invalid("Numeric arrays are empty")
                    continue

                frame_ok = self._check_frame(payload, has_array_payload=True)
                x_values = self._extract_x_array(payload, sample_count)
                sorted_keys = sorted(array_fields.keys())[: self.max_series]

                with self.lock:
                    for idx in range(sample_count):
                        if x_values is not None:
                            x_value = x_values[idx]
                        else:
                            self.sample_index += 1
                            x_value = float(self.sample_index)

                        self.x_values.append(x_value)
                        sample_fields = {key: array_fields[key][idx] for key in sorted_keys}
                        self._sync_series(sample_fields)

                    if frame_ok:
                        self.valid_frames += 1
                    else:
                        self.invalid_frames += 1
                continue

            frame_ok = self._check_frame(payload, has_array_payload=False)
            with self.lock:
                x_value = self._extract_x_scalar(payload)

                if x_value is None:
                    self.sample_index += 1
                    x_value = float(self.sample_index)

                self.x_values.append(x_value)
                self._sync_series(scalar_fields)

                if frame_ok:
                    self.valid_frames += 1
                else:
                    self.invalid_frames += 1

    def _extract_numeric_scalar_fields(self, payload: Dict[str, object]) -> Dict[str, float]:
        numeric: Dict[str, float] = {}
        for key, value in payload.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numeric[key] = float(value)

        if self.x_field in numeric:
            numeric.pop(self.x_field)
        if self.frame_field in numeric:
            numeric.pop(self.frame_field)

        if len(numeric) > self.max_series:
            keys = sorted(numeric.keys())[: self.max_series]
            numeric = {k: numeric[k] for k in keys}

        return numeric

    def _extract_numeric_array_fields(self, payload: Dict[str, object]) -> Dict[str, List[float]]:
        arrays: Dict[str, List[float]] = {}
        for key, value in payload.items():
            if key in {self.x_field, self.frame_field}:
                continue
            if not isinstance(value, list) or not value:
                continue

            converted: List[float] = []
            valid = True
            for item in value:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    valid = False
                    break
                converted.append(float(item))

            if valid:
                arrays[key] = converted

        return arrays

    def _extract_x_scalar(self, payload: Dict[str, object]) -> Optional[float]:
        value = payload.get(self.x_field)
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _extract_x_array(self, payload: Dict[str, object], sample_count: int) -> Optional[List[float]]:
        value = payload.get(self.x_field)
        if not isinstance(value, list) or len(value) != sample_count:
            return None

        converted: List[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            converted.append(float(item))
        return converted

    def _check_frame(self, payload: Dict[str, object], has_array_payload: bool) -> bool:
        frame_value = payload.get(self.frame_field)
        if frame_value is None:
            if has_array_payload:
                return True
            logging.warning("Frame check: missing '%s' field", self.frame_field)
            return False

        if isinstance(frame_value, bool) or not isinstance(frame_value, (int, float)):
            logging.warning("Frame check: '%s' is not numeric", self.frame_field)
            return False

        current = float(frame_value)
        if self.last_frame_value is not None and current <= self.last_frame_value:
            logging.warning(
                "Frame check: non-increasing frame (%s <= %s)",
                current,
                self.last_frame_value,
            )
            self.last_frame_value = current
            return False

        self.last_frame_value = current
        return True

    def _sync_series(self, numeric_fields: Dict[str, float]) -> None:
        # Add any new numeric field and backfill with NaN-like values for alignment.
        for key in numeric_fields:
            if key not in self.series_data:
                series = deque(maxlen=self.window_size)
                missing = len(self.x_values) - 1
                for _ in range(max(0, missing)):
                    series.append(float("nan"))
                self.series_data[key] = series

        for key, series in self.series_data.items():
            if key in numeric_fields:
                series.append(numeric_fields[key])
            else:
                series.append(float("nan"))

    def _mark_invalid(self, reason: str) -> None:
        with self.lock:
            self.invalid_frames += 1
        logging.warning("Invalid frame: %s", reason)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read JSON from serial and plot numeric values live")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Serial port (default: COM3)")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="Baudrate (default: 115200)")
    parser.add_argument(
        "--x-field",
        default="timestamp",
        help="JSON field for X axis (numeric). If missing, sample index is used.",
    )
    parser.add_argument(
        "--frame-field",
        default="frame",
        help="JSON field used for frame sequence check (default: frame)",
    )
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE, help="Number of samples in plot window")
    parser.add_argument("--refresh-ms", type=int, default=DEFAULT_REFRESH_MS, help="Plot refresh interval in ms")
    parser.add_argument("--max-series", type=int, default=DEFAULT_MAX_SERIES, help="Maximum numeric fields to plot")
    parser.add_argument("--check-only", action="store_true", help="Check/validate serial JSON frames without opening plot")
    parser.add_argument("--duration", type=int, default=10, help="Seconds to run in --check-only mode")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    plotter = SerialJsonPlotter(
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        x_field=args.x_field,
        frame_field=args.frame_field,
        window_size=args.window_size,
        refresh_ms=args.refresh_ms,
        max_series=args.max_series,
    )

    try:
        if args.check_only:
            plotter.run_check_only(max(1, args.duration))
        else:
            plotter.start()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
