# -*- coding: utf-8 -*-
# translator.py
# Motor de tradução via endereços web do Google Translate (HTTPS, sem scraping de página).
# Baseado em instantTranslate — Copyright (C) 2013-2024 Mesar Hameed et al., GPL.
#
# v1.0.5 (18/10/2026): o Google passou a recusar de vez em quando (HTTP 429, "your computer or
# network may be sending automated queries") o endereço translate.googleapis.com?client=gtx, que
# era o único usado aqui. O erro era engolido sem registro e o NVDA só dizia "Falha na tradução".
# Agora:
#   * vários endereços do Google em sequência (o que funcionou por último vai primeiro);
#   * tempo limite em cada pedido (antes não havia: uma conexão parada travava a tradução);
#   * o motivo de cada falha vai para o nvda.log (nível normal);
#   * verificação de certificado ligada (a versão anterior desligava a verificação do NVDA inteiro
#     com um ssl._create_default_https_context global; agora só cai para "sem verificação" neste
#     pedido, se o certificado não puder ser conferido).

import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from random import randint

from logHandler import log

# Marcadores de quebra de texto para diferentes scripts
_arabicBreaks  = u'[،؛؟]'
_chineseBreaks = u'[　-〿︐-︟︰-﹯！-｠]'
_latinBreaks   = r'[.,!?;:]'
_splitReg = re.compile(u"{a}|{c}|{l}".format(a=_arabicBreaks, c=_chineseBreaks, l=_latinBreaks))

# Correção de códigos retornados pelo Google (não-ISO)
_langConversion = {'iw': 'he', 'jw': 'jv'}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# Segundos de espera por pedido. Com 4 endereços, o pior caso é 4 x 8 s; o normal é < 1 s.
_TIMEOUT = 8

# (nome, modelo da URL). A ordem é a de tentativa quando nenhum ainda funcionou nesta sessão.
_ENDPOINTS = (
    ("clients5-at",
     "https://clients5.google.com/translate_a/single?client=at&sl={sl}&tl={tl}&dt=t&q={q}"),
    ("googleapis-gtx",
     "https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}&dj=1"),
    ("clients5-dict",
     "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl}&tl={tl}&q={q}"),
    ("google-dict",
     "https://translate.google.com/translate_a/t?client=dict-chrome-ex&sl={sl}&tl={tl}&q={q}"),
)
# Espelho de terceiros: só vai na frente quando o usuário liga "usar espelho" nas configurações.
_MIRROR = (
    "mirror-nvdadr",
    "https://translate.googleapis.mirror.nvdadr.com/translate_a/single"
    "?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}&dj=1",
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


def _parse(data):
    """Extrai (tradução, idioma detectado) de qualquer um dos formatos de resposta do Google.

    - dict  {"sentences": [{"trans": ...}], "src": "en"}      -> client=gtx&dj=1 e espelho
    - lista [[["trad", "orig", ...], ...], null, "en", ...]   -> client=at
    - lista [["trad", "en"]]                                  -> client=dict-chrome-ex
    Levanta ValueError se o formato não for reconhecido.
    """
    if isinstance(data, dict):
        sentences = data.get("sentences") or []
        trans = "".join(s.get("trans", "") for s in sentences if isinstance(s, dict))
        return trans, data.get("src", "") or ""
    if isinstance(data, list) and data and isinstance(data[0], list) and data[0]:
        first = data[0]
        if isinstance(first[0], list):
            trans = "".join(
                c[0] for c in first if isinstance(c, list) and c and isinstance(c[0], str))
            src = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
            return trans, src
        if isinstance(first[0], str):
            trans = "".join(
                i[0] for i in data if isinstance(i, list) and i and isinstance(i[0], str))
            src = first[1] if len(first) > 1 and isinstance(first[1], str) else ""
            return trans, src
    raise ValueError("formato de resposta desconhecido")


def _describe(exc):
    """Resumo curto de uma exceção, para o nvda.log."""
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %s" % exc.code
    if isinstance(exc, urllib.error.URLError):
        return "rede: %s" % (exc.reason,)
    return "%s: %s" % (type(exc).__name__, exc)


# Contextos TLS e "openers", criados na primeira necessidade e compartilhados.
_openers = {}
_openersLock = threading.Lock()


def _getOpener(verify):
    with _openersLock:
        op = _openers.get(verify)
        if op is None:
            ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
            op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            op.addheaders = [("User-agent", _USER_AGENT)]
            _openers[verify] = op
        return op


def _getJson(url, timeout=_TIMEOUT):
    """GET em url e devolve o JSON decodificado. Certificado verificado; se a verificação
    falhar por causa do certificado (não por rede), repete uma vez sem verificar."""
    last = None
    for verify in (True, False):
        try:
            with _getOpener(verify).open(url, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))
        except urllib.error.URLError as e:
            if verify and isinstance(e.reason, ssl.SSLCertVerificationError):
                last = e
                continue
            raise
    raise last


class _AllFailed(Exception):
    pass


class Translator(threading.Thread):
    """Thread que traduz um texto via Google Translate e armazena o resultado.

    Atributos após execução:
        translation (str): texto traduzido
        lang_detected (str): idioma detectado (código ISO)
        error (bool): True se a tradução falhou
        limited (bool): True se a falha incluiu HTTP 429 (Google limitou as consultas desta rede)
        failures (list[str]): motivo de cada endereço que falhou, para diagnóstico
    """

    # Nome do último endereço que funcionou; vale para todas as traduções desta sessão do NVDA.
    _preferred = None

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
        self.limited      = False
        self.failures     = []
        self._first_chunk = True

    def stop(self):
        self._stop_event.set()

    # ── endereços ─────────────────────────────────────────────────────────────

    def _endpoints(self):
        eps = list(_ENDPOINTS)
        pref = Translator._preferred
        if pref:
            eps.sort(key=lambda e: e[0] != pref)  # sort é estável: o preferido sobe, o resto fica na ordem
        if self.use_mirror:
            eps.insert(0, _MIRROR)
        return eps

    def _fetch(self, chunk, lang_to):
        """Traduz um pedaço tentando os endereços em ordem. Devolve (tradução, idioma detectado).
        Levanta _AllFailed se todos falharem."""
        q = urllib.parse.quote(chunk, safe="")
        sl = urllib.parse.quote(self.lang_from, safe="")
        tl = urllib.parse.quote(lang_to, safe="")
        for name, template in self._endpoints():
            if self._stop_event.is_set():
                raise _AllFailed()
            try:
                trans, src = _parse(_getJson(template.format(sl=sl, tl=tl, q=q)))
                if not trans.strip() and chunk.strip():
                    raise ValueError("resposta sem tradução")
            except Exception as e:
                self.failures.append("%s: %s" % (name, _describe(e)))
                if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                    self.limited = True
                continue
            if name != _MIRROR[0]:
                Translator._preferred = name
            return trans, _langConversion.get(src, src)
        log.warning(
            "translateClipboard: nenhum endereço de tradução respondeu (%s)" % "; ".join(self.failures))
        raise _AllFailed()

    # ── thread ────────────────────────────────────────────────────────────────

    def run(self):
        try:
            self._run()
        except _AllFailed:
            self.error = True
        except Exception:
            self.error = True
            log.error("translateClipboard: erro inesperado ao traduzir", exc_info=True)

    def _run(self):
        for chunk in _splitChunks(self.text, self.chunksize):
            if self._stop_event.is_set():
                return
            if not self._first_chunk:
                if self._stop_event.wait(randint(1, 5)):
                    return
            trans, detected = self._fetch(chunk, self.lang_to)
            self.lang_detected = detected
            # Auto-swap: se o texto detectado já está no idioma destino, traduzir para o idioma de troca
            if (self._first_chunk
                    and self.lang_from == "auto"
                    and self.lang_detected == self.lang_to
                    and self.lang_swap is not None):
                self.lang_to = self.lang_swap
                self._first_chunk = False
                trans, detected = self._fetch(chunk, self.lang_to)
            self._first_chunk = False
            self.translation += trans
