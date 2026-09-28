#!/usr/bin/env python3
"""Adiciona healthcheck ao servico 'api' do docker-compose.yml, com start_period
maior que o boot real (~57-65s medidos). O Dockerfile ja tem HEALTHCHECK com
start-period=10s; isso NAO pode ser sobrescrito por override no compose --
so a definicao de dentro do compose e a valida para um container criado
por 'docker compose up/build'. Por isso o healthcheck entra aqui, nao no
Dockerfile. Idempotente."""
import re, shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "docker-compose.yml"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "curl -f http://localhost:8000/health" in txt:
    print("Ja tem healthcheck no compose -- nada a fazer.")
    sys.exit(0)

m_srv = re.search(r'^  api:\n', txt, re.M)
if not m_srv:
    sys.exit("ERRO: servico 'api:' nao encontrado no docker-compose.yml.")

fim_bloco = re.search(r'\n  \S.*:\n', txt[m_srv.end():], re.M)
bloco_fim = m_srv.end() + fim_bloco.start() + 1 if fim_bloco else len(txt)
bloco = txt[m_srv.end():bloco_fim]

ancora = re.search(r'[ \t]*restart: always\n', bloco)
if not ancora:
    sys.exit("ERRO: 'restart: always' nao encontrado dentro do bloco do servico 'api'.")

healthcheck = (
    '    healthcheck:\n'
    '      test:\n'
    '      - CMD-SHELL\n'
    '      - curl -f http://localhost:8000/health || exit 1\n'
    '      interval: 30s\n'
    '      timeout: 5s\n'
    '      retries: 3\n'
    '      start_period: 120s\n'
)
novo_bloco = bloco[:ancora.end()] + healthcheck + bloco[ancora.end():]
novo = txt[:m_srv.end()] + novo_bloco + txt[bloco_fim:]

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"docker-compose.yml.{CARIMBO}")
ALVO.write_text(novo, encoding="utf-8")
print(f"Aplicado. Backup em _bak/docker-compose.yml.{CARIMBO}")
