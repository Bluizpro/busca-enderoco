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
    """Abre o Chrome."""
    return webdriver.Chrome()

def acessar_pagina(driver):
    """Acessa a URL configurada."""
    driver.get(URL)

def preencher_campo(driver, numero):
    """Localiza input e preenche com o número."""
    campo = driver.find_element(By.ID, ID_INPUT)
    campo.clear()
    campo.send_keys(str(numero))

def aguardar_captcha():
    """Pausa para intervenção manual."""
    input("\n[Ação Manual] Resolva o CAPTCHA/Desafio e pressione ENTER no terminal...")

def clicar_pesquisar(driver):
    """Clica no botão de busca pelo ID real."""
    try:
        driver.find_element(By.ID, ID_BOTAO).click()
    except Exception as e:
        print(f"Erro ao clicar no botão: {e}")

def extrair_resultado(driver):
    """Extrai os dados e separa em colunas (Logradouro, Bairro, Município, etc)."""
    time.sleep(5)
    dados = {
        "Situação": "N/D",
        "Proprietário": "N/D",
        "Logradouro": "N/D",
        "Bairro": "N/D",
        "Município": "N/D",
        "Ocupação": "N/D"
    }
    try:
        corpo = driver.find_element(By.TAG_NAME, "body").text

        def buscar(label, proximo):
            try:
                if label in corpo:
                    parte = corpo.split(label)[1]
                    if proximo and proximo in parte:
                        return parte.split(proximo)[0].replace("|", "").strip()
                    return parte.split("\n")[0].strip()
            except: pass
            return "N/D"

        dados["Situação"]    = buscar("Situação:",    "Proprietário:")
        dados["Proprietário"] = buscar("Proprietário:", "Responsável Técnico:")
        dados["Logradouro"]  = buscar("Logradouro:",  "Complemento:")
        dados["Bairro"]      = buscar("Bairro:",      "Município:")
        dados["Município"]   = buscar("Município:",   "Área Total:")
        dados["Ocupação"]    = buscar("Ocupação:",    "Observacoes:")

        return dados
    except:
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

def extrair_telefone(driver_maps):
    seletores = [
        'button[data-item-id="phone"]',
        'button[data-tooltip="Copiar número de telefone"]',
        '[aria-label*="Telefone"]',
        '[aria-label*="Phone"]',
        '[data-section-id="pn0"] span',
    ]
    for seletor in seletores:
        try:
            for el in driver_maps.find_elements(By.CSS_SELECTOR, seletor):
                aria  = el.get_attribute("aria-label") or ""
                texto = el.text or ""
                cand  = aria if aria else texto
                if any(c.isdigit() for c in cand) and ("(" in cand or "+" in cand or cand.count("-") >= 1):
                    return cand.replace("Telefone:", "").replace("Phone:", "").strip()
        except Exception:
            pass
    return "N/D"

def clicar_primeiro_resultado(driver_maps):
    try:
        WebDriverWait(driver_maps, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/maps/place/"]'))
        ).click()
        time.sleep(4)
    except Exception:
        pass

def extrair_telefone_ocr(driver_maps):
    """Fallback: tenta ler telefone nas fotos do estabelecimento via OCR."""
    if not OCR_DISPONIVEL:
        return "N/D"

    seletores_foto = [
        'button[jsaction*="photo"] img',
        '[data-photo-index] img',
        '.section-hero-header-image img',
        'img[src*="googleusercontent"]',
    ]

    fotos = []
    for seletor in seletores_foto:
        fotos = driver_maps.find_elements(By.CSS_SELECTOR, seletor)
        if fotos:
            break

    for foto in fotos[:5]:  # tenta nas primeiras 5 fotos
        try:
            png  = foto.screenshot_as_png
            img  = Image.open(io.BytesIO(png))
            texto = pytesseract.image_to_string(img, lang='por')
            match = PADRAO_TELEFONE.search(texto)
            if match:
                print(f"[OCR] Telefone encontrado na foto: {match.group()}")
                return match.group().strip()
        except Exception:
            continue

    return "N/D"

def buscar_telefone_maps(driver_maps, query):
    url = "https://www.google.com/maps/search/" + query.replace(" ", "+")
    driver_maps.get(url)
    time.sleep(4)

    # 1. Tenta extrair do texto da página
    telefone = extrair_telefone(driver_maps)
    if telefone != "N/D":
        return telefone

    # 2. Clica no primeiro resultado se veio lista
    clicar_primeiro_resultado(driver_maps)
    telefone = extrair_telefone(driver_maps)
    if telefone != "N/D":
        return telefone

    # 3. Fallback: OCR nas fotos do estabelecimento
    print("[Maps] Texto não encontrado, tentando OCR nas fotos...")
    return extrair_telefone_ocr(driver_maps)

def ler_saida():
    if not os.path.exists(EXCEL_SAIDA):
        return pd.DataFrame()
    return pd.read_excel(EXCEL_SAIDA)

def salvar_saida(df):
    df.to_excel(EXCEL_SAIDA, index=False)

def thread_telefones(stop_event):
    """Roda em paralelo: monitora resultados.xlsx e busca telefones a cada LOTE entradas."""
    print("[Maps] Thread de telefones iniciada.")
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
                    continue
                pendentes.append(row.to_dict())

            if len(pendentes) < LOTE:
                print(f"[Maps] Aguardando... {len(pendentes)}/{LOTE} endereços novos acumulados.")
                time.sleep(INTERVALO_CHECK)
                continue

            # Processa exatamente LOTE itens
            lote_atual = pendentes[:LOTE]
            print(f"\n[Maps] {LOTE} endereços prontos — buscando telefones...")

            for item in lote_atual:
                query = montar_query(item)
                print(f"[Maps] Buscando: {query}")
                item["Telefone"] = buscar_telefone_maps(driver_maps, query)
                print(f"[Maps] → {item['Telefone']}")
                df_saida = pd.concat([df_saida, pd.DataFrame([item])], ignore_index=True)
                processados.add(item.get("numero"))
                time.sleep(random.randint(4, 9))

            salvar_saida(df_saida)
            print(f"[Maps] Salvo — total processado: {len(processados)} registros.\n")

    except Exception as e:
        print(f"[Maps] Erro na thread: {e}")
    finally:
        if not df_saida.empty:
            salvar_saida(df_saida)
        driver_maps.quit()
        print("[Maps] Thread de telefones encerrada.")


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

            num_atual = 1023000
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
        for n in range(int(num_atual) + 1, 1028000):
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
