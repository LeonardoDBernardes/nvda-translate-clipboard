# speechOnDemand.py
# Suporte ao modo "falar sob demanda" (NVDA 2024.1+).
# Copiado de instantTranslate — Copyright (C) 2023-2024 Cyrille Bougot, GPL.

import threading
import speech
import core


def isSpeechOnDemandAvailable():
    """Retorna True se o NVDA suporta o modo 'falar sob demanda' (2024.1+)."""
    try:
        speech.SpeechMode.onDemand
        return True
    except AttributeError:
        return False


def getSpeechOnDemandParameter():
    """Retorna {'speakOnDemand': True} para NVDA 2024.1+, ou {} para versões anteriores.
    Use **getSpeechOnDemandParameter() no decorator @scriptHandler.script(...)."""
    if isSpeechOnDemandAvailable():
        return {'speakOnDemand': True}
    return {}


def executeWithSpeakOnDemand(f, *args, **kwargs):
    """Executa uma função forçando o modo talk temporariamente se estiver no modo onDemand.
    Deve ser chamado apenas da thread principal."""
    if threading.get_ident() != core.mainThreadId:
        raise RuntimeError('executeWithSpeakOnDemand deve ser chamado da thread principal.')
    if not isSpeechOnDemandAvailable() or speech.getState().speechMode != speech.SpeechMode.onDemand:
        return f(*args, **kwargs)
    try:
        speech.setSpeechMode(speech.SpeechMode.talk)
        return f(*args, **kwargs)
    finally:
        speech.setSpeechMode(speech.SpeechMode.onDemand)
