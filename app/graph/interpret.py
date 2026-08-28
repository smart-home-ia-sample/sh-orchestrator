import os
import re
from typing import Literal

from pydantic import BaseModel

Intent = Literal[
    "turn_off_light",
    "bedtime",
    "leave_home",
    "energy_status",
    "home_status",
    "device_control",
    "chitchat",
    "unknown",
]

# The device-control "capability" is now a generic verb (the same verb the Home
# MCP tool and the agent skill are named). It's a plain `str` — not a closed
# Literal — because which verbs a given device accepts is decided at runtime by
# the device's announced capabilities (`home://devices[*].actions`). These are
# just the ones the interpreter knows how to produce.
DEVICE_VERBS = (
    "turn_on",
    "turn_off",
    "set_brightness",
    "set_temperature",
    "open",
    "close",
    "lock",
    "unlock",
    "arm",
    "disarm",
)

ALARM_DEVICE_ID = "alarm"

# Was a closed Literal of the 7 seeded slugs; now any string. The authoritative
# list of valid device ids is the live topology (`home://devices`), injected into
# the prompt by `_topology_text` and re-checked at runtime in the interpret node.
ControllableDevice = str

SYSTEM_PROMPT = """You classify a Portuguese smart-home voice command into exactly one intent.
This is a precise matching task, not creative writing: pick the single tool (capability)
that actually operates on the device type and room the user mentioned, the same way a
human operator would glance at a panel of switches and press the one that matches. Do not
invent devices, capabilities or rooms that aren't in the lists below.
The user may phrase the same request in many different ways (formal, informal, slang,
imperative, question form, with or without "por favor" etc). Focus on the underlying
intent, not the exact wording — be generous in matching paraphrases and synonyms to one
of the supported intents below. A pure greeting or social pleasantry is its own intent,
"chitchat" (see below) — do NOT file that under "unknown". Only use "unknown" when the
message is a real request the house cannot fulfil, or is too vague to act on.

Intents (with example Portuguese phrasings that should all match):
- turn_off_light: user wants a specific room's light turned off.
  Examples: "apague a luz da sala", "desliga a luz da cozinha", "pode apagar a luz do
  quarto?", "luz da sala apagada, por favor", "tira a luz da cozinha", "escurece o quarto".
  Also set device_id (see below).
- bedtime: user is going to sleep / winding down for the night.
  Examples: "vou dormir", "estou indo dormir", "boa noite", "já vou deitar", "hora de
  dormir", "vou cochilar", "prepara a casa pra eu dormir".
- leave_home: user is leaving the house / heading out.
  Examples: "vou sair de casa", "estou saindo", "já estou indo", "vou sair, tranca tudo",
  "saindo agora, ativa a segurança", "vou trabalhar, cuida da casa".
- energy_status: user asks about energy consumption.
  Examples: "como está o consumo de energia?", "quanto estou gastando de energia?",
  "qual o consumo atual?", "estou gastando muita energia?", "energia da casa".
- home_status: user asks what is happening in the physical house / a general status
  check of real devices (security, environment, energy).
  Examples: "o que está acontecendo na casa?", "como está a casa?", "status da casa",
  "me dá um resumo da casa", "tudo bem por aí?", "alguma novidade em casa?".
  IMPORTANT: you have no visibility into any screen, app, dashboard, or UI the user might
  be looking at — you only know about real physical devices (lights, AC, doors, alarm,
  energy). Questions about "essa tela", "esse aplicativo", "essa aba", "esse painel", "o
  que tem aqui" or similar UI-referring phrasing are NOT home_status — you cannot see what
  the user sees on screen, so use "unknown" for those instead of guessing they mean the
  house.
- device_control: user wants to directly control one specific device that isn't covered
  by the intents above — turning a light on, adjusting its brightness, opening/closing a
  curtain, turning the AC on/off or setting its target temperature, locking/unlocking the
  front door, or arming/disarming the alarm.
  This also covers comfort/temperature-adjustment phrasing of any kind — not just literally
  "quente"/"frio" but also "diminua/abaixa/reduz [mais] a temperatura", "aumenta/sobe [mais]
  a temperatura", "está muito baixo"/"está muito alto", or a bare target number in context
  ("melhor 26", "coloca em 26", "que tal 26 graus"). Treat all of these as device_control
  with capability=set_temperature.

  CRITICAL: the `temperature` field is ALWAYS the absolute final reading in Celsius the AC
  should end up at — NEVER a delta/offset, even though you reason about the adjustment as a
  delta internally. If the current AC temperature is given to you below, compute
  new_target = current ± step (step is usually 2°C, or the exact number the user gave) and
  output new_target itself.
  Worked example: current temperature is 22°C.
  - "está muito quente" / "diminua mais" / "abaixa mais" -> temperature = 22 - 2 = 20
    (output 20, NOT -2)
  - "está muito frio" / "aumenta mais" / "sobe mais" -> temperature = 22 + 2 = 24
    (output 24, NOT +2 or 2)
  - "melhor 26" / "coloca em 26" -> the user gave an explicit target, output 26 directly,
    ignoring the current reading.
  Without any current-temperature context, fall back to 20°C for a "too hot"-style request
  or 24°C for a "too cold"-style request. A real AC only makes sense roughly between 16°C
  and 30°C — never output a value outside that range.

  Set capability to exactly one generic verb: turn_on, turn_off, set_brightness,
  set_temperature, open, close, lock, unlock, arm, disarm. The verb is the same for
  every device type — "liga a TV", "liga a cafeteira" and "acende a luz" are all
  turn_on; the device_id says WHICH device.
  Set device_id to the id of the device the user means (see the inventory below if one
  is provided). For arm/disarm use device_id="alarm".
  If the user asks to control a device type or room that doesn't exist, use "unknown".
  Set brightness (0-100) when capability is set_brightness.
  Examples:
  - "fecha a cortina da sala" -> capability=close, device_id=living_room_curtain
  - "abre a cortina do quarto" -> capability=open, device_id=bedroom_curtain
  - "desligue o ar-condicionado do quarto" -> capability=turn_off, device_id=bedroom_ac
  - "liga a cafeteira" -> capability=turn_on, device_id=kitchen_coffee_maker
  - "desliga a TV" -> capability=turn_off, device_id=living_room_tv
  - "ajuste a temperatura para 22 graus no quarto" -> capability=set_temperature,
    device_id=bedroom_ac, temperature=22
  - "está muito quente no quarto" (no current reading given) -> capability=set_temperature,
    device_id=bedroom_ac, temperature=20
  - current is 24°C, user says "diminua mais" -> capability=set_temperature,
    device_id=bedroom_ac, temperature=22 (24 - 2, an absolute value, not "-2")
  - "melhor 26" (mid-conversation about the AC) -> capability=set_temperature,
    device_id=bedroom_ac, temperature=26
  - "tranca a porta da frente" -> capability=lock, device_id=front_door
  - "arma o alarme" -> capability=arm, device_id=alarm
- chitchat: a bare greeting or social opener with nothing actionable in it — "olá",
  "oi", "bom dia", "boa tarde", "e aí", "tudo bem?", "como você está?", "opa". Use
  this (not "unknown", not "home_status") when the user is just saying hi or making
  small talk and is NOT asking the house to do or report anything. If the message
  greets AND asks for something ("oi, apaga a luz"), classify by the request, not as
  chitchat.
- unknown: none of the above apply (be conservative — prefer a real intent when the
  message is close to one of the examples above, even if not an exact match).

For turn_off_light, set device_id to one of: living_room_light, kitchen_light, bedroom_light
(pick based on the room mentioned; default to living_room_light if unclear).
For every intent other than turn_off_light and device_control, device_id/capability/
temperature/brightness must all be null."""


class InterpretedCommand(BaseModel):
    intent: Intent
    device_id: ControllableDevice | None = None
    capability: str | None = None  # a generic verb (see DEVICE_VERBS)
    temperature: float | None = None
    brightness: int | None = None


# A real AC unit only makes sense roughly in this range — clamp here as a safety net
# regardless of what the mock/LLM computed (never trust the interpreter blindly).
MIN_AC_TEMPERATURE = 16.0
MAX_AC_TEMPERATURE = 30.0


def _clamp_ac_temperature(value: float) -> float:
    return max(MIN_AC_TEMPERATURE, min(MAX_AC_TEMPERATURE, value))


def _topology_devices(topology: dict | None) -> list[dict]:
    return list((topology or {}).get("devices") or [])


def resolve_device_id(topology: dict | None, room: str | None, dtype: str, fallback: str) -> str:
    """Live-topology lookup of the device id for a (room, type) pair, with a
    hardcoded fallback for when the MCP is unreachable (empty topology). A plain
    `light` request also matches a `dimmable_light` in the same room."""
    devices = _topology_devices(topology)
    for d in devices:
        if d.get("room") == room and d.get("type") == dtype:
            return d["id"]
    if dtype == "light":
        for d in devices:
            if d.get("room") == room and d.get("type") == "dimmable_light":
                return d["id"]
    return fallback


def _resolve_by_type(topology: dict | None, dtype: str, fallback: str) -> str:
    for d in _topology_devices(topology):
        if d.get("type") == dtype:
            return d["id"]
    return fallback


_GREETING_PHRASES = (
    "ola", "olá", "oi", "oie", "opa", "salve", "hey", "hello", "hi", "hola", "alo", "alô",
    # "boa noite" is deliberately NOT here — this app models it as `bedtime`.
    "bom dia", "boa tarde", "e ai", "e aí", "eai",
    "tudo bem", "tudo bom", "como vai", "como voce esta", "como você está", "beleza",
)

# If a short "greeting" also names an action/device/report, it's a real command
# with a polite prefix ("oi, apaga a luz") — let the normal classifier handle it.
_ACTION_HINT = re.compile(
    r"\b(lig\w*|deslig\w*|apag\w*|acend\w*|abr\w*|fech\w*|tranc\w*|destranc\w*|arm\w*|"
    r"desarm\w*|ajust\w*|coloc\w*|mud\w*|aument\w*|diminu\w*|abaix\w*|sob\w*|reduz\w*|"
    r"status|consumo|energia|gast\w*|dormir|deitar|sair|saindo|"
    r"luz|luzes|cortina|porta|alarme|temperatura|ar.?condicionado|graus)\b"
)


def _is_chitchat(text: str) -> bool:
    """A bare greeting / social opener with nothing actionable in it. Deliberately
    conservative: short, must open with a known greeting, and must not mention any
    action/device/report keyword."""
    stripped = re.sub(r"[^\wçãõáéíóúâêôà\s]", " ", text.lower()).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    if not stripped or len(stripped.split()) > 5:
        return False
    opens_with_greeting = any(
        stripped == p or stripped.startswith(p + " ") for p in _GREETING_PHRASES
    )
    return opens_with_greeting and not _ACTION_HINT.search(stripped)


def _topology_text(topology: dict | None) -> str:
    devices = _topology_devices(topology)
    if not devices:
        return ""
    by_room: dict[str, list[str]] = {}
    for d in devices:
        actions = d.get("actions") or []
        verbs = f" [{', '.join(actions)}]" if actions else ""
        by_room.setdefault(str(d.get("room", "?")), []).append(f"{d['id']} ({d.get('type', '?')}){verbs}")
    lines = "\n".join(f"- {room}: {', '.join(sorted(items))}" for room, items in sorted(by_room.items()))
    return (
        "\n\nDispositivos realmente cadastrados nesta casa agora — use SOMENTE estes ids "
        "no campo device_id. Se o cômodo ou o tipo de aparelho que o usuário pediu não "
        f"aparecer nesta lista, não há o que controlar: responda com intent \"unknown\".\n{lines}"
    )


def _interpret_mock(
    user_message: str,
    context: dict | None = None,
    history: list[str] | None = None,
    topology: dict | None = None,
) -> InterpretedCommand:
    text = user_message.lower()

    # "apaga"/"apague" alone would wrongly grab "apaga a TV" — only treat it as a
    # light command when no other device noun is in play.
    other_device_nouns = ("cortina", "porta", "janela", "alarme", "cafeteira", "tv",
                          "televis", "geladeira", "ar-condicionado", "ar condicionado")
    light_keywords = ("luz", "luzes", "lâmpada", "lampada", "apague", "apagar", "apaga",
                      "escurece", "escurecer")
    if any(keyword in text for keyword in light_keywords) and not any(n in text for n in other_device_nouns):
        if "cozinha" in text:
            room = "kitchen"
        elif "quarto" in text or "dormit" in text:
            room = "bedroom"
        else:
            room = "living_room"
        device_id = resolve_device_id(topology, room, "light", f"{room}_light")
        return InterpretedCommand(intent="turn_off_light", device_id=device_id)

    bedtime_keywords = ("dormir", "boa noite", "deitar", "cochilar", "cochilo")
    if any(keyword in text for keyword in bedtime_keywords):
        return InterpretedCommand(intent="bedtime")

    leave_keywords = ("sair", "saindo", "vou embora", "indo trabalhar", "vou trabalhar")
    if any(keyword in text for keyword in leave_keywords):
        return InterpretedCommand(intent="leave_home")

    energy_keywords = ("consumo", "energia", "gastando", "gasto de luz")
    if any(keyword in text for keyword in energy_keywords):
        return InterpretedCommand(intent="energy_status")

    status_keywords = ("acontecendo", "novidade", "resumo da casa", "tudo bem por a")
    if any(keyword in text for keyword in status_keywords) or re.search(r"\bstatus\b", text):
        return InterpretedCommand(intent="home_status")

    device_control = _interpret_device_control_mock(text, context, history, topology)
    if device_control is not None:
        return device_control

    if _is_chitchat(text):
        return InterpretedCommand(intent="chitchat")

    return InterpretedCommand(intent="unknown")


def _find_room(text: str) -> str | None:
    room_keywords = {"sala": "living_room", "cozinha": "kitchen", "quarto": "bedroom"}
    for keyword, room in room_keywords.items():
        if keyword in text:
            return room
    return None


def _mentions_ac_or_temperature(text: str, decrease_keywords: tuple, increase_keywords: tuple) -> bool:
    return (
        "ar condicionado" in text
        or "ar-condicionado" in text
        or bool(re.search(r"\bac\b", text))
        or "temperatura" in text
        or "quente" in text
        or "frio" in text
        or any(k in text for k in decrease_keywords)
        or any(k in text for k in increase_keywords)
    )


def _interpret_device_control_mock(
    text: str,
    context: dict | None = None,
    history: list[str] | None = None,
    topology: dict | None = None,
) -> InterpretedCommand | None:
    on_words = ("liga", "ligar", "ligue", "acende", "acender", "acenda")
    off_words = ("desliga", "desligar", "desligue", "apaga", "apague", "apagar")

    if "cortina" in text:
        room = _find_room(text)
        if room in ("living_room", "bedroom"):
            open_words = ("abre", "abrir", "levanta")
            capability = "open" if any(w in text for w in open_words) else "close"
            device_id = resolve_device_id(topology, room, "curtain", f"{room}_curtain")
            return InterpretedCommand(intent="device_control", capability=capability, device_id=device_id)

    if "janela" in text:
        room = _find_room(text) or "bedroom"
        open_words = ("abre", "abrir", "levanta")
        capability = "open" if any(w in text for w in open_words) else "close"
        device_id = resolve_device_id(topology, room, "window", f"{room}_window")
        return InterpretedCommand(intent="device_control", capability=capability, device_id=device_id)

    # plain on/off appliances (no dedicated verb before the redesign)
    appliances = {
        "cafeteira": ("coffee_maker", "kitchen_coffee_maker"),
        "tv": ("tv", "living_room_tv"),
        "televisão": ("tv", "living_room_tv"),
        "televisao": ("tv", "living_room_tv"),
        "geladeira": ("refrigerator", "kitchen_refrigerator"),
    }
    for noun, (dtype, fallback) in appliances.items():
        if noun in text and (noun != "tv" or re.search(r"\btv\b", text)):
            device_id = _resolve_by_type(topology, dtype, fallback)
            if any(w in text for w in off_words):
                return InterpretedCommand(intent="device_control", capability="turn_off", device_id=device_id)
            if any(w in text for w in on_words):
                return InterpretedCommand(intent="device_control", capability="turn_on", device_id=device_id)

    decrease_keywords = ("diminui", "diminua", "abaixa", "abaixar", "reduz", "reduzir", "muito alto")
    increase_keywords = ("aumenta", "aumentar", "sobe", "subir", "muito baixo")

    # A bare follow-up ("melhor 26", "diminua mais") may carry no AC/temperature keyword
    # of its own — if the conversation was just about the AC, treat it as still being
    # about the AC instead of forcing every message to repeat "ar-condicionado".
    mentions_ac = _mentions_ac_or_temperature(text, decrease_keywords, increase_keywords) or any(
        _mentions_ac_or_temperature(prior.lower(), decrease_keywords, increase_keywords)
        for prior in (history or [])[-3:]
    )
    room = _find_room(text)
    room_has_ac = room is None or room == "bedroom"

    if mentions_ac and room_has_ac:
        device_id = resolve_device_id(topology, "bedroom", "ac", "bedroom_ac")
        current_temperature = context.get("temperature") if context else None

        temp_match = re.search(r"(\d{1,2})\s*graus", text) or re.search(r"\b(1[5-9]|2[0-9]|30)\b", text)
        if temp_match:
            return InterpretedCommand(
                intent="device_control",
                capability="set_temperature",
                device_id=device_id,
                temperature=_clamp_ac_temperature(float(temp_match.group(1))),
            )
        if "quente" in text or any(k in text for k in decrease_keywords):
            temperature = current_temperature - 2.0 if current_temperature is not None else 20.0
            return InterpretedCommand(
                intent="device_control",
                capability="set_temperature",
                device_id=device_id,
                temperature=_clamp_ac_temperature(temperature),
            )
        if "frio" in text or any(k in text for k in increase_keywords):
            temperature = current_temperature + 2.0 if current_temperature is not None else 24.0
            return InterpretedCommand(
                intent="device_control",
                capability="set_temperature",
                device_id=device_id,
                temperature=_clamp_ac_temperature(temperature),
            )
        if "deslig" in text:
            return InterpretedCommand(intent="device_control", capability="turn_off", device_id=device_id)
        if "lig" in text:
            return InterpretedCommand(intent="device_control", capability="turn_on", device_id=device_id)

    door_id = _resolve_by_type(topology, "door", "front_door")
    if "tranca" in text or "trancar" in text:
        return InterpretedCommand(intent="device_control", capability="lock", device_id=door_id)
    if "destranca" in text or "destrancar" in text:
        return InterpretedCommand(intent="device_control", capability="unlock", device_id=door_id)
    if "arma" in text and "alarme" in text:
        return InterpretedCommand(intent="device_control", capability="arm", device_id=ALARM_DEVICE_ID)
    if "desarma" in text and "alarme" in text:
        return InterpretedCommand(intent="device_control", capability="disarm", device_id=ALARM_DEVICE_ID)

    return None


# Intent/device classification is a structured lookup, not creative writing: a low
# temperature keeps it consistently picking the right tool for the right device instead
# of improvising. The conversational fallback gets a bit more room so it still feels like
# talking to someone, not a script.
CLASSIFICATION_TEMPERATURE = 0.0
CONVERSATION_TEMPERATURE = 0.4


def _build_llm(temperature: float):
    provider = os.environ.get("LLM_PROVIDER", "mock")
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=os.environ["LLM_MODEL"],
            google_api_key=os.environ["GEMINI_API_KEY"],
            temperature=temperature,
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        # temperature=0 alone doesn't guarantee identical output run-to-run on Ollama —
        # top_k/top_p sampling still applies on top of the scaled logits. Pin top_k=1
        # (pure greedy/argmax) and a fixed seed for the classification call so the same
        # command always resolves to the same intent instead of flip-flopping.
        extra = {"top_k": 1, "seed": 0} if temperature == 0 else {}
        return ChatOllama(
            model=os.environ["LLM_MODEL"],
            base_url=os.environ["OLLAMA_BASE_URL"],
            temperature=temperature,
            **extra,
        )
    raise ValueError(f"unsupported LLM_PROVIDER: {provider!r}")


def _ac_context_text(context: dict | None) -> str:
    if not context or context.get("temperature") is None:
        return ""
    state_desc = "ligado" if context.get("on") else "desligado"
    current = context["temperature"]
    return (
        f"\n\nEstado atual do ar-condicionado do quarto: {state_desc}, temperatura "
        f"configurada em {current}°C. Se o usuário pedir um ajuste relativo (diminua, "
        f"aumenta, muito baixo, muito alto, ainda está frio/quente etc.), calcule o novo "
        f"valor ABSOLUTO a partir de {current} (ex: {current - 2}°C se for para diminuir, "
        f"{current + 2}°C se for para aumentar) — nunca retorne apenas o delta (-2 ou 2)."
    )


def _history_text(history: list[str] | None) -> str:
    recent = (history or [])[-3:]
    if not recent:
        return ""
    numbered = "\n".join(f'{i}. "{msg}"' for i, msg in enumerate(recent, start=1))
    return (
        "\n\nMensagens anteriores do usuário nesta mesma conversa (mais antiga primeiro, "
        "a mensagem atual a interpretar vem depois, separada):\n"
        f"{numbered}\n"
        "Use isso para resolver comandos curtos e ambíguos (ex: \"diminua mais\", \"melhor "
        "26\") que só fazem sentido à luz do que acabou de ser dito — se as mensagens "
        "recentes já eram sobre o ar-condicionado do quarto, assuma que a mensagem atual "
        "também é, mesmo sem repetir \"ar-condicionado\"/\"temperatura\"."
    )


async def _interpret_with_llm(
    user_message: str,
    context: dict | None = None,
    history: list[str] | None = None,
    topology: dict | None = None,
) -> InterpretedCommand:
    llm = _build_llm(temperature=CLASSIFICATION_TEMPERATURE)
    provider = os.environ.get("LLM_PROVIDER", "mock")
    # Ollama's default structured-output method (strict JSON-schema-constrained decoding)
    # forces valid *shape* but not correct *content* — smaller models like llama3.1:8b
    # tend to leave optional fields (e.g. device_id) null under that constraint even when
    # they clearly understood the command. Plain json_mode, guided by the prose examples
    # already in SYSTEM_PROMPT, is far more reliable for this model in practice.
    method = "json_mode" if provider == "ollama" else "function_calling"
    structured_llm = llm.with_structured_output(InterpretedCommand, method=method)
    prompt = (
        f"{SYSTEM_PROMPT}{_topology_text(topology)}{_ac_context_text(context)}"
        f"{_history_text(history)}\n\nCommand: {user_message}"
    )
    return await structured_llm.ainvoke(prompt)


async def interpret_command(
    user_message: str,
    context: dict | None = None,
    history: list[str] | None = None,
    topology: dict | None = None,
) -> InterpretedCommand:
    provider = os.environ.get("LLM_PROVIDER", "mock")
    if provider == "mock":
        # The mock is a pure keyword classifier by design (CI, no model available),
        # so it owns its own greeting rule. The real LLM path classifies `chitchat`
        # itself from the SYSTEM_PROMPT — no shortcut around it.
        command = _interpret_mock(user_message, context, history, topology)
    else:
        command = await _interpret_with_llm(user_message, context, history, topology)

    # Safety net regardless of provider: never trust the interpreter (mock or LLM) to
    # keep the value within what a real AC can do — clamp here as the single choke point.
    if command.capability == "set_temperature" and command.temperature is not None:
        command.temperature = _clamp_ac_temperature(command.temperature)

    return command


FALLBACK_CAPABILITIES_TEXT = (
    "controlar luzes, cortinas, ar-condicionado (inclusive temperatura), porta e alarme de um "
    "cômodo, preparar a casa para dormir, sair de casa com segurança, consultar o consumo de "
    "energia, ou dar um status geral da casa"
)


def _fallback_message_mock() -> str:
    return f"Não entendi esse comando. Posso ajudar com: {FALLBACK_CAPABILITIES_TEXT}. Pode tentar reformular?"


async def _fallback_message_with_llm(user_message: str) -> str:
    llm = _build_llm(temperature=CONVERSATION_TEMPERATURE)
    prompt = (
        "Você é o assistente de uma casa inteligente, respondendo em português do Brasil, "
        "em no máximo duas frases curtas, com um tom natural e caloroso (nunca robótico).\n"
        "A mensagem do usuário não corresponde a nenhum dos comandos que você sabe executar.\n"
        f"Comandos que você sabe executar: {FALLBACK_CAPABILITIES_TEXT}.\n"
        "Se a mensagem for uma saudação ou conversa casual (ex: 'olá', 'tudo bem?', 'oi'), "
        "responda cordialmente e, de forma breve, convide a pessoa a pedir algo que você "
        "possa fazer. Se for um pedido real que você não suporta, diga isso com gentileza "
        "e sugira uma das opções acima.\n\n"
        f"Mensagem do usuário: {user_message}"
    )
    response = await llm.ainvoke(prompt)
    return _extract_text(response.content)


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if parts:
            return "".join(parts)
    return str(content)


async def fallback_message(user_message: str) -> str:
    provider = os.environ.get("LLM_PROVIDER", "mock")
    if provider == "mock":
        return _fallback_message_mock()
    try:
        return await _fallback_message_with_llm(user_message)
    except Exception:
        # The LLM itself may be unavailable/rate-limited — degrade to the
        # deterministic message rather than leaving the request hanging.
        return _fallback_message_mock()


CHITCHAT_MOCK = (
    "Olá! Sou o assistente da casa. Posso controlar luzes, cortinas, ar-condicionado, "
    "porta e alarme, preparar a casa para você dormir ou sair, e informar o consumo de "
    "energia e o status da casa. Como posso ajudar?"
)


async def chitchat_message(user_message: str) -> str:
    """Reply to a greeting / social opener. No devices are touched — this is the
    `interpret -> chitchat -> END` shortcut, so a plain command never falls into
    'greeting = interpretation failure'."""
    provider = os.environ.get("LLM_PROVIDER", "mock")
    if provider == "mock":
        return CHITCHAT_MOCK
    try:
        # The conversational-fallback prompt already handles greetings warmly.
        return await _fallback_message_with_llm(user_message)
    except Exception:
        return CHITCHAT_MOCK
