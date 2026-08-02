import pandas as pd
import os
import sys
import time
import random
import threading
import re
import io
import json
import termios
import unicodedata
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    # pyrefly: ignore [missing-import]
    import pytesseract
    from PIL import Image
    OCR_DISPONIVEL = True
except ImportError:
    OCR_DISPONIVEL = False

# Padrão antigo (mantido por compatibilidade) — aceita qualquer 10-11 dígitos.
PADRAO_TELEFONE = re.compile(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}')

# Padrão ESTRITO: exige separador (espaço/hífen/ponto) antes dos 4 dígitos finais.
# Isso elimina CNPJ/CPF/IDs colados (ex.: 14593353000) que o padrão antigo pegava.
# Casa formatos reais: (12) 98107-0208 | (16) 3947-4630 | 12 98107-0208 | (11)3101-9467
PADRAO_TELEFONE_FORMATADO = re.compile(r'\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]\d{4}')

# DDDs brasileiros que realmente existem — usados para descartar números inválidos.
DDDS_VALIDOS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19,
    21, 22, 24, 27, 28,
    31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49,
    51, 53, 54, 55,
    61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79,
    81, 82, 83, 84, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 96, 97, 98, 99,
}

def _telefone_valido(texto):
    """Valida se um trecho é um telefone brasileiro plausível (com DDD)."""
    d = re.sub(r"\D", "", texto)
    
    # Rejeita números falsos/placeholder óbvios
    if "00000" in d or "11111" in d or "999999" in d or "123456" in d:
        return False
        
    if len(d) == 11:
        return int(d[:2]) in DDDS_VALIDOS and d[2] == "9"
    if len(d) == 10:
        return int(d[:2]) in DDDS_VALIDOS and d[2] in "2345"
    return False

def _normalizar(s):
    """Minúsculas e sem acento, para comparação robusta com o texto do Google."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()

# Palavras genéricas que NÃO identificam o estabelecimento (não servem como pista).
PALAVRAS_IGNORADAS = {
    "rua", "avenida", "alameda", "praca", "travessa", "rodovia", "estrada", "via",
    "largo", "viela", "rod", "jardim", "vila", "parque", "centro", "conjunto",
    "habitacional", "residencial", "distrito", "area", "rural", "bairro", "cohab",
    "sao", "santa", "santo", "dos", "das",
    "ltda", "eireli", "epp", "mei", "comercio", "comercial", "industria",
    "servicos", "servico", "empresa", "company",
}

def _tokens_identificadores(texto):
    """Palavras significativas (>=4 letras, sem termos genéricos de endereço)."""
    norm = _normalizar(texto)
    return [t for t in re.findall(r"[a-z]+", norm)
            if len(t) >= 4 and t not in PALAVRAS_IGNORADAS]

def extrair_telefone_confiavel(texto, row):
    """Retorna um telefone com ALTA precisão usando sistema de pontuação."""
    municipio = _normalizar(row.get("Município", "")).strip()
    alvos_end = set(_tokens_identificadores(row.get("Logradouro", "")))
    if municipio:
        alvos_end.add(municipio)
        
    alvos_prop = set(_tokens_identificadores(row.get("Proprietário", "")))
    
    if not alvos_end and not alvos_prop:
        return "N/D"

    candidatos = []
    
    for m in PADRAO_TELEFONE_FORMATADO.finditer(texto):
        if not _telefone_valido(m.group()):
            continue
            
        numero_limpo = re.sub(r"\D", "", m.group())
            
        # Pega uma janela de 250 caracteres ao redor do telefone (cobre os resumos longos do Google IA)
        ini = max(0, m.start() - 250)
        fim = min(len(texto), m.end() + 250)
        contexto = _normalizar(texto[ini:fim])
        
        tem_end = any(alvo in contexto for alvo in alvos_end)
        tem_prop = any(alvo in contexto for alvo in alvos_prop) if alvos_prop else False
        
        # Só aceita se tiver o endereço OU o nome da empresa na mesma frase/linha
        if not tem_end and not tem_prop:
            continue
            
        contexto_antes = texto[max(0, m.start() - 40):m.start()].lower()
        tem_rotulo = any(w in contexto_antes for w in ["telefone", "ligar", "whatsapp", "whats", "contato", "contatos", "delivery"])
        
        pontos = 0
        if tem_prop: pontos += 3       # Nome da empresa é a prova mais forte
        if tem_rotulo: pontos += 2     # Rótulo explícito ("Telefone", "Contato", "Delivery")
        if tem_end: pontos += 1        # Endereço
            
        candidatos.append({
            "numero": m.group().strip(),
            "pontos": pontos
        })

    if not candidatos:
        return "N/D"
        
    # Ordena do maior pontuador para o menor
    candidatos.sort(key=lambda c: c["pontos"], reverse=True)
    
    numeros_unicos = []
    numeros_vistos = set()
    max_pontos = candidatos[0]["pontos"]
    
    for c in candidatos:
        num = c["numero"]
        # Só pega números novos (sem repetição)
        if num not in numeros_vistos:
            # Se a pontuação for igual ou no máximo 1 ponto menor que o melhor candidato
            # OU se for um candidato muito bom de qualquer forma (>= 2 pontos)
            if c["pontos"] >= max_pontos - 1 or c["pontos"] >= 2:
                numeros_unicos.append(num)
                numeros_vistos.add(num)
                
        # Retorna no máximo 3 telefones para não poluir a planilha
        if len(numeros_unicos) >= 3:
            break

    return " / ".join(numeros_unicos)

# ── Configurações ─────────────────────────────────────────────────────────────
URL        = "http://viafacil2.policiamilitar.sp.gov.br/SGSCI/PUBLICO/PESQUISARCLCB.ASPX"
ID_INPUT   = "txtNumeroCLCB"
ID_BOTAO   = "btnPesquisar"
EXCEL_FILE      = "resultado/resultados.xlsx"
EXCEL_SAIDA     = "resultado/resultados_com_telefone.xlsx"
SESSAO_FILE     = "resultado/sessao.json"

# Perfil DEDICADO da automação (pasta própria, separada do seu Chrome pessoal).
# É persistente: cookies/login ficam salvos entre execuções, então o bloqueio
# "tráfego incomum" do Google diminui com o tempo e, se precisar resolver um
# CAPTCHA, você resolve só uma vez. NÃO mexe no seu navegador do dia a dia —
# pode deixá-lo aberto normalmente.
CHROME_PERFIL_DIR = os.path.abspath("resultado/perfil_chrome")

# Evento global de pausa (set = pausado, clear = rodando)
pause_event = threading.Event()


# ══════════════════════════════════════════════════════════════════════════════
#  BLOCO 1 — AUTOMAÇÃO PMESP (original, sem alterações)
# ══════════════════════════════════════════════════════════════════════════════

def limpar_lock_perfil():
    """Remove locks órfãos do perfil dedicado do Chrome.

    Quando a automação é encerrada à força (Ctrl+C, kill, queda de energia), o
    Chrome deixa para trás SingletonLock/Cookie/Socket em CHROME_PERFIL_DIR e a
    execução seguinte falha com "session not created". Só apaga se o processo
    dono do lock realmente não existir mais — nunca derruba um Chrome vivo.
    """
    lock = os.path.join(CHROME_PERFIL_DIR, "SingletonLock")
    if not os.path.islink(lock) and not os.path.exists(lock):
        return

    # O alvo do symlink tem o formato "hostname-pid"
    try:
        dono = os.readlink(lock).rsplit("-", 1)[-1]
        pid = int(dono)
    except (OSError, ValueError):
        pid = None

    if pid is not None:
        try:
            os.kill(pid, 0)          # sinal 0: só testa se o processo existe
        except ProcessLookupError:
            pass                     # dono morto → lock é órfão, pode limpar
        except PermissionError:
            return                   # existe e é de outro usuário → não mexe
        else:
            return                   # Chrome vivo usando o perfil → não mexe

    for nome in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.unlink(os.path.join(CHROME_PERFIL_DIR, nome))
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[Chrome] Não foi possível remover {nome}: {e}")
    print("[Chrome] Lock órfão do perfil removido (execução anterior interrompida).")

def iniciar_navegador(usar_perfil=False):
    """Abre o Chrome com opções necessárias para automação estável.

    Com usar_perfil=True, usa um perfil DEDICADO e persistente da automação
    (CHROME_PERFIL_DIR) — separado do Chrome pessoal. Os cookies acumulam entre
    execuções, reduzindo o bloqueio anti-robô do Google, e não há conflito de
    perfil travado (o seu Chrome do dia a dia pode ficar aberto).
    """
    options = webdriver.ChromeOptions()

    if usar_perfil:
        limpar_lock_perfil()
        options.add_argument(f"--user-data-dir={CHROME_PERFIL_DIR}")

    # Evita crash em ambientes sem namespace de sandbox (Linux sem privilégio root)
    options.add_argument("--no-sandbox")

    # Previne OOM: /dev/shm tem 64MB por padrão em muitos sistemas; o Chrome
    # precisa de muito mais para renderização. Essa flag usa /tmp em vez disso.
    options.add_argument("--disable-dev-shm-usage")

    # Remove o flag navigator.webdriver=true que sites usam para detectar Selenium
    options.add_argument("--disable-blink-features=AutomationControlled")

    # Desativa infobars do tipo "Chrome está sendo controlado por software automatizado"
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # User-agent de navegador real para evitar bloqueio por fingerprint de UA
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)

    # Wait implícito como rede de segurança: toda busca por elemento aguarda
    # até 10s antes de lançar NoSuchElementException. Não substitui WebDriverWait
    # explícito nos pontos críticos, mas evita falhas em lentidões passageiras.
    driver.implicitly_wait(10)

    return driver

def acessar_pagina(driver):
    """Acessa a URL e aguarda o campo de busca estar presente antes de prosseguir.

    O site usa ASP.NET WebForms com postbacks — driver.get() retorna no
    DOMContentLoaded, mas o campo real pode demorar mais para ser injetado
    pelo framework. Aguardamos o elemento diretamente em vez de usar sleep.
    """
    driver.get(URL)
    try:
        # Aguarda o campo de input principal estar visível — confirma que
        # a página carregou o formulário completo, não só o HTML base
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, ID_INPUT))
        )
        print(f"[OK] Página carregada — campo '{ID_INPUT}' encontrado.")
    except Exception as e:
        print(f"[AVISO] Timeout aguardando campo '{ID_INPUT}' após 20s: {e}")
        print(f"[DEBUG] Título da página atual: '{driver.title}'")
        print(f"[DEBUG] URL atual: '{driver.current_url}'")

def preencher_campo(driver, numero):
    """Aguarda o campo estar interagível e preenche com o número.

    Após cada postback ASPX, o campo pode temporariamente ficar disabled
    enquanto o UpdatePanel recarrega. Aguardamos ele ser clicável
    (visible + enabled) antes de interagir, evitando ElementNotInteractable.
    """
    try:
        campo = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, ID_INPUT))
        )
        campo.clear()
        campo.send_keys(str(numero))
    except Exception as e:
        print(f"[ERRO] Não foi possível preencher o campo '{ID_INPUT}' com '{numero}': {e}")
        print(f"[DEBUG] URL atual: '{driver.current_url}'")
        raise  # propaga para o loop principal tratar e salvar sessão

def aguardar_captcha():
    """Pausa para intervenção manual."""
    input("\n[Ação Manual] Resolva o CAPTCHA/Desafio e pressione ENTER no terminal...")

def clicar_pesquisar(driver):
    """Aguarda o botão estar clicável e dispara a pesquisa.

    O botão btnPesquisar pode ficar desabilitado durante o postback ASPX.
    Usar element_to_be_clickable garante que ele está visible + enabled
    antes de clicar, evitando cliques ignorados silenciosamente.
    """
    try:
        botao = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, ID_BOTAO))
        )
        botao.click()
    except Exception as e:
        print(f"[ERRO] Não foi possível clicar no botão '{ID_BOTAO}': {e}")
        print(f"[DEBUG] URL atual: '{driver.current_url}'")

def extrair_resultado(driver):
    """Extrai os dados e separa em colunas (Logradouro, Bairro, Município, etc).

    Em vez de time.sleep(5) cego, aguarda o texto 'Situação:' aparecer no body
    — isso garante que o postback ASPX terminou e o resultado real está na tela.
    O fallback de 8s cobre casos em que o registro não existe (página sem resultado).
    """
    dados = {
        "Situação": "N/D",
        "Proprietário": "N/D",
        "Logradouro": "N/D",
        "Bairro": "N/D",
        "Município": "N/D",
        "Ocupação": "N/D"
    }
    try:
        # Aguarda o resultado carregar — o texto 'Situação:' só aparece quando
        # o postback ASP.NET terminou e a resposta do servidor chegou.
        # Timeout de 8s: se o registro não existir a página retorna sem esse label.
        WebDriverWait(driver, 8).until(
            EC.text_to_be_present_in_element((By.TAG_NAME, "body"), "Situação:")
        )
    except Exception:
        # Sem resultado para esse número — retorna N/D sem logar como erro,
        # pois é o comportamento esperado para registros inexistentes
        pass

    try:
        corpo = driver.find_element(By.TAG_NAME, "body").text

        def buscar(label, proximo):
            try:
                if label in corpo:
                    parte = corpo.split(label)[1]
                    if proximo and proximo in parte:
                        return parte.split(proximo)[0].replace("|", "").strip()
                    return parte.split("\n")[0].strip()
            except Exception as ex:
                print(f"[DEBUG] Erro ao extrair '{label}': {ex}")
            return "N/D"

        dados["Situação"]    = buscar("Situação:",    "Proprietário:")
        dados["Proprietário"] = buscar("Proprietário:", "Responsável Técnico:")
        dados["Logradouro"]  = buscar("Logradouro:",  "Complemento:")
        dados["Bairro"]      = buscar("Bairro:",      "Município:")
        dados["Município"]   = buscar("Município:",   "Área Total:")
        dados["Ocupação"]    = buscar("Ocupação:",    "Observacoes:")

        return dados
    except Exception as e:
        print(f"[ERRO] Falha ao extrair resultado da página: {e}")
        print(f"[DEBUG] URL atual: '{driver.current_url}'")
        return dados

def salvar_excel(numero, dados_dict):
    """Salva dados em Excel com colunas separadas."""
    dados_dict['numero'] = numero
    novo_dado = pd.DataFrame([dados_dict])
    try:
        if os.path.exists(EXCEL_FILE):
            df_antigo = pd.read_excel(EXCEL_FILE)
            df_final = pd.concat([df_antigo, novo_dado], ignore_index=True)
        else:
            df_final = novo_dado
        df_final.to_excel(EXCEL_FILE, index=False)
    except Exception as e:
        print(f"Erro ao salvar Excel: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  BLOCO 2 — BUSCA DE TELEFONE NO GOOGLE MAPS (thread paralela)
# ══════════════════════════════════════════════════════════════════════════════

def _limpar(valor):
    s = str(valor).strip()
    return "" if s in ("N/D", "nan", "None", "") else s

def montar_query(row):
    partes = [
        _limpar(row.get("Proprietário", "")),
        _limpar(row.get("Logradouro",   "")),
        _limpar(row.get("Bairro",        "")),
        _limpar(row.get("Município",      "")),
    ]
    return ", ".join(p for p in partes if p)

# def extrair_telefone(driver_maps):
#     seletores = [
#         'button[data-item-id="phone"]',
#         'button[data-tooltip="Copiar número de telefone"]',
#         '[aria-label*="Telefone"]',
#         '[aria-label*="Phone"]',
#         '[data-section-id="pn0"] span',
#     ]
#     for seletor in seletores:
#         try:
#             for el in driver_maps.find_elements(By.CSS_SELECTOR, seletor):
#                 aria  = el.get_attribute("aria-label") or ""
#                 texto = el.text or ""
#                 cand  = aria if aria else texto
#                 if any(c.isdigit() for c in cand) and ("(" in cand or "+" in cand or cand.count("-") >= 1):
#                     return cand.replace("Telefone:", "").replace("Phone:", "").strip()
#         except Exception:
#             pass
#     return "N/D"

# def clicar_primeiro_resultado(driver_maps):
#     try:
#         WebDriverWait(driver_maps, 6).until(
#             EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/maps/place/"]'))
#         ).click()
#         time.sleep(4)
#     except Exception:
#         pass

# def extrair_telefone_ocr(driver_maps):
#     """Fallback: tenta ler telefone nas fotos do estabelecimento via OCR."""
#     if not OCR_DISPONIVEL:
#         return "N/D"
#
#     seletores_foto = [
#         'button[jsaction*="photo"] img',
#         '[data-photo-index] img',
#         '.section-hero-header-image img',
#         'img[src*="googleusercontent"]',
#     ]
#
#     fotos = []
#     for seletor in seletores_foto:
#         fotos = driver_maps.find_elements(By.CSS_SELECTOR, seletor)
#         if fotos:
#             break
#
#     for foto in fotos[:5]:  # tenta nas primeiras 5 fotos
#         try:
#             png  = foto.screenshot_as_png
#             img  = Image.open(io.BytesIO(png))
#             texto = pytesseract.image_to_string(img, lang='por')
#             match = PADRAO_TELEFONE.search(texto)
#             if match:
#                 print(f"[OCR] Telefone encontrado na foto: {match.group()}")
#                 return match.group().strip()
#         except Exception:
#             continue
#
#     return "N/D"

# def buscar_telefone_maps(driver_maps, query):
#     url = "https://www.google.com/maps/search/" + query.replace(" ", "+")
#     driver_maps.get(url)
#     time.sleep(4)
#
#     # 1. Tenta extrair do texto da página
#     telefone = extrair_telefone(driver_maps)
#     if telefone != "N/D":
#         return telefone
#
#     # 2. Clica no primeiro resultado se veio lista
#     clicar_primeiro_resultado(driver_maps)
#     telefone = extrair_telefone(driver_maps)
#     if telefone != "N/D":
#         return telefone
#
#     # 3. Fallback: OCR nas fotos do estabelecimento
#     print("[Maps] Texto não encontrado, tentando OCR nas fotos...")
#     return extrair_telefone_ocr(driver_maps)

def buscar_telefone_google(driver, query, row):
    """Busca telefone no Google Search pelo nome/endereço do estabelecimento.

    Navega para a pesquisa '<query> telefone' e só aceita um número se ele
    aparecer perto de um identificador do estabelecimento (município/logradouro/
    proprietário) — ver extrair_telefone_confiavel. Nunca inventa número: retorna
    "N/D" quando não há número confiável no texto da página.
    """
    url = "https://www.google.com/search?q=" + query.replace(" ", "+") + "+telefone"
    driver.get(url)
    time.sleep(3)  # aguarda renderização mínima — sem JS pesado como no Maps

    try:
        texto = driver.find_element(By.TAG_NAME, "body").text

        # Detecta a tela anti-robô do Google ("tráfego incomum"). Antes o código
        # PARAVA esperando ENTER — o que travava o lote inteiro se ninguém
        # estivesse olhando. Agora NÃO bloqueia: avisa, marca o registro como
        # "BLOQUEIO GOOGLE" e segue para o próximo. Esses ficam pendentes e podem
        # ser refeitos depois pela opção 3 (re-verificar).
        if "tráfego incomum" in texto.lower() or "unusual traffic" in texto.lower():
            print("\n[Google] ⚠ Bloqueio anti-robô detectado (tráfego incomum) — pulando este contato e seguindo.")
            return "BLOQUEIO GOOGLE"

        tel = extrair_telefone_confiavel(texto, row)
        if tel != "N/D":
            return tel
            
        # Se não apareceu de primeira, age como um humano e entra no primeiro site
        try:
            links = driver.find_elements(By.CSS_SELECTOR, "div.g a[href]")
            if links:
                # Pega o primeiro link da lista orgânica
                primeiro_link = links[0].get_attribute("href")
                # Não entra se for um link interno do próprio Google
                if "google" not in primeiro_link:
                    driver.get(primeiro_link)
                    time.sleep(4)  # Espera a página de terceiros carregar
                    texto_site = driver.find_element(By.TAG_NAME, "body").text
                    return extrair_telefone_confiavel(texto_site, row)
        except Exception as e:
            pass
            
    except Exception:
        pass

    return "N/D"

def ler_saida():
    # Arquivo ausente ou truncado (0 bytes de uma gravação interrompida) conta
    # como "nada processado ainda" — não pode derrubar a execução.
    if not os.path.exists(EXCEL_SAIDA) or os.path.getsize(EXCEL_SAIDA) == 0:
        return pd.DataFrame()
    try:
        return pd.read_excel(EXCEL_SAIDA, engine="openpyxl")
    except Exception as e:
        print(f"[Google] {EXCEL_SAIDA} ilegível ({e}) — recomeçando do zero.")
        return pd.DataFrame()

def salvar_saida(df):
    # Grava em temporário e só então substitui, para que uma interrupção no meio
    # não deixe o arquivo final vazio/corrompido.
    os.makedirs(os.path.dirname(EXCEL_SAIDA) or ".", exist_ok=True)
    # A extensão precisa continuar .xlsx: o pandas escolhe o engine por ela.
    tmp = EXCEL_SAIDA + ".tmp.xlsx"
    df.to_excel(tmp, index=False, engine="openpyxl")
    os.replace(tmp, EXCEL_SAIDA)

def processar_telefones():
    """Roda UMA vez, após o fluxo principal terminar.

    Lê todos os endereços salvos em resultados.xlsx e busca o telefone de cada
    um, em sequência, gravando em resultados_com_telefone.xlsx. Registros já
    processados em execuções anteriores são ignorados (retomada segura).
    """
    if not os.path.exists(EXCEL_FILE):
        print("[Google] Nenhum arquivo de endereços encontrado — nada a buscar.")
        return

    try:
        df_entrada = pd.read_excel(EXCEL_FILE)
    except Exception as e:
        print(f"[Google] Não foi possível ler {EXCEL_FILE}: {e}")
        return

    if df_entrada.empty:
        print("[Google] Nenhum endereço para processar.")
        return

    # Carrega o que já foi processado para permitir retomada sem refazer buscas
    df_saida = ler_saida()
    processados = set()
    if not df_saida.empty and "numero" in df_saida.columns:
        processados = set(df_saida["numero"].tolist())

    pendentes = [row for _, row in df_entrada.iterrows() if row.get("numero") not in processados]
    if not pendentes:
        print("[Google] Todos os endereços já possuem telefone — nada a fazer.")
        return

    print(f"\n[Google] Buscando telefones de {len(pendentes)} endereços...")
    print("[Google] Usando um perfil dedicado da automação (seu Chrome pessoal pode ficar aberto).")

    try:
        driver_maps = iniciar_navegador(usar_perfil=True)
    except Exception as e:
        print(f"[Google] Não foi possível abrir o Chrome: {e}")
        return

    total_lote = len(pendentes)          # quantos faltavam nesta execução
    ja_feitos  = len(processados)        # quantos já existiam de execuções anteriores
    total_geral = ja_feitos + total_lote # total de registros da entrada

    # Permite pausar/retomar a busca de telefones com ENTER, igual ao fluxo de
    # endereços. Como cada item é salvo em disco na hora, pausar e até fechar o
    # programa é seguro: a próxima execução retoma de onde parou.
    iniciar_controle_pausa()

    try:
        for i, row in enumerate(pendentes, start=1):
            # Ponto de pausa: só interrompe entre um contato e outro, nunca no
            # meio de uma busca já iniciada.
            checar_pausa()

            numero = row.get("numero")
            item = row.to_dict()

            # Contador de progresso: mostra a posição no lote, o total geral e
            # o NÚMERO atual — assim você sabe exatamente até onde a busca chegou,
            # mesmo se o processo for interrompido.
            posicao_geral = ja_feitos + i
            print(f"\n[Progresso] {i}/{total_lote} deste lote | {posicao_geral}/{total_geral} no total | número atual: {numero}")

            # Endereço inválido/ausente: marca e segue sem consultar o Google
            if _limpar(row.get("Logradouro", "")) == "":
                item["Telefone"] = "SEM ENDEREÇO"
                df_saida = pd.concat([df_saida, pd.DataFrame([item])], ignore_index=True)
                processados.add(numero)
                salvar_saida(df_saida)
                continue

            query = montar_query(item)
            print(f"[Google] Buscando: {query}")
            tel = buscar_telefone_google(driver_maps, query, item)

            # Bloqueio do Google: NÃO grava como concluído — deixa pendente para
            # que uma próxima execução (opção 2) tente de novo automaticamente.
            if tel == "BLOQUEIO GOOGLE":
                print("[Google] → BLOQUEIO GOOGLE (fica pendente para a próxima execução)")
                aguardar_sem_pausa(random.randint(4, 9))
                continue

            # Achou telefone confiável → salva; não achou → texto fixo (nunca inventa)
            item["Telefone"] = tel if tel != "N/D" else "TELEFONE NÃO EXISTE"
            print(f"[Google] → {item['Telefone']}")
            df_saida = pd.concat([df_saida, pd.DataFrame([item])], ignore_index=True)
            processados.add(numero)
            salvar_saida(df_saida)  # salva a cada item para garantir persistência
            aguardar_sem_pausa(random.randint(4, 9))

        print(f"\n[Google] Concluído — total processado: {len(processados)}/{total_geral} registros. Último número: {numero}")
    except Exception as e:
        print(f"[Google] Erro na busca de telefones: {e}")
    finally:
        if not df_saida.empty:
            salvar_saida(df_saida)
        driver_maps.quit()
        print("[Google] Busca de telefones encerrada.")

def reverificar_telefones():
    """Lê os telefones já buscados e refaz a busca forçadamente."""
    if not os.path.exists(EXCEL_SAIDA):
        print(f"[Google] Arquivo {EXCEL_SAIDA} não encontrado para re-verificação.")
        return
    
    try:
        df = pd.read_excel(EXCEL_SAIDA)
    except Exception as e:
        print(f"[Google] Erro ao ler: {e}")
        return
        
    casos_erro = ["SEM ENDEREÇO", "NÃO ENCONTRADO", "FALHA BUSCA", "SEM NOME", "TELEFONE NÃO EXISTE", "N/D"]
    pendentes = df[~df["Telefone"].isin(casos_erro)]
    
    if pendentes.empty:
        print("[Google] Nenhum telefone válido para re-verificar.")
        return
        
    print(f"\n[Google] Re-verificando {len(pendentes)} telefones...")
    driver = None
    iniciar_controle_pausa()
    try:
        driver = iniciar_navegador(usar_perfil=True)
        for index, row in pendentes.iterrows():
            checar_pausa()
            item = row.to_dict()
            query = montar_query(item)
            print(f"[Google] Re-buscando: {query}")
            tel = buscar_telefone_google(driver, query, item)
            novo = tel if tel != "N/D" else "TELEFONE NÃO EXISTE"
            print(f"[Google] Antes: {item.get('Telefone')} -> Agora: {novo}")
            df.at[index, "Telefone"] = novo
            salvar_saida(df)
            aguardar_sem_pausa(random.randint(4, 9))
    except Exception as e:
        print(f"[Google] Erro na re-verificação: {e}")
    finally:
        if driver is not None:
            driver.quit()
        salvar_saida(df)
        print("[Google] Re-verificação encerrada.")


# ══════════════════════════════════════════════════════════════════════════════
#  BLOCO 3 — SESSÃO E PAUSA
# ══════════════════════════════════════════════════════════════════════════════

def salvar_sessao(num_atual, consultas_feitas, limite):
    with open(SESSAO_FILE, "w") as f:
        json.dump({"num_atual": num_atual, "consultas_feitas": consultas_feitas, "limite": limite}, f)

def carregar_sessao():
    if not os.path.exists(SESSAO_FILE):
        return None
    try:
        with open(SESSAO_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None

def limpar_sessao():
    if os.path.exists(SESSAO_FILE):
        os.remove(SESSAO_FILE)

def aguardar_sem_pausa(segundos):
    """Substitui time.sleep respeitando o pause_event em fatias de 1s."""
    for _ in range(segundos):
        if pause_event.is_set():
            return
        time.sleep(1)

def checar_pausa():
    """Se estiver pausado, bloqueia aqui até o ENTER de retomada."""
    if not pause_event.is_set():
        return
    print("\n╔══════════════════════════════════════════╗")
    print("║  ⏸  AUTOMAÇÃO PAUSADA                    ║")
    print("║  Pressione ENTER para retomar...         ║")
    print("╚══════════════════════════════════════════╝")
    while pause_event.is_set():
        time.sleep(1)
    print("\n╔══════════════════════════════════════════╗")
    print("║  ▶  AUTOMAÇÃO RETOMADA                   ║")
    print("╚══════════════════════════════════════════╝\n")

_controle_pausa_ativo = False

def iniciar_controle_pausa():
    """Limpa o stdin residual e sobe a thread que escuta o ENTER.

    Idempotente: duas threads escutando o mesmo stdin fariam o ENTER ser
    consumido por uma delas de forma imprevisível.
    """
    global _controle_pausa_ativo
    if _controle_pausa_ativo:
        return
    _controle_pausa_ativo = True
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass  # stdin sem terminal (ex.: entrada redirecionada)
    threading.Thread(target=thread_pausa, daemon=True).start()

def thread_pausa():
    """Fica escutando ENTER no terminal para pausar/retomar a automação."""
    print("\n[Controle] Pressione ENTER a qualquer momento para PAUSAR ou RETOMAR.")
    while True:
        try:
            input()
        except (EOFError, OSError):
            # stdin fechado/redirecionado: sem terminal não há como pausar.
            return
        if pause_event.is_set():
            pause_event.clear()
            print("\n[▶] Automação RETOMADA.")
        else:
            pause_event.set()
            print("\n[⏸] Automação PAUSADA — navegadores permanecem abertos.")
            print("     Pressione ENTER para retomar...")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — inicia os dois blocos juntos
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Menu inicial: fluxo completo ou apenas buscar telefones ───────────────
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  O que você deseja fazer?                        ║")
    print("║  [1] Fluxo completo (consultar PMESP + telefones)║")
    print("║  [2] Apenas buscar telefones dos resultados salvos║")
    print("║  [3] Re-verificar telefones já extraídos         ║")
    print("╚══════════════════════════════════════════════════╝")
    opcao = input("Escolha (1/2/3): ").strip()

    if opcao == "2":
        if not os.path.exists(EXCEL_FILE):
            print(f"[ERRO] Nenhum resultado salvo encontrado em '{EXCEL_FILE}'.")
            return
        processar_telefones()
        print("Programa encerrado.")
        return
    elif opcao == "3":
        reverificar_telefones()
        print("Programa encerrado.")
        return

    # ── Verifica sessão anterior ──────────────────────────────────────────────
    sessao = carregar_sessao()
    continuar = False
    if sessao:
        print("\n╔══════════════════════════════════════════════════╗")
        print("║  Sessão anterior encontrada!                     ║")
        print(f"║  Último número: {sessao['num_atual']}  |  "
              f"Feitas: {sessao['consultas_feitas']}/{sessao['limite']}  ║")
        print("╚══════════════════════════════════════════════════╝")
        resp = input("Deseja continuar de onde parou? (s/n): ").strip().lower()
        continuar = resp == "s"
        if not continuar:
            limpar_sessao()

    # ── Automação PMESP ───────────────────────────────────────────────────────
    driver = iniciar_navegador()
    try:
        acessar_pagina(driver)

        if continuar:
            num_atual       = sessao["num_atual"]
            consultas_feitas = sessao["consultas_feitas"]
            limite          = sessao["limite"]
            print(f"\n[Retomando] A partir do número {num_atual + 1} "
                  f"({consultas_feitas}/{limite} já realizadas).")
            print("Resolva o CAPTCHA se necessário e pressione ENTER para continuar...")
            input("Aguardando ENTER...")
        else:
            print("\n[Configuração] Quantas consultas deseja realizar hoje?")
            limite = int(input("Digite a quantidade (ex: 150): "))

            print("\n[Ação Inicial] Resolva o CAPTCHA, digite um número e clique em 'Pesquisar'.")
            print("Assim que o resultado aparecer, pressione ENTER para salvar e iniciar o automático...")
            input("Aguardando ENTER...")

            num_atual = 1059000
            try:
                valor = driver.find_element(By.ID, ID_INPUT).get_attribute("value")
                if valor:
                    num_atual = int(valor)
            except:
                pass

            print(f"Salvando primeiro resultado ({num_atual})...")
            dados_pri = extrair_resultado(driver)
            salvar_excel(num_atual, dados_pri)
            consultas_feitas = 1
            salvar_sessao(num_atual, consultas_feitas, limite)

        # ── Limpa stdin residual e inicia thread de pausa ──────────────────
        iniciar_controle_pausa()
        # ── Loop principal ────────────────────────────────────────────────────
        for n in range(int(num_atual) + 1,  1065000):
            if consultas_feitas >= limite:
                print(f"\nMeta de {limite} consultas atingida! Finalizando...")
                break

            # Verifica pausa — fica aguardando enquanto estiver pausado
            checar_pausa()

            print(f"\nConsultando automaticamente ({consultas_feitas + 1}/{limite}): {n}...")
            preencher_campo(driver, n)
            clicar_pesquisar(driver)

            dados = extrair_resultado(driver)
            salvar_excel(n, dados)
            print(f"Resultado Salvo: {dados.get('Situação')} | {dados.get('Logradouro')}")

            consultas_feitas += 1
            salvar_sessao(n, consultas_feitas, limite)  # salva estado após cada consulta

            espera = random.randint(5, 15)
            print(f"Aguardando {espera}s para a próxima consulta...")
            aguardar_sem_pausa(espera)

        limpar_sessao()  # sessão concluída com sucesso

        # ── Fluxo PMESP terminou: fecha o navegador e só então busca os telefones ──
        driver.quit()
        driver = None
        processar_telefones()

    except Exception as e:
        print(f"Erro no processamento: {e}")
        print("Estado salvo — rode novamente para continuar de onde parou.")
        print("(A busca de telefones só roda após o fluxo concluir sem erros.)")
    finally:
        if driver is not None:
            driver.quit()
        print("Programa encerrado.")

if __name__ == "__main__":
    main()
