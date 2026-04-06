# clipboard_monitor.py
# Monitoramento de área de transferência com histórico e modo jogo.
# Baseado em zPortapapeles (Héctor J. Benítez Corredera), corrigido para NVDA 2024+.
# Correção principal: setSpeechMode agora usa SpeechMode enum (não inteiro).

import ctypes
import os
import sys
from ctypes import c_size_t as SIZE_T
from ctypes.wintypes import BOOL, HWND, HANDLE, HGLOBAL, UINT, LPVOID
from threading import Thread, Lock, Event
from time import sleep

import wx
import nvwave
import ui
from logHandler import log
from tones import beep

# ── Acesso direto ao clipboard via WinAPI ──────────────────────────────────────

_OpenClipboard   = ctypes.windll.user32.OpenClipboard;   _OpenClipboard.argtypes   = (HWND,); _OpenClipboard.restype   = BOOL
_EmptyClipboard  = ctypes.windll.user32.EmptyClipboard;  _EmptyClipboard.restype   = BOOL
_GetClipboardData = ctypes.windll.user32.GetClipboardData; _GetClipboardData.argtypes = (UINT,); _GetClipboardData.restype = HANDLE
_SetClipboardData = ctypes.windll.user32.SetClipboardData; _SetClipboardData.argtypes = (UINT, HANDLE); _SetClipboardData.restype = HANDLE
_CloseClipboard  = ctypes.windll.user32.CloseClipboard;  _CloseClipboard.restype   = BOOL
_EnumClipboard   = ctypes.windll.user32.EnumClipboardFormats
_GetFormatName   = ctypes.windll.user32.GetClipboardFormatNameW
_GlobalAlloc     = ctypes.windll.kernel32.GlobalAlloc;   _GlobalAlloc.argtypes = (UINT, SIZE_T); _GlobalAlloc.restype = HGLOBAL
_GlobalLock      = ctypes.windll.kernel32.GlobalLock;    _GlobalLock.argtypes  = (HGLOBAL,); _GlobalLock.restype  = LPVOID
_GlobalUnlock    = ctypes.windll.kernel32.GlobalUnlock;  _GlobalUnlock.argtypes = (HGLOBAL,)
_GlobalSize      = ctypes.windll.kernel32.GlobalSize;    _GlobalSize.argtypes   = (HGLOBAL,); _GlobalSize.restype  = SIZE_T

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE  = 0x0002
_GMEM_ZEROINIT  = 0x0040


def clean():
    """Limpa a área de transferência."""
    try:
        _OpenClipboard(None)
        _EmptyClipboard()
        _CloseClipboard()
    except Exception:
        pass


def get():
    """Retorna o texto Unicode da área de transferência, ou None."""
    text = None
    try:
        _OpenClipboard(None)
        handle = _GetClipboardData(_CF_UNICODETEXT)
        ptr = _GlobalLock(handle)
        size = _GlobalSize(handle)
        if ptr and size:
            raw = ctypes.create_string_buffer(size)
            ctypes.memmove(raw, ptr, size)
            text = raw.raw.decode('utf-16le').rstrip('\x00')
        _GlobalUnlock(handle)
        _CloseClipboard()
    except Exception:
        pass
    return text


def put(text):
    """Coloca texto Unicode na área de transferência."""
    if not isinstance(text, str):
        text = text.decode('mbcs')
    data = text.encode('utf-16le')
    try:
        _OpenClipboard(None)
        _EmptyClipboard()
        handle = _GlobalAlloc(_GMEM_MOVEABLE | _GMEM_ZEROINIT, len(data) + 2)
        ptr = _GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        _GlobalUnlock(handle)
        _SetClipboardData(_CF_UNICODETEXT, handle)
        _CloseClipboard()
    except Exception:
        pass


def has_text():
    """Retorna True se a área de transferência contém texto Unicode."""
    result = False
    try:
        if _OpenClipboard(None):
            fmt = 0
            buf = (ctypes.c_wchar * 64)()
            while True:
                fmt = _EnumClipboard(fmt)
                if fmt == 0:
                    break
                _GetFormatName(fmt, buf, len(buf))
                if buf.value == 'CF_UNICODETEXT' or fmt == _CF_UNICODETEXT:
                    result = True
                    break
            _CloseClipboard()
    except Exception:
        pass
    return result


# ── Funções auxiliares de fala ─────────────────────────────────────────────────

def _announce(text):
    """Anuncia texto na thread principal do wx."""
    wx.CallAfter(ui.message, text)


# ── Módulo-nível: função de tradução injetada pelo __init__.py ─────────────────
# O __init__.py chama set_game_translate_func() com a função de tradução.
_game_translate_func = None

def set_game_translate_func(func):
    """Define a função de tradução para o modo jogo.
    func(text, lang_to) -> str  (bloqueia até traduzir, pode lançar exceção)
    """
    global _game_translate_func
    _game_translate_func = func


# ── Sons ───────────────────────────────────────────────────────────────────────
_sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")

SOUND_FILES = {
    "copy":      "copy.wav",
    "cut":       "cut.wav",
    "paste":     "paste.wav",
    "selectAll": "selectAll.wav",
    "undo":      "undo.wav",
    "redo":      "redo.wav",
    "history":   "history.wav",
}

def play_sound(name):
    """Reproduz um arquivo de som do diretório sounds/."""
    filename = SOUND_FILES.get(name)
    if not filename:
        return
    path = os.path.join(_sounds_dir, filename)
    if os.path.exists(path):
        nvwave.playWaveFile(path)
    else:
        beep(1000, 50)


# ── Mapeamento de gestos para ações ───────────────────────────────────────────

GESTURE_ACTIONS = {
    "control+c": ("copy",      lambda: _("Copiar")),
    "control+x": ("cut",       lambda: _("Recortar")),
    "control+v": ("paste",     lambda: _("Colar")),
    "control+z": ("undo",      lambda: _("Desfazer")),
    "control+y": ("redo",      lambda: _("Refazer")),
    "control+a": ("selectAll", lambda: _("Selecionar tudo")),
    "control+e": ("selectAll", lambda: _("Selecionar tudo")),
}


# ── ClipMonitor: histórico ─────────────────────────────────────────────────────

class ClipMonitor(Thread):
    """Thread que monitora a área de transferência e mantém histórico."""

    MAX_HISTORY = 50

    def __init__(self, interval=0.2, announce_copied=False, sound_on_add=False):
        super().__init__(daemon=True)
        self.interval       = interval
        self.announce_copied = announce_copied
        self.sound_on_add   = sound_on_add
        self._history       = []
        self._last_deleted  = ""
        self._temp          = ""
        self._lock          = Lock()
        self._running       = True
        self._paused        = False
        self._pending_announce = None

    def run(self):
        while self._running:
            try:
                clip = get()
                with self._lock:
                    if not self._paused and clip:
                        self._process(clip)
            except OSError:
                pass
            sleep(self.interval)

    def _process(self, clip):
        """Processa novo conteúdo do clipboard (deve ser chamado com lock)."""
        if clip == self._last_deleted:
            return
        if clip == self._temp:
            return
        if self._history and self._history[0] == clip:
            return
        if clip in self._history:
            # Já existe: só mover para o topo
            self._history.remove(clip)
        self._history.insert(0, clip)
        if len(self._history) > self.MAX_HISTORY:
            del self._history[self.MAX_HISTORY:]
        if self.sound_on_add:
            wx.CallAfter(play_sound, "history")
        if self.announce_copied:
            wx.CallAfter(ui.message, clip)

    @property
    def history(self):
        with self._lock:
            return list(self._history)

    def delete(self, index):
        with self._lock:
            if 0 <= index < len(self._history):
                self._last_deleted = self._history[index]
                del self._history[index]

    def delete_all(self):
        with self._lock:
            if self._history:
                self._last_deleted = self._history[0]
            self._history.clear()

    def set_temp(self, text):
        """Marca um texto como 'temporário' para não adicionar ao histórico."""
        with self._lock:
            self._temp = text

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False

    def set_interval(self, interval):
        with self._lock:
            self.interval = interval

    def kill(self):
        self._running = False


# ── ClipMonitorGame: modo jogo (traduz clipboard automaticamente) ──────────────

class ClipMonitorGame(Thread):
    """Thread que monitora clipboard e traduz automaticamente o novo conteúdo."""

    def __init__(self, interval=0.2, lang_to='en'):
        super().__init__(daemon=True)
        self.interval  = interval
        self.lang_to   = lang_to
        self._last     = ""
        self._running  = True
        self._paused   = True   # começa pausado; ativa com resume()
        self._lock     = Lock()

    def run(self):
        while self._running:
            try:
                clip = get()
                with self._lock:
                    if not self._paused and clip and clip != self._last:
                        self._last = clip
                        lang = self.lang_to
                        Thread(target=self._translate_and_announce, args=(clip, lang), daemon=True).start()
            except OSError:
                pass
            sleep(self.interval)

    def _translate_and_announce(self, text, lang):
        if _game_translate_func is None:
            return
        try:
            result = _game_translate_func(text, lang)
            if result:
                wx.CallAfter(ui.message, result)
        except Exception as e:
            log.debugWarning(f"translateClipboard/game mode: erro ao traduzir: {e}")

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False

    def set_interval(self, interval):
        with self._lock:
            self.interval = interval

    def set_lang(self, lang):
        with self._lock:
            self.lang_to = lang

    def kill(self):
        self._running = False
