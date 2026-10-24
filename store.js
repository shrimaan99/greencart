// =====================================================================
//  store.js - small helpers for the shop pages
//  (search bar, mobile menu, filter drawer, quantity buttons)
// =====================================================================
(function () {
    function $(selector) { return document.querySelector(selector); }

    // ---- Search bar under the header ----
    var searchButton = $("#open-search");
    var searchPanel = $("#search-panel");
    if (searchButton && searchPanel) {
        searchButton.addEventListener("click", function () {
            searchPanel.classList.toggle("open");
            if (searchPanel.classList.contains("open")) { $("#search-input").focus(); }
        });
    }

    // ---- Menu button on phones ----
    var menuButton = $("#open-menu");
    var nav = $("#main-nav");
    if (menuButton && nav) {
        menuButton.addEventListener("click", function () { nav.classList.toggle("open"); });
    }

    // ---- Filter drawer on the shop page ----
    var drawer = $("#filter-drawer");
    var backdrop = $("#drawer-backdrop");
    function setDrawer(isOpen) {
        drawer.classList.toggle("open", isOpen);
        backdrop.classList.toggle("open", isOpen);
    }
    if (drawer && backdrop) {
        $("#open-filter").addEventListener("click", function () { setDrawer(true); });
        $("#close-filter").addEventListener("click", function () { setDrawer(false); });
        backdrop.addEventListener("click", function () { setDrawer(false); });
    }

    // ---- Quantity buttons ( - 1 + ) ----
    document.querySelectorAll(".stepper").forEach(function (box) {
        var input = box.querySelector("input");
        box.querySelectorAll("button").forEach(function (button) {
            button.addEventListener("click", function () {
                var step = Number(button.dataset.step);
                var min = input.min !== "" ? Number(input.min) : 0;
                var max = input.max !== "" ? Number(input.max) : Infinity;
                var next = (Number(input.value) || 0) + step;
                input.value = Math.min(max, Math.max(min, next));
            });
        });
    });

    // ---- Messages disappear after a few seconds ----
    document.querySelectorAll(".toast").forEach(function (toast) {
        setTimeout(function () { toast.classList.add("hide"); }, 5000);
    });
})();
