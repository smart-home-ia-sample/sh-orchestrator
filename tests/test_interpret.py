import asyncio
import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from app.graph.interpret import (  # noqa: E402
    DEVICE_VERBS,
    available_verbs,
    build_system_prompt,
    interpret_command,
)


def test_turn_off_light():
    result = asyncio.run(interpret_command("Apague as luzes da sala."))

    assert result.intent == "turn_off_light"
    assert result.device_id == "living_room_light"


def test_bedtime():
    result = asyncio.run(interpret_command("Estou indo dormir."))

    assert result.intent == "bedtime"


def test_leave_home():
    result = asyncio.run(interpret_command("Vou sair de casa."))

    assert result.intent == "leave_home"


def test_energy_status():
    result = asyncio.run(interpret_command("Como está o consumo de energia?"))

    assert result.intent == "energy_status"


def test_home_status():
    result = asyncio.run(interpret_command("O que está acontecendo na casa?"))

    assert result.intent == "home_status"


def test_unrecognized_command():
    result = asyncio.run(interpret_command("Qual é a capital da França?"))

    assert result.intent == "unknown"


def test_bare_greeting_is_chitchat():
    for greeting in ("Olá!", "Oi, tudo bem?", "Bom dia", "e aí"):
        assert asyncio.run(interpret_command(greeting)).intent == "chitchat", greeting


def test_greeting_prefix_does_not_shadow_a_real_command():
    result = asyncio.run(interpret_command("Oi, apaga a luz da sala."))

    assert result.intent == "turn_off_light"
    assert result.device_id == "living_room_light"


def test_non_greeting_gibberish_stays_unknown():
    assert asyncio.run(interpret_command("isso não quer dizer nada")).intent == "unknown"


def test_device_id_resolved_from_live_topology_slugs():
    topology = {
        "devices": [{"id": "sala_luz_teto", "room": "living_room", "type": "light"}],
        "rooms": [],
    }
    result = asyncio.run(interpret_command("Apague a luz da sala.", topology=topology))

    assert result.intent == "turn_off_light"
    assert result.device_id == "sala_luz_teto"


def test_turn_off_light_kitchen():
    result = asyncio.run(interpret_command("Apague a luz da cozinha."))

    assert result.intent == "turn_off_light"
    assert result.device_id == "kitchen_light"


def test_close_curtain():
    result = asyncio.run(interpret_command("Fecha a cortina da sala."))

    assert result.intent == "device_control"
    assert result.capability == "close"
    assert result.device_id == "living_room_curtain"


def test_open_curtain():
    result = asyncio.run(interpret_command("Abre a cortina do quarto."))

    assert result.intent == "device_control"
    assert result.capability == "open"
    assert result.device_id == "bedroom_curtain"


def test_turn_off_ac():
    result = asyncio.run(interpret_command("Desligue o ar-condicionado do quarto."))

    assert result.intent == "device_control"
    assert result.capability == "turn_off"
    assert result.device_id == "bedroom_ac"


def test_set_temperature_explicit():
    result = asyncio.run(interpret_command("Ajuste a temperatura para 22 graus no quarto."))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.device_id == "bedroom_ac"
    assert result.temperature == 22.0


def test_set_temperature_implicit_from_discomfort():
    result = asyncio.run(interpret_command("Está muito quente no quarto."))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.device_id == "bedroom_ac"
    assert result.temperature is not None


def test_lock_door():
    result = asyncio.run(interpret_command("Tranca a porta da frente."))

    assert result.intent == "device_control"
    assert result.capability == "lock"
    assert result.device_id == "front_door"


def test_arm_alarm():
    result = asyncio.run(interpret_command("Arma o alarme."))

    assert result.intent == "device_control"
    assert result.capability == "arm"
    assert result.device_id == "alarm"


def test_turn_off_appliance():
    result = asyncio.run(interpret_command("Desliga a cafeteira."))

    assert result.intent == "device_control"
    assert result.capability == "turn_off"
    assert result.device_id == "kitchen_coffee_maker"


def test_turn_on_tv():
    result = asyncio.run(interpret_command("Liga a TV."))

    assert result.intent == "device_control"
    assert result.capability == "turn_on"
    assert result.device_id == "living_room_tv"


def test_cold_complaint_raises_temperature_relative_to_current():
    result = asyncio.run(interpret_command("Ainda está frio.", context={"on": True, "temperature": 24.0}))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 26.0


def test_hot_complaint_lowers_temperature_relative_to_current():
    result = asyncio.run(interpret_command("Está calor.", context={"on": True, "temperature": 20.0}))

    # mock only matches "quente", not "calor" — this exercises the fallback-to-default path.
    assert result.intent == "unknown"


def test_hot_complaint_lowers_temperature_relative_to_current_mock_keyword():
    result = asyncio.run(interpret_command("Ainda está quente.", context={"on": True, "temperature": 20.0}))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 18.0


def test_decrease_more_uses_absolute_value_not_delta():
    result = asyncio.run(interpret_command("Diminua mais.", context={"on": True, "temperature": 22.0}))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 20.0  # never a raw delta like -2.0


def test_increase_more_uses_absolute_value_not_delta():
    result = asyncio.run(interpret_command("Aumenta mais.", context={"on": True, "temperature": 22.0}))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 24.0


def test_bare_number_without_graus_is_treated_as_explicit_target():
    result = asyncio.run(interpret_command("Melhor 26 no ar-condicionado.", context={"on": True, "temperature": 22.0}))

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 26.0


def test_out_of_range_temperature_is_clamped():
    result = asyncio.run(interpret_command("Diminua mais.", context={"on": True, "temperature": 16.5}))

    assert result.temperature == 16.0  # clamped to MIN_AC_TEMPERATURE, never negative


def test_bare_ambiguous_number_resolved_via_conversation_history():
    # "Melhor 26" alone has no AC/temperature keyword — only the prior message in the
    # same conversation establishes that this is still about the bedroom AC.
    result = asyncio.run(
        interpret_command(
            "Melhor 26.",
            context={"on": True, "temperature": 22.0},
            history=["Liga o ar-condicionado.", "Está muito frio."],
        )
    )

    assert result.intent == "device_control"
    assert result.capability == "set_temperature"
    assert result.temperature == 26.0


def test_bare_ambiguous_number_without_history_is_unknown():
    # Same message as above, but with no conversation history — nothing ties "26" to
    # the AC, so it correctly stays unresolved instead of guessing.
    result = asyncio.run(interpret_command("Melhor 26.", context={"on": True, "temperature": 22.0}))

    assert result.intent == "unknown"


# ---- prompt bits derived from the live topology ------------------------------


def test_available_verbs_falls_back_to_all_when_topology_is_empty():
    assert available_verbs(None) == list(DEVICE_VERBS)
    assert available_verbs({"devices": []}) == list(DEVICE_VERBS)


def test_available_verbs_is_the_intersection_with_installed_actions():
    topology = {
        "devices": [
            {"id": "l1", "room": "living_room", "type": "light", "actions": ["turn_on", "turn_off"]},
            {"id": "d1", "room": "entrance", "type": "door", "actions": ["lock", "unlock"]},
        ]
    }
    # no AC installed -> set_temperature drops out of the verb menu
    assert available_verbs(topology) == ["turn_on", "turn_off", "lock", "unlock"]


def test_system_prompt_renders_the_dynamic_verb_menu_and_ac_range():
    topology = {
        "devices": [
            {"id": "l1", "room": "living_room", "type": "light", "actions": ["turn_on", "turn_off"]},
            {
                "id": "ac1",
                "room": "bedroom",
                "type": "ac",
                "actions": ["turn_on", "turn_off", "set_temperature"],
                "params": {"set_temperature": {"min": 17, "max": 28}},
            },
            {"id": "a1", "room": "home", "type": "alarm", "actions": ["arm", "disarm"]},
        ]
    }
    prompt = build_system_prompt(topology)

    assert "<<VERBS>>" not in prompt and "<<AC_MIN>>" not in prompt and "<<ALARM_ID>>" not in prompt
    assert "turn_on, turn_off, set_temperature, arm, disarm" in prompt
    assert "17°C and 28°C" in prompt
    assert 'device_id="a1"' in prompt


def test_system_prompt_uses_hard_defaults_without_a_topology():
    prompt = build_system_prompt(None)

    assert "16°C and 30°C" in prompt
    assert 'device_id="alarm"' in prompt
    assert ", ".join(DEVICE_VERBS) in prompt


def test_temperature_clamp_respects_the_installed_ac_bounds():
    topology = {
        "devices": [
            {
                "id": "bedroom_ac",
                "room": "bedroom",
                "type": "ac",
                "actions": ["set_temperature"],
                "params": {"set_temperature": {"min": 19, "max": 25}},
            }
        ]
    }
    result = asyncio.run(
        interpret_command(
            "Diminua mais.", context={"on": True, "temperature": 20.0}, topology=topology
        )
    )

    assert result.temperature == 19.0  # 20 - 2 = 18, clamped up to the announced min
