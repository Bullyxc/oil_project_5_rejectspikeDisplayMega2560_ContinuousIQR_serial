"""Plot Continuous, K3 Cal, and K2 Measurement streams in one live window."""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BAUD_RATE = 9600
STREAM_ORDER = ("CONT", "CAL", "MEA")
SERIAL_TO_STREAM = {"CONT": "CONT", "CAL": "CAL", "MEA": "MEA"}
STREAM_TITLES = {
    "CONT": "Continuous - Raw VPHS / VMAG",
    "CAL": "K3 Calibration - Raw VPHS / VMAG",
    "MEA": "K2 Measurement - Calculated VPHS / VMAG",
}


@dataclass(frozen=True)
class Sample:
    stream: str
    source_second: int
    elapsed_second: float
    pc_datetime: datetime
    vphs: float
    vmag: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live three-mode VPHS/VMAG plotter")
    parser.add_argument("--port", help="Serial port, for example COM4")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "three_mode_measurements",
    )
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--simulation-interval", type=float, default=0.03)
    return parser.parse_args()


def choose_serial_port(requested_port: str | None) -> str:
    if requested_port:
        return requested_port
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError("Install dependencies: py -m pip install -r requirements.txt") from exc

    ports = sorted(list(list_ports.comports()), key=lambda item: item.device)
    if not ports:
        raise RuntimeError("No serial ports found. Connect Arduino and try again.")
    if len(ports) == 1:
        print(f"Using {ports[0].device}: {ports[0].description}")
        return ports[0].device
    print("Available serial ports:")
    for index, port in enumerate(ports, start=1):
        print(f"  {index}. {port.device} - {port.description}")
    while True:
        try:
            selection = int(input("Select port number: ").strip())
            if 1 <= selection <= len(ports):
                return ports[selection - 1].device
        except ValueError:
            pass
        print("Invalid selection. Please try again.")


def parse_stream_line(line: str) -> tuple[str, int, float, float] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 4 or parts[0] not in SERIAL_TO_STREAM:
        return None
    try:
        source_second = int(parts[1])
        vphs = float(parts[2])
        vmag = float(parts[3])
    except ValueError:
        return None
    if source_second < 0:
        return None
    return SERIAL_TO_STREAM[parts[0]], source_second, vphs, vmag


class ThreeModePlot:
    def __init__(self) -> None:
        try:
            import matplotlib.pyplot as plt
            from matplotlib.widgets import Button, TextBox
        except ImportError as exc:
            raise RuntimeError("Install dependencies: py -m pip install -r requirements.txt") from exc

        self.plt = plt
        self.stop_requested = False
        self.graph_title = "Rubber Tester - Three Mode Measurement"
        self.door_closed_elapsed: float | None = None
        self.samples: dict[str, list[Sample]] = {stream: [] for stream in STREAM_ORDER}

        plt.ion()
        self.figure, self.axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
        self.figure.canvas.manager.set_window_title("Continuous / K3 Cal / K2 Mea")
        self.figure.subplots_adjust(bottom=0.20, hspace=0.36)
        self.figure.suptitle(self.graph_title, fontsize=14)

        self.lines = {}
        self.door_markers = []
        for axis, stream in zip(self.axes, STREAM_ORDER):
            vphs_line = axis.plot([], [], color="#2563EB", linewidth=1.5, label="VPHS")[0]
            vmag_line = axis.plot([], [], color="#DC2626", linewidth=1.5, label="VMAG")[0]
            self.lines[stream] = (vphs_line, vmag_line)
            axis.set_title(STREAM_TITLES[stream])
            axis.set_ylabel("Voltage (V)")
            axis.grid(True, alpha=0.3)
            axis.legend(loc="best")
        self.axes[-1].set_xlabel("Elapsed time from program start (seconds)")

        title_axis = self.figure.add_axes([0.22, 0.115, 0.62, 0.04])
        door_axis = self.figure.add_axes([0.27, 0.025, 0.20, 0.055])
        stop_axis = self.figure.add_axes([0.54, 0.025, 0.20, 0.055])
        self.title_box = TextBox(title_axis, "Graph title: ", initial=self.graph_title)
        self.door_button = Button(door_axis, "Mark Door Closed")
        self.stop_button = Button(stop_axis, "Stop && Save")
        self.title_box.on_submit(self._set_title)
        self.door_button.on_clicked(self._mark_door)
        self.stop_button.on_clicked(self._stop)
        self.figure.canvas.mpl_connect("key_press_event", self._key_press)
        self.figure.canvas.mpl_connect("close_event", self._stop)
        plt.show(block=False)

    def _set_title(self, text: str) -> None:
        self.graph_title = text.strip() or "Rubber Tester - Three Mode Measurement"
        self.figure.suptitle(self.graph_title, fontsize=14)
        self.figure.canvas.draw_idle()

    def _mark_door(self, _event=None) -> None:
        all_samples = [sample for values in self.samples.values() for sample in values]
        self.door_closed_elapsed = max((s.elapsed_second for s in all_samples), default=0.0)
        print(f"Door closed marker: {self.door_closed_elapsed:.1f} seconds")
        self._refresh()

    def _stop(self, _event=None) -> None:
        self.stop_requested = True

    def _key_press(self, event) -> None:
        if event.inaxes == self.title_box.ax:
            return
        if event.key == " ":
            self._mark_door()
        elif event.key in {"q", "escape"}:
            self.stop_requested = True

    def add_sample(self, sample: Sample) -> None:
        values = self.samples[sample.stream]
        values.append(sample)
        self._refresh()

    def _refresh(self) -> None:
        for stream, axis in zip(STREAM_ORDER, self.axes):
            values = self.samples[stream]
            x_values = [sample.elapsed_second for sample in values]
            self.lines[stream][0].set_data(x_values, [sample.vphs for sample in values])
            self.lines[stream][1].set_data(x_values, [sample.vmag for sample in values])
            axis.relim()
            axis.autoscale_view()

        for marker in self.door_markers:
            marker.remove()
        self.door_markers = []
        if self.door_closed_elapsed is not None:
            for axis in self.axes:
                self.door_markers.append(
                    axis.axvline(
                        self.door_closed_elapsed,
                        color="#16A34A",
                        linestyle="--",
                        linewidth=1.4,
                    )
                )
        self.figure.canvas.draw_idle()

    def process_events(self) -> None:
        if self.is_open():
            self.plt.pause(0.01)

    def is_open(self) -> bool:
        return bool(self.plt.fignum_exists(self.figure.number))

    def save_results(self, output_dir: Path, started_at: datetime) -> tuple[Path, Path] | None:
        all_samples = sorted(
            [sample for values in self.samples.values() for sample in values],
            key=lambda sample: sample.elapsed_second,
        )
        if not all_samples:
            print("No data received; no output files created.")
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        csv_path = output_dir / f"three_mode_{timestamp}.csv"
        jpg_path = output_dir / f"three_mode_{timestamp}.jpg"

        with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                ["Stream", "Source Second", "Elapsed Second", "PC Date Time", "VPHS", "VMAG"]
            )
            for sample in all_samples:
                writer.writerow(
                    [
                        sample.stream,
                        sample.source_second,
                        f"{sample.elapsed_second:.3f}",
                        sample.pc_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{sample.vphs:.3f}",
                        f"{sample.vmag:.3f}",
                    ]
                )

        self._set_title(self.title_box.text)
        self.title_box.ax.set_visible(False)
        self.door_button.ax.set_visible(False)
        self.stop_button.ax.set_visible(False)
        self.figure.subplots_adjust(bottom=0.08, hspace=0.36)
        self.figure.savefig(jpg_path, format="jpg", dpi=200, bbox_inches="tight")
        print(f"CSV saved:   {csv_path}")
        print(f"Graph saved: {jpg_path}")
        return csv_path, jpg_path

    def close(self) -> None:
        if self.is_open():
            self.plt.ioff()
            self.plt.close(self.figure)


def run_simulation(args: argparse.Namespace, plot: ThreeModePlot, start_clock: float) -> int:
    print("Simulating CONT, CAL, and MEA streams.")
    source_seconds = {stream: 0 for stream in STREAM_ORDER}
    for index in range(90):
        if plot.stop_requested or not plot.is_open():
            break
        stream = STREAM_ORDER[min(index // 30, 2)]
        source_seconds[stream] += 1
        elapsed = float(index + 1)
        response = 1.0 - math.exp(-max(0, index - 10) / 12.0)
        offsets = {"CONT": 0.0, "CAL": 0.15, "MEA": -0.60}
        plot.add_sample(
            Sample(
                stream,
                source_seconds[stream],
                elapsed,
                datetime.now(),
                1.1 + offsets[stream] + 0.08 * response,
                2.2 + offsets[stream] + 0.12 * response,
            )
        )
        if index == 10:
            plot._mark_door()
        plot.process_events()
        time.sleep(max(0.0, args.simulation_interval))
    return 0


def run_serial(args: argparse.Namespace, plot: ThreeModePlot, start_clock: float) -> int:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("Install dependencies: py -m pip install -r requirements.txt") from exc

    port = choose_serial_port(args.port)
    print(f"Opening {port} at {args.baud} baud...")
    print("CONT updates the top graph; K3 updates the middle; K2 updates the bottom.")
    print("Press Space to mark door closed. Press Stop, Q, Esc, Ctrl+C, or close to save.")
    connection = None
    try:
        connection = serial.Serial(port, args.baud, timeout=0.25)
        time.sleep(2.0)
        connection.reset_input_buffer()
        while not plot.stop_requested and plot.is_open():
            raw_line = connection.readline()
            plot.process_events()
            if not raw_line:
                continue
            parsed = parse_stream_line(raw_line.decode("utf-8", errors="replace").strip())
            if parsed is None:
                continue
            stream, source_second, vphs, vmag = parsed
            elapsed = time.monotonic() - start_clock
            plot.add_sample(Sample(stream, source_second, elapsed, datetime.now(), vphs, vmag))
            print(f"{stream:4s} {elapsed:7.1f}s  VPHS={vphs:.3f}  VMAG={vmag:.3f}")
    except serial.SerialException as exc:
        print(f"Serial connection error: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None and connection.is_open:
            connection.close()
    return 0


def main() -> int:
    args = parse_args()
    started_at = datetime.now()
    start_clock = time.monotonic()
    plot = ThreeModePlot()
    exit_code = 0
    try:
        if args.simulate:
            exit_code = run_simulation(args, plot, start_clock)
        else:
            exit_code = run_serial(args, plot, start_clock)
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        plot.save_results(args.output_dir, started_at)
        plot.close()
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
