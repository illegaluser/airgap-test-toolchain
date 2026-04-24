// Session heartbeat + expiry banner — REQ-004 frontend half.
//
// Every 60s calls /api/ping. On 401 we show a banner and kick to /login.

const HEARTBEAT_INTERVAL_MS = 60 * 1000;

function startHeartbeat() {
  setInterval(() => {
    fetch('/api/ping', { credentials: 'include' })
      .then((r) => {
        if (r.status === 401) {
          showExpiryBanner();
          setTimeout(() => (window.location = '/login'), 3000);
        }
      })
      .catch(() => {
        /* network blip — ignore */
      });
  }, HEARTBEAT_INTERVAL_MS);
}

function showExpiryBanner() {
  const banner = document.createElement('div');
  banner.className = 'session-expiry-banner';
  banner.textContent = 'Your session has expired. Redirecting to login...';
  document.body.appendChild(banner);
}

// INTENTIONAL ISSUE (MAJOR): storing raw auth tokens in localStorage.
// localStorage is accessible from any script on the origin → XSS elevates to
// full account takeover. SonarQube javascript:S5732 or similar.
function storeAuthToken(token) {
  localStorage.setItem('authToken', token);
}

function getAuthToken() {
  return localStorage.getItem('authToken');
}

document.addEventListener('DOMContentLoaded', () => {
  if (getAuthToken()) {
    startHeartbeat();
  }
});
