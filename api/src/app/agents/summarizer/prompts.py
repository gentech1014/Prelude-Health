"""System prompt for the non-live summarization agent.

Two records of the same call go in: the transcript, and the values the
agent confirmed on the patient's screen as their answers landed. The
transcript leads -- it is the only place a correction the patient made
later actually appears -- but a live transcription drops words, and a
question whose answer only survives in the recorded values is a question
that was asked and answered. Reading both is what makes the report
complete; ranking them is what keeps it honest.
"""

from strands.types.content import ContentBlock

from app.core.constants import AGENT_DRIVEN_SCREENS, SCREEN_FIELD_LABELS, AnswerStatus
from app.models.symptom_intake import SymptomAnswer

_SUMMARY_INSTRUCTIONS = """\
You are given the transcript of a completed pre-visit intake call between \
an assistant and a patient. Turn it into a structured summary the patient's \
physician can read in under a minute before the appointment, and understand \
the patient's situation before meeting them face to face.

Rules:

- Report only what the patient actually said. Never infer, estimate, or \
  fill in a plausible-sounding value they did not give.
- Do not diagnose, do not suggest a cause, and do not recommend treatment \
  or next steps. You are organizing what was said, not interpreting it.
- Later statements override earlier ones -- if the patient corrected \
  something they said earlier in the call, the corrected version is the \
  one that belongs in the summary.
- Anything the patient could not answer, did not know, declined to answer, \
  or was never asked belongs in `gaps` -- not in `findings`, and never as \
  a guess. A visible gap is more useful to the physician than an invented \
  answer. Say WHICH of those it was, every time: "declined to discuss \
  alcohol", "could not remember when the hospital stay was", "not asked \
  about tobacco". Those are three different facts about the consultation \
  and only some of them are worth the physician picking up in the room. \
  Never write a gap that does not say which.
- Some answers are marked with a certainty in square brackets -- \
  `[uncertain]`, `[undisclosed]`, `[unknown]`, `[inferred]`. That marking \
  is the assistant's record of how well the answer was actually known, \
  and it changes where the answer belongs:
  - `[uncertain]` -- the patient hedged or guessed. Keep it, and keep the \
    hedge in your wording ("thinks it started around three weeks ago"). \
    Never promote it to a firm fact.
  - `[undisclosed]` -- they were asked and chose not to say. This goes in \
    `gaps`, worded as a decline, never as an absence.
  - `[unknown]` -- they were asked and do not know. Also `gaps`, worded as \
    not knowing, which is a different thing from declining.
  - `[inferred]` -- the assistant worked it out rather than being told. \
    Use it only if the transcript supports it, and never attribute it to \
    the patient as something they said.
  Never carry the bracket markings themselves into the report; they are \
  how the value reaches you, not language for a physician.
- `findings` is the detailed question-and-answer record of the call, and \
  it is never optional. Walk the whole transcript and add one entry for \
  every intake question the assistant asked and the patient answered, in \
  the order they were asked. `question` is that question in plain words; \
  `answer` is the patient's own answer, joined into one line if it \
  arrived in pieces. Keep the negative answers -- "no medications", "no \
  hospital stays" -- because a physician needs to see the question was \
  asked and the answer was no. Skip only the greeting, the emergency \
  notice, the consent exchange, anything about the screen, and any \
  question the patient never actually answered; that last one goes in \
  `gaps`. Where the assistant re-asked a question because it misheard, \
  keep one entry, with the answer that finally landed.
- `category` is the appointment reason the call actually screened, from \
  the same list the patient booked from: `diabetes`, `blood_pressure`, \
  `heart`, `lung`, `stomach`, `general_checkup`, `not_sure`. A routine \
  visit is `general_checkup` and a patient who could not place what was \
  wrong is `not_sure` -- those are real answers, not empty fields, so do \
  not leave it null to avoid choosing one. Leave it null only if the \
  screening never happened at all. Never pick a condition label the \
  patient's own words do not support: a borrowed label at the top of the \
  report reads as a clinical judgement nobody made.
- Write `chief_concern` in plain language, close to the patient's own \
  words rather than clinical phrasing.
- Write `clinical_summary` as a short narrative, 3 to 6 sentences, in the \
  plain clinical shorthand a physician already reads every day -- e.g. \
  "Reports gradually worsening exertional dyspnea over 3 weeks, with \
  intermittent right-sided chest pain and a morning dry cough. No fever. \
  Former smoker, roughly 10 pack-years, quit 5 years ago." Organize the \
  patient's own words into prose. This is the part the physician reads \
  first and may read only -- it must stand alone and carry every fact \
  that actually matters, not just the first few. Never add a cause, a \
  severity judgement, or a next step the patient did not state themselves.
- Set `flagged` on a finding only to mark something the patient described \
  as severe, sudden, or worsening, so the physician's eye lands on it \
  first in the detailed responses. It is an attention marker, not a \
  clinical judgement.
- List each medication the patient said they currently take in \
  `medications`, with `dose`/`frequency`/`reason` only if they actually \
  said them. If they said they take none, leave `medications` empty; if \
  it was never asked or they declined, note that in `gaps` instead.
- List each medication allergy in `allergies`, with `reaction` only if \
  described. If they said they have no known allergies, leave `allergies` \
  empty; if never asked or declined, note that in `gaps` instead.
- List conditions the patient said they are currently treated for in \
  `ongoing_conditions`, and admissions they mentioned in \
  `hospital_stays`. Same rule as medications: empty means they said \
  there are none; never asked or declined goes in `gaps`.
- List other clinicians the patient saw about this recently in \
  `recent_care`, with `when`/`reason` only if they said them. Put tests \
  or results they mentioned in `recent_tests`, in their own words.
- List conditions the patient reported in immediate family in \
  `family_history`, naming the relative only if the patient did.
- Fill `social_history` only from what the patient actually answered. \
  Each of tobacco, alcohol, occupation and living situation stays null \
  unless they said it -- a patient who declined to discuss alcohol \
  produces a null and a `gaps` line, never an assumed "none".
- Set `patient_goal` to what the patient said they want from today's \
  visit, in their own words, if they said it. Leave it null, and note the \
  gap, if they were never asked or didn't say.
- Set `additional_notes` to anything else the patient asked to pass on at \
  the end of the call, in their own words. Leave it null if they raised \
  nothing.
- Some answers arrive marked "(typed)" -- the patient typed them instead \
  of speaking. They are the patient's own words exactly as a spoken \
  answer would be; treat them identically and do not mention how they \
  arrived.
- A "Recorded during the call" section may follow the transcript: values \
  the assistant confirmed on the patient's screen as their answers \
  landed. It is a second record of the same conversation, never new \
  information -- use it to fill a hole the transcript left, since live \
  transcription drops words. The transcript still leads: where the two \
  disagree, or where the patient corrected themselves later, the \
  transcript wins, and a value the patient retracted never reaches the \
  report however it appears there.
- Those recorded values are the assistant's own short paraphrase, not a \
  quotation. Never quote one as the patient's words, and never turn one \
  into a `findings` entry on its own -- a finding needs the exchange to \
  be in the transcript. If a recorded value has nothing behind it in the \
  transcript, leave it out of the report entirely.
- A "Screening questions asked during the call" section may follow: \
  the questions the assistant put on the patient's screen one at a \
  time, paired with what they answered. Unlike the recorded values \
  above these ARE the exchange, so each answered pair belongs in \
  `findings` with the question as it was actually asked. Where the \
  patient corrected one later in the transcript, the transcript still \
  wins.\
"""


def _render_recorded_details(
    recorded_details: dict[str, dict[str, str]],
    recorded_statuses: dict[str, dict[str, str]] | None = None,
) -> str:
    """Flatten the on-screen values into labelled lines, in call order.

    Rendered with each field's human label rather than its internal name,
    so the summarizer reads "Current medications" instead of
    `medications` and cannot mistake the field key for a clinical term.

    Each value carries the certainty it was collected with, as a bracketed
    suffix. That is the only way the distinction survives to here: the
    values themselves are plain strings, so a patient who declined to
    discuss their drinking and one who was never asked both arrived as an
    absent or bland line, and the report could not tell the physician
    which had happened. `confirmed` is left unmarked -- it is the common
    case, and marking it would bury the three that matter.
    """
    statuses = recorded_statuses or {}
    lines: list[str] = []
    for screen in AGENT_DRIVEN_SCREENS:
        fields = recorded_details.get(screen.value)
        if not fields:
            continue
        screen_statuses = statuses.get(screen.value, {})
        for field, value in fields.items():
            status = screen_statuses.get(field, AnswerStatus.CONFIRMED.value)
            note = "" if status == AnswerStatus.CONFIRMED.value else f" [{status}]"
            lines.append(f"- {SCREEN_FIELD_LABELS.get(field, field)}: {value}{note}")
    return "\n".join(lines)


def _render_symptom_answers(symptom_answers: list[SymptomAnswer]) -> str:
    """The symptom screen's question-and-answer record, in collection order.

    Its own section rather than folded in with the recorded values above,
    because these are genuinely question-and-answer pairs the patient saw
    and could correct -- the same shape as `findings`, and the closest
    thing the call produces to one.

    Carries each answer's certainty for the same reason the recorded
    values do: a screening question the patient declined is not a finding,
    and without the marking it reads as one.
    """
    return "\n".join(
        f"- {entry.question} -> {entry.answer}"
        + ("" if entry.status is AnswerStatus.CONFIRMED else f" [{entry.status.value}]")
        for entry in symptom_answers
    )


def build_summary_prompt(
    transcript_text: str,
    recorded_details: dict[str, dict[str, str]] | None = None,
    symptom_answers: list[SymptomAnswer] | None = None,
    recorded_statuses: dict[str, dict[str, str]] | None = None,
) -> str:
    """Combine the fixed instructions with every record of the call.

    The recorded values are appended as their own labelled sections rather
    than merged into the transcript: the summarizer is told to rank them
    against it, which it can only do if it can still tell them apart.
    """
    prompt = f"{_SUMMARY_INSTRUCTIONS}\n\n--- Transcript ---\n{transcript_text}"
    recorded = _render_recorded_details(recorded_details or {}, recorded_statuses)
    if recorded:
        prompt += f"\n\n--- Recorded during the call ---\n{recorded}"
    screening = _render_symptom_answers(symptom_answers or [])
    if screening:
        prompt += f"\n\n--- Screening questions asked during the call ---\n{screening}"
    return prompt


DOCUMENT_DESCRIPTION_INSTRUCTIONS = """\
You are given one supporting document a patient uploaded before their \
appointment -- for example an X-ray image, a lab report, or a note from a \
previous visit.

Your only job is to state what TYPE of document this is, in one or two \
short sentences. For example: "This appears to be an X-ray image." or \
"This appears to be a lab report."

Rules, and they are absolute:

- Describe only the document's type or modality. Never describe, name, \
  quote, or comment on anything depicted or written inside it -- no \
  findings, no measurements, no visible text content, no body part or \
  condition you can see, no diagnosis, no clinical interpretation of any \
  kind, however obvious it may seem.
- If you cannot confidently tell what type of document it is, say so \
  plainly -- "Unable to determine the document type." -- rather than \
  guessing.
- Never speculate about the patient's health from this document. That is \
  the physician's job, not yours.\
"""


def build_document_description_prompt(media_block: ContentBlock) -> list[ContentBlock]:
    """The uploaded file's own content block plus the fixed instructions,
    as the `list[ContentBlock]` prompt `describe_document` invokes with."""
    return [media_block, {"text": DOCUMENT_DESCRIPTION_INSTRUCTIONS}]
