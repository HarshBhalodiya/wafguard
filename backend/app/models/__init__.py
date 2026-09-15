"""
Reserved for typed MongoDB document models, per the project's FastAPI
layout. Currently empty by choice: repositories (app/database/repositories)
work directly with plain dicts (matching what pymongo returns natively),
and app/schemas holds the Pydantic models that actually validate API
request/response bodies. Add a model here if a collection's write path
grows complex enough to need document-level validation before insertion.
"""
