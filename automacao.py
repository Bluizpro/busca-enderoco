import pandas as pd
import os
import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By

# Configurações Reais (Via Fácil PMESP)
URL = "http://viafacil2.policiamilitar.sp.gov.br/SGSCI/PUBLICO/PESQUISARCLCB.ASPX"
ID_INPUT = "txtNumeroCLCB"
ID_BOTAO = "btnPesquisar"
EXCEL_FILE = "resultados.xlsx"

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
        
        # Função auxiliar para extrair texto entre dois rótulos
        def buscar(label, proximo):
            try:
                if label in corpo:
                    parte = corpo.split(label)[1]
                    if proximo and proximo in parte:
                        return parte.split(proximo)[0].replace("|", "").strip()
                    return parte.split("\n")[0].strip()
            except: pass
            return "N/D"

        dados["Situação"] = buscar("Situação:", "Proprietário:")
        dados["Proprietário"] = buscar("Proprietário:", "Responsável Técnico:")
        dados["Logradouro"] = buscar("Logradouro:", "Complemento:")
        dados["Bairro"] = buscar("Bairro:", "Município:")
        dados["Município"] = buscar("Município:", "Área Total:")
        dados["Ocupação"] = buscar("Ocupação:", "Observacoes:")
        
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

def main():
    driver = iniciar_navegador()
    try:
        acessar_pagina(driver)
        print("\n[Configuração] Quantas consultas deseja realizar hoje?")
        limite = int(input("Digite a quantidade (ex: 150): "))
        
        print("\n[Ação Inicial] Resolva o CAPTCHA, digite um número e clique em 'Pesquisar'.")
        print("Assim que o resultado aparecer na tela, pressione ENTER no terminal para salvar e iniciar o automático...")
        input("Aguardando ENTER...")
        
        # 1. Pega o número que o usuário digitou e o resultado na tela
        try:
            num_atual = int(driver.find_element(By.ID, ID_INPUT).get_attribute("value"))
        except:
            num_atual = 991999
            
        print(f"Salvando primeiro resultado ({num_atual})...")
        dados_pri = extrair_resultado(driver)
        salvar_excel(num_atual, dados_pri)
        
        consultas_feitas = 1
        
        # 2. Inicia o loop para os próximos números respeitando o limite
        for n in range(num_atual + 1, 992550):
            if consultas_feitas >= limite:
                print(f"\nMeta de {limite} consultas atingida! Finalizando...")
                break
                
            print(f"\nConsultando automaticamente ({consultas_feitas + 1}/{limite}): {n}...")
            preencher_campo(driver, n)
            clicar_pesquisar(driver)
            
            # Extração e salvamento
            dados = extrair_resultado(driver)
            salvar_excel(n, dados)
            print(f"Resultado Salvo: {dados.get('Situação')} | {dados.get('Logradouro')}")
            
            consultas_feitas += 1
            
            # Delay aleatório de 5 a 15 segundos para evitar bloqueio
            espera = random.randint(5, 15)
            print(f"Aguardando {espera}s para a próxima consulta...")
            time.sleep(espera)
            
    except Exception as e:
        print(f"Erro no processamento: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
