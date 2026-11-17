/* StockFlow · códigos QR de productos (dibujados en el navegador).
   Requiere qrcode-generator (window.qrcode). */
(function () {
    "use strict";
    function available() { return typeof window.qrcode === "function"; }

    function matrix(text) {
        const qr = window.qrcode(0, "M");
        qr.addData(String(text || ""));
        qr.make();
        return qr;
    }

    /** Dibuja el QR en un canvas de `size` px (con zona blanca de `quiet` módulos). */
    function draw(canvas, text, size, opts) {
        opts = opts || {};
        const quiet = opts.quiet == null ? 2 : opts.quiet;
        const caption = opts.caption || "";
        const qr = matrix(text);
        const n = qr.getModuleCount();
        const mod = Math.max(1, Math.floor(size / (n + quiet * 2)));
        const qrPx = mod * (n + quiet * 2);
        const capH = caption ? Math.round(qrPx * 0.12) : 0;
        canvas.width = qrPx;
        canvas.height = qrPx + capH;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#000";
        for (let r = 0; r < n; r++)
            for (let c = 0; c < n; c++)
                if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * mod, (r + quiet) * mod, mod, mod);
        if (caption) {
            ctx.font = "600 " + Math.round(capH * 0.55) + "px Arial, Helvetica, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(caption, qrPx / 2, qrPx + capH / 2 - mod);
        }
        return canvas;
    }

    function download(text, fileName, caption) {
        const canvas = draw(document.createElement("canvas"), text, 720, { quiet: 4, caption: caption });
        canvas.toBlob(function (blob) {
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = fileName || "codigo_qr.png";
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
        }, "image/png");
    }

    function slug(s) {
        return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
            .replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "producto";
    }

    // <canvas data-qr="CODIGO"> se dibuja solo; <button data-qr-download="CODIGO" data-qr-name="Nombre"> descarga
    function init() {
        document.querySelectorAll("canvas[data-qr]").forEach(function (c) {
            if (!available()) { c.replaceWith(Object.assign(document.createElement("p"), { className: "muted", textContent: "No se pudo cargar el generador de QR (revisa la conexión a internet)." })); return; }
            draw(c, c.dataset.qr, Number(c.dataset.qrSize || 220), { quiet: 2 });
        });
        document.addEventListener("click", function (e) {
            const b = e.target.closest("[data-qr-download]");
            if (!b) return;
            e.preventDefault();
            if (!available()) return alert("No se pudo cargar el generador de QR (revisa la conexión a internet).");
            const code = b.dataset.qrDownload;
            download(code, "QR_" + slug(b.dataset.qrName) + "_" + slug(code) + ".png", code.replace("#V", " · V"));
        });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
    window.StockFlowQR = { draw: draw, download: download, available: available };
})();
