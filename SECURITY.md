# Security Policy

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting for this
repository: **Security → Advisories → Report a vulnerability** at
<https://github.com/Mango-Metrics-NLM/MangoMas_V2/security/advisories/new>.
Do not open a public issue or pull request for a suspected vulnerability.

Include what you can of: the affected branch or tag, a reproduction, the
impact you believe it has, and any suggested fix. Encrypted mail is not
offered; the advisory form is the only channel.

## Disclosure timeline

| Step | Target |
|---|---|
| Acknowledge the report | 3 business days |
| Triage and severity assessment | 10 business days |
| Fix released or mitigation published | 90 days from report, sooner for critical |
| Public advisory | On release of the fix, credited unless you ask otherwise |

If a fix needs longer than 90 days we will say so in the advisory thread
with a revised date rather than go quiet.

## Supported branches

Fixes land on the branches below (ADR-0036); anything else — feature
branches, superseded tags — is unsupported.

| Branch | Role | Receives security fixes |
|---|---|---|
| `main` | Production record; releases are `v*` tags on it | Yes (hotfix, then back-merged) |
| `qa` | Release candidate | Yes, via promotion from `dev` |
| `dev` | Integration trunk | Yes |
| `feat/initial-release` | Current default branch until it is renamed to `dev` | Yes |

## Scope

In scope: the FastAPI runtime in this repository (`src/mangomas/`), its CLI,
the container image built from `Dockerfile`, and the GitHub Actions
workflows that build and deploy it.

Out of scope: the Hugging Face Gradio demo (`ianshank/MangoMAS`), the
`MangoMas-Demo` and MangoMas Studio repositories, and third-party services
this runtime connects to (LM Studio, Vertex AI, Cloud SQL). Report issues in
those to their own maintainers.

## Automated scanning

Dependabot, OpenSSF Scorecard (`scorecard.yml`), CodeQL (`codeql.yml`), a
pinned-binary secret scan and a CycloneDX SBOM + Trivy scan (`nightly.yml`)
run on this repository; their findings are triaged as above.
