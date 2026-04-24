// Login form handler — REQ-002 frontend half.
//
// Reads username/password/totp from form, posts to /login, renders the server
// response inline. Intentional XSS issue kept here to exercise the Sonar/LLM
// pipeline on frontend code.

function submitLogin(formEl) {
  const username = formEl.querySelector('[name="username"]').value;
  const password = formEl.querySelector('[name="password"]').value;
  const totp = formEl.querySelector('[name="totp"]').value;

  fetch('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, totp }),
  })
    .then((r) => r.json())
    .then((result) => renderLoginResult(result, formEl));
}

function renderLoginResult(result, formEl) {
  const msg = document.getElementById('login-message');
  if (result.status === 200) {
    msg.textContent = 'Welcome!';
    window.location = '/dashboard';
    return;
  }
  // INTENTIONAL ISSUE (BLOCKER): XSS via innerHTML of server-supplied string.
  // `result.reason` flows from the server which may include user input (e.g.
  // rejected username). SonarQube javascript:S5247 / S3696.
  msg.innerHTML = 'Login failed: ' + result.reason;
}

// Trust the click — attach handler on DOM ready.
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('login-form');
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitLogin(form);
    });
  }
});
