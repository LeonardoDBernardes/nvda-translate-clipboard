# -*- coding: utf-8 -*-
# __init__.py
# Addon NVDA: Tradução e Área de Transferência
# Combina instantTranslate + zPortapapeles, atualizado para NVDA 2023.1–2025.x
# Copyright (C) 2026 Leo. GPL.

from functools import wraps, lru_cache
from locale import getdefaultlocale
from time import sleep

import addonHandler
import api
import braille
import config
import core
import globalPluginHandler
import globalVars
import gui
import queueHandler
import scriptHandler
import speech
import speechViewer
import textInfos
import threading
import tones
import ui
import wx

from speech import speak
from versionInfo import version_year

try:
    from speech.commands import LangChangeCommand
except ImportError:
    from speech import LangChangeCommand

from .speechOnDemand import getSpeechOnDemandParameter, executeWithSpeakOnDemand, isSpeechOnDemandAvailable
from .translator import Translator
from .langslist import g, langslist
from .settings import TranslateClipboardSettings, INTERVAL_VALUES, GAME_LANG_CODES
from . import clipboard_monitor as clipmon

addonHandler.initTranslation()

# ── Módulo de speech compatível com NVDA 2021+ ────────────────────────────────
_speechModule = speech.speech if version_year >= 2021 else speech

# ── Idioma padrão do sistema ──────────────────────────────────────────────────
def _system_lang():
    try:
        loc = getdefaultlocale()[0] or 'en'
        if loc == "zh_HK":
            return "zh-TW"
        if loc.startswith("zh"):
            return loc.replace('_', '-')
        return loc.split('_')[0]
    except Exception:
        return 'en'

# ── Especificação de configuração ─────────────────────────────────────────────
ADDON_NAME = "translateClipboard"
_confspec = {
    # Tradução
    "lang_from":            "string(default='auto')",
    "lang_to":              f"string(default='{_system_lang()}')",
    "lang_swap":            "string(default='en')",
    "copy_result":          "boolean(default=True)",
    "auto_swap":            "boolean(default=True)",
    "is_auto_swapped":      "boolean(default=False)",
    "replace_underscores":  "boolean(default=False)",
    "use_mirror":           "boolean(default=False)",
    # Clipboard
    "clip_active":          "boolean(default=True)",
    "clip_announce":        "boolean(default=True)",
    "clip_sounds":          "boolean(default=False)",
    "clip_history":         "boolean(default=False)",
    "clip_interval":        "integer(default=1, min=0, max=5)",
    "clip_sound_on_history":"boolean(default=False)",
    "clip_announce_copied": "boolean(default=False)",
    # Modo jogo
    "game_mode":            "boolean(default=False)",
    "game_lang":            "string(default='en')",
    "game_interval":        "integer(default=1, min=0, max=5)",
}

# Parâmetro speakOnDemand para scripts que anunciam texto
_speakOnDemand = getSpeechOnDemandParameter()


# ── Helpers de compatibilidade de SpeechMode ─────────────────────────────────

def _get_speech_mode():
    """Retorna o modo de fala atual (SpeechMode enum). Compatível 2023.1–2025.x."""
    try:
        return speech.getState().speechMode
    except AttributeError:
        return None

def _set_speech_mode(mode):
    """Define o modo de fala. Aceita SpeechMode enum ou inteiro (fallback)."""
    try:
        speech.setSpeechMode(mode)
    except Exception:
        pass

def _silence():
    """Silencia a fala e retorna o modo anterior."""
    try:
        mode = speech.getState().speechMode
        speech.setSpeechMode(speech.SpeechMode.off)
        return mode
    except AttributeError:
        speech.setSpeechMode(0)
        return None

def _restore(old_mode):
    """Restaura o modo de fala anterior."""
    if old_mode is None:
        try:
            speech.setSpeechMode(speech.SpeechMode.talk)
        except AttributeError:
            speech.setSpeechMode(2)
    else:
        _set_speech_mode(old_mode)


# ── Decorador auxiliar ────────────────────────────────────────────────────────

def _finally(func, final):
    """Chama final após func, mesmo que func lance exceção."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            final()
    return wrapper


# ── Mensagem com detecção de idioma ──────────────────────────────────────────

def _speak_with_lang(msg):
    """Anuncia texto ativando troca automática de idioma se configurado."""
    if config.conf['speech']['autoLanguageSwitching']:
        seq = [LangChangeCommand(msg['lang']), msg['text']]
        speak(seq)
        braille.handler.message(msg['text'])
    else:
        ui.message(msg['text'])


# ── Plugin principal ──────────────────────────────────────────────────────────

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Tradução e Área de Transferência")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if globalVars.appArgs.secure:
            return

        # Configuração
        config.conf.spec[ADDON_NAME] = _confspec
        self._conf = config.conf[ADDON_NAME]
        self._conf['_restart_monitors_callback'] = self._restart_monitors

        # Estado da tradução
        self._toggling        = False
        self._lastTranslation = None
        self._autoTranslate   = False
        self._lastSpoken      = ''

        # Monitores de clipboard
        self._clipMonitor     = None
        self._gameMonitor     = None

        # Injetar função de tradução no módulo clipboard_monitor
        clipmon.set_game_translate_func(self._sync_translate_for_game)

        # Painel de configurações
        TranslateClipboardSettings.addonConf = self._conf
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(TranslateClipboardSettings)

        # Hook na fala para auto-translate e rastrear último texto falado
        self._origSpeak = _speechModule.speak
        _speechModule.speak = self._hookedSpeak

        # Iniciar monitores
        self._start_monitors()
        core.postNvdaStartup.register(self._post_startup)

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def _post_startup(self):
        clipmon.clean()
        self._start_monitors()

    def terminate(self):
        if not globalVars.appArgs.secure:
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(TranslateClipboardSettings)
            _speechModule.speak = self._origSpeak
            self._stop_monitors()
            core.postNvdaStartup.unregister(self._post_startup)
        super().terminate()

    # ── Monitores de clipboard ────────────────────────────────────────────────

    def _start_monitors(self):
        self._stop_monitors()
        c = self._conf
        interval = INTERVAL_VALUES[int(c['clip_interval'])]
        game_interval = INTERVAL_VALUES[int(c['game_interval'])]

        if c['clip_active'] and c['clip_history']:
            self._clipMonitor = clipmon.ClipMonitor(
                interval=interval,
                announce_copied=bool(c['clip_announce_copied']),
                sound_on_add=bool(c['clip_sound_on_history']),
            )
            self._clipMonitor.start()

        # Modo jogo: criar sempre, pausado se não ativo
        self._gameMonitor = clipmon.ClipMonitorGame(
            interval=game_interval,
            lang_to=str(c['game_lang']),
        )
        self._gameMonitor.start()
        if not (c['clip_active'] and c['game_mode']):
            self._gameMonitor.pause()

    def _stop_monitors(self):
        if self._clipMonitor:
            self._clipMonitor.kill()
            self._clipMonitor = None
        if self._gameMonitor:
            self._gameMonitor.kill()
            self._gameMonitor = None

    def _restart_monitors(self):
        """Chamado pelo painel de configurações ao salvar."""
        self._start_monitors()
        # Atualizar função de tradução com novo idioma
        clipmon.set_game_translate_func(self._sync_translate_for_game)

    # ── Hook de fala (auto-translate + rastrear último falado) ────────────────

    def _hookedSpeak(self, sequence, *args, **kwargs):
        text_items = [x for x in sequence if isinstance(x, str)]
        self._lastSpoken = speechViewer.SPEECH_ITEM_SEPARATOR.join(text_items)

        if self._autoTranslate and text_items:
            text = self._lastSpoken
            if self._conf['replace_underscores']:
                text = text.replace("_", " ")
            try:
                result = self._translate_cached(
                    text, self._conf['lang_from'], self._conf['lang_to'])
                new_seq = []
                if config.conf['speech']['autoLanguageSwitching']:
                    new_seq.append(LangChangeCommand(result.lang_to))
                new_seq.append(result.translation)
                self._origSpeak(new_seq, *args, **kwargs)
                self._copy_result(result.translation)
                return
            except RuntimeError:
                pass

        self._origSpeak(sequence, *args, **kwargs)

    # ── Tradução ──────────────────────────────────────────────────────────────

    def _get_selected_text(self):
        obj = api.getCaretObject()
        try:
            info = obj.makeTextInfo(textInfos.POSITION_SELECTION)
            if info and not info.isCollapsed:
                return info.text
        except (RuntimeError, NotImplementedError):
            pass
        return None

    def _translate_async(self, text, lang_from, lang_to):
        """Inicia tradução em thread separada e anuncia o resultado."""
        threading.Thread(
            target=self._translate_and_announce,
            args=(text, lang_from, lang_to),
            daemon=True,
        ).start()

    def _translate_and_announce(self, text, lang_from, lang_to):
        if self._conf['replace_underscores']:
            text = text.replace("_", " ")
        lang_swap = self._conf['lang_swap'] if (lang_from == "auto" and self._conf['auto_swap']) else None
        try:
            result = self._translate_cached(text, lang_from, lang_to, lang_swap)
        except RuntimeError:
            return
        self._lastTranslation = result.translation
        msg = {'text': result.translation, 'lang': result.lang_to}
        queueHandler.queueFunction(
            queueHandler.eventQueue,
            lambda: executeWithSpeakOnDemand(_speak_with_lang, msg)
        )
        self._copy_result(result.translation)

    @lru_cache(maxsize=128)
    def _translate_cached(self, text, lang_from, lang_to, lang_swap=None):
        """Traduz com cache. Bloqueia até terminar."""
        t = Translator(lang_from, lang_to, text, lang_swap, bool(self._conf['use_mirror']))
        t.start()
        i = 0
        while t.is_alive():
            sleep(0.1)
            i += 1
            if i == 10:
                tones.beep(500, 100)
                i = 0
        t.join()
        if t.error:
            if not self._autoTranslate:
                queueHandler.queueFunction(queueHandler.eventQueue, ui.message, _("Falha na tradução"))
            raise RuntimeError("Falha na tradução")
        return t

    def _sync_translate_for_game(self, text, lang_to):
        """Tradução síncrona para o modo jogo. Retorna string ou lança exceção."""
        t = Translator("auto", lang_to, text, use_mirror=bool(self._conf['use_mirror']))
        t.start()
        t.join(timeout=15)
        if t.is_alive() or t.error:
            raise RuntimeError("Falha na tradução do modo jogo")
        return t.translation

    def _copy_result(self, translation, force=False):
        if force or self._conf['copy_result']:
            api.copyToClip(translation)

    def _swap_langs(self, from_lang, to_lang):
        self._conf['lang_from'] = to_lang
        self._conf['lang_to']   = from_lang

    # ── Scripts de tradução ───────────────────────────────────────────────────

    @scriptHandler.script(
        description=_("Ativar camada de comandos de tradução. Pressione H para listar comandos."),
    )
    def script_ITLayer(self, gesture):
        if self._toggling:
            self._layer_error(gesture)
            return
        self.bindGestures(self.__ITGestures)
        self._toggling = True
        tones.beep(100, 10)

    def getScript(self, gesture):
        if not self._toggling:
            return globalPluginHandler.GlobalPlugin.getScript(self, gesture)
        script = globalPluginHandler.GlobalPlugin.getScript(self, gesture)
        if not script:
            script = _finally(self._layer_error, self._layer_finish)
        return _finally(script, self._layer_finish)

    def _layer_finish(self):
        self._toggling = False
        self.clearGestureBindings()
        self.bindGestures(self.__gestures)

    def _layer_error(self, gesture):
        tones.beep(120, 100)

    @scriptHandler.script(
        description=_("Traduzir texto selecionado."),
        **_speakOnDemand,
    )
    def script_translateSelection(self, gesture):
        text = self._get_selected_text()
        if not text:
            ui.message(_("Nenhum texto selecionado"))
            return
        self._translate_async(text, self._conf['lang_from'], self._conf['lang_to'])

    @scriptHandler.script(
        description=_("Traduzir texto da área de transferência."),
        **_speakOnDemand,
    )
    def script_translateClipboard(self, gesture):
        try:
            text = api.getClipData()
        except Exception:
            text = None
        if not text or not isinstance(text, str) or text.isspace():
            ui.message(_("Área de transferência vazia"))
            return
        self._translate_async(text, self._conf['lang_from'], self._conf['lang_to'])

    @scriptHandler.script(
        description=_("Traduzir último texto falado pelo NVDA."),
        **_speakOnDemand,
    )
    def script_translateLastSpoken(self, gesture):
        if self._lastSpoken:
            self._translate_async(self._lastSpoken, self._conf['lang_from'], self._conf['lang_to'])
        else:
            ui.message(_("Nenhum texto foi falado ainda"))

    @scriptHandler.script(
        description=_("Trocar idiomas de origem e destino."),
    )
    def script_swapLanguages(self, gesture):
        lf = self._conf['lang_from']
        lt = self._conf['lang_to']
        ls = self._conf['lang_swap']
        if lf == "auto":
            self._swap_langs(ls, lt)
            self._conf['is_auto_swapped'] = True
        elif self._conf['is_auto_swapped'] and lt == ls:
            self._swap_langs(lf, "auto")
            self._conf['is_auto_swapped'] = False
        else:
            self._swap_langs(lf, lt)
        ui.message(_("Idiomas trocados"))
        ui.message(_("Traduzir: de {de} para {para}").format(
            de=g(self._conf['lang_from'], short=True),
            para=g(self._conf['lang_to'], short=True),
        ))
        # Re-traduzir seleção após troca
        try:
            should = speech.getState().speechMode != speech.SpeechMode.onDemand
        except AttributeError:
            should = True
        if should:
            self.script_translateSelection(gesture)

    @scriptHandler.script(
        description=_("Anunciar idiomas atuais."),
        **_speakOnDemand,
    )
    def script_announceLanguages(self, gesture):
        ui.message(_("Traduzir: de {de} para {para}").format(
            de=g(self._conf['lang_from'], short=True),
            para=g(self._conf['lang_to'], short=True),
        ))

    @scriptHandler.script(
        description=_("Copiar última tradução para a área de transferência."),
        **_speakOnDemand,
    )
    def script_copyLastTranslation(self, gesture):
        if self._lastTranslation:
            self._copy_result(self._lastTranslation, force=True)
            ui.message(_("Última tradução copiada"))
        else:
            ui.message(_("Nenhuma tradução armazenada"))

    @scriptHandler.script(
        description=_("Identificar idioma do texto selecionado."),
        **_speakOnDemand,
    )
    def script_identifyLanguage(self, gesture):
        text = self._get_selected_text()
        if not text:
            ui.message(_("Nenhum texto selecionado"))
            return
        ui.message(_("Identificando idioma..."))
        t = Translator("auto", self._conf['lang_to'], text)
        t.start()
        i = 0
        while t.is_alive():
            sleep(0.1)
            i += 1
            if i == 10:
                tones.beep(500, 100)
                i = 0
        t.join()
        queueHandler.queueFunction(queueHandler.eventQueue, ui.message, g(t.lang_detected))

    @scriptHandler.script(
        description=_("Alternar auto-tradução de toda fala do NVDA."),
    )
    def script_toggleAutoTranslate(self, gesture):
        self._autoTranslate = not self._autoTranslate
        if self._autoTranslate:
            ui.message(_("Auto-tradução ativada"))
        else:
            ui.message(_("Auto-tradução desativada"))

    @scriptHandler.script(
        description=_("Abrir configurações de Tradução e Área de Transferência."),
    )
    def script_showSettings(self, gesture):
        wx.CallAfter(
            gui.mainFrame._popupSettingsDialog,
            gui.settingsDialogs.NVDASettingsDialog,
            TranslateClipboardSettings,
        )

    @scriptHandler.script(
        description=_("Mostrar lista de comandos disponíveis."),
        **_speakOnDemand,
    )
    def script_showHelp(self, gesture):
        ui.message(_(
            "Comandos disponíveis na camada NVDA+Shift+T: "
            "T — traduzir seleção; "
            "Shift+T — traduzir clipboard; "
            "L — traduzir último texto falado; "
            "V — alternar auto-tradução; "
            "S — trocar idiomas; "
            "A — anunciar idiomas; "
            "C — copiar última tradução; "
            "I — identificar idioma; "
            "O — abrir configurações; "
            "H — esta ajuda."
        ))

    # ── Scripts de clipboard ──────────────────────────────────────────────────

    @scriptHandler.script(
        description=_("Mostrar histórico da área de transferência."),
        **_speakOnDemand,
    )
    def script_showHistory(self, gesture):
        if not self._conf['clip_active']:
            ui.message(_("Funcionalidades de clipboard estão desativadas"))
            return
        if self._conf['game_mode']:
            ui.message(_("Desative o modo jogo antes de usar o histórico"))
            return
        if not self._conf['clip_history']:
            ui.message(_("Histórico não está ativado. Ative nas configurações."))
            return
        if not self._clipMonitor:
            ui.message(_("Monitor de histórico não está ativo"))
            return
        items = self._clipMonitor.history
        if not items:
            ui.message(_("Histórico vazio"))
            return
        wx.CallAfter(self._open_history_dialog, items)

    def _open_history_dialog(self, items):
        dlg = _HistoryDialog(gui.mainFrame, items, self._clipMonitor)
        dlg.ShowModal()
        dlg.Destroy()

    @scriptHandler.script(
        description=_("Alternar modo jogo (traduz clipboard automaticamente)."),
    )
    def script_toggleGameMode(self, gesture):
        if not self._conf['clip_active']:
            ui.message(_("Funcionalidades de clipboard estão desativadas"))
            return
        new_state = not self._conf['game_mode']
        self._conf['game_mode'] = new_state
        if self._gameMonitor:
            if new_state:
                self._gameMonitor.resume()
                self._gameMonitor.set_lang(str(self._conf['game_lang']))
                tones.beep(400, 150)
                ui.message(_("Modo jogo ativado"))
            else:
                self._gameMonitor.pause()
                tones.beep(100, 150)
                ui.message(_("Modo jogo desativado"))

    @scriptHandler.script(
        description=_("Ativar/desativar funcionalidades de clipboard."),
    )
    def script_toggleClipActive(self, gesture):
        new_state = not self._conf['clip_active']
        self._conf['clip_active'] = new_state
        if new_state:
            tones.beep(400, 150)
            ui.message(_("Clipboard ativado"))
        else:
            tones.beep(100, 150)
            ui.message(_("Clipboard desativado"))

    def script_clipboardKey(self, gesture):
        """Intercepta teclas de clipboard, encaminha e anuncia/toca som."""
        obj = api.getFocusObject()

        # Em consoles, não interferir
        if obj and getattr(obj, 'windowClassName', '') == 'ConsoleWindowClass':
            gesture.send()
            return

        # Encaminhar a tecla real primeiro
        gesture.send()

        if not self._conf['clip_active']:
            return
        if not self._conf['clip_announce'] and not self._conf['clip_sounds']:
            return

        # Identificar a ação
        key = gesture.mainKeyName.lower()
        mod = "+".join(m.lower() for m in sorted(gesture.modifierNames))
        combo = f"{mod}+{key}" if mod else key

        action_info = clipmon.GESTURE_ACTIONS.get(combo)
        if not action_info:
            return

        sound_name, get_label = action_info
        label = get_label()

        has_clip_text = clipmon.has_text()

        if self._conf['clip_announce']:
            if combo == "control+c":
                # Só anuncia "Copiar" se havia seleção (tem texto no clipboard)
                if has_clip_text:
                    try:
                        info = obj.makeTextInfo(textInfos.POSITION_SELECTION) if obj else None
                        if info and not info.isCollapsed:
                            ui.message(label)
                        else:
                            ui.message(_("Sem seleção"))
                    except Exception:
                        if has_clip_text:
                            ui.message(label)
                else:
                    ui.message(_("Sem seleção"))
            else:
                ui.message(label)

        if self._conf['clip_sounds']:
            threading.Thread(target=clipmon.play_sound, args=(sound_name,), daemon=True).start()

    # ── Gestos ────────────────────────────────────────────────────────────────

    __ITGestures = {
        "kb:t":       "translateSelection",
        "kb:shift+t": "translateClipboard",
        "kb:l":       "translateLastSpoken",
        "kb:v":       "toggleAutoTranslate",
        "kb:s":       "swapLanguages",
        "kb:a":       "announceLanguages",
        "kb:c":       "copyLastTranslation",
        "kb:i":       "identifyLanguage",
        "kb:o":       "showSettings",
        "kb:h":       "showHelp",
    }

    __gestures = {
        # Camada de tradução
        "kb:NVDA+shift+t": "ITLayer",
        # Histórico (sem atalho padrão — atribuir via Gestos de Entrada)
        # Modo jogo (sem atalho padrão — atribuir via Gestos de Entrada)
        # Clipboard (ativos por padrão para anúncio)
        "kb:control+c": "clipboardKey",
        "kb:control+x": "clipboardKey",
        "kb:control+v": "clipboardKey",
        "kb:control+z": "clipboardKey",
        "kb:control+y": "clipboardKey",
        "kb:control+a": "clipboardKey",
    }


# ── Diálogo de histórico ──────────────────────────────────────────────────────

class _HistoryDialog(wx.Dialog):
    """Janela de histórico da área de transferência."""

    def __init__(self, parent, items, monitor):
        super().__init__(parent, title=_("Histórico da Área de Transferência"))
        self._monitor = monitor
        self._items   = list(items)

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Instrução
        instructions = wx.StaticText(self, label=_(
            "Enter: copiar item  |  Del: remover item  |  Ctrl+Del: limpar tudo  |  Esc: fechar"
        ))
        sizer.Add(instructions, flag=wx.ALL, border=5)

        # Lista
        self._listBox = wx.ListBox(self, choices=self._items, style=wx.LB_SINGLE)
        self._listBox.Bind(wx.EVT_KEY_DOWN, self._onKey)
        if self._items:
            self._listBox.SetSelection(0)
        sizer.Add(self._listBox, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        # Botões
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        copyBtn  = wx.Button(self, label=_("&Copiar"))
        deleteBtn = wx.Button(self, label=_("&Remover"))
        clearBtn = wx.Button(self, label=_("Limpar &tudo"))
        closeBtn = wx.Button(self, id=wx.ID_CANCEL, label=_("&Fechar"))
        copyBtn.Bind(wx.EVT_BUTTON, self._onCopy)
        deleteBtn.Bind(wx.EVT_BUTTON, self._onDelete)
        clearBtn.Bind(wx.EVT_BUTTON, self._onClearAll)
        btnSizer.Add(copyBtn);  btnSizer.Add(deleteBtn)
        btnSizer.Add(clearBtn); btnSizer.Add(closeBtn)
        sizer.Add(btnSizer, flag=wx.ALL, border=5)

        self.SetSizer(sizer)
        self.SetSize((600, 400))
        self._listBox.SetFocus()

    def _onKey(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_RETURN:
            self._onCopy(None)
        elif key == wx.WXK_DELETE:
            if event.ControlDown():
                self._onClearAll(None)
            else:
                self._onDelete(None)
        elif key == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    def _onCopy(self, event):
        idx = self._listBox.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        text = self._items[idx]
        api.copyToClip(text)
        ui.message(_("Copiado"))
        self.EndModal(wx.ID_OK)

    def _onDelete(self, event):
        idx = self._listBox.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        self._monitor.delete(idx)
        self._listBox.Delete(idx)
        del self._items[idx]
        if self._items:
            new_idx = min(idx, len(self._items) - 1)
            self._listBox.SetSelection(new_idx)
        else:
            ui.message(_("Histórico vazio"))
            self.EndModal(wx.ID_OK)

    def _onClearAll(self, event):
        self._monitor.delete_all()
        self._listBox.Clear()
        self._items.clear()
        ui.message(_("Histórico limpo"))
        self.EndModal(wx.ID_OK)
