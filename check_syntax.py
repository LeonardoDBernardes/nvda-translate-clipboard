"""Confere a sintaxe de todos os .py do add-on (sem importar nada do NVDA)."""
import ast
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'globalPlugins', 'translateClipboard')
erros = 0
for nome in sorted(os.listdir(BASE)):
    if not nome.endswith('.py'):
        continue
    try:
        with open(os.path.join(BASE, nome), encoding='utf-8') as f:
            ast.parse(f.read())
        print('OK:', nome)
    except SyntaxError as e:
        print('ERRO em %s: linha %s: %s' % (nome, e.lineno, e.msg))
        erros += 1
sys.exit(erros)
