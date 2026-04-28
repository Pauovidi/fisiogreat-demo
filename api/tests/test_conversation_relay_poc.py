import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.utils.mini_context import CTX


client = TestClient(app)


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


class ConversationRelayTwimlTests(unittest.TestCase):
    def test_conversationrelay_twiml_exposes_connect_and_websocket(self):
        response = client.post("/webhook/voice/conversationrelay")
        self.assertEqual(response.status_code, 200)

        root = parse_xml(response.text)
        connect = root.find("Connect")
        self.assertIsNotNone(connect)

        relay = connect.find("ConversationRelay") if connect is not None else None
        self.assertIsNotNone(relay)
        expected_ws_url = (
            settings.PUBLIC_BASE_URL.rstrip("/")
            .replace("https://", "wss://", 1)
            .replace("http://", "ws://", 1)
            + "/webhook/voice/conversationrelay/ws"
        )
        self.assertEqual(
            relay.attrib.get("url"),
            expected_ws_url,
        )
        self.assertIn("504875836299", relay.attrib.get("url", ""))
        self.assertEqual(relay.attrib.get("ttsProvider"), "ElevenLabs")
        self.assertEqual(relay.attrib.get("ttsLanguage"), "es-ES")
        self.assertEqual(relay.attrib.get("transcriptionLanguage"), "es-ES")
        self.assertNotIn("transcriptionProvider", relay.attrib)
        self.assertNotIn("speechModel", relay.attrib)

    def test_conversationrelay_twiml_injects_voice_id_when_configured(self):
        previous = settings.CONVERSATIONRELAY_TTS_VOICE
        settings.CONVERSATIONRELAY_TTS_VOICE = "voice_abc123realid"
        try:
            response = client.post("/webhook/voice/conversationrelay")
            root = parse_xml(response.text)
            relay = root.find("Connect/ConversationRelay")
            self.assertIsNotNone(relay)
            self.assertEqual(relay.attrib.get("voice"), "voice_abc123realid")
        finally:
            settings.CONVERSATIONRELAY_TTS_VOICE = previous

    def test_conversationrelay_action_falls_back_to_agent_on_failed_session(self):
        response = client.post(
            "/webhook/voice/conversationrelay/action",
            data={
                "CallSid": "CA-failed-1",
                "SessionStatus": "failed",
                "ErrorCode": "39001",
                "ErrorMessage": "Network connection to WebSocket server failed.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("/webhook/voice/agent", response.text)


class ConversationRelayWebSocketTests(unittest.TestCase):
    def setUp(self):
        self.call_sid = "CA-conversationrelay-1"
        CTX.clear(self.call_sid)

    def tearDown(self):
        CTX.clear(self.call_sid)

    def _setup_ws(self, websocket, session_id: str):
        websocket.send_json({"type": "setup", "sessionId": session_id, "callSid": self.call_sid})
        opening = websocket.receive_json()
        self.assertEqual(opening["type"], "text")
        self.assertIn("asistente de la peluqueria romualdo", opening["token"].lower())
        self.assertIn("en que puedo ayudarte", opening["token"].lower())
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_conversationrelay_booking_sequence(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-1")

            websocket.send_json({"type": "prompt", "voicePrompt": "quiero corte", "last": True})
            ask_date = websocket.receive_json()
            self.assertIn("que dia te va bien", ask_date["token"].lower())
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            offer = websocket.receive_json()
            lower = offer["token"].lower()
            self.assertIn("tengo", lower)
            self.assertIn("las diez", lower)
            self.assertIn("las cinco de la tarde", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            confirm = websocket.receive_json()
            self.assertIn("perfecto", confirm["token"].lower())
            self.assertIn("te apunto", confirm["token"].lower())
            self.assertIn("cinco de la tarde", confirm["token"].lower())
            self.assertNotIn("17:00", confirm["token"])
            self.assertNotIn("las las", confirm["token"].lower())
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_slot_selection_with_primera_confirms_first_slot(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-slot-1")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "primera", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("perfecto", lower)
            self.assertIn("te apunto", lower)
            self.assertNotIn("di primera o segunda", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_slot_selection_with_la_primera_confirms_first_slot(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-slot-2")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "la primera", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("perfecto", lower)
            self.assertIn("te apunto", lower)
            self.assertNotIn("di primera o segunda", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_slot_selection_with_segunda_confirms_second_slot(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-slot-3")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("perfecto", lower)
            self.assertIn("te apunto", lower)
            self.assertNotIn("di primera o segunda", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_slot_selection_with_la_segunda_confirms_second_slot(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-slot-4")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "la segunda", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("perfecto", lower)
            self.assertIn("te apunto", lower)
            self.assertNotIn("di primera o segunda", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_booking_without_service_asks_service(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-booking-1")
            websocket.send_json({"type": "prompt", "voicePrompt": "quiero una cita", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("vale, claro", lower)
            self.assertIn("que te quieres hacer", lower)
            self.assertNotIn("que dia", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_conversationrelay_hours_mid_flow_reengages(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-2")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "que horario teneis", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("abrimos de lunes a viernes", lower)
            self.assertIn("que dia te va bien", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_conversationrelay_services_mid_flow_reengages(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-3")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "que servicios teneis", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("hacemos corte, color, mechas y peinado", lower)
            self.assertIn("para corte, que dia te va bien", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_conversationrelay_thanks_after_confirmation_is_human(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-thanks-1")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "gracias", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
            self.assertIn("te esperamos", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_acknowledgement_after_confirmation_is_human(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-ack-1")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "perfecto", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
            self.assertIn("te esperamos", lower)
            self.assertNotIn("que te apetece hacerte", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_ok_after_confirmation_is_human(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-ack-2")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "ok", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
            self.assertIn("te esperamos", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_conversationrelay_restart_booking_after_completed(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-restart-1")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "segunda", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "quiero otra cita", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("que te quieres hacer", lower)
            self.assertNotIn("que te apetece hacerte", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_conversationrelay_handles_pricing_question(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-3b")
            websocket.send_json({"type": "prompt", "voicePrompt": "cuanto cuesta", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("depende del servicio", lower)
            self.assertIn("que te apetece", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_conversationrelay_no_se_guides_pending_step(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-4")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "no se", "last": True})
            response = websocket.receive_json()
            self.assertIn("no pasa nada", response["token"].lower())
            self.assertIn("dime un dia", response["token"].lower())
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_conversationrelay_handoff_mid_flow_reengages(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-5")
            websocket.send_json({"type": "prompt", "voicePrompt": "corte", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "quiero hablar con una persona", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("telefono del salon", lower)
            self.assertIn("aviso para que te llamen", lower)
            self.assertIn("que dia te va bien", lower)
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_conversationrelay_change_time_mid_offer(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-6")
            websocket.send_json({"type": "prompt", "voicePrompt": "peinado", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "por la tarde", "last": True})
            response = websocket.receive_json()
            lower = response["token"].lower()
            self.assertIn("de la tarde", lower)
            self.assertNotIn("17:00", response["token"])
            self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_conversationrelay_handles_mas_tarde_mid_offer(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-6b")
            websocket.send_json({"type": "prompt", "voicePrompt": "peinado", "last": True})
            websocket.receive_json()
            websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
            websocket.receive_json()

            websocket.send_json({"type": "prompt", "voicePrompt": "mas tarde", "last": True})
            response = websocket.receive_json()
            self.assertIn("de la tarde", response["token"].lower())
            self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_conversationrelay_weird_input_retries_with_guidance(self):
        with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
            self._setup_ws(websocket, "VX-conversationrelay-7")
            websocket.send_json({"type": "prompt", "voicePrompt": "ruido raro", "last": True})
            response = websocket.receive_json()
            self.assertEqual(response["type"], "text")
            self.assertIn("quieres corte, color, mechas o peinado", response["token"].lower())
            self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")


if __name__ == "__main__":
    unittest.main()
