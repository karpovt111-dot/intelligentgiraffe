(function () {
    'use strict';

    // ---- ТЕМА ----
    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        try { localStorage.setItem('giraffic_theme', theme); } catch (e) {}
        var icon = document.querySelector('#themeToggle .theme-icon');
        if (icon) icon.textContent = (theme === 'dark') ? '☀️' : '🌙';
    }

    // применяем тему сразу, ещё до DOMContentLoaded
    try {
        applyTheme(localStorage.getItem('giraffic_theme') || 'light');
    } catch (e) {}

    document.addEventListener('DOMContentLoaded', function () {

        // ---- Кнопка темы ----
        var themeBtn = document.getElementById('themeToggle');
        if (themeBtn) {
            themeBtn.addEventListener('click', function (e) {
                e.preventDefault();
                var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
                applyTheme(isDark ? 'light' : 'dark');
            });
        }

        // ---- Дропдаун ----
        var dropdown = document.querySelector('.dropdown');
        if (dropdown) {
            var toggle = dropdown.querySelector('.dropdown-toggle');
            if (toggle) {
                toggle.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    dropdown.classList.toggle('open');
                });
            }
            document.addEventListener('click', function (e) {
                if (!dropdown.contains(e.target)) {
                    dropdown.classList.remove('open');
                }
            });
            dropdown.querySelectorAll('.dropdown-item').forEach(function (item) {
                item.addEventListener('click', function () {
                    dropdown.classList.remove('open');
                });
            });
        }
    });
})();