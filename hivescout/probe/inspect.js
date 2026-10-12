// Runs inside the page. Returns what a user can actually see and do right now.
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity || "1") > 0.05;
  };
  const text = (document.body && document.body.innerText) || "";
  const media = [...document.querySelectorAll("img,svg,canvas,video,picture")].filter(visible).length;
  const spinnerSel = [
    '[aria-busy="true"]', '[role="progressbar"]', ".animate-spin", ".spinner", ".loader", ".loading",
    '[class*="spinner"]', '[class*="Spinner"]', '[class*="skeleton"]', '[class*="Skeleton"]', '[data-loading="true"]',
  ].join(",");
  const spinners = [...document.querySelectorAll(spinnerSel)].filter(visible).length;
  const actions = [...document.querySelectorAll('button,a[href],[role="button"],input[type="submit"]')]
    .filter(visible)
    .map((el) => (el.innerText || el.getAttribute("aria-label") || el.value || "").trim())
    .filter(Boolean)
    .slice(0, 80);
  const alerts = [...document.querySelectorAll('[role="alert"],[role="status"],[aria-live]')]
    .filter(visible)
    .map((el) => (el.innerText || "").trim())
    .filter(Boolean);
  const controls = [...document.querySelectorAll('button,[role="button"],input[type="submit"]')].filter(visible);
  const disabled = controls.filter((el) => el.disabled || el.getAttribute("aria-disabled") === "true").length;
  const busy = [...document.querySelectorAll('[aria-busy="true"]')].length;
  return { text: text.slice(0, 20000), media, spinners, actions, alerts, disabled, busy, title: document.title, url: location.href };
}
