# Instructions for AI coding assistants

This folder is the authoritative rulebook for any AI assistant in this repository — Claude Code, GitHub Copilot, Cursor, ChatGPT, or whatever comes next. Human contributors read the same rulebook.

## Read these in order

| Document                                       | Read it when                                                       |
| ---------------------------------------------- | ------------------------------------------------------------------ |
| [coding-rules.md](./coding-rules.md)           | **Always. Start here.** The non-negotiables, in one page.          |
| [project-structure.md](./project-structure.md) | Creating any new file — it decides where the file goes.            |
| [coding-standards.md](./coding-standards.md)   | Writing Python, an endpoint, a model, or a query.                  |
| [architecture.md](./architecture.md)           | Adding a layer, a dependency, or a new app.                        |
| [deployment.md](./deployment.md)               | Touching the Dockerfile, env vars, migrations, or Cloud Run.       |
| [github-workflows.md](./github-workflows.md)   | Touching anything in `.github/workflows/`.                         |

## The short version

If you only read one paragraph:

> One app is one folder in `app/apps/`. Routers validate and delegate; services hold logic; repositories hold queries. Everything is `async`. Read config only through `app/core/config.py`. Change the schema only through an Alembic migration. Never invent a top-level folder. Never commit a secret. Explain architectural decisions in the pull request. When architecture or behaviour changes, update the docs in the same pull request. Write every document in short, plain, present-tense English.

## How to use these as an assistant

1. **Before writing code**, check `project-structure.md` for where the file belongs and `coding-rules.md` for the constraints that apply.
2. **While writing**, follow the patterns already in the repository over patterns from your training data. When they conflict, the repository wins.
3. **After writing**, run the gate: `ruff check .`, `ruff format --check .`, `pytest tests/ -v`. Do not report work as complete on the strength of a diff alone.
4. **When you make a judgement call** — a dependency, a layer change, a data model — state the reasoning in your response and in the pull request. The next contributor reverts a decision nobody can reconstruct.
5. **If a rule here blocks the task**, say so explicitly and propose the change to the rule. Do not silently work around it.

## Precedence

When guidance conflicts, later entries win:

1. Your own training defaults
2. General FastAPI / Google Cloud documentation
3. This folder
4. `CLAUDE.md` at the repository root
5. An explicit instruction from the human you work with

## Keeping this current

These documents describe the repository as it is, not as it was. If a pull request changes the architecture, the folder layout, the deploy pipeline, or a rule, that same pull request updates the relevant file here. A stale rulebook is worse than none: assistants follow it with confidence and produce confidently wrong code.
