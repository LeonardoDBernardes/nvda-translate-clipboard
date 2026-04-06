import ast, os, sys

base = r'C:\projetos-claude\nvda\translateClipboard\globalPlugins\translateClipboard'
files = ['speechOnDemand.py', 'langslist.py', 'translator.py',
         'clipboard_monitor.py', 'settings.py', '__init__.py']

errors = 0
for f in files:
    path = os.path.join(base, f)
    try:
        src = open(path, 'r', encoding='utf-8').read()
        ast.parse(src)
        print(f'OK: {f}')
    except SyntaxError as e:
        print(f'ERRO em {f}: linha {e.lineno}: {e.msg}')
        errors += 1
    except Exception as e:
        print(f'ERRO em {f}: {e}')
        errors += 1

sys.exit(errors)
