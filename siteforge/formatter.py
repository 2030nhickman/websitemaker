"""Small, dependency-free prettifiers for the Code tab's "Format" buttons.

They re-indent rather than reflow: line breaks are added around block-level
tags (HTML), braces and declarations (CSS) and braces/semicolons (JS), while
strings, comments, ``<pre>``/``<script>``/``<style>``/``<textarea>`` content
and regular expressions are left untouched.
"""

from __future__ import annotations

import re

INDENT = "  "

_BLOCK_TAGS = set("""address article aside blockquote body canvas dd details dialog div dl dt
fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 head header hr html iframe li link
main meta nav ol option p script section select style summary table tbody td tfoot th thead
title tr ul button textarea input label video audio source img br""".split())
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())
_RAW = ("pre", "script", "style", "textarea")
_INLINE_OK = set("a abbr b bdi bdo cite code data dfn em i kbd mark q s samp small span strong "
                 "sub sup time u var".split())

_TOKEN_RE = re.compile(r"<!--.*?-->|<!\w[^>]*>|</?[a-zA-Z][^>]*?/?>|[^<]+|<", re.S)
_TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][\w:-]*)")


def format_html(source: str) -> str:
    tokens = _TOKEN_RE.findall(source)
    out: list[str] = []
    depth = 0
    line = ""
    i = 0

    def flush():
        nonlocal line
        if line.strip():
            out.append(INDENT * depth + line.strip())
        line = ""

    while i < len(tokens):
        tok = tokens[i]
        m = _TAG_NAME_RE.match(tok)
        name = m.group(1).lower() if m else None
        is_close = tok.startswith("</")
        if name in _RAW and not is_close:
            # Copy raw content verbatim up to the matching close tag.
            flush()
            j = i + 1
            raw = ""
            while j < len(tokens) and not re.match(rf"</\s*{name}\s*>", tokens[j], re.I):
                raw += tokens[j]
                j += 1
            close = tokens[j] if j < len(tokens) else ""
            if "\n" in raw.strip("\n") and name in ("script", "style"):
                out.append(INDENT * depth + tok)
                body = _reindent_block(raw, INDENT * (depth + 1))
                if body:
                    out.append(body)
                out.append(INDENT * depth + close)
            else:
                out.append(INDENT * depth + tok + raw + close)
            i = j + 1
            continue
        if name is None or tok.startswith("<!--") or tok.startswith("<!"):
            if tok.startswith("<!"):
                flush()
                out.append(INDENT * depth + tok.strip())
            else:
                text = re.sub(r"\s+", " ", tok)
                line += text if line else text.lstrip()
            i += 1
            continue
        if name in _INLINE_OK or (name not in _BLOCK_TAGS):
            line += tok
            i += 1
            continue
        if is_close:
            flush()
            depth = max(0, depth - 1)
            if out and not out[-1].strip().startswith("</") and _opens(out[-1], name) and \
                    out[-1].startswith(INDENT * depth + "<"):
                out[-1] = out[-1] + tok  # keep <p>text</p> on one line
            else:
                out.append(INDENT * depth + tok)
            i += 1
            continue
        flush()
        # Keep short elements that contain only inline content on one line.
        j, inline, ok = i + 1, "", True
        while j < len(tokens):
            nxt = tokens[j]
            nm = _TAG_NAME_RE.match(nxt)
            nname = nm.group(1).lower() if nm else None
            if nname == name and nxt.startswith("</"):
                break
            if nname and nname not in _INLINE_OK:
                ok = False
                break
            inline += nxt
            j += 1
        else:
            ok = False
        if name in _VOID or tok.endswith("/>"):
            out.append(INDENT * depth + tok)
            i += 1
        elif ok and len(inline) + len(tok) < 100 and "\n" not in inline.strip():
            out.append(INDENT * depth + tok + re.sub(r"\s+", " ", inline).strip() + tokens[j])
            i = j + 1
        else:
            out.append(INDENT * depth + tok)
            depth += 1
            i += 1
    flush()
    return "\n".join(out) + "\n"


def _opens(line: str, name: str) -> bool:
    return bool(re.match(rf"\s*<{name}[\s>]", line))


def _reindent_block(text: str, indent: str) -> str:
    lines = [ln.rstrip() for ln in text.strip("\n").split("\n")]
    widths = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(indent + ln[cut:] if ln.strip() else "" for ln in lines)


def _split_code(source: str, regex_ok: bool):
    """Yield (kind, text) with kind in {code, string, comment, regex}."""
    i, n = 0, len(source)
    buf = ""
    prev_sig = ""
    while i < n:
        ch = source[i]
        two = source[i:i + 2]
        if two == "/*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            if buf:
                yield "code", buf
                buf = ""
            yield "comment", source[i:end]
            i = end
            continue
        if regex_ok and two == "//":
            end = source.find("\n", i)
            end = n if end < 0 else end
            if buf:
                yield "code", buf
                buf = ""
            yield "comment", source[i:end]
            i = end
            continue
        if ch in "\"'`":
            j = i + 1
            while j < n and source[j] != ch:
                j += 2 if source[j] == "\\" else 1
            if buf:
                yield "code", buf
                buf = ""
            yield "string", source[i:j + 1]
            prev_sig = "a"
            i = j + 1
            continue
        if regex_ok and ch == "/" and (prev_sig in "(,=:[!&|?{};+-*%<>~^" or prev_sig == ""):
            j = i + 1
            in_class = False
            while j < n and source[j] != "\n":
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                j += 1
            if j < n and source[j] == "/":
                j += 1
                while j < n and source[j].isalpha():
                    j += 1
                if buf:
                    yield "code", buf
                    buf = ""
                yield "regex", source[i:j]
                prev_sig = "a"
                i = j
                continue
        buf += ch
        if not ch.isspace():
            prev_sig = ch if not (ch.isalnum() or ch in "_$)]") else "a"
            if source[max(0, i - 5):i + 1].endswith("return"):
                prev_sig = "("
        i += 1
    if buf:
        yield "code", buf


def _css_decl(text: str, depth: int) -> str:
    decl = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*:\s*", ": ", decl, count=1) if depth else decl


def format_css(source: str) -> str:
    out, depth, line = [], 0, ""

    def emit(text):
        if text.strip():
            out.append(INDENT * depth + text.strip())

    for kind, text in _split_code(source, regex_ok=False):
        if kind != "code":
            if kind == "comment" and not line.strip():
                emit(text)
            else:
                line += text
            continue
        for ch in text:
            if ch == "{":
                emit(re.sub(r"\s+", " ", line).strip() + " {")
                line = ""
                depth += 1
            elif ch == "}":
                if line.strip():
                    emit(_css_decl(line, depth) + ("" if line.strip().endswith(";") else ";"))
                line = ""
                depth = max(0, depth - 1)
                emit("}")
                if depth == 0:
                    out.append("")
            elif ch == ";":
                emit(_css_decl(line, depth) + ";")
                line = ""
            elif ch == "\n":
                line += " "
            else:
                line += ch
    emit(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def format_js(source: str) -> str:
    """Re-indent JavaScript by bracket depth; one statement per line."""
    out, depth, line = [], 0, ""
    paren = 0

    def emit():
        nonlocal line
        text = line.strip()
        if text:
            out.append(INDENT * depth + text)
        line = ""

    for kind, text in _split_code(source, regex_ok=True):
        if kind == "comment":
            if text.startswith("//"):
                if line.strip():
                    line += " " + text
                    emit()
                else:
                    line = text
                    emit()
            else:
                emit()
                for cline in text.split("\n"):
                    out.append(INDENT * depth + cline.strip() if not cline.strip().startswith("*")
                               else INDENT * depth + " " + cline.strip())
            continue
        if kind in ("string", "regex"):
            line += text
            continue
        for ch in text:
            if ch in "([":
                paren += 1
                line += ch
            elif ch in ")]":
                paren = max(0, paren - 1)
                line += ch
            elif ch == "{":
                line = line.rstrip() + " {" if line.strip() else "{"
                emit()
                depth += 1
            elif ch == "}":
                emit()
                depth = max(0, depth - 1)
                line = "}"
            elif ch == ";" and paren == 0:
                line += ";"
                emit()
            elif ch == "\n":
                if line.strip() == "}":
                    emit()
                elif line.strip() and paren == 0 and not line.rstrip().endswith((",", "(", "=", "+", "&&", "||", "?", ":", "." )):
                    emit()
                else:
                    line += " "
            else:
                if line.strip() == "}" and not ch.isspace() and ch not in ",;)":
                    if ch.isalpha():
                        line += " "  # "} else", "} catch"
                    else:
                        emit()
                if ch.isspace() and line.endswith(" "):
                    continue
                line += ch
    emit()
    # Join "}" with a following "else"/"catch"/"finally"/"while" and closing punctuation.
    joined: list[str] = []
    for ln in out:
        s = ln.strip()
        if joined and joined[-1].strip() == "}" and (
                re.match(r"(else|catch|finally|while)\b", s) or s[:1] in ",;)"):
            joined[-1] = joined[-1] + ("" if s[:1] in ",;)" else " ") + s
        else:
            joined.append(ln)
    text = "\n".join(joined)
    text = re.sub(r"\}\s+(else|catch|finally)", r"} \1", text)
    return text + "\n"
