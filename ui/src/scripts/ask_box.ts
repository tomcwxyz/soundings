// Client behaviour for the AskBox component.
//
// Each example chip fills the question input and submits the form, teaching
// the user what kinds of questions work (the model infers intent from the
// text — there is no explicit mode).
//
// `.ask-box` IS the form element, so init operates on it directly.

import {
  askHistoryHref,
  recentQuestions,
} from "../lib/ask_history";

function renderRecentQuestions(form: HTMLFormElement): void {
  const host = form.querySelector<HTMLElement>("[data-ask-history]");
  if (!host) return;

  const placeId =
    form.querySelector<HTMLInputElement>("input[name='place_id']")?.value ||
    undefined;
  const entries = recentQuestions(placeId).slice(0, 4);
  host.innerHTML = "";

  if (entries.length === 0) {
    host.hidden = true;
    return;
  }

  const label = document.createElement("span");
  label.className = "recent-questions-label";
  label.textContent = "Recently asked";
  host.appendChild(label);

  for (const entry of entries) {
    const link = document.createElement("a");
    link.className = "recent-question";
    link.href = askHistoryHref(entry);
    link.textContent = entry.query;
    host.appendChild(link);
  }
  host.hidden = false;
}

export function initAskBox(form: HTMLFormElement): void {
  const chips = form.querySelectorAll<HTMLButtonElement>(".example-chip");
  const qInput = form.querySelector<HTMLInputElement>("input[name='q']");
  if (!qInput) return;

  renderRecentQuestions(form);
  window.addEventListener("soundings:ask-history-updated", () => {
    renderRecentQuestions(form);
  });

  for (const chip of chips) {
    chip.addEventListener("click", () => {
      qInput.value = chip.dataset.example ?? "";
      form.requestSubmit();
    });
  }
}

export function initAllAskBoxes(root: ParentNode = document): void {
  root
    .querySelectorAll<HTMLFormElement>(".ask-box")
    .forEach(initAskBox);
}

initAllAskBoxes();
