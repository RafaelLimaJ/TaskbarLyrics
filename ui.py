import os
import time
import urllib.request
import threading
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSystemTrayIcon,
    QMenu, QApplication, QPushButton, QPlainTextEdit, QDialog
)
from PyQt6.QtGui import QPainter, QPainterPath, QColor, QFont, QFontMetrics, QPixmap, QIcon, QAction, QTransform
from PyQt6.QtCore import Qt, QTimer, QRectF, pyqtSignal, QObject
import win32gui
import win32con
from config import CARD_WIDTH, CARD_HEIGHT, load_saved_position, log_debug, LOG_FILE, load_translate_setting, save_translate_setting
from win32_utils import apply_overlay_window_styles, show_overlay_window, hide_overlay_window

def is_window_truly_visible(widget):
    """Verifica se a janela está visível tanto no estado do Qt quanto no pipeline nativo do Win32."""
    if not widget:
        return False
    if widget.isVisible():
        return True
    try:
        hwnd = int(widget.winId())
        if hwnd and win32gui.IsWindowVisible(hwnd):
            return True
    except Exception:
        pass
    return False

class UISignals(QObject):
    artwork_loaded = pyqtSignal(bytes)
    force_topmost = pyqtSignal()
    stop_playback = pyqtSignal()

GLOBAL_SIGNALS = UISignals()

class KaraokeLineWidget(QWidget):
    """Widget de texto com renderização dupla, slide-up animado e preenchimento karaokê a 60 FPS."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_text = ""
        self.prev_text = ""
        self.next_text = ""
        self.progress = 0.0
        self.is_card_mode = False
        self.is_translate_mode = False

        self.transition_start_time = 0.0
        self.transition_duration = 0.24  # 240ms cubic ease

        self.font_main = QFont("SF Pro Display", 11, QFont.Weight.Bold)
        self.font_main.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.font_symbol = QFont("Segoe UI Emoji", 13, QFont.Weight.Bold)
        self.font_symbol.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.font_next = QFont("SF Pro Display", 9, QFont.Weight.DemiBold)
        self.font_next.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

    def set_state(self, current_text, next_text, progress, is_card_mode, is_translate_mode=False):
        text_changed = (self.current_text != current_text)

        if text_changed and not is_card_mode and not self.is_card_mode and self.current_text:
            self.prev_text = self.current_text
            self.transition_start_time = time.perf_counter()

        if (text_changed or
            self.next_text != next_text or
            self.is_card_mode != is_card_mode or
            self.is_translate_mode != is_translate_mode or
            abs(self.progress - progress) > 0.002):
            self.current_text = current_text
            self.next_text = next_text
            self.progress = max(0.0, min(1.0, progress))
            self.is_card_mode = is_card_mode
            self.is_translate_mode = is_translate_mode
            self.update()

    def _draw_text_with_shadow(self, painter, x, y, text, font, text_color, shadow_alpha=160):
        """Desenha texto com sombra suave para legibilidade perfeita sobre fundos claros."""
        painter.setFont(font)
        # Sombra sutil projetada para contraste instantâneo
        if shadow_alpha > 0:
            painter.setPen(QColor(0, 0, 0, shadow_alpha))
            painter.drawText(x + 1, y + 1, text)
        painter.setPen(text_color)
        painter.drawText(x, y, text)

    def paintEvent(self, event):
        if not self.current_text and not self.next_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        line_y = 16
        avail_w = self.width()

        # MODO CARD LIMPO (Título + Artista)
        if self.is_card_mode:
            self._draw_text_with_shadow(painter, 0, line_y, self.current_text, self.font_main, QColor(255, 255, 255, 255), 180)
            if self.next_text:
                self._draw_text_with_shadow(painter, 0, line_y + 16, self.next_text, self.font_next, QColor(255, 255, 255, 185), 150)
            painter.end()
            return

        # TRANSIÇÃO ANIMADA SUBINDO (SLIDE-UP)
        now = time.perf_counter()
        elapsed = now - self.transition_start_time
        in_transition = (elapsed < self.transition_duration and self.prev_text)

        if in_transition:
            t = min(1.0, elapsed / self.transition_duration)
            ease = 1.0 - (1.0 - t) ** 3
            delta_y = int(16 * ease)

            # 1. Linha anterior sobe e desaparece
            prev_alpha = int(255 * (1.0 - ease))
            if prev_alpha > 5:
                s_alpha = int(150 * (1.0 - ease))
                self._draw_text_with_shadow(painter, 0, 16 - delta_y, self.prev_text, self.font_main, QColor(255, 255, 255, prev_alpha), s_alpha)

            # 2. Nova linha sobe para a posição principal
            curr_y = 32 - delta_y
            curr_alpha = int(150 + 105 * ease)
            s_alpha = int(180 * ease)
            is_note_trans = (self.current_text.strip() == "♪")
            active_font_trans = self.font_symbol if is_note_trans else self.font_main
            self._draw_text_with_shadow(painter, 0, curr_y, self.current_text, active_font_trans, QColor(255, 255, 255, curr_alpha), s_alpha)

            # Preenchimento enquanto sobe
            if self.progress > 0.0:
                fm = QFontMetrics(active_font_trans)
                tw = fm.horizontalAdvance(self.current_text)
                if tw > 0:
                    if is_note_trans:
                        note_h = 24
                        fill_h = int(note_h * self.progress)
                        clip_y = curr_y - fill_h + 4
                        painter.save()
                        painter.setClipRect(-2, clip_y, tw + 8, fill_h + 4)
                        painter.setFont(active_font_trans)
                        painter.setPen(QColor(255, 255, 255, 255))
                        painter.drawText(0, curr_y, self.current_text)
                        painter.restore()
                    else:
                        act_w = int(tw * self.progress)
                        painter.save()
                        painter.setClipRect(0, curr_y - 14, act_w, 22)
                        painter.setFont(active_font_trans)
                        painter.setPen(QColor(255, 255, 255, 255))
                        painter.drawText(0, curr_y, self.current_text)
                        painter.restore()


            # 3. Próximo verso entra embaixo (ou tradução com karaokê)
            if self.next_text:
                is_note_trans_next = (self.next_text.strip() == "♪")
                font_trans_next = self.font_symbol if is_note_trans_next else self.font_next
                next_y = int(38 - 6 * ease)
                next_alpha = int(175 * ease)
                s_alpha = int(140 * ease)
                self._draw_text_with_shadow(painter, 0, next_y, self.next_text, font_trans_next, QColor(255, 255, 255, next_alpha), s_alpha)

                # Karaokê na segunda linha APENAS se o modo de tradução estiver ativado
                if self.is_translate_mode and self.progress > 0.0:
                    fm_n = QFontMetrics(font_trans_next)
                    tw_n = fm_n.horizontalAdvance(self.next_text)
                    if tw_n > 0:
                        if is_note_trans_next:
                            note_h_n = 20
                            fill_h_n = int(note_h_n * self.progress)
                            clip_y_n = next_y - fill_h_n + 3
                            painter.save()
                            painter.setClipRect(-2, clip_y_n, tw_n + 8, fill_h_n + 4)
                            painter.setFont(font_trans_next)
                            painter.setPen(QColor(255, 255, 255, 255))
                            painter.drawText(0, next_y, self.next_text)
                            painter.restore()
                        else:
                            act_w_n = int(tw_n * self.progress)
                            painter.save()
                            painter.setClipRect(0, next_y - 12, act_w_n, 20)
                            painter.setFont(font_trans_next)
                            painter.setPen(QColor(255, 255, 255, 255))
                            painter.drawText(0, next_y, self.next_text)
                            painter.restore()

            painter.end()
            return

        # MODO KARAOKÊ ESTÁVEL
        is_note = (self.current_text.strip() == "♪")
        active_font = self.font_symbol if is_note else self.font_main

        fm = QFontMetrics(active_font)
        text_w = fm.horizontalAdvance(self.current_text)

        offset_x = 0
        if text_w > avail_w:
            max_scroll = text_w - avail_w + 14
            offset_x = -int(max_scroll * self.progress)

        painter.save()
        painter.translate(offset_x, 0)
        # Linha principal com sombra para garantir leitura
        self._draw_text_with_shadow(painter, 0, line_y, self.current_text, active_font, QColor(255, 255, 255, 140), 160)

        if self.progress > 0.0 and text_w > 0:
            if is_note:
                # Efeito Copo (enchia igual um copo: de baixo para cima)
                # Altura total do caractere da nota
                note_h = 24
                fill_h = int(note_h * self.progress)
                clip_y = line_y - fill_h + 4
                painter.setClipRect(-2, clip_y, text_w + 8, fill_h + 4)
                painter.setFont(active_font)
                painter.setPen(QColor(255, 255, 255, 255))
                painter.drawText(0, line_y, self.current_text)
            else:
                # Texto normal preenche da esquerda para a direita
                active_w = int(text_w * self.progress)
                painter.setClipRect(0, 0, active_w, 24)
                painter.setFont(active_font)
                painter.setPen(QColor(255, 255, 255, 255))
                painter.drawText(0, line_y, self.current_text)
        painter.restore()

        # 2. SEGUNDA LINHA (Tradução com karaokê em branco puro OU Próximo Verso estático)
        if self.next_text:
            is_note_next = (self.next_text.strip() == "♪")
            font_line2 = self.font_symbol if is_note_next else self.font_next
            line2_y = line_y + 16

            fm2 = QFontMetrics(font_line2)
            tw2 = fm2.horizontalAdvance(self.next_text)

            offset2_x = 0
            if tw2 > avail_w:
                max_scroll2 = tw2 - avail_w + 14
                offset2_x = -int(max_scroll2 * self.progress)

            painter.save()
            painter.translate(offset2_x, 0)
            # Linha de fundo secundária (translúcida suave)
            self._draw_text_with_shadow(painter, 0, line2_y, self.next_text, font_line2, QColor(255, 255, 255, 140), 120)

            # Efeito Karaokê na segunda linha APENAS se estiver com o modo de tradução ativado
            if self.is_translate_mode and self.progress > 0.0 and tw2 > 0:
                if is_note_next:
                    # Efeito copo na nota musical secundária
                    note_h2 = 20
                    fill_h2 = int(note_h2 * self.progress)
                    clip_y2 = line2_y - fill_h2 + 3
                    painter.setClipRect(-2, clip_y2, tw2 + 8, fill_h2 + 4)
                    painter.setFont(font_line2)
                    painter.setPen(QColor(255, 255, 255, 255))
                    painter.drawText(0, line2_y, self.next_text)
                else:
                    # Preenchimento branco puro destacando simultaneamente a tradução
                    act_w2 = int(tw2 * self.progress)
                    painter.setClipRect(0, line2_y - 14, act_w2, 22)
                    painter.setFont(font_line2)
                    painter.setPen(QColor(255, 255, 255, 255))
                    painter.drawText(0, line2_y, self.next_text)
            painter.restore()

        painter.end()

def create_tray_icon():
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(QColor(255, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 28, 28)

    painter.setFont(QFont("Segoe UI Symbol", 13, QFont.Weight.Bold))
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QRectF(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, "♪")
    painter.end()
    return QIcon(pix)

class LogViewerDialog(QDialog):
    """Janela de visualização e monitoramento de logs em tempo real com auto-scroll."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TaskbarLyrics - Logs do Sistema em Tempo Real")
        self.resize(780, 500)
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #e4e4e7;
                font-family: 'Segoe UI', sans-serif;
            }
            QPlainTextEdit {
                background-color: #18181b;
                color: #d4d4d8;
                font-family: 'Consolas', 'Cascadia Code', monospace;
                font-size: 11px;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 10px;
                line-height: 1.4;
            }
            QPushButton {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
            QPushButton#btnOpenNotepad {
                background-color: #2563eb;
                color: #ffffff;
                border: 1px solid #3b82f6;
            }
            QPushButton#btnOpenNotepad:hover {
                background-color: #1d4ed8;
            }
            QLabel {
                font-size: 12px;
                color: #d4d4d8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header com status e botão de abrir arquivo
        header = QHBoxLayout()
        self.lbl_status = QLabel("🟢 Monitor de Logs Ativo")
        self.lbl_status.setStyleSheet("color: #22c55e; font-weight: bold; font-size: 13px;")
        header.addWidget(self.lbl_status)
        header.addStretch()

        self.btn_notepad = QPushButton("📂 Abrir Arquivo no Bloco de Notas")
        self.btn_notepad.setObjectName("btnOpenNotepad")
        self.btn_notepad.clicked.connect(self.open_in_notepad)
        header.addWidget(self.btn_notepad)
        layout.addLayout(header)

        # Área de texto dos logs
        self.log_text = QPlainTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        layout.addWidget(self.log_text)

        # Botões inferiores
        bottom = QHBoxLayout()
        self.btn_clear = QPushButton("Limpar Visualização")
        self.btn_clear.clicked.connect(self.log_text.clear)
        bottom.addWidget(self.btn_clear)

        self.btn_copy = QPushButton("Copiar Todos os Logs")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        bottom.addWidget(self.btn_copy)

        bottom.addStretch()

        self.btn_close = QPushButton("Fechar")
        self.btn_close.clicked.connect(self.hide)
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

        self.file_pos = 0
        self.load_initial_logs()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_new_logs)
        self.poll_timer.start(250)

    def load_initial_logs(self):
        try:
            if os.path.exists(LOG_FILE):
                size = os.path.getsize(LOG_FILE)
                read_start = max(0, size - 32768)
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(read_start)
                    content = f.read()
                    self.file_pos = f.tell()
                self.log_text.setPlainText(content)
                self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        except Exception:
            pass

    def poll_new_logs(self):
        try:
            if not self.isVisible():
                return
            if os.path.exists(LOG_FILE):
                curr_size = os.path.getsize(LOG_FILE)
                if curr_size < self.file_pos:
                    self.file_pos = 0
                if curr_size > self.file_pos:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self.file_pos)
                        new_content = f.read()
                        self.file_pos = f.tell()
                    if new_content:
                        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
                        self.log_text.insertPlainText(new_content)
                        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        except Exception:
            pass

    def open_in_notepad(self):
        try:
            os.startfile(LOG_FILE)
        except Exception as e:
            log_debug(f"Erro ao abrir LOG_FILE: {e}")

    def copy_to_clipboard(self):
        QApplication.clipboard().setText(self.log_text.toPlainText())

class TaskbarLyricsWidget(QWidget):
    """Janela principal do TaskbarLyrics com efeito vidro, capa e persistência de topo."""

    def __init__(self, state_manager):
        super().__init__()
        self.state = state_manager
        self.current_art_url = ""
        self.log_viewer = None

        GLOBAL_SIGNALS.artwork_loaded.connect(self.on_artwork_loaded)
        GLOBAL_SIGNALS.force_topmost.connect(self.enforce_topmost)
        GLOBAL_SIGNALS.stop_playback.connect(self.on_stop_playback)

        self.init_ui()
        self.init_tray()
        self.apply_saved_position()

        # Timer de renderização a 60 FPS (16ms)
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self.on_render_tick)
        self.render_timer.start(16)

        # Timer de verificação de topmost e timeout de inatividade
        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self.check_status)
        self.topmost_timer.start(150)

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QHBoxLayout()
        layout.setContentsMargins(6, 4, 10, 4)
        layout.setSpacing(10)

        self.lbl_art = QLabel()
        self.lbl_art.setFixedSize(36, 36)
        self.lbl_art.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 6px;")
        self.lbl_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_default_art()

        self.karaoke_widget = KaraokeLineWidget(self)

        layout.addWidget(self.lbl_art)
        layout.addWidget(self.karaoke_widget, 1)
        self.setLayout(layout)

        self.resize(CARD_WIDTH, CARD_HEIGHT)
        self.hide_window()

    def show_window(self):
        self.show()
        try:
            hwnd = int(self.winId())
            show_overlay_window(hwnd)
        except Exception:
            pass

    def hide_window(self):
        self.hide()
        try:
            hwnd = int(self.winId())
            hide_overlay_window(hwnd)
        except Exception:
            pass

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(create_tray_icon())
        self.tray.setToolTip("TaskbarLyrics")

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #202020;
                color: #FFFFFF;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #FF0000;
                color: #FFFFFF;
            }
        """)

        title_action = QAction("🎵 TaskbarLyrics (Ativo)", self)
        title_action.setEnabled(False)
        menu.addAction(title_action)
        menu.addSeparator()

        # Opção de Tradução para Português (PT-BR)
        init_trans = load_translate_setting()
        self.state.set_translate_enabled(init_trans)

        self.translate_action = QAction("🌐 Traduzir para Português (PT)", self)
        self.translate_action.setCheckable(True)
        self.translate_action.setChecked(init_trans)
        self.translate_action.triggered.connect(self.on_toggle_translate)
        menu.addAction(self.translate_action)
        menu.addSeparator()

        logs_action = QAction("📜 Ver Logs em Tempo Real", self)
        logs_action.triggered.connect(self.show_log_viewer)
        menu.addAction(logs_action)

        open_file_action = QAction("📂 Abrir TaskbarLyrics.log", self)
        open_file_action.triggered.connect(self.open_log_file_direct)
        menu.addAction(open_file_action)
        menu.addSeparator()

        quit_action = QAction("❌ Fechar Letras", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_toggle_translate(self, checked):
        save_translate_setting(checked)
        self.state.set_translate_enabled(checked)
        log_debug(f"[UI] Tradução alternada pelo usuário no menu: {checked}")

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_log_viewer()

    def show_log_viewer(self):
        if not self.log_viewer:
            self.log_viewer = LogViewerDialog(None)
        self.log_viewer.show()
        self.log_viewer.raise_()
        self.log_viewer.activateWindow()

    def open_log_file_direct(self):
        try:
            os.startfile(LOG_FILE)
        except Exception as e:
            log_debug(f"Erro ao abrir arquivo de log: {e}")

    def apply_saved_position(self):
        x, y = load_saved_position()
        self.move(x, y)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)

        painter.save()
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor(12, 12, 12, 110))
        painter.restore()

        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.end()

        super().paintEvent(event)

    def set_default_art(self):
        pix = QPixmap(36, 36)
        pix.fill(QColor(25, 28, 38))
        self.lbl_art.setPixmap(self.get_rounded_pixmap(pix))

    def get_rounded_pixmap(self, src_pixmap):
        scaled = src_pixmap.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        rounded = QPixmap(36, 36)
        rounded.fill(Qt.GlobalColor.transparent)

        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 36, 36, 6, 6)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        return rounded

    def enforce_topmost(self):
        if not self.state.has_track or not self.state.is_playing:
            return
        if not is_window_truly_visible(self):
            return
        try:
            hwnd = int(self.winId())
            apply_overlay_window_styles(hwnd)
        except Exception:
            pass

    def check_status(self):
        truly_visible = is_window_truly_visible(self)

        if self.state.has_track:
            if self.state.is_timed_out():
                self.state.has_track = False
                if truly_visible:
                    log_debug("[UI] Ocultando widget por inatividade (timeout 10s)")
                    self.hide_window()
                return

            if self.state.is_playing:
                if not truly_visible:
                    log_debug("[UI] Exibindo widget (reprodução ativa)")
                    self.show_window()
                self.enforce_topmost()
            else:
                if truly_visible:
                    log_debug("[UI] Ocultando widget (reprodução pausada)")
                    self.hide_window()
        else:
            if truly_visible:
                log_debug("[UI] Ocultando widget (sem faixa ativa)")
                self.hide_window()

    def on_artwork_loaded(self, img_bytes):
        pix = QPixmap()
        if pix.loadFromData(img_bytes):
            self.lbl_art.setPixmap(self.get_rounded_pixmap(pix))

    def on_stop_playback(self):
        log_debug("[UI] Sinal de parada recebido.")
        self.state.has_track = False
        self.state.is_playing = False
        self.hide_window()

    def trigger_artwork_download(self, url):
        if url and url != self.current_art_url:
            self.current_art_url = url
            def fetch():
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        GLOBAL_SIGNALS.artwork_loaded.emit(resp.read())
                except Exception:
                    pass
            threading.Thread(target=fetch, daemon=True).start()

    def on_render_tick(self):
        if not self.state.has_track or not self.state.is_playing:
            return
        if not is_window_truly_visible(self):
            return

        l1, l2, prog, is_card = self.state.get_display_state()
        is_trans = getattr(self.state, "translate_enabled", False)
        self.karaoke_widget.set_state(l1, l2, prog, is_card, is_trans)
