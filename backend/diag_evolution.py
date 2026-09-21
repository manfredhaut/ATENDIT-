import urllib.request
import urllib.error
import json
import time

EVO_URL = "http://evolution-api:8080"
API_KEY = "TokenDeSegurancaGeradoParaAEvolutionAPI"
INSTANCE = "teste_10"
PHONE = "5585989020465"

def request_evo(endpoint, method="GET", body=None):
    url = f"{EVO_URL}{endpoint}"
    headers = {
        "apikey": API_KEY,
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, str(e)

print("\n=======================================================")
print("  DIAGNÓSTICO PROFUNDO: EVOLUTION API v2.2.2")
print("=======================================================")

# 1. Obter informações e status geral
print("\n[1] Verificando instâncias existentes...")
status, res = request_evo("/instance/fetchInstances")
print(f"Status HTTP: {status}")
print(f"Instâncias ativas: {json.dumps(res, indent=2)}")

# 2. Deletar estância para garantir teste limpo
print(f"\n[2] Limpando estância prévia '{INSTANCE}'...")
status, res = request_evo(f"/instance/delete/{INSTANCE}", method="DELETE")
print(f"Delete Status: {status} -> {res}")
time.sleep(2)

# 3. Criar estância no padrão v2.2.2 (WhatsApp Baileys)
print(f"\n[3] Criando estância '{INSTANCE}' no Baileys...")
create_payload = {
    "instanceName": INSTANCE,
    "integration": "WHATSAPP-BAILEYS",
    "qrcode": True
}
status, res = request_evo("/instance/create", method="POST", body=create_payload)
print(f"Create Status HTTP: {status}")
print(f"Resposta Create:\n{json.dumps(res, indent=2)}")

# 4. Consultar Conexão / QR Code imediato
print(f"\n[4] Solicitando Connect / QR Code para '{INSTANCE}'...")
status, res = request_evo(f"/instance/connect/{INSTANCE}")
print(f"Connect Status HTTP: {status}")
print(f"Resposta Connect:\n{json.dumps(res, indent=2)}")

# 5. Consultar Conexão com Pairing Code (Método 2 por Telefone)
print(f"\n[5] Solicitando Pairing Code para o número '{PHONE}'...")
status, res = request_evo(f"/instance/connect/{INSTANCE}?number={PHONE}")
print(f"Pairing Code Status HTTP: {status}")
print(f"Resposta Pairing Code:\n{json.dumps(res, indent=2)}")

# 6. Status da Conexão
print(f"\n[6] Verificando connectionState de '{INSTANCE}'...")
status, res = request_evo(f"/instance/connectionState/{INSTANCE}")
print(f"ConnectionState HTTP: {status}")
print(f"Estado:\n{json.dumps(res, indent=2)}")
print("=======================================================\n")
