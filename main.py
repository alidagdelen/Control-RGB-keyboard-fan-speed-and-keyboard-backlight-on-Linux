"""
Glow Control Center
====================

A terminal (TUI) application for controlling RGB / backlit keyboards on
Linux laptops. Originally written for the ASUS TUF series, it now detects
the best available control method at startup so it also works on other
vendors where a compatible interface exists:

  1. ASUS  - native `asus-nb-wmi` sysfs interface (full RGB, no extra deps)
  2. Any device supported by OpenRGB (e.g. Acer Predator/Nitro's ITE8291
     controller, Clevo, MSI, etc.) - requires the `openrgb` CLI to be
     installed and its daemon/udev rules set up
  3. Generic Linux LED class keyboard backlight - brightness only, no RGB,
     but present on many laptops (Dell, HP, Lenovo, some Acer models) that
     don't expose per-color control

If none of these are available, the app still starts so the interface can
be inspected, but color/brightness changes will simply have no effect on
the hardware.

Author: Dağdelen
License: MIT (c) 2026
"""

from __future__ import annotations

import colorsys
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Footer, Header, Input, Select, Static, TabbedContent, TabPane

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

REFRESH_INTERVAL = 2.0
SPINNER_INTERVAL = 0.1
MAX_BRIGHTNESS = 3
GITHUB_URL = "https://github.com/alidagdelen"

# --------------------------------------------------------------------------
# Logging & configuration
# --------------------------------------------------------------------------

LOG = logging.getLogger("glow-control")
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")

CONFIG_DIR = Path.home() / ".config" / "glow-control"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {"color": "#FFFFFF", "brightness": "3", "fan_profile": ""}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Config could not be read (%s), using defaults", exc)
    return dict(DEFAULT_CONFIG)


def save_config(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        LOG.warning("Config could not be saved: %s", exc)


# --------------------------------------------------------------------------
# Hardware backends
# --------------------------------------------------------------------------

ASUS_KBD_ROOT = "/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight"


def _write_sysfs(path: str, value: str) -> bool:
    try:
        with open(path, "w") as fh:
            fh.write(value)
        return True
    except (PermissionError, FileNotFoundError, OSError) as exc:
        LOG.warning("Write failed for %s: %s", path, exc)
        return False


class KeyboardBackend(ABC):
    """Common interface every hardware backend must implement."""

    name: str = "Unknown"
    supports_rgb: bool = False

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
    """Native control for ASUS laptops exposing the asus-nb-wmi driver
    (TUF, ROG and most recent ASUS models)."""

    name = "ASUS (asus-nb-wmi)"
    supports_rgb = True

    def is_available(self) -> bool:
        return os.path.isdir(ASUS_KBD_ROOT)

    def set_color(self, r: int, g: int, b: int) -> bool:
        return _write_sysfs(f"{ASUS_KBD_ROOT}/kbd_rgb_mode", f"1 0 {r} {g} {b} 0\n")

    def set_brightness(self, level: int) -> bool:
        return _write_sysfs(f"{ASUS_KBD_ROOT}/brightness", f"{level}\n")


class OpenRGBBackend(KeyboardBackend):
    """Generic RGB backend built on top of the OpenRGB CLI. This is what
    makes the app usable on non-ASUS laptops (e.g. Acer Predator/Nitro's
    ITE8291 controller, Clevo, MSI) as long as OpenRGB is installed and
    the device is on its supported-hardware list."""

    name = "OpenRGB"
    supports_rgb = True

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
    """Fallback for laptops with a plain (non-RGB) keyboard backlight."""

    name = "Generic LED class (brightness only)"
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


class NullBackend(KeyboardBackend):
    name = "None detected"
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
            LOG.info("Using backend: %s", backend.name)
            return backend
    return NullBackend()


# --------------------------------------------------------------------------
# Fan control & thermal monitoring
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
    name = "None detected"

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
        LOG.info("Using fan backend: %s", backend.name)
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


# --------------------------------------------------------------------------
# Color presets & widgets
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
    """Animated fan RPM indicator"""

    rotation = reactive(0)

    def render(self) -> str:
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        return f"Fan: {spinner[self.rotation % 10]} RPM"

    def on_mount(self) -> None:
        self.set_interval(SPINNER_INTERVAL, self._rotate)

    def _rotate(self) -> None:
        self.rotation += 1


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class GlowControlApp(App):
    TITLE = "Glow Control Center"
    SUB_TITLE = "RGB & Brightness Controller for Linux Laptops"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    color_hex: reactive[str] = reactive("#FFFFFF")

    CSS = """
    Screen {
        background: $surface;
        color: $text;
    }
    
    Header {
        background: #1a1f2e;
        color: #6366f1;
        text-style: bold;
        height: 2;
    }
    
    #main-container {
        background: #0f1419;
        border: solid #6366f1;
        padding: 1 2;
        height: 1fr;
    }
    
    #tabs {
        height: 1fr;
    }
    
    TabPane {
        padding: 2 3;
    }
    
    .section-title {
        color: #818cf8;
        text-style: bold;
        margin: 1 0;
    }
    
    #preview {
        height: 5;
        border: round #6366f1;
        text-align: center;
        text-style: bold;
        color: #ffffff;
        margin-bottom: 2;
    }
    
    #backend-status {
        color: #10b981;
        text-style: italic;
        margin-bottom: 2;
    }
    
    #button-grid {
        grid-size: 3 4;
        height: 14;
        border: solid #6366f1;
        padding: 1;
        margin-bottom: 2;
    }
    
    #hue-bar {
        height: 4;
        border: solid #6366f1;
        margin-bottom: 1;
    }
    
    #sv-grid {
        height: 12;
        border: solid #6366f1;
        margin-bottom: 2;
    }
    
    Button {
        background: #4f46e5;
        color: #ffffff;
        border: none;
        margin: 0 1;
    }
    
    Button:hover {
        background: #6366f1;
    }
    
    Button.warning {
        background: #f59e0b;
    }
    
    Input {
        border: solid #6366f1;
        background: #0f1419;
        color: #ffffff;
        margin-bottom: 1;
    }
    
    Select {
        border: solid #6366f1;
        margin-bottom: 1;
    }
    
    #fan-status {
        color: #06b6d4;
        text-style: italic;
        border-left: solid #06b6d4;
        padding-left: 1;
        margin-bottom: 2;
    }
    
    #fan-display {
        height: 4;
        border: solid #06b6d4;
        text-align: center;
        color: #06b6d4;
        text-style: bold;
    }
    
    #about-info {
        border: solid #6366f1;
        padding: 1 2;
        color: white;
        height: auto;
        margin-bottom: 2;
    }
    
    Footer {
        background: #0f172a;
        color: #64748b;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.backend: KeyboardBackend = detect_backend()
        self.fan_backend: FanBackend = detect_fan_backend()
        self.config_data = load_config()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="main-container") as container:
            container.border_title = " Glow Control "
            
            with TabbedContent(id="tabs"):
                # ========== LIGHTING TAB ==========
                with TabPane("🎨 Lighting", id="lighting-tab"):
                    yield Static(self.config_data["color"], id="preview")
                    yield Static(self._backend_status_text(), id="backend-status")

                    yield Static("📌 Preset Colors", classes="section-title")
                    with Grid(id="button-grid"):
                        for key, (name, _) in PRESET_COLORS.items():
                            yield Button(name, id=f"preset-{key}")

                    yield Static("🎨 Custom Color Picker", classes="section-title")
                    yield HueBar(id="hue-bar")
                    yield SaturationValueGrid(id="sv-grid")

                    yield Static("💾 Or enter HEX color", classes="section-title")
                    yield Input(placeholder="#FFFFFF", id="custom-color-input")

                    yield Static("💡 Brightness Level", classes="section-title")
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

                # ========== FAN CONTROL TAB ==========
                with TabPane("❄️ Fan Control", id="fan-tab"):
                    yield Static("🌡️ System Temperature & Fans", classes="section-title")
                    yield Static(self._fan_status_text(), id="fan-status")
                    
                    yield Static("")
                    
                    yield Static("💨 Fan Animation", classes="section-title")
                    yield FanDisplay(id="fan-display")
                    
                    yield Static("")
                    
                    yield Static("⚙️ Fan Profile", classes="section-title")
                    fan_profiles = self.fan_backend.get_profiles()
                    if fan_profiles:
                        current = self.fan_backend.get_current_profile() or fan_profiles[0]
                        yield Select(
                            options=[(profile.replace("-", " ").title(), profile) for profile in fan_profiles],
                            value=current,
                            id="fan-profile-select",
                        )
                    else:
                        yield Static(
                            "ℹ️ No fan profile interface detected.",
                            id="fan-profile-unavailable",
                        )

                # ========== SETTINGS TAB ==========
                with TabPane("⚙️ Settings", id="settings-tab"):
                    yield Static("📋 Device Information", classes="section-title")
                    yield Static(self._backend_status_text(), id="backend-status-settings")
                    yield Static(f"Config: {CONFIG_FILE}", id="config-info")
                    
                    yield Static("")
                    yield Static("🔧 Advanced Options", classes="section-title")
                    yield Button("Reset to Defaults", id="reset-button", variant="warning")
                    yield Button("Open Config File", id="open-config-button")

                # ========== ABOUT TAB ==========
                with TabPane("ℹ️ About", id="about-tab"):
                    yield Static("Glow Control Center", classes="section-title")
                    yield Static(
                        "RGB and backlit keyboard controller for Linux laptops.\n"
                        "Originally written for ASUS TUF series, now supports OpenRGB and generic LED.\n\n"
                        "Author: Dağdelen\n"
                        "License: MIT (c) 2026",
                        id="about-info"
                    )
                    yield Button("Open GitHub Repository", id="open-github-button")

        yield Footer()

    def on_mount(self) -> None:
        r, g, b = self._hex_to_rgb(self.config_data["color"])
        self.color_hex = self.config_data["color"]
        if self.backend.supports_rgb:
            self.apply_system_color(r, g, b, persist=False)
        saved_profile = self.config_data.get("fan_profile")
        if saved_profile and saved_profile in self.fan_backend.get_profiles():
            self.fan_backend.set_profile(saved_profile)
        self.set_interval(REFRESH_INTERVAL, self._refresh_fan_status)

    def _backend_status_text(self) -> str:
        if isinstance(self.backend, NullBackend):
            return "Backend: none detected - color/brightness changes will not apply"
        rgb_note = "RGB" if self.backend.supports_rgb else "brightness only"
        return f"Backend: {self.backend.name} ({rgb_note})"

    def _fan_status_text(self) -> str:
        parts = []
        temp = read_cpu_temperature()
        parts.append(f"CPU: {temp:.0f}°C" if temp is not None else "CPU: n/a")
        rpms = read_fan_rpm()
        if rpms:
            parts.append(", ".join(f"Fan {i + 1}: {rpm} RPM" for i, rpm in enumerate(rpms)))
        else:
            parts.append("Fan: n/a")
        return " | ".join(parts)

    def _refresh_fan_status(self) -> None:
        try:
            self.query_one("#fan-status", Static).update(self._fan_status_text())
        except Exception:
            LOG.debug("Fan status widget not mounted yet", exc_info=True)

    @staticmethod
    def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
        hex_code = hex_code.strip().lstrip("#")
        if len(hex_code) != 6:
            return 255, 255, 255
        try:
            return int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
        except ValueError:
            return 255, 255, 255

    def watch_color_hex(self, new_color: str) -> None:
        try:
            preview = self.query_one("#preview", Static)
            preview.styles.background = new_color
            preview.update(f"ACTIVE COLOR: {new_color}")
        except Exception:
            LOG.debug("Preview widget not mounted yet", exc_info=True)

    # -- Event handlers -----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.id:
            return

        if event.button.id.startswith("preset-"):
            key = event.button.id.removeprefix("preset-")
            _, (r, g, b) = PRESET_COLORS[key]
            self.color_hex = f"#{r:02X}{g:02X}{b:02X}"
            self.apply_system_color(r, g, b)
        
        elif event.button.id == "reset-button":
            self.config_data = dict(DEFAULT_CONFIG)
            save_config(self.config_data)
            self.notify("✓ Settings reset to defaults!")

        elif event.button.id == "open-github-button":
            subprocess.run(
                ["xdg-open", GITHUB_URL],
                check=False,
            )       

        elif event.button.id == "open-config-button":
            subprocess.run(["xdg-open", str(CONFIG_FILE)])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hex_code = event.value.strip().lstrip("#")
        if len(hex_code) != 6:
            return
        try:
            r, g, b = (int(hex_code[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return
        self.color_hex = f"#{hex_code.upper()}"
        self.apply_system_color(r, g, b)

    def on_hue_bar_hue_changed(self, message: HueBar.HueChanged) -> None:
        self.query_one("#sv-grid", SaturationValueGrid).hue = message.hue

    def on_saturation_value_grid_color_picked(
        self, message: SaturationValueGrid.ColorPicked
    ) -> None:
        r, g, b = message.rgb
        self.color_hex = f"#{r:02X}{g:02X}{b:02X}"
        self.apply_system_color(r, g, b)
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
            success = self.fan_backend.set_profile(profile)
            if success:
                self.config_data["fan_profile"] = profile
                save_config(self.config_data)
                self.notify(f"✓ Fan profile: {profile}")
            else:
                self.notify("✗ Failed to set fan profile - check permissions", severity="error")

    def apply_system_color(self, r: int, g: int, b: int, persist: bool = True) -> None:
        if self.backend.supports_rgb:
            self.backend.set_color(r, g, b)
        if persist:
            self.config_data["color"] = f"#{r:02X}{g:02X}{b:02X}"
            save_config(self.config_data)


def _relaunch_with_privileges() -> None:
    print("[*] Root privileges are required to control the keyboard backlight.")
    print("[*] Requesting authentication via pkexec...")
    script_path = os.path.abspath(__file__)
    
    # Try pkexec first, fallback to sudo
    for cmd_prefix in (["pkexec"], ["sudo"]):
        cmd = [*cmd_prefix, sys.executable, script_path, *sys.argv[1:]]
        try:
            subprocess.run(cmd, check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    
    print("[-] Authentication failed or was cancelled.")


def apply_saved_settings() -> None:
    config_data = load_config()
    keyboard = detect_backend()
    fan = detect_fan_backend()

    r, g, b = GlowControlApp._hex_to_rgb(config_data["color"])
    if keyboard.supports_rgb:
        keyboard.set_color(r, g, b)
    if str(config_data.get("brightness", "")).isdigit():
        keyboard.set_brightness(int(config_data["brightness"]))

    saved_profile = config_data.get("fan_profile")
    if saved_profile and saved_profile in fan.get_profiles():
        fan.set_profile(saved_profile)

    print("[*] Saved settings applied.")


def main() -> None:
    if os.geteuid() != 0:
        _relaunch_with_privileges()
        sys.exit(0)

    if "--apply" in sys.argv[1:]:
        apply_saved_settings()
        return

    GlowControlApp().run()


if __name__ == "__main__":
    main()
