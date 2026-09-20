(function () {
  "use strict";

  var body = document.body;
  var reducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* private mode etc. */ }
    return null;
  }

  // ---- Toast -------------------------------------------------------------
  var toastEl = document.getElementById("toast");
  var toastTimer = null;
  function toast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 2600);
  }

  // ---- Pull-rope lamp ----------------------------------------------------
  // Drag the rope down: the screen goes dark first, then the menu floats in.
  var lamp = document.getElementById("lamp");
  var grab = document.getElementById("lamp-grab");
  var knob = document.getElementById("rope-knob");
  var rope = document.getElementById("rope-line");
  var dark = document.getElementById("lamp-dark");
  var nav = document.getElementById("nav-drawer");
  var hint = document.getElementById("lamp-hint");

  var PULL_THRESHOLD = 30;   // px of drag that "clicks" the lamp
  var PULL_MAX = 64;         // furthest the rope stretches
  var ROPE_LEN = 34;
  var DARK_DELAY_MS = 550;   // menu waits for the screen to go dark
  var PULLS_KEY = "myportfolio_lamp_pulls";

  var isOpen = false;
  var locked = false;
  var dragging = false;
  var pulled = false;
  var startY = 0;
  var dragY = 0;
  var menuTimer = null;

  function setRope(dy) {
    var t = "translateY(" + dy + "px)";
    knob.style.transform = t;
    grab.style.transform = t;
    rope.style.transform = "scaleY(" + (1 + dy / ROPE_LEN) + ")";
  }

  function swing() {
    lamp.classList.remove("swing");
    void lamp.offsetWidth;
    lamp.classList.add("swing");
  }

  function pullCount() { return parseInt(store(PULLS_KEY), 10) || 0; }

  function hideHintIfLearned() {
    if (pullCount() >= 2) hint.classList.add("gone");
  }

  function openMenu() {
    isOpen = true;
    lamp.classList.add("on");
    dark.classList.add("on");
    body.classList.add("menu-open");
    grab.setAttribute("aria-expanded", "true");
    nav.setAttribute("aria-hidden", "false");
    store(PULLS_KEY, String(pullCount() + 1));
    clearTimeout(menuTimer);
    menuTimer = setTimeout(function () { nav.classList.add("open"); },
      reducedMotion ? 0 : DARK_DELAY_MS);
  }

  function closeMenu() {
    isOpen = false;
    clearTimeout(menuTimer);
    nav.classList.remove("open");
    lamp.classList.remove("on");
    grab.setAttribute("aria-expanded", "false");
    nav.setAttribute("aria-hidden", "true");
    setTimeout(function () {
      if (isOpen) return;
      dark.classList.remove("on");
      body.classList.remove("menu-open");
      hideHintIfLearned();
    }, 200);
  }

  function toggleMenu() {
    if (locked) return;
    locked = true;
    setTimeout(function () { locked = false; }, 950);
    if (isOpen) closeMenu(); else openMenu();
  }

  function tease() {
    if (dragging || isOpen) return;
    lamp.classList.remove("dragging");
    setRope(14);
    setTimeout(function () { if (!dragging) setRope(0); }, 260);
  }

  function nudge() {
    // A tap is not a pull: wiggle the rope and point at the instruction.
    tease();
    if (hint) {
      hint.classList.remove("gone", "pulse");
      void hint.offsetWidth;
      hint.classList.add("pulse");
      setTimeout(function () { hint.classList.remove("pulse"); }, 1000);
    }
  }

  if (lamp && grab && dark && nav) {
    hideHintIfLearned();

    grab.addEventListener("pointerdown", function (e) {
      dragging = true;
      pulled = false;
      dragY = 0;
      startY = e.clientY;
      lamp.classList.add("dragging");
      try { grab.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });

    grab.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      dragY = Math.max(0, Math.min(PULL_MAX, e.clientY - startY));
      setRope(dragY);
      if (!pulled && dragY >= PULL_THRESHOLD) {
        pulled = true;
        if (navigator.vibrate) { try { navigator.vibrate(18); } catch (err) {} }
        toggleMenu();
      }
    });

    function endDrag() {
      if (!dragging) return;
      dragging = false;
      lamp.classList.remove("dragging");
      setRope(0);
      if (pulled) swing();
      else if (dragY < 6) nudge();
    }
    grab.addEventListener("pointerup", endDrag);
    grab.addEventListener("pointercancel", endDrag);

    // Keyboard: Enter / Space (detail === 0) toggles the lamp.
    grab.addEventListener("click", function (e) {
      if (e.detail === 0) { toggleMenu(); swing(); }
    });

    // Click on the dark backdrop (not on a card) switches the light back on.
    nav.addEventListener("click", function (e) {
      if (!e.target.closest("a, button") && isOpen) toggleMenu();
    });
    dark.addEventListener("click", function () { if (isOpen) toggleMenu(); });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen) toggleMenu();
    });

    // Back/forward cache restore: never come back to a half-open dark screen.
    window.addEventListener("pageshow", function (e) {
      if (e.persisted && isOpen) { locked = false; closeMenu(); }
    });

    // Keep tugging the rope now and then until the visitor has learned it.
    setInterval(function () {
      if (!hint.classList.contains("gone") && !body.classList.contains("scrolled")) tease();
    }, 6000);
    setTimeout(tease, 1400);

    // Hide the instruction bubble once the page is scrolled.
    var ticking = false;
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(function () {
        body.classList.toggle("scrolled", window.scrollY > 120);
        ticking = false;
      });
    }, { passive: true });
  }

  // ---- Dark page transition ---------------------------------------------
  // The new page fades in from black (pure CSS). On the way out we fade to
  // black first, then navigate.
  var veil = document.getElementById("page-veil");
  var LEAVE_MS = reducedMotion ? 0 : 520;

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || !veil) return;
    if (a.target && a.target !== "_self") return;
    if (a.hasAttribute("download")) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#") return;
    var url;
    try { url = new URL(a.href, window.location.href); } catch (err) { return; }
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;

    e.preventDefault();
    veil.classList.add("leaving");
    setTimeout(function () { window.location.href = url.href; }, LEAVE_MS);
  });

  // Coming back via the browser's back button: clear the veil.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted && veil) veil.classList.remove("leaving");
  });

  // ---- Share this app ----------------------------------------------------
  function copyText(text, done, fail) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fail);
      return;
    }
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) done(); else fail();
    } catch (err) { fail(); }
  }

  function shareApp() {
    var url = window.location.origin + "/";
    var data = {
      title: body.getAttribute("data-share-title") || document.title,
      text: body.getAttribute("data-share-text") || "",
      url: url
    };
    if (navigator.share) {
      navigator.share(data).catch(function () { /* cancelled */ });
      return;
    }
    copyText(url, function () {
      toast("Link copied. Paste it anywhere to share.");
    }, function () {
      window.prompt("Copy this link to share:", url);
    });
  }

  document.querySelectorAll("[data-share]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      e.preventDefault();
      shareApp();
    });
  });

  // ---- Comment form ----------------------------------------------------
  var form = document.getElementById("comment-form");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var status = document.getElementById("form-status");
      var submitBtn = form.querySelector('button[type="submit"]');
      var formData = new FormData(form);

      status.className = "form-status";
      status.textContent = "";
      if (submitBtn) submitBtn.disabled = true;

      fetch("/contact", { method: "POST", body: formData })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok && data.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            status.className = "form-status error";
            status.textContent = result.data.error || "Something went wrong — please try again.";
            if (submitBtn) submitBtn.disabled = false;
            return;
          }
          status.textContent = "Posted. Thank you!";
          form.reset();
          setTimeout(function () { window.location.reload(); }, 700);
        })
        .catch(function () {
          status.className = "form-status error";
          status.textContent = "Something went wrong — please try again.";
          if (submitBtn) submitBtn.disabled = false;
        });
    });
  }

  // ---- PWA: service worker + install button ---------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
    });
  }

  var installBtn = document.getElementById("install-btn");
  var installItem = document.getElementById("install-item");
  var popup = document.getElementById("install-popup");
  var popupAccept = document.getElementById("install-accept");
  var popupDismiss = document.getElementById("install-dismiss");
  var deferredInstall = null;

  var SNOOZE_KEY = "myportfolio_install_snooze_until";
  var POPUP_SEEN_KEY = "myportfolio_install_popup_seen";
  var SNOOZE_DAYS = 7;
  var POPUP_DELAY_MS = 3500;

  var ua = navigator.userAgent || "";
  var isStandalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;
  var isIos = /iphone|ipad|ipod/i.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function safeGet(store, key) { try { return store.getItem(key); } catch (err) { return null; } }
  function safeSet(store, key, val) { try { store.setItem(key, val); } catch (err) {} }

  function snoozed() { return Date.now() < Number(safeGet(localStorage, SNOOZE_KEY) || 0); }
  function snooze(days) { safeSet(localStorage, SNOOZE_KEY, String(Date.now() + days * 86400000)); }

  function showInstall(show) {
    if (installItem) installItem.hidden = !show;
  }

  function hidePopup() {
    if (!popup) return;
    popup.classList.remove("show");
    setTimeout(function () { popup.hidden = true; }, 400);
  }

  // Show the pop-up once per browser session, a few seconds after arrival,
  // unless the app is already installed or the visitor said "Not now" lately.
  function schedulePopup(mode) {
    if (!popup || isStandalone || snoozed()) return;
    if (safeGet(sessionStorage, POPUP_SEEN_KEY)) return;
    setTimeout(function () {
      if (isStandalone || snoozed()) return;
      if (mode === "prompt" && !deferredInstall) return;
      safeSet(sessionStorage, POPUP_SEEN_KEY, "1");
      popup.setAttribute("data-mode", mode);
      popup.hidden = false;
      requestAnimationFrame(function () { requestAnimationFrame(function () { popup.classList.add("show"); }); });
    }, POPUP_DELAY_MS);
  }

  function runInstall() {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    deferredInstall.userChoice.then(function (choice) {
      deferredInstall = null;
      showInstall(false);
      hidePopup();
      if (!choice || choice.outcome !== "accepted") snooze(SNOOZE_DAYS);
    });
  }

  // Chrome, Edge, Samsung Internet, Opera (Android + PC) fire this event.
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredInstall = e;
    showInstall(true);
    schedulePopup("prompt");
  });

  // iPhone / iPad have no install event, so show the manual steps instead.
  if (isIos && !isStandalone) {
    window.addEventListener("load", function () { schedulePopup("ios"); });
  }

  if (installBtn) installBtn.addEventListener("click", runInstall);
  if (popupAccept) popupAccept.addEventListener("click", runInstall);
  if (popupDismiss) {
    popupDismiss.addEventListener("click", function () {
      hidePopup();
      snooze(SNOOZE_DAYS);
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && popup && !popup.hidden) popupDismiss.click();
  });

  window.addEventListener("appinstalled", function () {
    deferredInstall = null;
    showInstall(false);
    hidePopup();
    snooze(3650);
  });

  // ---- Live visitor tracking -------------------------------------------
  var VISITOR_KEY = "myportfolio_visitor_id";
  var HEARTBEAT_INTERVAL_MS = 12000;

  function getVisitorId() {
    var id = localStorage.getItem(VISITOR_KEY);
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() :
        "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
          var r = (Math.random() * 16) | 0;
          var v = c === "x" ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        }));
      localStorage.setItem(VISITOR_KEY, id);
    }
    return id;
  }

  function sendHeartbeat() {
    fetch("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitor_id: getVisitorId(), page: window.location.pathname }),
    }).catch(function () {});
  }

  sendHeartbeat();
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);

  function formatDuration(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    if (m <= 0) return s + "s";
    return m + "m " + s + "s";
  }

  function refreshLiveBadge() {
    fetch("/api/live-visitors")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var badge = document.getElementById("live-count");
        if (badge) badge.textContent = data.count;

        var total = document.getElementById("live-total-count");
        if (total) total.textContent = data.count;

        var list = document.getElementById("live-list");
        if (list) {
          list.innerHTML = "";
          if (data.visitors.length === 0) {
            list.innerHTML = '<p class="muted">No one else here right now.</p>';
          }
          data.visitors.forEach(function (v) {
            var row = document.createElement("div");
            row.className = "live-row";
            row.innerHTML =
              '<span class="live-dot small"></span>' +
              '<span class="live-visitor-id">Visitor ' + v.id + "</span>" +
              '<span class="live-visitor-page">' + v.page + "</span>" +
              '<span class="live-visitor-duration">' + formatDuration(v.duration_seconds) + "</span>";
            list.appendChild(row);
          });
        }
      })
      .catch(function () {});
  }

  refreshLiveBadge();
  setInterval(refreshLiveBadge, 5000);
})();
