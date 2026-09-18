---
name: mango-workflow-generator
description: Skill for generating and managing declarative workflow definitions (JSON) for AI agents.
argument-hint: Ask for generating workflows
---

# Mango Workflow Generator

## Overview
This skill is utilized when defining, generating, or validating declarative agent workflows. It enforces the use of JSON graph definitions over hard-coded procedural scripts.

## Guidelines
- **Declarative Graphs**: Workflows must be defined as graphs (nodes and edges) using JSON.
- **No Hard-coded Logic**: Agent routing, conditionals, and fan-outs must be parameterized.
- **Enterprise Standards**: Follow enterprise AQA (Automated Quality Assurance) by ensuring workflows are predictable, testable, and validatable against schemas.
- **Dynamic Adaptability**: Design workflow schemas that can be extended without breaking older graph definitions.

## Best Practices
1. **Schema Validation**: Ensure all generated workflow JSONs conform to a standard schema.
2. **Idempotency**: Workflow definitions should produce the same execution graph regardless of environment.
