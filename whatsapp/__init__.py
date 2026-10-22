"""WhatsApp messaging and monitoring.

Business workflows live in operations, HTTP endpoints in views, Meta clients
and credentials in meta, shared helpers in utils, and WebSocket handling in
realtime. Each package groups related modules by domain.

Django and Celery entry points (models, admin, urls, routing, and tasks) stay
at the application root. Import workflows and views from their domain modules
so package initialization does not load unrelated domains.
"""
