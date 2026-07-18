# TUF-Glow 🌟

A sleek, modern Terminal User Interface (TUI) to control the RGB keyboard backlight and brightness levels on **Asus TUF Gaming** series laptops running Linux. Built with Python and the Textual framework.

---

## Features ✨

* **Preset Colors:** Quick-access buttons for popular colors (Cyan, Turquoise, Blue, Red, Green, Purple, White).
* **Custom HEX Input:** Type any custom HEX color code (e.g., `#FF5500`) and hit Enter to apply instantly.
* **Brightness Control:** A precise dropdown selector to change keyboard brightness levels from Off (0) to Maximum (3).
* **Modern UI:** A clean, responsive dark-themed interface built for the terminal.

---

## Prerequisites 📋

This application interacts directly with Linux kernel attributes (`sysfs`). It requires access to the following paths:
* `/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/kbd_rgb_mode`
* `/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/brightness`

---

## Installation & Setup 🚀

### Method 1: Using a Virtual Environment (Recommended)

1. Clone the repository:
   ```bash
   git clone [https://github.com/yourusername/tuf-glow.git](https://github.com/yourusername/tuf-glow.git)
   cd tuf-glow
