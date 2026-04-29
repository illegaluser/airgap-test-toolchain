"""Phase R-Plus experimental module — replay / enrich (back-inference) / compare (Doc↔Recording).

Once R-MVP is met, this module is operated separately from the main UI until
evaluation evidence (2-person rubric, 5-sample scores, 90% replay DoD) is in.
The activation switch is the env var ``RPLUS_ENABLED=1``. Without it,
`recording_service.server` does not include the router at all, so every
``/experimental/*`` endpoint returns 404.
"""
