"""Source-span aware HTML parsing.

The HTML text in the Code tab is the single source of truth for a page.
Components dropped from the palette are ordinary HTML elements carrying a
``data-sf-id`` attribute.  This module parses the HTML into a light tree in
which every node remembers where it lives in the source text, so the editor
can select, edit, move and delete elements by rewriting exact spans of the
text instead of regenerating the whole document (which would destroy the
user's hand-written markup and formatting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

SF_ID_ATTR = "data-sf-id"


@dataclass
class Node:
    tag: str
    attrs: dict
    start: int  # offset of "<tag"
    start_end: int  # offset just after the start tag's ">"
    end_start: int = -1  # offset of "</tag" (== start_end for void tags)
    end: int = -1  # offset just after the end tag's ">"
    children: list = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    @property
    def sf_id(self) -> str | None:
        return self.attrs.get(SF_ID_ATTR)

    def inner(self, source: str) -> str:
        return source[self.start_end:self.end_start]

    def outer(self, source: str) -> str:
        return source[self.start:self.end]

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find_first(self, tag: str) -> "Node | None":
        for node in self.walk():
            if node is not self and node.tag == tag:
                return node
        return None


class _SpanParser(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self._line_starts = [0]
        for match in re.finditer("\n", source):
            self._line_starts.append(match.end())
        self.root = Node("#root", {}, 0, 0, len(source), len(source))
        self._stack = [self.root]

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _open(self, tag, attrs, self_closing=False):
        start = self._offset()
        text = self.get_starttag_text() or ""
        start_end = start + len(text)
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, start, start_end)
        parent = self._stack[-1]
        node.parent = parent
        parent.children.append(node)
        if self_closing or tag in VOID_TAGS:
            node.end_start = start_end
            node.end = start_end
        else:
            self._stack.append(node)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        pos = self._offset()
        close = self.source.find(">", pos)
        end = len(self.source) if close < 0 else close + 1
        # Find the nearest open element with this tag; tolerate stray end tags.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                break
        else:
            return
        while len(self._stack) > i:
            node = self._stack.pop()
            if node.tag == tag and len(self._stack) == i:
                node.end_start, node.end = pos, end
            else:  # implicitly closed element (e.g. unclosed <p>)
                node.end_start = node.end = pos

    def finish(self) -> Node:
        self.close()
        while len(self._stack) > 1:
            node = self._stack.pop()
            node.end_start = node.end = len(self.source)
        return self.root


def parse(source: str) -> Node:
    parser = _SpanParser(source)
    parser.feed(source)
    return parser.finish()


class HtmlDocument:
    """Parsed view of an HTML fragment with helpers for span edits."""

    def __init__(self, source: str):
        self.source = source
        self.root = parse(source)

    def sf_nodes(self):
        return [n for n in self.root.walk() if n.sf_id]

    def find(self, sf_id: str) -> Node | None:
        for node in self.root.walk():
            if node.sf_id == sf_id:
                return node
        return None

    def sf_parent(self, node: Node) -> Node | None:
        parent = node.parent
        while parent is not None and parent.tag != "#root":
            if parent.sf_id:
                return parent
            parent = parent.parent
        return None

    def element_tree(self, node: Node | None = None) -> list:
        """Nested list of {sf_id, tag, children} for sf elements only."""
        node = node or self.root
        result = []
        for child in node.children:
            if child.sf_id:
                result.append({"sf_id": child.sf_id, "node": child,
                               "children": self.element_tree(child)})
            else:
                result.extend(self.element_tree(child))
        return result

    def _line_indent(self, offset: int) -> str:
        line_start = self.source.rfind("\n", 0, offset) + 1
        match = re.match(r"[ \t]*", self.source[line_start:offset])
        return match.group(0) if match else ""

    def _starts_line(self, offset: int) -> bool:
        line_start = self.source.rfind("\n", 0, offset) + 1
        return self.source[line_start:offset].strip() == ""

    def replace(self, sf_id: str, snippet: str) -> str:
        node = self.find(sf_id)
        if node is None:
            return self.source
        indent = self._line_indent(node.start)
        snippet = _indent_continuation(snippet, indent)
        return self.source[:node.start] + snippet + self.source[node.end:]

    def remove(self, sf_id: str) -> str:
        node = self.find(sf_id)
        if node is None:
            return self.source
        start, end = node.start, node.end
        # Remove the whole line when the element sits alone on it.
        line_start = self.source.rfind("\n", 0, start) + 1
        line_end = self.source.find("\n", end)
        line_end = len(self.source) if line_end < 0 else line_end
        if (self.source[line_start:start].strip() == ""
                and self.source[end:line_end].strip() == ""):
            start = line_start
            end = min(line_end + 1, len(self.source))
        return self.source[:start] + self.source[end:]

    def insert(self, snippet: str, target_id: str | None = None, where: str = "after") -> str:
        """Insert *snippet* relative to element *target_id*.

        ``where`` is ``before``, ``after`` or ``inside`` (append as last child).
        With no target the snippet is appended to the end of the document.
        """
        node = self.find(target_id) if target_id else None
        src = self.source
        if node is None:
            body = src.rstrip("\n")
            sep = "\n" if body else ""
            return body + sep + snippet + "\n"
        indent = self._line_indent(node.start)
        if where == "before":
            if self._starts_line(node.start):
                at = src.rfind("\n", 0, node.start) + 1
                text = indent + _indent_continuation(snippet, indent) + "\n"
            else:
                at = node.start
                text = snippet
            return src[:at] + text + src[at:]
        if where == "inside" and node.tag not in VOID_TAGS:
            inner = node.inner(src)
            child_indent = indent + "  "
            body = _indent_continuation(snippet, child_indent)
            if "\n" in inner or inner.strip() == "":
                trimmed = inner.rstrip()
                lead = "" if trimmed.strip() == "" else trimmed
                new_inner = (lead + "\n" + child_indent + body + "\n" + indent)
                return src[:node.start_end] + new_inner + src[node.end_start:]
            return src[:node.end_start] + " " + body + src[node.end_start:]
        # after
        text = "\n" + indent + _indent_continuation(snippet, indent)
        return src[:node.end] + text + src[node.end:]

    def move(self, sf_id: str, direction: int) -> str:
        """Swap element *sf_id* with its previous (-1) or next (+1) sf sibling."""
        node = self.find(sf_id)
        if node is None or node.parent is None:
            return self.source
        siblings = [c for c in node.parent.children if c.sf_id]
        idx = siblings.index(node)
        other_idx = idx + direction
        if not 0 <= other_idx < len(siblings):
            return self.source
        a, b = sorted((node, siblings[other_idx]), key=lambda n: n.start)
        src = self.source
        return (src[:a.start] + b.outer(src) + src[a.end:b.start]
                + a.outer(src) + src[b.end:])


def _indent_continuation(snippet: str, indent: str) -> str:
    lines = snippet.split("\n")
    return "\n".join([lines[0]] + [indent + line if line else line for line in lines[1:]])


_SF_ATTR_RE = re.compile(r'\s+data-sf-id="[^"]*"')


def strip_editor_attributes(html: str) -> str:
    """Remove the editor-only ``data-sf-id`` attributes for clean exports."""
    return _SF_ATTR_RE.sub("", html)
