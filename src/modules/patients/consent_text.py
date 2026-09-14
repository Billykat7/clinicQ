"""The words a patient reads before they answer (Issue 21).

**Owned by F (Data & Research); this is draft v1 and is marked for their review.** It is committed
in code rather than left to each screen so the same words appear on the web page, in the USSD menu
(Issue 73) and at the reception desk, and so the version a patient was shown is recorded with their
answer (``patient_consent.wording_version``).

Written to POPIA's plain-language expectation (s18): each question says what will happen, who does
it, that it is optional, and that it can be changed at any time. Short sentences, no legal terms,
no "processing" or "data subject"; each one has to fit a feature phone's USSD screen, which is why
the USSD wording is a separate, shorter line rather than the web sentence cut off.

Changing any wording means a new :data:`CONSENT_WORDING_VERSION`, so the record stays honest about
what each patient was actually shown.
"""

from typing import Final

from src.commons.enums import ConsentPurpose

#: The version recorded with every answer. Bump it whenever any wording below changes.
CONSENT_WORDING_VERSION: Final = "2026-09-v2-draft"

#: What the patient is asked, per purpose, on a screen with room for a sentence.
CONSENT_WORDING: Final[dict[ConsentPurpose, str]] = {
    ConsentPurpose.DISPLAY_NAME: (
        "Show my name on the waiting-room screen instead of my ticket number. "
        "Everyone in the waiting room can see the screen. You can say no, and still keep your "
        "place in the queue."
    ),
    ConsentPurpose.DISPLAY_COMMENT: (
        "Also show the reason I gave for my visit, next to my name. "
        "This is health information on a public screen, so we only show it if you say yes here. "
        "You can say no, and still keep your place in the queue."
    ),
    ConsentPurpose.NOTIFICATIONS: (
        "Send me messages about my place in the queue, such as when it is nearly my turn. "
        "Standard network charges may apply. We never use your number for anything else. "
        "The code that signs you in is not part of this: you asked for that one."
    ),
    ConsentPurpose.FEEDBACK_SURVEY: (
        "Send me one short message after my visit, asking how it went. "
        "Answering is up to you, and your answer never affects your care."
    ),
    ConsentPurpose.VISIT_NOTE_HISTORY: (
        "Let the nurse or doctor at a clinic read the short notes written at my earlier visits to "
        "that same clinic. The notes are private, never shown to other patients, and deleted after "
        "a short time. If you say no, each visit's notes are seen only during that visit."
    ),
}

#: The same questions for a USSD menu: one line each, GSM-7 characters only, well under 160.
USSD_CONSENT_WORDING: Final[dict[ConsentPurpose, str]] = {
    ConsentPurpose.DISPLAY_NAME: "Show my name on the clinic screen? 1 Yes 2 No",
    ConsentPurpose.DISPLAY_COMMENT: "Also show my reason for visiting? 1 Yes 2 No",
    ConsentPurpose.NOTIFICATIONS: "Message me about my turn? 1 Yes 2 No",
    ConsentPurpose.FEEDBACK_SURVEY: "One message after the visit to ask how it went? 1 Yes 2 No",
    ConsentPurpose.VISIT_NOTE_HISTORY: "Let the clinic nurse see notes from my past visits here? 1 Yes 2 No",
}

#: Shown above the questions, wherever they are asked.
CONSENT_INTRO: Final = (
    "You choose what we may do. Everything here is optional, the answer is no until you say yes, "
    "and you can change any answer at any time, on this page, on the menu, or by asking at the "
    "desk. Saying no never affects your care or your place in the queue."
)

#: Shown when a patient withdraws one: what happens next, in the words they will look for.
CONSENT_WITHDRAWN_NOTICE: Final = (
    "Done. This takes effect straight away: the screen and our messages follow your new answer "
    "from now on. We keep a record that you changed it, and when, and nothing else."
)
