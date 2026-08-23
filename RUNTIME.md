# Runtime failure semantics

`app/runtime.py` is the minimal orchestration boundary around the existing core.

- **Recoverable external error:** website analyzer exceptions are logged as structured runtime events and converted into a persisted unreachable analysis plus website signal; the record and batch continue.
- **Permanent record error:** invalid or unusable identity returns `skipped` with `invalid_record`; malformed application data returns `failed` with a stage and safe error code.
- **Batch-level fatal error:** source fetch/iteration failure returns `fatal_error`; already completed records remain in the batch result.
- **Database error:** duplicate `(org_id, dedupe_key)` insertion is reread and merged. Other database failures return a failed record and are not silently swallowed.
- **External provider error:** never exposed with credentials or stack traces; it is logged by type and represented as structured `provider_error` analysis data.

`app/api/runtime.py` owns request order: authentication, server-derived org context, strict payload validation, rate limiting, then query or source action. A caller-supplied `org_id` is rejected as an unknown field and is never used.
