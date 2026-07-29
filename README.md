# Glow Control Center

RGB and backlit keyboard controller for Linux laptops.

## Overview

Glow Control Center is a terminal-based application that provides unified control over RGB and backlit keyboards on Linux laptops. Originally developed for ASUS TUF series, it now supports multiple hardware backends and gracefully falls back to generic LED control when specialized drivers aren't available.

## Features

- RGB color control with visual color picker
- Brightness adjustment (0-3 levels)
- Hardware backend detection and auto-selection
- System temperature monitoring
- Fan RPM reading
- ACPI platform profile (fan mode) control
- Configuration persistence
- Preset color library
- Runs on multiple laptop manufacturers

## Supported Hardware

### Primary Backends

1. **ASUS (native asus-nb-wmi)**
   - Full RGB support
   - Works on ASUS TUF, ROG, and recent ASUS models
   - No external dependencies

2. **OpenRGB**
   - Generic RGB support via OpenRGB daemon
   - Compatible with: Acer Predator/Nitro (ITE8291), Clevo, MSI, and more
   - Requires: openrgb CLI and daemon running

3. **Generic LED (brightness-only)**
   - Fallback for laptops with standard keyboard backlight
   - Works on Dell, HP, Lenovo, some Acer models
   - No color control, brightness only

## Installation

### Requirements

- Python 3.10+
- textual library
- rich library
- Linux kernel with backlight support
- Root/sudo privileges for hardware control

### Setup

```bash
# Install dependencies
pip install textual rich

# Make executable
chmod +x glow-control.py

# Run (will prompt for sudo/root)
python3 glow-control.py
```

### Optional: OpenRGB Support

```bash
# Install OpenRGB from your package manager
sudo apt install openrgb  # Debian/Ubuntu
sudo pacman -S openrgb    # Arch

# Start OpenRGB daemon
openrgb --startasdaemon
```

## Usage

### Launch Application

```bash
sudo python3 glow-control.py
```

The app will auto-detect your hardware and use the best available backend.

### Interface

The application has 4 tabs:

#### Lighting Tab
- Preset colors (16 built-in options)
- Interactive color picker (hue + saturation/value grid)
- Manual HEX color input
- Brightness slider (Off, Low, Medium, Maximum)

#### Fan Control Tab
- Real-time CPU temperature
- Fan RPM display
- Fan mode selection (if ACPI platform profile available)

#### Settings Tab
- Active backend information
- Config file location
- Reset to defaults button
- Open config file button

#### About Tab
- Project information
- GitHub link

### Keyboard Shortcuts

- `Ctrl+C` - Quit application

## Configuration

Settings are stored in `~/.config/glow-control/config.json`

```json
{
  "color": "#FFFFFF",
  "brightness": "3",
  "fan_profile": ""
}
```

Automatically updated when you change settings in the app. Can be edited manually between sessions.

## Command Line Usage

Apply saved settings without opening the UI:

```bash
sudo python3 glow-control.py --apply
```

Useful for startup scripts or automation.

## Troubleshooting

### "Backend: none detected"

Your laptop model isn't detected. Check:
- ASUS models: verify `/sys/devices/platform/asus-nb-wmi/` exists
- OpenRGB: run `openrgb --list-devices` to verify
- Generic LED: check `/sys/class/leds/` for backlight devices

### Colors not changing

- Ensure app is run with `sudo`
- Check backend detection (Settings tab)
- Verify your kernel supports the backlight interface
- Try running with `--apply` to apply saved settings at boot

### Permission denied errors

Some systems require additional udev rules for non-root access. For now, running with sudo is the simplest solution.

## License

MIT (c) 2026

Author: Dağdelen

## Contributing

Found an issue? Want to improve something? Open an issue or pull request on GitHub.

## Acknowledgments

- Textual framework for the TUI
- OpenRGB project for cross-device RGB support
