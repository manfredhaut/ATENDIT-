import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, update, and_, or_

from app.core.database import AsyncSessionLocal
from app.models.intel_operacional import (
    OperationalTag,
    EntityTagAssignment,
    CompositeServicePackage,
    CompositeServiceStep,
)
from app.models.scheduling import (
    Appointment,
    Waitlist,
    ServiceType,
    STATUS_AGENDAMENTO,
    STATUS_ESPERA,
)
from app.services.calendar.routes import _exigir_dono

logger = logging.getLogger("atendit.intel_operacional")
router = APIRouter(prefix="/intel", tags=["Inteligência Comercial & Operacional"])

# --- MODELOS PYDANTIC ---
class TagCreate(BaseModel):
    nome: str
    categoria: str = "cliente"
    cor: str = "#0DA2B6"
    descricao: Optional[str] = None

class StepCreate(BaseModel):
    name: str
    step_order: int = 1
    duration_minutes: int = 30
    buffer_minutes: int = 10
    required_skill_tag: Optional[str] = None

class PackageCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[StepCreate] = []

class CalculateSlotRequest(BaseModel):
    steps: List[StepCreate] = []
    travel_time_minutes: int = 0

class StatusUpdateRequest(BaseModel):
    item_id: str
    item_type: str = "appointment" # appointment ou waitlist
    new_status: str

class WaitlistCreateRequest(BaseModel):
    customer_name: str
    customer_phone: str
    service_type_id: Optional[str] = None
    priority: int = 1
    desired_start: Optional[datetime] = None
    desired_end: Optional[datetime] = None

class EncaixeRequest(BaseModel):
    waitlist_id: str
    appointment_id: str

# --- 1. ENDPOINTS DE TAGS (JEV) ---
@router.get("/tags/{tenant}", include_in_schema=False)
async def listar_tags(tenant: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        q = select(OperationalTag).where(OperationalTag.tenant_id == tenant_id).order_by(OperationalTag.nome)
        res = await session.execute(q)
        tags = res.scalars().all()
        return {
            "tags": [
                {
                    "id": str(t.id),
                    "nome": t.nome,
                    "categoria": t.categoria,
                    "cor": t.cor,
                    "descricao": t.descricao or "",
                }
                for t in tags
            ]
        }

@router.post("/tags/{tenant}", include_in_schema=False)
async def criar_tag(tenant: str, data: TagCreate, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        nova = OperationalTag(
            tenant_id=tenant_id,
            nome=data.nome.strip(),
            categoria=data.categoria,
            cor=data.cor,
            descricao=data.descricao,
        )
        session.add(nova)
        await session.commit()
        await session.refresh(nova)
        return {"status": "ok", "tag_id": str(nova.id), "nome": nova.nome}

@router.delete("/tags/{tenant}/{tag_id}", include_in_schema=False)
async def remover_tag(tenant: str, tag_id: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        try:
            tid = uuid.UUID(tag_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="ID de tag inválido")
        await session.execute(
            delete(OperationalTag).where(OperationalTag.id == tid, OperationalTag.tenant_id == tenant_id)
        )
        await session.commit()
        return {"status": "ok"}

# --- 2. ENDPOINTS DE SERVIÇOS COMPOSTOS ---
@router.get("/composite/{tenant}", include_in_schema=False)
async def listar_compostos(tenant: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        pq = select(CompositeServicePackage).where(CompositeServicePackage.tenant_id == tenant_id).order_by(CompositeServicePackage.created_at.desc())
        res = await session.execute(pq)
        pacotes = res.scalars().all()

        lista = []
        for p in pacotes:
            sq = select(CompositeServiceStep).where(CompositeServiceStep.package_id == p.id).order_by(CompositeServiceStep.step_order)
            sres = await session.execute(sq)
            passos = sres.scalars().all()
            total_duration = sum(s.duration_minutes + s.buffer_minutes for s in passos)
            lista.append({
                "id": str(p.id),
                "name": p.name,
                "description": p.description or "",
                "is_active": p.is_active,
                "total_duration_minutes": total_duration,
                "steps": [
                    {
                        "id": str(s.id),
                        "order": s.step_order,
                        "name": s.name,
                        "duration_minutes": s.duration_minutes,
                        "buffer_minutes": s.buffer_minutes,
                        "required_skill": s.required_skill_tag or "",
                    }
                    for s in passos
                ]
            })
        return {"packages": lista}

@router.post("/composite/{tenant}", include_in_schema=False)
async def salvar_composto(tenant: str, data: PackageCreate, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        pkg = CompositeServicePackage(
            tenant_id=tenant_id,
            name=data.name.strip(),
            description=data.description,
            is_active=True
        )
        session.add(pkg)
        await session.flush()

        for i, st in enumerate(data.steps, start=1):
            passo = CompositeServiceStep(
                package_id=pkg.id,
                step_order=i,
                name=st.name.strip(),
                duration_minutes=st.duration_minutes,
                buffer_minutes=st.buffer_minutes,
                required_skill_tag=st.required_skill_tag,
            )
            session.add(passo)

        await session.commit()
        return {"status": "ok", "package_id": str(pkg.id)}

@router.post("/composite/calculate-slot", include_in_schema=False)
async def calcular_janela_slot(req: CalculateSlotRequest):
    steps = req.steps or []
    dur_total = sum(s.duration_minutes for s in steps)
    buf_total = sum(s.buffer_minutes for s in steps)
    transito = req.travel_time_minutes or 0
    janela_final = dur_total + buf_total + transito
    return {
        "duration_total": dur_total,
        "buffer_total": buf_total,
        "transit_minutes": transito,
        "slot_window_required_minutes": janela_final,
        "estimated_hours": round(janela_final / 60, 2)
    }

# --- 3. KANBAN DE FILAS, CRM OPERACIONAL & GESTÃO DE AGENDA ---
@router.get("/kanban/{tenant}", include_in_schema=False)
async def get_kanban_data(tenant: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    agora = datetime.now(timezone.utc)
    hoje_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    hoje_fim = hoje_inicio + timedelta(days=1)

    async with AsyncSessionLocal() as session:
        # Mapa de serviços
        st_res = await session.execute(select(ServiceType).where(ServiceType.tenant_id == tenant_id))
        services_map = {s.id: s for s in st_res.scalars().all()}

        # 1. Fila de Espera
        wq = select(Waitlist).where(
            Waitlist.tenant_id == tenant_id,
            Waitlist.status.in_(["waiting", "notified"])
        ).order_by(Waitlist.priority.desc(), Waitlist.created_at.asc())
        w_res = await session.execute(wq)
        waitlist_items = w_res.scalars().all()

        # 2. Agendamentos Recentes (últimos 7 dias e próximos 14 dias)
        corte_passado = hoje_inicio - timedelta(days=7)
        corte_futuro = hoje_inicio + timedelta(days=14)
        aq = select(Appointment).where(
            Appointment.tenant_id == tenant_id,
            Appointment.start_at >= corte_passado,
            Appointment.start_at <= corte_futuro
        ).order_by(Appointment.start_at.asc())
        a_res = await session.execute(aq)
        appointments = a_res.scalars().all()

        # Agrupamento das 5 Colunas
        col_fila = []
        for w in waitlist_items:
            srv = services_map.get(w.service_type_id)
            col_fila.append({
                "id": str(w.id),
                "type": "waitlist",
                "customer_name": w.customer_name or "Cliente na Fila",
                "customer_phone": w.customer_phone,
                "service_name": srv.name if srv else "Geral",
                "service_duration": srv.duration_minutes if srv else 30,
                "priority": w.priority,
                "status": w.status,
                "desired_window": f"{w.desired_start.strftime('%d/%m %H:%M') if w.desired_start else 'Flexível'} a {w.desired_end.strftime('%d/%m %H:%M') if w.desired_end else 'Flexível'}",
                "created_at": w.created_at.strftime('%d/%m %H:%M')
            })

        col_confirmados = []
        col_em_rota_hoje = []
        col_concluidos = []
        col_desistencias = []

        for a in appointments:
            srv = services_map.get(a.service_type_id)
            dur = int((a.end_at - a.start_at).total_seconds() / 60)
            item = {
                "id": str(a.id),
                "type": "appointment",
                "customer_name": a.customer_name or "Cliente Agendado",
                "customer_phone": a.customer_phone,
                "service_name": srv.name if srv else "Atendimento Técnico",
                "service_duration": dur,
                "start_at_iso": a.start_at.isoformat(),
                "end_at_iso": a.end_at.isoformat(),
                "data_fmt": a.start_at.strftime('%d/%m/%Y'),
                "hora_fmt": f"{a.start_at.strftime('%H:%M')} - {a.end_at.strftime('%H:%M')}",
                "status": a.status,
                "provider": a.provider
            }

            if a.status in ["cancelled", "no_show"]:
                col_desistencias.append(item)
            elif a.status == "completed":
                col_concluidos.append(item)
            elif a.status == "confirmed":
                if hoje_inicio <= a.start_at < hoje_fim:
                    col_em_rota_hoje.append(item)
                else:
                    col_confirmados.append(item)

        return {
            "columns": {
                "fila_espera": col_fila,
                "confirmados": col_confirmados,
                "em_rota_hoje": col_em_rota_hoje,
                "concluidos": col_concluidos,
                "desistencias": col_desistencias
            },
            "metrics": {
                "total_fila": len(col_fila),
                "total_confirmados": len(col_confirmados),
                "total_hoje": len(col_em_rota_hoje),
                "total_concluidos": len(col_concluidos),
                "total_desistencias": len(col_desistencias)
            }
        }

@router.post("/kanban/status/{tenant}", include_in_schema=False)
async def update_status_kanban(tenant: str, data: StatusUpdateRequest, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        try:
            uid = uuid.UUID(data.item_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="ID inválido")

        if data.item_type == "appointment":
            if data.new_status not in STATUS_AGENDAMENTO:
                raise HTTPException(status_code=400, detail="Status de agendamento inválido")
            await session.execute(
                update(Appointment).where(
                    Appointment.id == uid, Appointment.tenant_id == tenant_id
                ).values(status=data.new_status, updated_at=datetime.now(timezone.utc))
            )
        elif data.item_type == "waitlist":
            if data.new_status not in STATUS_ESPERA:
                raise HTTPException(status_code=400, detail="Status de espera inválido")
            await session.execute(
                update(Waitlist).where(
                    Waitlist.id == uid, Waitlist.tenant_id == tenant_id
                ).values(status=data.new_status)
            )

        await session.commit()
        return {"status": "ok", "new_status": data.new_status}

# --- 4. MOTOR JEV DE RECUPERAÇÃO DE DESISTÊNCIAS (GAP-FILLING) ---
@router.get("/gap-fill/{tenant}/{appointment_id}", include_in_schema=False)
async def get_gap_fill_candidates(tenant: str, appointment_id: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    try:
        aid = uuid.UUID(appointment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de agendamento inválido")

    async with AsyncSessionLocal() as session:
        # Busca agendamento cancelado
        aq = select(Appointment).where(Appointment.id == aid, Appointment.tenant_id == tenant_id)
        a_res = await session.execute(aq)
        appo = a_res.scalar_one_or_none()
        if not appo:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")

        gap_dur = int((appo.end_at - appo.start_at).total_seconds() / 60)

        # Busca fila de espera
        wq = select(Waitlist).where(
            Waitlist.tenant_id == tenant_id,
            Waitlist.status.in_(["waiting", "notified"])
        )
        w_res = await session.execute(wq)
        fila = w_res.scalars().all()

        st_res = await session.execute(select(ServiceType).where(ServiceType.tenant_id == tenant_id))
        services_map = {s.id: s for s in st_res.scalars().all()}

        # Algoritmo JEV de Pontuação de Encaixe
        candidatos = []
        for w in fila:
            srv = services_map.get(w.service_type_id)
            srv_dur = srv.duration_minutes if srv else 30

            score = 50 + (w.priority * 15)
            # Cabe na janela de desistência?
            if srv_dur <= gap_dur:
                score += 30
            else:
                score -= 40

            # Janela desejada coincide com o dia?
            if w.desired_start and w.desired_start.date() == appo.start_at.date():
                score += 20

            candidatos.append({
                "waitlist_id": str(w.id),
                "customer_name": w.customer_name or "Cliente na Fila",
                "customer_phone": w.customer_phone,
                "service_name": srv.name if srv else "Geral",
                "service_duration": srv_dur,
                "priority": w.priority,
                "score_aderencia": max(10, min(100, score)),
                "cabe_no_horario": srv_dur <= gap_dur
            })

        candidatos.sort(key=lambda x: x["score_aderencia"], reverse=True)

        return {
            "gap_start": appo.start_at.isoformat(),
            "gap_end": appo.end_at.isoformat(),
            "gap_dur_minutes": gap_dur,
            "gap_data_fmt": appo.start_at.strftime("%d/%m/%Y"),
            "gap_hora_fmt": f"{appo.start_at.strftime('%H:%M')} às {appo.end_at.strftime('%H:%M')}",
            "candidates": candidatos[:5]
        }

@router.post("/gap-fill/encaixar/{tenant}", include_in_schema=False)
async def executar_encaixe_jev(tenant: str, data: EncaixeRequest, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    try:
        wid = uuid.UUID(data.waitlist_id)
        aid = uuid.UUID(data.appointment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="IDs inválidos")

    async with AsyncSessionLocal() as session:
        # Busca a desistência
        a_res = await session.execute(select(Appointment).where(Appointment.id == aid, Appointment.tenant_id == tenant_id))
        appo_antigo = a_res.scalar_one_or_none()
        if not appo_antigo:
            raise HTTPException(status_code=404, detail="Vaga de agendamento não localizada")

        # Busca o item na fila
        w_res = await session.execute(select(Waitlist).where(Waitlist.id == wid, Waitlist.tenant_id == tenant_id))
        wait_item = w_res.scalar_one_or_none()
        if not wait_item:
            raise HTTPException(status_code=404, detail="Cliente na fila não localizado")

        # Cria o novo agendamento aproveitando a vaga
        novo_appo = Appointment(
            tenant_id=tenant_id,
            service_type_id=wait_item.service_type_id or appo_antigo.service_type_id,
            customer_phone=wait_item.customer_phone,
            customer_name=wait_item.customer_name,
            start_at=appo_antigo.start_at,
            end_at=appo_antigo.end_at,
            status="confirmed",
            provider="local",
            source="jev_gap_fill"
        )
        session.add(novo_appo)

        # Atualiza status da fila para booked
        wait_item.status = "booked"
        await session.commit()
        await session.refresh(novo_appo)

        return {
            "status": "ok",
            "message": "Encaixe JEV realizado com sucesso!",
            "new_appointment_id": str(novo_appo.id)
        }

@router.post("/waitlist/{tenant}", include_in_schema=False)
async def adicionar_fila_espera(tenant: str, data: WaitlistCreateRequest, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        srv_id = None
        if data.service_type_id:
            try:
                srv_id = uuid.UUID(data.service_type_id)
            except ValueError:
                pass

        item = Waitlist(
            tenant_id=tenant_id,
            customer_name=data.customer_name.strip(),
            customer_phone=data.customer_phone.strip(),
            service_type_id=srv_id,
            priority=data.priority,
            status="waiting",
            desired_start=data.desired_start,
            desired_end=data.desired_end
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return {"status": "ok", "waitlist_id": str(item.id)}
