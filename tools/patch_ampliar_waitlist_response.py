#!/usr/bin/env python3
"""GET /waitlist/{tenant} so devolvia datas ja formatadas para humano e o
NOME do servico -- sem os campos brutos (service_type_id, ISO das datas)
que o botao de 'Enviar Oferta' precisa para montar o payload de
disparar-oferta. Acrescenta 3 campos, sem remover nenhum dos existentes
(quem ja le os campos formatados continua funcionando igual). Idempotente."""
import shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/services/calendar/routes.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "desired_start_iso" in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ALVO_DICT = '''            resultado.append({
                "id": str(w.id),
                "cliente": w.customer_name or "Cliente",
                "telefone": w.customer_phone,
                "servico": st.name,
                "prioridade": w.priority,
                "status": w.status,
                "janela_inicio": w.desired_start.astimezone(fuso).strftime("%d/%m %H:%M"),
                "janela_fim": w.desired_end.astimezone(fuso).strftime("%d/%m %H:%M"),
                "ofertado_em": w.notified_at.astimezone(fuso).strftime("%d/%m às %H:%M") if w.notified_at else None,
                "horario_ofertado": w.offered_start.astimezone(fuso).strftime("%d/%m às %H:%M") if w.offered_start else None,
            })'''

NOVO_DICT = '''            resultado.append({
                "id": str(w.id),
                "cliente": w.customer_name or "Cliente",
                "telefone": w.customer_phone,
                "servico": st.name,
                "service_type_id": str(w.service_type_id),
                "prioridade": w.priority,
                "status": w.status,
                "janela_inicio": w.desired_start.astimezone(fuso).strftime("%d/%m %H:%M"),
                "janela_fim": w.desired_end.astimezone(fuso).strftime("%d/%m %H:%M"),
                "desired_start_iso": w.desired_start.isoformat(),
                "desired_end_iso": w.desired_end.isoformat(),
                "ofertado_em": w.notified_at.astimezone(fuso).strftime("%d/%m às %H:%M") if w.notified_at else None,
                "horario_ofertado": w.offered_start.astimezone(fuso).strftime("%d/%m às %H:%M") if w.offered_start else None,
            })'''

if ALVO_DICT not in txt:
    sys.exit("ERRO: bloco original do dicionario de resultado nao encontrado.")
txt = txt.replace(ALVO_DICT, NOVO_DICT, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"calendar_routes.py.{CARIMBO}")
ALVO.write_text(txt, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/calendar_routes.py.{CARIMBO}")
print("Sintaxe valida.")
