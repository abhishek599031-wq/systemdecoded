"""YouTube integration: OAuth, Data API, and error classification.

Everything above this package talks to `oauth` / `data_api` and never makes an
HTTP call itself, so swapping the client (ADR 0002) stays contained here.
"""
