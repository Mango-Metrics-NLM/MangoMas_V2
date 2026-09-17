---
name: mango-composition-builder
description: Skill for building dynamic, declarative, and backwards-compatible LLM factory compositions.
argument-hint: Ask for building components
---

# Mango Composition Builder

## Overview
This skill guides the construction of LLM factory components and settings overrides within the Mango-Mas architecture. Agents should use this when extending, building, or modifying composition builders that instantiate AI models, tools, or memory components.

## Guidelines
- **Declarative Approach**: Avoid hardcoding values. Use declarative settings injection.
- **Backwards Compatibility**: Any new factory method or class must maintain compatibility with previous versions. Use kwargs extraction or sensible defaults.
- **Strict Typing**: Enforce strict Python typing using `mypy` standards (e.g., `Optional`, `Dict`, `Any`).
- **Reusability**: Build modular, reusable components rather than monolithic instantiation scripts.

## Best Practices
1. **Dynamic Configuration**: Load settings from validated configuration files or environment variables.
2. **Error Handling & Logging**: Implement robust logging and fail-safes during component initialization.
3. **Linting**: Ensure code passes `ruff` and `mypy` checks.
