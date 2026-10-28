"""Pascal qualified-parent and interface/implementation dedupe helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tree_sitter import Node

from ..extractors import node_text

if TYPE_CHECKING:
    from ..models import Symbol

_WHITESPACE_RE = re.compile(r"\s+")


def _qualified_pascal_parent(name_node: Node, src: str) -> str | None:
    """Return the owning class for a Pascal out-of-line method header.

    In ``function TCalculator.Add(...)`` the captured name is the
    ``genericDot``'s ``rhs`` and the class is its ``lhs``. Nesting cannot find
    it: the ``defProc`` sits in the implementation section, outside the
    class's ``declType``. Handles the plain and the generic-method
    (``rhs: genericTpl``) shapes. Reads with ``node_text`` because slicing a
    decoded ``str`` by byte offsets misaligns on non-ASCII identifiers.

    Returns ``None`` for a free function/procedure.
    """
    parent = name_node.parent
    if parent is not None and parent.type == "genericTpl":
        parent = parent.parent
    if parent is None or parent.type != "genericDot":
        return None
    lhs = parent.child_by_field_name("lhs")
    if lhs is None:
        return None
    text = node_text(lhs, src).strip()
    return text or None


_SECTION_VISIBILITY = {
    "kPrivate": "private",
    "kProtected": "protected",
    "kPublic": "public",
    "kPublished": "public",
}


def _pascal_visibility(def_node: Node) -> str:
    """Who can name the declaration at *def_node*.

    A class member takes its ``declSection`` keyword (``strict private`` is
    private); one before any section keyword is public. Outside a class, a
    declaration in the unit's ``implementation`` section is private to the
    unit: only one in ``interface`` is exported. A routine defined in
    ``implementation`` takes the visibility of its ``interface`` declaration
    in :func:`_dedupe_pascal_interface_symbols`.
    """
    node = def_node.parent
    while node is not None:
        if node.type == "declSection":
            for child in node.children:
                found = _SECTION_VISIBILITY.get(child.type)
                if found is not None:
                    return found
            return "public"
        if node.type in ("declClass", "declIntf", "declHelper"):
            return "public"
        if node.type == "implementation":
            return "private"
        if node.type == "interface":
            return "public"
        node = node.parent
    return "public"


def _dedupe_pascal_interface_symbols(
    symbols: list[Symbol], node_types: list[str]
) -> list[Symbol]:
    """Drop an interface-section method signature once its implementation
    is also present, so the two don't become two graph nodes for one method.

    Pascal declares a signature in the ``interface`` section (``declProc``,
    no body) and the body in ``implementation`` (``defProc``). Once both
    resolve to one parent, only the ``defProc`` is kept, since it carries the
    body ``get_symbol`` should return.

    Keyed on the normalized signature (see ``_pascal_dedupe_key``) rather than
    the name, so an interface-only ``overload;`` sibling survives when a
    different overload of the same name is implemented. Parameter names need
    not match between the two halves in Pascal, so a renamed parameter still
    defeats the dedupe and leaves both symbols.

    The body takes its declaration's visibility. A declaration that lost its
    class (an error-recovered class body leaves later members parentless) is
    paired with the one method body of that signature when no free routine
    has it; a method body with no declaration found is public.
    """
    pairs = list(zip(symbols, node_types, strict=True))
    impl_keys = {_pascal_dedupe_key(s.parent_name, s.signature) for s, nt in pairs if nt == "defProc"}
    method_bodies: dict[str, list[Symbol]] = {}
    for s, nt in pairs:
        if nt == "defProc" and s.parent_name:
            method_bodies.setdefault(_pascal_dedupe_key(None, s.signature)[1], []).append(s)

    declared: dict[tuple[str | None, str], str] = {}
    orphaned: set[int] = set()
    for s, nt in pairs:
        if nt != "declProc":
            continue
        key = _pascal_dedupe_key(s.parent_name, s.signature)
        declared[key] = s.visibility
        bodies = method_bodies.get(key[1], [])
        if s.parent_name is None and key not in impl_keys and len(bodies) == 1:
            orphaned.add(id(s))
            declared[_pascal_dedupe_key(bodies[0].parent_name, bodies[0].signature)] = s.visibility
    for s, nt in pairs:
        if nt == "defProc":
            key = _pascal_dedupe_key(s.parent_name, s.signature)
            fallback = "public" if s.parent_name else s.visibility
            s.visibility = declared.get(key, fallback)  # type: ignore[assignment]
    return [
        s
        for s, nt in pairs
        if not (
            nt == "declProc"
            and (_pascal_dedupe_key(s.parent_name, s.signature) in impl_keys or id(s) in orphaned)
        )
    ]


def _pascal_dedupe_key(parent_name: str | None, signature: str) -> tuple[str | None, str]:
    """Normalize a (parent, signature) pair for Pascal's interface/impl dedup.

    Whitespace-insensitive, because the two halves wrap long parameter lists
    differently, and case-insensitive, because Pascal identifiers are.
    """
    parent_key = parent_name.lower() if parent_name else None
    sig_key = _WHITESPACE_RE.sub("", signature).lower()
    return (parent_key, sig_key)
