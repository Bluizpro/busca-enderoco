# Automação Consulta CLCB - PMESP

Sistema para automação de consultas de CLCB no portal Via Fácil da Polícia Militar de SP.

## 🚀 Como Instalar

1. Certifique-se de ter o **Python 3** e o **Google Chrome** instalados.
2. Instale as dependências necessárias:
   ```bash
   pip install pandas selenium openpyxl
   ```

## 🛠️ Como Rodar

1. Execute o script principal:
   ```bash
   python3 automacao.py
   ```
2. Siga as instruções no terminal:
   - Defina a quantidade de consultas.
   - Resolva o primeiro **CAPTCHA** manualmente no navegador.
   - Pressione **ENTER** no terminal para iniciar o ciclo automático.

## 📊 Resultados
Os dados extraídos serão salvos automaticamente no arquivo `resultados.xlsx` na mesma pasta do projeto.
