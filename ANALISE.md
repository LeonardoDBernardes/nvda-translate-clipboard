# Análise e Plano — Addon translateClipboard

Data de início: 2026-04-05  
NVDA instalado: **2025.3.2** (Python 3.11)  
Objetivo: Unificar as funcionalidades de **instantTranslate** e **zPortapapeles** em um único addon compatível com NVDA 2023.1 até 2025.x.

---

## 1. O que cada addon faz

### instantTranslate (v4.8.2 — testado até NVDA 2025.1.2)
**Fonte:** `AppData\Roaming\nvda\addons\instantTranslate\`

Funcionalidades:
- Traduz texto **selecionado** (atalho de camada: `t`)
- Traduz texto da **área de transferência** (`shift+t`)
- Traduz o **último texto falado** pelo NVDA (`l`)
- **Auto-tradução**: intercepta toda fala do NVDA e traduz em tempo real (`v`)
- Detecta idioma automaticamente
- Troca idioma de origem/destino (`s`)
- Anuncia idiomas atuais (`a`)
- Copia resultado para área de transferência
- Identifica idioma de texto selecionado (`i`)
- Copia última tradução para área de transferência (`c`)
- Painel de configurações no NVDA

**Atalho de entrada:** `NVDA+Shift+T` → ativa modo de camada, depois pressione letras.

**Motor de tradução:** `translator.py`
- Usa `https://translate.googleapis.com/translate_a/single?client=gtx&...`
- Suporta mirror: `translate.googleapis.mirror.nvdadr.com`
- Divide textos longos em chunks de 3000 caracteres
- Thread separada para não bloquear NVDA
- Cache com `@lru_cache()`

**Compatibilidade com NVDA novo:**
- Trata `SpeechMode` com try/except para NVDA 2024.1+ vs ≤ 2023.3 ✓
- Usa `version_year >= 2021` para selecionar módulo de speech ✓
- `getSpeechOnDemandParameter()` para suporte ao modo "falar sob demanda" ✓
- Bem atualizado, sem problemas críticos.

---

### zPortapapeles (v0.5.1 — testado até NVDA 2023.1 — DESATUALIZADO)
**Fonte:** `AppData\Roaming\nvda\addons\zPortapapeles\`

Funcionalidades:
- **Anuncia** as teclas Ctrl+C/X/V/Z/Y/A com voz e/ou som (arquivos .wav)
- **Histórico** da área de transferência (thread que monitora mudanças)
- **Modo Jogo** (`isGame`): monitora clipboard e auto-traduz o conteúdo novo
- Janela de histórico (lista com itens copiados, pode re-colar)
- Sons: copiar.wav, cortar.wav, pegar.wav, todo.wav, rehacer.wav, desacer.wav

**Motor de tradução interno (`trans_clipboard/core.py`):**
- Usa `http://translate.google.com/m?tl=%s&sl=%s&q=%s` (scraping de HTML)
- Extrai resultado com regex: `class="(?:t0|result-container)">(.*?)<`
- **QUEBRADO / INSTÁVEL**: o Google mudou o HTML da versão mobile, essa regex falha
- Só suporta 12 idiomas (lista fixa em `ajustes.py`)
- Não tem cache, não divide texto longo

**Problemas de compatibilidade com NVDA 2024+:**
1. `speech.setSpeechMode(0)` — em NVDA 2024+, deve usar enum `speech.SpeechMode.off`
   - O código passa `0` (inteiro) e salva `self.old = speech.getState()` — pode quebrar
2. `lastTestedNVDAVersion = 2023.1.0` — nunca atualizado
3. `html.HTMLParser().unescape()` — removido no Python 3.9 (NVDA usa Python 3.11)
   - O código faz `parser = html; parser.unescape(text)` — `html.unescape` é função, não método de objeto. Isso **quebra** no Python 3.
4. A janela de histórico usa `CustomCheckListBox` e outros widgets — pode ter mudanças na API do NVDA

---

## 2. O que o novo addon (translateClipboard) deve ter

### Funcionalidades unificadas

| Funcionalidade | Origem | Prioridade |
|---|---|---|
| Traduzir texto selecionado | instantTranslate | Alta |
| Traduzir área de transferência | instantTranslate | Alta |
| Traduzir último texto falado | instantTranslate | Alta |
| Auto-tradução de toda fala | instantTranslate | Média |
| Trocar idiomas / detectar idioma | instantTranslate | Alta |
| Anunciar teclas Ctrl+C/X/V/Z/Y | zPortapapeles | Alta |
| Sons para ações de clipboard | zPortapapeles | Média |
| Histórico de clipboard | zPortapapeles | Média |
| Modo Jogo (auto-traduz clipboard) | zPortapapeles | Média |
| Painel de configurações NVDA | ambos | Alta |

### Motor de tradução
Usar **apenas** o motor do instantTranslate (`translate.googleapis.com`).
Descartar completamente o `trans_clipboard` do zPortapapeles.

---

## 3. Problemas a corrigir no código herdado

### De zPortapapeles:

**Problema 1 — `speech.setSpeechMode(int)`**
```python
# Código antigo (quebra no NVDA 2024+):
self.old = speech.getState()
speech.setSpeechMode(0)
speech.setSpeechMode(self.old)

# Correto para NVDA 2024+:
speech.setSpeechMode(speech.SpeechMode.off)
# ou com compatibilidade:
try:
    speech.setSpeechMode(speech.SpeechMode.off)
except AttributeError:
    speech.setSpeechMode(0)
```

**Problema 2 — `html.unescape` em Python 3.9+**
O `trans_clipboard/core.py` faz:
```python
parser = html  # módulo html
return (parser.unescape(text))  # html.unescape(text) — funciona
```
Na verdade isso funciona pois `html.unescape` é função do módulo, mas a função está
em um motor que usa HTTP (não HTTPS) e scraping HTML frágil — descartar tudo.

**Problema 3 — Motor de tradução quebrado**
`trans_clipboard` usa `http://translate.google.com/m?` com regex de HTML.
Google mudou o layout. Substituir pelo motor do instantTranslate.

**Problema 4 — `getState()` retorna objeto diferente entre versões**
```python
# Novo NVDA retorna SpeechMode enum, não inteiro
# Precisa tratar com try/except como faz instantTranslate
```

### De instantTranslate:
- Bem atualizado, poucos ajustes necessários
- Remover o `donate_dialog.py` (não necessário no addon próprio)
- O `speechOnDemand.py` é útil e deve ser mantido

---

## 4. Estrutura de arquivos do novo addon

```
translateClipboard/
├── manifest.ini
├── installTasks.py
├── globalPlugins/
│   └── translateClipboard/
│       ├── __init__.py          ← lógica principal (gestos, scripts)
│       ├── translator.py        ← motor Google Translate (do instantTranslate)
│       ├── clipboard.py         ← gerenciamento de clipboard (do zPortapapeles)
│       ├── settings.py          ← painel de configurações NVDA
│       ├── langslist.py         ← lista de idiomas (do instantTranslate)
│       ├── speechOnDemand.py    ← suporte modo falar sob demanda
│       └── sounds/
│           ├── copy.wav
│           ├── cut.wav
│           ├── paste.wav
│           ├── selectAll.wav
│           ├── undo.wav
│           └── redo.wav
├── locale/
│   └── pt_BR/
│       └── LC_MESSAGES/
│           └── nvda.po
└── doc/
    └── pt_BR/
        └── readme.md
```

---

## 5. Atalhos planejados

### Camada de tradução (NVDA+Shift+T → letra):
- `t` — traduzir texto selecionado
- `Shift+T` — traduzir área de transferência
- `l` — traduzir último texto falado
- `v` — alternar auto-tradução de fala
- `s` — trocar idiomas
- `a` — anunciar idiomas atuais
- `c` — copiar última tradução
- `i` — identificar idioma do texto selecionado
- `o` — abrir configurações
- `h` — ajuda

### Histórico de clipboard:
- `NVDA+Shift+H` — abrir janela de histórico (ou atalho configurável)

---

## 6. Configurações a expor no painel

### Tradução:
- Idioma de origem (padrão: auto)
- Idioma de destino (padrão: idioma do sistema)
- Idioma de troca (padrão: en)
- Copiar tradução para área de transferência: sim/não
- Auto-troca quando texto já está no idioma destino: sim/não
- Usar mirror de tradução: sim/não
- Substituir underscores por espaços: sim/não

### Clipboard:
- Anunciar teclas (Ctrl+C/X/V/Z/Y): sim/não
- Reproduzir sons: sim/não
- Habilitar histórico: sim/não
- Intervalo de monitoramento: [0.1s, 0.2s, 0.3s, 0.4s, 0.5s, 1s]
- Anunciar texto ao copiar: sim/não
- Modo Jogo (auto-traduzir clipboard): sim/não
- Idioma para tradução automática do clipboard
- Intervalo do modo jogo

---

## 7. Estado atual do desenvolvimento

- [x] Análise dos dois addons concluída
- [x] Diretório criado em `C:\projetos-claude\nvda\translateClipboard\`
- [x] `manifest.ini` criado
- [x] `translator.py` (adaptado do instantTranslate)
- [x] `clipboard_monitor.py` (adaptado do zPortapapeles, bugs corrigidos)
- [x] `settings.py` (painel unificado com seções Tradução e Clipboard)
- [x] `__init__.py` (plugin principal completo)
- [x] Sons copiados do zPortapapeles e renomeados
- [x] `langslist.py` com lista completa de idiomas
- [x] Verificação de sintaxe Python: todos OK
- [x] Pacote `.nvda-addon` gerado em `Desktop\translateClipboard-1.0.0.nvda-addon`
- [x] GitHub: LeonardoDBernardes/nvda-translate-clipboard
- [ ] Teste no NVDA (amanhã pelo Leo)

---

## 8. Análise de Compatibilidade por Versão do NVDA

### Histórico das mudanças que quebram addons antigos

#### NVDA 2021.1
- `speech.speech` module separado de `speech` — código antigo que fazia `import speech; speech.speak()` precisa usar `speech.speech.speak()` ou verificar versão.
- **Impacto no instantTranslate:** já tratado com `speechModule = speech.speech if version_year>=2021 else speech`
- **Impacto no zPortapapeles:** usa `speech.setSpeechMode()` diretamente — ok para essa versão pois `setSpeechMode` ainda aceita inteiro

#### NVDA 2023.2
- `speech.setSpeechMode()` passou a usar o enum `speech.SpeechMode` com valores:
  - `SpeechMode.off = 0`
  - `SpeechMode.beeps = 1`
  - `SpeechMode.talk = 2`
- Ainda aceita inteiro (0, 1, 2) por compatibilidade nessa versão
- **Impacto:** nenhum imediato, mas código com inteiro é frágil

#### NVDA 2024.1 ← **Principal causa da quebra do zPortapapeles**
- Adicionado **modo "Falar sob demanda"** (`SpeechMode.onDemand = 3`)
- Scripts que chamam `ui.message()` precisam do parâmetro `speakOnDemand=True` no decorator `@scriptHandler.script(...)` para funcionar nesse modo
- `speech.getState()` agora retorna objeto `SpeechState` com campos `.speechMode`, `.isPaused` etc. — não é mais um inteiro simples
- **Impacto crítico no zPortapapeles:**
  - `self.old = speech.getState()` → salva objeto `SpeechState`
  - `speech.setSpeechMode(self.old)` → passa `SpeechState` onde espera `SpeechMode` enum → **TypeError/crash**
  - Scripts sem `speakOnDemand` ficam silenciosos no modo falar sob demanda

#### NVDA 2024.2 / 2024.3
- `gui.settingsDialogs` — pequenas mudanças nos painéis de configuração
- `NVDAObjects.UIA` — algumas mudanças em eventos UIA
- **Impacto no zPortapapeles:** possíveis avisos de depreciação nos painéis de configuração

#### NVDA 2025.1 / 2025.3 (versão instalada: 2025.3.2)
- Python 3.11 confirmado (`python311.dll`)
- `speechViewer.SPEECH_ITEM_SEPARATOR` ainda existe (usado pelo instantTranslate)
- `speech.SpeechMode` enum confirmado na API
- `setSpeechMode`, `getState`, `speak`, `speakMessage` todos presentes

### Resumo: por que zPortapapeles quebrou no NVDA 2024.1+

```
NVDA 2024.1 mudou speech.getState() para retornar SpeechState(namedtuple)
                                           ↓
zPortapapeles faz: self.old = speech.getState()  → salva SpeechState
                   speech.setSpeechMode(self.old) → ERRO: espera SpeechMode enum
                                           ↓
                   Script falha silenciosamente ou crasha
                                           ↓
                   Addon fica instável / funcionalidades param de responder
```

### Correção necessária
```python
# Código correto compatível com NVDA 2021.1 até 2025.x:
try:
    # NVDA 2024.1+
    _oldSpeechMode = speech.getState().speechMode
    speech.setSpeechMode(speech.SpeechMode.off)
    # ... fazer algo ...
    speech.setSpeechMode(_oldSpeechMode)
except AttributeError:
    # NVDA <= 2023.3
    speech.setSpeechMode(0)
    # ... fazer algo ...
    speech.setSpeechMode(2)  # talk
```

### Notas finais
- **NVDA mínimo alvo:** 2023.1
- **NVDA máximo testado:** 2025.3.2 (Python 3.11)
- **API speech:** usar `SpeechMode` enum com fallback para inteiro
- **Falar sob demanda:** incluir `speakOnDemand=True` em todos os scripts que anunciam texto
- Motor de tradução: `translate.googleapis.com` (HTTPS, não scraping)

---

## 9. Referências

- instantTranslate GitHub: https://github.com/addonFactory/instantTranslate
- zPortapapeles GitHub: https://github.com/hxebolax/zPortapapeles
- Documentação NVDA addon dev: https://www.nvaccess.org/files/nvda/documentation/developerGuide.html
