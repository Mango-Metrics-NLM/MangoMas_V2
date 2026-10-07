# docling-serve response fixtures

JSON bodies returned by `POST /v1/convert/file`, used by
`tests/adapters/parsers/test_docling_serve.py` through
`tests.constants.docling.load_docling_fixture`.

| File | `status` |
|---|---|
| `success.json` | `success` |
| `partial_success.json` | `partial_success` |
| `failure.json` | `failure` |
| `skipped.json` | `skipped` |

## Provenance

These are **hand-written** to the response shape documented in docling-serve's
`docs/usage.md`: a top-level `document` object carrying `md_content`, plus
`status`, `errors` and `processing_time`. They are **not** live captures.

They are **PENDING replacement** by sanitised captures from a real
docling-serve container (spec-0035 implementation runbook, PR 1 task 1.1),
which will also record the image digest, the capture date and the command used.
Until then, every field name the adapter reads is a named constant in
`src/mangomas/adapters/parsers/docling_serve.py` marked as pending live
verification, so a correction lands in one place.

Inputs are synthetic; no fixture contains real document content.
