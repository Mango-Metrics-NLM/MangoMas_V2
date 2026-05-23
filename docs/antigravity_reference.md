# 🚀 Antigravity Developer Reference

This document provides a reference of the highly-optimized command shortcuts configured for this repository to ensure a seamless, **prompt-free** development experience in **Antigravity**.

---

## ⚡ Prompt-Free Commands

We have authorized general wildcards and specific targets so that the assistant can run these workflows instantly without prompting you for individual confirmations:

| Workflow | Command Format | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Testing** | `pytest <args>` | **Auto-Approved** | Always run `pytest` directly instead of `python -m pytest` |
| **Linting & Formatting** | `ruff <args>` | **Auto-Approved** | Run ruff check or format directly |
| **Type Checking** | `mypy` | **Auto-Approved** | Enforce static types automatically |
| **Git Operations** | `git <subcommand>` | **Auto-Approved** | All git commands (`status`, `diff`, `commit`, etc.) run silently |
| **FastAPI Dev Server** | `uvicorn <args>` | **Auto-Approved** | Spin up the server without prompts |
| **Custom CLI** | `mangomas <args>` | **Auto-Approved** | Manage agents and invoke pipelines |
| **Dependencies** | `npm <args>` / `npx <args>` | **Auto-Approved** | Package installs and lifecycle scripts |
| **GitKraken Tools** | `GitKraken/*` (MCP) | **Auto-Approved** | Seamless git visualization & management |
| **Browser Testing** | `chrome_devtools/*` (MCP) | **Auto-Approved** | Browser and UI automation |

---

## 🔒 Security Gatekeepers (Will Still Prompt)

For your safety, the Antigravity sandbox strictly requires explicit manual approval for dangerous execution patterns:

1. **Bare `python <script>` Invocations**
   - *Why:* The platform denies auto-approving any command starting with `python` to prevent arbitrary file system or network code execution.
   - *Workaround:* Use `pytest` for executing verification checks, or approve the one-off python script run when prompted.
2. **Access to Sensitive Environment Variables & Credentials**
   - *Why:* Accessing or writing `.env` files is gated to protect your keys.
