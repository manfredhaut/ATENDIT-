# Matriz de Paridade e Inventário Funcional: ATENDIT → Presenthia (F1.0)

Última atualização: **2026-09-24**
Referência: *Plano de Ação — ATENDIT → Presenthia V2*

Regra inegociável: nenhuma funcionalidade existente é desativada. Todas as rotas do ATENDIT continuam operantes ou são mapeadas de forma aditiva no Presenthia.

## 1. Mapeamento das 14 Abas Principais

| # | Aba Presenthia | Aba Legada ATENDIT | Sub-abas / Funcionalidades Mapeadas | Status de Paridade |
|---|---|---|---|---|
| 1 | **Início** | Painel Principal | Boas-vindas, pendências do dia, saúde dos canais WhatsApp, consumo de plano e atalhos rápidos de configuração. | Em migração |
| 2 | **Atendimento** | Atendimento | Triagem, Conversas ativas, Transbordo humano, Histórico de finalizados, Setores e filas, Notas internas, SLA. | Mantido / Operante |
| 3 | **Aquisição** | *(Nova)* | Visão geral de tráfego, Conectar origem (links rastreáveis/QR), Indicação, Atribuição Meta CTWA, Webhooks de formulários (F2.7). | Estrutura pronta |
| 4 | **CRM & Funil** | *(Nova / Absorve Cockpit)* | Kanban de negócios, Contatos e empresas unificados, Tags e perfis comportamentais, Histórico de atividades, Previsão de vendas. | Modelos criados |
| 5 | **Agenda** | Configuração do Calendário | Grade (dia, semana, mês), Encaixe manual (+Encaixar), Sincronização externa (Google, Microsoft, Calendly), Lembretes anti-no-show. | Validado E2E |
| 6 | **Profissionais & Escalas** | *(Absorve parte de Calendário)* | Cadastro de profissionais, Escalas e folgas, Serviços executados, Salas e recursos, Combos multi-profissionais, Deslocamento logístico (F3.18). | Modelos criados |
| 7 | **Vitrine & Loja** | Loja & E-Commerce | Catálogo unificado, Vitrine pública /<slug>, Políticas de reserva, Links de pedido (SDUI/Magic Links), Pedidos e cupons. | Mantido / Operante |
| 8 | **Consultoria por Vídeo** | *(Absorve Vídeo WebRTC)* | Sessões guiadas no navegador (LiveKit/WebRTC), Câmera opcional, Roteiros por segmento, Transbordo para especialista. | Modelos e sala prontos |
| 9 | **Assistente IA** | Configurações do Atendente + Configuração da IA | Persona e tom unificados, Perguntas de qualificação, Provedores e chaves (Gemini/OpenAI), Matriz de capacidades (F5.8), Regras e FAQ, Simulador. | Operante |
| 10 | **Base de Conhecimento** | Gestão de Documentos | Ingestão e upload de documentos, Catálogo de ativos e expurgo, Simulador semântico e auditoria vetorial. | Mantido / Operante |
| 11 | **Canais** | Conexão WhatsApp QR Code + WhatsApp Meta Oficial | Pareamento QR Code (Evolution API), WhatsApp Cloud API Oficial, Webhooks, Comparativo de limitações lado a lado. | Validado |
| 12 | **Financeiro** | *(Absorve Gateways & Pix)* | Recebimentos (Mercado Pago, Asaas, Pix direto), Formas de pagamento, Extrato e repasses, Assinatura da plataforma. | Em estruturação |
| 13 | **Inteligência Comercial & Operacional** | Inteligência Comercial & Operacional | Relatórios consolidados, Tempos de resposta, Conversão IA x Humano, Auditoria de atendimento e métricas. | Mantido / Operante |
| 14 | **Empresa & Conta** | Cadastro da Empresa + Minha Conta | Perfil cadastral completo da empresa, Gestão de usuários e permissões (RBAC), Segurança e troca de senha, Sessões ativas, Termos LGPD. | Telas e endpoints prontos |

## 2. Inventário de Rotas Legadas e Compatibilidade

* `POST /webhook/evolution/{tenant_id}` -> Preservado (com trava HMAC e suporte a `@lid` puro).
* `POST /calendar/webhook/calendly/{tenant_id}` -> Preservado e validado ponta a ponta.
* `GET /tenant/painel` -> Preservado com suporte à chave de alternância de menu.
* `GET /admin` -> Preservado, direcionando para console administrativo com isolamento Traefik.
* `POST /v1/auth/alterar-senha` -> Implementado e ativo.
* `POST /v1/auth/esqueci-senha` -> Implementado e ativo.
