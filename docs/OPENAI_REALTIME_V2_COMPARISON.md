# OpenAI Realtime V2 Comparison

## Resumen

La V1 actual se mantiene como implementación estable:

- Twilio ConversationRelay.
- ElevenLabs como voz.
- Lógica de turnos propia.
- Estado propio en backend.
- Integración ya probada con Calendar, Supabase, WhatsApp y smokes.

La V2 se añade como experimento paralelo:

- OpenAI Realtime como modelo de voz integrado.
- Tools conectadas al motor actual.
- Transporte recomendado para prueba real: SIP.
- Modo shadow por defecto.
- Sin cambios de producción.

## Tabla Comparativa

| Criterio | V1 ConversationRelay | V2 OpenAI Realtime |
|---|---|---|
| Latencia | Ya controlada en el flujo actual; protegida evitando red/LLM en primer turno. | Esperada baja por speech-to-speech nativo, pendiente medir en llamada real. |
| Naturalidad | Depende de ConversationRelay + ElevenLabs + turnos propios. | Voz y conversación integradas en Realtime; potencialmente más natural. |
| Control | Alto control determinista del estado y copy. | Menos lógica conversacional propia, más dependencia del prompt y tool calls. |
| Debug | Logs y tests actuales cubren muchos casos. | Requiere trazas de eventos Realtime, sideband y tool calls. |
| Coste | Coste Twilio + ElevenLabs + backend. | Coste Realtime pendiente de medir por tokens/audio y duración real. |
| Telefonía | Twilio ya integrado. | SIP recomendado con Twilio como trunk hacia OpenAI. WebSocket exige puente de audio. |
| Tools | Lógica embebida en handlers y servicios comunes. | Tools explícitas: servicios, slots, booking, contacto, urgencias, cambio y cancelación. |
| Mantenimiento | Más lógica propia de turnos, pero conocida y testeada. | Menos estado conversacional propio, pero nuevo stack Realtime/SIP. |
| Riesgo | Bajo si no se toca; bugs conocidos ya cubiertos por tests. | Medio: SIP, firma webhook, sideband, coste y comportamiento real pendientes. |
| Estado actual | Producción/demo estable, no modificada. | Implementación shadow aislada, testeable sin llamadas reales. |

## V1 Actual

Fortalezas:

- Endpoints existentes intactos.
- WhatsApp funcional.
- Calendar y Supabase integrados.
- Booking, cambio y cancelación con orden correcto Calendar -> Supabase.
- Urgencias derivan a 112 y no agendan.
- Nombre, motivo de consulta y contacto en voz cubiertos.
- Tests y smokes ya protegen regresiones.

Coste técnico:

- Estado conversacional propio más extenso.
- La naturalidad depende de turnos y copy.
- Hay más puntos donde mantener ramas de diálogo.

## V2 OpenAI Realtime

Fortalezas esperadas:

- Modelo de voz integrado.
- Mejor manejo natural de interrupciones, pendiente medir.
- Tools explícitas y separadas.
- Prompt específico para FisioGreat V2.
- Shadow mode permite comparar sin alterar V1.

Riesgos:

- Vendor lock-in mayor.
- Coste real pendiente de medir.
- SIP requiere coordinación entre Twilio, OpenAI y backend.
- Sideband WebSocket y webhook signing pendientes para llamada real.
- Hay que validar que el modelo no salta pasos críticos.

## Estado Implementado

Código nuevo:

- `api/app/services/openai_realtime/`
- `api/app/routers/openai_realtime.py`

Endpoints nuevos:

- `GET /__health/openai-realtime-v2`
- `POST /webhook/voice/openai-realtime-v2`
- `POST /webhook/voice/openai-realtime-v2/tools/{tool_name}`

Flags:

- `OPENAI_REALTIME_SHADOW_MODE=true`
- `OPENAI_REALTIME_WRITE_ENABLED=false`

## Qué Queda Por Validar

- Llamada SIP real con número de prueba separado.
- Firma de webhooks OpenAI.
- Accept call real controlado por flag.
- Sideband WebSocket para tool calls reales.
- Métricas de latencia por turno.
- Coste por llamada.
- Calidad de interrupciones.
- Robustez con fechas ambiguas.
- Comparativa con llamadas grabadas o transcripciones anonimizadas.
