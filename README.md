# TaskbarLyrics

A sleek, lightweight, and hardware-accelerated synced lyrics and karaoke overlay embedded directly into the Windows taskbar, built with Python (PyQt6 + Win32 API) and integrated in real-time with YouTube Music via a local WebSocket bridge.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d7.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen.svg)
![Render](https://img.shields.io/badge/render-60%20FPS%20Fluid-red.svg)

---

## Features

- **Dual-Line Real-Time Karaoke (60 FPS)**: Ultra-smooth character-by-character lyrics fill and animated slide-up verse transitions.
- **Bilingual Portuguese (PT) Translation**:
  - Optional instant toggle from the system tray menu ("Traduzir para Portugues (PT)").
  - Translates foreign lyrics to Portuguese on the secondary line in real time without interfering with the original lyrics.
  - Symmetrical musical solo indicator handling and simultaneous dual-line karaoke fill.
- **Natural Dynamic Cadence**:
  - Intelligent syllabic timing: fast phrasing on connector words and melodic vowel sustaining at the end of verses.
  - Eliminates artificial instrumental cutoffs on held vocal notes.
- **Windows Taskbar Native Integration**:
  - Transparent, frameless overlay using low-level Win32 hooks (WS_EX_LAYERED, WS_EX_TRANSPARENT, topmost persistence).
  - Auto-hides on pause/stop and appears instantly on playback.
- **Zero-Latency Local WebSocket Bridge**: Direct communication between the browser extension and desktop widget at ws://127.0.0.1:5678.
- **Multi-Source Synced Lyrics Engine**: High-speed concurrent lookups (LRCLIB, Musixmatch) with in-memory RAM caching.

---

## Installation & Setup

### 1. Prerequisites
- Windows 10 / 11
- Python 3.10+ (with pip)
- Google Chrome / Brave / Edge (for the YouTube Music extension)

### 2. Clone the Repository
```bash
git clone https://github.com/RafaelLimaJ/TaskbarLyrics.git
cd TaskbarLyrics
```

### 3. Install Python Dependencies
```bash
pip install PyQt6 pywin32 websockets requests
```

### 4. Install Browser Extension
1. Open your Chromium-based browser and navigate to `chrome://extensions/`.
2. Enable Developer mode (top-right corner).
3. Click "Load unpacked" and select the `chrome_extension` folder located inside this repository.
4. Pin the extension or open [YouTube Music](https://music.youtube.com).

---

## Running TaskbarLyrics

- **Standard Launch**:
  Double-click `Iniciar_Letras.bat` or run:
  ```bash
  python app.py
  ```

- **Silent Background Launch (No Terminal)**:
  Double-click `Iniciar_Silencioso.vbs`.

- **To Stop**:
  Right-click the note icon in the system tray and select "Fechar Letras", or run `Parar_Letras.bat`.

---

## System Tray Menu

Right-click the TaskbarLyrics tray icon near the Windows clock:
- **TaskbarLyrics (Ativo)**: Displays current status.
- **Traduzir para Portugues (PT)**: Toggle on/off real-time Portuguese translations.
- **Ver Logs em Tempo Real**: Opens the live debug log monitor.
- **Abrir TaskbarLyrics.log**: Opens the persistent log file.
- **Fechar Letras**: Exits the application cleanly.

---

## License
This project is licensed under the MIT License.
