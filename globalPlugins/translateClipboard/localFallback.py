# -*- coding: utf-8 -*-
# localFallback.py
# Offline translation fallback for the translateClipboard NVDA add-on.
#
# When every Google endpoint fails (HTTP 429, no network), the add-on asks a small local model
# (Tencent Hy-MT2-1.8B, GGUF, Apache-2.0) served by llama.cpp's llama-server on 127.0.0.1.
# The server is started on demand and stopped after an idle period, so it costs nothing while
# Google works. Standard library only (NVDA 2026.x ships Python 3.13, 64-bit); no admin rights.
#
# Folder layout (base_dir), outside the add-on folder so an add-on update does not wipe ~1 GB:
#   <NVDA config dir>\translateClipboard-offline\
#       runtime\llama-server.exe (+ its DLLs, from the llama.cpp win-cpu-x64 zip)
#       models\Hy-MT2-1.8B-Q4_K_M.gguf   (any *.gguf works; the first in sorted order is used)
# The NVDA config dir is globalVars.appArgs.configPath, which already points inside the portable
# folder when NVDA runs portable, so the same code covers installed and portable NVDA.

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from logHandler import log
except ImportError:  # outside NVDA (tests, launcher.py)
    import logging
    log = logging.getLogger("translateClipboard.localFallback")

FOLDER_NAME = "translateClipboard-offline"
SERVER_EXE = "llama-server.exe" if os.name == "nt" else "llama-server"

# Hy-MT2 model card: no system prompt; these sampling values for 1.8B and 7B.
SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05}
# Hy-MT2 "default translation" English prompt. Full English language names, per the model card.
PROMPT = ("Translate the following text into {lang}. Note that you should only output the "
    "translated result without any additional explanation:\n\n{text}")

# ISO code (as used by the add-on / Google) -> English name, for the 33 Hy-MT2 languages.
LANG_NAMES = {
    "zh": "Chinese", "zh-CN": "Chinese", "zh-TW": "Traditional Chinese", "zh-Hant": "Traditional Chinese",
    "en": "English", "fr": "French", "pt": "Portuguese", "pt-BR": "Brazilian Portuguese",
    "pt-PT": "European Portuguese", "es": "Spanish", "ja": "Japanese", "tr": "Turkish",
    "ru": "Russian", "ar": "Arabic", "ko": "Korean", "th": "Thai", "it": "Italian", "de": "German",
    "vi": "Vietnamese", "ms": "Malay", "id": "Indonesian", "tl": "Filipino", "fil": "Filipino",
    "hi": "Hindi", "pl": "Polish", "cs": "Czech", "nl": "Dutch", "km": "Khmer", "my": "Burmese",
    "fa": "Persian", "gu": "Gujarati", "ur": "Urdu", "te": "Telugu", "mr": "Marathi",
    "he": "Hebrew", "iw": "Hebrew", "bn": "Bengali", "ta": "Tamil", "uk": "Ukrainian",
    "bo": "Tibetan", "kk": "Kazakh", "mn": "Mongolian", "ug": "Uyghur", "yue": "Cantonese",
}

IDLE_SECONDS = 300      # stop the server after 5 min without a request
START_TIMEOUT = 60      # model load on a slow disk; normal is a few seconds
REQUEST_TIMEOUT = 60    # one chunk of text on a slow CPU
CTX_SIZE = 4096         # model supports 8192; 4096 keeps RAM low and fits the add-on's 3000-char chunks


class FallbackUnavailable(Exception):
    """The local fallback is not installed, failed to start or did not answer."""


def default_base_dir():
    """<NVDA config dir>\\translateClipboard-offline (portable-aware). None outside NVDA."""
    try:
        import globalVars
        return os.path.join(globalVars.appArgs.configPath, FOLDER_NAME)
    except Exception:
        return None


def lang_name(code):
    """English language name for a code, or None if Hy-MT2 does not support it."""
    if not code:
        return None
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    return LANG_NAMES.get(code.split("-")[0].split("_")[0].lower())


def build_prompt(text, lang_to):
    name = lang_name(lang_to)
    if name is None:
        raise FallbackUnavailable("idioma sem suporte no modelo local: %s" % lang_to)
    return PROMPT.format(lang=name, text=text)


def clean_output(text):
    """Trim what small models sometimes add around the translation."""
    text = (text or "").strip()
    for tag in ("<|im_end|>", "</s>", "<|eos|>"):
        text = text.replace(tag, "")
    return text.strip()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_install(base_dir):
    """(server_exe, model_path) or None if either is missing."""
    if not base_dir:
        return None
    exe = os.path.join(base_dir, "runtime", SERVER_EXE)
    models_dir = os.path.join(base_dir, "models")
    try:
        models = sorted(f for f in os.listdir(models_dir) if f.lower().endswith(".gguf"))
    except OSError:
        return None
    if not os.path.isfile(exe) or not models:
        return None
    return exe, os.path.join(models_dir, models[0])


def server_command(exe, model, port, threads=None):
    """llama-server command line. CPU only (-ngl 0); bound to 127.0.0.1."""
    if threads is None:
        # Leave one core for NVDA and the foreground app (a game, the browser).
        threads = max(1, (os.cpu_count() or 2) - 1)
    return [
        exe, "--model", model, "--host", "127.0.0.1", "--port", str(port),
        "--ctx-size", str(CTX_SIZE), "--threads", str(threads), "-ngl", "0",
        "--parallel", "1", "--no-webui", "--jinja",
    ]


class LocalServer:
    """Starts llama-server on demand, stops it after IDLE_SECONDS without use.

    command_factory(port) -> argv list is injectable for tests; by default it comes from
    find_install(base_dir) + server_command().
    """

    def __init__(self, base_dir=None, command_factory=None, idle_seconds=IDLE_SECONDS,
            start_timeout=START_TIMEOUT, request_timeout=REQUEST_TIMEOUT):
        self.base_dir = base_dir if base_dir is not None else default_base_dir()
        self._command_factory = command_factory
        self.idle_seconds = idle_seconds
        self.start_timeout = start_timeout
        self.request_timeout = request_timeout
        self._proc = None
        self.port = None
        self._lock = threading.RLock()
        self._last_use = 0.0
        self._idle_timer = None

    # ── availability ──────────────────────────────────────────────────────────

    def installed(self):
        return self._command_factory is not None or find_install(self.base_dir) is not None

    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def _command(self, port):
        if self._command_factory is not None:
            return self._command_factory(port)
        found = find_install(self.base_dir)
        if found is None:
            raise FallbackUnavailable("modelo local não instalado em %s" % self.base_dir)
        return server_command(found[0], found[1], port)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def ensure_started(self):
        with self._lock:
            if self.running():
                return
            self._stop_locked()
            port = free_port()
            cmd = self._command(port)
            kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL, "cwd": os.path.dirname(cmd[0]) or None}
            if os.name == "nt":
                # No console window; lowest-but-one priority so speech never stutters.
                kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW
                    | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            try:
                self._proc = subprocess.Popen(cmd, **kwargs)
            except OSError as e:
                self._proc = None
                raise FallbackUnavailable("não abriu o servidor local: %s" % e)
            self.port = port
            log.info("translateClipboard: servidor local iniciado (pid %s, porta %s)"
                % (self._proc.pid, port))
            deadline = time.monotonic() + self.start_timeout
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    code = self._proc.returncode
                    self._proc = None
                    raise FallbackUnavailable("servidor local fechou ao iniciar (código %s)" % code)
                if self._healthy():
                    self._touch()
                    return
                time.sleep(0.25)
            self._stop_locked()
            raise FallbackUnavailable("servidor local não ficou pronto em %s s" % self.start_timeout)

    def _healthy(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%s/health" % self.port, timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            # 503 while the model loads arrives as HTTPError (a URLError subclass)
            return False

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            log.info("translateClipboard: servidor local parado")

    def _touch(self):
        self._last_use = time.monotonic()
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self.idle_seconds, self._idle_check)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _idle_check(self):
        with self._lock:
            if time.monotonic() - self._last_use >= self.idle_seconds - 0.05:
                self._stop_locked()

    # ── requests ──────────────────────────────────────────────────────────────

    def chat(self, prompt, max_tokens):
        """One OpenAI-compatible chat completion; returns the assistant text."""
        self.ensure_started()
        body = dict(SAMPLING, messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens, stream=False)
        req = urllib.request.Request(
            "http://127.0.0.1:%s/v1/chat/completions" % self.port,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise FallbackUnavailable("servidor local não respondeu: %s" % e)
        finally:
            with self._lock:
                if self.running():
                    self._touch()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise FallbackUnavailable("resposta do servidor local sem texto")


class LocalTranslator:
    """Translate one chunk with the local model. Same shape as the Google path in translator.py:
    translate(chunk, lang_from, lang_to) -> (translation, detected_lang). The model does not report
    the source language, so detected_lang is lang_from when given, else ""."""

    def __init__(self, server=None):
        self.server = server if server is not None else LocalServer()

    def available(self):
        return self.server.installed()

    def translate(self, chunk, lang_from, lang_to):
        if not chunk.strip():
            return chunk, ""
        prompt = build_prompt(chunk, lang_to)
        # Portuguese output runs ~1.3x the English token count; leave room, cap at the context.
        max_tokens = min(CTX_SIZE // 2, max(64, len(chunk)))
        out = clean_output(self.server.chat(prompt, max_tokens))
        if not out:
            raise FallbackUnavailable("modelo local devolveu texto vazio")
        return out, ("" if lang_from in (None, "", "auto") else lang_from)

    def shutdown(self):
        self.server.stop()


_shared = None
_sharedLock = threading.Lock()


def shared():
    """One LocalTranslator per NVDA session (one server process at most)."""
    global _shared
    with _sharedLock:
        if _shared is None:
            _shared = LocalTranslator()
        return _shared


def shutdown_shared():
    """Call from GlobalPlugin.terminate() so the server never outlives NVDA."""
    global _shared
    with _sharedLock:
        if _shared is not None:
            _shared.shutdown()
            _shared = None
