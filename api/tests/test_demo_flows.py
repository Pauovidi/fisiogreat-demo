import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.main import app
from app.utils.mini_context import CTX
from app.utils.voice_copy import VOICE_COPY


client = TestClient(app)


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


class WhatsAppDemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = "+34600000001"
        CTX.clear(self.user)

    def post_whatsapp(self, body: str):
        response = client.post(
            "/webhook/whatsapp",
            data={"From": f"whatsapp:{self.user}", "Body": body},
        )
        self.assertEqual(response.status_code, 200)
        parse_xml(response.text)
        return response

    def test_whatsapp_hello_is_human_and_not_hard_menu(self):
        response = self.post_whatsapp("Hola")
        lower = response.text.lower()

        self.assertTrue("hola" in lower or "buenas" in lower)
        self.assertTrue("servicio" in lower or "hacerte" in lower)
        self.assertNotIn("cambiar o cancelar", lower)
        self.assertEqual(CTX.get_stage(self.user), "awaiting_service")

    def test_whatsapp_booking_progression_keeps_state(self):
        first = self.post_whatsapp("quiero reservar")
        first_lower = first.text.lower()
        self.assertTrue("servicio" in first_lower or "hacerte" in first_lower)
        self.assertEqual(CTX.get_stage(self.user), "awaiting_service")

        second = self.post_whatsapp("peinado")
        self.assertIn("día te viene bien", second.text.lower())
        self.assertEqual(CTX.get_stage(self.user), "awaiting_date")

        third = self.post_whatsapp("jueves")
        lower = third.text.lower()
        self.assertIn("te puedo ofrecer", lower)
        self.assertIn("1.", lower)
        self.assertEqual(CTX.get_stage(self.user), "offering_slots")

    def test_whatsapp_hours_reengages_current_step(self):
        self.post_whatsapp("quiero reservar")
        self.post_whatsapp("peinado")

        response = self.post_whatsapp("¿Cuál es el horario?")
        lower = response.text.lower()

        self.assertIn("nuestro horario es", lower)
        self.assertIn("día te viene bien", lower)
        self.assertEqual(CTX.get_stage(self.user), "awaiting_date")


class VoiceDemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.call_sid = "call-seq-1"
        CTX.clear(self.call_sid)

    def post_voice(self, **data):
        payload = {"CallSid": self.call_sid}
        payload.update(data)
        response = client.post("/webhook/voice/agent", data=payload)
        self.assertEqual(response.status_code, 200)
        parse_xml(response.text)
        return response

    def assert_no_voice_greeting(self, text: str):
        lower = text.lower()
        self.assertNotIn("soy pelu agent", lower)

    def test_voice_demo_sequence(self):
        start = self.post_voice()
        self.assertIn("asistente de la peluqueria romualdo", start.text.lower())
        self.assertIn("en que puedo ayudarte", start.text.lower())
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

        hello = self.post_voice(SpeechResult="hola")
        self.assertIn("que te apetece hacerte", hello.text.lower())
        self.assert_no_voice_greeting(hello.text)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

        service = self.post_voice(SpeechResult="peinado")
        self.assertIn("que dia te va bien", service.text.lower())
        self.assert_no_voice_greeting(service.text)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

        day = self.post_voice(SpeechResult="jueves por la tarde")
        day_lower = day.text.lower()
        self.assertIn("tengo", day_lower)
        self.assertIn("de la tarde", day_lower)
        self.assertNotIn("17:00", day.text)
        self.assert_no_voice_greeting(day.text)
        self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

        pick = self.post_voice(SpeechResult="segunda")
        self.assertIn("perfecto", pick.text.lower())
        self.assert_no_voice_greeting(pick.text)
        self.assertNotIn("17:00", pick.text)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

        faq = self.post_voice(SpeechResult="que horario teneis")
        lower = faq.text.lower()
        self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
        self.assertIn("te esperamos", lower)
        self.assertNotIn("que te apetece hacerte", lower)

    def test_voice_detects_quiero_corte(self):
        self.post_voice()
        service = self.post_voice(SpeechResult="quiero corte")
        self.assertIn("para corte", service.text.lower())
        self.assertIn("que dia te va bien", service.text.lower())
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_voice_detects_sabado(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        day = self.post_voice(SpeechResult="sábado")
        lower = day.text.lower()
        self.assertIn("tengo", lower)
        self.assertIn("di primera o segunda", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_voice_handles_services_question(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="que servicios teneis")
        lower = response.text.lower()
        self.assertIn("hacemos corte, color, mechas y peinado", lower)
        self.assertIn("que te apetece", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_booking_without_service_asks_service_not_day(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="quiero una cita")
        lower = response.text.lower()
        self.assertIn("vale, claro", lower)
        self.assertIn("que te quieres hacer", lower)
        self.assertNotIn("que dia", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_handles_haceis_mechas_as_services_faq(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="haceis mechas")
        lower = response.text.lower()
        self.assertIn("hacemos corte", lower)
        self.assertIn("que te apetece", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_handles_pricing_question(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="cuanto cuesta")
        lower = response.text.lower()
        self.assertIn("depende del servicio", lower)
        self.assertIn("que te quieres hacer", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_handles_location_question(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="donde estais")
        lower = response.text.lower()
        self.assertIn("estamos en", lower)
        self.assertIn("whatsapp", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_handles_no_se(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        response = self.post_voice(SpeechResult="no se")
        lower = response.text.lower()
        self.assertIn("no pasa nada", lower)
        self.assertIn("dime un dia", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_voice_handles_human_handoff(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="quiero hablar con una persona")
        lower = response.text.lower()
        self.assertIn("telefono del salon", lower)
        self.assertIn("aviso para que te llamen", lower)
        self.assertIn("que te apetece", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_hours_mid_flow_reengages_date(self):
        self.post_voice()
        self.post_voice(SpeechResult="peinado")
        response = self.post_voice(SpeechResult="que horario teneis")
        lower = response.text.lower()
        self.assertIn("abrimos de lunes a viernes", lower)
        self.assertIn("que dia te va bien", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_voice_services_mid_flow_reengages_date(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        response = self.post_voice(SpeechResult="que servicios haceis")
        lower = response.text.lower()
        self.assertIn("hacemos corte", lower)
        self.assertIn("para corte", lower)
        self.assertIn("que dia te va bien", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_voice_captures_afternoon_preference_before_day(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        response = self.post_voice(SpeechResult="por la tarde")
        lower = response.text.lower()
        self.assertIn("mejor por la tarde", lower)
        self.assertIn("que dia te va bien", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_date")

    def test_voice_changes_time_preference_mid_slot_offer(self):
        self.post_voice()
        self.post_voice(SpeechResult="peinado")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="por la tarde")
        lower = response.text.lower()
        self.assertIn("tengo", lower)
        self.assertIn("de la tarde", lower)
        self.assertNotIn("17:00", response.text)
        self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_voice_handles_mas_tarde_without_resetting_flow(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="mas tarde")
        lower = response.text.lower()
        self.assertIn("tengo", lower)
        self.assertIn("de la tarde", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_voice_handles_slot_rejection_without_resetting_flow(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="no puedo a esa hora")
        lower = response.text.lower()
        self.assertIn("prefieres antes, mas tarde u otro dia", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "offering_slots")

    def test_voice_copy_reads_hours_naturally(self):
        spoken = VOICE_COPY._slot_to_voice_text("jueves 24/04 a las 17:00 CEST", include_day=False, include_prefix=False)
        self.assertEqual(spoken, "las cinco de la tarde")
        self.assertNotIn("ces", spoken.lower())

    def test_voice_confirm_booking_has_no_double_las(self):
        confirmation = VOICE_COPY.confirm_booking("jueves 24/04 a las 17:00")
        lower = confirmation.lower()
        self.assertNotIn("las las", lower)
        self.assertIn("te apunto", lower)
        self.assertIn("a las cinco de la tarde", lower)

    def test_voice_thanks_after_confirmation_is_human(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        self.post_voice(SpeechResult="segunda")
        response = self.post_voice(SpeechResult="gracias")
        lower = response.text.lower()
        self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
        self.assertIn("te esperamos", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_acknowledgement_after_confirmation_is_human(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        self.post_voice(SpeechResult="segunda")
        response = self.post_voice(SpeechResult="perfecto")
        lower = response.text.lower()
        self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
        self.assertIn("te esperamos", lower)
        self.assertNotIn("que te apetece hacerte", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_ok_after_confirmation_is_human(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        self.post_voice(SpeechResult="segunda")
        response = self.post_voice(SpeechResult="ok")
        lower = response.text.lower()
        self.assertIn("gracias a ti por confiar en la peluqueria romualdo", lower)
        self.assertIn("te esperamos", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_restart_booking_after_completed(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        self.post_voice(SpeechResult="segunda")
        response = self.post_voice(SpeechResult="quiero otra cita")
        lower = response.text.lower()
        self.assertIn("que te quieres hacer", lower)
        self.assertNotIn("que te apetece hacerte", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_slot_selection_with_primera_confirms_first_slot(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="primera")
        lower = response.text.lower()
        self.assertIn("perfecto", lower)
        self.assertIn("te apunto", lower)
        self.assertNotIn("di primera o segunda", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_slot_selection_with_la_primera_confirms_first_slot(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="la primera")
        lower = response.text.lower()
        self.assertIn("perfecto", lower)
        self.assertIn("te apunto", lower)
        self.assertNotIn("di primera o segunda", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_slot_selection_with_segunda_confirms_second_slot(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="segunda")
        lower = response.text.lower()
        self.assertIn("perfecto", lower)
        self.assertIn("te apunto", lower)
        self.assertNotIn("di primera o segunda", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_slot_selection_with_la_segunda_confirms_second_slot(self):
        self.post_voice()
        self.post_voice(SpeechResult="corte")
        self.post_voice(SpeechResult="jueves")
        response = self.post_voice(SpeechResult="la segunda")
        lower = response.text.lower()
        self.assertIn("perfecto", lower)
        self.assertIn("te apunto", lower)
        self.assertNotIn("di primera o segunda", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "completed")

    def test_voice_fallback_with_weird_input(self):
        self.post_voice()
        response = self.post_voice(SpeechResult="blablabla marciano")
        lower = response.text.lower()
        self.assertIn("quieres corte, color, mechas o peinado", lower)
        self.assertEqual(CTX.get_stage(self.call_sid), "awaiting_service")

    def test_voice_tts_head_returns_audio_mpeg(self):
        response = client.head("/webhook/voice/tts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type"), "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
