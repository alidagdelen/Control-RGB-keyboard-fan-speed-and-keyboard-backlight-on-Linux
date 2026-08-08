"""
Glow Control Center
====================

RGB / backlit keyboard control center for Linux laptops.
Runs via a Terminal User Interface (TUI).

Automatically selects the most appropriate method to interact with hardware on startup:

  1. ASUS     - `asus-nb-wmi` sysfs interface (full RGB support, no extra dependencies)
  2. OpenRGB  - Acer Predator/Nitro (ITE8291), Clevo, MSI and dozens of similar
                devices; requires `openrgb` CLI installed and daemon/udev rules ready
  3. Generic  - Linux LED class keyboard backlight (Dell, HP, Lenovo, some
                Acer models); brightness only, no color support

If no suitable interface is found, the application still launches; color/brightness
changes simply won't affect the hardware.

Introduced in Version 2.0: effect engine (breathing / rainbow / favorite cycle),
favorite colors, automatic brightness dimming based on battery status, hardware rescan,
suppressing unnecessary sudo prompts, and command-line usage.

Author: Dağdelen
License: MIT (c) 2026
"""

from __future__ import annotations

import argparse
import colorsys
import glob
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Grid, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Button, Footer, Header, Input, Select, Static, TabbedContent, TabPane

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

APP_VERSION = "2.0"
FAN_REFRESH_INTERVAL = 2.0
SPINNER_INTERVAL = 0.1
MAX_BRIGHTNESS = 3
MAX_FAVORITES = 8
GITHUB_URL = "https://github.com/alidagdelen"

EFFECT_SPEEDS = {"slow": 0.35, "normal": 0.18, "fast": 0.08}
OPENRGB_MIN_INTERVAL = 0.5  # OpenRGB spawns a new process per tick, prevents rapid firing

LOG = logging.getLogger("glow-control")

CONFIG_DIR = Path.home() / ".config" / "glow-control"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_VERSION = 2

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "color": "#FFFFFF",
    "brightness": "3",
    "fan_profile": "",
    "favorites": [],
    "effect": "off",
    "effect_speed": "normal",
    "auto_dim_on_battery": False,
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            merged = {**DEFAULT_CONFIG, **data}
            merged["config_version"] = CONFIG_VERSION
            return merged
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Failed to read config file (%s), using defaults", exc)
    return dict(DEFAULT_CONFIG)


def save_config(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        LOG.warning("Failed to save settings: %s", exc)


# --------------------------------------------------------------------------
# Keyboard Hardware Backends
# --------------------------------------------------------------------------

ASUS_KBD_ROOT = "/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight"


def _write_sysfs(path: str, value: str) -> bool:
    try:
        with open(path, "w") as fh:
            fh.write(value)
        return True
    except (PermissionError, FileNotFoundError, OSError) as exc:
        LOG.warning("Write failed (%s): %s", path, exc)
        return False


class KeyboardBackend(ABC):
    """Common interface that every hardware backend must implement."""

    name: str = "Unknown"
    supports_rgb: bool = False
    write_interval_floor: float = 0.0  # some layers require a rate limit (e.g., OpenRGB)

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def set_color(self, r: int, g: int, b: int) -> bool:
        ...

    @abstractmethod
    def set_brightness(self, level: int) -> bool:
        ...


class AsusBackend(KeyboardBackend):
    """Direct control using ASUS asus-nb-wmi driver (TUF, ROG, etc.)."""

    name = "ASUS (asus-nb-wmi)"
    supports_rgb = True

    def is_available(self) -> bool:
        return os.path.isdir(ASUS_KBD_ROOT)

    def set_color(self, r: int, g: int, b: int) -> bool:
        return _write_sysfs(f"{ASUS_KBD_ROOT}/kbd_rgb_mode", f"1 0 {r} {g} {b} 0\n")

    def set_brightness(self, level: int) -> bool:
        return _write_sysfs(f"{ASUS_KBD_ROOT}/brightness", f"{level}\n")

    def has_direct_access(self) -> bool:
        return os.access(f"{ASUS_KBD_ROOT}/kbd_rgb_mode", os.W_OK)


class OpenRGBBackend(KeyboardBackend):
    """General RGB support via OpenRGB CLI (ITE8291, Clevo, MSI, ...)."""

    name = "OpenRGB"
    supports_rgb = True
    write_interval_floor = OPENRGB_MIN_INTERVAL

    def __init__(self) -> None:
        self._binary = shutil.which("openrgb")

    def is_available(self) -> bool:
        if not self._binary:
            return False
        try:
            result = subprocess.run(
                [self._binary, "--list-devices"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.SubprocessError, OSError):
            return False

    def set_color(self, r: int, g: int, b: int) -> bool:
        hex_color = f"{r:02X}{g:02X}{b:02X}"
        try:
            subprocess.run(
                [self._binary, "--mode", "static", "--color", hex_color],
                check=True,
                capture_output=True,
                timeout=5,
            )
            return True
        except (subprocess.SubprocessError, OSError) as exc:
            LOG.warning("OpenRGB color change failed: %s", exc)
            return False

    def set_brightness(self, level: int) -> bool:
        return False


class GenericLedBackend(KeyboardBackend):
    """Fallback backend for devices with non-RGB, plain keyboard backlights."""

    name = "Generic LED (brightness only)"
    supports_rgb = False

    def __init__(self) -> None:
        matches = sorted(glob.glob("/sys/class/leds/*kbd_backlight*"))
        self._path: Optional[str] = matches[0] if matches else None

    def is_available(self) -> bool:
        return bool(self._path) and os.path.isfile(f"{self._path}/brightness")

    def set_color(self, r: int, g: int, b: int) -> bool:
        return False

    def set_brightness(self, level: int) -> bool:
        if not self._path:
            return False
        max_level = 3
        try:
            with open(f"{self._path}/max_brightness") as fh:
                max_level = int(fh.read().strip())
        except (FileNotFoundError, ValueError):
            pass
        scaled = round((level / MAX_BRIGHTNESS) * max_level)
        return _write_sysfs(f"{self._path}/brightness", f"{scaled}\n")

    def has_direct_access(self) -> bool:
        return bool(self._path) and os.access(f"{self._path}/brightness", os.W_OK)


class NullBackend(KeyboardBackend):
    name = "No hardware found"
    supports_rgb = False

    def is_available(self) -> bool:
        return True

    def set_color(self, r: int, g: int, b: int) -> bool:
        return False

    def set_brightness(self, level: int) -> bool:
        return False


def detect_backend() -> KeyboardBackend:
    for backend_cls in (AsusBackend, OpenRGBBackend, GenericLedBackend):
        backend = backend_cls()
        if backend.is_available():
            LOG.info("Active backend: %s", backend.name)
            return backend
    return NullBackend()


def has_unelevated_hardware_access() -> bool:
    """Returns True if the user already has unprivileged write access to hardware
    (e.g., via udev rules), preventing unnecessary sudo/pkexec prompts."""
    checks: list[bool] = []
    if os.path.isdir(ASUS_KBD_ROOT):
        checks.append(os.access(f"{ASUS_KBD_ROOT}/kbd_rgb_mode", os.W_OK))
    for path in glob.glob("/sys/class/leds/*kbd_backlight*/brightness"):
        checks.append(os.access(path, os.W_OK))
    if os.path.isfile(PLATFORM_PROFILE_PATH := "/sys/firmware/acpi/platform_profile"):
        checks.append(os.access(PLATFORM_PROFILE_PATH, os.W_OK))
    return bool(checks) and all(checks)


# --------------------------------------------------------------------------
# Fan Control and Thermal Monitoring
# --------------------------------------------------------------------------

PLATFORM_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CHOICES_PATH = "/sys/firmware/acpi/platform_profile_choices"


class FanBackend(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def get_profiles(self) -> list[str]:
        ...

    @abstractmethod
    def get_current_profile(self) -> Optional[str]:
        ...

    @abstractmethod
    def set_profile(self, profile: str) -> bool:
        ...


class PlatformProfileBackend(FanBackend):
    name = "ACPI Platform Profile"

    def is_available(self) -> bool:
        return os.path.isfile(PLATFORM_PROFILE_PATH) and os.path.isfile(
            PLATFORM_PROFILE_CHOICES_PATH
        )

    def get_profiles(self) -> list[str]:
        try:
            with open(PLATFORM_PROFILE_CHOICES_PATH) as fh:
                return fh.read().split()
        except (FileNotFoundError, OSError):
            return []

    def get_current_profile(self) -> Optional[str]:
        try:
            with open(PLATFORM_PROFILE_PATH) as fh:
                return fh.read().strip()
        except (FileNotFoundError, OSError):
            return None

    def set_profile(self, profile: str) -> bool:
        return _write_sysfs(PLATFORM_PROFILE_PATH, f"{profile}\n")


class NullFanBackend(FanBackend):
    name = "Not found"

    def is_available(self) -> bool:
        return True

    def get_profiles(self) -> list[str]:
        return []

    def get_current_profile(self) -> Optional[str]:
        return None

    def set_profile(self, profile: str) -> bool:
        return False


def detect_fan_backend() -> FanBackend:
    backend = PlatformProfileBackend()
    if backend.is_available():
        LOG.info("Active fan backend: %s", backend.name)
        return backend
    return NullFanBackend()


def read_cpu_temperature() -> Optional[float]:
    for zone_type_path in sorted(glob.glob("/sys/class/thermal/thermal_zone*/type")):
        try:
            with open(zone_type_path) as fh:
                zone_type = fh.read().strip().lower()
            if "cpu" in zone_type or "x86_pkg_temp" in zone_type or "soc" in zone_type:
                temp_path = zone_type_path.replace("/type", "/temp")
                with open(temp_path) as fh:
                    return int(fh.read().strip()) / 1000
        except (FileNotFoundError, ValueError, OSError):
            continue
    return None


def read_fan_rpm() -> list[int]:
    speeds: list[int] = []
    for fan_input_path in sorted(glob.glob("/sys/class/hwmon/hwmon*/fan*_input")):
        try:
            with open(fan_input_path) as fh:
                speeds.append(int(fh.read().strip()))
        except (FileNotFoundError, ValueError, OSError):
            continue
    return speeds


def read_battery_status() -> Optional[tuple[str, int]]:
    """Returns (status, percentage); None if no battery. E.g., ('Discharging', 62)."""
    for status_path in sorted(glob.glob("/sys/class/power_supply/BAT*/status")):
        base = os.path.dirname(status_path)
        try:
            with open(status_path) as fh:
                status = fh.read().strip()
            with open(f"{base}/capacity") as fh:
                capacity = int(fh.read().strip())
            return status, capacity
        except (FileNotFoundError, ValueError, OSError):
            continue
    return None


# --------------------------------------------------------------------------
# Color Helpers and Widgets
# --------------------------------------------------------------------------

PRESET_COLORS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "cyan": ("Cyan", (0, 255, 255)),
    "turquoise": ("Turquoise", (64, 224, 208)),
    "sky_blue": ("Sky Blue", (0, 120, 255)),
    "indigo": ("Indigo", (75, 0, 200)),
    "purple": ("Purple", (170, 0, 255)),
    "magenta": ("Magenta", (255, 0, 200)),
    "pink": ("Pink", (255, 105, 180)),
    "red": ("Red", (255, 0, 0)),
    "orange": ("Orange", (255, 110, 0)),
    "amber": ("Amber", (255, 180, 0)),
    "yellow": ("Yellow", (255, 230, 0)),
    "lime": ("Lime", (150, 255, 0)),
    "green": ("Green", (0, 255, 0)),
    "emerald": ("Emerald", (0, 200, 120)),
    "white": ("White", (255, 255, 255)),
    "warm_white": ("Warm White", (255, 214, 170)),
}


def hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    hex_code = hex_code.strip().lstrip("#")
    if len(hex_code) != 6:
        return 255, 255, 255
    try:
        return int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
    except ValueError:
        return 255, 255, 255


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgb_style(r: int, g: int, b: int) -> str:
    return f"on rgb({r},{g},{b})"


class HueBar(Static):
    class HueChanged(Message):
        def __init__(self, hue: float) -> None:
            self.hue = hue
            super().__init__()

    def render(self) -> Text:
        width = max(self.size.width, 1)
        text = Text()
        for x in range(width):
            hue = x / max(width - 1, 1)
            r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1, 1))
            text.append(" ", style=_rgb_style(r, g, b))
        return text

    def on_click(self, event: events.Click) -> None:
        width = max(self.size.width, 1)
        hue = min(max(event.x / max(width - 1, 1), 0.0), 1.0)
        self.post_message(self.HueChanged(hue))


class SaturationValueGrid(Static):
    hue: reactive[float] = reactive(0.0)

    class ColorPicked(Message):
        def __init__(self, r: int, g: int, b: int) -> None:
            self.rgb = (r, g, b)
            super().__init__()

    def watch_hue(self, _value: float) -> None:
        self.refresh()

    def render(self) -> Text:
        width = max(self.size.width, 1)
        height = max(self.size.height, 1)
        text = Text()
        for y in range(height):
            value = 1 - (y / max(height - 1, 1))
            for x in range(width):
                sat = x / max(width - 1, 1)
                r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(self.hue, sat, value))
                text.append(" ", style=_rgb_style(r, g, b))
            if y != height - 1:
                text.append("\n")
        return text

    def on_click(self, event: events.Click) -> None:
        width = max(self.size.width, 1)
        height = max(self.size.height, 1)
        sat = min(max(event.x / max(width - 1, 1), 0.0), 1.0)
        value = 1 - min(max(event.y / max(height - 1, 1), 0.0), 1.0)
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(self.hue, sat, value))
        self.post_message(self.ColorPicked(r, g, b))


class FanDisplay(Static):
    """Simple rotating indicator based on fan status."""

    rotation = reactive(0)
    spinning = reactive(True)

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def render(self) -> str:
        if not self.spinning:
            return "Fan: stopped"
        return f"Fan running {self._FRAMES[self.rotation % len(self._FRAMES)]}"

    def on_mount(self) -> None:
        self.set_interval(SPINNER_INTERVAL, self._rotate)

    def _rotate(self) -> None:
        self.rotation += 1


# --------------------------------------------------------------------------
# Main Application
# --------------------------------------------------------------------------

class GlowControlApp(App):
    TITLE = "Glow Control Center"
    SUB_TITLE = f"Keyboard Backlight & Fan Control for Linux — v{APP_VERSION}"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("r", "rescan", "Rescan Hardware"),
        ("ctrl+s", "save_current", "Save"),
    ]

    color_hex: reactive[str] = reactive("#FFFFFF")
    active_effect: reactive[str] = reactive("off")

    CSS = """
    Screen {
        background: $surface;
        color: $text;
    }

    Header {
        background: #14181f;
        color: #f5a524;
        text-style: bold;
        height: 2;
    }

    #main-container {
        background: #10141b;
        border: solid #f5a524;
        padding: 1 2;
        height: 1fr;
    }

    #tabs {
        height: 1fr;
    }

    TabPane {
        padding: 1 3;
    }

    .section-title {
        color: #f5a524;
        text-style: bold;
        margin: 1 0;
    }

    #preview {
        height: 4;
        border: round #f5a524;
        text-align: center;
        text-style: bold;
        color: #101010;
        margin-bottom: 1;
    }

    #backend-status, #backend-status-settings {
        color: #2dd4bf;
        text-style: italic;
        margin-bottom: 1;
    }

    #button-grid {
        grid-size: 4 4;
        height: 14;
        border: solid #33404f;
        padding: 1;
        margin-bottom: 1;
    }

    #favorites-grid {
        grid-size: 4 2;
        height: 7;
        border: solid #33404f;
        padding: 1;
        margin-bottom: 1;
    }

    #favorites-empty {
        color: #6b7686;
        text-style: italic;
        margin-bottom: 1;
    }

    #hue-bar {
        height: 3;
        border: solid #33404f;
        margin-bottom: 1;
    }

    #sv-grid {
        height: 10;
        border: solid #33404f;
        margin-bottom: 1;
    }

    Button {
        background: #23303f;
        color: #e6ecf3;
        border: none;
        margin: 0 1;
    }

    Button:hover {
        background: #f5a524;
        color: #101010;
    }

    Button.-active {
        background: #f5a524;
        color: #101010;
        text-style: bold;
    }

    Button.warning {
        background: #b45309;
    }

    Input {
        border: solid #33404f;
        background: #10141b;
        color: #e6ecf3;
        margin-bottom: 1;
    }

    Select {
        border: solid #33404f;
        margin-bottom: 1;
    }

    #fan-status {
        color: #2dd4bf;
        text-style: italic;
        border-left: solid #2dd4bf;
        padding-left: 1;
        margin-bottom: 1;
    }

    #battery-status {
        color: #94a3b8;
        border-left: solid #33404f;
        padding-left: 1;
        margin-bottom: 1;
    }

    #fan-display {
        height: 3;
        border: solid #2dd4bf;
        text-align: center;
        color: #2dd4bf;
        text-style: bold;
    }

    #effect-buttons {
        grid-size: 4 1;
        height: 4;
        margin-bottom: 1;
    }

    #about-info {
        border: solid #33404f;
        padding: 1 2;
        color: #e6ecf3;
        height: auto;
        margin-bottom: 1;
    }

    #about-logo {
        color: #f5a524;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    Footer {
        background: #0b0e13;
        color: #6b7686;
    }
    """

    def __init__(self, *, no_elevate: bool = False) -> None:
        super().__init__()
        self.no_elevate = no_elevate
        self.backend: KeyboardBackend = detect_backend()
        self.fan_backend: FanBackend = detect_fan_backend()
        self.config_data = load_config()
        self._effect_timer: Optional[Timer] = None
        self._effect_tick = 0.0
        self._effect_favorite_index = 0

    # -- UI Setup -------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="main-container") as container:
            container.border_title = " Glow Control "

            with TabbedContent(id="tabs"):
                with TabPane("Lighting", id="lighting-tab"):
                    yield Static(self.config_data["color"], id="preview")
                    yield Static(self._backend_status_text(), id="backend-status")

                    yield Static("Preset Colors", classes="section-title")
                    with Grid(id="button-grid"):
                        for key, (name, _) in PRESET_COLORS.items():
                            yield Button(name, id=f"preset-{key}")

                    yield Static("Favorites", classes="section-title")
                    yield from self._build_favorites_row()

                    yield Static("Custom Color Picker", classes="section-title")
                    yield HueBar(id="hue-bar")
                    yield SaturationValueGrid(id="sv-grid")

                    yield Static("Enter HEX code (e.g. #FFAA00)", classes="section-title")
                    yield Input(placeholder="#FFFFFF", id="custom-color-input")

                    yield Static("Brightness", classes="section-title")
                    yield Select(
                        options=[
                            ("Off (0)", "0"),
                            ("Low (1)", "1"),
                            ("Medium (2)", "2"),
                            ("Maximum (3)", "3"),
                        ],
                        value=self.config_data["brightness"],
                        id="brightness-select",
                    )

                with TabPane("Effects", id="effects-tab"):
                    yield Static("Light Effect", classes="section-title")
                    if not self.backend.supports_rgb:
                        yield Static(
                            "This hardware backend does not support RGB; effects only "
                            "work with RGB-supported backends.",
                            id="effects-unavailable",
                        )
                    with Grid(id="effect-buttons"):
                        yield Button("Off", id="effect-off")
                        yield Button("Breathing", id="effect-breathing")
                        yield Button("Rainbow", id="effect-rainbow")
                        yield Button("Favorite Cycle", id="effect-cycle")

                    yield Static("Effect Speed", classes="section-title")
                    yield Select(
                        options=[("Slow", "slow"), ("Normal", "normal"), ("Fast", "fast")],
                        value=self.config_data["effect_speed"],
                        id="effect-speed-select",
                    )

                with TabPane("Fan Control", id="fan-tab"):
                    yield Static("Temperature and Fan Status", classes="section-title")
                    yield Static(self._fan_status_text(), id="fan-status")
                    yield Static(self._battery_status_text(), id="battery-status")

                    yield Static("Fan Animation", classes="section-title")
                    yield FanDisplay(id="fan-display")

                    yield Static("Fan Profile", classes="section-title")
                    fan_profiles = self.fan_backend.get_profiles()
                    if fan_profiles:
                        current = self.fan_backend.get_current_profile() or fan_profiles[0]
                        yield Select(
                            options=[(p.replace("-", " ").title(), p) for p in fan_profiles],
                            value=current,
                            id="fan-profile-select",
                        )
                    else:
                        yield Static(
                            "Fan profile interface not found.",
                            id="fan-profile-unavailable",
                        )

                with TabPane("Settings", id="settings-tab"):
                    yield Static("Device Information", classes="section-title")
                    yield Static(self._backend_status_text(), id="backend-status-settings")
                    yield Static(f"Config file: {CONFIG_FILE}", id="config-info")
                    yield Button("Rescan Hardware", id="rescan-button")

                    yield Static("Power Management", classes="section-title")
                    yield Button(
                        self._battery_dim_label(),
                        id="battery-dim-toggle",
                        classes="-active" if self.config_data["auto_dim_on_battery"] else "",
                    )

                    yield Static("Advanced", classes="section-title")
                    yield Button("Reset to Defaults", id="reset-button", variant="warning")
                    yield Button("Open Config File", id="open-config-button")

                with TabPane("About", id="about-tab"):
                    yield Static("⌨  G L O W   C O N T R O L", id="about-logo")
                    yield Static(
                        f"Version {APP_VERSION}\n\n"
                        "RGB and backlit keyboard control center for Linux laptops. "
                        "Written for ASUS, now also works on OpenRGB-supported "
                        "devices and plain LED backlights.\n\n"
                        "New in this version: light effects, favorite colors, automatic "
                        "brightness dimming on battery, and hardware rescan.\n\n"
                        "Author: Dağdelen\n"
                        "License: MIT (c) 2026",
                        id="about-info",
                    )
                    yield Button("Open GitHub Repository", id="open-github-button")

        yield Footer()

    def _build_favorites_row(self) -> ComposeResult:
        favorites = self.config_data.get("favorites", [])
        if favorites:
            with Grid(id="favorites-grid"):
                for hex_code in favorites:
                    r, g, b = hex_to_rgb(hex_code)
                    yield Button(hex_code, id=f"favorite-{hex_code.lstrip('#')}")
                yield Button("★ Add", id="add-favorite")
                if len(favorites) > 1:
                    yield Button("Clear", id="clear-favorites")
        else:
            yield Static(
                "No favorites yet — pick a color and save it with '★ Add'.",
                id="favorites-empty",
            )
            yield Button("★ Add Current Color", id="add-favorite")

    # -- Lifecycle ------------------------------------------------

    def on_mount(self) -> None:
        r, g, b = hex_to_rgb(self.config_data["color"])
        self.color_hex = self.config_data["color"]
        if self.backend.supports_rgb:
            self.apply_system_color(r, g, b, persist=False)

        saved_profile = self.config_data.get("fan_profile")
        if saved_profile and saved_profile in self.fan_backend.get_profiles():
            self.fan_backend.set_profile(saved_profile)

        self.set_interval(FAN_REFRESH_INTERVAL, self._refresh_status_panels)

        saved_effect = self.config_data.get("effect", "off")
        if saved_effect != "off" and self.backend.supports_rgb:
            self._start_effect(saved_effect)

    # -- Status Texts --------------------------------------------------

    def _backend_status_text(self) -> str:
        if isinstance(self.backend, NullBackend):
            return "Backend: not found — color/brightness changes will not affect hardware"
        rgb_note = "RGB" if self.backend.supports_rgb else "brightness only"
        return f"Backend: {self.backend.name} ({rgb_note})"

    def _fan_status_text(self) -> str:
        parts = []
        temp = read_cpu_temperature()
        parts.append(f"CPU: {temp:.0f}°C" if temp is not None else "CPU: unknown")
        rpms = read_fan_rpm()
        if rpms:
            parts.append(", ".join(f"Fan {i + 1}: {rpm} RPM" for i, rpm in enumerate(rpms)))
        else:
            parts.append("Fan: unknown")
        return " | ".join(parts)

    def _battery_status_text(self) -> str:
        battery = read_battery_status()
        if battery is None:
            return "Battery: none / desktop system"
        status, capacity = battery
        status_map = {"Discharging": "Discharging", "Charging": "Charging", "Full": "Full"}
        status_en = status_map.get(status, status)
        return f"Battery: %{capacity} ({status_en})"

    def _battery_dim_label(self) -> str:
        state = "ENABLED" if self.config_data["auto_dim_on_battery"] else "DISABLED"
        return f"Auto-dim on Battery: {state}"

    def _refresh_status_panels(self) -> None:
        try:
            self.query_one("#fan-status", Static).update(self._fan_status_text())
            self.query_one("#battery-status", Static).update(self._battery_status_text())
        except Exception:
            LOG.debug("Status panels not ready yet", exc_info=True)
        self._apply_battery_auto_dim()

    def _apply_battery_auto_dim(self) -> None:
        if not self.config_data.get("auto_dim_on_battery"):
            return
        battery = read_battery_status()
        if battery is None or battery[0] != "Discharging":
            return
        current = str(self.config_data.get("brightness", "3"))
        if current.isdigit() and int(current) > 1:
            self.backend.set_brightness(1)
            self.notify("Battery mode: keyboard brightness dimmed", timeout=3)

    # -- Color / Brightness --------------------------------------------------

    def watch_color_hex(self, new_color: str) -> None:
        try:
            preview = self.query_one("#preview", Static)
            preview.styles.background = new_color
            preview.update(f"ACTIVE COLOR: {new_color}")
        except Exception:
            LOG.debug("Preview widget not ready yet", exc_info=True)

    def apply_system_color(self, r: int, g: int, b: int, persist: bool = True) -> None:
        if self.backend.supports_rgb:
            self.backend.set_color(r, g, b)
        if persist:
            self.config_data["color"] = rgb_to_hex(r, g, b)
            save_config(self.config_data)

    def _pick_color(self, r: int, g: int, b: int) -> None:
        """Called when user manually picks a color: stops the active effect."""
        if self.active_effect != "off":
            self._stop_effect()
        self.color_hex = rgb_to_hex(r, g, b)
        self.apply_system_color(r, g, b)

    # -- Effect Engine --------------------------------------------------

    def _start_effect(self, effect: str) -> None:
        self._stop_effect(persist=False)
        if not self.backend.supports_rgb:
            self.notify("This hardware backend does not support effects", severity="warning")
            return
        self.active_effect = effect
        self._effect_tick = 0.0
        self._effect_favorite_index = 0

        speed_key = self.config_data.get("effect_speed", "normal")
        interval = EFFECT_SPEEDS.get(speed_key, EFFECT_SPEEDS["normal"])
        interval = max(interval, self.backend.write_interval_floor)

        self._effect_timer = self.set_interval(interval, self._effect_step)
        self.config_data["effect"] = effect
        save_config(self.config_data)
        self._refresh_effect_buttons()

    def _stop_effect(self, persist: bool = True) -> None:
        if self._effect_timer is not None:
            self._effect_timer.stop()
            self._effect_timer = None
        self.active_effect = "off"
        if persist:
            self.config_data["effect"] = "off"
            save_config(self.config_data)
        self._refresh_effect_buttons()

    def _refresh_effect_buttons(self) -> None:
        mapping = {
            "off": "effect-off",
            "breathing": "effect-breathing",
            "rainbow": "effect-rainbow",
            "cycle": "effect-cycle",
        }
        for effect, button_id in mapping.items():
            try:
                button = self.query_one(f"#{button_id}", Button)
                button.set_class(effect == self.active_effect, "-active")
            except Exception:
                pass

    def _effect_step(self) -> None:
        self._effect_tick += 1
        base_r, base_g, base_b = hex_to_rgb(self.color_hex)

        if self.active_effect == "breathing":
            phase = (math.sin(self._effect_tick * 0.25) + 1) / 2  # 0..1
            level = 0.15 + phase * 0.85
            r, g, b = (int(base_r * level), int(base_g * level), int(base_b * level))
            self.backend.set_color(r, g, b)

        elif self.active_effect == "rainbow":
            hue = (self._effect_tick * 0.02) % 1.0
            r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1, 1))
            self.backend.set_color(r, g, b)

        elif self.active_effect == "cycle":
            favorites = self.config_data.get("favorites") or list(
                f"#{r:02X}{g:02X}{b:02X}" for _, (_, (r, g, b)) in list(PRESET_COLORS.items())[:4]
            )
            if self._effect_tick % 20 == 0:
                self._effect_favorite_index = (self._effect_favorite_index + 1) % len(favorites)
            r, g, b = hex_to_rgb(favorites[self._effect_favorite_index])
            self.backend.set_color(r, g, b)

    # -- Favorites --------------------------------------------------------

    def _rebuild_favorites_row(self) -> None:
        try:
            old = self.query_one("#favorites-grid")
            parent = old.parent
            old.remove()
        except Exception:
            try:
                old = self.query_one("#favorites-empty")
                parent = old.parent
                old.remove()
                try:
                    self.query_one("#add-favorite").remove()
                except Exception:
                    pass
            except Exception:
                return
        for widget in self._build_favorites_row():
            parent.mount(widget)

    def _add_current_to_favorites(self) -> None:
        favorites: list[str] = self.config_data.setdefault("favorites", [])
        if self.color_hex in favorites:
            self.notify("This color is already in favorites", severity="warning")
            return
        favorites.append(self.color_hex)
        if len(favorites) > MAX_FAVORITES:
            favorites.pop(0)
        save_config(self.config_data)
        self._rebuild_favorites_row()
        self.notify(f"{self.color_hex} added to favorites")

    def _clear_favorites(self) -> None:
        self.config_data["favorites"] = []
        save_config(self.config_data)
        self._rebuild_favorites_row()
        self.notify("Favorites cleared")

    # -- Hardware Rescan --------------------------------------------

    def action_rescan(self) -> None:
        self._stop_effect(persist=False)
        self.backend = detect_backend()
        self.fan_backend = detect_fan_backend()
        for selector in ("#backend-status", "#backend-status-settings"):
            try:
                self.query_one(selector, Static).update(self._backend_status_text())
            except Exception:
                pass
        r, g, b = hex_to_rgb(self.color_hex)
        if self.backend.supports_rgb:
            self.backend.set_color(r, g, b)
        self.notify(f"Rescanned: {self.backend.name}")

    def action_save_current(self) -> None:
        save_config(self.config_data)
        self.notify("Settings saved")

    # -- Event Handlers --------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if not button_id:
            return

        if button_id.startswith("preset-"):
            key = button_id.removeprefix("preset-")
            _, (r, g, b) = PRESET_COLORS[key]
            self._pick_color(r, g, b)

        elif button_id.startswith("favorite-"):
            hex_code = "#" + button_id.removeprefix("favorite-")
            r, g, b = hex_to_rgb(hex_code)
            self._pick_color(r, g, b)

        elif button_id == "add-favorite":
            self._add_current_to_favorites()

        elif button_id == "clear-favorites":
            self._clear_favorites()

        elif button_id == "effect-off":
            self._stop_effect()
        elif button_id == "effect-breathing":
            self._start_effect("breathing")
        elif button_id == "effect-rainbow":
            self._start_effect("rainbow")
        elif button_id == "effect-cycle":
            self._start_effect("cycle")

        elif button_id == "rescan-button":
            self.action_rescan()

        elif button_id == "battery-dim-toggle":
            new_state = not self.config_data["auto_dim_on_battery"]
            self.config_data["auto_dim_on_battery"] = new_state
            save_config(self.config_data)
            event.button.label = self._battery_dim_label()
            event.button.set_class(new_state, "-active")
            self.notify(f"Auto-dim on battery {'enabled' if new_state else 'disabled'}")

        elif button_id == "reset-button":
            self._stop_effect(persist=False)
            self.config_data = dict(DEFAULT_CONFIG)
            save_config(self.config_data)
            self.notify("Settings reset to defaults")

        elif button_id == "open-github-button":
            subprocess.run(["xdg-open", GITHUB_URL], check=False)

        elif button_id == "open-config-button":
            subprocess.run(["xdg-open", str(CONFIG_FILE)], check=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hex_code = event.value.strip().lstrip("#")
        if len(hex_code) != 6:
            self.notify("Invalid HEX code (e.g. #FFAA00)", severity="error")
            return
        try:
            r, g, b = (int(hex_code[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            self.notify("Invalid HEX code (e.g. #FFAA00)", severity="error")
            return
        self._pick_color(r, g, b)

    def on_hue_bar_hue_changed(self, message: HueBar.HueChanged) -> None:
        self.query_one("#sv-grid", SaturationValueGrid).hue = message.hue

    def on_saturation_value_grid_color_picked(
        self, message: SaturationValueGrid.ColorPicked
    ) -> None:
        r, g, b = message.rgb
        self._pick_color(r, g, b)
        try:
            self.query_one("#custom-color-input", Input).value = self.color_hex
        except Exception:
            pass

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "brightness-select":
            if event.value is None or not str(event.value).isdigit():
                return
            level = int(str(event.value))
            self.backend.set_brightness(level)
            self.config_data["brightness"] = str(level)
            save_config(self.config_data)

        elif event.select.id == "fan-profile-select":
            if not event.value:
                return
            profile = str(event.value)
            if self.fan_backend.set_profile(profile):
                self.config_data["fan_profile"] = profile
                save_config(self.config_data)
                self.notify(f"Fan profile: {profile}")
            else:
                self.notify("Failed to set fan profile — check permissions", severity="error")

        elif event.select.id == "effect-speed-select":
            self.config_data["effect_speed"] = str(event.value)
            save_config(self.config_data)
            if self.active_effect != "off":
                self._start_effect(self.active_effect)  # restart with new speed


# --------------------------------------------------------------------------
# CLI / Entry Point
# --------------------------------------------------------------------------

def _relaunch_with_privileges(extra_args: list[str]) -> None:
    print("[*] Administrator privileges required to control keyboard backlight.")
    print("[*] Requesting authentication via pkexec / sudo...")
    script_path = os.path.abspath(__file__)

    for cmd_prefix in (["pkexec"], ["sudo"]):
        cmd = [*cmd_prefix, sys.executable, script_path, *extra_args]
        try:
            subprocess.run(cmd, check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    print("[-] Authentication failed or was cancelled.")


def apply_saved_settings() -> None:
    """Applies saved settings to hardware without opening the UI (--apply)."""
    config_data = load_config()
    keyboard = detect_backend()
    fan = detect_fan_backend()

    r, g, b = hex_to_rgb(config_data["color"])
    if keyboard.supports_rgb:
        keyboard.set_color(r, g, b)
    if str(config_data.get("brightness", "")).isdigit():
        keyboard.set_brightness(int(config_data["brightness"]))

    saved_profile = config_data.get("fan_profile")
    if saved_profile and saved_profile in fan.get_profiles():
        fan.set_profile(saved_profile)

    print("[*] Saved settings applied.")


def list_backends() -> None:
    keyboard = detect_backend()
    fan = detect_fan_backend()
    print(f"Keyboard backend : {keyboard.name} (RGB: {'yes' if keyboard.supports_rgb else 'no'})")
    print(f"Fan backend      : {fan.name}")
    if fan.get_profiles():
        print(f"Fan profiles     : {', '.join(fan.get_profiles())}")
    battery = read_battery_status()
    print(f"Battery          : {'%' + str(battery[1]) + ' ' + battery[0] if battery else 'none'}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glow-control",
        description="RGB / backlit keyboard control center for Linux laptops.",
    )
    parser.add_argument("--version", action="version", version=f"glow-control {APP_VERSION}")
    parser.add_argument("--color", metavar="HEX", help="Set keyboard color and exit (e.g. FF00AA)")
    parser.add_argument(
        "--brightness", metavar="0-3", type=int, choices=range(0, 4), help="Set brightness and exit"
    )
    parser.add_argument(
        "--effect",
        choices=["off", "breathing", "rainbow", "cycle"],
        help="Set saved effect (starts when UI opens)",
    )
    parser.add_argument("--list-backends", action="store_true", help="List detected hardware backends and exit")
    parser.add_argument("--apply", action="store_true", help="Apply saved settings to hardware and exit")
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help="Run without root privileges (if udev rules already grant access)",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list_backends:
        list_backends()
        return

    if args.color or args.brightness is not None or args.effect:
        config_data = load_config()
        if args.color:
            config_data["color"] = f"#{args.color.lstrip('#').upper()}"
        if args.brightness is not None:
            config_data["brightness"] = str(args.brightness)
        if args.effect:
            config_data["effect"] = args.effect
        save_config(config_data)
        apply_saved_settings()
        return

    if args.apply:
        apply_saved_settings()
        return

    no_elevate = args.no_elevate or os.environ.get("GLOW_CONTROL_NO_ELEVATE") == "1"
    if os.geteuid() != 0 and not no_elevate and not has_unelevated_hardware_access():
        _relaunch_with_privileges(sys.argv[1:])
        sys.exit(0)

    GlowControlApp(no_elevate=no_elevate).run()


if __name__ == "__main__":
    main()
