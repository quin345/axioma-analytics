# CLAUDE.md

## Task routing

Subagents live in `.claude/agents/`: **scout** (Haiku, read-only), **implementer**
(Sonnet, edits) and **architect** (Opus, read-only unless told otherwise).

- **Plan first.** For any task that touches 3+ files or has separable parts, first
  write a short plan that splits it into independent subtasks.
- **Parallel when independent.** Spawn independent subtasks in parallel, in a
  single turn. Run dependent subtasks sequentially, each after the one it needs.
- **Difficulty rubric:**
  - **Easy** (lookup, grep, summarize, single-file mechanical change) -> `scout`
  - **Medium** (clear spec, known pattern, tests exist or are easy to write) -> `implementer`
  - **Hard** (ambiguous requirements, architecture, concurrency, security, data
    migrations, bug with unknown cause) -> `architect`
- **Cheapest tier first.** When unsure, pick the cheaper tier and escalate one tier
  if the result is incomplete or tests fail.
- **Self-contained briefs.** Every child gets its goal, the relevant paths, the
  constraints, and the expected output format. Children do not see this
  conversation.
- **The main session orchestrates.** It plans, delegates and integrates results;
  it does not do bulk work itself.
- **Verify before done.** After children finish, review their outputs and run the
  tests before reporting the task as done.
