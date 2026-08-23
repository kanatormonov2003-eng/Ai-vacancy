"""Structured error taxonomy. Users never see stack traces or internal detail."""
from __future__ import annotations

class AppError(Exception):
    code = "internal_error"
    status = 500
    safe_message = "Internal error"

    def __init__(self, message: str | None = None, *, details: dict | None = None, retryable: bool = False):
        super().__init__(message or self.safe_message)
        self.message = message or self.safe_message
        self.details = details or {}
        self.retryable = retryable

    def to_public(self, error_id: str) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details, "error_id": error_id}}

class ValidationError(AppError):
    code, status, safe_message = "validation_error", 400, "Invalid request"

class AuthError(AppError):
    code, status, safe_message = "unauthenticated", 401, "Authentication required"

class ForbiddenError(AppError):
    code, status, safe_message = "forbidden", 403, "Not allowed"

class NotFoundError(AppError):
    code, status, safe_message = "not_found", 404, "Not found"

class ConflictError(AppError):
    code, status, safe_message = "conflict", 409, "Conflict"

class RateLimitError(AppError):
    code, status, safe_message = "rate_limited", 429, "Too many requests"

class BudgetExceededError(AppError):
    code, status, safe_message = "budget_exceeded", 402, "Budget exceeded"

class ProviderError(AppError):
    code, status, safe_message = "provider_error", 502, "Upstream provider failed"

class ProviderTimeout(ProviderError):
    code, status, safe_message = "provider_timeout", 504, "Upstream provider timed out"

class CircuitOpenError(ProviderError):
    code, status, safe_message = "circuit_open", 503, "Provider temporarily disabled after repeated failures"

class PolicyError(AppError):
    code, status, safe_message = "policy_violation", 422, "Blocked by compliance policy"
