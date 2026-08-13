# Project Context

Last updated: 2026-08-13

## Purpose

This project runs an Arduino Mega2560 oil/rubber measurement instrument using an ADS1115 and a 320x240 ST7789 TFT. It supports Single and Continuous modes and streams raw VPHS/VMAG values to a Python real-time plotter.

## Active files

- `oil_project_5_rejectspikeDisplayMega2560_ContinuousIQR_serial.ino` — Arduino firmware and TFT UI.
- `realtime_vphs_vmag_plotter.py` — live two-panel VPHS/VMAG time-domain plotter.
- `requirements.txt` — Python dependencies (`pyserial` and `matplotlib`).
- `AGENTS.md` — instructions requiring this file to be read and maintained.

The obsolete `saturation_realtime_logger.py` and `three_mode_realtime_logger.py` programs were removed. Do not restore their old protocols unless explicitly requested.

## Hardware and main configuration

- Target board: Arduino Mega2560 (`arduino:avr:mega`).
- Serial baud rate: `9600`.
- Buttons use `INPUT_PULLUP`:
  - K1: pin 2 — short-press clear in Single mode; hold for mode change.
  - K3: pin 3 — Calibration in Single mode.
  - K2: pin 4 — Measurement in Single mode.
- TFT ST7789: CS 10, RST 9, DC 8, rotation 3 (landscape 320x240).
- ADS1115 address: `0x48`; channel 0 is VPHS and channel 1 is VMAG.
- ADC conversion currently used: `adc * 0.1875 / 1000.0` volts.

User-adjustable constants near the top of the sketch:

```cpp
const unsigned long SERIAL_OUTPUT_INTERVAL_MS = 1000UL;
const unsigned long SAMPLE_INTERVAL_MS = 1000UL;
const unsigned long K1_LONG_PRESS_MS = 3000UL;
const uint8_t COUNTDOWN_TIME_SECONDS = 30;
const uint8_t SAMPLE_COUNT = 10;
```

`SAMPLE_COUNT` must remain at least 4 because the calculation uses IQR filtering.

## Current Arduino behavior

### Startup and Continuous mode

- Arduino begins sending raw VPHS/VMAG data as soon as the ADS1115 initializes.
- Raw Serial output continues every `SERIAL_OUTPUT_INTERVAL_MS` in every screen and in both Single and Continuous modes, including waits and animations.
- Continuous-mode TFT values continue to refresh every 50 ms.
- The old one-minute/120-row/two-hour Serial logger no longer exists.

### K1 behavior

- K1 is the highest-priority input during countdown and sample collection.
- A short K1 press in Single mode clears calibration/measurement data and shows the NRT logo.
- A K1 hold of `K1_LONG_PRESS_MS` toggles Single/Continuous mode.
- A long press is classified separately from a short press and must not clear stored Calibration or Measurement values.
- While K2 or K3 work is active, K1 can abort the operation; a long hold enters Continuous mode while preserving stored values.

### K3 Calibration

- Available only in Single mode.
- At the instant K3 is pressed, Arduino emits a `K3_PRESS` record using the current raw VPHS/VMAG values.
- TFT shows a centered 30-second saturation countdown and warns the user not to open the door.
- After the countdown, calibration collects `SAMPLE_COUNT` samples, one every `SAMPLE_INTERVAL_MS`.
- The sampling animation and horizontal progress bar run at the same time as real sample collection.
- Data is filtered using IQR, then mean and SD are calculated.
- Current SD limits are VPHS `< 0.0010` and VMAG `< 0.0005` (the code includes a small comparison tolerance).
- On successful calibration, Arduino displays the result, stores `ReferenceVPHS`/`ReferenceVMAG`, and emits `K3_CAL` using those calculated reference values.
- A failed calibration shows an error and does not emit `K3_CAL`.

### K2 Measurement

- Available only in Single mode and only after successful calibration.
- It uses the same saturation countdown, sample interval, sample count, progress animation, IQR filtering, and SD validation.
- Displayed measurement values are:
  - `RealVPHS = ReferenceVPHS - MeasurementVPHS`
  - `RealVMAG = ReferenceVMAG - MeasurementVMAG`
- K2 does not currently create a special Serial marker; the normal `DATA` stream continues.

## Serial protocol

CSV-like records contain exactly four fields:

```text
DATA,<ArduinoMillis>,<rawVPHS>,<rawVMAG>
K3_PRESS,<ArduinoMillis>,<rawVPHS>,<rawVMAG>
K3_CAL,<ArduinoMillis>,<ReferenceVPHS>,<ReferenceVMAG>
```

Example:

```text
DATA,15234,1.234,2.345
K3_PRESS,18120,1.236,2.349
K3_CAL,58150,1.240,2.352
```

If ADS1115 initialization fails, Arduino emits `ERROR,ADS1115_INIT`. Python intentionally ignores records that do not match one of the three four-field plotting formats above.

Do not reintroduce `CONT`, `CAL`, `MEA`, per-minute logging, Excel logging, or two-hour session control into this protocol unless explicitly requested.

## Python plotter behavior

`realtime_vphs_vmag_plotter.py`:

- Detects Serial ports and asks the user to choose when multiple ports exist; `--port COMx` can select one directly.
- Accepts only `DATA`, `K3_PRESS`, and `K3_CAL` records.
- Uses Arduino `millis()` relative to the first accepted record for elapsed time in seconds.
- Shows two plots in one window:
  - VPHS versus elapsed time on top.
  - VMAG versus elapsed time on the bottom.
- Marks `K3_PRESS` on both plots with yellow circles.
- Marks successful `K3_CAL` values on both plots with yellow stars.
- Provides a TextBox for typing or pasting the graph title.
- Provides a `Save JPG` button that captures the graph at that moment without stopping acquisition.
- The save controls are temporarily hidden from the exported image and restored afterward.
- Multiple saves are allowed. Files use capture timestamps including milliseconds and are not intentionally overwritten.
- Default image directory: `realtime_graphs` beside the Python script.
- Pressing Q outside the title TextBox stops the program only. Q does not save an image.
- Closing the window or pressing Ctrl+C also stops without automatically saving an image.
- `--simulate` supplies test DATA and K3 markers without an Arduino.

Typical commands:

```powershell
py -m pip install -r requirements.txt
py realtime_vphs_vmag_plotter.py
py realtime_vphs_vmag_plotter.py --port COM4
py realtime_vphs_vmag_plotter.py --simulate
```

Close Arduino Serial Monitor before running Python because only one program can normally own the COM port at a time.

## Maintenance checklist

When behavior changes:

1. Update Arduino and Python together if the Serial record format changes.
2. Preserve TFT behavior unless the requested change includes the display.
3. Update the relevant sections of this file and its `Last updated` date.
4. Compile the sketch for Mega2560 when possible.
5. Syntax-check Python and test the plotting path when Matplotlib is available.

