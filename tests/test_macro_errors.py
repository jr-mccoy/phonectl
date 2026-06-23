from phonectl import errors
import phonectl.macro as macro


def test_macro_error_codes():
    assert errors.MacroValidationError("x").code == "macro_invalid"
    assert errors.MacroValidationError("x").requires_user is True
    assert errors.MacroCancelledError().code == "macro_cancelled"


def test_step_kind_sets_are_disjoint_and_populated():
    assert "tap" in macro.PHONE_VERBS
    assert "if" in macro.CONTROL_STEPS and "for_each" in macro.CONTROL_STEPS
    assert macro.PHONE_VERBS.isdisjoint(macro.CONTROL_STEPS)
