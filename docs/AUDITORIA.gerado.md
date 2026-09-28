# AUDITORIA (gerada do codigo; triagem, nao veredito)

## 1. Rotas com resposta fixa (nao consultam banco, IA nem servico)

- `POST /v1/ai/copilot` (main.py:979)
- `GET /api/v1/presenthia/canary` (main.py:1284)
- `GET /api/meu-perfil` (routes/conta_tenant.py:1120)

## 2. Telas com padrao de simulacao

- `admin.html`: salvar simulado (setTimeout)
- `dashboards/ecommerce_config.html`: token/valor aleatorio no navegador
- `dashboards/faturamento.html`: botoes sem nenhuma chamada ao servidor
- `dashboards/gemini_config.html`: eval()
- `dashboards/inicio.html`: salvar simulado (setTimeout); status/valor fixo de mockup; botoes sem nenhuma chamada ao servidor
- `dashboards/video.html`: token/valor aleatorio no navegador; botoes sem nenhuma chamada ao servidor
- `vitrine.html`: token/valor aleatorio no navegador

## 3. Rotas sem verificacao de sessao visivel (revisar se deveriam ser publicas)

- `API_ROUTE /favicon.ico` (main.py:23)
- `POST /v1/leads` (main.py:412)
- `GET /health` (main.py:468)
- `GET /` (main.py:472)
- `GET /register` (main.py:478)
- `POST /login` (main.py:483)
- `POST /logout` (main.py:547)
- `GET /login` (main.py:555)
- `GET /LOGO%20ATENDIT%20STICH.jpg` (main.py:577)
- `POST /v1/auth/register` (main.py:585)
- `GET /api/v1/presenthia/canary` (main.py:1284)
- `API_ROUTE /presenthia/console` (main.py:1298)
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
- `POST /api/public/{slug}/leads` (routes/conta_tenant.py:1298)
- `GET /vitrine/{tenant_slug}` (routes/ecommerce.py:40)
- `GET /items/{tenant}` (routes/ecommerce.py:48)
- `POST /items/{tenant}` (routes/ecommerce.py:85)
- `DELETE /items/{tenant}/{item_id}` (routes/ecommerce.py:143)
- `GET /public/items/{tenant_slug}` (routes/ecommerce.py:165)
- `POST /upload-media/{tenant}` (routes/ecommerce.py:209)
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
- `GET /tenants` (routes/presenthia.py:135)
- `POST /tenants/{tenant_id}/toggle-active` (routes/presenthia.py:146)
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
- `POST /waitlist/{tenant}` (services/calendar/routes.py:679)
- `DELETE /waitlist/{tenant}/{waitlist_id}` (services/calendar/routes.py:705)
- `POST /waitlist/{tenant}/disparar-oferta` (services/calendar/routes.py:725)

## 4. Arquivos com mais marcadores mock/demo/TODO

- 24 x `tests/test_calendly_e2e.py`
- 17 x `routes/conta_tenant.py`
- 15 x `frontend/dashboards/ecommerce_config.html`
- 15 x `frontend/dashboards/configuracao_guiada.html`
- 11 x `frontend/dashboards/intel_operacional.html`
- 9 x `frontend/landing.html`
- 9 x `frontend/dashboards/link_whatsapp.html`
- 8 x `frontend/dashboards/empresa_cadastro.html`
- 7 x `frontend/dashboards/leads.html`
- 6 x `frontend/dashboards/calendar_config.html`
- 5 x `frontend/dashboards/meta_config.html`
- 5 x `frontend/dashboards/ia_config.html`
- 4 x `services/template_service.py`
- 4 x `routes/onboarding.py`
- 4 x `main.py`
