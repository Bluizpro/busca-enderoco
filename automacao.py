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

PADRAO_TELEFONE = re.compile(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}')

# ── Configurações ─────────────────────────────────────────────────────────────
URL        = "http://viafacil2.policiamilitar.sp.gov.br/SGSCI/PUBLICO/PESQUISARCLCB.ASPX"
ID_INPUT   = "txtNumeroCLCB"
ID_BOTAO   = "btnPesquisar"
EXCEL_FILE      = "resultado/resultados.xlsx"
EXCEL_SAIDA     = "resultado/resultados_com_telefone.xlsx"
SESSAO_FILE     = "resultado/sessao.json"
LOTE            = 10   # busca telefone a cada N endereços novos
INTERVALO_CHECK = 15   # segundos entre cada verificação do arquivo

# Evento global de pausa (set = pausado, clear = rodando)
pause_event = threading.Event()


# ══════════════════════════════════════════════════════════════════════════════
#  BLOCO 1 — AUTOMAÇÃO PMESP (original, sem alterações)
# ══════════════════════════════════════════════════════════════════════════════

def iniciar_navegador():
    """Abre o Chrome com opções necessárias para automação estável."""
    options = webdriver.ChromeOptions()

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

def buscar_telefone_google(driver, query):
    """Busca telefone no Google Search pelo nome/endereço do estabelecimento.

    Navega para a pesquisa '<query> telefone', aguarda o carregamento e tenta
    extrair o número usando o PADRAO_TELEFONE já definido no módulo.  Retorna
    "N/D" quando nenhum número é encontrado no texto da página.
    """
    url = "https://www.google.com/search?q=" + query.replace(" ", "+") + "+telefone"
    driver.get(url)
    time.sleep(3)  # aguarda renderização mínima — sem JS pesado como no Maps

    try:
        texto = driver.find_element(By.TAG_NAME, "body").text
        match = PADRAO_TELEFONE.search(texto)
        if match:
            return match.group().strip()
    except Exception:
        pass

    return "N/D"

def ler_saida():
    if not os.path.exists(EXCEL_SAIDA):
        return pd.DataFrame()
    return pd.read_excel(EXCEL_SAIDA)

def salvar_saida(df):
    df.to_excel(EXCEL_SAIDA, index=False)

def thread_telefones(stop_event):
    """Roda em paralelo: monitora resultados.xlsx e busca telefones a cada LOTE entradas."""
    print("[Google] Thread de telefones iniciada.")
    driver_maps = iniciar_navegador()

    processados = set()
    df_saida = ler_saida()
    if not df_saida.empty and "numero" in df_saida.columns:
        processados = set(df_saida["numero"].tolist())

    try:
        while not stop_event.is_set():
            # Lê o arquivo de endereços com proteção contra leitura simultânea
            try:
                df_entrada = pd.read_excel(EXCEL_FILE) if os.path.exists(EXCEL_FILE) else pd.DataFrame()
            except Exception:
                time.sleep(3)
                continue

            if df_entrada.empty:
                time.sleep(INTERVALO_CHECK)
                continue

            # Separa os pendentes (com endereço válido, ainda não processados)
            pendentes = []
            for _, row in df_entrada.iterrows():
                numero = row.get("numero")
                if numero in processados:
                    continue
                if _limpar(row.get("Logradouro", "")) == "":
                    row_dict = row.to_dict()
                    row_dict["Telefone"] = "SEM ENDEREÇO"
                    df_saida = pd.concat([df_saida, pd.DataFrame([row_dict])], ignore_index=True)
                    processados.add(numero)
                    salvar_saida(df_saida)  # salva imediatamente para não perder na saída do programa
                    continue
                pendentes.append(row.to_dict())

            if len(pendentes) < LOTE:
                print(f"[Google] Aguardando... {len(pendentes)}/{LOTE} endereços novos acumulados.")
                time.sleep(INTERVALO_CHECK)
                continue

            # Processa exatamente LOTE itens
            lote_atual = pendentes[:LOTE]
            print(f"\n[Google] {LOTE} endereços prontos — buscando telefones...")

            for item in lote_atual:
                query = montar_query(item)
                print(f"[Google] Buscando: {query}")
                # item["Telefone"] = buscar_telefone_maps(driver_maps, query)
                item["Telefone"] = buscar_telefone_google(driver_maps, query)
                print(f"[Google] → {item['Telefone']}")
                df_saida = pd.concat([df_saida, pd.DataFrame([item])], ignore_index=True)
                processados.add(item.get("numero"))
                salvar_saida(df_saida)  # salva a cada item para garantir persistência
                time.sleep(random.randint(4, 9))

            print(f"[Google] Lote concluído — total processado: {len(processados)} registros.\n")

    except Exception as e:
        print(f"[Google] Erro na thread: {e}")
    finally:
        if not df_saida.empty:
            salvar_saida(df_saida)
        driver_maps.quit()
        print("[Google] Thread de telefones encerrada.")


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

def thread_pausa():
    """Fica escutando ENTER no terminal para pausar/retomar a automação."""
    print("\n[Controle] Pressione ENTER a qualquer momento para PAUSAR ou RETOMAR.")
    while True:
        input()
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

    # ── Thread de telefones (pode iniciar já) ────────────────────────────────
    stop_event = threading.Event()
    threading.Thread(target=thread_telefones, args=(stop_event,), daemon=True).start()

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

            num_atual = 1028000
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
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
        threading.Thread(target=thread_pausa, daemon=True).start()
        # ── Loop principal ────────────────────────────────────────────────────
        for n in range(int(num_atual) + 1, 1034000):
            if consultas_feitas >= limite:
                print(f"\nMeta de {limite} consultas atingida! Finalizando...")
                break

            # Verifica pausa — fica aguardando enquanto estiver pausado
            if pause_event.is_set():
                print("\n╔══════════════════════════════════════════╗")
                print("║  ⏸  AUTOMAÇÃO PAUSADA                    ║")
                print("║  Pressione ENTER para retomar...         ║")
                print("╚══════════════════════════════════════════╝")
                while pause_event.is_set():
                    time.sleep(1)
                print("\n╔══════════════════════════════════════════╗")
                print("║  ▶  AUTOMAÇÃO RETOMADA                   ║")
                print("╚══════════════════════════════════════════╝\n")

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

    except Exception as e:
        print(f"Erro no processamento: {e}")
        print("Estado salvo — rode novamente para continuar de onde parou.")
    finally:
        driver.quit()
        stop_event.set()
        print("Programa encerrado.")

if __name__ == "__main__":
    main()
