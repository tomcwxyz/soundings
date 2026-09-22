// Client-side logic for /contribute — the observation submission page.
//
// Handles three flows, all via the public API (proxied through Caddy or
// same-origin relative paths):
//   1. Existing-org magic link: search orgs, request link, "check your email"
//   2. New-org sign-up: name + place + email -> request magic link pre-filled
//   3. Observation submission (authenticated): form -> POST /v1/observations
//
// Autocomplete for places and orgs is powered by the find_place /
// find_organisations_in_place tools. The API base is read from the
// `data-api-base` attribute on the page root.

interface PlaceMatch {
  id: string;
  name: string;
  type: string;
}

interface OrgMatch {
  id: string;
  name: string;
}

const root = document.getElementById("contribute-root");
if (root) {
  const apiBase = root.dataset.apiBase ?? "";

  async function postJSON<T>(
    path: string,
    body: unknown,
  ): Promise<T> {
    const response = await fetch(apiBase + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
      credentials: "include",
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const errBody = await response.json();
        if (errBody && errBody.detail) detail = String(errBody.detail);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return (await response.json()) as T;
  }

  function show(el: HTMLElement | null) {
    if (el) el.hidden = false;
  }
  function hide(el: HTMLElement | null) {
    if (el) el.hidden = true;
  }
  function setError(containerId: string, message: string | null) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (message) {
      el.textContent = message;
      show(el);
    } else {
      el.textContent = "";
      hide(el);
    }
  }

  function makePlaceAutocomplete(
    inputId: string,
    hiddenId: string,
    resultsId: string,
    selectedLabelId?: string,
  ) {
    const input = document.getElementById(inputId) as HTMLInputElement | null;
    const hidden = document.getElementById(hiddenId) as HTMLInputElement | null;
    const results = document.getElementById(resultsId);
    const selectedLabel = selectedLabelId
      ? document.getElementById(selectedLabelId)
      : null;
    if (!input || !hidden || !results) return;
    // Non-null assertions: the guard above ensures these are non-null for
    // the closures below, but TS widens them back inside nested functions.
    const inputEl = input;
    const hiddenEl = hidden;
    const resultsEl = results;

    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    inputEl.addEventListener("input", () => {
      hiddenEl.value = "";
      if (selectedLabel) selectedLabel.textContent = "";
      const query = inputEl.value.trim();
      if (debounceTimer) clearTimeout(debounceTimer);
      if (query.length < 2) {
        resultsEl.innerHTML = "";
        hide(resultsEl);
        return;
      }
      debounceTimer = setTimeout(async () => {
        try {
          const out = await postJSON<{ matches: PlaceMatch[] }>(
            "/v1/tools/find_place",
            { query, limit: 8 },
          );
          renderResults(out.matches ?? []);
        } catch {
          resultsEl.innerHTML = "";
          hide(resultsEl);
        }
      }, 250);
    });

    function renderResults(matches: PlaceMatch[]) {
      resultsEl.innerHTML = "";
      if (matches.length === 0) {
        hide(resultsEl);
        return;
      }
      for (const m of matches) {
        const item = document.createElement("li");
        item.className = "autocomplete-item";
        item.textContent = `${m.name} (${m.type})`;
        item.addEventListener("click", () => {
          hiddenEl.value = m.id;
          inputEl.value = m.name;
          if (selectedLabel) selectedLabel.textContent = m.name;
          resultsEl.innerHTML = "";
          hide(resultsEl);
        });
        resultsEl.appendChild(item);
      }
      show(resultsEl);
    }

    document.addEventListener("click", (e) => {
      if (!resultsEl.contains(e.target as Node) && e.target !== inputEl) {
        hide(resultsEl);
      }
    });
  }

  function makeOrgAutocomplete(
    inputId: string,
    hiddenId: string,
    resultsId: string,
  ) {
    const input = document.getElementById(inputId) as HTMLInputElement | null;
    const hidden = document.getElementById(hiddenId) as HTMLInputElement | null;
    const results = document.getElementById(resultsId);
    const placeInput = document.getElementById(
      "existing-org-place-id",
    ) as HTMLInputElement | null;
    if (!input || !hidden || !results) return;
    const inputEl = input;
    const hiddenEl = hidden;
    const resultsEl = results;

    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    inputEl.addEventListener("input", () => {
      hiddenEl.value = "";
      const query = inputEl.value.trim();
      if (debounceTimer) clearTimeout(debounceTimer);
      if (query.length < 2) {
        resultsEl.innerHTML = "";
        hide(resultsEl);
        return;
      }
      debounceTimer = setTimeout(async () => {
        // Use find_organisations_in_place if a place is selected, else
        // fall back to a simple name search via find_place won't work —
        // we only have find_organisations_in_place which needs a place_id.
        // For the MVP, if no place is set, we show a hint to pick a place.
        const placeId = placeInput?.value;
        if (!placeId) {
          resultsEl.innerHTML =
            "<li class='autocomplete-hint'>Select a place first to search for organisations operating there.</li>";
          show(resultsEl);
          return;
        }
        try {
          const out = await postJSON<{ organisations: OrgMatch[] }>(
            "/v1/tools/find_organisations_in_place",
            { place_id: placeId, limit: 20 },
          );
          const filtered = (out.organisations ?? []).filter((o) =>
            o.name.toLowerCase().includes(query.toLowerCase()),
          );
          renderResults(filtered.slice(0, 10));
        } catch {
          resultsEl.innerHTML = "";
          hide(resultsEl);
        }
      }, 250);
    });

    function renderResults(matches: OrgMatch[]) {
      resultsEl.innerHTML = "";
      if (matches.length === 0) {
        resultsEl.innerHTML =
          "<li class='autocomplete-hint'>No organisations found. Try a new sign-up instead.</li>";
        show(resultsEl);
        return;
      }
      for (const m of matches) {
        const item = document.createElement("li");
        item.className = "autocomplete-item";
        item.textContent = m.name;
        item.addEventListener("click", () => {
          hiddenEl.value = m.id;
          inputEl.value = m.name;
          resultsEl.innerHTML = "";
          hide(resultsEl);
        });
        resultsEl.appendChild(item);
      }
      show(resultsEl);
    }

    document.addEventListener("click", (e) => {
      if (!resultsEl.contains(e.target as Node) && e.target !== inputEl) {
        hide(resultsEl);
      }
    });
  }

  // --- Flow 1: existing org magic link ------------------------------------
  const existingPlaceInput = document.getElementById(
    "existing-place-search",
  ) as HTMLInputElement | null;
  if (existingPlaceInput) {
    makePlaceAutocomplete(
      "existing-place-search",
      "existing-org-place-id",
      "existing-place-results",
    );
    makeOrgAutocomplete(
      "existing-org-search",
      "existing-org-id",
      "existing-org-results",
    );

    const existingForm = document.getElementById(
      "existing-org-form",
    ) as HTMLFormElement | null;
    if (existingForm) {
      existingForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const orgId = (
          document.getElementById("existing-org-id") as HTMLInputElement
        ).value;
        const email = (
          document.getElementById("existing-org-email") as HTMLInputElement
        ).value;
        setError("existing-org-error", null);
        if (!orgId) {
          setError(
            "existing-org-error",
            "Please select an organisation from the list.",
          );
          return;
        }
        const submitBtn = existingForm.querySelector(
          "button[type=submit]",
        ) as HTMLButtonElement;
        submitBtn.disabled = true;
        submitBtn.textContent = "Sending…";
        try {
          await postJSON<{ status: string }>("/v1/contribute/request-link", {
            organisation_id: orgId,
            email,
          });
          hide(existingForm);
          const success = document.getElementById("existing-org-success");
          show(success);
        } catch (err) {
          setError(
            "existing-org-error",
            err instanceof Error ? err.message : String(err),
          );
          submitBtn.disabled = false;
          submitBtn.textContent = "Send magic link";
        }
      });
    }
  }

  // --- Flow 2: new org sign-up --------------------------------------------
  const signupForm = document.getElementById(
    "signup-form",
  ) as HTMLFormElement | null;
  if (signupForm) {
    makePlaceAutocomplete(
      "signup-place-search",
      "signup-place-id",
      "signup-place-results",
    );

    signupForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = (
        document.getElementById("signup-name") as HTMLInputElement
      ).value.trim();
      const email = (
        document.getElementById("signup-email") as HTMLInputElement
      ).value.trim();
      const placeId = (
        document.getElementById("signup-place-id") as HTMLInputElement
      ).value;
      setError("signup-error", null);
      if (!placeId) {
        setError(
          "signup-error",
          "Please select a primary place from the suggestions.",
        );
        return;
      }
      const submitBtn = signupForm.querySelector(
        "button[type=submit]",
      ) as HTMLButtonElement;
      submitBtn.disabled = true;
      submitBtn.textContent = "Signing up…";
      try {
        const out = await postJSON<{
          status: string;
          organisation_id: string;
        }>("/v1/contribute/signup", {
          name,
          email,
          primary_place_id: placeId,
        });
        // After signup, pre-fill the magic-link form with the new org_id
        const prefillOrgId = document.getElementById(
          "prefill-org-id",
        ) as HTMLInputElement | null;
        const prefillOrgName = document.getElementById(
          "prefill-org-name",
        ) as HTMLInputElement | null;
        const prefillEmail = document.getElementById(
          "prefill-email",
        ) as HTMLInputElement | null;
        if (prefillOrgId) prefillOrgId.value = out.organisation_id;
        if (prefillOrgName) prefillOrgName.value = name;
        if (prefillEmail) prefillEmail.value = email;
        hide(signupForm);
        show(document.getElementById("signup-success"));
        show(document.getElementById("prefill-magic-link-section"));
      } catch (err) {
        setError(
          "signup-error",
          err instanceof Error ? err.message : String(err),
        );
        submitBtn.disabled = false;
        submitBtn.textContent = "Sign up";
      }
    });
  }

  // --- Flow 2b: pre-filled magic link request for newly signed-up org ------
  const prefillForm = document.getElementById(
    "prefill-magic-link-form",
  ) as HTMLFormElement | null;
  if (prefillForm) {
    prefillForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const orgId = (
        document.getElementById("prefill-org-id") as HTMLInputElement
      ).value;
      const email = (
        document.getElementById("prefill-email") as HTMLInputElement
      ).value;
      setError("prefill-error", null);
      const submitBtn = prefillForm.querySelector(
        "button[type=submit]",
      ) as HTMLButtonElement;
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending…";
      try {
        await postJSON<{ status: string }>("/v1/contribute/request-link", {
          organisation_id: orgId,
          email,
        });
        hide(prefillForm);
        show(document.getElementById("prefill-success"));
      } catch (err) {
        setError(
          "prefill-error",
          err instanceof Error ? err.message : String(err),
        );
        submitBtn.disabled = false;
        submitBtn.textContent = "Send magic link";
      }
    });
  }

  // --- Flow 3: observation submission (authenticated) ---------------------
  const obsForm = document.getElementById(
    "observation-form",
  ) as HTMLFormElement | null;
  if (obsForm) {
    makePlaceAutocomplete(
      "obs-place-search",
      "obs-place-id",
      "obs-place-results",
    );

    // Show/hide quantitative vs qualitative fields based on evidence_type
    const form = obsForm;
    function updateEvidenceFields() {
      const checked = form.querySelector<HTMLInputElement>(
        "input[name='evidence_type']:checked",
      );
      const quant = document.getElementById("quant-fields");
      const qual = document.getElementById("qual-fields");
      if (!checked) return;
      if (checked.value === "quantitative") {
        show(quant);
        hide(qual);
      } else {
        hide(quant);
        show(qual);
      }
    }
    const evidenceRadios = obsForm.querySelectorAll<HTMLInputElement>(
      "input[name='evidence_type']",
    );
    evidenceRadios.forEach((r) =>
      r.addEventListener("change", updateEvidenceFields),
    );
    updateEvidenceFields();

    const statementInput = document.getElementById(
      "statement",
    ) as HTMLTextAreaElement | null;
    const charCount = document.getElementById("statement-charcount");
    if (statementInput && charCount) {
      statementInput.addEventListener("input", () => {
        const len = statementInput.value.length;
        charCount.textContent = `${len} / 1000`;
      });
    }

    obsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("observation-error", null);
      const fd = new FormData(obsForm);
      const organisationId = root.dataset.organisationId ?? "";
      const placeId = (fd.get("place_id") ?? "") as string;
      if (!placeId) {
        setError(
          "observation-error",
          "Please select a place from the suggestions.",
        );
        return;
      }
      const evidenceType = fd.get("evidence_type") as string;
      const payload: Record<string, unknown> = {
        organisation_id: organisationId,
        place_id: placeId,
        period_start: fd.get("period_start"),
        theme: fd.get("theme"),
        statement: fd.get("statement"),
        evidence_type: evidenceType,
        confidence: fd.get("confidence"),
      };
      const periodEnd = fd.get("period_end");
      if (periodEnd) payload.period_end = periodEnd;
      const indicatorKey = fd.get("indicator_key");
      if (indicatorKey) payload.indicator_key = indicatorKey;
      if (evidenceType === "quantitative") {
        const val = fd.get("value");
        if (val !== null && val !== "") payload.value = Number(val);
        const unit = fd.get("unit");
        if (unit) payload.unit = unit;
      } else {
        const methodology = fd.get("methodology_note");
        if (methodology) payload.methodology_note = methodology;
      }

      const submitBtn = obsForm.querySelector(
        "button[type=submit]",
      ) as HTMLButtonElement;
      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting…";
      try {
        await postJSON<{ status: string; observation_id: string }>(
          "/v1/observations",
          payload,
        );
        hide(obsForm);
        show(document.getElementById("observation-success"));
      } catch (err) {
        setError(
          "observation-error",
          err instanceof Error ? err.message : String(err),
        );
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit observation";
      }
    });
  }

  // "Submit another" link — reset the form
  const submitAnother = document.getElementById("submit-another");
  if (submitAnother) {
    submitAnother.addEventListener("click", (e) => {
      e.preventDefault();
      const obsForm = document.getElementById(
        "observation-form",
      ) as HTMLFormElement | null;
      const success = document.getElementById("observation-success");
      if (obsForm) {
        obsForm.reset();
        updateEvidenceStateAfterReset(obsForm);
        show(obsForm);
      }
      if (success) hide(success);
    });
  }

  function updateEvidenceStateAfterReset(form: HTMLFormElement) {
    const quant = document.getElementById("quant-fields");
    const qual = document.getElementById("qual-fields");
    const checked = form.querySelector<HTMLInputElement>(
      "input[name='evidence_type']:checked",
    );
    if (checked && checked.value === "quantitative") {
      show(quant);
      hide(qual);
    } else {
      hide(quant);
      show(qual);
    }
  }
}
