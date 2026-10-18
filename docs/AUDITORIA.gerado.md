# AUDITORIA (gerada do codigo; triagem, nao veredito)

## 1. Rotas com resposta fixa (nao consultam banco, IA nem servico)

- `POST /v1/ai/copilot` (main.py:987)
- `GET /api/v1/presenthia/canary` (main.py:1294)
- `GET /api/meu-perfil` (routes/conta_tenant.py:1137)

## 2. Telas com padrao de simulacao

- `dashboards/aviso_fase3_profissionais.html`: botoes sem nenhuma chamada ao servidor
- `dashboards/ecommerce_config.html`: token/valor aleatorio no navegador
- `dashboards/faturamento.html`: botoes sem nenhuma chamada ao servidor
- `dashboards/gemini_config.html`: eval()
- `dashboards/video.html`: token/valor aleatorio no navegador; botoes sem nenhuma chamada ao servidor
- `vitrine.html`: token/valor aleatorio no navegador

## 3. Rotas sem verificacao de sessao visivel (revisar se deveriam ser publicas)

- `API_ROUTE /favicon.ico` (main.py:23)
- `POST /v1/leads` (main.py:411)
- `GET /health` (main.py:467)
- `GET /` (main.py:471)
- `GET /register` (main.py:477)
- `POST /login` (main.py:482)
- `POST /logout` (main.py:546)
- `GET /login` (main.py:554)
- `GET /LOGO%20ATENDIT%20STICH.jpg` (main.py:576)
- `POST /v1/auth/register` (main.py:584)
- `API_ROUTE /presenthia/console` (main.py:1308)
- `POST /submit/{tenant_id}` (routes/ai_config.py:56)
- `GET /{tenant_id}` (routes/ai_config.py:130)
- `GET /tenant/login` (routes/conta_tenant.py:246)
- `POST /tenant/login` (routes/conta_tenant.py:284)
- `POST /tenant/logout` (routes/conta_tenant.py:315)
- `GET /v1/auth/esqueci-senha-form` (routes/conta_tenant.py:818)
- `POST /v1/auth/esqueci-senha` (routes/conta_tenant.py:850)
- `GET /reset-senha` (routes/conta_tenant.py:903)
- `POST /v1/auth/reset-senha` (routes/conta_tenant.py:950)
- `GET /api/templates/segmentos` (routes/conta_tenant.py:1000)
- `POST /api/public/{slug}/leads` (routes/conta_tenant.py:1315)
- `GET /vitrine/{tenant_slug}` (routes/ecommerce.py:41)
- `GET /items/{tenant}` (routes/ecommerce.py:49)
- `POST /items/{tenant}` (routes/ecommerce.py:86)
- `DELETE /items/{tenant}/{item_id}` (routes/ecommerce.py:144)
- `GET /public/items/{tenant_slug}` (routes/ecommerce.py:166)
- `POST /upload-media/{tenant}` (routes/ecommerce.py:210)
- `GET /media/{tenant}` (routes/ecommerce.py:247)
- `DELETE /media/{tenant}/{media_id}` (routes/ecommerce.py:270)
- `GET /status/{tenant_id}` (routes/hardware.py:6)
- `GET /tags/{tenant}` (routes/intel_operacional.py:70)
- `POST /tags/{tenant}` (routes/intel_operacional.py:90)
- `DELETE /tags/{tenant}/{tag_id}` (routes/intel_operacional.py:106)
- `GET /composite/{tenant}` (routes/intel_operacional.py:121)
- `POST /composite/{tenant}` (routes/intel_operacional.py:155)
- `POST /composite/calculate-slot` (routes/intel_operacional.py:182)
- `GET /kanban/{tenant}` (routes/intel_operacional.py:198)
- `POST /kanban/status/{tenant}` (routes/intel_operacional.py:296)
- `GET /gap-fill/{tenant}/{appointment_id}` (routes/intel_operacional.py:326)
- `POST /gap-fill/encaixar/{tenant}` (routes/intel_operacional.py:394)
- `POST /waitlist/{tenant}` (routes/intel_operacional.py:441)
- `GET /config/{tenant}` (routes/logistics.py:16)
- `POST /config/{tenant}` (routes/logistics.py:49)
- `GET /webhook/{tenant_id}` (routes/meta.py:28)
- `POST /webhook/{tenant_id}` (routes/meta.py:45)
- `GET /stats` (routes/presenthia.py:109)
- `GET /flags` (routes/presenthia.py:157)
- `POST /flags/{flag_key}/toggle` (routes/presenthia.py:168)
- `GET /copilots` (routes/presenthia.py:180)
- `POST /copilots/{tenant_id}/toggle-active` (routes/presenthia.py:234)
- `GET /subscriptions` (routes/presenthia.py:246)
- `GET /pulso` (routes/presenthia.py:289)
- `GET /auditoria` (routes/presenthia.py:346)
- `POST /` (routes/tenants.py:51)
- `GET /` (routes/tenants.py:106)
- `GET /{tenant_id}` (routes/tenants.py:119)
- `GET /meet/{room_token}` (routes/video.py:56)
- `GET /v1/video/rooms/{room_token}` (routes/video.py:107)
- `GET /v1/video/config/{slug_or_id}` (routes/video.py:208)
- `POST /v1/video/config/{slug_or_id}` (routes/video.py:235)
- `GET /oauth/{provider}/start` (services/calendar/routes.py:143)
- `GET /oauth/{provider}/callback` (services/calendar/routes.py:162)
- `GET /status/{tenant}` (services/calendar/routes.py:227)
- `POST /disconnect/{tenant}` (services/calendar/routes.py:309)
- `GET /appointments/{tenant}` (services/calendar/routes.py:349)
- `GET /message-templates/{tenant}` (services/calendar/routes.py:409)
- `POST /message-templates/{tenant}` (services/calendar/routes.py:415)
- `POST /connections/calendly/{tenant}` (services/calendar/routes.py:428)
- `POST /appointments/{tenant}` (services/calendar/routes.py:501)
- `GET /crm-summary/{tenant}` (services/calendar/routes.py:565)
- `GET /waitlist/{tenant}` (services/calendar/routes.py:647)
- `POST /waitlist/{tenant}` (services/calendar/routes.py:682)
- `DELETE /waitlist/{tenant}/{waitlist_id}` (services/calendar/routes.py:708)
- `POST /waitlist/{tenant}/disparar-oferta` (services/calendar/routes.py:728)

## 4. Arquivos com mais marcadores mock/demo/TODO

- 24 x `tests/test_calendly_e2e.py`
- 17 x `routes/conta_tenant.py`
- 15 x `frontend/dashboards/ecommerce_config.html`
- 15 x `frontend/dashboards/configuracao_guiada.html`
- 11 x `frontend/dashboards/intel_operacional.html`
- 9 x `frontend/landing.html`
- 9 x `frontend/dashboards/link_whatsapp.html`
- 9 x `frontend/dashboards/calendar_config.html`
- 8 x `frontend/dashboards/empresa_cadastro.html`
- 7 x `frontend/dashboards/leads.html`
- 5 x `frontend/dashboards/meta_config.html`
- 5 x `frontend/dashboards/ia_config.html`
- 4 x `services/template_service.py`
- 4 x `main.py`
- 4 x `frontend/dashboards/gemini_config.html`
