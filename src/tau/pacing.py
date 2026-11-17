"""Communication and pacing profile (accessibility). Off by default.

Two kinds of data, kept apart on purpose:

* **Rules** (neutral, e.g. "keep replies under three paragraphs") live in
  ``profile.yaml`` under ``pacing:``. Only these ever reach a model prompt.
* **Reasons** (any diagnosis or condition the user chooses to note) live in
  ``<brain>/private/pacing_reasons.md`` and are never read into a prompt.

Enforcement is local: length, list and number rules are checked by the same
post-check as the style guard (``tau.postcheck``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from tau import okf

CLOUD_NOTICE = (
    "Heads up about privacy. You have a cloud brain set up. While the pacing profile is on, "
    "its rules (for example \"keep replies short\") are sent with every request to that cloud "
    "provider. The reasons you gave me are never sent; they stay in a private file on this "
    "machine. Anything you type in conversation goes to the cloud provider as usual.\n"
    "What would you like?"
)
CLOUD_CHOICES = {
    "Keep going (rules go to the cloud, reasons stay here)": "keep",
    "Use only the local brain while this profile is on": "local_only",
    "Turn the pacing profile off": "off",
}

SUGGESTION_START = re.compile(
    r"^(you could|you might|maybe|perhaps|consider|if you (want|like)|would you like|"
    r"next,? you|another option|alternatively|also,)\b", re.I)

_PROFANITY = {"damn", "dammit", "hell", "shit", "fuck", "fucking", "crap", "bloody", "wtf", "ffs"}


@dataclass
class PacingRules:
    enabled: bool = False
    max_paragraphs: Optional[int] = 3
    max_sentences: Optional[int] = None
    lists_allowed: bool = True
    answer_first: bool = True
    one_thing_per_reply: bool = False
    restate_on_resume: bool = False
    sparse_numbers: bool = False
    announce_before_touching: bool = False
    never_assume_finished: bool = False
    never_push_next_step: bool = False
    hard_day: bool = False
    one_question_at_a_time: bool = False
    accessibility: bool = False
    overwhelm_signals: List[str] = field(default_factory=list)
    cloud_choice: Optional[str] = None

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any]) -> "PacingRules":
        raw = dict(profile.get("pacing") or {})
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw and raw[k] is not None}
        r = cls(**known)
        r.overwhelm_signals = [str(s) for s in (r.overwhelm_signals or [])]
        return r

    @property
    def active(self) -> bool:
        return (self.enabled or self.accessibility) and self.cloud_choice != "off"

    def effective(self) -> "PacingRules":
        """Rules after the accessibility preset and the hard-day switch are applied."""
        import dataclasses

        r = self
        if r.accessibility:
            # One switch: answer first, short replies, no lists unless asked, one question at a time.
            r = dataclasses.replace(r, answer_first=True, max_paragraphs=min(r.max_paragraphs or 2, 2),
                                    lists_allowed=False, one_question_at_a_time=True)
        if not r.hard_day:
            return r
        return dataclasses.replace(r, max_paragraphs=1, max_sentences=min(self.max_sentences or 3, 3),
                                   lists_allowed=False, one_thing_per_reply=True,
                                   never_push_next_step=True)

    def prompt_lines(self) -> List[str]:
        """Neutral instructions for the model. Never includes reasons."""
        if not self.active:
            return []
        r = self.effective()
        out = []
        if r.max_paragraphs:
            out.append(f"Keep each reply to at most {r.max_paragraphs} short paragraph(s).")
        if r.max_sentences:
            out.append(f"Use at most {r.max_sentences} sentences.")
        if not r.lists_allowed:
            out.append("Do not use bulleted or numbered lists; write plain sentences.")
        if r.answer_first:
            out.append("Put the answer in the first sentence.")
        if r.one_thing_per_reply:
            out.append("Cover one thing per reply.")
        if r.one_question_at_a_time:
            out.append("Ask at most one question per reply.")
        if r.restate_on_resume:
            out.append("When a topic is picked back up, restate in one clause the decision that matters.")
        if r.sparse_numbers:
            out.append("Use few numbers and round them; exact figures go to the pin card, not the reply.")
        if r.announce_before_touching:
            out.append("Say what you are about to change before changing it, especially finished work.")
        if r.never_assume_finished:
            out.append("Never assume the user has finished with a topic; ask.")
        if r.never_push_next_step:
            out.append("Do not push to the next step; the user sets the pace.")
        if r.hard_day:
            out.append("Today: short, literal answers. No side topics, no suggestions.")
        return out


# -- overwhelm signals ------------------------------------------------------------------
def overwhelm_match(text: str, signals: List[str]) -> Optional[str]:
    """Return the signal the user described that this message matches, if any."""
    letters = [c for c in text if c.isalpha()]
    for sig in signals:
        s = sig.lower()
        if any(w in s for w in ("caps", "capital", "shout", "upper")):
            if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
                return sig
        elif any(w in s for w in ("swear", "curs", "profan")):
            words = set(re.findall(r"[a-z]+", text.lower()))
            if words & _PROFANITY:
                return sig
        elif any(w in s for w in ("exclam", "!!!")):
            if "!!" in text:
                return sig
        elif s and s in text.lower():
            return sig
    return None


OVERWHELM_PROMPT = (
    "The user's last message matched a sign they told you means your previous move was too fast, "
    "too dense or unexpected. Work out which, fix it, say plainly in one sentence what happened, then "
    "carry on at a normal register. Do not apologise at length, do not soothe, do not back off the work."
)


# -- privacy: reasons stay local --------------------------------------------------------
def reasons_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / "private" / "pacing_reasons.md"


def save_reasons(brain_dir: Path, text: str) -> Path:
    p = reasons_path(brain_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    okf.write_concept(Path(brain_dir), "private/pacing_reasons", "Private Note", text.strip() + "\n",
                      title="Pacing reasons", description="Why the pacing rules exist. Never sent to a model.",
                      generated_by="human:user", update_index=False,
                      log_entry="Saved private pacing reasons")
    return p


def record_cloud_choice(brain_dir: Path, choice: str) -> None:
    from tau.brain import Brain

    words = {"keep": "keep the pacing profile on with the cloud brain",
             "local_only": "use only the local brain while the pacing profile is on",
             "off": "turn the pacing profile off"}[choice]
    Brain(Path(brain_dir)).add_rule(f"After the cloud privacy notice I chose to {words}.",
                                    rule=f"Pacing profile and cloud: {choice}")


def choose_cloud(asker) -> str:
    asker.say(CLOUD_NOTICE)
    pick = asker.ask("Pick one", None, list(CLOUD_CHOICES))
    return CLOUD_CHOICES.get(pick, "local_only")


def offer_pacing(asker, cloud_lane: bool, brain_dir: Path) -> Optional[Dict[str, Any]]:
    """Offered at first run. Every item is its own plain question."""
    from tau.setup import SKIP

    if not asker.yes("Would you like me to follow a communication and pacing profile? It sets how long "
                     "my replies are, whether I use lists, and how I pace things. Most people skip this.",
                     False):
        return None
    out: Dict[str, Any] = {"enabled": True}

    def num(q, rec):
        a = asker.ask(q, rec)
        try:
            return int(a)
        except (TypeError, ValueError):
            return rec

    out["max_paragraphs"] = num("At most how many paragraphs per reply?", 3)
    out["lists_allowed"] = asker.yes("Are bulleted or numbered lists okay?", True)
    out["answer_first"] = asker.yes("Should I put the answer first?", True)
    out["one_thing_per_reply"] = asker.yes("Only one thing per reply?", False)
    out["restate_on_resume"] = asker.yes("When we come back to a topic, should I restate the key decision?", True)
    out["sparse_numbers"] = asker.yes("Keep numbers few and rounded, with exact figures on a pin card?", False)
    out["announce_before_touching"] = asker.yes("Tell you what I'm about to touch before I touch it?", True)
    out["never_assume_finished"] = asker.yes("Ask instead of assuming you're done with a topic?", True)
    out["never_push_next_step"] = asker.yes("Never push you to the next step?", True)
    sig = asker.ask("In your own words, what does overwhelm look like in your messages? "
                    "(for example: sudden caps, swearing). Leave blank to skip.", "")
    if sig not in (SKIP, None, ""):
        out["overwhelm_signals"] = [s.strip() for s in str(sig).split(",") if s.strip()]
    reasons = asker.ask("If you'd like, note why these help (kept private on this machine, never sent "
                        "to any model). Leave blank to skip.", "")
    if reasons not in (SKIP, None, ""):
        save_reasons(brain_dir, str(reasons))
    if cloud_lane:
        choice = choose_cloud(asker)
        out["cloud_choice"] = choice
        if choice == "off":
            out["enabled"] = False
        record_cloud_choice(brain_dir, choice)
    return out
