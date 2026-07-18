# TUF-Glow-RGB 🌈

A lightweight Terminal User Interface (TUI) for controlling the RGB keyboard backlight and brightness on ASUS TUF Gaming laptops running Linux.

Built with **Python** and **Textual**.

---

## ✨ Features

- 🎨 Preset RGB colors
- 🌈 Custom HEX color input
- 💡 Keyboard brightness control
- 🖥️ Modern terminal interface
- 🔒 Automatic privilege request with `pkexec`
- 🐧 Native Linux support

---

## 📸 Screenshot

> Coming soon...

---

## 📋 Requirements

- Linux
- Python 3.10+
- ASUS TUF Gaming laptop with RGB keyboard
- `pkexec`
- `textual`

---

## 🚀 Installation

Clone the repository:

```bash
git clone https://github.com/alidagdelen/tuf-glow-rgb.git
cd tuf-glow-rgb
```

Create a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
python3 main.py
```

---

## 🎮 Controls

| Action | Description |
|--------|-------------|
| Color Buttons | Apply preset RGB colors |
| HEX Input | Apply any custom RGB color |
| Brightness | Change keyboard brightness |
| Ctrl + C | Exit the application |

---

## 🧩 Supported Hardware

Currently tested on:

- ASUS TUF Gaming F16 (FX607VU)

Other ASUS TUF models may work if they expose the following Linux sysfs paths:

```
/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/
```

---

## 📦 Dependencies

- Python
- Textual

Install manually:

```bash
pip install textual
```

or

```bash
pip install -r requirements.txt
```

---

## 📅 Roadmap

- [x] Preset colors
- [x] Custom HEX colors
- [x] Brightness control
- [ ] RGB effects
- [ ] Color profiles
- [ ] Configuration file
- [ ] Automatic hardware detection
- [ ] Packaging (.deb)

---

## 🤝 Contributing

Pull requests, issues and suggestions are welcome.

---

## 📄 License

This project is licensed under the MIT License.

---

## 👨‍💻 Author

**Ali Dağdelen**

GitHub:
https://github.com/alidagdelen
