// Dummy-data walkthrough for the CONTROL page, shown when a participant
// arrives via ?tutorial=1 (e.g. from a Qualtrics redirect). Kept isolated
// from component.ts so the tour copy/steps can change without touching the
// main activity logic.
//
// Flow: phase 1 is the real prior-belief elicitation modal running against
// the dummy dataset (no tour needed — it's already a guided wizard).
// component.ts calls startTutorial() below once that modal closes, which
// runs phase 2: a driver.js tour of the main page. Interactive steps
// disable the "Next" button and only advance once the participant actually
// performs the action, rather than letting them click through blind.
import { driver } from "driver.js";

const TUTORIAL_QUERY_PARAM = "tutorial";

export function isTutorialRequested(params: { [key: string]: string }): boolean {
  return params[TUTORIAL_QUERY_PARAM] === "1" || params[TUTORIAL_QUERY_PARAM] === "true";
}

/**
 * Sends the participant to the task-intro briefing page with the tutorial
 * flag stripped and appType/level forced to the real CONTROL task, so
 * "Start the Task" there lands in the actual live study with a fresh
 * (non-tutorial) component instance and a real socket connection. This is
 * the only exit path out of the tutorial — triggered from the real "Submit
 * Task" confirm flow once the dummy task is done.
 */
export function exitTutorial(): void {
  const url = new URL(window.location.href);
  url.searchParams.delete(TUTORIAL_QUERY_PARAM);
  // Only fill in "type" if it's missing entirely -- don't clobber whatever
  // value/alias (e.g. "F") was already there. The tutorial forces CONTROL
  // layout at runtime regardless of what's in the URL (see isTutorialRequested
  // handling in component.ts), so this is purely about not rewriting the
  // participant's original link.
  if (!url.searchParams.has("type")) {
    url.searchParams.set("type", "CONTROL");
  }
  if (!url.searchParams.has("level")) {
    url.searchParams.set("level", "live");
  }
  url.pathname = "/task-intro";
  window.location.href = url.toString();
}

/**
 * Advances the tour past `stepIndex` once the cursor has genuinely dwelled
 * on `selector` for `dwellMs`, not just brushed past it. `mouseover`/`mouseout`
 * turned out unreliable here — they fire on entry regardless of cause (e.g.
 * the browser re-hit-testing under an already-stationary cursor when driver.js
 * repositions/scrolls the target into view) and also re-fire when the cursor
 * crosses between sibling data points inside the plot. `mousemove` can only
 * ever come from real pointer input, so use sustained mousemove activity as
 * the actual dwell signal instead, with `mouseleave` (which — unlike
 * `mouseout` — doesn't re-fire for descendant elements) to reset it.
 *
 * Stays armed for the tour's whole lifetime (never removes its own
 * listeners) so that clicking "Previous" back into this step and redoing
 * the hover still works — a one-shot listener would leave "Next" stuck
 * hidden forever after the first successful pass.
 */
function advanceOnHoverDwell(
  tour: ReturnType<typeof driver>,
  stepIndex: number,
  selector: string,
  dwellMs: number = 400,
  requireTargetSelector?: string
): void {
  const el = document.querySelector(selector);
  if (!el) return;
  let enteredAt: number | null = null;
  let checkInterval: any = null;
  const stopChecking = () => {
    if (checkInterval) {
      clearInterval(checkInterval);
      checkInterval = null;
    }
  };
  const reset = () => {
    enteredAt = null;
    stopChecking();
  };
  // d3 re-renders the plot's points on every data/interaction update, so
  // delegate from the stable #plot_container rather than binding straight to
  // a single <circle> (which would go stale the moment d3 replaces it).
  const onMove = (event: Event) => {
    if (tour.getActiveIndex() !== stepIndex) return;
    if (requireTargetSelector) {
      const target = event.target as Element | null;
      if (!target || !target.closest(requireTargetSelector)) {
        reset();
        return;
      }
    }
    if (enteredAt === null) {
      enteredAt = Date.now();
      checkInterval = setInterval(() => {
        if (tour.getActiveIndex() !== stepIndex) {
          stopChecking();
          return;
        }
        if (enteredAt !== null && Date.now() - enteredAt >= dwellMs) {
          stopChecking();
          tour.moveNext();
        }
      }, 80);
    }
  };
  el.addEventListener("mousemove", onMove);
  el.addEventListener("mouseleave", reset);
}

/**
 * Reveals the current step's "Next" button once `eventType` fires on
 * `selector`, instead of auto-advancing straight past it. Lets the
 * participant confirm they're ready rather than being jumped to the next
 * step the instant they interact. Stays armed for the tour's whole lifetime
 * (see advanceOnHoverDwell for why) so redoing the interaction after
 * clicking "Previous" back into this step still reveals it again.
 */
function revealNextOnEvent(
  tour: ReturnType<typeof driver>,
  stepIndex: number,
  selector: string,
  eventType: "click" | "mouseover" = "click"
): void {
  const el = document.querySelector(selector);
  if (!el) return;
  el.addEventListener(eventType, () => {
    if (tour.getActiveIndex() !== stepIndex) return;
    const nextBtn = document.querySelector(".driver-popover-next-btn") as HTMLElement | null;
    if (nextBtn) nextBtn.style.display = "block";
  });
}

/**
 * Advances the tour past `stepIndex` whenever `eventType` fires on
 * `selector` while that step is actually active — a stray event before (or
 * after) its step is current is ignored. Stays armed for the tour's whole
 * lifetime (see advanceOnHoverDwell for why) so redoing the interaction
 * after clicking "Previous" back into this step still works.
 */
function advanceOnEvent(
  tour: ReturnType<typeof driver>,
  stepIndex: number,
  selector: string,
  eventType: "click" | "mouseover" = "click",
  requireTargetSelector?: string
): void {
  const el = document.querySelector(selector);
  if (!el) return;
  el.addEventListener(eventType, (event) => {
    if (tour.getActiveIndex() !== stepIndex) return;
    if (requireTargetSelector) {
      const target = event.target as Element | null;
      if (!target || !target.closest(requireTargetSelector)) return;
    }
    tour.moveNext();
  });
}

/**
 * Bin labels come from binning.service as plain "150000-200000" (no
 * formatting, since that service is generic across attributes). This is
 * specifically about the Price attribute, so reformat each side as currency
 * for the explanation text.
 */
function formatPriceRange(rangeLabel: string): string {
  const parts = rangeLabel.split("-");
  if (parts.length !== 2) return rangeLabel;
  const fmt = (s: string) => {
    const n = Number(s);
    return Number.isFinite(n) ? `$${n.toLocaleString()}` : s;
  };
  return `${fmt(parts[0])}-${fmt(parts[1])}`;
}

/**
 * Special-cased gate for the "Placing Tokens" step: instead of just
 * unlocking "Next" the moment a token is placed, rewrite the *current*
 * popover's own text to explain the specific bin column that was just
 * clicked (reading its actual range label and current count out of the
 * DOM), and reveal its "Next" button so the participant continues manually.
 *
 * This deliberately does NOT open a second driver.js tour instance here:
 * driver.js 1.3.1 keeps a single module-level state object shared by every
 * `driver()` call, not per-instance state, so a second concurrent instance
 * clobbers the first one's state, and calling the first tour's moveNext()
 * afterward silently fails. Mutating the live popover DOM directly avoids
 * that entirely.
 */
function armTokenPlacementExplanation(tour: ReturnType<typeof driver>, stepIndex: number, selector: string): void {
  const el = document.querySelector(selector);
  if (!el) return;
  el.addEventListener("click", (event) => {
    if (tour.getActiveIndex() !== stepIndex) return;
    const target = event.target as Element | null;
    const binColumn = target?.closest(".bin-column");
    if (!binColumn) return;
    const rawRangeLabel = binColumn.querySelector(".label")?.textContent?.trim() || "this range";
    const rangeLabel = formatPriceRange(rawRangeLabel);
    const countText = binColumn.querySelector(".count")?.textContent?.trim() || "0";
    const group = binColumn.querySelector(".count-a") ? "with" : "without";
    const titleEl = document.querySelector(".driver-popover-title");
    const descEl = document.querySelector(".driver-popover-description");
    const nextBtn = document.querySelector(".driver-popover-next-btn") as HTMLElement | null;
    if (titleEl) titleEl.textContent = "What This Means";
    if (descEl) {
      descEl.textContent = `Out of 30 homes ${group} central air conditioning, you expect ${countText} to have a price between ${rangeLabel}.`;
    }
    if (nextBtn) {
      nextBtn.style.display = "block";
      nextBtn.classList.remove("driver-popover-btn-disabled");
    }
  });
}

/**
 * Short driver.js walkthrough of the elicitation modal itself (phase 1).
 * Should be called once, right after the modal has mounted (showPriorModal
 * flips true and Angular has had a tick to render it). Explains the
 * token-placement mechanic before handing control back to the modal's own
 * built-in instructions/buttons.
 */
export function startElicitationIntro(): void {
  const tour = driver({
    showProgress: true,
    allowClose: false,
    showButtons: ["next", "previous"],
    overlayOpacity: 0.55,
    stagePadding: 6,
    steps: [
      {
        popover: {
          title: "Tutorial Overview",
          description:
            "This is a tutorial. It's intended to walk you through how the interface works, using sample data, before you begin the real task. Nothing you do here is recorded, and the real task will be given to you once you finish.",
        },
      },
      {
        popover: {
          title: "About the Sample Data",
          description:
            "This practice round uses a small sample of home sale records, things like lot size, number of rooms, and sale price.",
        },
      },
      {
        popover: {
          title: "Purpose of This Section",
          description:
            "This section will ask you to estimate how common different traits are among two groups of homes: those with central air conditioning, and those without, at the same time, side by side.",
        },
      },
      {
        element: ".modal-header-left",
        popover: {
          title: "Instructions",
          description:
            "Before the real page, here's a practice round using one sample question. This text at the top always tells you what to do at each step, read it as you go. Teal is for one group, orange for the other; each bin stacks both, teal at the bottom.",
          side: "bottom",
        },
      },
      {
        element: ".histogram-box",
        popover: {
          title: "Placing Tokens",
          description: 'Click a slot to jump straight to that count, or use the "+"/"−" buttons: teal for the first group, orange for the second. Each has its own 30 tokens. Try it now.',
          side: "top",
          showButtons: ["previous"],
        },
      },
      {
        // Each distribution now has its own Uniform/Reset pair; querySelector
        // picks the first (teal) one, but the same controls exist identically
        // for the second (orange) distribution below it.
        element: ".preset-bar",
        popover: {
          title: "Additional Controls",
          description: 'Each distribution has its own "Uniform" (split its tokens evenly across all ranges) and "Reset" (clear it and start over) buttons.',
          side: "bottom",
        },
      },
      {
        element: ".modal-balls-counter",
        popover: {
          title: "Remaining Tokens",
          description: "Each distribution shows how many of its own tokens are left. You must place all of both before continuing.",
          side: "bottom",
        },
      },
      {
        element: ".modal-footer button.primary",
        popover: {
          title: "Saving Your Response",
          description: 'Once all tokens are placed, click "Save & continue" here to move to the next question.',
          side: "top",
        },
      },
    ],
  });

  tour.drive();

  // Step 4: don't allow "Next" until they place at least one token; explain
  // what that placement means before letting them continue.
  armTokenPlacementExplanation(tour, 4, ".histogram-box");

  // The confidence slider only exists once the participant has for real
  // placed all 60 tokens (both distributions) and clicked "Save & continue"
  // — the scripted tour above can't wait for that without forcing full
  // completion, so watch the DOM and pop up a one-off explanation whenever
  // that screen actually appears.
  watchForConfidenceStep();
}

function watchForConfidenceStep(): void {
  const modalBody = document.querySelector(".modal-body");
  if (!modalBody) return;
  const observer = new MutationObserver(() => {
    const slider = document.querySelector(".confidence-slider");
    if (!slider) return;
    observer.disconnect();
    const confidenceTour = driver({
      allowClose: false,
      showButtons: ["next"],
      overlayOpacity: 0.55,
      steps: [
        {
          element: ".confidence-slider",
          popover: {
            title: "How Confident Are You?",
            description: "Drag the slider to say how confident you are in the tokens you just placed, then continue.",
            side: "top",
          },
        },
      ],
    });
    confidenceTour.drive();
  });
  observer.observe(modalBody, { childList: true, subtree: true });
}

/**
 * Reveals "Next" (never auto-advances) once both the X and Y axis ng-select
 * dropdowns actually have a value chosen. ng-select doesn't dispatch a plain
 * DOM "change" event a raw listener can catch, so watch the DOM instead of
 * hooking a single click/event.
 *
 * Deliberately just reveals rather than calling moveNext(): this observer
 * stays armed permanently (see advanceOnEvent for why) and fires on every
 * subsequent dropdown change too, not just the first time both get filled.
 * If it auto-advanced, going back with "Previous" and merely trying out a
 * different axis value (with both still non-empty) would yank the tour
 * forward again the instant that change registered -- revealing the button
 * instead leaves the "when to move on" decision with the participant.
 */
function revealNextWhenBothAxesSelected(tour: ReturnType<typeof driver>, stepIndex: number): void {
  const panel = document.querySelector("#tutorial-encoding-panel");
  if (!panel) return;
  const bothSelected = () =>
    !!panel.querySelector('ng-select[name="xVarSelect"] .ng-value') &&
    !!panel.querySelector('ng-select[name="yVarSelect"] .ng-value');
  const reveal = () => {
    if (tour.getActiveIndex() !== stepIndex) return;
    const nextBtn = document.querySelector(".driver-popover-next-btn") as HTMLElement | null;
    if (!nextBtn) return;
    // Also hides it again if a value gets cleared after being revealed --
    // e.g. via ng-select's "clear" (x) button -- not just a one-way reveal.
    nextBtn.style.display = bothSelected() ? "block" : "none";
  };
  const observer = new MutationObserver(reveal);
  observer.observe(panel, { childList: true, subtree: true });
}

/**
 * Starts the driver.js tour over the live CONTROL DOM. Should be called
 * once, after the dummy dataset has loaded, the chart has rendered, and the
 * prior-belief elicitation modal (phase 1) has closed.
 */
export function startTutorial(): void {
  const tour = driver({
    showProgress: true,
    allowClose: false,
    showButtons: ["next", "previous"],
    overlayOpacity: 0.55,
    steps: [
      {
        popover: {
          title: "Quick walkthrough",
          description:
            "This is the exploration user interface, using the same small sample of home sale records. Try each step yourself as you go, click Next to begin.",
        },
      },
      {
        element: "#tutorial-encoding-panel",
        popover: {
          title: "Encoding",
          description: "Choose what's shown on the X and Y axis. Pick a value in both dropdowns to continue.",
          side: "right",
          showButtons: ["previous"],
        },
        // If the participant goes "Previous" back into this step after
        // already picking both axes, don't make them reselect just to see
        // "Next" again -- reveal it immediately since the requirement is
        // already met.
        onHighlighted: () => {
          const panel = document.querySelector("#tutorial-encoding-panel");
          const bothSelected =
            !!panel?.querySelector('ng-select[name="xVarSelect"] .ng-value') &&
            !!panel?.querySelector('ng-select[name="yVarSelect"] .ng-value');
          if (!bothSelected) return;
          const nextBtn = document.querySelector(".driver-popover-next-btn") as HTMLElement | null;
          if (nextBtn) nextBtn.style.display = "block";
        },
      },
      {
        element: "#plot_container",
        popover: {
          title: "Visualization",
          description: "Each point is a home. Hover over one to see its details.",
          side: "top",
          showButtons: ["previous"],
        },
      },
      {
        // Highlights the shared wrapper around BOTH #plot_container and
        // #tutorial-details-panel (rather than just one or the other) so
        // both are visibly spotlighted together. Descendants of the active
        // element keep pointer-events, so the plot stays hoverable too.
        element: "#tutorial-vis-column",
        popover: {
          title: "Details",
          description: "This is the information regarding the home you are hovering over. Hovering (or selecting) a point always shows its full details here.",
          // driver.js's automatic side/align placement keeps landing this
          // popover mid-screen against such a tall highlighted element, so
          // position it manually against the details panel's own actual
          // bounding box instead of trusting the side/align heuristics.
          // onPopoverRender fires BEFORE driver.js's own internal positioning
          // pass runs (confirmed in driver.js's source: it calls this hook,
          // then immediately repositions the popover afterward) — a deferred
          // setTimeout(0) runs after that pass completes, so this doesn't get
          // silently overwritten.
          onPopoverRender: (popoverDom) => {
            setTimeout(() => {
              const detailsPanel = document.querySelector("#tutorial-details-panel");
              if (!detailsPanel) return;
              const rect = detailsPanel.getBoundingClientRect();
              popoverDom.wrapper.style.position = "fixed";
              popoverDom.wrapper.style.top = `${rect.top}px`;
              popoverDom.wrapper.style.left = `${rect.right + 12}px`;
              popoverDom.wrapper.style.right = "auto";
              popoverDom.wrapper.style.bottom = "auto";
            }, 0);
          },
        },
      },
      {
        element: "#tutorial-filters-panel",
        popover: {
          title: "Filters",
          description: "Click an attribute name below to expand its filter options.",
          side: "right",
          showButtons: ["previous"],
        },
      },
      {
        element: "#plot_container",
        popover: {
          title: "Selecting a Home",
          description: "Now click on a point to select it. This is different from hovering, as the main task will ask you to select data points to complete it. Clicking a selected point again removes it from your list.",
          side: "top",
          showButtons: ["previous"],
        },
      },
      {
        element: ".selected-subjects-panel",
        popover: {
          title: "Finish up",
          description:
            'Select 3 homes total, then click "Submit Task" below. That completes the tutorial and takes you to the real task.',
          side: "left",
        },
      },
    ],
  });

  tour.drive();

  // Step 1: don't allow "Next" until both X and Y axis are actually selected.
  revealNextWhenBothAxesSelected(tour, 1);
  // Step 2: don't allow "Next" until they hover a data point.
  advanceOnHoverDwell(tour, 2, "#plot_container", 400, ".post");
  // Step 4: don't allow "Next" until the participant actually expands a filter.
  revealNextOnEvent(tour, 4, "#tutorial-filters-panel");
  // Step 5: don't allow "Next" until they click a data point to select it.
  advanceOnEvent(tour, 5, "#plot_container", "click", ".post");
}
