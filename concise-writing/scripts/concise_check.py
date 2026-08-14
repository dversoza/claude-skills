#!/usr/bin/env python3
"""PreToolUse gate: blocks verbose outward-facing writing.

Budgets and style rules: the concise-writing skill (SKILL.md; rule
sources in references/, worked rewrites in examples/).
Fail open on any internal error: never block work because this script broke.
Do not loosen thresholds to make a specific output pass; shorten the output.
"""

import json
import os
import re
import sys

SKILL = "~/.claude/skills/concise-writing"

EMOJI = re.compile("[\U0001f000-\U0001faff☀-⛿✀-➿️]")
DASH = re.compile("[–—]")
FILLER = re.compile(
    r"\b(comprehensive(?:ly)?|robust(?:ly|ness)?|seamless(?:ly)?"
    r"|leverag(?:e[sd]?|ing)|crucial(?:ly)?|cutting-edge|blazing"
    r"|effortless(?:ly)?|delve)\b"
    r"|it(?:'| i)s worth noting|in order to|please note"
    r"|as mentioned (?:above|earlier)",
    re.I,
)
BOLD = re.compile(r"\*\*[^*\n]+\*\*")
URL = re.compile(r"https?://\S+")
NARRATION = re.compile(
    r"\b(this (?:ensures|means|allows|will|is needed)"
    r"|we (?:now|then|also|first|need)|note that|as you can see"
    r"|now (?:we|that)|the following|this is (?:because|the))\b",
    re.I,
)
COMMENT_EXEMPT = re.compile(
    r"^#!|noqa|type:\s|pragma|ruff|pylint|eslint|shellcheck|fmt:|coding[:=]"
    r"|TODO|FIXME|-\*-"
)
TEMPLATE_BOILERPLATE = re.compile(r"^\s*(- If you added|\[Address these|## )")
CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|ref|refactor|test|chore|perf|revert)(\([^)]+\))?!?: \S"
)

CODE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".sh",
    ".zsh",
    ".tf",
    ".sql",
    ".rb",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".rs",
}
HASH_COMMENT = {".py", ".sh", ".zsh", ".tf", ".rb", ".sql"}
DOC_EXT = {".md", ".mdx", ".rst"}


def deny(violations):
    reason = "; ".join(violations) + f". Rewrite shorter per {SKILL}."
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def style(text, label):
    v = []
    if EMOJI.search(text):
        v.append(f"{label}: emoji")
    if DASH.search(text):
        v.append(f"{label}: em/en dash (use '-')")
    m = FILLER.search(text)
    if m:
        v.append(f"{label}: filler word '{m.group(0)}'")
    return v


def word_count(text):
    text = URL.sub("", text)
    lines = [l for l in text.splitlines() if not TEMPLATE_BOILERPLATE.match(l)]
    return len(re.findall(r"\S+", "\n".join(lines)))


def sentence_count(text):
    text = text.strip()
    if not text:
        return 0
    return len(re.findall(r"[.!?](?:\s|$)", text)) or 1


def nonblank_lines(text):
    return [l for l in text.splitlines() if l.strip()]


def heredocs(cmd):
    return [
        m.group(2)
        for m in re.finditer(
            r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?\s*\n(.*?)\n\s*\1(?=\s|$)", cmd, re.S
        )
    ]


def flag_values(cmd, flags):
    vals = []
    for f in flags:
        pat = re.escape(f) + r"[= ]\s*(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)')"
        for m in re.finditer(pat, cmd, re.S):
            vals.append(m.group(1) if m.group(1) is not None else m.group(2))
    return vals


def file_flag_content(cmd, flags):
    vals = []
    for f in flags:
        for m in re.finditer(re.escape(f) + r"[= ]\s*([^\s\"']+)", cmd):
            path = os.path.expanduser(m.group(1))
            if path != "-" and os.path.isfile(path):
                try:
                    with open(path) as fh:
                        vals.append(fh.read())
                except OSError:
                    pass
    return vals


def extract_message(cmd, value_flags, file_flags):
    docs = heredocs(cmd)
    if docs:
        return "\n\n".join(docs)
    vals = flag_values(cmd, value_flags)
    if vals:
        return "\n\n".join(vals)
    files = file_flag_content(cmd, file_flags)
    if files:
        return "\n\n".join(files)
    return None


def check_commit(cmd):
    msg = extract_message(cmd, ["-m", "--message"], ["-F", "--file"])
    if not msg:
        return []
    lines = msg.splitlines()
    subject = lines[0].strip()
    body = "\n".join(lines[1:])
    v = style(msg, "commit")
    if subject.startswith(("Merge", "fixup!", "squash!")):
        return v
    core = re.sub(r"\s*\[[A-Z]+-\d+\]\s*$", "", subject)
    if len(core) > 50:
        v.append(f"commit subject {len(core)} chars (max 50)")
    if not CONVENTIONAL.match(subject):
        v.append("commit subject not conventional type(scope): form")
    if subject.endswith("."):
        v.append("commit subject ends with period")
    body_lines = nonblank_lines(body)
    if len(body_lines) > 6:
        v.append(f"commit body {len(body_lines)} lines (max 6)")
    for l in body_lines:
        if len(l) > 72 and not URL.search(l):
            v.append("commit body line over 72 chars")
            break
    low = msg.lower()
    for banned in ("co-authored-by", "claude code", "generated with"):
        if banned in low:
            v.append(f"commit contains '{banned}'")
    return v


def check_pr_body(body, label="PR body"):
    if not body:
        return []
    v = style(body, label)
    w = word_count(body)
    if w > 150:
        v.append(f"{label} {w} words (max 150)")
    lines = body.splitlines()
    bullets = [
        l
        for l in lines
        if re.match(r"\s*[-*] ", l) and not TEMPLATE_BOILERPLATE.match(l)
    ]
    if len(bullets) > 6:
        v.append(f"{label}: {len(bullets)} bullets (max 6)")
    if any(re.match(r"\s+[-*] ", l) for l in lines):
        v.append(f"{label}: nested bullets")
    if BOLD.search(body):
        v.append(f"{label}: bold markup")
    return v


def check_short_comment(body, label, max_lines=2, max_words=80):
    if not body:
        return []
    v = style(body, label)
    lines = nonblank_lines(body)
    if len(lines) > max_lines:
        v.append(f"{label} {len(lines)} lines (max {max_lines})")
    w = word_count(body)
    if w > max_words:
        v.append(f"{label} {w} words (max {max_words})")
    return v


def check_review_summary(body, label="review summary"):
    if not body:
        return []
    v = style(body, label)
    s = sentence_count(body)
    if s > 3:
        v.append(f"{label} {s} sentences (max 3)")
    return v


def check_gh_api_review(cmd):
    v = []
    for doc in heredocs(cmd):
        try:
            payload = json.loads(doc)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        v += check_review_summary(payload.get("body") or "")
        for c in payload.get("comments") or []:
            if isinstance(c, dict):
                v += check_short_comment(
                    c.get("body") or "", "inline review comment", 2, 60
                )
    return v


def check_bash(cmd):
    if "CONCISE_SKIP=1" in cmd:
        return []
    v = []
    if re.search(r"\bgit commit\b", cmd):
        v += check_commit(cmd)
    if re.search(r"\bgh pr (create|edit)\b", cmd) or re.search(
    ):
        v += check_pr_body(
            extract_message(cmd, ["--body", "-b"], ["--body-file", "-F"])
        )
    elif re.search(r"\bgh pr comment\b", cmd):
        v += check_short_comment(
            extract_message(cmd, ["--body", "-b"], ["--body-file", "-F"]), "PR comment"
        )
    elif re.search(r"\bgh pr review\b", cmd):
        v += check_review_summary(
            extract_message(cmd, ["--body", "-b"], ["--body-file", "-F"])
        )
    if re.search(r"\bgh issue create\b", cmd):
        v += check_pr_body(
            extract_message(cmd, ["--body", "-b"], ["--body-file", "-F"]), "issue body"
        )
    elif re.search(r"\bgh issue comment\b", cmd):
        v += check_short_comment(
            extract_message(cmd, ["--body", "-b"], ["--body-file", "-F"]),
            "issue comment",
        )
    if (
        re.search(r"\bgh api\b", cmd)
        and "/pulls/" in cmd
        and re.search(r"reviews|comments", cmd)
    ):
        v += check_gh_api_review(cmd)
    return v


def annotated_lines(text, ext):
    """Yield (stripped_line, kind) with kind in code|comment|docstring."""
    marker = "#" if ext in HASH_COMMENT else "//"
    in_doc = False
    out = []
    for line in text.splitlines():
        s = line.strip()
        if ext == ".py" and ('"""' in s or "'''" in s):
            out.append((s, "docstring"))
            if (s.count('"""') + s.count("'''")) % 2 == 1:
                in_doc = not in_doc
            continue
        if in_doc:
            out.append((s, "docstring"))
        elif s.startswith(marker):
            out.append((s, "comment"))
        else:
            out.append((s, "code"))
    return out


def check_code(added_set, new_text, ext):
    v = []
    comments, docstrings, code_count = [], [], 0
    for s, kind in annotated_lines(new_text, ext):
        if not s or s not in added_set:
            continue
        if kind == "comment":
            if not COMMENT_EXEMPT.search(s):
                comments.append(s)
        elif kind == "docstring":
            docstrings.append(s)
        else:
            code_count += 1
    for c in comments + docstrings:
        m = NARRATION.search(c)
        if m:
            v.append(
                f"narration comment ('{m.group(0)}'): state the constraint or delete"
            )
            break
    for c in comments:
        if len(c) > 100:
            v.append("comment line over 100 chars")
            break
    total = code_count + len(comments)
    if len(comments) >= 3 and total and len(comments) > 0.2 * total:
        v.append(
            f"{len(comments)} comment lines for {code_count} code "
            "lines: keep only non-obvious constraints"
        )
    return v


def check_doc(added_text):
    v = style(added_text, "doc")
    if any(re.match(r"\s*#{4,}\s", l) for l in added_text.splitlines()):
        v.append("doc: header nesting past ###")
    if len(BOLD.findall(added_text)) > 3:
        v.append("doc: more than 3 bold spans")
    return v


def check_file(ti, tool):
    path = ti.get("file_path") or ""
    home = os.path.expanduser("~")
    if (
        "/.claude/" in path
        or path.startswith(home + "/.claude")
        or "/scratchpad" in path
    ):
        return []
    ext = os.path.splitext(path)[1].lower()
    if ext not in CODE_EXT and ext not in DOC_EXT:
        return []
    if tool == "Edit":
        new_text = ti.get("new_string") or ""
        old = {l.strip() for l in (ti.get("old_string") or "").splitlines()}
        added = [l for l in new_text.splitlines() if l.strip() not in old]
    else:
        new_text = ti.get("content") or ""
        added = new_text.splitlines()
    added_text = "\n".join(added)
    if not added_text.strip():
        return []
    if ext in DOC_EXT:
        return check_doc(added_text)
    return check_code({l.strip() for l in added}, new_text, ext)


def check_linear(tool, ti):
    v = []
    if tool == "mcp__linear__save_issue":
        desc = ti.get("description") or ""
        v += style(desc, "ticket")
        w = word_count(desc)
        if w > 120:
            v.append(f"ticket description {w} words (max 120)")
    elif tool == "mcp__linear__save_comment":
        body = ti.get("body") or ""
        v += style(body, "Linear comment")
        s = sentence_count(body)
        if s > 2:
            v.append(f"Linear comment {s} sentences (max 2)")
    elif tool == "mcp__linear__save_document":
        v += style(ti.get("content") or "", "Linear doc")
    elif tool == "mcp__linear__submit_diff_review":
        v += check_review_summary(ti.get("body") or "")
        for c in ti.get("comments") or []:
            if isinstance(c, dict):
                v += check_short_comment(
                    c.get("body") or "", "inline review comment", 2, 60
                )
    return v


def check_slack(tool, ti):
    text = ti.get("text") or ti.get("message") or ""
    if tool.endswith(("slack_send_message", "slack_send_message_draft")):
        v = style(text, "Slack message")
        w = word_count(text)
        if w > 100:
            v.append(f"Slack message {w} words (max 100)")
        return v
    return style(ti.get("content") or text, "Slack canvas")


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if tool == "Bash":
        v = check_bash(ti.get("command") or "")
    elif tool in ("Edit", "Write"):
        v = check_file(ti, tool)
    elif tool.startswith("mcp__linear__"):
        v = check_linear(tool, ti)
    elif tool.startswith("mcp__claude_ai_Slack__"):
        v = check_slack(tool, ti)
    else:
        v = []
    if v:
        deny(v)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
