import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger("atendit.jev")


class JevService:
    """
    Serviço de avaliação semântica TypeSafe AI (JEV) para escalonamento para vídeo
    e extração de tags de necessidades e intenções de compra.
    """

    def __init__(self):
        self.api_key = settings.JEV_API_KEY
        self.api_url = settings.JEV_API_URL
        self.threshold = settings.JEV_VIDEO_THRESHOLD

    async def avaliar_escalonamento_video(
        self,
        user_message: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        tenant_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calcula o score S(video) = w1*C(lead) + w2*T(complex) + w3*I(explicit)
        e extrai tags contextuais do desejo/necessidade do cliente.
        """
        # 1. Se possuir chave configurada, consome a API TypeSafe JEV
        if self.api_key:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    payload = {
                        "prompt": user_message,
                        "history": chat_history or [],
                        "schema": {
                            "type": "object",
                            "properties": {
                                "c_lead": {"type": "number", "description": "Propensão comercial / valor do lead (0 a 1)"},
                                "t_complex": {"type": "number", "description": "Complexidade técnica do problema/dúvida (0 a 1)"},
                                "i_explicit": {"type": "number", "description": "Intenção explícita de ver produto/vídeo (0 a 1)"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "foco_produto": {"type": "string"}
                            },
                            "required": ["c_lead", "t_complex", "i_explicit", "tags"]
                        }
                    }
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                    resp = await client.post(self.api_url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        dados = resp.json()
                        c_lead = float(dados.get("c_lead", 0.3))
                        t_complex = float(dados.get("t_complex", 0.2))
                        i_explicit = float(dados.get("i_explicit", 0.0))
                        
                        s_video = round((0.25 * c_lead) + (0.45 * t_complex) + (0.30 * i_explicit), 3)
                        deve_escalar = s_video >= self.threshold

                        logger.info(
                            f"[JEV API SUCCESS] S_video={s_video} (Threshold={self.threshold}) | "
                            f"Escalar={deve_escalar} | Tags={dados.get('tags')}"
                        )
                        return {
                            "s_video": s_video,
                            "deve_escalar": deve_escalar,
                            "tags": dados.get("tags", []),
                            "foco_produto": dados.get("foco_produto", ""),
                            "provider": "typesafe_jev"
                        }
                    else:
                        logger.warning(f"[JEV API HTTP {resp.status_code}] Falha de resposta; acionando motor local.")
            except Exception as exc:
                logger.error(f"[JEV API ERROR] {exc}. Acionando motor semântico local.")

        # 2. Motor Heurístico Local de Alta Precisão (Garante operação sem falhas)
        return self._avaliar_heuristica_local(user_message)

    def _avaliar_heuristica_local(self, msg: str) -> Dict[str, Any]:
        """Avaliação sintática e semântica com pesos algorítmicos locais."""
        texto = (msg or "").lower()
        tags = []

        # Detecção de Intenção Explícita de Vídeo / Ver Peça (I_explicit)
        termos_explicitos = ["video", "vídeo", "câmera", "camera", "mostrar", "olhar", "ao vivo", "chamada", "conferência", "conferencia", "demonstrar"]
        i_explicit = 1.0 if any(t in texto for t in termos_explicitos) else 0.0
        if i_explicit > 0:
            tags.append("intencao_explicita_video")

        # Detecção de Complexidade Técnica / Problema Visual (T_complex)
        termos_complexos = ["defeito", "quebrado", "quebrou", "peça", "peca", "código", "codigo", "instalar", "esquema", "ligação", "inversora", "placa", "modelo", "manual", "visita"]
        qtd_complexos = sum(1 for t in termos_complexos if t in texto)
        t_complex = min(1.0, qtd_complexos * 0.4)
        if t_complex > 0:
            tags.append("complexidade_tecnica")

        # Detecção de Valor de Lead / Interesse Comercial (C_lead)
        termos_comerciais = ["comprar", "preço", "preco", "quanto custa", "orçamento", "orcamento", "catálogo", "catalogo", "serviço", "servico", "contratar", "garantia"]
        qtd_comerciais = sum(1 for t in termos_comerciais if t in texto)
        c_lead = min(1.0, 0.4 + (qtd_comerciais * 0.3))
        if qtd_comerciais > 0:
            tags.append("interesse_comercial")

        # Identificação de Produtos em Destaque
        foco_produto = ""
        if "manutencao" in texto or "manutenção" in texto:
            tags.append("manutencao")
            foco_produto = "Visita Técnica Especializada"
        elif "placa" in texto or "inversor" in texto:
            tags.append("componentes")
            foco_produto = "Placa Inversora Controladora"

        s_video = round((0.25 * c_lead) + (0.45 * t_complex) + (0.30 * i_explicit), 3)
        deve_escalar = s_video >= self.threshold

        logger.info(
            f"[JEV LOCAL ENGINE] Msg='{msg[:40]}...' -> S_video={s_video} "
            f"(C={c_lead:.2f}, T={t_complex:.2f}, I={i_explicit:.2f}) | "
            f"Escalar={deve_escalar} | Tags={tags}"
        )

        return {
            "s_video": s_video,
            "deve_escalar": deve_escalar,
            "tags": tags,
            "foco_produto": foco_produto,
            "provider": "local_semantic_engine"
        }


jev_service = JevService()
