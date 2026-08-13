"""Plot Arduino VPHS/VMAG data, K3 stamps, and collected samples in real time."""

from __future__ import annotations

import argparse
import sys
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BAUD_RATE = 9600
PLOT_REFRESH_INTERVAL_SECONDS = 0.10
LIVE_WINDOW_SECONDS = 30.0 * 60.0
SERIAL_READ_TIMEOUT_SECONDS = 0.05
STANDARD_RECORD_TYPES = {"DATA", "K3_PRESS", "K3_CAL"}
SAMPLE_RECORD_TYPES = {"K3_SAMPLE"}


@dataclass(frozen=True)
class SerialRecord:
    record_type: str
    device_ms: int
    vphs: float
    vmag: float
    sample_index: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live VPHS/VMAG time-domain plotter")
    parser.add_argument("--port", help="Serial port, for example COM4")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "realtime_graphs",
    )
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--simulation-interval", type=float, default=0.03)
    parser.add_argument(
        "--live-window",
        type=float,
        default=LIVE_WINDOW_SECONDS,
        help="Seconds shown in the live window; use 0 to show all data",
    )
    return parser.parse_args()


def parse_serial_line(line: str) -> SerialRecord | None:
    parts = [part.strip() for part in line.split(",")]
    if not parts:
        return None
    try:
        if parts[0] in STANDARD_RECORD_TYPES and len(parts) == 4:
            record = SerialRecord(parts[0], int(parts[1]), float(parts[2]), float(parts[3]))
        elif parts[0] in SAMPLE_RECORD_TYPES and len(parts) == 5:
            record = SerialRecord(
                parts[0], int(parts[1]), float(parts[3]), float(parts[4]), int(parts[2])
            )
        else:
            return None
    except (ValueError, IndexError):
        return None
    if record.device_ms < 0 or (record.sample_index is not None and record.sample_index < 1):
        return None
    return record


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
            selected = int(input("Select port number: ").strip())
            if 1 <= selected <= len(ports):
                return ports[selected - 1].device
        except ValueError:
            pass
        print("Invalid selection. Please try again.")


class RealtimePlot:
    def __init__(self, output_dir: Path, live_window_seconds: float) -> None:
        try:
            import matplotlib.pyplot as plt
            from matplotlib.widgets import Button, TextBox
        except ImportError as exc:
            raise RuntimeError("Install dependencies: py -m pip install -r requirements.txt") from exc

        self.plt = plt
        self.output_dir = output_dir
        self.live_window_seconds = max(0.0, live_window_seconds)
        self.stop_requested = False
        self.dirty = True
        self.legend_dirty = False
        self.last_refresh_at = 0.0
        self.latest_elapsed = 0.0
        self.graph_title = "VPHS / VMAG Measurement"
        self.first_device_ms: int | None = None
        self.times: list[float] = []
        self.vphs_values: list[float] = []
        self.vmag_values: list[float] = []
        self.press_stamps: list[tuple[float, float, float]] = []
        self.cal_stamps: list[tuple[float, float, float]] = []
        self.cal_samples: list[tuple[float, int, float, float]] = []

        plt.ion()
        self.figure, self.axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        self.figure.canvas.manager.set_window_title("VPHS / VMAG Real-time Plot")
        self.figure.subplots_adjust(bottom=0.17, hspace=0.28)
        self.figure.suptitle(self.graph_title, fontsize=14)

        self.vphs_line = self.axes[0].plot([], [], color="#2563EB", linewidth=1.5)[0]
        self.vmag_line = self.axes[1].plot([], [], color="#DC2626", linewidth=1.5)[0]

        self.axes[0].set_title("VPHS - Time Domain")
        self.axes[0].set_ylabel("VPHS (V)")
        self.axes[1].set_title("VMAG - Time Domain")
        self.axes[1].set_ylabel("VMAG (V)")
        self.axes[1].set_xlabel("Elapsed time (seconds)")
        for axis in self.axes:
            axis.grid(True, alpha=0.3)

        title_axis = self.figure.add_axes([0.18, 0.055, 0.54, 0.05])
        self.title_box = TextBox(title_axis, "Graph title: ", initial=self.graph_title)
        self.title_box.on_submit(self._set_title)
        save_axis = self.figure.add_axes([0.77, 0.055, 0.14, 0.05])
        self.save_button = Button(save_axis, "Save JPG", color="#E5E7EB", hovercolor="#D1D5DB")
        self.save_button.on_clicked(self._save_button_clicked)
        self.help_text = self.figure.text(
            0.5,
            0.018,
            "Save JPG: capture the graph now    |    Q: stop the program",
            ha="center",
            fontsize=9,
            color="#555555",
        )
        self.figure.canvas.mpl_connect("key_press_event", self._key_press)
        self.figure.canvas.mpl_connect("close_event", self._close_event)
        plt.show(block=False)

    def _set_title(self, text: str) -> None:
        self.graph_title = text.strip() or "VPHS / VMAG Measurement"
        self.figure.suptitle(self.graph_title, fontsize=14)
        self.figure.canvas.draw_idle()

    def _key_press(self, event) -> None:
        if event.inaxes == self.title_box.ax:
            return
        if event.key and event.key.lower() == "q":
            self.stop_requested = True

    def _save_button_clicked(self, _event) -> None:
        self.save_jpg()

    def _close_event(self, _event) -> None:
        self.stop_requested = True

    def _elapsed_seconds(self, device_ms: int) -> float:
        if self.first_device_ms is None:
            self.first_device_ms = device_ms
        return (device_ms - self.first_device_ms) / 1000.0

    def add_record(self, record: SerialRecord) -> None:
        elapsed = self._elapsed_seconds(record.device_ms)
        self.latest_elapsed = max(self.latest_elapsed, elapsed)
        if record.record_type == "DATA":
            self.times.append(elapsed)
            self.vphs_values.append(record.vphs)
            self.vmag_values.append(record.vmag)
        elif record.record_type == "K3_PRESS":
            self.press_stamps.append((elapsed, record.vphs, record.vmag))
            self._add_k3_press_artists(elapsed, record.vphs, record.vmag)
            print(
                f"K3 pressed at {elapsed:.1f}s: "
                f"VPHS={record.vphs:.3f}, VMAG={record.vmag:.3f}"
            )
        elif record.record_type == "K3_CAL":
            self.cal_stamps.append((elapsed, record.vphs, record.vmag))
            self._add_k3_cal_artists(elapsed, record.vphs, record.vmag)
            print(
                f"K3 calibration result at {elapsed:.1f}s: "
                f"VPHS={record.vphs:.3f}, VMAG={record.vmag:.3f}"
            )
        elif record.record_type in SAMPLE_RECORD_TYPES and record.sample_index is not None:
            self.cal_samples.append((elapsed, record.sample_index, record.vphs, record.vmag))
            self._add_cal_sample_artists(
                elapsed, record.sample_index, record.vphs, record.vmag
            )
            print(
                f"CAL {record.sample_index} at {elapsed:.1f}s: "
                f"VPHS={record.vphs:.3f}, VMAG={record.vmag:.3f}"
            )
        self.dirty = True

    def _add_k3_press_artists(self, elapsed: float, vphs: float, vmag: float) -> None:
        label = "K3 pressed" if len(self.press_stamps) == 1 else None
        for axis, value in zip(self.axes, (vphs, vmag)):
            axis.scatter(
                elapsed, value, s=70, color="#FACC15", edgecolor="#854D0E",
                zorder=5, label=label,
            )
        if label:
            self.legend_dirty = True

    def _add_k3_cal_artists(self, elapsed: float, vphs: float, vmag: float) -> None:
        label = "K3 calculated" if len(self.cal_stamps) == 1 else None
        for axis, value in zip(self.axes, (vphs, vmag)):
            axis.scatter(
                elapsed, value, s=110, marker="*", color="#FACC15",
                edgecolor="#713F12", zorder=6, label=label,
            )
        if label:
            self.legend_dirty = True

    def _add_cal_sample_artists(
        self, elapsed: float, sample_index: int, vphs: float, vmag: float
    ) -> None:
        label = "Calibration samples" if len(self.cal_samples) == 1 else None
        point_name = f"CAL {sample_index}"
        for axis, value in zip(self.axes, (vphs, vmag)):
            axis.scatter(
                elapsed, value, s=48, marker="o", color="#0891B2",
                edgecolor="white", linewidth=0.6, zorder=7, label=label,
            )
            axis.annotate(
                point_name,
                (elapsed, value),
                xytext=(4, 7 if sample_index % 2 else -13),
                textcoords="offset points",
                fontsize=7,
                color="#0891B2",
                rotation=35,
                zorder=8,
            )
        if label:
            self.legend_dirty = True

    def _refresh(self, *, force: bool = False, full_history: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if not self.dirty or now - self.last_refresh_at < PLOT_REFRESH_INTERVAL_SECONDS:
                return

        x_end = max(1.0, self.latest_elapsed)
        if full_history or self.live_window_seconds <= 0.0:
            x_start = 0.0
        else:
            x_start = max(0.0, x_end - self.live_window_seconds)

        first_visible = bisect_left(self.times, x_start)
        visible_times = self.times[first_visible:]
        visible_vphs = self.vphs_values[first_visible:]
        visible_vmag = self.vmag_values[first_visible:]
        self.vphs_line.set_data(visible_times, visible_vphs)
        self.vmag_line.set_data(visible_times, visible_vmag)

        self.axes[0].set_xlim(x_start, x_end + max(1.0, (x_end - x_start) * 0.01))
        self._set_y_limits(self.axes[0], visible_vphs, x_start, channel_index=1)
        self._set_y_limits(self.axes[1], visible_vmag, x_start, channel_index=2)

        if self.legend_dirty:
            for axis in self.axes:
                handles, labels = axis.get_legend_handles_labels()
                if labels:
                    axis.legend(loc="best")
            self.legend_dirty = False

        self.dirty = False
        self.last_refresh_at = now
        self.figure.canvas.draw_idle()

    def _set_y_limits(
        self, axis, line_values: list[float], x_start: float, channel_index: int
    ) -> None:
        values = list(line_values)
        values.extend(
            stamp[channel_index]
            for stamp in self.press_stamps
            if stamp[0] >= x_start
        )
        values.extend(
            stamp[channel_index]
            for stamp in self.cal_stamps
            if stamp[0] >= x_start
        )
        sample_value_index = channel_index + 1
        values.extend(
            sample[sample_value_index]
            for sample in self.cal_samples
            if sample[0] >= x_start
        )
        if not values:
            axis.set_ylim(0.0, 1.0)
            return
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        padding = max(span * 0.08, abs((minimum + maximum) / 2.0) * 0.002, 0.0005)
        axis.set_ylim(minimum - padding, maximum + padding)

    def process_events(self) -> None:
        if self.is_open():
            self._refresh()
            self.plt.pause(0.001)

    def is_open(self) -> bool:
        return bool(self.plt.fignum_exists(self.figure.number))

    def save_jpg(self) -> Path | None:
        if not any(
            (self.times, self.press_stamps, self.cal_stamps, self.cal_samples)
        ):
            print("No graph data received; no JPG was created.")
            return None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        captured_at = datetime.now()
        output_path = self.output_dir / (
            f"vphs_vmag_{captured_at:%Y%m%d_%H%M%S}_{captured_at.microsecond // 1000:03d}.jpg"
        )

        self._set_title(self.title_box.text)
        self._refresh(force=True, full_history=True)
        previous_bottom = self.figure.subplotpars.bottom
        self.title_box.ax.set_visible(False)
        self.save_button.ax.set_visible(False)
        self.help_text.set_visible(False)
        self.figure.subplots_adjust(bottom=0.09, hspace=0.28)
        try:
            self.figure.savefig(output_path, format="jpg", dpi=200, bbox_inches="tight")
        finally:
            self.figure.subplots_adjust(bottom=previous_bottom, hspace=0.28)
            self.title_box.ax.set_visible(True)
            self.save_button.ax.set_visible(True)
            self.help_text.set_visible(True)
            self._refresh(force=True, full_history=False)
            self.figure.canvas.draw_idle()
        print(f"Graph saved: {output_path}")
        return output_path

    def close(self) -> None:
        if self.is_open():
            self.plt.ioff()
            self.plt.close(self.figure)


def run_simulation(args: argparse.Namespace, plot: RealtimePlot) -> int:
    for second in range(90):
        if plot.stop_requested or not plot.is_open():
            break
        response = 1.0 - pow(2.718281828, -second / 18.0)
        plot.add_record(SerialRecord("DATA", second * 1000, 1.1 + 0.15 * response, 2.2 + 0.22 * response))
        if second == 12:
            plot.add_record(SerialRecord("K3_PRESS", second * 1000, 1.173, 2.307))
        if 43 <= second <= 52:
            sample_index = second - 42
            plot.add_record(
                SerialRecord(
                    "K3_SAMPLE", second * 1000, 1.232 + sample_index * 0.0008,
                    2.394 + sample_index * 0.0011, sample_index,
                )
            )
        if second == 52:
            plot.add_record(SerialRecord("K3_CAL", second * 1000, 1.241, 2.407))
        plot.process_events()
        time.sleep(max(0.0, args.simulation_interval))
    return 0


def run_serial(args: argparse.Namespace, plot: RealtimePlot) -> int:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("Install dependencies: py -m pip install -r requirements.txt") from exc

    port = choose_serial_port(args.port)
    print(f"Opening {port} at {args.baud} baud...")
    print("Type or paste a graph title. Use Save JPG to capture; press Q to stop.")
    connection = None
    try:
        connection = serial.Serial(port, args.baud, timeout=SERIAL_READ_TIMEOUT_SECONDS)
        time.sleep(2.0)
        connection.reset_input_buffer()
        while not plot.stop_requested and plot.is_open():
            raw_line = connection.readline()
            plot.process_events()
            if not raw_line:
                continue
            record = parse_serial_line(raw_line.decode("utf-8", errors="replace").strip())
            if record is not None:
                plot.add_record(record)
    except serial.SerialException as exc:
        print(f"Serial connection error: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None and connection.is_open:
            connection.close()
    return 0


def main() -> int:
    args = parse_args()
    plot = RealtimePlot(args.output_dir, args.live_window)
    exit_code = 0
    try:
        exit_code = run_simulation(args, plot) if args.simulate else run_serial(args, plot)
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        plot.close()
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
