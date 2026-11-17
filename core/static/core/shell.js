// Interface behaviour: collapsible sidebar, double-click protection, unsaved-changes warning, dialogs.
(function () {
  var root = document.documentElement, KEY = "yool.side";
  function setCollapsed(on) {
    root.classList.toggle("side-collapsed", on);
    var b = document.querySelector(".side-toggle");
    if (b) { b.setAttribute("aria-expanded", on ? "false" : "true"); b.setAttribute("aria-label", on ? "Expand menu" : "Collapse menu"); }
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) {}
  }
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.querySelector(".side-toggle");
    if (toggle) {
      var on = root.classList.contains("side-collapsed");
      toggle.setAttribute("aria-expanded", on ? "false" : "true");
      toggle.setAttribute("aria-label", on ? "Expand menu" : "Collapse menu");
      toggle.addEventListener("click", function () { setCollapsed(!root.classList.contains("side-collapsed")); });
    }
    // A second click on Save must not submit the form twice.
    document.querySelectorAll("form").forEach(function (f) {
      f.addEventListener("submit", function () {
        setTimeout(function () {
          f.querySelectorAll("input[type=submit],button[type=submit]").forEach(function (x) { x.disabled = true; });
        }, 0);
      });
    });
    window.addEventListener("pageshow", function () {
      document.querySelectorAll("input[type=submit],button[type=submit]").forEach(function (x) { x.disabled = false; });
    });
    // Warn before leaving a form with unsaved changes (attendance, grades, comments).
    document.querySelectorAll("form[data-warn-unsaved]").forEach(function (f) {
      var dirty = false;
      f.addEventListener("input", function () { dirty = true; });
      f.addEventListener("change", function () { dirty = true; });
      f.addEventListener("submit", function () { dirty = false; });
      window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
    });
    // Dialogs: <button data-open-dialog="id" data-action-url="..." data-title="..." data-name="..." ...>
    document.querySelectorAll("[data-open-dialog]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var d = document.getElementById(btn.getAttribute("data-open-dialog"));
        if (!d || !d.showModal) return;
        var f = d.querySelector("form");
        if (btn.dataset.actionUrl) f.action = btn.dataset.actionUrl;
        var heading = d.querySelector("[data-dialog-title]"); if (heading) heading.textContent = btn.dataset.title || heading.textContent;
        var sub = d.querySelector("[data-dialog-sub]"); if (sub && btn.dataset.sub) sub.textContent = btn.dataset.sub;
        ["name", "type", "max", "date"].forEach(function (k) {
          var map = { name: "title", type: "assessment_type", max: "max_score", date: "held_on" };
          var el = f.elements[map[k]]; if (el) el.value = btn.dataset[k] !== undefined ? btn.dataset[k] : (k === "type" ? el.options && el.options[0] ? el.options[0].value : "" : "");
        });
        var del = d.querySelector("[data-delete]");
        if (del) { if (btn.dataset.deleteUrl) { del.hidden = false; del.setAttribute("formaction", btn.dataset.deleteUrl); } else { del.hidden = true; } }
        d.showModal();
        var first = f.querySelector("input:not([type=hidden]),select"); if (first) first.focus();
      });
    });
    document.querySelectorAll("dialog [data-close]").forEach(function (b) {
      b.addEventListener("click", function () { b.closest("dialog").close(); });
    });
  });
})();
