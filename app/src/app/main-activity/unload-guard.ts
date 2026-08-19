// Warns on accidental refresh/close during a live task (see task-intro's "Do not
// refresh or close this page" note). Kept in its own module, rather than as an
// inline listener in component.ts, so exitTutorial() (tutorial.ts) can suppress
// it right before an intentional full-page navigation it triggers itself
// (window.location.href reassignment, handing off out of the tutorial) --
// without suppression, that navigation would trigger the browser's native
// "leave site?" prompt, and a participant who clicks "stay" there is left
// stuck: our own code already tore down the modal/tutorial state assuming the
// navigation would go through.
let suppressed = false;

export function installUnloadGuard(): void {
  window.addEventListener("beforeunload", (e) => {
    if (suppressed) return;
    e.preventDefault(); // Cancel the event
    e.returnValue = ""; // Chrome requires returnValue to be set
  });
}

export function suppressUnloadPrompt(): void {
  suppressed = true;
}
