import ctypes
from ctypes import wintypes
import win32gui
import win32con
from config import log_debug

user32 = ctypes.windll.user32

def apply_overlay_window_styles(hwnd):
    """Aplica atributos Win32 para garantir transparência a cliques e permanência no topo sem roubar foco."""
    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd,
            win32con.GWL_EXSTYLE,
            ex_style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST
        )
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        )
    except Exception as e:
        log_debug(f"Erro ao aplicar estilos Win32 overlay: {e}")

def show_overlay_window(hwnd):
    """Garante que a janela nativa seja exibida sem foco e permaneça topmost no DWM."""
    try:
        apply_overlay_window_styles(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
        )
    except Exception as e:
        log_debug(f"Erro ao exibir janela overlay: {e}")

def hide_overlay_window(hwnd):
    """Garante a remoção física imediata e completa da janela do pipeline de composição do DWM."""
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        win32gui.SetWindowPos(
            hwnd,
            0,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_HIDEWINDOW
        )
    except Exception as e:
        log_debug(f"Erro ao ocultar janela overlay: {e}")

_hook_proc_ref = None

def setup_topmost_event_hook(trigger_callback):
    """Configura um gancho do Windows para reaplicar topmost quando outras janelas ganham foco."""
    global _hook_proc_ref

    def event_callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        try:
            trigger_callback()
        except Exception:
            pass

    WinEventProcType = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD
    )

    _hook_proc_ref = WinEventProcType(event_callback)
    user32.SetWinEventHook(3, 9, 0, _hook_proc_ref, 0, 0, 0)
