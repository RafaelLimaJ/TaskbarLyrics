import os
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase
from config import FONTS_DIR, log_debug
from player_state import PlayerStateManager
from ui import TaskbarLyricsWidget, GLOBAL_SIGNALS
from win32_utils import setup_topmost_event_hook
from server import start_server_thread

def load_fonts():
    if os.path.exists(FONTS_DIR):
        for font_file in os.listdir(FONTS_DIR):
            if font_file.endswith((".otf", ".ttf")):
                font_path = os.path.join(FONTS_DIR, font_file)
                font_id = QFontDatabase.addApplicationFont(font_path)
                families = QFontDatabase.applicationFontFamilies(font_id)
                log_debug(f"[App] Fonte carregada: {font_file} -> {families}")

def main():
    log_debug("=== TaskbarLyrics v3.0 Iniciado ===")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    load_fonts()

    state = PlayerStateManager()
    widget = TaskbarLyricsWidget(state)

    setup_topmost_event_hook(lambda: GLOBAL_SIGNALS.force_topmost.emit())
    start_server_thread(state, widget)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
