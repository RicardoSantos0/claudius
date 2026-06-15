# Authoring Skills

How to add, register, and validate a skill so it passes
`scripts/validate_skills.py`. The repo currently has 11 skills under `skills/`.

## 1. Create the skill package

A skill is a directory under `skills/` containing a `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: One-line description of when to use this skill.
---

# My Skill

Body: trigger, inputs, behaviour.
```

Required frontmatter fields checked by the validator: `name` and `description`.
A skill may include any supporting files alongside `SKILL.md`.

## 2. Register the skill

Add the skill to the `skills:` list in **`mas/roster/registry_index.yaml`**:

```yaml
skills:
- skill_id: my-skill
  status: active
  category: workflow        # workflow | research | meta | delivery
  # workflow skills MUST set trigger_phases:
  trigger_phases: [intake, execution]
  recommended_for: [master_orchestrator]   # optional; agents must exist in registry
```

Registry rules checked by the validator:

- Every `status: active` skill must have a matching `skills/{skill_id}/SKILL.md` on disk.
- Every `category: workflow` skill must set a non-empty `trigger_phases`.
- Every agent listed under `recommended_for` must exist in
  `mas/roster/registry_canonical.yaml`.

## 3. Validate

```bash
python scripts/validate_skills.py
```

Exit code `0` means all `SKILL.md` files and active registry entries pass.

## The `notebooklm` submodule case

`skills/notebooklm` is a **git submodule** (see `.gitmodules`), not a regular
directory in this repo. Do not edit its contents from here — changes belong in its
own repository (`notebooklm-skill`). It still registers like any other skill (its
`skill_id` is `notebooklm`, `category: research`). The source-export tooling and
`scripts/check_archive_clean.py` explicitly exclude its private runtime paths
(`skills/notebooklm/data/browser_state/`, `auth_info.json`, `.venv/`), so never add
those to the tree.
