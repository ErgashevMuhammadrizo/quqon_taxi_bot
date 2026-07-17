from core.command_parsing import parse_admin_command_args, parse_channel_registration_args


def test_admin_defaults_to_viewer_role():
    assert parse_admin_command_args("123456789") == (123456789, "viewer")


def test_admin_accepts_explicit_role():
    assert parse_admin_command_args("123456789 moderator") == (123456789, "moderator")


def test_admin_uses_reply_user_when_no_args_provided():
    assert parse_admin_command_args("", reply_user_id=987654321) == (987654321, "viewer")


def test_channel_registration_supports_optional_alert_chat():
    assert parse_channel_registration_args("-1001234567 -1009876543") == (-1001234567, -1009876543)


def test_channel_registration_defaults_alert_chat_to_none():
    assert parse_channel_registration_args("-1001234567") == (-1001234567, None)
