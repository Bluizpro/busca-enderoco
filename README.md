# Automação Consulta CLCB — PMESP

Sistema de automação para consulta em lote de CLCBs (Certificado de Licenciamento do Corpo de Bombeiros) no portal [Via Fácil](http://viafacil2.policiamilitar.sp.gov.br/SGSCI/PUBLICO/PESQUISARCLCB.ASPX) da Polícia Militar de SP, com busca automática de telefones via Google Search.

---

## Visão Geral

O sistema opera em **3 blocos paralelos**:

| Bloco | Função | Thread | Início |
|-------|--------|--------|--------|
| **1 — Consulta PMESP** | Preenche número CLCB → clica Pesquisar → extrai dados | Principal | Imediato |
| **2 — Telefones (Google)** | Busca telefone no Google Search a cada 10 endereços novos | Daemon | Imediato |
| **3 — Controle de Pausa** | ENTER no terminal pausa/retoma a automação | Daemon | Após setup inicial ¹ |

> ¹ A thread de pausa só inicia **depois** de todos os `input()` de configuração (quantidade, CAPTCHA), com limpeza do buffer stdin via `termios.tcflush` para evitar pausas acidentais.

### Fluxo simplificado

```
[Usuário resolve CAPTCHA] → Loop automático (N consultas)
         ↓                          ↓
   resultados.xlsx  ←── salva dados a cada consulta
         ↓
   Thread Google lê → busca telefone → resultados_com_telefone.xlsx
```

---

## Requisitos

- **Python 3.8+**
- **Google Chrome** (versão compatível com ChromeDriver)
- **ChromeDriver** no PATH (o Selenium gerencia automaticamente com `selenium >= 4.6`)

### Dependências Python

```bash
pip install pandas selenium openpyxl
```

#### Opcionais (OCR — atualmente desativado)

```bash
pip install pytesseract pillow
sudo apt install tesseract-ocr tesseract-ocr-por
```

> O OCR era usado para extrair telefones de fotos no Google Maps. Com a mudança para Google Search, esse fallback não é mais acionado — as libs podem ser instaladas sem impacto.

---

## Como Usar

```bash
python3 automacao.py
```

### Primeira execução

1. Dois navegadores Chrome abrem automaticamente (um para o site da PMESP, outro para o Google Search).
2. Defina a **quantidade de consultas** desejada.
3. No navegador PMESP: resolva o **CAPTCHA**, digite um número CLCB e clique em "Pesquisar".
4. Pressione **ENTER** no terminal — o sistema salva o primeiro resultado e entra em modo automático.

### Controles durante execução

| Tecla | Ação |
|-------|------|
| `ENTER` | Pausa / retoma a automação (navegadores permanecem abertos) |
| `Ctrl+C` | Encerra o programa (estado é salvo para retomar depois) |

### Retomada de sessão

Se interrompido, ao rodar novamente o sistema detecta a sessão anterior:

```
╔══════════════════════════════════════════════════╗
║  Sessão anterior encontrada!                     ║
║  Último número: 1023014  |  Feitas: 15/100       ║
╚══════════════════════════════════════════════════╝
Deseja continuar de onde parou? (s/n):
```

---

## Arquivos de Saída

Todos em `resultado/`:

| Arquivo | Conteúdo |
|---------|----------|
| `resultados.xlsx` | Dados brutos de cada CLCB consultado |
| `resultados_com_telefone.xlsx` | Mesmos dados + coluna **Telefone** (via Google Search) |
| `sessao.json` | Estado da sessão para retomada (apagado ao concluir) |

### Colunas extraídas

| Coluna | Exemplo |
|--------|---------|
| `numero` | 1023001 |
| `Situação` | CLCB Vigente |
| `Proprietário` | EMPRESA XYZ LTDA |
| `Logradouro` | RUA JOÃO PÍRES, 649 |
| `Bairro` | CENTRO |
| `Município` | ATIBAIA |
| `Ocupação` | COMERCIAL |
| `Telefone` ¹ | (11) 4412-3456 |

¹ Apenas em `resultados_com_telefone.xlsx`. Valores possíveis: número, `N/D` (não encontrado) ou `SEM ENDEREÇO`.

---

## Configurações

Constantes no topo de `automacao.py`:

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `LOTE` | `10` | Endereços acumulados antes de iniciar busca de telefones |
| `INTERVALO_CHECK` | `15` | Segundos entre cada verificação do arquivo pela thread Google |
| Delay entre consultas PMESP | `5–15s` | Aleatório para evitar bloqueio |
| Delay entre buscas Google | `4–9s` | Aleatório por busca de telefone |

---

## Estratégia de Busca de Telefone

A thread monta a query com `Proprietário + Logradouro + Bairro + Município` e pesquisa no Google:

```
https://www.google.com/search?q=<query>+telefone
```

Após 3 segundos de carregamento, extrai o texto do `body` e aplica o regex `\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}` para capturar o primeiro número de telefone encontrado. Retorna `N/D` se nenhum for localizado.

> **Nota:** O código de busca via Google Maps (seletores CSS, OCR em fotos) está preservado comentado no BLOCO 2 do script para referência futura.

---

## Configuração do Chrome

O navegador é iniciado com as seguintes opções para garantir estabilidade e evitar bloqueios:

| Flag | Motivo |
|------|--------|
| `--no-sandbox` | Evita crash em Linux sem privilégio root |
| `--disable-dev-shm-usage` | Previne OOM em sistemas com `/dev/shm` limitado |
| `--disable-blink-features=AutomationControlled` | Remove `navigator.webdriver=true` usado para detectar Selenium |
| `excludeSwitches: enable-automation` | Remove barra de aviso "controlado por software automatizado" |
| User-Agent real | Evita bloqueio por fingerprint de UA |
| `implicitly_wait(10)` | Rede de segurança: toda busca por elemento aguarda até 10s |

---

## Robustez e Diagnóstico

Todas as interações críticas com o site usam `WebDriverWait` em vez de `time.sleep` fixo:

| Ponto | Condição aguardada | Timeout |
|-------|--------------------|---------|
| `acessar_pagina` | Campo `txtNumeroCLCB` presente no DOM | 20s |
| `preencher_campo` | Campo clicável (visible + enabled) | 15s |
| `clicar_pesquisar` | Botão clicável após postback ASP.NET | 15s |
| `extrair_resultado` | Texto `"Situação:"` presente no body | 8s |

Em caso de falha, logs `[ERRO]` e `[DEBUG]` exibem o título e URL da página atual para diagnóstico imediato.

---

## Estrutura do Projeto

```
consulta/
├── automacao.py                      # Script principal
├── README.md                         # Esta documentação
├── resultado/
│   ├── resultados.xlsx               # Dados das consultas CLCB
│   ├── resultados_com_telefone.xlsx  # Dados + telefones do Google
│   └── sessao.json                   # Estado para retomada
└── .venv/                            # Ambiente virtual Python
```

---

## Limitações Conhecidas

- **CAPTCHA manual**: o primeiro CAPTCHA precisa ser resolvido pelo usuário.
- **Anti-bot**: se o site detectar automação, pode bloquear — os delays aleatórios e as flags do Chrome mitigam isso.
- **Google Search**: pode exibir tela de consentimento de cookies em novas sessões; se telefones retornarem `N/D` com frequência, inspecione o `body.text` capturado.
- **Escrita no Excel**: `pandas.to_excel` reescreve o arquivo inteiro a cada consulta — funcional, mas não ideal para volumes muito grandes.
