# PARIDADE ATENDIT -> Presenthia (gerado do codigo; revisar tela a tela)

Legenda: L = no menu legado, V2 = no menu novo, Ref = aberta por outra tela.

| View | Titulo | L | V2 | Ref | Rotas chamadas | Destino Presenthia | Status |
|---|---|---|---|---|---|---|---|
| calendar_config | Painel de Atendimentos & Gestão de Clientes | x | x |  | /calendar/appointments/<br>/calendar/connections/calendly/<br>/calendar/crm-summary/<br>/calendar/disconnect/<br>/calendar/message-templates/<br>/calendar/status/ | 5 Agenda (+ 6 Profissionais & Escalas) | conferir |
| canais | 📡 Central de Canais de Mensageria | x | x | configuracao_guiada,conta_tenant | /dashboards/ | 11 Canais | conferir |
| configuracao_guiada | ✯ Configuração Guiada Presenthia |  |  | conta_tenant | /api/tenant/onboarding-persona<br>/api/tenant/onboarding-status<br>/api/tenant/simulador-teste | 1 Início › Configuração guiada | conferir |
| ecommerce_config | Vitrine &amp; Loja | x | x | configuracao_guiada,faturamento | /v1/ecommerce/items/<br>/v1/video/config/ | 7 Vitrine & Loja | conferir |
| empresa_cadastro | 🏢 Dados da Empresa & Ficha Cadastral | x | x |  | /v1/tenants/<br>/v1/tenants/update | 14 Empresa & Conta | conferir |
| equipe | 👥 Gestão de Equipe e Permissões (F1.7) |  | x |  | /api/equipe<br>/api/equipe/<br>/api/equipe/convidar | 14 Empresa & Conta › Usuários e permissões (F1.7) | conferir |
| faturamento | Financeiro |  | x |  | - | 12 Financeiro | conferir |
| fila_atendimento | - | x | x |  | /v1/whatsapp/chats<br>/v1/whatsapp/chats/ | 2 Atendimento | conferir |
| gemini_config | - | x |  |  | - | 9 Assistente IA › Modelos e chaves | !! SEM ENTRADA NO MENU NOVO |
| ia_config | - | x | x |  | /v1/agent/config<br>/v1/tenants/ | 9 Assistente IA › Persona e tom | conferir |
| inicio | - | x | x | conta_tenant | - | 1 Início | conferir |
| intel_operacional | Matriz de Tags & Contexto JEV | x | x |  | /calendar/logistics/config/<br>/intel/composite/<br>/intel/gap-fill/<br>/intel/gap-fill/encaixar/<br>/intel/kanban/<br>/intel/kanban/status/ | 13 Inteligência Comercial & Operacional | conferir |
| leads | 📥 Leads & Conversão Omnichannel (Fase 2 Compl |  | x |  | /api/tenant/leads<br>/api/tenant/leads/ | 3 Aquisição / 4 CRM & Funil (provisório: mesma view) | conferir |
| link_whatsapp | - |  |  | canais | /v1/tenants/<br>/v1/whatsapp/pair-code<br>/v1/whatsapp/qrcode<br>/v1/whatsapp/reset<br>/v1/whatsapp/status | 11 Canais › WhatsApp QR Code | conferir |
| meta_config | - |  |  | canais | /v1/meta/config/ | 11 Canais › WhatsApp Oficial | conferir |
| modelos_segmento | 🎯 Modelos Especializados por Segmento (F1.4) |  |  |  | /api/templates/aplicar | F1.4 Modelos por segmento | orfa (nao acessivel) |
| rag_management | - | x | x | configuracao_guiada | /v1/rag/documents/ | 10 Base de Conhecimento | conferir |
| video | 📹 Consultoria Guiada por Vídeo |  | x |  | - | 8 Consultoria por Vídeo | conferir |

## Alertas

- `gemini_config`: existe no menu legado e nao tem caminho no menu novo.
