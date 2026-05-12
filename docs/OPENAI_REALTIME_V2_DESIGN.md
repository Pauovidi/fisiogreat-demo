# OpenAI Realtime V2 Design

## Objetivo

Crear una V2 experimental del callbot de FisioGreat Demo sin sustituir la V1:

- V1: Twilio ConversationRelay + ElevenLabs + estado conversacional propio.
- V2: OpenAI Realtime como núcleo de voz, con tools server-side conectadas al motor actual.

La V2 queda aislada, en shadow por defecto y sin escrituras reales salvo activación explícita.

## Fuentes oficiales usadas

- OpenAI Realtime SIP: https://developers.openai.com/api/docs/guides/realtime-sip
- OpenAI Realtime WebSocket: https://developers.openai.com/api/docs/guides/realtime-websocket
- OpenAI Realtime server-side controls: https://developers.openai.com/api/docs/guides/realtime-server-controls
- Modelo `gpt-realtime-2`: https://developers.openai.com/api/docs/models/gpt-realtime-2

## Arquitectura Recomendada

Recomendación para una prueba real: Opción A, OpenAI Realtime vía SIP con Twilio como proveedor telefónico.

Motivo:

- OpenAI documenta SIP para conectar números telefónicos a Realtime mediante un proveedor SIP como Twilio.
- Evita implementar un puente propio de audio Twilio -> backend -> OpenAI.
- Nuestro backend mantiene la lógica privada con webhook/sideband/server-side controls.
- Encaja con la V2 shadow añadida: el backend ya expone configuración de sesión y tools sin tocar V1.

Flujo propuesto:

1. Twilio recibe la llamada en un número de prueba separado.
2. Twilio enruta la llamada a un SIP trunk apuntando a OpenAI.
3. OpenAI emite `realtime.call.incoming` al webhook V2 del backend.
4. El backend acepta la llamada con instrucciones, modelo, voz y tools.
5. El backend abre sideband WebSocket contra la misma sesión para monitorizar eventos y responder tool calls.
6. Las tools llaman a `booking_service`, `calendar_service`, `supabase_repo` y guards actuales.

Endpoint webhook V2:

- `POST /webhook/voice/openai-realtime-v2`

Health:

- `GET /__health/openai-realtime-v2`

Endpoint auxiliar local/test:

- `POST /webhook/voice/openai-realtime-v2/tools/{tool_name}`

## Opción A: SIP

Configuración futura:

- Crear webhook OpenAI del proyecto apuntando a:
  - `https://<backend-publico>/webhook/voice/openai-realtime-v2`
- Configurar SIP trunk de Twilio hacia:
  - `sip:$OPENAI_PROJECT_ID@sip.api.openai.com;transport=tls`
- En el webhook `realtime.call.incoming`, aceptar la llamada con:
  - `model`
  - `voice`
  - `instructions`
  - `tools`

Estado actual en repo:

- Implementado el webhook shadow que devuelve la configuración de sesión.
- No se llama todavía al endpoint OpenAI `accept`.
- No se cambia Twilio ni Cloud Run.

Riesgos:

- Configuración SIP/Twilio/OpenAI delicada.
- Verificación de firma de webhook pendiente para prueba real.
- Hay que validar latencia, interrupciones y coste con llamadas reales controladas.

## Opción B: WebSocket Server-to-Server

OpenAI documenta WebSocket como conexión server-to-server directa del backend a Realtime. Es útil cuando el backend controla el cliente de audio.

En telefonía, esta opción exigiría:

- Mantener una conexión WebSocket con OpenAI Realtime.
- Recibir audio desde Twilio u otro puente.
- Convertir/reenviar chunks de audio en el formato esperado.
- Gestionar eventos de audio, turnos, interrupciones y backpressure.

Ventaja:

- Más control técnico del audio y eventos.

Riesgo principal en este repo:

- Más cableado propio de audio, justo lo que la V1 ya evita con ConversationRelay.
- Mayor riesgo de romper latencia de voz si se meten llamadas LLM/red en el primer turno.

Conclusión:

- Viable para laboratorio técnico, pero menos recomendable para primera prueba real telefónica.
- Mantener como alternativa si SIP bloquea por configuración externa.

## Diferencias Con V1

- V1 conserva endpoints y lógica actuales:
  - `/webhook/voice/conversationrelay`
  - `/webhook/voice/conversationrelay/ws`
  - `/webhook/voice/agent`
  - `/webhook/whatsapp`
- V2 añade endpoints nuevos, router nuevo y servicios nuevos.
- V2 usa prompt Realtime con tools, no el estado conversacional de ConversationRelay.
- V2 reutiliza el motor común de negocio.

## Variables Necesarias

```env
OPENAI_API_KEY=
OPENAI_REALTIME_ENABLED=false
OPENAI_REALTIME_MODEL=gpt-realtime-2
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_INSTRUCTIONS_VERSION=fisiogreat-v2
OPENAI_REALTIME_TRANSPORT=sip
OPENAI_REALTIME_WEBHOOK_SECRET=
OPENAI_REALTIME_LOG_LEVEL=info
OPENAI_REALTIME_SHADOW_MODE=true
OPENAI_REALTIME_WRITE_ENABLED=false
```

El modelo queda parametrizado. Si `gpt-realtime-2` no está disponible en el entorno, se puede cambiar por otro modelo Realtime soportado sin tocar código.

## Shadow Mode

Por defecto:

- `OPENAI_REALTIME_SHADOW_MODE=true`
- `OPENAI_REALTIME_WRITE_ENABLED=false`

Con write disabled:

- `book_appointment` devuelve una cita simulada.
- `reschedule_appointment` devuelve una reprogramación simulada.
- `cancel_appointment` devuelve una cancelación simulada.
- No se escribe en Calendar.
- No se escribe en Supabase.

Con write enabled:

- Se usa `booking_service.confirm_slot`.
- Se usa `booking_service.reschedule_appointment`.
- Se usa `booking_service.cancel_appointment`.
- Calendar se actualiza antes que Supabase según contrato actual.

## Tools V2

- `get_services`
- `collect_consultation_reason`
- `get_available_slots`
- `book_appointment`
- `list_future_appointments`
- `reschedule_appointment`
- `cancel_appointment`
- `save_contact`
- `emergency_protocol`

## Cómo Probar Sin Afectar V1

Desde `api/`:

```powershell
$env:USE_REAL_CALENDAR="false"
$env:USE_REAL_SUPABASE="false"
$env:USE_REAL_TWILIO="false"
$env:USE_REAL_ELEVENLABS="false"
$env:OPENAI_REALTIME_ENABLED="false"
$env:OPENAI_REALTIME_SHADOW_MODE="true"
$env:OPENAI_REALTIME_WRITE_ENABLED="false"
python -m pytest -q
```

Health:

```powershell
python -c "from fastapi.testclient import TestClient; from app.main import app; print(TestClient(app).get('/__health/openai-realtime-v2').json())"
```

Tool local:

```powershell
python -c "from fastapi.testclient import TestClient; from app.main import app; print(TestClient(app).post('/webhook/voice/openai-realtime-v2/tools/get_services', json={}).json())"
```

## Pendiente Para Prueba Real

- Configurar webhook OpenAI con firma y secreto.
- Implementar aceptación real de llamada SIP solo cuando `OPENAI_REALTIME_ENABLED=true`.
- Abrir sideband WebSocket para responder tool calls reales.
- Mapear eventos de tool call Realtime al dispatcher local.
- Crear número o trunk de prueba separado.
- Medir latencia de primer turno, interrupciones y coste.
- Revisar logs para no exponer PII ni secretos.

## Deploy

No desplegar en esta fase.

Si en el futuro se aprueba despliegue, el destino correcto documentado es:

```bash
gcloud run deploy fisiogreat-demo \
  --source . \
  --region=europe-west1 \
  --platform managed \
  --service-account="fisiogreat-demo@ultra-sunset-494816-r3.iam.gserviceaccount.com" \
  --min-instances=1 \
  --timeout=3600 \
  --no-invoker-iam-check
```

Ejecutar desde `api/` y solo tras revisión manual.

## Checklist De Validación

- Health V2 responde OK.
- Endpoints V1 siguen registrados.
- `.env.example` contiene flags V2 con shadow y write disabled.
- Tools no escriben en shadow.
- Urgencias bloquean booking.
- Jueves explícito no genera martes/miércoles.
- Contacto voz exige teléfono o email.
- Email dictado se normaliza.
- Cancelación con varias citas lista opciones.
- Reprogramación actualiza sin duplicar.
- Pytest completo pasa.
- Smokes V1 pasan.
- No se ha hecho deploy.
- No se han cambiado variables reales.
