# translator.py
# Motor de tradução via Google Translate API (HTTPS, sem scraping).
# Baseado em instantTranslate — Copyright (C) 2013-2024 Mesar Hameed et al., GPL.

import re
import ssl
import json
import threading
import urllib.request as urllibRequest
from random import randint
from time import sleep
from logHandler import log

ssl._create_default_https_context = ssl._create_unverified_context

# Marcadores de quebra de texto para diferentes scripts
_arabicBreaks  = u'[،؛؟]'
_chineseBreaks = u'[　-〿︐-︟︰-﹯！-｠]'
_latinBreaks   = r'[.,!?;:]'
_splitReg = re.compile(u"{a}|{c}|{l}".format(a=_arabicBreaks, c=_chineseBreaks, l=_latinBreaks))

# Correção de códigos retornados pelo Google (não-ISO)
_langConversion = {'iw': 'he', 'jw': 'jv'}

_URL_TEMPLATE = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl={lang_from}&tl={lang_to}&dt=t&q={text}&dj=1"
)
_URL_MIRROR = (
    "https://translate.googleapis.mirror.nvdadr.com/translate_a/single"
    "?client=gtx&sl={lang_from}&tl={lang_to}&dt=t&q={text}&dj=1"
)


def _splitChunks(text, chunksize=3000):
    """Divide o texto em pedaços respeitando marcadores de pontuação."""
    pos = 0
    potentialPos = 0
    for m in _splitReg.finditer(text):
        if (m.start() - pos + 1) < chunksize:
            potentialPos = m.start()
            continue
        yield text[pos:potentialPos + 1]
        pos = potentialPos + 1
        potentialPos = m.start()
    yield text[pos:]


class Translator(threading.Thread):
    """Thread que traduz um texto via Google Translate e armazena o resultado.

    Atributos após execução:
        translation (str): texto traduzido
        lang_detected (str): idioma detectado (código ISO)
        error (bool): True se a tradução falhou
    """

    def __init__(self, lang_from, lang_to, text, lang_swap=None, use_mirror=False, chunksize=3000):
        super().__init__(daemon=True)
        if lang_from != "auto" and lang_swap is not None:
            raise ValueError(f"lang_swap só pode ser usado com lang_from='auto' (got lang_from='{lang_from}')")
        self._stop_event = threading.Event()
        self.lang_from    = lang_from
        self.lang_to      = lang_to
        self.lang_swap    = lang_swap
        self.use_mirror   = use_mirror
        self.text         = text
        self.chunksize    = chunksize
        self.translation  = ''
        self.lang_detected = ''
        self.error        = False
        self._opener = urllibRequest.build_opener()
        self._opener.addheaders = [('User-agent', 'Mozilla/5.0')]
        self._first_chunk = True

    def stop(self):
        self._stop_event.set()

    def run(self):
        url_template = _URL_MIRROR if self.use_mirror else _URL_TEMPLATE
        for chunk in _splitChunks(self.text, self.chunksize):
            if self._stop_event.is_set():
                return
            if not self._first_chunk:
                sleep(randint(1, 5))
            url = url_template.format(
                lang_from=self.lang_from,
                lang_to=self.lang_to,
                text=urllibRequest.quote(chunk.encode('utf-8')),
            )
            try:
                response = json.load(self._opener.open(url))
            except Exception:
                self.error = True
                return
            detected = response.get('src', '')
            self.lang_detected = _langConversion.get(detected, detected)
            # Auto-swap: se o texto detectado já está no idioma destino, traduzir para o idioma de troca
            if (self._first_chunk
                    and self.lang_from == "auto"
                    and self.lang_detected == self.lang_to
                    and self.lang_swap is not None):
                self.lang_to = self.lang_swap
                self._first_chunk = False
                url = url_template.format(
                    lang_from=self.lang_from,
                    lang_to=self.lang_to,
                    text=urllibRequest.quote(chunk.encode('utf-8')),
                )
                try:
                    response = json.load(self._opener.open(url))
                except Exception:
                    self.error = True
                    return
            self._first_chunk = False
            sentences = response.get("sentences", [])
            self.translation += "".join(s.get("trans", "") for s in sentences)
