(function () {
    function wireNav(root) {
        var toggles = root.querySelectorAll("[data-app-nav-toggle]");
        toggles.forEach(function (btn) {
            var panelId = btn.getAttribute("aria-controls");
            if (!panelId) return;
            var panel = document.getElementById(panelId);
            if (!panel) return;

            btn.addEventListener("click", function () {
                var open = panel.classList.toggle("hidden") === false;
                btn.setAttribute("aria-expanded", open ? "true" : "false");
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            wireNav(document);
        });
    } else {
        wireNav(document);
    }
})();
