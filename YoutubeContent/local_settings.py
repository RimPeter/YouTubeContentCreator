"""Read local AI settings as data; never execute a private configuration file."""

import ast


def read_local_ai_settings(path):
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.strip().partition("=")
        name = name.strip()
        if not separator or name not in {"OPENAI_API_KEY", "OPENAI_RESEARCH_MODEL", "OPENAI_REACTION_MODEL"}:
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                continue
        if isinstance(value, str) and value.strip():
            values[name] = value.strip()
    return values
