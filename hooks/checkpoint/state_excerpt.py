"""One bounded STATE excerpt for initial context and compact recovery."""

import re

from storage import guarded_read, utf8_prefix

READ_LIMIT = 1024 * 1024
CRITICAL = ("next move", "next action", "next step", "objective", "aligned",
            "constraints", "requirements", "in flight", "failures", "unresolved failures",
            "blockers", "tests", "verification", "proof", "dead ends")


def read_state(path):
    """Read at most 1 MiB total, retaining both the opening and latest sections."""
    head, complete = guarded_read(path, READ_LIMIT // 2)
    if head is None or complete:
        return head, complete
    tail, _ = guarded_read(path, READ_LIMIT // 2, tail=True)
    return head + b"\n\n" + (tail or b""), False


def heading(part):
    first = part.partition("\n")[0]
    return first[3:].strip().casefold() if first.startswith("## ") else ""


def sections_excerpt(raw, complete, budget, order):
    """Keep the newest details per section, with a fixed number of low-priority sections."""
    body = raw.decode("utf-8", errors="ignore" if not complete else "replace")
    sections = [part for part in re.split(r"(?im)(?=^## )", body) if part.strip()]
    preamble = []
    if sections and not sections[0].startswith("## "):
        preamble = [sections.pop(0)]
    priorities = list(dict.fromkeys(name.casefold() for name in order))
    priorities += [name for name in CRITICAL if name not in priorities]
    names = [heading(part) for part in sections]
    indices = []
    seen = set()
    for name in priorities:
        for index, title in enumerate(names):
            if title == name and index not in seen:
                indices.append(index)
                seen.add(index)
    indices.extend(index for index in range(len(sections)) if index not in seen)
    ordered = preamble + [sections[index] for index in indices]
    full = "\n\n".join(part.rstrip("\n") for part in ordered) + "\n" if ordered else ""
    if complete and len(full.encode()) <= budget:
        return full, False

    # Bound old notes, not distinct critical facts. Repeated priority headings
    # retain their latest section so old objectives cannot crowd out failures,
    # constraints or proof. Small complete records above keep every section.
    if len(ordered) > 12:
        latest = {heading(part): part for part in sections}
        important = [latest[name] for name in priorities if name in latest]
        other = [part for part in sections if heading(part) not in priorities]
        ordered = preamble + important
        ordered += other[:max(0, 12 - len(ordered))]
    headers = [part.split("\n", 1)[0] + "\n" if part.startswith("## ") else ""
               for part in ordered]
    content_budget = max(0, budget - sum(len(h.encode()) for h in headers)
                         - 2 * max(0, len(ordered) - 1))
    share = content_budget // max(1, len(ordered))
    pieces = []
    for number, (part, header) in enumerate(zip(ordered, headers)):
        content = part[len(header):]
        if len(content.encode()) <= share:
            excerpt = content
        elif preamble and number == 0:
            excerpt = ""
            for line in content.splitlines(keepends=True):
                if len((excerpt + line).encode()) > share:
                    break
                excerpt += line
            if not excerpt:
                excerpt = utf8_prefix(content, share)
        else:
            lines = content.splitlines(keepends=True)
            while lines and not lines[-1].strip():
                lines.pop()
            newest = []
            remaining = share
            for line in reversed(lines):
                if len(line.encode()) > remaining:
                    break
                newest.append(line)
                remaining -= len(line.encode())
            excerpt = "".join(reversed(newest))
            if not excerpt and lines and share >= 2:
                kept = lines[-1].rstrip("\r\n").encode()[-(share - 1):]
                excerpt = kept.decode("utf-8", "ignore") + "\n"
        pieces.append((header + excerpt).rstrip("\n"))
    return utf8_prefix("\n\n".join(pieces), budget), True


def state_excerpt(raw, complete, budget, order, *, plain_paragraphs=False):
    """Return (UTF-8-safe excerpt, truncated); callers budget their own wrappers."""
    if plain_paragraphs and not re.search(rb"(?m)^## ", raw):
        # Retain the existing paragraph contract for ordinary unsectioned notes.
        body = raw.decode("utf-8", errors="ignore" if not complete else "replace").replace("\r\n", "\n")
        paragraphs = re.split(r"\n[ \t]*\n", body)
        if not complete:
            paragraphs.pop()
        paragraphs = [part.strip() for part in paragraphs if part.strip()]
        shown = []
        for paragraph in paragraphs:
            candidate = "\n\n".join(shown + [paragraph])
            if len(candidate.encode()) > budget:
                break
            shown.append(paragraph)
        return "\n\n".join(shown), not complete or len(shown) != len(paragraphs)
    return sections_excerpt(raw, complete, budget, order)
