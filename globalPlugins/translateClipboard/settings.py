# settings.py
# Painel de configurações unificado para o addon translateClipboard.

import wx
import gui
import gui.guiHelper
from gui.settingsDialogs import SettingsPanel
from copy import deepcopy
from locale import strxfrm

import addonHandler
addonHandler.initTranslation()

from .langslist import langslist, g
from . import clipboard_monitor as clipmon

# Intervalos disponíveis para o monitor de clipboard
INTERVAL_LABELS = [
    "0,1 segundo",
    "0,2 segundos",
    "0,3 segundos",
    "0,4 segundos",
    "0,5 segundos",
    "1 segundo",
]
INTERVAL_VALUES = [0.1, 0.2, 0.3, 0.4, 0.5, 1.0]

# Idiomas disponíveis para o modo jogo (subconjunto comum)
GAME_LANG_CODES = ["de","ar","hr","es","fr","en","it","pl","pt","ru","tr","uk","zh-CN","ja","ko"]


def _sorted_langs(include_auto=True):
    """Retorna lista de nomes de idiomas em ordem alfabética (auto primeiro se incluído)."""
    auto_label = g("auto")
    names = [n for n in langslist.keys() if n != auto_label]
    names.sort(key=strxfrm)
    if include_auto:
        return [auto_label] + names
    return names


class TranslateClipboardSettings(SettingsPanel):
    # Translators: título do painel
    title = _("Tradução e Área de Transferência")
    addonConf = None  # injetado pelo __init__.py

    def makeSettings(self, sizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)

        # ── Seção: Tradução ──────────────────────────────────────────────────
        transBox = wx.StaticBox(self, label=_("Tradução"))
        transSizer = wx.StaticBoxSizer(transBox, wx.VERTICAL)
        transHelper = gui.guiHelper.BoxSizerHelper(self, sizer=transSizer)

        # Idioma de origem
        src_labels = _sorted_langs(include_auto=True)
        # Remove zh-TW das opções de origem (Google não suporta como origem)
        zh_tw_label = g("zh-TW")
        src_labels_filtered = [l for l in src_labels if l != zh_tw_label]
        self._fromChoice = transHelper.addLabeledControl(
            _("Idioma de origem:"), wx.Choice, choices=src_labels_filtered)
        self._fromChoice.Bind(wx.EVT_CHOICE, self._onFromSelect)

        # Idioma de destino
        dst_labels = _sorted_langs(include_auto=False)
        self._intoChoice = transHelper.addLabeledControl(
            _("Idioma de destino:"), wx.Choice, choices=dst_labels)

        # Idioma de troca (quando origem=auto e texto já está no destino)
        self._swapChoice = transHelper.addLabeledControl(
            _("Idioma de troca (auto-swap):"), wx.Choice, choices=dst_labels)

        # Checkboxes de tradução
        self._autoSwapChk = transHelper.addItem(wx.CheckBox(
            self, label=_("Auto-trocar idiomas se texto já estiver no destino")))
        self._copyResultChk = transHelper.addItem(wx.CheckBox(
            self, label=_("Copiar tradução para área de transferência")))
        self._replaceUnderscoresChk = transHelper.addItem(wx.CheckBox(
            self, label=_("Substituir underscores por espaços antes de traduzir")))
        self._useMirrorChk = transHelper.addItem(wx.CheckBox(
            self, label=_("Usar servidor espelho (para usuários na China)")))

        sizer.Add(transSizer, flag=wx.EXPAND | wx.ALL, border=5)

        # ── Seção: Área de Transferência ─────────────────────────────────────
        clipBox = wx.StaticBox(self, label=_("Área de Transferência"))
        clipSizer = wx.StaticBoxSizer(clipBox, wx.VERTICAL)
        clipHelper = gui.guiHelper.BoxSizerHelper(self, sizer=clipSizer)

        self._clipActiveChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Ativar funcionalidades de clipboard (master)")))
        self._clipActiveChk.Bind(wx.EVT_CHECKBOX, self._onClipActiveChange)

        self._announceChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Anunciar teclas de clipboard (Ctrl+C, Ctrl+V...)")))
        self._soundsChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Reproduzir sons nas ações de clipboard")))

        self._historyChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Ativar histórico de clipboard")))
        self._historyChk.Bind(wx.EVT_CHECKBOX, self._onHistoryChange)

        self._intervalChoice = clipHelper.addLabeledControl(
            _("Intervalo do monitor de histórico:"), wx.Choice, choices=INTERVAL_LABELS)

        self._soundOnAddChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Som ao adicionar item ao histórico")))
        self._announceCopiedChk = clipHelper.addItem(wx.CheckBox(
            self, label=_("Anunciar texto ao copiar para o histórico")))

        # ── Modo Jogo ────────────────────────────────────────────────────────
        gameBox = wx.StaticBox(self, label=_("Modo Jogo (traduzir clipboard automaticamente)"))
        gameSizer = wx.StaticBoxSizer(gameBox, wx.VERTICAL)
        gameHelper = gui.guiHelper.BoxSizerHelper(self, sizer=gameSizer)

        self._gameModeChk = gameHelper.addItem(wx.CheckBox(
            self, label=_("Ativar modo jogo")))
        self._gameModeChk.Bind(wx.EVT_CHECKBOX, self._onGameModeChange)

        game_lang_labels = [g(c) for c in GAME_LANG_CODES]
        self._gameLangChoice = gameHelper.addLabeledControl(
            _("Idioma de destino para tradução automática:"), wx.Choice, choices=game_lang_labels)

        self._gameIntervalChoice = gameHelper.addLabeledControl(
            _("Intervalo do monitor do modo jogo:"), wx.Choice, choices=INTERVAL_LABELS)

        clipSizer.Add(gameSizer, flag=wx.EXPAND | wx.ALL, border=5)
        sizer.Add(clipSizer, flag=wx.EXPAND | wx.ALL, border=5)

        # ── Carregar valores atuais ──────────────────────────────────────────
        self._load_values()

    def _load_values(self):
        c = self.addonConf

        # Tradução
        src_labels_filtered = self._fromChoice.GetStrings()
        from_label = self._lang_to_display(c['lang_from'])
        idx = self._fromChoice.FindString(from_label)
        self._fromChoice.SetSelection(idx if idx >= 0 else 0)

        into_label = self._lang_to_display(c['lang_to'])
        idx = self._intoChoice.FindString(into_label)
        self._intoChoice.SetSelection(idx if idx >= 0 else 0)

        swap_label = self._lang_to_display(c['lang_swap'])
        idx = self._swapChoice.FindString(swap_label)
        self._swapChoice.SetSelection(idx if idx >= 0 else 0)

        self._autoSwapChk.SetValue(bool(c['auto_swap']))
        self._copyResultChk.SetValue(bool(c['copy_result']))
        self._replaceUnderscoresChk.SetValue(bool(c['replace_underscores']))
        self._useMirrorChk.SetValue(bool(c['use_mirror']))

        # Habilitar/desabilitar swap baseado no idioma de origem
        if self._fromChoice.GetStringSelection() != g("auto"):
            self._swapChoice.Disable()
            self._autoSwapChk.Disable()

        # Clipboard
        clip_active = bool(c['clip_active'])
        self._clipActiveChk.SetValue(clip_active)
        self._announceChk.SetValue(bool(c['clip_announce']))
        self._soundsChk.SetValue(bool(c['clip_sounds']))

        hist_active = bool(c['clip_history'])
        self._historyChk.SetValue(hist_active)
        self._intervalChoice.SetSelection(int(c['clip_interval']))
        self._soundOnAddChk.SetValue(bool(c['clip_sound_on_history']))
        self._announceCopiedChk.SetValue(bool(c['clip_announce_copied']))

        # Modo jogo
        game_active = bool(c['game_mode'])
        self._gameModeChk.SetValue(game_active)
        try:
            game_lang_idx = GAME_LANG_CODES.index(c['game_lang'])
        except ValueError:
            game_lang_idx = GAME_LANG_CODES.index('en') if 'en' in GAME_LANG_CODES else 0
        self._gameLangChoice.SetSelection(game_lang_idx)
        self._gameIntervalChoice.SetSelection(int(c['game_interval']))

        # Atualizar estado de controles dependentes
        self._update_clip_controls(clip_active)
        self._update_history_controls(hist_active)
        self._update_game_controls(game_active)

    def _lang_to_display(self, code):
        """Converte código de idioma para nome de exibição."""
        for name, c in langslist.items():
            if c == code:
                return name
        return g(code)

    def _onFromSelect(self, event):
        is_auto = (event.GetString() == g("auto"))
        self._swapChoice.Enable(is_auto)
        self._autoSwapChk.Enable(is_auto)

    def _onClipActiveChange(self, event):
        self._update_clip_controls(event.IsChecked())

    def _update_clip_controls(self, active):
        for ctrl in (self._announceChk, self._soundsChk, self._historyChk,
                     self._gameModeChk):
            ctrl.Enable(active)
        if active:
            self._update_history_controls(self._historyChk.IsChecked())
            self._update_game_controls(self._gameModeChk.IsChecked())
        else:
            for ctrl in (self._intervalChoice, self._soundOnAddChk,
                         self._announceCopiedChk, self._gameLangChoice,
                         self._gameIntervalChoice):
                ctrl.Enable(False)

    def _onHistoryChange(self, event):
        self._update_history_controls(event.IsChecked())

    def _update_history_controls(self, active):
        for ctrl in (self._intervalChoice, self._soundOnAddChk, self._announceCopiedChk):
            ctrl.Enable(active and self._clipActiveChk.IsChecked())

    def _onGameModeChange(self, event):
        self._update_game_controls(event.IsChecked())

    def _update_game_controls(self, active):
        for ctrl in (self._gameLangChoice, self._gameIntervalChoice):
            ctrl.Enable(active and self._clipActiveChk.IsChecked())

    def postInit(self):
        self._fromChoice.SetFocus()

    def onSave(self):
        c = self.addonConf

        # Tradução
        c['lang_from']          = langslist.get(self._fromChoice.GetStringSelection(), 'auto')
        c['lang_to']            = langslist.get(self._intoChoice.GetStringSelection(), 'pt')
        c['lang_swap']          = langslist.get(self._swapChoice.GetStringSelection(), 'en')
        c['auto_swap']          = self._autoSwapChk.IsChecked()
        c['copy_result']        = self._copyResultChk.IsChecked()
        c['replace_underscores'] = self._replaceUnderscoresChk.IsChecked()
        c['use_mirror']         = self._useMirrorChk.IsChecked()

        # Clipboard
        c['clip_active']        = self._clipActiveChk.IsChecked()
        c['clip_announce']      = self._announceChk.IsChecked()
        c['clip_sounds']        = self._soundsChk.IsChecked()
        c['clip_history']       = self._historyChk.IsChecked()
        c['clip_interval']      = self._intervalChoice.GetSelection()
        c['clip_sound_on_history'] = self._soundOnAddChk.IsChecked()
        c['clip_announce_copied'] = self._announceCopiedChk.IsChecked()

        # Modo jogo
        c['game_mode']          = self._gameModeChk.IsChecked()
        game_idx                = self._gameLangChoice.GetSelection()
        c['game_lang']          = GAME_LANG_CODES[game_idx] if 0 <= game_idx < len(GAME_LANG_CODES) else 'en'
        c['game_interval']      = self._gameIntervalChoice.GetSelection()

        # Notificar o plugin principal para reiniciar monitores
        if self.addonConf.get('_restart_monitors_callback'):
            try:
                self.addonConf['_restart_monitors_callback']()
            except Exception:
                pass
