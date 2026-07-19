import re


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_identifier(value: str, label: str = "identifier") -> str:
    """校验会参与 workspace 路径拼接的外部标识符。"""

    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            f"Invalid {label}: use 1-128 ASCII letters, numbers, dots, underscores, or hyphens; "
            "the first character must be alphanumeric."
        )
    return value
