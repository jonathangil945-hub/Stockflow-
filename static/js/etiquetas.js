/* =====================================================================
 * StockFlow - Etiquetas para impresora térmica (P1 / ToPrint y similares)
 *
 * Dibuja cada etiqueta en un <canvas> al tamaño exacto del rollo
 * (ancho x alto en mm a 203 dpi = 8 puntos por mm) y la deja en blanco y
 * negro puro, que es lo que imprime una impresora térmica sin grises.
 * El PNG resultante se descarga o se comparte y se imprime desde la app
 * de la impresora (ToPrint: "Imagen" / "Imprimir imagen").
 *
 * Diseño único: QR a la izquierda y, a la derecha, tienda, producto,
 * talla · color arriba; precio grande y código abajo.
 * ===================================================================== */
(function () {
    "use strict";

    const DATA = window.ETIQUETAS_DATA;               // productos + variantes (del servidor)
    const STORE = window.ETIQUETAS_STORE || "";
    const STORAGE_KEY = "stockflow_etiquetas_v1";

    const DEFAULTS = {
        width: 50, height: 30, dpi: 203,
        showStore: true, showName: true, showVariant: true, showPrice: true, showCode: true,
        border: false,
    };

    const $ = (id) => document.getElementById(id);
    let settings = loadSettings();
    let items = [];          // [{product, variant|null}]

    // ------------------------------------------------------------------ utils
    function loadSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
            return Object.assign({}, DEFAULTS, saved);
        } catch (e) { return Object.assign({}, DEFAULTS); }
    }
    function saveSettings() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (e) { /* sin almacenamiento */ }
    }
    function money(v) {
        return "$\u00a0" + Math.round(Number(v || 0)).toLocaleString("es-CO");   // espacio no separable: el precio nunca se parte
    }
    function esc(s) {
        return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }
    function slug(s) {
        return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "etiqueta";
    }
    function qrAvailable() { return typeof window.qrcode === "function"; }
    function codeOf(item) { return item.variant ? item.variant.code : item.product.code; }
    function variantText(item) {
        if (item.variant) {
            return [item.variant.size ? "Talla " + item.variant.size : "", item.variant.color].filter(Boolean).join(" · ");
        }
        return "";
    }
    function fileName(item) {
        const v = item.variant ? "_" + slug((item.variant.size || "") + "-" + (item.variant.color || "")) : "";
        return "etiqueta_" + slug(item.product.barcode || item.product.name) + v + ".png";
    }

    // ------------------------------------------------------------- drawing
    function setFont(ctx, px, bold) {
        ctx.font = (bold ? "bold " : "") + Math.max(6, Math.round(px)) + "px Arial, Helvetica, sans-serif";
    }

    /** Ajusta el tamaño de letra para que el texto quepa en maxWidth (y en maxLines líneas). */
    function fitLines(ctx, text, maxWidth, startPx, minPx, bold, maxLines) {
        let px = startPx;
        while (px >= minPx) {
            setFont(ctx, px, bold);
            const lines = wrap(ctx, text, maxWidth);
            if (lines.length <= maxLines && lines.every(l => ctx.measureText(l).width <= maxWidth)) {
                return { px, lines };
            }
            px -= 1;
        }
        setFont(ctx, minPx, bold);
        let lines = wrap(ctx, text, maxWidth).slice(0, maxLines);
        // recorta con "…" la última línea si aún no cabe
        lines = lines.map(l => {
            let t = l;
            while (t.length > 1 && ctx.measureText(t).width > maxWidth) t = t.slice(0, -2) + "…";
            return t;
        });
        return { px: minPx, lines };
    }

    function wrap(ctx, text, maxWidth) {
        const words = String(text || "").split(/[ \t\n]+/).filter(Boolean);   // no corta en espacios no separables
        const lines = [];
        let line = "";
        for (const w of words) {
            const test = line ? line + " " + w : w;
            if (ctx.measureText(test).width <= maxWidth || !line) line = test;
            else { lines.push(line); line = w; }
        }
        if (line) lines.push(line);
        return lines.length ? lines : [""];
    }

    function drawQR(ctx, text, x, y, maxSide) {
        const qr = window.qrcode(0, "M");
        qr.addData(text);
        qr.make();
        const n = qr.getModuleCount();
        const module = Math.max(1, Math.floor(maxSide / n));   // módulos enteros = QR nítido
        const size = module * n;
        const ox = x + Math.floor((maxSide - size) / 2);
        const oy = y + Math.floor((maxSide - size) / 2);
        ctx.fillStyle = "#000";
        for (let r = 0; r < n; r++) {
            for (let c = 0; c < n; c++) {
                if (qr.isDark(r, c)) ctx.fillRect(ox + c * module, oy + r * module, module, module);
            }
        }
        return size;
    }

    /** Textos de la etiqueta: grupo superior (identidad) e inferior (precio y código). */
    function textGroups(item) {
        const p = item.product;
        const top = [], bottom = [];
        if (settings.showStore && STORE) top.push({ text: STORE.toUpperCase(), rel: 0.082, bold: false, lines: 1 });
        if (settings.showName) top.push({ text: p.name + (p.model ? " " + p.model : ""), rel: 0.15, bold: true, lines: 2 });
        const vt = variantText(item);
        if (settings.showVariant && vt) top.push({ text: vt.replace(/ /g, "\u00a0"), rel: 0.115, bold: false, lines: 1 });
        if (settings.showPrice) bottom.push({ text: money(p.price), rel: 0.2, bold: true, lines: 1, minF: 0.38 });
        if (settings.showCode) bottom.push({ text: codeOf(item).replace("#V", "\u00a0·\u00a0V"), rel: 0.075, bold: false, lines: 1 });
        return { top, bottom };
    }

    function layout(ctx, blocks, w, H, scale) {
        return blocks.map(b => {
            const start = b.rel * H * scale;
            const fit = fitLines(ctx, b.text, w, start, Math.max(9, start * (b.minF || 0.5)), b.bold, b.lines);
            return Object.assign({}, b, fit, { lh: Math.round(fit.px * 1.14) });
        });
    }
    const heightOf = (laid) => laid.reduce((s, b) => s + b.lh * b.lines.length, 0);

    function drawLaid(ctx, laid, x, y, w, align) {
        ctx.fillStyle = "#000";
        ctx.textBaseline = "top";
        ctx.textAlign = align;
        const tx = align === "center" ? x + w / 2 : x;
        let cy = y;
        for (const b of laid) {
            setFont(ctx, b.px, b.bold);
            for (const line of b.lines) { ctx.fillText(line, tx, cy); cy += b.lh; }
        }
        return cy;
    }

    /** Columna de texto: grupo superior arriba, precio/código abajo y una línea fina entre ambos. */
    function drawColumn(ctx, groups, x, y, w, h, H, align) {
        const minGap = Math.round(H * 0.05);
        let scale = 1, top, bottom;
        for (let i = 0; i < 14; i++) {
            top = layout(ctx, groups.top, w, H, scale);
            bottom = layout(ctx, groups.bottom, w, H, scale);
            if (heightOf(top) + heightOf(bottom) + (top.length && bottom.length ? minGap : 0) <= h) break;
            scale *= 0.92;
        }
        const th = heightOf(top), bh = heightOf(bottom);
        if (!top.length || !bottom.length) {           // un solo grupo: centrado vertical
            drawLaid(ctx, top.length ? top : bottom, x, y + Math.max(0, Math.floor((h - th - bh) / 2)), w, align);
            return;
        }
        drawLaid(ctx, top, x, y, w, align);
        const last = bottom[bottom.length - 1];
        const by = y + h - bh + (last.lh - Math.round(last.px * 0.95));   // pegado al borde inferior
        drawLaid(ctx, bottom, x, by, w, align);
        const gap = by - (y + th);
        if (gap >= minGap) {                             // separador fino
            const lw = Math.max(2, Math.round(H * 0.008));
            const ly = Math.round(y + th + gap / 2 - lw / 2);
            const len = align === "center" ? Math.round(w * 0.5) : Math.round(w * 0.35);
            const lx = align === "center" ? Math.round(x + (w - len) / 2) : x;
            ctx.fillStyle = "#000";
            ctx.fillRect(lx, ly, len, lw);
        }
    }

    /** Convierte a blanco y negro puro (la impresora térmica no imprime grises). */
    function toMonochrome(ctx, W, H) {
        const img = ctx.getImageData(0, 0, W, H);
        const d = img.data;
        for (let i = 0; i < d.length; i += 4) {
            const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
            const v = lum < 150 ? 0 : 255;
            d[i] = d[i + 1] = d[i + 2] = v;
            d[i + 3] = 255;
        }
        ctx.putImageData(img, 0, 0);
    }

    async function renderLabel(item) {
        const dpmm = settings.dpi / 25.4;
        const W = Math.round(settings.width * dpmm);
        const H = Math.round(settings.height * dpmm);
        const m = Math.round(1.5 * dpmm);              // margen de seguridad 1,5 mm
        const canvas = document.createElement("canvas");
        canvas.width = W;
        canvas.height = H;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, W, H);
        ctx.imageSmoothingEnabled = false;
        if (!qrAvailable()) {
            setFont(ctx, H * 0.1, true);
            ctx.fillStyle = "#000"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText("Sin generador de QR", W / 2, H / 2);
            return canvas;
        }

        const code = codeOf(item);
        const groups = textGroups(item);
        const landscape = W / H >= 1.25;
        if (landscape) {
            // QR a la izquierda, del alto completo de la etiqueta
            const side = Math.min(H - 2 * m, Math.floor(W * 0.46));
            const qrSize = drawQR(ctx, code, m, Math.floor((H - side) / 2), side);
            const qrTop = Math.floor((H - qrSize) / 2);
            const x = m + Math.floor((side + qrSize) / 2) + Math.round(2 * dpmm);
            drawColumn(ctx, groups, x, Math.max(m, qrTop), W - x - m, Math.min(H - 2 * m, qrSize), H, "left");
        } else {
            // etiqueta vertical/cuadrada: QR arriba y los datos centrados debajo
            const side = Math.min(W - 2 * m, Math.floor(H * 0.52));
            const qrSize = drawQR(ctx, code, Math.floor((W - side) / 2), m, side);
            const ty = m + Math.floor((side + qrSize) / 2) + Math.round(1.5 * dpmm);
            drawColumn(ctx, groups, m, ty, W - 2 * m, H - ty - m, H, "center");
        }

        if (settings.border) {
            ctx.strokeStyle = "#000";
            ctx.lineWidth = Math.max(2, Math.round(0.3 * dpmm));
            ctx.strokeRect(ctx.lineWidth / 2, ctx.lineWidth / 2, W - ctx.lineWidth, H - ctx.lineWidth);
        }
        toMonochrome(ctx, W, H);
        return canvas;
    }

    // ------------------------------------------------------------------ UI
    function productById(id) { return DATA.find(p => p.id === id); }

    function renderProductList() {
        const q = $("productSearch").value.trim().toLowerCase();
        const list = $("productList");
        const rows = DATA.filter(p => !q || [p.name, p.model, p.barcode, p.category].join(" ").toLowerCase().includes(q)).slice(0, 80);
        if (!rows.length) {
            list.innerHTML = '<p class="muted">No hay productos que coincidan.</p>';
            return;
        }
        list.innerHTML = rows.map(p => `
            <div class="prod">
                <div class="prod-head">
                    <div><strong>${esc(p.name)}</strong> <span class="muted">${esc(p.model)}</span><br>
                        <small class="muted">${esc(p.barcode)} · ${money(p.price)} · stock ${p.stock}</small></div>
                    <button class="btn btn-sm" type="button" data-add="${p.id}">+ Producto</button>
                </div>
                ${p.variants.length ? `<div class="chips">${p.variants.map(v => `
                    <button type="button" class="chip" data-add="${p.id}" data-variant="${v.id}" title="Etiqueta de esta talla/color">
                        ${esc(v.size || "—")} · ${esc(v.color || "")} <small>(${v.stock})</small></button>`).join("")}
                    <button type="button" class="chip chip-all" data-add-all="${p.id}">+ Todas las tallas</button></div>` : ""}
            </div>`).join("");
    }

    function addItem(productId, variantId) {
        const p = productById(productId);
        if (!p) return;
        const v = variantId ? p.variants.find(x => x.id === variantId) : null;
        items.push({ product: p, variant: v || null });
        refresh();
    }

    async function refresh() {
        $("count").textContent = items.length;
        const grid = $("labels");
        if (!items.length) {
            grid.innerHTML = '<p class="muted">Agrega productos o tallas de la lista para ver sus etiquetas aquí.</p>';
            return;
        }
        grid.innerHTML = "";
        const dpmm = settings.dpi / 25.4;
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            const canvas = await renderLabel(item);
            const card = document.createElement("div");
            card.className = "label-card";
            const shown = Math.min(300, Math.round(settings.width * 6));
            canvas.style.width = shown + "px";
            canvas.style.height = Math.round(shown * settings.height / settings.width) + "px";
            canvas.className = "label-canvas";
            card.appendChild(canvas);
            const info = document.createElement("div");
            info.className = "label-info";
            info.innerHTML = `<span>${esc(item.product.name)}${variantText(item) ? " · " + esc(variantText(item)) : ""}</span>
                <small class="muted">${canvas.width}×${canvas.height} px (${settings.width}×${settings.height} mm)</small>`;
            card.appendChild(info);
            const actions = document.createElement("div");
            actions.className = "label-actions";
            actions.innerHTML = `<button class="btn btn-sm" type="button" data-dl="${i}">⬇ PNG</button>
                <button class="btn btn-secondary btn-sm" type="button" data-rm="${i}">Quitar</button>`;
            card.appendChild(actions);
            grid.appendChild(card);
            void dpmm;
        }
        $("shareBtn").style.display = canShareFiles() ? "" : "none";
    }

    function canvasBlob(canvas) {
        return new Promise(resolve => canvas.toBlob(resolve, "image/png"));
    }

    function downloadBlob(blob, name) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
    }

    async function downloadOne(i) {
        const canvas = await renderLabel(items[i]);
        downloadBlob(await canvasBlob(canvas), fileName(items[i]));
    }

    async function downloadAll() {
        if (!items.length) return alert("Agrega al menos una etiqueta.");
        for (let i = 0; i < items.length; i++) {
            await downloadOne(i);
            await new Promise(r => setTimeout(r, 400));   // el navegador puede pedir permitir descargas múltiples
        }
    }

    function canShareFiles() {
        try {
            return !!(navigator.canShare && navigator.canShare({ files: [new File([""], "a.png", { type: "image/png" })] }));
        } catch (e) { return false; }
    }

    async function shareAll() {
        if (!items.length) return alert("Agrega al menos una etiqueta.");
        const files = [];
        for (const item of items) {
            files.push(new File([await canvasBlob(await renderLabel(item))], fileName(item), { type: "image/png" }));
        }
        try {
            await navigator.share({ files, title: "Etiquetas StockFlow" });
        } catch (e) {
            if (e.name !== "AbortError") alert("No se pudo compartir: " + e.message);
        }
    }

    async function printFromPc() {
        if (!items.length) return alert("Agrega al menos una etiqueta.");
        const urls = [];
        for (const item of items) urls.push((await renderLabel(item)).toDataURL("image/png"));
        const w = window.open("", "_blank");
        if (!w) return alert("Permite las ventanas emergentes para imprimir.");
        w.document.write(`<!DOCTYPE html><html><head><title>Etiquetas</title><style>
            @page { size: ${settings.width}mm ${settings.height}mm; margin: 0; }
            html, body { margin: 0; padding: 0; }
            img { display: block; width: ${settings.width}mm; height: ${settings.height}mm; page-break-after: always;
                  image-rendering: pixelated; }
            </style></head><body>${urls.map(u => `<img src="${u}">`).join("")}</body></html>`);
        w.document.close();
        w.onload = () => { w.focus(); w.print(); };
    }

    // --------------------------------------------------------- settings UI
    function bindSettings() {
        const sizeSel = $("labelSize");
        const sizeKey = settings.width + "x" + settings.height;
        sizeSel.value = [...sizeSel.options].some(o => o.value === sizeKey) ? sizeKey : "custom";
        $("customW").value = settings.width;
        $("customH").value = settings.height;
        $("customSize").style.display = sizeSel.value === "custom" ? "" : "none";
        $("dpi").value = String(settings.dpi);
        for (const k of ["showStore", "showName", "showVariant", "showPrice", "showCode", "border"]) {
            $(k).checked = !!settings[k];
        }

        sizeSel.addEventListener("change", () => {
            if (sizeSel.value === "custom") {
                $("customSize").style.display = "";
            } else {
                $("customSize").style.display = "none";
                const [w, h] = sizeSel.value.split("x").map(Number);
                settings.width = w; settings.height = h;
                $("customW").value = w; $("customH").value = h;
                changed();
            }
        });
        for (const id of ["customW", "customH"]) {
            $(id).addEventListener("change", () => {
                const w = Number($("customW").value), h = Number($("customH").value);
                if (w >= 10 && w <= 120 && h >= 10 && h <= 200) {
                    settings.width = w; settings.height = h; changed();
                }
            });
        }
        $("dpi").addEventListener("change", () => { settings.dpi = Number($("dpi").value); changed(); });
        for (const k of ["showStore", "showName", "showVariant", "showPrice", "showCode", "border"]) {
            $(k).addEventListener("change", () => { settings[k] = $(k).checked; changed(); });
        }
    }

    function changed() { saveSettings(); refresh(); }

    // ---------------------------------------------------------------- init
    document.addEventListener("DOMContentLoaded", () => {
        if (!qrAvailable()) {
            $("qrWarning").style.display = "";
        }
        bindSettings();
        renderProductList();
        $("productSearch").addEventListener("input", renderProductList);

        $("productList").addEventListener("click", (e) => {
            const all = e.target.closest("[data-add-all]");
            if (all) {
                const p = productById(Number(all.dataset.addAll));
                p.variants.forEach(v => items.push({ product: p, variant: v }));
                return refresh();
            }
            const b = e.target.closest("[data-add]");
            if (b) addItem(Number(b.dataset.add), b.dataset.variant ? Number(b.dataset.variant) : null);
        });
        $("labels").addEventListener("click", (e) => {
            const dl = e.target.closest("[data-dl]");
            if (dl) return downloadOne(Number(dl.dataset.dl));
            const rm = e.target.closest("[data-rm]");
            if (rm) { items.splice(Number(rm.dataset.rm), 1); refresh(); }
        });
        $("downloadAllBtn").addEventListener("click", downloadAll);
        $("shareBtn").addEventListener("click", shareAll);
        $("printBtn").addEventListener("click", printFromPc);
        $("clearBtn").addEventListener("click", () => { items = []; refresh(); });

        const pre = window.ETIQUETAS_PRESELECT || {};
        if (pre.product_id) addItem(pre.product_id, pre.variant_id || null);
        else refresh();
    });

    // expuesto para pruebas
    window.StockFlowEtiquetas = { renderLabel, settings: () => settings, items: () => items };
})();
