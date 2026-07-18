from textual.app import App, ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Button, Header, Footer, Static, Input, Select
from textual.reactive import reactive
import subprocess
import sys
import os

COLORS = {
    "cyan": ("Cyan", (0, 255, 255)),
    "turquoise": ("Turquoise", (64, 224, 208)),
    "blue": ("Blue", (0, 120, 255)),
    "red": ("Red", (255, 0, 0)),
    "green": ("Green", (0, 255, 0)),
    "purple": ("Purple", (170, 0, 255)),
    "white": ("White", (255, 255, 255)),
}

class TUFGlowApp(App):
    """Asus TUF RGB & Brightness Controller."""
    
    TITLE = "TUF-Glow"
    SUB_TITLE = "Asus TUF RGB & Brightness Controller"
    
    BINDINGS = [
        ("ctrl+c", "quit", "Dağdelen | MIT License © 2026")
    ]

    color_hex = reactive("#FFFFFF")

    CSS = """
    Screen {
        align: center middle;
        background: #0f172a;
    }

    #main-container {
        width: 65;
        height: auto;
        border: solid #38bdf8;
        background: #1e293b;
        padding: 1 2;
        border-title-align: center;
    }

    #preview {
        height: 3;
        content-align: center middle;
        color: #0f172a;
        background: white;
        margin-bottom: 1;
        text-style: bold;
        border: inner #64748b;
    }

    .section-title {
        text-style: bold;
        color: #38bdf8;
        margin-top: 1;
        margin-bottom: 0;
    }

    #button-grid {
        grid-size: 2;
        grid-gutter: 1;
        height: auto;
        margin-bottom: 1;
    }

    Button {
        width: 100%;
        background: #334155;
        color: #f8fafc;
        border: none;
    }

    Button:hover {
        background: #475569;
        text-style: bold;
    }

    Input {
        background: #334155;
        border: solid #64748b;
        color: white;
        margin-bottom: 1;
    }

    Select {
        background: #334155;
        border: solid #64748b;
        margin-bottom: 1;
    }

    Footer {
        background: #0f172a;
        color: #64748b;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with Vertical(id="main-container") as v:
            v.border_title = " TUF Control Center "
            
            yield Static(self.color_hex, id="preview")
            
            yield Static("Preset Colors", classes="section-title")
            with Grid(id="button-grid"):
                for key, (name, _) in COLORS.items():
                    yield Button(name, id=key)
            
            yield Static("Custom HEX Color (e.g., #FF5500)", classes="section-title")
            yield Input(placeholder="#FFFFFF", id="custom-color-input")
            
            yield Static("Keyboard Brightness Level", classes="section-title")
            yield Select(
                options=[
                    ("Off (0)", "0"),
                    ("Low (1)", "1"),
                    ("Medium (2)", "2"),
                    ("Maximum (3)", "3")
                ],
                value="3",
                id="brightness-select"
            )
                    
        yield Footer()

    def watch_color_hex(self, new_color: str) -> None:
        try:
            preview = self.query_one("#preview", Static)
            preview.styles.background = new_color
            preview.update(f"ACTIVE COLOR: {new_color}")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        name, (r, g, b) = COLORS[event.button.id]
        self.color_hex = f"#{r:02X}{g:02X}{b:02X}"
        self.apply_system_color(r, g, b)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hex_code = event.value.strip().lstrip('#')
        
        if len(hex_code) == 6:
            try:
                r = int(hex_code[0:2], 16)
                g = int(hex_code[2:4], 16)
                b = int(hex_code[4:6], 16)
                
                self.color_hex = f"#{hex_code.upper()}"
                self.apply_system_color(r, g, b)
            except ValueError:
                pass

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value and str(event.value).isdigit():
            brightness_value = int(event.value)
            cmd = f'echo "{brightness_value}" > /sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/brightness'
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError:
                pass

    def apply_system_color(self, r: int, g: int, b: int) -> None:
        cmd = f'echo "1 0 {r} {g} {b} 0" > /sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/kbd_rgb_mode'
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError:
            pass


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("[*] Requesting root privileges. Please enter your password in the authentication prompt...")
        # os.path.abspath(__file__) kullanarak dosyanın tam konumunu root kullanıcısına iletiyoruz
        script_path = os.path.abspath(__file__)
        cmd = ["pkexec", sys.executable, script_path] + sys.argv[1:]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print("[-] Authentication failed. Root privileges are required to modify keyboard state.")
        sys.exit(0)

    TUFGlowApp().run()