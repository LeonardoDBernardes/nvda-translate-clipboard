"""Empacota o add-on como .nvda-addon (zip com o manifest.ini na raiz).

Uso:  python3 build_addon.py [pasta-de-saida]      (padrão: ./dist)
Gera dist/translateClipboard-<versao>.nvda-addon a partir da pasta deste arquivo.
"""
import os
import re
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
EXCLUDES = {'__pycache__', '.git', '.gitignore', 'ANALISE.md', 'check_syntax.py',
            'build_addon.py', 'tests', 'dist'}


def versao():
    with open(os.path.join(AQUI, 'manifest.ini'), encoding='utf-8') as f:
        m = re.search(r'^version\s*=\s*(\S+)', f.read(), re.M)
    return m.group(1)


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else os.path.join(AQUI, 'dist')
    os.makedirs(saida, exist_ok=True)
    destino = os.path.join(saida, 'translateClipboard-%s.nvda-addon' % versao())
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        for raiz, dirs, arquivos in os.walk(AQUI):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDES)
            for nome in sorted(arquivos):
                caminho = os.path.join(raiz, nome)
                rel = os.path.relpath(caminho, AQUI).replace('\\', '/')
                if rel.split('/')[0] in EXCLUDES or nome in EXCLUDES or nome.endswith(('.pyc', '.nvda-addon')):
                    continue
                z.write(caminho, rel)
                print('  +', rel)
    print('\nAdd-on criado:', destino)


if __name__ == '__main__':
    main()
