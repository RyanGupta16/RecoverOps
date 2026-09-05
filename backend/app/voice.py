"""Hinglish voice recovery: the last, highest-value channel.

Voice is not a leak type. It is a channel the policy reserves for cases that
earn it — high value, text already tried, a live consent, inside the window,
on a number series that may legally originate the message class. Its job is to
capture a promise a human can hear and a machine can verify.

The dialogue is a fixed state machine with slots, not a free-running model. A
collections call must not improvise: what it may say is bounded by the same
policy that decided to place it, and every line is a template whose variables
come from the leak. The customer's reply is classified into intents by rules,
with an optional LLM upgrade for the ambiguous ones — the same structure as the
diagnosis layer.

Speech is Sarvam: ``POST https://api.sarvam.ai/text-to-speech`` (``bulbul:v3``,
``language_code hi-IN``, native code-mixed Hinglish, ≤2,500 chars, base64
``audios[]``) and ``POST /speech-to-text`` (``saaras:v3``, ``mode codemix``).
Without ``SARVAM_API_KEY`` the adapter returns a labelled mock and the console
shows the script rather than the audio — it never claims a call was spoken.
Telephony is always simulated: placing real calls needs a provisioned 140/1600
series and DLT registration, which is paperwork, not code.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .leaks import LeakEvent

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_MODEL = "bulbul:v3"
STT_MODEL = "saaras:v3"
TTS_MAX_CHARS = 2500
DEFAULT_SPEAKER = "ritu"

IST = timezone(timedelta(hours=5, minutes=30))

# The script. Every line is a template; nothing is generated free-form.
SCRIPT = {
    "greet": "Namaste {name} ji, main {merchant} se RecoverOps assistant bol raha hoon. Yeh call recording ke liye hai.",
    "state": "Aapka {plan} ka payment {amount} fail ho gaya tha — reason: {reason}. Main aapki help karne ke liye call kar raha hoon.",
    "offer": "Do options hain: main abhi aapko ek payment link bhej doon, ya aap bata dijiye kis date tak pay kar denge?",
    "confirm_promise": "Theek hai, main note kar raha hoon: {amount}, {date} tak. Us din se pehle ek reminder bhej doonga.",
    "confirm_link": "Bilkul, main abhi {channel} par payment link bhej raha hoon. Wo chhattis ghante tak valid rahega.",
    "decline": "Samajh gaya. Main aapko aur call nahi karunga. Zaroorat ho to hamein contact kar sakte hain.",
    "close": "Dhanyavaad {name} ji. Aapka din shubh ho.",
}

# Intent classification: rules first, because most replies are unambiguous.
#
# Saaras returns code-mixed transcripts in mixed script — Hindi in Devanagari,
# English in Latin ("मैं कल तक payment कर दूँगा।") — so every pattern has to
# match both. Matching only the romanisation silently drops real promises,
# which is the worst possible failure here.
#
# Every term is boundary-guarded, and Devanagari cannot use \b for it. Python's
# \b is defined on \w, which excludes the combining vowel signs (Mc/Mn) that
# most Hindi words end in: \bदूँगा\b does NOT match "कर दूँगा", because the
# final ा is not a word character. Meanwhile an unguarded term matches inside
# longer words — bare मत fires on मतलब ("meaning"), bare कल on आजकल
# ("nowadays"), turning ordinary filler into a refusal or a fabricated promise
# date. So Devanagari terms are wrapped in explicit block lookarounds, which
# treat every Devanagari character — letters and matras alike — as word-internal.

DEVANAGARI = r"ऀ-ॿ"


def _term(word: str) -> str:
    """One alternative, boundary-guarded in whichever script it is written."""
    if word.isascii():
        return rf"\b{word}\b"
    return rf"(?<![{DEVANAGARI}]){word}(?![{DEVANAGARI}])"


def _alt(*words: str) -> str:
    return "|".join(_term(w) for w in words)


PROMISE_TERMS = (
    # Romanised
    "kal", "parso", "tomorrow", "salary", "dunga", "doonga", "karunga", "karoonga",
    "kar dunga", "kar doonga", "ho jayega", "ho jaayega",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # Devanagari: tomorrow / day after / date / salary / "will do" / "it'll happen"
    "कल", "परसों", "परसो", "तारीख", "तारिख", "सैलरी", "तनख्वाह", "वेतन",
    "दूँगा", "दूंगा", "दूगा", "देंगे", "दूँगी", "दूंगी",
    "करूँगा", "करूंगा", "करूँगी", "करूंगी", "करेंगे", "कर दूँगा", "कर दूंगा",
    "हो जाएगा", "हो जायेगा", "भर दूँगा", "भर दूंगा",
    "सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार",
)
SEND_LINK_TERMS = (
    "link", "bhej", "bhejo", "bhejiye", "send", "abhi", "now", "whatsapp", "sms",
    "लिंक", "भेज", "भेजिए", "भेजो", "अभी", "व्हाट्सएप", "व्हाट्सऐप", "एसएमएस",
)
DISPUTE_TERMS = (
    "galat", "wrong", "dispute", "refund", "cancel kar diya", "nahi liya",
    "ग़लत", "गलत", "कैंसिल", "कैन्सल", "रद्द", "रिफंड", "रिफ़ंड", "नहीं लिया", "वापस",
)
DECLINE_TERMS = (
    "nahi", "nahin", "no", "mat", "band", "stop", "pareshan",
    "नहीं", "नही", "मत", "बंद", "बन्द", "परेशान", "रुकिए", "रोक",
)
# "phir" alone means "then" and shows up in "link bhej do, phir pay karta hoon",
# which is a link request, not a deferral. Only the explicitly deferring forms
# belong here.
CALLBACK_TERMS = (
    "baad mein", "baad me", "later", "busy", "call back", "callback",
    "phir se", "baad mein call", "abhi nahi",
    "बाद में", "बाद मे", "व्यस्त", "बिज़ी", "बिजी", "फिर से", "दोबारा", "अभी नहीं",
)

INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("promise", re.compile(rf"{_alt(*PROMISE_TERMS)}|\b(\d{{1,2}})\s*(?:tarikh|tareekh|date)\b", re.I)),
    ("send_link", re.compile(_alt(*SEND_LINK_TERMS), re.I)),
    ("dispute", re.compile(_alt(*DISPUTE_TERMS), re.I)),
    ("decline", re.compile(_alt(*DECLINE_TERMS), re.I)),
    ("callback", re.compile(_alt(*CALLBACK_TERMS), re.I)),
]

DAY_WORDS = {"kal": 1, "parso": 2, "tomorrow": 1, "कल": 1, "परसों": 2, "परसो": 2}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
WEEKDAYS_HI = {"सोमवार": 0, "मंगलवार": 1, "बुधवार": 2, "गुरुवार": 3, "शुक्रवार": 4, "शनिवार": 5, "रविवार": 6}
SALARY_WORDS = ("salary", "सैलरी", "तनख्वाह", "वेतन")
# "18 tarikh", "18 ko", "१८ को" — the ASCII forms keep their trailing \b so
# "12 dated" does not read as the 12th; the Devanagari form uses the block guard.
DATE_TAIL = r"(?:\b(?:tarikh|tareekh|date|ko)\b|(?<![ऀ-ॿ])(?:तारीख|तारिख|को)(?![ऀ-ॿ]))"

# Devanagari digits, so "१२ तारीख" parses the same as "12 tarikh".
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalise_digits(text: str) -> str:
    return (text or "").translate(_DEVANAGARI_DIGITS)


def classify_intent(text: str) -> tuple[str, float]:
    """Rule-first intent classification. Returns (intent, confidence)."""
    t = normalise_digits(text).strip()
    if not t:
        return "silence", 1.0
    hits = [name for name, pat in INTENT_PATTERNS if pat.search(t)]
    if not hits:
        return "unclear", 0.3
    # Order matters where a reply hits several patterns. An objection outranks a
    # promise, because mishearing "this is wrong" as "I'll pay" keeps chasing
    # someone with a grievance. A deferral outranks a link request, because
    # "abhi busy hoon, baad mein call kijiye" contains "abhi" but asks us to
    # stop — and CALLBACK_TERMS is kept narrow so a plain "phir" cannot swallow
    # a genuine link request.
    for priority in ("dispute", "decline", "promise", "callback", "send_link"):
        if priority in hits:
            return priority, 0.9 if len(hits) == 1 else 0.7
    return hits[0], 0.6


def extract_promise_date(text: str, now: datetime | None = None) -> datetime | None:
    """Pull a date out of a Hinglish reply, in either script. Conservative: an
    unparseable promise is no promise, because a wrong date becomes a wrong hold."""
    now = now or datetime.now(timezone.utc)
    t = normalise_digits(text).lower()

    for word, days in DAY_WORDS.items():
        if re.search(_term(word), t):
            return (now + timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0)

    for table in (WEEKDAYS, WEEKDAYS_HI):
        for name, idx in table.items():
            if re.search(_term(name), t):
                ahead = (idx - now.weekday()) % 7 or 7
                return (now + timedelta(days=ahead)).replace(hour=12, minute=0, second=0, microsecond=0)

    m = re.search(rf"\b(\d{{1,2}})\s*{DATE_TAIL}", t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            ist_now = now.astimezone(IST)
            month, year = ist_now.month, ist_now.year
            if day <= ist_now.day:
                month, year = (1, year + 1) if month == 12 else (month + 1, year)
            try:
                return datetime(year, month, day, 12, 0, tzinfo=IST).astimezone(timezone.utc)
            except ValueError:
                return None

    if any(re.search(_term(w), t) for w in SALARY_WORDS):
        ist_now = now.astimezone(IST)
        month, year = (1, ist_now.year + 1) if ist_now.month == 12 else (ist_now.month + 1, ist_now.year)
        return datetime(year, month, 1, 12, 0, tzinfo=IST).astimezone(timezone.utc)
    return None


@dataclass
class Turn:
    speaker: str  # agent | customer
    text: str
    intent: str | None = None
    audio_b64: str | None = None
    audio_mocked: bool = True
    latency_ms: int = 0


@dataclass
class CallResult:
    event_id: str
    state: str
    turns: list[Turn] = field(default_factory=list)
    promise: dict | None = None
    outcome: str = "no_answer"  # promise | link_sent | declined | dispute | no_answer | unclear
    audio_live: bool = False
    note: str = ""
    duration_s: int = 0

    def public(self) -> dict:
        return {
            "eventId": self.event_id,
            "outcome": self.outcome,
            "state": self.state,
            "promise": self.promise,
            "audioLive": self.audio_live,
            "note": self.note,
            "durationSeconds": self.duration_s,
            "turns": [
                {
                    "speaker": t.speaker,
                    "text": t.text,
                    "intent": t.intent,
                    "audioB64": t.audio_b64,
                    "audioMocked": t.audio_mocked,
                    "latencyMs": t.latency_ms,
                }
                for t in self.turns
            ],
        }


class SarvamVoice:
    """Sarvam TTS/STT adapter. Real when SARVAM_API_KEY is set; a labelled
    mock otherwise. The mock returns no audio rather than fake audio."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("SARVAM_API_KEY", "")).strip()

    @property
    def live(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> dict:
        return {
            "provider": "sarvam",
            "ttsModel": TTS_MODEL,
            "sttModel": STT_MODEL,
            "live": self.live,
            "note": (
                f"Real Hinglish speech via {TTS_MODEL}; telephony is simulated in every configuration."
                if self.live
                else "SARVAM_API_KEY is not set — the script is produced but no audio is synthesised, and every turn says so."
            ),
        }

    def speak(self, text: str, speaker: str = DEFAULT_SPEAKER) -> tuple[str | None, bool, int, str]:
        """(base64 wav, mocked, latency_ms, note)."""
        clipped = text[:TTS_MAX_CHARS]
        if not self.live:
            return None, True, 0, "SARVAM_API_KEY not set — no audio synthesised."
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                SARVAM_TTS_URL,
                headers={"api-subscription-key": self.api_key, "content-type": "application/json"},
                json={
                    "text": clipped,
                    "language_code": "hi-IN",
                    "model": TTS_MODEL,
                    "speaker": speaker,
                    "output_audio_codec": "wav",
                    "speech_sample_rate": 22050,
                },
                timeout=30.0,
            )
            r.raise_for_status()
            audios = r.json().get("audios") or []
            latency = int((time.perf_counter() - t0) * 1000)
            if not audios:
                return None, True, latency, "Sarvam returned no audio."
            return audios[0], False, latency, f"{TTS_MODEL} · hi-IN · {len(clipped)} chars."
        except Exception as exc:  # noqa: BLE001 — a TTS failure must not sink a batch
            return None, True, int((time.perf_counter() - t0) * 1000), f"Sarvam TTS failed ({type(exc).__name__}) — script kept, audio mocked."

    def transcribe(self, wav_bytes: bytes) -> tuple[str, bool, str]:
        """(transcript, mocked, note). Code-mixed mode keeps Hindi in
        Devanagari and English in Latin, which is what the intent rules expect."""
        if not self.live:
            return "", True, "SARVAM_API_KEY not set — no transcription performed."
        try:
            r = httpx.post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": self.api_key},
                files={"file": ("turn.wav", wav_bytes, "audio/wav")},
                data={"model": STT_MODEL, "mode": "codemix", "language_code": "unknown"},
                timeout=30.0,
            )
            r.raise_for_status()
            return str(r.json().get("transcript", "")), False, f"{STT_MODEL} · codemix."
        except Exception as exc:  # noqa: BLE001
            return "", True, f"Sarvam STT failed ({type(exc).__name__})."


class SimulatedCustomer:
    """The person on the other end. Their reply follows the leak's ground truth
    where it exists, so a persuadable tends to promise and a lost cause tends to
    decline — which is what makes the call worth grading."""

    REPLIES = {
        "promise": ["Haan ji, {day} tarikh tak kar dunga", "Salary aate hi pay kar dunga", "Kal tak ho jayega"],
        "send_link": ["Link bhej dijiye abhi", "WhatsApp par bhej do", "Haan abhi kar deta hoon"],
        "decline": ["Nahi chahiye, band kar dijiye", "Mujhe interest nahi hai", "Call mat kijiye please"],
        "dispute": ["Maine to cancel kar diya tha", "Yeh charge galat hai"],
        "callback": ["Abhi busy hoon, baad mein call kijiye"],
        "silence": [""],
    }

    def __init__(self, ev: LeakEvent, rng) -> None:
        self.ev = ev
        self.rng = rng

    def reply(self) -> tuple[str, str]:
        seg = self.ev.segment
        if seg == "persuadable":
            weights = {"promise": 0.45, "send_link": 0.35, "callback": 0.1, "decline": 0.05, "silence": 0.05}
        elif seg == "sure_thing":
            weights = {"send_link": 0.35, "promise": 0.35, "callback": 0.15, "decline": 0.1, "silence": 0.05}
        elif seg == "sleeping_dog":
            weights = {"decline": 0.4, "dispute": 0.2, "callback": 0.2, "promise": 0.1, "silence": 0.1}
        elif seg == "lost_cause":
            weights = {"decline": 0.45, "dispute": 0.2, "silence": 0.2, "callback": 0.15}
        else:
            weights = {"promise": 0.3, "send_link": 0.3, "decline": 0.2, "callback": 0.1, "silence": 0.1}
        kinds = list(weights)
        probs = [weights[k] for k in kinds]
        kind = kinds[int(self.rng.choice(len(kinds), p=probs))]
        template = self.REPLIES[kind][int(self.rng.integers(0, len(self.REPLIES[kind])))]
        day = int(self.rng.integers(1, 29))
        return template.format(day=day), kind


def run_call(
    ev: LeakEvent,
    merchant_name: str,
    voice: SarvamVoice,
    rng,
    now: datetime | None = None,
    synthesize_audio: bool = True,
) -> CallResult:
    """Drive the fixed dialogue over a simulated line. Returns the transcript,
    the audio where Sarvam is configured, and any promise captured."""
    now = now or datetime.now(timezone.utc)
    name = str(ev.extras.get("customer_name") or "Customer")
    res = CallResult(event_id=ev.event_id, state="greet")
    audio_live = False

    def say(key: str, **kw) -> None:
        nonlocal audio_live
        text = SCRIPT[key].format(**kw)
        b64, mocked, latency, note = (None, True, 0, "")
        if synthesize_audio:
            b64, mocked, latency, note = voice.speak(text)
            audio_live = audio_live or not mocked
        res.turns.append(Turn("agent", text, audio_b64=b64, audio_mocked=mocked, latency_ms=latency))

    say("greet", name=name, merchant=merchant_name)
    say(
        "state",
        plan=ev.plan_name or "subscription",
        amount=f"₹{ev.amount_paise / 100:,.0f}",
        reason=ev.reason_label,
    )
    say("offer")

    customer = SimulatedCustomer(ev, rng)
    reply_text, _true_kind = customer.reply()
    intent, confidence = classify_intent(reply_text)
    res.turns.append(Turn("customer", reply_text, intent=intent))

    if intent == "promise":
        due = extract_promise_date(reply_text, now)
        if due is None:
            res.outcome = "unclear"
            res.state = "unclear"
            res.note = "A promise was heard but no date could be parsed; nothing was recorded rather than guessing one."
        else:
            res.outcome = "promise"
            res.state = "promised"
            res.promise = {
                "amountPaise": ev.amount_paise,
                "dueAt": due.isoformat(),
                "verbatim": reply_text,
                "confidence": confidence,
            }
            say("confirm_promise", amount=f"₹{ev.amount_paise / 100:,.0f}", date=due.astimezone(IST).strftime("%d %B"))
    elif intent == "send_link":
        res.outcome = "link_sent"
        res.state = "link_sent"
        say("confirm_link", channel="WhatsApp" if ev.method in ("upi", "upi_autopay") else "SMS")
    elif intent in ("decline", "dispute"):
        res.outcome = intent
        res.state = intent
        say("decline")
    elif intent == "callback":
        res.outcome = "callback"
        res.state = "callback"
    else:
        res.outcome = "no_answer" if intent == "silence" else "unclear"
        res.state = res.outcome

    say("close", name=name)
    res.audio_live = audio_live
    res.duration_s = 20 + 12 * len(res.turns)
    if not res.note:
        res.note = (
            "Real Hinglish speech from Sarvam; the telephony line and the customer are simulated."
            if audio_live
            else "Script produced without audio — SARVAM_API_KEY is not set. The telephony line and the customer are simulated in every configuration."
        )
    return res
