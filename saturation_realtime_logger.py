"""Plot VPHS/VMAG every second and save the final saturation graph on stop."""

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
SATURATION_PREFIX = "CONT"


@dataclass(frozen=True)
class Sample:
    second: int
    pc_datetime: datetime
    vphs: float
    vmag: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot the 1-second VPHS/VMAG saturation stream from Arduino."
    )
    parser.add_argument("--port", help="Serial port, for example COM4")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "saturation_measurements",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run a fast saturation simulation without Arduino",
    )
    parser.add_argument(
        "--simulation-interval",
        type=float,
        default=0.05,
        help="Seconds between simulated samples",
    )
    return parser.parse_args()


def choose_serial_port(requested_port: str | None) -> str:
    if requested_port:
        return requested_port

    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is not installed. Run: py -m pip install -r requirements.txt"
        ) from exc

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


def parse_saturation_line(line: str) -> tuple[int, float, float] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 4 or parts[0] != SATURATION_PREFIX:
        return None
    try:
        second = int(parts[1])
        vphs = float(parts[2])
        vmag = float(parts[3])
    except ValueError:
        return None
    if second < 0:
        return None
    return second, vphs, vmag


class SaturationPlot:
    def __init__(self) -> None:
        try:
            import matplotlib.pyplot as plt
            from matplotlib.widgets import Button, TextBox
        except ImportError as exc:
            raise RuntimeError(
                "matplotlib is not installed. Run: py -m pip install -r requirements.txt"
            ) from exc

        self.plt = plt
        self.stop_requested = False
        self.door_closed_second: int | None = None
        self.samples: list[Sample] = []
        self.graph_title = "Saturation measurement"

        plt.ion()
        self.figure, self.axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        self.figure.canvas.manager.set_window_title("VPHS / VMAG Saturation Monitor")
        self.figure.subplots_adjust(bottom=0.23, hspace=0.28)
        self.figure.suptitle(self.graph_title, fontsize=13)

        self.vphs_line = self.axes[0].plot([], [], color="#2563EB", linewidth=1.6)[0]
        self.vmag_line = self.axes[1].plot([], [], color="#DC2626", linewidth=1.6)[0]
        self.door_markers = []

        self.axes[0].set_title("VPHS - Real-time (1 second/sample)")
        self.axes[0].set_ylabel("VPHS (V)")
        self.axes[1].set_title("VMAG - Real-time (1 second/sample)")
        self.axes[1].set_ylabel("VMAG (V)")
        self.axes[1].set_xlabel("Seconds from Continuous mode start")
        for axis in self.axes:
            axis.grid(True, alpha=0.3)

        title_axis = self.figure.add_axes([0.22, 0.135, 0.62, 0.05])
        mark_axis = self.figure.add_axes([0.26, 0.035, 0.22, 0.065])
        stop_axis = self.figure.add_axes([0.54, 0.035, 0.20, 0.065])
        self.title_box = TextBox(
            title_axis,
            "Graph title: ",
            initial=self.graph_title,
        )
        self.mark_button = Button(mark_axis, "Mark Door Closed (Space)")
        self.stop_button = Button(stop_axis, "Stop && Save JPG")
        self.title_box.on_submit(self._set_graph_title)
        self.mark_button.on_clicked(self._mark_door_from_button)
        self.stop_button.on_clicked(self._request_stop)
        self.figure.canvas.mpl_connect("key_press_event", self._on_key_press)
        self.figure.canvas.mpl_connect("close_event", self._request_stop)
        plt.show(block=False)

    def _on_key_press(self, event) -> None:
        if event.inaxes == self.title_box.ax:
            return
        if event.key == " ":
            self.mark_door_closed()
        elif event.key in {"escape", "q"}:
            self.stop_requested = True

    def _mark_door_from_button(self, _event) -> None:
        self.mark_door_closed()

    def _request_stop(self, _event) -> None:
        self.stop_requested = True

    def _set_graph_title(self, text: str) -> None:
        entered_title = text.strip()
        self.graph_title = entered_title or "Saturation measurement"
        self.figure.suptitle(self.graph_title, fontsize=13)
        self.figure.canvas.draw_idle()

    def mark_door_closed(self) -> None:
        self.door_closed_second = self.samples[-1].second if self.samples else 0
        print(f"Door closed marker set at Arduino second {self.door_closed_second}.")
        self._refresh()

    def add_sample(self, sample: Sample) -> None:
        if self.samples and sample.second <= self.samples[-1].second:
            return
        self.samples.append(sample)
        self._refresh()

    def _x_values(self) -> list[int]:
        if self.door_closed_second is None:
            return [sample.second for sample in self.samples]
        return [sample.second - self.door_closed_second for sample in self.samples]

    def _refresh(self) -> None:
        x_values = self._x_values()
        self.vphs_line.set_data(x_values, [sample.vphs for sample in self.samples])
        self.vmag_line.set_data(x_values, [sample.vmag for sample in self.samples])

        for marker in self.door_markers:
            marker.remove()
        self.door_markers = []
        if self.door_closed_second is not None:
            for axis in self.axes:
                self.door_markers.append(
                    axis.axvline(0, color="#16A34A", linestyle="--", linewidth=1.5,
                                 label="Door closed")
                )
            self.axes[1].set_xlabel("Seconds after door closed (negative = before closing)")
            self.axes[0].legend(loc="best")
        else:
            self.axes[1].set_xlabel("Seconds from Continuous mode start")
            legend = self.axes[0].get_legend()
            if legend is not None:
                legend.remove()

        for axis in self.axes:
            axis.relim()
            axis.autoscale_view()
        self.figure.canvas.draw_idle()

    def process_events(self) -> None:
        if self.is_open():
            self.plt.pause(0.01)

    def is_open(self) -> bool:
        return bool(self.plt.fignum_exists(self.figure.number))

    def save_results(self, output_dir: Path, started_at: datetime) -> tuple[Path, Path] | None:
        if not self.samples:
            print("No SAT samples were received; no output files were created.")
            return None

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        base_name = f"saturation_{timestamp}"
        csv_path = output_dir / f"{base_name}.csv"
        jpg_path = output_dir / f"{base_name}.jpg"

        with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                ["Arduino Second", "Seconds After Door Closed", "PC Date Time", "VPHS", "VMAG"]
            )
            for sample in self.samples:
                relative_second = (
                    "" if self.door_closed_second is None
                    else sample.second - self.door_closed_second
                )
                writer.writerow(
                    [
                        sample.second,
                        relative_second,
                        sample.pc_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{sample.vphs:.3f}",
                        f"{sample.vmag:.3f}",
                    ]
                )

        self._refresh()
        self._set_graph_title(self.title_box.text)
        self.title_box.ax.set_visible(False)
        self.mark_button.ax.set_visible(False)
        self.stop_button.ax.set_visible(False)
        self.figure.subplots_adjust(bottom=0.10, hspace=0.28)
        self.figure.savefig(jpg_path, format="jpg", dpi=200, bbox_inches="tight")
        print(f"CSV saved:   {csv_path}")
        print(f"Graph saved: {jpg_path}")
        return csv_path, jpg_path

    def close(self) -> None:
        if self.is_open():
            self.plt.ioff()
            self.plt.close(self.figure)


def run_simulation(args: argparse.Namespace, plot: SaturationPlot) -> int:
    print("Simulation started. The door marker will be set at second 10.")
    for second in range(1, 91):
        if plot.stop_requested or not plot.is_open():
            break
        response = 1.0 - math.exp(-max(0, second - 10) / 14.0)
        plot.add_sample(
            Sample(
                second=second,
                pc_datetime=datetime.now(),
                vphs=1.100 + 0.180 * response,
                vmag=2.300 + 0.260 * response,
            )
        )
        if second == 10:
            plot.mark_door_closed()
        plot.process_events()
        time.sleep(max(0.0, args.simulation_interval))
    return 0


def run_serial(args: argparse.Namespace, plot: SaturationPlot) -> int:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is not installed. Run: py -m pip install -r requirements.txt"
        ) from exc

    port = choose_serial_port(args.port)
    print(f"Opening {port} at {args.baud} baud...")
    print("Enter Continuous mode, then close the door and press SPACE in the graph window.")
    print("Press the Stop & Save JPG button, Q, Esc, Ctrl+C, or close the window to stop.")

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
            line = raw_line.decode("utf-8", errors="replace").strip()
            parsed = parse_saturation_line(line)
            if parsed is None:
                continue
            second, vphs, vmag = parsed
            plot.add_sample(Sample(second, datetime.now(), vphs, vmag))
            print(f"{second:6d}s  VPHS={vphs:.3f}  VMAG={vmag:.3f}")
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
    plot = SaturationPlot()
    exit_code = 0
    try:
        if args.simulate:
            exit_code = run_simulation(args, plot)
        else:
            exit_code = run_serial(args, plot)
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
