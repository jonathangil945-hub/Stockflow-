/* StockFlow · selector de colores de la página.
   Se carga en <head> para aplicar el tema guardado antes de pintar (sin parpadeo). */
(function () {
    "use strict";
    var KEY = "stockflow_theme_v1";
    var ACCENTS = [
        { id: "azul",      name: "Azul",      c: "#3b82f6", c2: "#8b5cf6" },
        { id: "esmeralda", name: "Esmeralda", c: "#10b981", c2: "#06b6d4" },
        { id: "morado",    name: "Morado",    c: "#8b5cf6", c2: "#ec4899" },
        { id: "rosa",      name: "Rosa",      c: "#ec4899", c2: "#f97316" },
        { id: "naranja",   name: "Naranja",   c: "#f97316", c2: "#eab308" },
        { id: "rojo",      name: "Rojo",      c: "#ef4444", c2: "#f97316" },
        { id: "cian",      name: "Cian",      c: "#06b6d4", c2: "#3b82f6" },
        { id: "dorado",    name: "Dorado",    c: "#d4a017", c2: "#f97316" }
    ];
    var BACKGROUNDS = {
        noche:  { name: "Noche",  bg: "#0f172a", bg1: "#0b1120", soft: "#111827", bg3: "#1e293b", surface: "15, 23, 42" },
        carbon: { name: "Carbón", bg: "#18181b", bg1: "#0a0a0b", soft: "#1c1c1f", bg3: "#27272a", surface: "24, 24, 27" },
        bosque: { name: "Bosque", bg: "#0c1f17", bg1: "#06140e", soft: "#10261c", bg3: "#173426", surface: "12, 31, 23" },
        vino:   { name: "Vino",   bg: "#1f0c14", bg1: "#14060c", soft: "#2a0f1b", bg3: "#3a1626", surface: "31, 12, 20" },
        claro:  { name: "Claro",  bg: "#f1f5f9", bg1: "#e8eef6", soft: "#ffffff", bg3: "#e2e8f0", surface: "255, 255, 255" }
    };
    var DEFAULT = { accent: "azul", c: "#3b82f6", c2: "#8b5cf6", mode: "dark", bg: "noche" };

    function load() {
        try { var t = JSON.parse(localStorage.getItem(KEY) || "null"); if (t && t.c) return t; } catch (e) { }
        return Object.assign({}, DEFAULT);
    }
    function save(t) { try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) { } }

    function hexToRgb(h) {
        h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&");
        var n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    function rgbToHex(r) { return "#" + r.map(function (v) { v = Math.max(0, Math.min(255, Math.round(v))); return (v < 16 ? "0" : "") + v.toString(16); }).join(""); }
    function mix(hex, target, amt) { var a = hexToRgb(hex), b = hexToRgb(target); return rgbToHex(a.map(function (v, i) { return v + (b[i] - v) * amt; })); }
    function shiftHue(hex, deg) {
        var c = hexToRgb(hex).map(function (v) { return v / 255; }), r = c[0], g = c[1], b = c[2];
        var max = Math.max(r, g, b), min = Math.min(r, g, b), h = 0, s, l = (max + min) / 2, d = max - min;
        s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
        if (d) { h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60; }
        h = (h + deg + 360) % 360;
        var C = (1 - Math.abs(2 * l - 1)) * s, X = C * (1 - Math.abs((h / 60) % 2 - 1)), m = l - C / 2, p;
        p = h < 60 ? [C, X, 0] : h < 120 ? [X, C, 0] : h < 180 ? [0, C, X] : h < 240 ? [0, X, C] : h < 300 ? [X, 0, C] : [C, 0, X];
        return rgbToHex(p.map(function (v) { return (v + m) * 255; }));
    }

    function apply(t) {
        var root = document.documentElement, light = t.mode === "light";
        var bg = BACKGROUNDS[light ? "claro" : (BACKGROUNDS[t.bg] && t.bg !== "claro" ? t.bg : "noche")];
        var v = {
            "--primary": t.c, "--primary-rgb": hexToRgb(t.c).join(", "), "--primary-hover": mix(t.c, "#000000", 0.15),
            "--primary-light": light ? mix(t.c, "#000000", 0.35) : mix(t.c, "#ffffff", 0.45),
            "--accent2": t.c2, "--accent2-rgb": hexToRgb(t.c2).join(", "), "--accent2-hover": mix(t.c2, "#000000", 0.2),
            "--bg": bg.bg, "--bg-1": bg.bg1, "--bg-soft": bg.soft, "--bg-3": bg.bg3, "--surface-rgb": bg.surface
        };
        if (light) Object.assign(v, {
            "--fg-rgb": "15, 23, 42", "--text": "#0f172a", "--text-soft": "#1e293b", "--muted": "#475569", "--muted-2": "#64748b",
            "--border": "rgba(15, 23, 42, 0.14)", "--panel": "rgba(255, 255, 255, 0.82)", "--panel-strong": "rgba(255, 255, 255, 0.96)",
            "--shadow": "0 14px 32px rgba(15, 23, 42, 0.10)",
            "--ok-text": "#15803d", "--warn-text": "#b45309", "--danger-text": "#b91c1c"
        });
        else Object.assign(v, {
            "--fg-rgb": "255, 255, 255", "--text": "#f8fafc", "--text-soft": "#e2e8f0", "--muted": "#cbd5e1", "--muted-2": "#94a3b8",
            "--border": "rgba(255, 255, 255, 0.12)", "--panel": "rgba(255, 255, 255, 0.08)", "--panel-strong": "rgba(255, 255, 255, 0.12)",
            "--shadow": "0 20px 40px rgba(0, 0, 0, 0.28)",
            "--ok-text": "#bbf7d0", "--warn-text": "#fde68a", "--danger-text": "#fecaca"
        });
        for (var k in v) root.style.setProperty(k, v[k]);
        root.setAttribute("data-mode", light ? "light" : "dark");
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute("content", bg.bg);
    }

    var theme = load();
    apply(theme);

    function buildUI() {
        if (document.querySelector(".sf-theme-btn") || window.self !== window.top) return;
        var btn = document.createElement("button");
        btn.type = "button"; btn.className = "sf-theme-btn"; btn.title = "Cambiar colores"; btn.textContent = "🎨";
        var panel = document.createElement("div");
        panel.className = "sf-theme-panel"; panel.setAttribute("role", "dialog");
        document.body.appendChild(btn); document.body.appendChild(panel);

        function render() {
            var html = '<h4>🎨 Colores de la página <button type="button" data-close title="Cerrar">✕</button></h4>';
            html += '<div class="sf-label">Modo</div><div class="sf-row">' +
                '<button type="button" class="sf-mode' + (theme.mode !== "light" ? " active" : "") + '" data-mode="dark">🌙 Oscuro</button>' +
                '<button type="button" class="sf-mode' + (theme.mode === "light" ? " active" : "") + '" data-mode="light">☀️ Claro</button></div>';
            html += '<div class="sf-label">Color principal</div><div class="sf-row">';
            ACCENTS.forEach(function (a) {
                html += '<button type="button" class="sf-swatch' + (theme.accent === a.id ? " active" : "") + '" data-accent="' + a.id +
                    '" title="' + a.name + '" style="background:linear-gradient(135deg,' + a.c + ',' + a.c2 + ')"></button>';
            });
            html += '<label class="sf-custom' + (theme.accent === "custom" ? " active" : "") + '" title="Elegir cualquier color">' +
                '<input type="color" data-custom value="' + theme.c + '"> Otro</label></div>';
            if (theme.mode !== "light") {
                html += '<div class="sf-label">Fondo</div><div class="sf-row">';
                ["noche", "carbon", "bosque", "vino"].forEach(function (id) {
                    var b = BACKGROUNDS[id];
                    html += '<button type="button" class="sf-swatch sf-bg' + (theme.bg === id ? " active" : "") + '" data-bg="' + id +
                        '" title="' + b.name + '" style="background:linear-gradient(135deg,' + b.bg1 + ',' + b.bg3 + ')"></button>';
                });
                html += "</div>";
            }
            html += '<button type="button" class="sf-reset" data-reset>Restablecer colores originales</button>';
            panel.innerHTML = html;
        }
        function update(patch) { Object.assign(theme, patch); save(theme); apply(theme); render(); }

        btn.addEventListener("click", function () { if (!panel.classList.contains("open")) render(); panel.classList.toggle("open"); });
        panel.addEventListener("click", function (e) {
            var el = e.target.closest("button"); if (!el) return;
            if (el.hasAttribute("data-close")) return panel.classList.remove("open");
            if (el.hasAttribute("data-reset")) return update(Object.assign({}, DEFAULT));
            if (el.dataset.mode) return update({ mode: el.dataset.mode });
            if (el.dataset.bg) return update({ bg: el.dataset.bg });
            if (el.dataset.accent) {
                var a = ACCENTS.filter(function (x) { return x.id === el.dataset.accent; })[0];
                if (a) update({ accent: a.id, c: a.c, c2: a.c2 });
            }
        });
        panel.addEventListener("input", function (e) {
            if (!e.target.hasAttribute("data-custom")) return;
            var c = e.target.value;
            Object.assign(theme, { accent: "custom", c: c, c2: shiftHue(c, 40) }); save(theme); apply(theme);
        });
        panel.addEventListener("change", function (e) { if (e.target.hasAttribute("data-custom")) render(); });
        document.addEventListener("click", function (e) {
            if (panel.classList.contains("open") && !panel.contains(e.target) && e.target !== btn) panel.classList.remove("open");
        });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") panel.classList.remove("open"); });
    }
    // Otras pestañas abiertas se actualizan al cambiar el tema
    window.addEventListener("storage", function (e) { if (e.key === KEY) { theme = load(); apply(theme); } });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", buildUI); else buildUI();
    window.StockFlowTheme = { apply: apply, get: function () { return theme; } };
})();
