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


def test_redact_value_recurses_and_leaves_non_strings():
    out = redact.redact_value(
        {"i": 7, "selector": {"text": "code 123456"}, "xs": [1, "a@b.co"]}
    )
    assert out["i"] == 7
    assert out["selector"]["text"] == "code [REDACTED]"
    assert out["xs"][0] == 1 and "[REDACTED]" in out["xs"][1]
