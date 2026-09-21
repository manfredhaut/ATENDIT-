"""
E-mail transacional via Resend.

Uma função só, deliberadamente: `enviar_email`. Confirmação de cadastro,
reset de senha e avisos operacionais são todos "mandar um HTML para um
endereço" — o que muda é o conteúdo, não o transporte.

## Por que httpx e não urllib

A API do Resend fica atrás da Cloudflare, e ela **bloqueia o User-Agent padrão
do urllib** com `403 error code 1010`. Medido em 2026-09-01: a mesma requisição,
mesma chave, mesmo servidor — `urllib` levou 403, `httpx` devolveu 200. O
projeto já usa httpx em todo lugar; a nota existe para que ninguém "simplifique"
para urllib depois e passe horas caçando um 403 que parece problema de chave.

## O que esta função NÃO faz

Não repete o envio. Devolve `False` e loga; quem chama decide se tenta de novo.
Reenvio automático de e-mail é o caminho curto para mandar a mesma confirmação
cinco vezes ao mesmo destinatário.
"""

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

URL_RESEND = "https://api.resend.com/emails"
TEMPO_LIMITE = 30.0


async def enviar_email(
    destinatario: str,
    assunto: str,
    corpo_html: str,
    remetente: Optional[str] = None,
) -> bool:
    """
    Envia um e-mail. Devolve True se o Resend aceitou, False caso contrário.

    NUNCA levanta exceção: quem chama costuma estar num fluxo em que o dado
    principal já foi gravado (cadastro feito, senha trocada), e uma falha de
    e-mail não pode desfazer isso nem virar erro 500 para o usuário.
    """
    if not settings.RESEND_API_KEY:
        # Falha explicita e fechada. O contrario -- logar "enviado" sem enviar --
        # e o modo de falha que o PNL-01 existe para eliminar.
        logger.error(
            "[EMAIL] RESEND_API_KEY nao configurada. "
            f"NADA foi enviado para '{destinatario}'."
        )
        return False

    de = remetente or settings.EMAIL_REMETENTE
    payload = {
        "from": de,
        "to": [destinatario],
        "subject": assunto,
        "html": corpo_html,
    }

    try:
        async with httpx.AsyncClient(timeout=TEMPO_LIMITE) as cliente:
            resposta = await cliente.post(
                URL_RESEND,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
    except Exception as exc:
        logger.error(f"[EMAIL] Falha de rede ao falar com o Resend: {exc}")
        return False

    if resposta.status_code in (200, 201):
        # O id do Resend e o que permite rastrear a entrega no painel deles
        # depois. Sem ele, "o cliente diz que nao recebeu" nao tem investigacao.
        try:
            id_msg = resposta.json().get("id", "?")
        except Exception:
            id_msg = "?"
        logger.info(
            f"[EMAIL] Enviado para '{destinatario}' | assunto='{assunto}' | id={id_msg}"
        )
        return True

    logger.error(
        f"[EMAIL] Resend recusou para '{destinatario}': "
        f"HTTP {resposta.status_code} :: {resposta.text[:300]}"
    )
    return False


def montar_html(titulo: str, paragrafos: list[str], botao_texto: str = "", botao_url: str = "") -> str:
    """
    Molde HTML simples e sem dependência externa.

    Tudo inline: cliente de e-mail ignora <style> em <head> com frequência, e
    nenhum deles carrega CSS externo. Largura fixa em 560px e tabela como
    contêiner porque é o que o Outlook renderiza de forma previsível.
    """
    corpo = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333;">{p}</p>'
        for p in paragrafos
    )
    botao = ""
    if botao_texto and botao_url:
        botao = (
            f'<p style="margin:28px 0 8px;">'
            f'<a href="{botao_url}" style="background:#0aa2c0;color:#fff;text-decoration:none;'
            f'padding:13px 26px;border-radius:6px;font-size:15px;font-weight:600;'
            f'display:inline-block;">{botao_texto}</a></p>'
            # O link em texto vem SEMPRE junto: cliente de e-mail corporativo
            # reescreve ou bloqueia <a>, e ai o botao vira um retangulo morto.
            f'<p style="margin:14px 0 0;font-size:12px;color:#888;word-break:break-all;">'
            f'Se o botão não funcionar, copie este endereço no navegador:<br>{botao_url}</p>'
        )
    return (
        '<div style="background:#f4f6f8;padding:28px 12px;font-family:'
        '-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px;margin:0 auto;background:#fff;border-radius:10px;'
        'border:1px solid #e3e8ee;"><tr><td style="padding:32px;">'
        '<p style="margin:0 0 22px;font-size:19px;font-weight:700;color:#0aa2c0;'
        'letter-spacing:.5px;">ATENDIT</p>'
        f'<h1 style="margin:0 0 18px;font-size:20px;color:#111;">{titulo}</h1>'
        f'{corpo}{botao}'
        '<p style="margin:30px 0 0;padding-top:18px;border-top:1px solid #eef1f4;'
        'font-size:12px;color:#98a2b3;">Esta mensagem é automática — não responda '
        'a este endereço.</p>'
        '</td></tr></table></div>'
    )
