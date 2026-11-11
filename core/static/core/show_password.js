// Adds a Show / Hide button to every password box (login, change password, new user).
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    if (input.dataset.pwToggle) return;
    input.dataset.pwToggle = "1";
    var wrap = document.createElement("span");
    var onLogin = !!input.closest(".login");
    wrap.style.cssText = "position:relative;max-width:100%;display:" + (onLogin ? "block" : "inline-block");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.style.paddingRight = "64px";
    input.style.boxSizing = "border-box";
    if (onLogin) input.style.width = "100%";
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Show";
    button.setAttribute("aria-label", "Show password");
    button.setAttribute("aria-pressed", "false");
    button.style.cssText = "position:absolute;right:6px;top:50%;transform:translateY(-50%);padding:3px 9px;" +
      "font-size:12px;border:1px solid var(--border-color,#888);border-radius:4px;background:var(--body-bg,#fff);" +
      "color:var(--body-fg,#333);cursor:pointer;";
    button.addEventListener("click", function () {
      var reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "Hide" : "Show";
      button.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
      button.setAttribute("aria-pressed", reveal ? "true" : "false");
    });
    wrap.appendChild(button);
  });
});
