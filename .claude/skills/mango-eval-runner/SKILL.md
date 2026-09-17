---
name: mango-eval-runner
description: Skill for creating and executing automated evaluations and custom scorers for AI models.
argument-hint: Ask for evaluating models
---

# Mango Eval Runner

## Overview
This skill provides instructions for building evaluation pipelines, custom scorers, and AQA (Automated Quality Assurance) harnesses. It ensures evaluation logic is reusable, rigorous, and statistically sound.

## Guidelines
- **Custom Scorers**: Implement metrics dynamically and without hard-coded thresholds where possible.
- **Testing Practices**: Adhere to strict testing practices. Eval runners must include comprehensive test suites.
- **Logging & Debugging**: Incorporate robust logging to trace evaluation failures and metrics anomalies.
- **Type Safety**: Strictly annotate all evaluation inputs and outputs (`mypy` compliant).

## Best Practices
1. **Separation of Concerns**: Keep evaluation logic decoupled from workflow execution.
2. **Backwards Compatibility**: When adding new metrics, ensure legacy metric classes still function.
