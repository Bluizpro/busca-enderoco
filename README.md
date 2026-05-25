# Automação Consulta CLCB — PMESP

Sistema de automação para consulta em lote de CLCBs (Certificado de Licenciamento do Corpo de Bombeiros) no portal [Via Fácil](http://viafacil2.policiamilitar.sp.gov.br/SGSCI/PUBLICO/PESQUISARCLCB.ASPX) da Polícia Militar de SP, com busca automática de telefones via Google Maps.

---

## Visão Geral

O sistema opera em **3 blocos paralelos**:

| Bloco | Função | Thread | Início |
|-------|--------|--------|--------|
| **1 — Consulta PMESP** | Preenche número CLCB → clica Pesquisar → extrai dados | Principal | Imediato |
| **2 — Telefones (Maps)** | Busca telefone no Google Maps a cada 10 endereços novos | Daemon | Imediato |
| **3 — Controle de Pausa** | ENTER no terminal pausa/retoma a automação | Daemon | Após setup inicial ¹ |

> ¹ A thread de pausa só inicia **depois** de todos os `input()` de configuração (quantidade, CAPTCHA), com limpeza do buffer stdin via `termios.tcflush` para evitar pausas acidentais.

### Fluxo simplificado

```
[Usuário resolve CAPTCHA] → Loop automático (N consultas)
         ↓                          ↓
   resultados.xlsx  ←── salva dados a cada consulta
         ↓
   Thread Maps lê → busca telefone → resultados_com_telefone.xlsx
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

#### Opcionais (OCR para telefones em fotos)

```bash
pip install pytesseract pillow
sudo apt install tesseract-ocr tesseract-ocr-por
```

> Se não instaladas, o OCR é desativado silenciosamente — sem impacto no restante.

---

## Como Usar

```bash
python3 automacao.py
```

### Primeira execução

1. Dois navegadores Chrome abrem automaticamente (um para PMESP, outro para Maps).
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
| `resultados_com_telefone.xlsx` | Mesmos dados + coluna **Telefone** (via Maps) |
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
| `LOTE` | `10` | Quantidade de endereços acumulados antes de buscar telefones |
| `INTERVALO_CHECK` | `15` | Segundos entre cada verificação do arquivo pela thread Maps |
| Delay entre consultas | `5–15s` | Aleatório para evitar bloqueio |
| Delay entre buscas Maps | `4–9s` | Aleatório por busca de telefone |

---

## Estratégia de Busca de Telefone

A thread Maps tenta 3 abordagens em cascata:

1. **Seletores CSS** — busca elementos com `data-item-id="phone"`, `aria-label` contendo "Telefone", etc.
2. **Clique no primeiro resultado** — se a busca retornou lista, clica e tenta extrair novamente.
3. **OCR (fallback)** — tira screenshot das fotos do estabelecimento no Maps e aplica Tesseract para reconhecer padrões de telefone `(XX) XXXXX-XXXX`.

---

## Estrutura do Projeto

```
consulta/
├── automacao.py                 # Script principal (436 linhas)
├── README.md                    # Esta documentação
├── resultado/
│   ├── resultados.xlsx          # Dados das consultas CLCB
│   ├── resultados_com_telefone.xlsx  # Dados + telefones do Maps
│   └── sessao.json              # Estado para retomada
└── .venv/                       # Ambiente virtual Python
```

---

## Limitações Conhecidas

- **CAPTCHA manual**: o primeiro CAPTCHA precisa ser resolvido pelo usuário.
- **Anti-bot**: se o site detectar automação, pode bloquear — os delays aleatórios mitigam isso.
- **Maps sem resultado**: estabelecimentos sem presença no Google Maps retornam `N/D`.
- **OCR**: depende da qualidade da foto; taxa de acerto é baixa.
- **Escrita no Excel**: `pandas.to_excel` reescreve o arquivo inteiro a cada consulta (funcional, mas não ideal para volumes muito grandes).
