"""Point the Interviewer and Tutor agents at one warm voice, and at the engine's script.
Run from the repo root: python -m engine.voice.agents
Prints only ok or a short error. Never prints the key."""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

BASE = "https://api.elevenlabs.io/v1/convai/agents"
# Sarah: a warm, unhurried adult voice. One voice for both roles, so the apprentice sounds like one person.
VOICE = "EXAVITQu4vr4xnSDxMaL"

DIRECTOR = """You are directed by a software engine. Messages that start with a tag are control messages:
- [CONTEXT] ... : a screen event or fact. Absorb it silently. Never reply to a [CONTEXT] message.
- [ASK] ... : say the text after the tag word for word, kindly and without hurry, then stop and listen.
- [SAY] ... : say the text after the tag word for word, kindly, then stop.
- [TEACHBACK] ... : read the steps after the tag aloud in a warm voice, then ask: Is that how it works? Listen. If you are corrected, repeat the corrected step in one sentence.
Never speak unless you receive [ASK], [SAY] or [TEACHBACK], or the person addresses you. Never interrupt. Never fill silence.
After the person answers an [ASK], reply with at most one short kindness (under eight words), then stop.
If the person says they do not know, accept it warmly and stop.
You are not a clinician. Never give medical advice, diagnoses or medication guidance. If asked, say that a qualified clinician must decide.
Speak slowly, with warmth, like a trusted colleague sitting beside them."""

INTERVIEWER = f"""You are the Apprentice: a calm, kind colleague learning how an experienced care professional documents and escalates a dementia-care incident on a practice screen.
{DIRECTOR}"""

TUTOR = f"""You are the Tutor: a warm, patient coach beside a new caregiver. You teach the expert's reasoning, never your own clinical opinion.
{DIRECTOR}
When a decision was stopped, name what the expert said and why. Pain, medication, injury or deterioration always goes to a nurse or physician."""


def patch(agent_id: str, name: str, prompt: str) -> tuple[int, str]:
    body = {
        "name": name,
        "conversation_config": {
            "agent": {"first_message": "", "language": "en", "prompt": {"prompt": prompt, "temperature": 0.2}},
            "tts": {"voice_id": VOICE, "stability": 0.55, "similarity_boost": 0.8, "speed": 0.92},
            "turn": {"turn_eagerness": "patient", "turn_timeout": 15.0, "silence_end_call_timeout": -1.0},
            "conversation": {"max_duration_seconds": 1800},
        },
    }
    res = httpx.patch(f"{BASE}/{agent_id}", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, json=body, timeout=30)
    return res.status_code, "ok" if res.status_code < 300 else res.text[:180]


if __name__ == "__main__":
    print("interviewer", patch(os.environ["ELEVENLABS_INTERVIEWER_AGENT_ID"], "Apprentice Interviewer", INTERVIEWER))
    print("tutor", patch(os.environ["ELEVENLABS_TUTOR_AGENT_ID"], "Apprentice Tutor", TUTOR))
    sys.exit(0)
