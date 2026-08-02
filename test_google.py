from selenium import webdriver
from selenium.webdriver.common.by import By
import time

options = webdriver.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
driver = webdriver.Chrome(options=options)

driver.get("https://www.google.com/search?q=Núcleo+do+Programa+de+Saúde+da+Família+São+Gonçalo+telefone")
time.sleep(3)
texto = driver.find_element(By.TAG_NAME, "body").text
with open("google_body.txt", "w") as f:
    f.write(texto)
driver.quit()
