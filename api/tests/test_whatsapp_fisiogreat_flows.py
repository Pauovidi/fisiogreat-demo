from test_demo_flows import post_whatsapp, reset_state
from app.services.supabase_repo import STORE


def test_whatsapp_fisiogreat_create_flow():
    user = "+34600000003"
    reset_state(user)

    post_whatsapp(user, "quiero una cita")
    post_whatsapp(user, "valoracion inicial")
    post_whatsapp(user, "jueves")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" in response.text.lower()
    assert len(STORE.appointments) == 1
