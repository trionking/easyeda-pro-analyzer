---
name: easyeda-pro-analyzer
description: >
  Analyze EasyEDA Pro schematic project files (.eprj). Use this skill whenever the user uploads
  a .eprj file, mentions EasyEDA Pro schematics, asks to review or extract BOM/netlist from
  an EasyEDA project, or wants to compare a schematic design against specifications.
  Also trigger when the user says "회로도 분석", "EDA 파일", "schematic analysis", or references
  an uploaded .eprj file path. This skill handles parsing, BOM extraction, net tracing,
  and IC-to-component mapping from EasyEDA Pro SQLite databases.
---

# EasyEDA Pro Schematic Analyzer

## File Format Overview

EasyEDA Pro `.eprj` files are **SQLite 3 databases**. All schematic data is stored as
**base64-encoded gzip-compressed JSON-lines** inside the `dataStr` column.

## Step 1: Run the analysis script

Before doing any manual parsing, run the bundled analysis script:

```bash
python3 /mnt/skills/user/easyeda-pro-analyzer/scripts/analyze_eprj.py /path/to/file.eprj
```

For a compact overview, use `--summary`:

```bash
python3 /mnt/skills/user/easyeda-pro-analyzer/scripts/analyze_eprj.py /path/to/file.eprj --summary
```

This outputs:
- Per-sheet part counts and net counts
- Cross-sheet net comparison (shared / sheet-only)
- Floating component detection
- FB voltage divider auto-calculation (BQ24650, TPS61088, LGS5145)
- IC signal-net connection summary

For full detail (BOM, all net connections, power symbols, texts):

```bash
python3 /mnt/skills/user/easyeda-pro-analyzer/scripts/analyze_eprj.py /path/to/file.eprj
```

For JSON output (programmatic processing):

```bash
python3 /mnt/skills/user/easyeda-pro-analyzer/scripts/analyze_eprj.py /path/to/file.eprj --json
```

## Step 2: Interpret the results

### Component-to-IC Mapping (CRITICAL)

**Never map components to ICs by coordinate proximity alone.** Use the net connectivity
data from the script output. Two components are electrically connected only if:
1. They share a wire endpoint at the same coordinate, OR
2. They connect through power symbols (GND, VCC, etc.), OR
3. Their wires share the same NET attribute name

The script computes pin endpoint coordinates by combining:
- Component placement position (x, y) and rotation from the schematic sheet
- Pin offset positions from the symbol definition
- Rotation transform (0°, 90°, 180°, 270°)
- **x-negated fallback** for symbols with inverted pin x-coordinates

### IC Pin Maps — Placed Components Only

The IC pin maps section only includes symbols that are **actually placed on schematic sheets**.
Library-only symbols (e.g., TPS61088 leftover from copying another project) are excluded.
Do NOT assume a component exists in the circuit just because its symbol appears in the
component library. Always verify against the BOM.

### Multi-part Symbol Matching

Some components have `.N` suffixes in their names (e.g., `DTC143ZE.1`). The script
automatically strips these suffixes when matching against symbol library titles.

### BOM Interpretation

Component names in EasyEDA Pro follow patterns:
- **MPN-based**: `0603WAF1803T5E` = manufacturer part number (decode from LCSC/datasheet)
- **Value-based**: `10k`, `100nF`, `47uH` = direct value
- **IC-based**: `TPS61088RHLR`, `BQ24650RVAR` = full MPN with package suffix

Common MPN decoding for resistors (UniOhm/Yageo 0603 series):
- `0603WAFxxxxT5E`: xxxx = resistance code (1803 = 180kΩ, 2002 = 20kΩ, 1002 = 10kΩ, etc.)
- `RC0603FR-07xxxKL`: xxx = resistance value (07 = tolerance code, ignore it)
- `RT0603BRD07xxxKL`: same structure as RC series
- `0805W8FxxxKT5E`: K/M/R/L unit character (K=kΩ, M=MΩ, R=Ω, L=mΩ)
- Standard 4-digit code: first 3 digits = significand, 4th = multiplier (e.g., 1803 = 180×10³ = 180kΩ)

### Power Symbols

Power symbols (GND, 3V3, +12V, +5V, etc.) appear as COMPONENT entries with no Designator.

**CRITICAL**: The component `name` field comes from the **library symbol title**, which may
NOT match the actual net name used in the schematic (e.g., library title `'24v'` but actual
net is `'+12V'`). The script resolves the correct net name by finding the Wire connected to
the power symbol's coordinates and reading that wire's `NET` attribute. It falls back to the
component name only when no wire NET attribute is found (e.g., GND symbols connected purely
by coordinate overlap).

### FB Voltage Divider Auto-Detection

The `--summary` mode automatically detects resistor voltage dividers connected to known ICs:
- **BQ24650** (Vref=2.09V, VFB pin)
- **TPS61088** (Vref=0.6V, FB pin)
- **LGS5145** (Vref=0.8V, FB pin)

**Caution**: ADC voltage dividers (e.g., BAT_VOL, SOL_VOL) sharing GND paths with an IC
may be falsely detected as FB dividers. Verify against the signal net membership data.

### Floating Components

Floating components may be:
- **Intentional**: 0uF reserve pads, mounting holes
- **Wiring errors**: Wire not snapped to pin (verify in EasyEDA)
- **False positives**: Connectors and decoupling caps with coordinate mismatch

## Step 3: Present findings

When presenting analysis results:
1. Group components by the IC/block they connect to (using net data, not position)
2. For each IC block, list connected passives with their function (FB divider, sense R, bypass C, etc.)
3. Flag any values that differ from expected/discussed specifications
4. Show the pin-to-component mapping for key ICs
5. Clearly distinguish actual BOM components from library-only symbols

## Database Schema Reference

Key tables:
- `projects` — Project name, metadata (1 row)
- `schematics` — Schematic info, sheet count
- `documents` — Schematic sheets (docType=1) and PCB (docType=3), contains `dataStr`
- `components` — Symbol/footprint library (docType=2=symbol, 4=footprint, 18=power, 20=frame)
- `devices` — Device definitions linking symbols to footprints
- `attributes` — Component attributes (key-value pairs per device_uuid)

### Document dataStr Format

Each line is a JSON array: `["TYPE", "id", ...params]`

Element types:
- `DOCTYPE` — File type header: `["DOCTYPE", "SCH", "1.1"]`
- `HEAD` — Sheet metadata: `["HEAD", {originX, originY, version, maxId}]`
- `COMPONENT` — Part placement: `["COMPONENT", "id", "name", x, y, rotation, mirror, {}, 0]`
  - rotation: 0, 90, 180, 270 (degrees)
  - mirror: 0 or 1
- `ATTR` — Attribute on component: `["ATTR", "id", "parent_id", "key", "value", ...]`
  - Key attributes: Designator, Footprint, Origin Footprint, Symbol, Device, Name, Supplier Part
  - On WIRE elements: NET (electrical net name)
- `WIRE` — Electrical connection: `["WIRE", "id", [[x1,y1,x2,y2], [x3,y3,x4,y4], ...], "style", 0]`
  - Each sub-array is a line segment [startX, startY, endX, endY]
- `PIN` — Pin definition (in symbol data): `["PIN", "id", 1, null, x, y, length, rotation, ...]`
  - Pin attributes: NAME (pin function), NUMBER (pin number), Pin Type
- `TEXT` — Text annotation: `["TEXT", "id", x, y, rotation, "text", "style", 0]`
- `FONTSTYLE`, `LINESTYLE` — Style definitions
- `POLY` — Polygon/polyline shapes
- `RECT` — Rectangle shapes
- `CIRCLE`, `ELLIPSE` — Circle/ellipse shapes
- `PART` — Part definition header in symbols: `["PART", "name", {BBOX: [...]}]`

### Symbol Pin Coordinate Calculation

To find the absolute position of a pin on the schematic:

```
pin_abs_x, pin_abs_y = rotate(pin_offset_x, pin_offset_y, component_rotation) + (comp_x, comp_y)
```

Where rotation transform for EasyEDA Pro is:
- 0°:   (x, y) → (x, y)
- 90°:  (x, y) → (-y, x)
- 180°: (x, y) → (-x, -y)
- 270°: (x, y) → (y, -x)

Mirror (if mirror=1): negate x before rotation.

Two pins are electrically connected if their absolute coordinates match a wire endpoint.

**Note**: Some symbols have inverted pin x-coordinates. The script handles this by trying
both `(pin_x, pin_y)` and `(-pin_x, pin_y)` offsets, keeping whichever matches a wire.
