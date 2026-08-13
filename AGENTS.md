# Project Instructions

## Required context workflow

- Read `CONTEXT.md` completely before inspecting or changing project code.
- Treat the source files as the final authority when they disagree with `CONTEXT.md`.
- After every material change to behavior, protocol, configuration, dependencies, filenames, or operating instructions, update `CONTEXT.md` in the same task.
- Do not update the `Last updated` field for formatting-only changes that do not alter project behavior.
- Keep `CONTEXT.md` concise and describe the current state. Record removed behavior only when it prevents a likely regression or protocol mismatch.

## Project safety rules

- Preserve the existing TFT display, bitmap data, button behavior, calibration, measurement, and Continuous-mode behavior unless the user explicitly requests changes to them.
- Do not restore any obsolete one-minute, two-hour, `CONT`, `CAL`, or `MEA` Serial logging protocol.
- Keep the Arduino and Python Serial formats synchronized. If either side changes, update the other side and document the protocol in `CONTEXT.md`.
- Put user-adjustable timing and sample-count constants near the top of the Arduino sketch.
- K1 has the highest priority during countdown and sample collection. Preserve the distinction between a short press and a 3-second hold.
- A K1 long press must preserve stored calibration and measurement values.
- Avoid editing generated measurement folders or previously captured output unless the user explicitly asks.

## Verification expectations

- For Arduino changes, compile for `arduino:avr:mega` when the Arduino CLI is available.
- For Python changes, at minimum run a syntax check. When Matplotlib is available, also exercise `--simulate` or the changed plotting path.
- Check the final diff for accidental changes to large logo bitmap arrays.
- Report any verification step that could not run because a required local tool or package is unavailable.

