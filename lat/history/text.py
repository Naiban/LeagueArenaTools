"""Riot text markup -> plain text for item and augment tooltips."""

import re


_TAG_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*/?>")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")


def clean_game_text(text):
    """Strip Riot markup so descriptions read naturally in a tooltip."""
    if not isinstance(text, str):
        return ""
    text = _TEMPLATE_RE.sub("", text)
    text = _TAG_BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


_KNOWN_STAT_TOKENS = {
    "AP": "AP", "AD": "AD", "Health": "HP", "Armor": "Armor",
    "MagicResist": "MR", "AttackSpeed": "Attack Speed",
    "MoveSpeed": "Move Speed", "AbilityHaste": "Haste",
    "AbilityPower": "AP", "Lifesteal": "Lifesteal", "OmniVamp": "Omni Vamp",
    "Mana": "Mana", "Energy": "Energy", "CritChance": "Crit Chance",
}


def expand_augment_description(text, data_values, item_names=None):
    """Replace scaling placeholders and item refs with real numbers/names."""
    if not isinstance(text, str):
        return ""
    values = data_values if isinstance(data_values, dict) else {}

    def format_number(raw):
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if abs(number) < 1:
            return f"{int(round(number * 100))}%"
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")

    def replace_scaled(match):
        key = match.group(1)
        entries = values.get(key)
        if not isinstance(entries, list) or not entries:
            return ""
        numbers = []
        for entry in entries:
            formatted = format_number(entry)
            if formatted is not None:
                numbers.append(formatted)
        deduped = [n for i, n in enumerate(numbers)
                   if i == 0 or n != numbers[i - 1]]
        if not deduped:
            return ""
        result = "/".join(deduped)
        if len(entries) > 1:
            result += f" ({len(entries)} levels)"
        return result

    def replace_item_ref(match):
        try:
            item_id = int(match.group(1))
        except (TypeError, ValueError):
            return ""
        name = (item_names or {}).get(item_id)
        return name or ""

    def replace_token(match):
        token = match.group(1)
        return _KNOWN_STAT_TOKENS.get(
            token, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", token))

    text = re.sub(r"%:([A-Za-z0-9_]+)%", replace_scaled, text)
    text = re.sub(r"%i:(\d+):i%", replace_item_ref, text)
    text = re.sub(r"@([A-Za-z]+)@", replace_token, text)
    # Strip any remaining Riot template tokens without touching literal %.
    text = re.sub(r"%(?:[a-zA-Z]:)?[A-Za-z0-9_]+(?::[a-zA-Z])?%", "", text)
    return text
