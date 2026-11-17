// Small behaviour only: theme toggle, local times, and refreshing the content every poll interval.
// No business logic: the server sends display-ready content (docs/ui.md).
(function () {
  "use strict";

  var THEME_KEY = "hfm-dashboard-theme";
  var root = document.documentElement;

  function readTheme() {
    try { return window.localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function saveTheme(value) {
    try { window.localStorage.setItem(THEME_KEY, value); } catch (e) { /* private mode: ignore */ }
  }
  var saved = readTheme();
  if (saved === "light" || saved === "dark") { root.setAttribute("data-theme", saved); }

  function localTimes(scope) {
    var nodes = scope.querySelectorAll("time[data-local]");
    for (var i = 0; i < nodes.length; i++) {
      var t = nodes[i];
      var iso = t.getAttribute("datetime");
      if (!iso) { continue; }
      var d = new Date(iso);
      if (isNaN(d.getTime())) { continue; }
      var opts = t.hasAttribute("data-time-only")
        ? { hour: "2-digit", minute: "2-digit" }
        : { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" };
      t.textContent = d.toLocaleString(undefined, opts);
      t.title = d.toISOString().replace(".000Z", "Z") + " (UTC)";
    }
  }

  function refresh(content) {
    var url = new URL(window.location.href);
    url.searchParams.set("partial", "1");
    fetch(url.toString(), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error(String(r.status)); } return r.text(); })
      .then(function (html) { content.innerHTML = html; localTimes(content); })
      .catch(function () { /* keep the last content; the stale banner appears once polls stop */ });
  }

  document.addEventListener("DOMContentLoaded", function () {
    localTimes(document);
    var toggle = document.querySelector("[data-theme-toggle]");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
        root.setAttribute("data-theme", next);
        saveTheme(next);
      });
    }
    var content = document.getElementById("content");
    var seconds = content ? parseInt(content.getAttribute("data-refresh-seconds"), 10) : 0;
    if (content && seconds > 0) {
      window.setInterval(function () { refresh(content); }, seconds * 1000);
    }
  });
})();
