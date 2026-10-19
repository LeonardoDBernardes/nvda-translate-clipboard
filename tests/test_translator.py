# -*- coding: utf-8 -*-
"""Testes do translator.py fora do NVDA (logHandler falso, rede falsa).

Rodar:  python3 -m unittest discover -s tests -v      (de dentro da pasta do repositório)
Teste ao vivo opcional (usa a internet):  TC_AO_VIVO=1 python3 -m unittest tests.test_translator.AoVivo
"""
import importlib.util
import json
import os
import ssl
import sys
import unittest
import urllib.error

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)  # logHandler falso
RAIZ = os.path.dirname(AQUI)
_spec = importlib.util.spec_from_file_location(
    "tc_translator", os.path.join(RAIZ, "globalPlugins", "translateClipboard", "translator.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

# Respostas reais capturadas em 18/10/2026 ("Hello world. How are you today?" -> pt)
RESP_GTX = {"sentences": [{"trans": "Olá mundo. ", "orig": "Hello world. "},
                          {"trans": "Como você está hoje?", "orig": "How are you today?"}], "src": "en"}
RESP_AT = [[["Olá mundo. ", "Hello world. ", None, None, 3], ["Como você está hoje?", "How are you today?", None, None, 10]],
           None, "en", None, None, None, 1, [], [["en"], None, [1], ["en"]]]
RESP_DICT = [["Olá mundo. Como você está hoje?", "en"]]


def http_erro(codigo):
    return urllib.error.HTTPError("http://x", codigo, "erro", {}, None)


class FakeRede:
    """Substitui T._getJson: responde por nome de endereço, na ordem pedida."""

    def __init__(self, respostas):
        self.respostas = respostas  # trecho da URL -> dado | Exception
        self.urls = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        for trecho, r in self.respostas.items():
            if trecho in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise urllib.error.URLError("sem rota")


class Base(unittest.TestCase):
    def setUp(self):
        self._orig = T._getJson
        T.Translator._preferred = None
        T.log.mensagens.clear()

    def tearDown(self):
        T._getJson = self._orig
        T.Translator._preferred = None

    def traduz(self, rede, texto="Hello world. How are you today?", de="auto", para="pt", **kw):
        T._getJson = rede
        t = T.Translator(de, para, texto, **kw)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive())
        return t


class Parse(unittest.TestCase):
    def test_tres_formatos(self):
        self.assertEqual(T._parse(RESP_GTX), ("Olá mundo. Como você está hoje?", "en"))
        self.assertEqual(T._parse(RESP_AT), ("Olá mundo. Como você está hoje?", "en"))
        self.assertEqual(T._parse(RESP_DICT), ("Olá mundo. Como você está hoje?", "en"))

    def test_formato_desconhecido(self):
        for ruim in ("html", 3, None, [], [[]], [1, 2]):
            with self.assertRaises(ValueError):
                T._parse(ruim)

    def test_dict_vazio_nao_quebra(self):
        self.assertEqual(T._parse({}), ("", ""))


class Failover(Base):
    def test_gtx_429_cai_para_clients5(self):
        # o caso real de 18/10: translate.googleapis.com devolve 429, clients5 responde
        rede = FakeRede({"googleapis.com": http_erro(429), "clients5.google.com/translate_a/single": RESP_AT})
        t = self.traduz(rede)
        self.assertFalse(t.error)
        self.assertEqual(t.translation, "Olá mundo. Como você está hoje?")
        self.assertEqual(t.lang_detected, "en")

    def test_primeiro_endereco_falha_usa_o_seguinte_e_lembra(self):
        rede = FakeRede({"clients5.google.com/translate_a/single": http_erro(429), "googleapis.com": RESP_GTX})
        t = self.traduz(rede)
        self.assertFalse(t.error)
        self.assertEqual(T.Translator._preferred, "googleapis-gtx")
        self.assertTrue(t.limited)  # houve um 429 no caminho
        n = len(rede.urls)
        t2 = self.traduz(rede)  # a segunda tradução já começa pelo endereço que funcionou
        self.assertIn("googleapis.com", rede.urls[n])
        self.assertEqual(len(rede.urls) - n, 1)
        self.assertFalse(t2.error)

    def test_todos_falham_marca_erro_limited_e_registra_no_log(self):
        rede = FakeRede({"google": http_erro(429)})
        t = self.traduz(rede)
        self.assertTrue(t.error)
        self.assertTrue(t.limited)
        self.assertEqual(t.translation, "")
        self.assertEqual(len(t.failures), 4)
        avisos = [m for n, m in T.log.mensagens if n == "warning"]
        self.assertEqual(len(avisos), 1)
        self.assertIn("HTTP 429", avisos[0])

    def test_falha_de_rede_nao_marca_limited(self):
        t = self.traduz(FakeRede({}))  # tudo "sem rota"
        self.assertTrue(t.error)
        self.assertFalse(t.limited)

    def test_json_invalido_passa_ao_proximo(self):
        rede = FakeRede({"clients5.google.com/translate_a/single": json.JSONDecodeError("x", "y", 0),
                         "googleapis.com": RESP_GTX})
        self.assertFalse(self.traduz(rede).error)

    def test_resposta_sem_traducao_passa_ao_proximo(self):
        rede = FakeRede({"clients5.google.com/translate_a/single": {"sentences": []}, "googleapis.com": RESP_GTX})
        t = self.traduz(rede)
        self.assertFalse(t.error)
        self.assertEqual(t.translation, "Olá mundo. Como você está hoje?")

    def test_espelho_so_na_frente_se_pedido(self):
        rede = FakeRede({"mirror.nvdadr.com": RESP_GTX})
        t = self.traduz(rede, use_mirror=True)
        self.assertFalse(t.error)
        self.assertIn("mirror.nvdadr.com", rede.urls[0])
        self.assertIsNone(T.Translator._preferred)  # o espelho não vira "preferido"
        rede2 = FakeRede({"clients5.google.com/translate_a/single": RESP_AT})
        self.traduz(rede2)
        self.assertNotIn("mirror", " ".join(rede2.urls))

    def test_url_leva_texto_escapado_e_idiomas(self):
        rede = FakeRede({"clients5": RESP_AT})
        self.traduz(rede, texto="a/b & c?")
        self.assertIn("q=a%2Fb%20%26%20c%3F", rede.urls[0])
        self.assertIn("sl=auto", rede.urls[0])
        self.assertIn("tl=pt", rede.urls[0])

    def test_stop_interrompe(self):
        T._getJson = FakeRede({"clients5": RESP_AT})
        t = T.Translator("auto", "pt", "oi")
        t.stop()
        t.start(); t.join(5)
        self.assertEqual(t.translation, "")


class Troca(Base):
    def test_auto_swap_quando_ja_esta_no_idioma_destino(self):
        # texto em português, destino pt: detecta pt e traduz para o idioma de troca (en)
        respostas = []

        def rede(url, timeout=None):
            respostas.append(url)
            if "tl=pt" in url:
                return {"sentences": [{"trans": "Olá"}], "src": "pt"}
            return {"sentences": [{"trans": "Hello"}], "src": "pt"}
        t = self.traduz(rede, texto="Olá", lang_swap="en")
        self.assertEqual(t.translation, "Hello")
        self.assertEqual(t.lang_to, "en")

    def test_lang_swap_exige_auto(self):
        with self.assertRaises(ValueError):
            T.Translator("en", "pt", "x", lang_swap="en")

    def test_codigo_iw_vira_he(self):
        t = self.traduz(FakeRede({"clients5": [[["x", "y"]], None, "iw"]}), texto="x")
        self.assertEqual(t.lang_detected, "he")


class Pedacos(Base):
    def test_texto_longo_junta_os_pedacos(self):
        texto = ". ".join(["frase numero %d" % i for i in range(40)]) + "."
        n = []

        def rede(url, timeout=None):
            n.append(1)
            return {"sentences": [{"trans": "[%d]" % len(n)}], "src": "en"}
        orig = T.randint
        T.randint = lambda a, b: 0  # sem espera entre pedaços
        try:
            t = self.traduz(rede, texto=texto, chunksize=100)
        finally:
            T.randint = orig
        self.assertFalse(t.error)
        self.assertGreater(len(n), 3)
        self.assertEqual(t.translation, "".join("[%d]" % i for i in range(1, len(n) + 1)))

    def test_split_chunks_sem_perder_texto(self):
        texto = "Um. Dois, tres! Quatro? Cinco; seis: sete."
        self.assertEqual("".join(T._splitChunks(texto, 10)), texto)


class Ssl(unittest.TestCase):
    def test_nao_ha_mais_patch_global_de_ssl(self):
        # a versão antiga trocava ssl._create_default_https_context do NVDA inteiro
        self.assertIsNot(ssl._create_default_https_context, ssl._create_unverified_context)

    def test_getjson_cai_para_sem_verificacao_so_se_o_erro_for_de_certificado(self):
        chamadas = []

        class Op:
            def __init__(self, verify): self.verify = verify

            def open(self, url, timeout=None):
                chamadas.append(self.verify)
                if self.verify:
                    raise urllib.error.URLError(ssl.SSLCertVerificationError("falha"))

                class R:
                    def __enter__(s): return s
                    def __exit__(s, *a): return False
                    def read(s): return b'{"ok": 1}'
                return R()
        orig = T._getOpener
        T._getOpener = lambda verify: Op(verify)
        try:
            self.assertEqual(T._getJson("https://x"), {"ok": 1})
            self.assertEqual(chamadas, [True, False])
            # erro de rede comum NÃO repete sem verificação
            chamadas.clear()
            T._getOpener = lambda verify: type("O", (), {"open": lambda s, u, timeout=None: (chamadas.append(verify), (_ for _ in ()).throw(urllib.error.URLError("sem rede")))[1]})()
            with self.assertRaises(urllib.error.URLError):
                T._getJson("https://x")
            self.assertEqual(chamadas, [True])
        finally:
            T._getOpener = orig


@unittest.skipUnless(os.environ.get("TC_AO_VIVO"), "defina TC_AO_VIVO=1 para testar na internet de verdade")
class AoVivo(unittest.TestCase):
    def test_traduz_de_verdade(self):
        t = T.Translator("auto", "pt", "Hello world. How are you today?")
        t.start(); t.join(60)
        print("\n  endereco:", T.Translator._preferred, "| falhas:", t.failures, "| traducao:", t.translation)
        self.assertFalse(t.error)
        self.assertIn("mundo", t.translation.lower())


if __name__ == "__main__":
    unittest.main()
