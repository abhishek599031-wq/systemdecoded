/* ===========================================================================
   Shared scene bootstrap.

   Two jobs:
     1. Load props — from `window.__PROPS__` (injected by Playwright) or from a
        `?props=<base64-json>` query param (so any scene is previewable in a
        browser by hand, which is the whole point of the HTML approach).
     2. Expose `window.seek(t)` and `window.ready`, the contract the renderer
        depends on.

   V1 renders stills at declared keyframes. `seek(t)` already drives all motion
   through a single deterministic function so V2 can capture real frames
   without touching any template (ARCH §2.2).
   =========================================================================== */

(function () {
  function readProps() {
    if (window.__PROPS__) return window.__PROPS__;
    const param = new URLSearchParams(location.search).get("props");
    if (param) {
      try {
        return JSON.parse(decodeURIComponent(escape(atob(param))));
      } catch (e) {
        console.error("Bad props param", e);
      }
    }
    return window.__DEMO_PROPS__ || {};
  }

  const props = readProps();
  window.PROPS = props;

  // Progress through the scene, 0..1. Templates read this in their draw().
  let progress = 1;
  window.getProgress = () => progress;

  /**
   * Deterministically place the scene at time `t` (0..1).
   * No requestAnimationFrame, no Date.now — the same t always yields the same
   * pixels, which is what makes renders reproducible.
   */
  window.seek = function seek(t) {
    progress = Math.max(0, Math.min(1, Number(t) || 0));
    document.documentElement.style.setProperty("--t", String(progress));
    if (typeof window.draw === "function") window.draw(progress);
    return progress;
  };

  // Escape hatch for text that comes from a script rather than a designer.
  window.esc = function esc(s) {
    return String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  };

  function boot() {
    if (new URLSearchParams(location.search).get("safe") === "1") {
      document.body.classList.add("show-safe");
    }
    if (typeof window.build === "function") window.build(props);

    const t = new URLSearchParams(location.search).get("t");
    window.seek(t === null ? 1 : parseFloat(t));

    // The renderer waits on this instead of a fixed sleep. Fonts must be ready
    // or text reflows after the screenshot and the render is subtly wrong.
    const done = () => {
      window.ready = true;
      document.body.setAttribute("data-ready", "1");
    };
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(done);
    } else {
      done();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
