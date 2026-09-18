"""The option vocabulary is a contract, so drift has to fail a test.

`SCREEN_FIELD_OPTIONS` is what refuses an option id the model invents, and
the frontend's own lists are what the patient actually sees. Those are the
same vocabulary held in two codebases, exported to
`contracts/intake-field-options.json` and asserted from both sides -- this
module checks the file matches the registry, and the UI's
`intakeFieldOptions.test.ts` checks its lists match the file.

Without both halves, adding an option on one side means the server drops
an id the screen offers, the card never ticks, and nothing reports why.
"""

import json
from pathlib import Path

import pytest

from app.core.constants import (
    BOOLEAN_SCREEN_FIELDS,
    SCREEN_FIELD_OPTIONS,
    SCREEN_FIELDS,
    IntakeScreen,
)

pytest.importorskip("app.core.constants")

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "intake-field-options.json"


def _exported() -> dict[str, object]:
    from scripts.export_field_options import build

    return build()


def test_contract_file_is_not_stale() -> None:
    """The committed export matches the registry it was generated from.

    Regenerate with `python scripts/export_field_options.py` when this
    fails. It failing means the frontend is being checked against a
    vocabulary the server no longer has.
    """
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing; run scripts/export_field_options.py"
    )
    assert json.loads(CONTRACT_PATH.read_text(encoding="utf-8")) == _exported()


def test_every_option_field_belongs_to_a_screen() -> None:
    """A field with options that no screen has is unreachable and a typo.

    `record_intake_details` checks the screen's field list first, so such
    an entry can never be filled -- it would look like a supported option
    set while dropping every value sent to it.
    """
    known = {field for fields in SCREEN_FIELDS.values() for field in fields}

    assert set(SCREEN_FIELD_OPTIONS) <= known


def test_option_ids_are_unique_within_a_field() -> None:
    """Duplicates would make the accepted-id order nondeterministic."""
    for field, options in SCREEN_FIELD_OPTIONS.items():
        assert len(set(options)) == len(options), field


def test_boolean_fields_are_exactly_the_yes_no_ones() -> None:
    """`BOOLEAN_SCREEN_FIELDS` is derived, so this pins what it derives to.

    These are the fields where a misread value is not a missing tick but
    an inverted clinical claim, and the set is used to reason about that --
    so it has to stay the same set the options describe.
    """
    assert {
        "no_known_allergies",
        "no_hospital_stays",
        "saw_other_provider",
        "had_recent_tests",
    } == BOOLEAN_SCREEN_FIELDS


def test_symptom_story_offers_no_options() -> None:
    """That screen is driven by the symptom tools, not by field options."""
    for field in SCREEN_FIELDS[IntakeScreen.SYMPTOM_STORY]:
        assert field not in SCREEN_FIELD_OPTIONS
