def match_brace(text: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Return the closing delimiter index, skipping comments and quoted strings."""
    depth = 0
    index = open_idx
    while index < len(text):
        char = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close == -1 else close + 2
            continue
        string_end = _skip_string_literal(text, index)
        if string_end is not None:
            index = string_end
            continue
        if char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text) - 1


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on a separator outside quotes and nested delimiters."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    previous = ""
    index = 0
    while index < len(text):
        string_end = _skip_string_literal(text, index)
        if string_end is not None:
            current.append(text[index:string_end])
            index = string_end
            previous = text[index - 1]
            continue
        char = text[index]
        if char in "<([{":
            depth += 1
        elif char in ">)]}" and not (char == ">" and previous == "="):
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
            previous = char
            index += 1
            continue
        current.append(char)
        previous = char
        index += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _skip_string_literal(text: str, start: int) -> int | None:
    """Return the index after a string/character literal starting at start."""
    if start >= len(text):
        return None

    prefix_end = start
    while prefix_end < len(text) and text[prefix_end] in "@$":
        prefix_end += 1
    if prefix_end == start:
        quote_index = start
    elif prefix_end < len(text) and text[prefix_end] == '"':
        quote_index = prefix_end
    else:
        return None

    quote = text[quote_index]
    if quote not in "\"'`":
        return None

    if quote == '"':
        quote_count = 1
        while quote_index + quote_count < len(text) and text[quote_index + quote_count] == '"':
            quote_count += 1
        if quote_count >= 3:
            terminator = '"' * quote_count
            close = text.find(terminator, quote_index + quote_count)
            return len(text) if close == -1 else close + quote_count
    else:
        quote_count = 1

    verbatim = "@" in text[start:quote_index]
    index = quote_index + quote_count
    while index < len(text):
        if verbatim and quote == '"' and text.startswith('""', index):
            index += 2
            continue
        if not verbatim and text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return len(text)


def skip_string_literal(text: str, start: int) -> int | None:
    """Return the end offset of a C#/TypeScript string beginning at start."""
    return _skip_string_literal(text, start)


def leading_block(text: str, line_start: int) -> tuple[int, list[str]]:
    """Walk upward over attached attributes, decorators, and line comments."""
    start = line_start
    attributes: list[str] = []
    while start > 0:
        previous_start = text.rfind("\n", 0, start - 1) + 1
        line = text[previous_start:start - 1].strip()
        if line.startswith("[") or line.startswith("@"):
            attributes.append(line)
        elif not line.startswith("//"):
            break
        start = previous_start
    return start, attributes[::-1]