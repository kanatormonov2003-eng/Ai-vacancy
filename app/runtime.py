"""Minimal production runtime around the existing domain and repository APIs.

This module deliberately stays small: it is the missing orchestration boundary,
not a replacement for normalisation, dedupe, analysis, signals, scoring, or DB
logic.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Iterator

from . import obs
from .analysis import website as website_module
from .db import repo, sqlite as db
from .domain import dedupe, pipeline, scoring, signals
from .domain.query import SearchQuery
from .errors import AppError


@dataclass
class RecordResult:
    status: str  # created, merged, skipped, failed
    lead_id: str | None = None
    source: str | None = None
    external_id: str | None = None
    stage: str | None = None
    error_code: str | None = None
    error: str | None = None
    changes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BatchResult:
    items: list[RecordResult] = field(default_factory=list)
    fatal_error: str | None = None

    @property
    def counters(self) -> dict[str, int]:
        out = {
            "created": 0,
            "merged": 0,
            "skipped": 0,
            "failed": 0,
        }

        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1

        return out

    def as_dict(self) -> dict:
        return {
            "counters": self.counters,
            "items": [item.as_dict() for item in self.items],
            "fatal_error": self.fatal_error,
        }


def _source_ref(org_id: str, lead_id: str, raw) -> None:
    """Make source provenance idempotent under concurrent workers."""
    try:
        repo.add_source_ref(
            org_id,
            lead_id,
            raw.source,
            raw.source_url,
            raw.external_id,
            raw.__dict__,
            raw.is_demo,
        )
        return

    except Exception as exc:
        # Only a real unique/integrity race is recoverable here.
        if not db.is_integrity_error(exc):
            raise

    # The other worker won the race. Re-read the canonical provenance row.
    #
    # IMPORTANT:
    # PostgreSQL uses:
    #     external_id IS NOT DISTINCT FROM ?
    # SQLite also supports it, so this stays portable.
    existing = db.one(
        "SELECT id "
        "FROM lead_source_refs "
        "WHERE lead_id = ? "
        "AND source = ? "
        "AND external_id IS NOT DISTINCT FROM ?",
        (
            lead_id,
            raw.source,
            raw.external_id,
        ),
    )

    if not existing:
        # Integrity error occurred but the expected canonical row does not
        # exist. Do not silently hide a real database problem.
        raise


def _merge_into(
    org_id: str,
    lead_id: str,
    incoming: dict,
) -> tuple[dict, list[str]]:
    existing = repo.get_lead(
        org_id,
        lead_id,
    )

    patch, changes = dedupe.merge_fields(
        existing,
        incoming,
    )

    if patch:
        repo.update_lead(
            org_id,
            lead_id,
            patch,
        )
        existing.update(patch)

    return existing, changes


def _canonical_after_conflict(
    org_id: str,
    incoming: dict,
) -> tuple[str, dict, list[str]]:
    """Reread the canonical lead after a concurrent insert conflict."""
    candidates = repo.find_candidates(
        org_id,
        incoming,
    )

    match = dedupe.best_match(
        incoming,
        candidates,
    )

    if not match.lead_id:
        raise RuntimeError(
            "canonical lead disappeared after duplicate insert"
        )

    lead, changes = _merge_into(
        org_id,
        match.lead_id,
        incoming,
    )

    return match.lead_id, lead, changes


def _analysis_dict(value) -> dict:
    if hasattr(value, "as_dict"):
        return value.as_dict()

    return dict(value or {})


def _enrich_and_score(
    org_id: str,
    lead_id: str,
    lead: dict,
    raw,
    analyzer: Callable,
) -> None:
    """Run enrichment and scoring; external provider failures are recoverable."""
    analysis = None

    if lead.get("website"):
        try:
            analysis = _analysis_dict(
                analyzer(lead["website"])
            )

        except Exception as exc:
            obs.error(
                "runtime.website_analysis_failed",
                source=raw.source,
                external_id=raw.external_id,
                error_type=type(exc).__name__,
            )

            analysis = {
                "url": lead["website"],
                "final_url": lead["website"],
                "reachable": False,
                "http_status": None,
                "https": False,
                "ssl_valid": None,
                "redirects": 0,
                "response_ms": None,
                "html_bytes": 0,
                "scores": {},
                "total_score": 0,
                "facts": [],
                "detected": {},
                "error_code": "provider_error",
            }

        repo.save_website_analysis(
            org_id,
            lead_id,
            analysis,
        )

        for fact in analysis.get("facts") or []:
            repo.upsert_fact(
                org_id,
                lead_id,
                fact["fact"],
                fact["value"],
                fact["source"],
                fact.get("source_url"),
                fact.get("confidence", 0.5),
            )

    current = repo.get_lead(
        org_id,
        lead_id,
    )

    found = (
        signals.from_profile(current)
        + signals.from_text(
            current.get("description"),
            raw.source,
            raw.source_url,
        )
        + signals.from_website(analysis)
    )

    for item in found:
        repo.upsert_signal(
            org_id,
            lead_id,
            item["signal"],
            item["source"],
            polarity=item["polarity"],
            source_url=item.get("source_url"),
            evidence=item.get("evidence"),
            confidence=item.get("confidence", 0.5),
        )

    result = scoring.score_lead(
        current,
        found,
        analysis,
    )

    source_count = len(
        repo.sources_for(
            org_id,
            [lead_id],
        ).get(
            lead_id,
            [],
        )
    )

    quality = scoring.data_quality(
        current,
        analysis,
        sources_count=max(1, source_count),
    )

    repo.save_score(
        org_id,
        lead_id,
        result["score"],
        result["reasons"],
        result["confidence"],
        result["weights_version"],
        result["ai_adjustment"],
    )

    repo.update_lead(
        org_id,
        lead_id,
        {
            "lead_score": result["score"],
            "score_confidence": result["confidence"],
            "data_quality_score": quality["data_quality_score"],
            "contact_confidence": quality["contact_confidence"],
            "website_confidence": quality["website_confidence"],
            "website_status": (
                "ok"
                if analysis and analysis.get("reachable")
                else "unknown"
                if analysis is None
                else "unreachable"
            ),
            "website_score": (
                analysis.get("total_score")
                if analysis
                else None
            ),
            "website_response_ms": (
                analysis.get("response_ms")
                if analysis
                else None
            ),
            "analyzed_at": (
                current.get("last_verified_at")
                if analysis
                else None
            ),
        },
    )


def process_record(
    raw,
    org_id: str,
    *,
    analyzer: Callable | None = None,
) -> RecordResult:
    """Process one RawRecord through the complete available pipeline."""
    analyzer = analyzer or (
        lambda url: website_module.analyze(url)
    )

    result = RecordResult(
        "failed",
        source=getattr(raw, "source", None),
        external_id=getattr(raw, "external_id", None),
    )

    try:
        result.stage = "sanitize"

        raw.sanitized()

        result.stage = "normalize"

        incoming = pipeline.normalize_record(
            raw
        )

        if not pipeline._valid(incoming):
            return RecordResult(
                "skipped",
                source=raw.source,
                external_id=raw.external_id,
                stage="validation",
                error_code="invalid_record",
                error="record has no usable company identity",
            )

        result.stage = "dedupe"

        candidates = repo.find_candidates(
            org_id,
            incoming,
        )

        match = dedupe.best_match(
            incoming,
            candidates,
        )

        created = False

        if match.should_merge:
            old = repo.get_lead(
                org_id,
                match.lead_id,
            )

            patch, changes = dedupe.merge_fields(
                old,
                incoming,
            )

            if patch:
                repo.update_lead(
                    org_id,
                    match.lead_id,
                    patch,
                )
                old.update(patch)

            lead_id = match.lead_id
            lead = old

        else:
            result.stage = "persist"

            try:
                lead_id = repo.insert_lead(
                    org_id,
                    pipeline._payload(incoming),
                )

                lead = repo.get_lead(
                    org_id,
                    lead_id,
                )

                created = True
                changes = []

            except Exception as exc:
                if not db.is_integrity_error(exc):
                    raise

                # Concurrent duplicate insertion:
                # insert -> integrity error -> reread -> merge.
                lead_id, lead, changes = _canonical_after_conflict(
                    org_id,
                    incoming,
                )

        _source_ref(
            org_id,
            lead_id,
            raw,
        )

        result.stage = "enrichment"

        _enrich_and_score(
            org_id,
            lead_id,
            lead,
            raw,
            analyzer,
        )

        return RecordResult(
            "created" if created else "merged",
            lead_id=lead_id,
            source=raw.source,
            external_id=raw.external_id,
            stage="complete",
            changes=changes,
        )

    except AppError as exc:
        obs.error(
            "runtime.record_failed",
            source=result.source,
            external_id=result.external_id,
            stage=result.stage,
            error_code=exc.code,
        )

        return RecordResult(
            "failed",
            source=result.source,
            external_id=result.external_id,
            stage=result.stage,
            error_code=exc.code,
            error=exc.message,
        )

    except Exception as exc:
        obs.error(
            "runtime.record_failed",
            source=result.source,
            external_id=result.external_id,
            stage=result.stage,
            error_type=type(exc).__name__,
        )

        return RecordResult(
            "failed",
            source=result.source,
            external_id=result.external_id,
            stage=result.stage,
            error_code="internal_error",
            error="record processing failed",
        )

    finally:
        # process_record owns its thread-local DB handle.
        # This is required for short-lived ThreadPoolExecutor workers.
        db.close()


def _bounded_results(
    records: Iterator,
    fn: Callable,
    max_workers: int,
    on_fatal: Callable[[Exception], None],
) -> Iterator[RecordResult]:
    """Keep at most max_workers futures alive."""
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
    ) as pool:
        pending: set[concurrent.futures.Future] = set()
        exhausted = False

        while not exhausted or pending:
            while (
                not exhausted
                and len(pending) < max_workers
            ):
                try:
                    pending.add(
                        pool.submit(
                            fn,
                            next(records),
                        )
                    )

                except StopIteration:
                    exhausted = True

                except Exception as exc:
                    on_fatal(exc)
                    exhausted = True

            if not pending:
                break

            done, pending = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )

            for future in done:
                try:
                    yield future.result()

                except Exception as exc:
                    yield RecordResult(
                        "failed",
                        stage="worker",
                        error_code="internal_error",
                        error="worker failed: " + type(exc).__name__,
                    )


def ingest_records(
    records: Iterable,
    org_id: str,
    *,
    max_workers: int = 1,
    analyzer: Callable | None = None,
) -> BatchResult:
    """Ingest records with bounded concurrency and per-record isolation."""
    workers = max(
        1,
        min(int(max_workers), 32),
    )

    iterator = iter(records)
    fatal: list[Exception] = []

    fn = lambda record: process_record(
        record,
        org_id,
        analyzer=analyzer,
    )

    items = list(
        _bounded_results(
            iterator,
            fn,
            workers,
            fatal.append,
        )
    )

    return BatchResult(
        items=items,
        fatal_error=(
            "batch iteration failure: "
            + type(fatal[0]).__name__
            if fatal
            else None
        ),
    )


def ingest_source(
    source,
    query: SearchQuery,
    org_id: str,
    *,
    limit: int = 50,
    max_workers: int = 1,
    analyzer: Callable | None = None,
) -> BatchResult:
    """Fetch a source lazily, then run the same bounded record pipeline."""
    try:
        records = source.fetch(
            query,
            limit=max(
                1,
                min(int(limit), 500),
            ),
        )

        result = ingest_records(
            records,
            org_id,
            max_workers=max_workers,
            analyzer=analyzer,
        )

        if result.fatal_error:
            obs.error(
                "runtime.source_failed",
                source=getattr(
                    source,
                    "name",
                    type(source).__name__,
                ),
                error_code=result.fatal_error,
            )

        return result

    except Exception as exc:
        obs.error(
            "runtime.source_failed",
            source=getattr(
                source,
                "name",
                type(source).__name__,
            ),
            error_type=type(exc).__name__,
        )

        return BatchResult(
            fatal_error="source failure: " + type(exc).__name__,
        )