# langslist.py
# Lista de idiomas suportados pelo Google Translate.
# Baseado em instantTranslate — Copyright (C) 2012-2016 Aleksey Sadovoy et al., GPL.

from languageHandler import getLanguageDescription
from logHandler import log
import addonHandler
addonHandler.initTranslation()


def g(code, short=False):
    """Retorna o nome do idioma para o código fornecido.
    Se short=True e code='auto', retorna descrição curta."""
    if short and code == "auto":
        return _("Automático")
    if code in _forced_codes:
        return _forced_codes[code]
    res = getLanguageDescription(code)
    if res is not None:
        return res
    if code in _needed_codes:
        return _needed_codes[code]
    return code


_forced_codes = {
    "ckb": _("Curdo (Sorani)"),
}

_needed_codes = {
    "auto": _("Detectar idioma automaticamente"),
    "ak":   _("Twi (Akan)"),
    "ay":   _("Aimará"),
    "bho":  _("Bhojpuri"),
    "bm":   _("Bambara"),
    "ceb":  _("Cebuano"),
    "doi":  _("Dogri"),
    "ee":   _("Ewe"),
    "eo":   _("Esperanto"),
    "gom":  _("Concani"),
    "haw":  _("Havaiano"),
    "hmn":  _("Hmong"),
    "ht":   _("Crioulo do Haiti"),
    "ilo":  _("Ilocano"),
    "jv":   _("Javanês"),
    "kri":  _("Krio"),
    "ku":   _("Curdo"),
    "la":   _("Latim"),
    "lg":   _("Luganda"),
    "ln":   _("Lingala"),
    "lus":  _("Mizo"),
    "mai":  _("Maithili"),
    "mg":   _("Malgaxe"),
    "mni-Mtei": _("Meitei (Manipuri)"),
    "my":   _("Birmanês"),
    "ny":   _("Chichewa"),
    "sd":   _("Sindi"),
    "sm":   _("Samoano"),
    "sn":   _("Shona"),
    "so":   _("Somali"),
    "st":   _("Sesoto"),
    "su":   _("Sundanês"),
    "tl":   _("Tagalog"),
    "yi":   _("Iídiche"),
}

_langcodes = [
    "auto","af","ak","am","ar","as","ay","az","be","bg","bho","bm","bn","bs","ca",
    "ceb","ckb","co","cs","cy","da","de","doi","dv","ee","el","en","eo","es","et",
    "eu","fa","fi","fil","fr","fy","ga","gd","gl","gn","gom","gu","ha","haw","he",
    "hi","hmn","hr","ht","hu","hy","id","ig","ilo","is","it","ja","jv","ka","kk",
    "km","kn","ko","kri","ku","ky","la","lb","lg","ln","lo","lt","lus","lv","mai",
    "mg","mi","mk","ml","mn","mni-Mtei","mr","ms","mt","my","ne","nl","no","nso",
    "ny","om","or","pa","pl","ps","pt","qu","ro","ru","rw","sa","sd","si","sk","sl",
    "sm","sn","so","sq","sr","st","su","sv","sw","ta","te","tg","th","ti","tk","tl",
    "tr","ts","tt","ug","uk","ur","uz","vi","xh","yi","yo","zh-CN","zh-TW","zu",
]

langslist = {}
for _code in _langcodes:
    _name = g(_code)
    try:
        _existing = langslist[_name]
        log.error(f'translateClipboard/langslist: nome duplicado "{_name}" para código "{_code}" (já existe "{_existing}")')
    except KeyError:
        langslist[_name] = _code
