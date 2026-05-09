# SMART Planning Workflow

Use file-based planning for complex work in SMART, but keep it lightweight.

This repository does not depend on automated planning hooks. The goal is simpler:

- keep long tasks oriented
- keep research results out of volatile chat context
- preserve failed attempts and verification history

## When To Use It

Use the planning workflow when at least one of these is true:

- the task will likely take more than 20 to 30 minutes
- the task touches multiple modules, pages, or services
- the task needs STK, SPICE, Qt WebEngine, or browser/debug verification
- the task involves research plus implementation
- the task is likely to continue across multiple sessions

Skip it for:

- trivial questions
- one-file fixes with obvious scope
- quick documentation lookups

## File Layout

Store planning files under `.planning/<date>-<slug>/`.

Each plan directory contains:

- `task_plan.md`: scope, phases, decisions, blockers, error log
- `findings.md`: research notes, command results worth preserving, references
- `progress.md`: chronological work log, verification notes, next step

The active plan id may also be stored in `.planning/.active_plan` for convenience.

## Quick Start

Create a new planning session:

```powershell
.\scripts\init-planning-session.ps1 -TaskName "Fix launch-window cache refresh"
```

That script creates a dated plan directory, copies the templates, and marks it active.

## SMART-Specific Rules

### 1. Keep project facts in the right place

- Put durable repository rules in `AGENTS.md`.
- Put task-local research and experiments in the plan's `findings.md`.
- Do not put transient debugging output into permanent docs unless it becomes a stable rule.

### 2. Record exact verification

For UI, STK, SPICE, and launch-window work, write down:

- what you ran
- which project or dataset you used
- what passed
- what failed
- what still needs manual verification

### 3. Preserve failed attempts

If a fix fails, add one short note to `task_plan.md` or `progress.md`:

- command or action attempted
- observed error
- reason the next attempt will differ

Do not silently retry the same approach multiple times.

### 4. Prefer references over context stuffing

If you looked up:

- STK help
- SPICE kernel behavior
- launch-window formulas

summarize the result in `findings.md` instead of relying on chat memory.

### 5. Keep secrets and bulky artifacts out

Never place these in planning files:

- API keys
- full SPICE kernels
- large CSV dumps
- large binary artifacts

Write concise summaries and file paths instead.

## Recommended Workflow

1. Create a plan when the task is clearly non-trivial.
2. Fill in goal, constraints, and planned phases.
3. During research, append durable notes to `findings.md`.
4. After each meaningful implementation step, append a short entry to `progress.md`.
5. When the task changes direction, update `task_plan.md` rather than relying on memory.
6. Before ending the session, write the next concrete step and current verification state.

## Suggested Phase Pattern

Use phases that make sense for engineering work in SMART:

1. Reproduce and scope
2. Inspect code and runtime behavior
3. Implement or adjust
4. Verify locally
5. Document follow-up risks or manual checks

## Relationship To Existing SMART Docs

Use planning files as working memory. Promote only stable knowledge into repository docs such as:

- `doc/spice_usage.md`
- `doc/launch_window_workflow.md`
- `doc/launch_window_angle_reference.md`
- `doc/ai_project_analysis.md`

If a lesson is specific to one task, keep it in `.planning/`. If it becomes a recurring rule, move it into the appropriate permanent doc.
