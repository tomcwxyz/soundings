const banner = document.querySelector<HTMLElement>("[data-consent-banner]");

if (banner) {
  const options = banner.querySelector<HTMLElement>("[data-consent-options]");
  const toggle = banner.querySelector<HTMLButtonElement>("[data-consent-toggle]");
  const error = banner.querySelector<HTMLElement>("[data-consent-error]");
  const buttons = Array.from(
    banner.querySelectorAll<HTMLButtonElement>("[data-consent-level]"),
  );

  toggle?.addEventListener("click", () => {
    if (options) options.hidden = !options.hidden;
  });

  for (const button of buttons) {
    button.addEventListener("click", async () => {
      const consentLevel = button.dataset.consentLevel;
      if (!consentLevel) return;

      for (const candidate of buttons) candidate.disabled = true;
      if (error) {
        error.hidden = true;
        error.textContent = "";
      }

      try {
        const response = await fetch("/v1/capture/consent", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({ consent_level: consentLevel }),
          credentials: "include",
        });
        if (!response.ok) {
          throw new Error(`Consent update failed (${response.status})`);
        }
        window.location.reload();
      } catch (err) {
        if (error) {
          error.hidden = false;
          error.textContent =
            err instanceof Error ? err.message : "Could not save your choice.";
        }
        for (const candidate of buttons) candidate.disabled = false;
      }
    });
  }
}
