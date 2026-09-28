#!/usr/bin/env python3
import subprocess
import threading
import sys
import time

def log_stream(container_name, prefix, color_code):
    cmd = ["docker", "logs", "-f", "--tail", "15", container_name]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in iter(proc.stdout.readline, ''):
        line_clean = line.strip()
        if not line_clean:
            continue
        # Destaque de eventos vitais
        tag = ""
        if any(w in line_clean for w in ["pairingCode", "pair-code", "PairCode"]):
            tag = " 🔑 [TOKEN PAIR]"
        elif any(w in line_clean for w in ["qrcode", "qrcodeCount", "base64"]):
            tag = " 📷 [QR-CODE]"
        elif "open" in line_clean:
            tag = " ✅ [CONECTADO/OPEN]"
        elif any(w in line_clean for w in ["close", "LOGOUT", "Connection Closed"]):
            tag = " ❌ [DESCONECTADO/LOGOUT]"
        elif "HTTP" in line_clean and (" 200 " in line_clean or " 201 " in line_clean):
            tag = " 🟢 [HTTP OK]"
        elif any(e in line_clean for e in [" 400 ", " 404 ", " 500 ", "Error", "Exception"]):
            tag = " ⚠️ [ERRO/ALERTA]"

        print(f"\033[{color_code}m[{prefix}]\033[0m{tag} {line_clean}", flush=True)

print("=" * 70)
print("🚀 MONITOR DE TELEMETRIA EM TEMPO REAL (ATENDIT & EVOLUTION API)")
print("Pressione Ctrl + C a qualquer momento para interromper.")
print("=" * 70)

# Snapshot de status prévio
print("\n[DIAGNÓSTICO INICIAL DOS SERVIÇOS]")
subprocess.run(["docker", "ps", "--filter", "name=atendit-api", "--filter", "name=atendit-whatsapp-gateway", "--format", "table {{.Names}}\t{{.Status}}"])
print("-" * 70)
print("Aguardando requisições do painel... (Abra o navegador e faça as ações)")
print("-" * 70)

t1 = threading.Thread(target=log_stream, args=("atendit-api", "BACKEND-API", "36"), daemon=True)
t2 = threading.Thread(target=log_stream, args=("atendit-whatsapp-gateway", "EVOLUTION", "33"), daemon=True)

t1.start()
t2.start()

try:
    while True:
        time.sleep(0.5)
except KeyboardInterrupt:
    print("\n\nMonitor encerrado com sucesso.")
    sys.exit(0)
