#!/usr/bin/env python3
"""Quick structural validation for an Agent Skill directory.

Checks the pieces a skill needs to be discovered and loaded:

  * SKILL.md with a YAML frontmatter block
  * a non-empty `name` that matches the directory name and the official
    lowercase-hyphen grammar
  * a non-empty `description` within the documented length limit
  * a body with actual instructions
  * optional `agents/openai.yaml` carrying UI metadata

Usage:
    python scripts/quick_validate.py [path ...]

`path` may be a skill directory, a SKILL.md file, or a repository root whose
skills live in `skills/<name>/SKILL.md`. Exits 1 when any error is found.

Only the Python standard library is used.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MIN_BODY_LEN = 80
ALLOWED_TOP_LEVEL = {
    "SKILL.md",
    "agents",
    "assets",
    "references",
    "scripts",
    "README.md",
    "LICENSE",
}


class Report:
    def __init__(self, skill_dir: Path) -> None:
        self.skill_dir = skill_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  [ok]   {label:<12}{detail}".rstrip())

    def warn(self, label: str, detail: str) -> None:
        self.warnings.append(f"{label}: {detail}")
        print(f"  [warn] {label:<12}{detail}")

    def fail(self, label: str, detail: str) -> None:
        self.errors.append(f"{label}: {detail}")
        print(f"  [FAIL] {label:<12}{detail}")


def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Return (fields, body). `fields` is None when there is no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            fields: dict[str, str] = {}
            for line in lines[1:index]:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    quote = value[0]
                    value = value[1:-1]
                    if quote == "'":
                        value = value.replace("''", "'")
                    else:
                        value = value.replace('\\"', '"')
                fields[key.strip()] = value
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return fields, body
    return None, text


def read_text(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    return text, raw.startswith(b"\xef\xbb\xbf")


def validate_skill(skill_dir: Path) -> Report:
    report = Report(skill_dir)
    print(f"\n{skill_dir}")

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        report.fail("file", "SKILL.md is missing")
        return report

    try:
        text, has_bom = read_text(skill_file)
    except UnicodeDecodeError as exc:
        report.fail("encoding", f"SKILL.md is not valid UTF-8 ({exc})")
        return report
    if has_bom:
        report.warn("encoding", "SKILL.md starts with a UTF-8 BOM")
    if not text.strip():
        report.fail("file", "SKILL.md is empty")
        return report

    fields, body = parse_frontmatter(text)
    if fields is None:
        report.fail("frontmatter", "missing leading '---' ... '---' block")
        return report
    report.ok("frontmatter", "found")

    name = fields.get("name", "").strip()
    if not name:
        report.fail("name", "missing or empty")
    elif len(name) > MAX_NAME_LEN:
        report.fail("name", f"{len(name)} chars (max {MAX_NAME_LEN})")
    elif not NAME_RE.match(name):
        report.fail("name", f"'{name}' is not lowercase-hyphenated")
    elif name != skill_dir.name:
        report.fail("name", f"'{name}' does not match directory '{skill_dir.name}'")
    else:
        report.ok("name", name)

    description = fields.get("description", "").strip()
    if not description:
        report.fail("description", "missing or empty")
    elif len(description) > MAX_DESCRIPTION_LEN:
        report.fail("description", f"{len(description)} chars (max {MAX_DESCRIPTION_LEN})")
    else:
        report.ok("description", f"{len(description)} chars")
        if len(description) < 20:
            report.warn("description", "shorter than 20 chars; say when to use the skill")

    if len(body.strip()) < MIN_BODY_LEN:
        report.fail("body", f"{len(body.strip())} chars; add usable instructions")
    else:
        report.ok("body", f"{len(body.strip()):,} chars")

    unexpected = sorted(
        entry.name
        for entry in skill_dir.iterdir()
        if entry.name not in ALLOWED_TOP_LEVEL and not entry.name.startswith(".")
    )
    if unexpected:
        report.warn("layout", "unexpected top-level entries: " + ", ".join(unexpected))
    else:
        report.ok("layout", "clean")

    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if openai_yaml.is_file():
        try:
            meta = openai_yaml.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.fail("openai.yaml", "not valid UTF-8")
        else:
            if not meta.strip():
                report.fail("openai.yaml", "empty")
            elif "interface:" not in meta:
                report.warn("openai.yaml", "no top-level 'interface:' block")
            elif "display_name" not in meta:
                report.warn("openai.yaml", "no display_name")
            elif "short_description" not in meta:
                report.warn("openai.yaml", "no short_description")
            else:
                report.ok("openai.yaml", "interface metadata present")
    elif (skill_dir / "agents").exists():
        report.warn("openai.yaml", "agents/ exists but agents/openai.yaml is missing")

    return report


def find_skill_dirs(raw_paths: list[str]) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    problems: list[str] = []
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.exists():
            problems.append(f"{raw}: path does not exist")
            continue
        if path.is_file():
            if path.name.lower() == "skill.md":
                found.append(path.parent)
            else:
                problems.append(f"{raw}: expected a SKILL.md file")
            continue
        if (path / "SKILL.md").is_file():
            found.append(path)
            continue
        candidates = sorted(path.glob("skills/*/SKILL.md"))
        if not candidates:
            problems.append(f"{raw}: no SKILL.md or skills/*/SKILL.md found")
        for candidate in candidates:
            found.append(candidate.parent)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique, problems


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["."]
    skill_dirs, problems = find_skill_dirs(targets)
    for problem in problems:
        print(f"[FAIL] {problem}")

    reports = [validate_skill(skill_dir) for skill_dir in skill_dirs]
    errors = sum(len(report.errors) for report in reports) + len(problems)
    warnings = sum(len(report.warnings) for report in reports)

    print()
    for report in reports:
        for warning in report.warnings:
            print(f"[warn] {report.skill_dir.name}: {warning}")
        for error in report.errors:
            print(f"[FAIL] {report.skill_dir.name}: {error}")

    if not reports:
        print("No skill directory was checked.")
        return 1

    verdict = "PASS" if errors == 0 else "FAIL"
    print(f"{verdict}: {len(reports)} skill(s), {warnings} warning(s), {errors} error(s)")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
