"""Merchant onboarding config — merchant.toml, with defaults that match the demo.

The pipeline is merchant-agnostic. Budgets, thresholds, windows, channel
realness and message costs are read from here rather than hard-coded, so real
data from any Razorpay account runs against the merchant's own policy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "merchant.toml"

# RBI E-Mandate Framework 2026: AFA-free ceilings by category, in paise.
AFA_LIMIT_DEFAULT_PAISE = 15_000_00
AFA_LIMIT_RAISED_PAISE = 1_00_000_00
AFA_RAISED_CATEGORIES = ("mutual_fund", "insurance", "credit_card_bill")


@dataclass
class Window:
    start: int
    end: int

    def contains(self, hour: int) -> bool:
        return self.start <= hour < self.end

    def label(self) -> str:
        return f"{self.start:02d}:00–{self.end:02d}:00 IST"


@dataclass
class MerchantConfig:
    name: str = "RecoverOps demo merchant"
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    is_registered_mse: bool = False
    category: str = "subscription"

    contact_budget_per_batch: int = 120
    holdout_share: float = 0.10
    # Share of Agent B's contact decisions flipped at random inside the treatment
    # arm, so contact has a known propensity and per-event uplift is learnable.
    exploration_share: float = 0.10
    churn_residual_cycles: int = 3
    approval_threshold_paise: int = 1_000_000
    discount_cap_pct: int = 5

    baseline_probability: float = 0.35
    uplift_threshold: float = 0.10

    razorpay_notify_sms: bool = True
    razorpay_notify_email: bool = True
    whatsapp: str = "mock"
    voice: str = "mock"

    costs_paise: dict[str, int] = field(
        default_factory=lambda: {
            "sms_transactional": 20,
            "whatsapp_utility": 15,
            "whatsapp_marketing": 109,
            "email": 2,
            "voice_per_minute": 100,
        }
    )

    promotional_window: Window = field(default_factory=lambda: Window(9, 21))
    dues_window: Window = field(default_factory=lambda: Window(8, 19))

    max_contacts_7d: int = 2
    max_retries_per_cycle_cards: int = 3
    max_mandate_attempts: int = 4
    max_voice_calls_per_day: int = 3
    max_voice_calls_per_week: int = 8
    network_retry_cap_30d: dict[str, int] = field(default_factory=lambda: {"Visa": 15, "MasterCard": 10, "default": 10})

    # Voice: TRAI requires promotional auto-dialled calls on the 140-series and
    # service/transactional ones on 1600. A merchant configures the series it
    # actually holds; the gate refuses a class it cannot legally originate.
    voice_caller_series: str = "1600"
    voice_recording_disclosure: bool = True
    voice_min_value_paise: int = 200000
    # Margin given up per rupee of discount, for the cart incentive arm.
    gross_margin_pct: int = 45

    source_path: str | None = None

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: Path | str | None = None) -> "MerchantConfig":
        p = Path(path) if path else DEFAULT_PATH
        cfg = cls()
        if not p.exists():
            return cfg
        raw = tomllib.loads(p.read_text())
        m = raw.get("merchant", {})
        b = raw.get("budget", {})
        t = raw.get("thresholds", {})
        ch = raw.get("channels", {})
        costs = raw.get("costs_paise", {})
        w = raw.get("windows", {})
        f = raw.get("frequency", {})

        cfg.name = m.get("name", cfg.name)
        cfg.currency = m.get("currency", cfg.currency)
        cfg.timezone = m.get("timezone", cfg.timezone)
        cfg.is_registered_mse = bool(m.get("is_registered_mse", cfg.is_registered_mse))
        cfg.category = m.get("category", cfg.category)

        cfg.contact_budget_per_batch = int(b.get("contact_budget_per_batch", cfg.contact_budget_per_batch))
        cfg.holdout_share = float(b.get("holdout_share", cfg.holdout_share))
        cfg.exploration_share = float(b.get("exploration_share", cfg.exploration_share))
        cfg.churn_residual_cycles = int(b.get("churn_residual_cycles", cfg.churn_residual_cycles))
        cfg.approval_threshold_paise = int(b.get("approval_threshold_paise", cfg.approval_threshold_paise))
        cfg.discount_cap_pct = int(b.get("discount_cap_pct", cfg.discount_cap_pct))

        cfg.baseline_probability = float(t.get("baseline_probability", cfg.baseline_probability))
        cfg.uplift_threshold = float(t.get("uplift", cfg.uplift_threshold))

        cfg.razorpay_notify_sms = bool(ch.get("razorpay_notify_sms", cfg.razorpay_notify_sms))
        cfg.razorpay_notify_email = bool(ch.get("razorpay_notify_email", cfg.razorpay_notify_email))
        cfg.whatsapp = str(ch.get("whatsapp", cfg.whatsapp))
        cfg.voice = str(ch.get("voice", cfg.voice))

        cfg.costs_paise = {**cfg.costs_paise, **{k: int(v) for k, v in costs.items()}}

        if "promotional" in w:
            cfg.promotional_window = Window(int(w["promotional"]["start"]), int(w["promotional"]["end"]))
        if "dues" in w:
            cfg.dues_window = Window(int(w["dues"]["start"]), int(w["dues"]["end"]))

        cfg.max_contacts_7d = int(f.get("max_contacts_7d", cfg.max_contacts_7d))
        cfg.max_retries_per_cycle_cards = int(f.get("max_retries_per_cycle_cards", cfg.max_retries_per_cycle_cards))
        cfg.max_mandate_attempts = int(f.get("max_mandate_attempts", cfg.max_mandate_attempts))
        cfg.max_voice_calls_per_day = int(f.get("max_voice_calls_per_day", cfg.max_voice_calls_per_day))
        cfg.max_voice_calls_per_week = int(f.get("max_voice_calls_per_week", cfg.max_voice_calls_per_week))
        if "network_retry_cap_30d" in f:
            cfg.network_retry_cap_30d = {**cfg.network_retry_cap_30d, **{k: int(v) for k, v in f["network_retry_cap_30d"].items()}}

        v = raw.get("voice", {})
        cfg.voice_caller_series = str(v.get("caller_series", cfg.voice_caller_series))
        cfg.voice_recording_disclosure = bool(v.get("recording_disclosure", cfg.voice_recording_disclosure))
        cfg.voice_min_value_paise = int(v.get("min_value_paise", cfg.voice_min_value_paise))
        cfg.gross_margin_pct = int(b.get("gross_margin_pct", cfg.gross_margin_pct))

        cfg.source_path = str(p)
        return cfg

    # --------------------------------------------------------------- derived

    @property
    def afa_limit_paise(self) -> int:
        return AFA_LIMIT_RAISED_PAISE if self.category in AFA_RAISED_CATEGORIES else AFA_LIMIT_DEFAULT_PAISE

    def network_cap(self, network: str | None) -> int:
        return self.network_retry_cap_30d.get(network or "", self.network_retry_cap_30d["default"])

    def cost_for(self, action: str, message_class: str | None) -> int:
        """Marginal cost of one execution of `action`, in paise, at the class the gate assigned."""
        if action in ("silent_retry", "retry_scheduled", "escalate", "no_action", "virtual_account"):
            return 0
        if action == "voice_call":
            # A collections call runs about ninety seconds, plus TTS and STT.
            return int(self.costs_paise["voice_per_minute"] * 1.5) + self.costs_paise.get("voice_ai_per_call", 0)
        if action in ("invoice_reminder", "statement_of_account", "msmed_notice"):
            return self.costs_paise["email"] + self.costs_paise["sms_transactional"]
        if action in ("payment_link_sms", "card_update_request"):
            return self.costs_paise["sms_transactional"]
        if action in ("payment_link_whatsapp", "cart_reminder"):
            return self.costs_paise["whatsapp_marketing"] if message_class == "promotional" else self.costs_paise["whatsapp_utility"]
        if action in ("incentive_link", "cart_incentive"):
            return self.costs_paise["whatsapp_marketing"]
        return self.costs_paise["sms_transactional"]

    def public(self) -> dict:
        return {
            "name": self.name,
            "currency": self.currency,
            "timezone": self.timezone,
            "isRegisteredMse": self.is_registered_mse,
            "category": self.category,
            "afaLimitPaise": self.afa_limit_paise,
            "contactBudgetPerBatch": self.contact_budget_per_batch,
            "holdoutShare": self.holdout_share,
            "explorationShare": self.exploration_share,
            "churnResidualCycles": self.churn_residual_cycles,
            "approvalThresholdPaise": self.approval_threshold_paise,
            "discountCapPct": self.discount_cap_pct,
            "baselineProbability": self.baseline_probability,
            "upliftThreshold": self.uplift_threshold,
            "channels": {
                "razorpayNotifySms": self.razorpay_notify_sms,
                "razorpayNotifyEmail": self.razorpay_notify_email,
                "whatsapp": self.whatsapp,
                "voice": self.voice,
            },
            "costsPaise": self.costs_paise,
            "windows": {
                "promotional": {"start": self.promotional_window.start, "end": self.promotional_window.end},
                "dues": {"start": self.dues_window.start, "end": self.dues_window.end},
            },
            "frequency": {
                "maxContacts7d": self.max_contacts_7d,
                "maxRetriesPerCycleCards": self.max_retries_per_cycle_cards,
                "maxMandateAttempts": self.max_mandate_attempts,
                "maxVoiceCallsPerDay": self.max_voice_calls_per_day,
                "maxVoiceCallsPerWeek": self.max_voice_calls_per_week,
                "networkRetryCap30d": self.network_retry_cap_30d,
            },
            "voice": {
                "callerSeries": self.voice_caller_series,
                "recordingDisclosure": self.voice_recording_disclosure,
                "minValuePaise": self.voice_min_value_paise,
            },
            "grossMarginPct": self.gross_margin_pct,
            "sourcePath": self.source_path,
        }
