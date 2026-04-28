import pytest

from zero_touch_qa.executor import QAExecutor


class _FakePage:
    def __init__(self):
        self.routes = []

    def route(self, pattern, handler, times=None):
        self.routes.append((pattern, times))


def test_mock_scope_guard_allows_narrow_local_pattern(monkeypatch):
    monkeypatch.delenv("MOCK_BLOCKED_HOSTS", raising=False)
    monkeypatch.delenv("MOCK_OVERRIDE", raising=False)
    monkeypatch.setenv("TARGET_URL", "https://staging.example.test")
    page = _FakePage()

    QAExecutor._install_mock_route(page, "**/api/list", body='{"items":[]}')

    assert page.routes == [("**/api/list", 1)]


def test_mock_scope_guard_blocks_broad_pattern_when_target_url_set(monkeypatch):
    monkeypatch.delenv("MOCK_OVERRIDE", raising=False)
    monkeypatch.setenv("TARGET_URL", "https://staging.example.test")

    with pytest.raises(ValueError, match="너무 넓어"):
        QAExecutor._install_mock_route(_FakePage(), "**/*", status=500)


def test_mock_scope_guard_blocks_configured_host(monkeypatch):
    monkeypatch.delenv("MOCK_OVERRIDE", raising=False)
    monkeypatch.setenv("MOCK_BLOCKED_HOSTS", "api.prod.example")

    with pytest.raises(ValueError, match="차단된 host"):
        QAExecutor._install_mock_route(
            _FakePage(), "https://api.prod.example/users", status=500
        )


def test_mock_scope_guard_override_allows_blocked_pattern(monkeypatch):
    monkeypatch.setenv("TARGET_URL", "https://staging.example.test")
    monkeypatch.setenv("MOCK_BLOCKED_HOSTS", "api.prod.example")
    monkeypatch.setenv("MOCK_OVERRIDE", "1")
    page = _FakePage()

    QAExecutor._install_mock_route(page, "https://api.prod.example/users", status=500)

    assert page.routes == [("https://api.prod.example/users", 1)]
