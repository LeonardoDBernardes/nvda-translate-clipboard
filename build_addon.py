"""Empacota o addon como arquivo .nvda-addon para instalação."""
import zipfile
import os
import shutil

src_dir = r'C:\projetos-claude\nvda\translateClipboard'
output  = r'C:\Users\leodb\Desktop\translateClipboard-1.0.0.nvda-addon'

# Arquivos/pastas a excluir do pacote
EXCLUDES = {
    '__pycache__', '.git', '.gitignore',
    'ANALISE.md', 'check_syntax.py', 'build_addon.py',
}

def should_include(path):
    parts = path.replace('\\', '/').split('/')
    return not any(p in EXCLUDES for p in parts)

with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src_dir):
        # Filtrar diretórios excluídos
        dirs[:] = [d for d in dirs if d not in EXCLUDES]
        for file in files:
            filepath = os.path.join(root, file)
            arcname  = os.path.relpath(filepath, src_dir)
            if should_include(arcname):
                z.write(filepath, arcname)
                print(f'  + {arcname}')

print(f'\nAddon criado: {output}')
