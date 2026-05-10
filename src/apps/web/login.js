const loginForm = document.getElementById("login-form");
const workspaceInput = document.getElementById("workspace-input");
const accountInput = document.getElementById("account-input");
const passwordInput = document.getElementById("password-input");
const rememberInput = document.getElementById("remember-input");
const loginMessage = document.getElementById("login-message");
const loginButton = document.getElementById("login-button");

function setMessage(message, state = "") {
  loginMessage.textContent = message;
  loginMessage.className = `login-message${state ? ` ${state}` : ""}`;
}

function validateForm() {
  if (!workspaceInput.value.trim()) {
    return "Enter a workspace name.";
  }
  if (!accountInput.value.trim()) {
    return "Enter an account email.";
  }
  if (!accountInput.validity.valid) {
    return "Enter a valid email address.";
  }
  if (passwordInput.value.length < 6) {
    return "Password must be at least 6 characters.";
  }
  return "";
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const validationError = validateForm();
  if (validationError) {
    setMessage(validationError, "error");
    return;
  }

  loginButton.disabled = true;
  loginButton.textContent = "Entering...";
  setMessage("Session accepted. Opening console...", "success");

  const storage = rememberInput.checked ? window.localStorage : window.sessionStorage;
  storage.setItem("taskForgeLogin", JSON.stringify({
    workspace: workspaceInput.value.trim(),
    account: accountInput.value.trim(),
    signedInAt: new Date().toISOString(),
  }));

  window.setTimeout(() => {
    window.location.assign("/console/agents");
  }, 450);
});

[workspaceInput, accountInput, passwordInput].forEach((input) => {
  input.addEventListener("input", () => {
    if (loginMessage.classList.contains("error")) {
      setMessage("");
    }
  });
});
