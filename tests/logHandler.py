"""logHandler falso para os testes fora do NVDA: guarda as mensagens em listas."""


class _Log:
    def __init__(self):
        self.mensagens = []

    def _reg(self, nivel):
        def f(msg, *a, **k):
            self.mensagens.append((nivel, msg))
        return f

    def __getattr__(self, nome):
        if nome.startswith("_"):
            raise AttributeError(nome)
        return self._reg(nome)


log = _Log()
