from phonectl import redact


def test_redact_text_scrubs_otp_email_phone_card_and_token():
    assert redact.redact_text("your code is 482913") == "your code is [REDACTED]"
    assert "[REDACTED]" in redact.redact_text("mail me at a.b@example.com")
    assert "[REDACTED]" in redact.redact_text("call +1 (415) 555-2671 now")
    assert "[REDACTED]" in redact.redact_text("card 4111111111111111")
    assert "[REDACTED]" in redact.redact_text("https://x.test/cb?token=abc123def")


def test_redact_text_keeps_benign_labels():
    assert redact.redact_text("Wi-Fi") == "Wi-Fi"
    assert redact.redact_text("Connected devices") == "Connected devices"


def test_redact_masks_high_entropy_token():
    # A bare high-entropy secret with no `token=` prefix must still be masked — e.g. the
    # companion pairing token (32 hex chars) or an API key pasted into a field. The `key=`
    # pattern alone misses these.
    hex_token = "3f9a1c7e5b2d4086af13e9c7d5b1a2f0"
    assert redact.redact_text(f"companion_token {hex_token}") == "companion_token [REDACTED]"
    assert "[REDACTED]" in redact.redact_text("Authorization: Bearer sk-Ab12Cd34Ef56Gh78Ij90")
    # Benign long identifiers must NOT be masked: letters-only class names (no digit) and
    # ordinary spaced prose stay intact.
    assert redact.redact_text("AccessibilityService") == "AccessibilityService"
    assert redact.redact_text("Connected devices and storage") == "Connected devices and storage"


def test_redact_value_recurses_and_leaves_non_strings():
    out = redact.redact_value(
        {"i": 7, "selector": {"text": "code 123456"}, "xs": [1, "a@b.co"]}
    )
    assert out["i"] == 7
    assert out["selector"]["text"] == "code [REDACTED]"
    assert out["xs"][0] == 1 and "[REDACTED]" in out["xs"][1]
