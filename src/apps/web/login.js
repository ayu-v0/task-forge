const loginForm = document.getElementById("login-form");
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

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const validationError = validateForm();
  if (validationError) {
    setMessage(validationError, "error");
    return;
  }

  loginButton.disabled = true;
  loginButton.textContent = "Entering...";
  setMessage("Checking account...", "");
  let shouldResetButton = true;

  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({
        account: accountInput.value.trim(),
        password: passwordInput.value,
        remember_me: rememberInput.checked,
      }),
    });

    if (!response.ok) {
      setMessage("Invalid account or password.", "error");
      return;
    }

    const session = await response.json();
    const storage = rememberInput.checked ? window.localStorage : window.sessionStorage;
    storage.setItem("taskForgeLogin", JSON.stringify({
      account: session.account,
      signedInAt: new Date().toISOString(),
    }));

    setMessage("Session accepted. Opening console...", "success");
    shouldResetButton = false;
    window.setTimeout(() => {
      window.location.assign("/console/agents");
    }, 450);
  } catch (error) {
    setMessage("Unable to sign in. Check the API service and try again.", "error");
  } finally {
    if (shouldResetButton) {
      loginButton.disabled = false;
      loginButton.textContent = "Enter Console";
    }
  }
});

[accountInput, passwordInput].forEach((input) => {
  input.addEventListener("input", () => {
    if (loginMessage.classList.contains("error")) {
      setMessage("");
    }
  });
});
