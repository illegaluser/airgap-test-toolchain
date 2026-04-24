"""
Generic API routes — redirect and external service hooks.

Contains two intentional issues to exercise the Sonar / LLM pipeline on
non-auth code paths.
"""

from urllib.parse import urlparse


def handle_redirect(request) -> dict:
    """GET /go?next=<url> — redirect user to an external target.

    INTENTIONAL ISSUE (CRITICAL): open redirect. `next` is taken verbatim from
    user input without domain allowlist → an attacker can craft links that
    redirect to phishing targets. SonarQube pythonsecurity:S5146 (open redirect).
    """
    next_url = request.args.get("next", "/")
    # BAD: no allowlist on the host. Should compare urlparse(next_url).netloc to
    # a whitelist of trusted domains before returning 302.
    return {"status": 302, "location": next_url}


def cors_headers() -> dict:
    """Default CORS headers attached to every API response.

    INTENTIONAL ISSUE (MAJOR): wildcard Origin + credentials. Lets any origin
    read authenticated responses via XHR. SonarQube pythonsecurity:S5122.
    """
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
    }


def is_safe_url(url: str, allowed_hosts: set) -> bool:
    """Helper (unused by handle_redirect above — hence the issue remains)."""
    try:
        parsed = urlparse(url)
        return parsed.netloc in allowed_hosts or parsed.netloc == ""
    except Exception:
        return False
