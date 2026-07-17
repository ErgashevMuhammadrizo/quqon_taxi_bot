from __future__ import annotations


def parse_admin_command_args(args: str | None, reply_user_id: int | None = None) -> tuple[int, str]:
    """Parse /add_admin arguments in a friendly way.

    Supported forms:
    - /add_admin 123456789
    - /add_admin 123456789 moderator
    - /add_admin (reply to a user message)
    """
    if not args:
        if reply_user_id is None:
            raise ValueError("No admin target provided")
        return reply_user_id, "viewer"

    parts = args.strip().split()
    if not parts:
        if reply_user_id is None:
            raise ValueError("No admin target provided")
        return reply_user_id, "viewer"

    if len(parts) == 1 and parts[0].isdigit():
        return int(parts[0]), "viewer"

    if len(parts) == 2 and parts[0].isdigit():
        return int(parts[0]), parts[1].lower()

    raise ValueError("Use: /add_admin <user_id> [role]")


def parse_channel_registration_args(args: str | None) -> tuple[int, int | None]:
    """Parse /protect_channel arguments.

    Supported forms:
    - /protect_channel -1001234567890
    - /protect_channel -1001234567890 -1009876543210
    """
    if not args:
        raise ValueError("Use: /protect_channel <channel_id> [alert_chat_id]")

    parts = args.strip().split()
    if not parts:
        raise ValueError("Use: /protect_channel <channel_id> [alert_chat_id]")

    if len(parts) == 1 and parts[0].lstrip("-").isdigit():
        return int(parts[0]), None

    if len(parts) == 2 and all(p.lstrip("-").isdigit() for p in parts):
        return int(parts[0]), int(parts[1])

    raise ValueError("Use: /protect_channel <channel_id> [alert_chat_id]")
