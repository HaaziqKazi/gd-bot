#!/usr/bin/env python3
"""Generate mod/src/snapshot_fields.hpp from the Geode binding headers.

WHY THIS IS GENERATED AND NOT HAND-WRITTEN
------------------------------------------
A state snapshot that is *almost* complete is worse than none: it produces
rollouts that silently diverge from reality, which is precisely the failure mode
that would make a Benchmark-B oracle emit wrong ground truth. A hand-curated
field list cannot be audited for completeness -- "did anyone remember
m_dashStartTime?" has no answer. A list mechanically derived from the binding
headers has exactly one failure mode (the parser missed a declaration form), and
that failure mode is checkable: the emitted per-class counts below are compared
against a raw `;`-count of the member region, and any dropped line is emitted as
a `GDRL_SNAP_UNPARSED_*` comment rather than silently discarded.

The C++ side then decides *per field* whether it is copyable, so the set of
fields that cannot be captured is also derived rather than asserted. See
snapshot.cpp.

Source of truth is the ARM binding headers, because that is what this build
compiles against on this machine. The generated file names the binding version
it came from so a bindings bump that changes a class is visible in the diff.

Usage:
    python3 mod/tools/gen_snapshot_fields.py [--bindings DIR] [--out FILE]
"""

import argparse
import os
import re
import sys

# The classes whose state the snapshot facility captures. Each entry is
# (header stem, class name). Order is the order they appear in the output.
#
# PlayLayer derives from GJBaseGameLayer and PlayerObject from GameObject, so
# both halves of each pair are needed: a generated header declares only the
# members a class introduces itself.
CLASSES = [
    ("GameObject", "GameObject"),
    ("PlayerObject", "PlayerObject"),
    ("GJBaseGameLayer", "GJBaseGameLayer"),
    ("PlayLayer", "PlayLayer"),
    ("GJEffectManager", "GJEffectManager"),
]

# A member declaration in a generated binding header looks like
#     "    gd::vector<GameObject*> m_visibleObjects;"
# i.e. four-space indented, no parentheses, ends in `;`.
MEMBER_RE = re.compile(r"^ {4}([A-Za-z_][^;]*?[ >*&])([A-Za-z_]\w*)\s*;\s*$")

# Lines that look like members but are not.
REJECT_PREFIXES = (
    "static ", "using ", "typedef ", "friend ", "virtual ", "template",
    "return ", "public", "private", "protected", "GEODE_", "constexpr ",
    "inline ", "class ", "struct ", "enum ", "union ",
)


def strip_block_comments(text: str) -> str:
    """Remove /* ... */ comments, preserving line count so numbers stay useful."""
    out = []
    i = 0
    while True:
        start = text.find("/*", i)
        if start < 0:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find("*/", start + 2)
        if end < 0:
            break
        out.append("\n" * text.count("\n", start, end + 2))
        i = end + 2
    return "".join(out)


def class_body(text: str, name: str):
    """Return the text between `class <name> ... {` and the closing `};`."""
    m = re.search(r"^class %s\b[^{]*\{" % re.escape(name), text, re.M)
    if not m:
        raise SystemExit("could not find `class %s` in the header" % name)
    depth = 1
    i = m.end()
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[m.end():i - 1]


def parse_members(body: str):
    """Return (members, unparsed). `members` is a list of field names in
    declaration order; `unparsed` holds every line that ended in `;`, had no
    parentheses, and still did not yield a member -- the audit trail."""
    members, unparsed, seen = [], [], set()
    for raw in body.split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("*"):
            continue
        if s.startswith("[["):          # attributes such as [[renamed_from(...)]]
            continue
        if "(" in s or ")" in s:        # functions, GEODE_PAD, macros
            continue
        if not s.endswith(";"):
            continue
        if s.startswith(REJECT_PREFIXES):
            continue
        m = MEMBER_RE.match(line)
        if not m:
            unparsed.append(s)
            continue
        nm = m.group(2)
        if nm in seen:                  # defensive; duplicates would break the struct
            unparsed.append(s + "   // DUPLICATE NAME")
            continue
        seen.add(nm)
        members.append(nm)
    return members, unparsed


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bindings", default=os.path.join(
        root, "mod", "build", "bindings", "bindings", "Geode", "binding_arm"))
    ap.add_argument("--out", default=os.path.join(root, "mod", "src", "snapshot_fields.hpp"))
    ap.add_argument("--version", default="2.2081")
    args = ap.parse_args()

    if not os.path.isdir(args.bindings):
        print("bindings dir not found: %s" % args.bindings, file=sys.stderr)
        print("build the mod once so codegen has run, then retry.", file=sys.stderr)
        return 1

    chunks = []
    summary = []
    for stem, cls in CLASSES:
        path = os.path.join(args.bindings, stem + ".hpp")
        with open(path, "r", encoding="utf-8") as fh:
            text = strip_block_comments(fh.read())
        members, unparsed = parse_members(class_body(text, cls))
        summary.append((cls, len(members), len(unparsed)))

        lines = ["// %s -- %d members declared by this class (bases excluded)"
                 % (cls, len(members))]
        if unparsed:
            lines.append("// %d line(s) in the member region did NOT parse as a member."
                         % len(unparsed))
            lines.append("// They are NOT captured. Listed verbatim so the gap is visible:")
            for u in unparsed:
                lines.append("//   %s" % u)
        lines.append("#define GDRL_SNAP_FIELDS_%s(X) \\" % cls)
        for i, nm in enumerate(members):
            lines.append("    X(%s)%s" % (nm, " \\" if i + 1 < len(members) else ""))
        if not members:
            lines.append("    /* none */")
        chunks.append("\n".join(lines))

    header = [
        "// GENERATED FILE -- DO NOT EDIT.",
        "//",
        "// Produced by mod/tools/gen_snapshot_fields.py from the GD %s ARM binding" % args.version,
        "// headers (mod/build/bindings/bindings/Geode/binding_arm/<Class>.hpp).",
        "// Regenerate with:  python3 mod/tools/gen_snapshot_fields.py",
        "//",
        "// Each macro expands X(name) once per data member the class declares ITSELF;",
        "// base-class members are covered by that base's own macro. The list is",
        "// mechanical on purpose -- a hand-written one cannot be audited for",
        "// completeness, and an incomplete snapshot is the failure mode that makes a",
        "// rollout diverge silently. Anything the parser could not read is recorded",
        "// as a comment above the macro rather than dropped.",
        "//",
        "// Counts at generation time:",
    ]
    for cls, n, u in summary:
        header.append("//   %-18s %4d members, %d unparsed" % (cls, n, u))
    header += ["", "#pragma once", ""]

    out = "\n".join(header) + "\n" + "\n\n".join(chunks) + "\n"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(out)
    for cls, n, u in summary:
        print("%-18s %4d members  %d unparsed" % (cls, n, u))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
