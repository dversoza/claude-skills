#!/usr/bin/env python3
"""
Skill Initializer - Creates a new skill from template

Usage:
    init_skill.py <skill-name> --path <path>

Examples:
    init_skill.py my-new-skill --path ~/.claude/skills
    init_skill.py my-api-helper --path ~/dev/claude-skills
"""

import sys
from pathlib import Path


SKILL_TEMPLATE = """---
name: {skill_name}
description: >-
  TODO: What the skill does. Use when the user asks to ... or needs to ....
  Triggers on requests like "...", "...", "...".
---

# {skill_title}

TODO: One-line intro stating what this skill does and how.

## Script

```
python3 ~/.claude/skills/{skill_name}/scripts/TODO.py <subcommand> [args]
```

## Subcommands

`TODO <args>` -- description of what this subcommand does.

## Guidelines

- TODO: Default behaviors, safety limits, scope boundaries.
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""
TODO: Replace with actual script implementation or delete if not needed.
"""

import json
import sys


def main():
    if len(sys.argv) < 2:
        print(json.dumps({{"error": "usage: {skill_name}.py <subcommand>"}}), file=sys.stderr)
        sys.exit(1)

    subcommand = sys.argv[1]
    print(json.dumps({{"error": f"unknown subcommand: {{subcommand}}"}}), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
'''


def title_case_skill_name(skill_name):
    """Convert hyphenated skill name to Title Case for display."""
    return ' '.join(word.capitalize() for word in skill_name.split('-'))


def init_skill(skill_name, path):
    skill_dir = Path(path).resolve() / skill_name

    if skill_dir.exists():
        print(f"Error: Skill directory already exists: {skill_dir}")
        return None

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        print(f"Error creating directory: {e}")
        return None

    skill_title = title_case_skill_name(skill_name)
    skill_content = SKILL_TEMPLATE.format(
        skill_name=skill_name,
        skill_title=skill_title,
    )

    skill_md_path = skill_dir / 'SKILL.md'
    try:
        skill_md_path.write_text(skill_content)
        print(f"Created {skill_md_path}")
    except Exception as e:
        print(f"Error creating SKILL.md: {e}")
        return None

    try:
        scripts_dir = skill_dir / 'scripts'
        scripts_dir.mkdir(exist_ok=True)
        example_script = scripts_dir / f'{skill_name}.py'
        example_script.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name))
        example_script.chmod(0o755)
        print(f"Created {example_script}")
    except Exception as e:
        print(f"Error creating scripts/: {e}")
        return None

    print(f"\nSkill '{skill_name}' initialized at {skill_dir}")
    print("Delete scripts/ if the skill does not need a script.")
    print("Create references/ or assets/ directories as needed.")
    return skill_dir


def main():
    if len(sys.argv) < 4 or sys.argv[2] != '--path':
        print("Usage: init_skill.py <skill-name> --path <path>")
        print("\nSkill name: hyphen-case, lowercase letters/digits/hyphens, max 64 chars.")
        print("\nExamples:")
        print("  init_skill.py my-new-skill --path ~/.claude/skills")
        print("  init_skill.py my-api-helper --path ~/dev/claude-skills")
        sys.exit(1)

    skill_name = sys.argv[1]
    path = sys.argv[3]

    print(f"Initializing skill: {skill_name}")
    print(f"Location: {path}\n")

    result = init_skill(skill_name, path)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
