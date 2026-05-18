async function redirectIfAlreadyLoggedIn() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (response.ok) {
      window.location.replace("/");
    }
  } catch {
    // Stay on the login page when the dashboard session is not available.
  }
}

redirectIfAlreadyLoggedIn();
