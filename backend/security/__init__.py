"""Production security & observability layer for Facette.

Submodules:
- crypto:        AES-256-GCM (Fernet) field-level encryption for secrets/PII
- redactor:      Render sensitive fields as masked strings for non-superadmins
- monitoring:    Exception capture, error log persistence, threshold detection
- alerts:        Multi-channel alert dispatch (email/SMTP, in-app)
- circuit_breaker: Lightweight circuit breaker for external integrations
"""
