/* ClipTool editor workspace.
 *
 * Full-screen editor for one clip: layout (ratio, templates, facecam split), trim + speed, text overlays,
 * captions, filters, audio and extras, with a live canvas preview, a timeline and undo/redo.
 * Every setting maps 1:1 onto the clip fields that cliptool/render.py renders.
 *
 * Uses globals from index.html: $, esc, fmt, toast, currentRun, releaseVideos, setBusy, showJob, poll, openRun.
 */
(() => {
  let C = null;                 // option lists from /api/presets
  let S = null;                 // the clip's editable state (undoable)
  let X = {};                   // context: folder, rank, words, bounds, video id, ranges, UI state
  let hist = [], fut = [], lastKey = "", lastT = 0, dirty = false;
  const NEUTRAL = { brightness: 0, contrast: 1, saturation: 1, warmth: 0, sharpen: 0, vignette: false };
  const TOOLS = [
    ["layout", "Layout", '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 10h18M10 10v11"/>'],
    ["trim", "Trim", '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>'],
    ["text", "Text", '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>'],
    ["captions", "Captions", '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M7 15h4M13 15h4M7 11h10"/>'],
    ["logo", "Logo", '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>'],
    ["filters", "Filters", '<circle cx="9" cy="9" r="6"/><circle cx="15" cy="15" r="6"/>'],
    ["audio", "Audio", '<path d="M11 5L6 9H2v6h4l5 4V5zM15.5 8.5a5 5 0 010 7M19 5a10 10 0 010 14"/>'],
    ["extras", "Extras", '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>'],
  ];
  const TEMPLATES = [
    ["auto", "Auto (follow action)", '<rect x="1" y="1" width="32" height="58" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10 34l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17" cy="20" r="4" fill="currentColor"/>'],
    ["crop", "Crop to fill", '<rect x="1" y="1" width="32" height="58" rx="3" fill="currentColor" opacity=".35"/><rect x="1" y="1" width="32" height="58" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>'],
    ["blur", "Full + blur", '<rect x="1" y="1" width="32" height="58" rx="3" fill="currentColor" opacity=".15"/><rect x="1" y="21" width="32" height="18" fill="currentColor" opacity=".6"/><rect x="1" y="1" width="32" height="58" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>'],
    ["black", "Full + black", '<rect x="1" y="1" width="32" height="58" rx="3" fill="#000"/><rect x="1" y="21" width="32" height="18" fill="currentColor" opacity=".6"/><rect x="1" y="1" width="32" height="58" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>'],
    ["stack", "Facecam + game", '<rect x="1" y="1" width="32" height="25" rx="3" fill="#f472b6" opacity=".55"/><circle cx="17" cy="12" r="5" fill="currentColor"/><rect x="1" y="27" width="32" height="32" fill="#22d3ee" opacity=".45"/><rect x="1" y="1" width="32" height="58" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>'],
  ];
  const SPLITS = [[0.25, "Cam 1/4"], [0.33, "Cam 1/3"], [0.4375, "Cam 44%"], [0.5, "Half"], [0.6, "Cam 60%"]];
  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
  const TEXT_PRESETS = {
    title: { label: "Title", text: "WAIT FOR IT...", size: 96, y: 0.1, color: "#FFFFFF", box: true, box_color: "#000000", anim: "pop" },
    label: { label: "Label", text: "Part 1", size: 60, y: 0.22, color: "#000000", box: true, box_color: "#FFD400", anim: "fade" },
    cta:   { label: "Call to action", text: "Follow for more", size: 64, y: 0.86, color: "#FFFFFF", outline: 5, anim: "pop", fromEnd: 3 },
    mark:  { label: "Watermark", text: "@yourname", size: 40, x: 0.84, y: 0.95, color: "#FFFFFF", bold: false, font: "Segoe UI", outline: 2, anim: "none", whole: true },
  };
  const el = id => document.getElementById(id);

  // ------------------------------------------------------------------ DOM
  function buildDom(){
    if (el("ws")) return;
    const ws = document.createElement("div");
    ws.id = "ws"; ws.className = "ws"; ws.hidden = true;
    ws.setAttribute("role", "dialog"); ws.setAttribute("aria-modal", "true"); ws.setAttribute("aria-label", "Clip editor");
    ws.innerHTML = `
      <header class="ws-top">
        <button type="button" class="back" data-act="close" title="Back to clips (Esc)">← Clips</button>
        <input class="ttl" id="ws-title" aria-label="Clip title" maxlength="100">
        <span class="dirty" id="ws-dirty"></span>
        <button type="button" class="icon" data-act="undo" title="Undo (Ctrl+Z)" aria-label="Undo">↶</button>
        <button type="button" class="icon" data-act="redo" title="Redo (Ctrl+Shift+Z)" aria-label="Redo">↷</button>
        <button type="button" class="primary" data-act="save">Save &amp; render</button>
      </header>
      <div class="ws-main">
        <nav class="ws-rail" role="tablist" aria-label="Editing tools">
          ${TOOLS.map(([k, label, svg]) => `<button type="button" role="tab" data-tool="${k}" aria-selected="false">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${svg}</svg>${label}</button>`).join("")}
        </nav>
        <section class="ws-stage" id="ws-stage">
          <div class="ws-frame" id="ws-frame">
            <canvas id="ws-canvas"></canvas>
            <div class="vig" id="ws-vig"></div>
            <div class="fade" id="ws-fade"></div>
            <div class="seam" id="ws-seam" hidden></div>
            <div class="ilayer" id="ws-ilayer"></div>
            <div class="cap" id="ws-cap"></div>
            <div class="tlayer" id="ws-tlayer"></div>
            <div class="pbar" id="ws-pbar"></div>
          </div>
          <div class="ws-transport">
            <button type="button" data-act="back5" title="Back 2 s (Shift+←)">−2s</button>
            <button type="button" class="play primary" data-act="play" title="Play / pause (Space)">▶ Play</button>
            <button type="button" data-act="fwd5" title="Forward 2 s (Shift+→)">+2s</button>
            <div class="spd" role="group" aria-label="Clip speed">
              <button type="button" data-act="spdstep" data-v="-1" title="Slower" aria-label="Slower">−</button>
              <select id="ws-speed" aria-label="Clip speed" title="Speed of the final clip (captions stay in sync)">
                ${[0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2].map(v => `<option value="${v}">${v}×</option>`).join("")}
              </select>
              <button type="button" data-act="spdstep" data-v="1" title="Faster" aria-label="Faster">+</button>
            </div>
            <span class="tc" id="ws-tc">0.0 / 0.0 s</span>
          </div>
          <p class="ws-hint" id="ws-hint">Drag text on the preview to move it. Space plays, Ctrl+Z undoes.</p>
        </section>
        <aside class="ws-panel" id="ws-panel" aria-live="polite"></aside>
      </div>
      <footer class="ws-tl">
        <div class="tl-head"><span id="ws-tl-l"></span><span id="ws-tl-r"></span></div>
        <div class="track" id="ws-track" title="Click to jump; drag the orange handles to trim">
          <div class="ticks" id="ws-ticks"></div>
          <div class="sel" id="ws-sel"><div class="h l" data-h="start"></div><div class="h r" data-h="end"></div></div>
          <div class="hk" id="ws-hk" hidden title="Hook intro - plays first"></div>
          <div class="ph" id="ws-ph"></div>
        </div>
        <div class="ttrack" id="ws-ttrack"></div>
      </footer>
      <video id="ws-video" playsinline preload="auto" hidden></video>`;
    document.body.appendChild(ws);
    wire();
  }

  // ------------------------------------------------------------------ helpers
  const outSize = () => C.aspects[S.aspect].size;
  const outDur = () => (S.end - S.start) / S.speed;
  const video = () => el("ws-video");
  const tOut = () => X.phase === "hook" ? -1 : (video().currentTime - S.start) / S.speed;
  const hookOn = () => S.hook && S.hook.on && S.hook.start != null && S.hook.end != null;
  const hookLen = () => hookOn() ? S.hook.end - S.hook.start : 0;
  const sel = () => S.texts.find(t => t.id === X.selText);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const hexA = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

  function win(vw, vh, aspect, zoom, cx, cy){
    let w, h;
    if (aspect <= vw / vh) { h = vh / Math.max(zoom, 1); w = h * aspect; } else { w = vw / Math.max(zoom, 1); h = w / aspect; }
    return { w, h, x: clamp(cx * vw - w / 2, 0, vw - w), y: clamp(cy * vh - h / 2, 0, vh - h) };
  }
  const bandAspect = () => { const [W, H] = outSize(); return W / Math.max(2, H * S.cam_share); };
  function shapeFacecam(fc){
    // Keep the facecam box the same shape as the band it fills.
    const v = video(), ar = (v.videoWidth || 16) / (v.videoHeight || 9), bA = bandAspect();
    let w = fc.w, h = w * ar / bA;
    if (h > 1) { h = 1; w = h * bA / ar; }
    const cx = fc.x + fc.w / 2, cy = fc.y + fc.h / 2;
    return { x: clamp(cx - w / 2, 0, 1 - w), y: clamp(cy - h / 2, 0, 1 - h), w: +w.toFixed(4), h: +h.toFixed(4) };
  }

  // ------------------------------------------------------------------ state & undo
  function mutate(key, fn, opts = {}){
    const now = Date.now();
    if (key !== lastKey || now - lastT > 700) { hist.push(JSON.stringify(S)); if (hist.length > 150) hist.shift(); fut = []; }
    lastKey = key; lastT = now;
    fn();
    dirty = true; el("ws-dirty").textContent = "Unsaved changes";
    if (opts.panel !== false) renderPanel();
    refresh();
  }
  function undo(){ if (!hist.length) return; fut.push(JSON.stringify(S)); S = JSON.parse(hist.pop()); lastKey = ""; afterHistory(); }
  function redo(){ if (!fut.length) return; hist.push(JSON.stringify(S)); S = JSON.parse(fut.pop()); lastKey = ""; afterHistory(); }
  function afterHistory(){
    dirty = true; el("ws-dirty").textContent = "Unsaved changes";
    el("ws-title").value = S.title; renderPanel(); refresh();
  }

  function getPath(path){
    const parts = path.split("."), head = { sel: sel, selimg: selImg }[parts[0]];
    let o = head ? head() : S;
    for (const p of parts.slice(head ? 1 : 0, -1)) o = o?.[p];
    return [o, parts[parts.length - 1]];
  }

  // ------------------------------------------------------------------ layout of the stage
  function sizeFrame(){
    const stage = el("ws-stage"), fr = el("ws-frame"), [W, H] = outSize();
    const availW = stage.clientWidth - 28, availH = stage.clientHeight - 110;
    const s = Math.max(0.05, Math.min(availW / W, availH / H));
    fr.style.width = Math.round(W * s) + "px"; fr.style.height = Math.round(H * s) + "px";
    const a = C.aspects[S.aspect];
    fr.style.setProperty("--cap-bottom", (a.bottom / H * 100) + "%");
    fr.style.setProperty("--cap-top", (a.top / H * 100) + "%");
  }

  // ------------------------------------------------------------------ canvas preview
  function cssFilter(){
    const f = S.filters, p = [];
    if (Math.abs(f.brightness) > 1e-3) p.push(`brightness(${1 + f.brightness * 1.6})`);
    if (Math.abs(f.contrast - 1) > 1e-3) p.push(`contrast(${f.contrast})`);
    if (Math.abs(f.saturation - 1) > 1e-3) p.push(`saturate(${f.saturation})`);
    if (f.warmth > 0.01) p.push(`sepia(${f.warmth * 0.35})`);
    if (f.warmth < -0.01) p.push(`hue-rotate(${f.warmth * 18}deg)`);
    return p.join(" ") || "none";
  }
  function cover(ctx, v, sx, sy, sw, sh, dx, dy, dw, dh){
    const sa = sw / sh, da = dw / dh;
    if (sa > da) { const nw = sh * da; sx += (sw - nw) / 2; sw = nw; } else { const nh = sw / da; sy += (sh - nh) / 2; sh = nh; }
    ctx.drawImage(v, sx, sy, sw, sh, dx, dy, dw, dh);
  }
  function effectiveFit(){
    if (S.aspect === "original" && (S.fit === "auto" || S.fit === "crop")) return "black";
    if (S.fit === "auto" && S.aspect !== "vertical") return "blur";
    return S.fit;
  }
  function drawFrame(){
    const v = video(), c = el("ws-canvas"), fr = el("ws-frame");
    if (!X.open || v.readyState < 2) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(fr.clientWidth * dpr), H = Math.round(fr.clientHeight * dpr);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const ctx = c.getContext("2d"), vw = v.videoWidth, vh = v.videoHeight, flt = cssFilter(), fit = effectiveFit();
    ctx.filter = "none"; ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    ctx.filter = flt;
    el("ws-seam").hidden = fit !== "stack";
    if (fit === "stack") {
      const camH = H * S.cam_share, gameH = H - camH, fc = S.facecam || C.default_facecam;
      const camY = S.cam_pos === "top" ? 0 : gameH, gameY = S.cam_pos === "top" ? camH : 0;
      cover(ctx, v, fc.x * vw, fc.y * vh, fc.w * vw, fc.h * vh, 0, camY, W, camH);
      const g = win(vw, vh, W / gameH, S.game_zoom, S.game_x, S.game_y);
      ctx.drawImage(v, g.x, g.y, g.w, g.h, 0, gameY, W, gameH);
      el("ws-seam").style.top = (S.cam_pos === "top" ? camH : gameH) / H * 100 + "%";
    } else if (fit === "blur" || fit === "black") {
      if (fit === "blur") { ctx.filter = flt + ` blur(${W / 40}px) brightness(.92)`; cover(ctx, v, 0, 0, vw, vh, 0, 0, W, H); ctx.filter = flt; }
      const s = Math.min(W / vw, H / vh) * S.zoom, dw = vw * s, dh = vh * s;
      ctx.drawImage(v, 0, 0, vw, vh, (W - dw) / 2, (H - dh) / 2, dw, dh);
    } else {
      const auto = fit === "auto";
      const g = win(vw, vh, W / H, auto ? 1 : S.zoom, auto ? 0.5 : S.crop_x, auto ? 0.5 : S.crop_y);
      ctx.drawImage(v, g.x, g.y, g.w, g.h, 0, 0, W, H);
    }
    ctx.filter = "none";
    el("ws-vig").classList.toggle("on", !!S.filters.vignette);
  }

  // ------------------------------------------------------------------ captions / text / extras overlays
  function capWords(){
    const ws = X.words.filter(w => w.s >= S.start - 0.05 && w.s < S.end);
    const txt = (S.text || "").trim();
    if (!S.textEdited || !txt || !ws.length) return ws;
    const toks = txt.split(/\s+/);
    if (toks.length === ws.length) return ws.map((w, i) => ({ ...w, w: toks[i] }));
    const t0 = ws[0].s, t1 = ws[ws.length - 1].e, wts = toks.map(t => t.length + 2), tot = wts.reduce((a, b) => a + b, 0);
    let acc = 0;
    return toks.map((t, i) => { const s = t0 + (t1 - t0) * acc / tot; acc += wts[i]; return { w: t, s, e: t0 + (t1 - t0) * acc / tot }; });
  }
  // Caption fields the editor can change; presets supply the defaults (see _style_defaults in render.py).
  const STYLE_KEYS = ["font", "bold", "italic", "mode", "outline", "outline_color", "shadow", "shadow_color", "shadow_opacity",
                      "spacing", "background", "band_color", "band_opacity", "color", "highlight", "uppercase", "words_per_line"];
  const presetStyle = k => { const p = C.presets[k]; return Object.fromEntries(STYLE_KEYS.map(f => [f, p[f]])); };

  /* Draw one caption line into `node` (mirrors _caption_style_line / caption_events in render.py). */
  function drawCap(node, toks, active, st, fontPx){
    const p = C.presets[st.preset] || {}, mode = st.mode || "color";
    const unit = fontPx / (p.size || 78);                // 1 ASS pixel at this preview size
    const ol = (st.outline || 0) * unit, sh = (st.shadow || 0) * unit;
    node.style.cssText = `font-family:"${st.font}","Arial Black",Impact,sans-serif;font-size:${fontPx}px;` +
      `font-weight:${st.bold === false ? 400 : 800};color:${st.color};font-style:${st.italic ? "italic" : "normal"};` +
      (st.spacing ? `letter-spacing:${st.spacing * unit}px;` : "");
    const shadowCss = !st.background && sh > 0 ? `${sh}px ${sh}px 0 ${hexA(st.shadow_color || "#000000", st.shadow_opacity ?? 0.5)}` : "";
    const word = (t, on) => {
      let css = "";
      if (!st.background && ol > 0) css += `-webkit-text-stroke:${ol * 2}px ${mode === "glow" ? st.highlight : st.outline_color};paint-order:stroke fill;`;
      const shadows = [];
      if (mode === "glow") shadows.push(`0 0 ${fontPx * .18}px ${st.highlight}`, `0 0 ${fontPx * .4}px ${st.highlight}`);
      if (shadowCss) shadows.push(shadowCss);
      if (shadows.length) css += `text-shadow:${shadows.join(",")};`;
      if (on && mode === "box") css += `background:${st.highlight};border-radius:.18em;padding:0 .14em;-webkit-text-stroke:0;text-shadow:none;`;
      else if (on && ["color", "glow", "pop"].includes(mode)) css += `color:${st.highlight};`;
      return `<span class="w" style="${css}">${esc(st.uppercase ? t.toUpperCase() : t)}</span>`;
    };
    let html = mode === "pop" ? (active >= 0 ? word(toks[active], true) : "")
             : toks.map((t, i) => word(t, mode !== "plain" && i === active)).join(" ");
    if (st.background && html)
      html = `<span style="background:${hexA(st.band_color || "#000000", st.band_opacity ?? 0.62)};padding:.08em .35em;border-radius:.12em;` +
             `box-decoration-break:clone;-webkit-box-decoration-break:clone;line-height:1.5">${html}</span>`;
    node.innerHTML = html;
  }
  function drawCaptions(){
    const cap = el("ws-cap"), v = video(), st = S.style;
    cap.className = "cap " + st.position;
    if (!S.captions) { cap.innerHTML = ""; return; }
    const cw = capWords(), t = v.currentTime, n = Math.max(1, st.words_per_line);
    let k = -1;
    for (let i = 0; i < cw.length; i++) { if (cw[i].s <= t) k = i; else break; }
    if (k < 0 || t > S.end) { cap.innerHTML = ""; return; }
    const g = Math.floor(k / n), group = cw.slice(g * n, g * n + n), next = cw[g * n + n];
    if (t > (next ? next.s : Math.min(group[group.length - 1].e + 0.4, S.end))) { cap.innerHTML = ""; return; }
    const p = C.presets[st.preset], [, H] = outSize();
    drawCap(cap, group.map(w => w.w), k - g * n, st, p.size * C.aspects[S.aspect].cap_scale * st.scale / H * el("ws-frame").clientHeight);
  }
  function textCss(t, fh){
    const [, H] = outSize(), px = t.size / H * fh, stroke = (t.outline || 0) / H * fh * 1.6;
    let css = `font-family:"${t.font}",sans-serif;font-size:${px}px;color:${t.color};font-weight:${t.bold ? 800 : 400};font-style:${t.italic ? "italic" : "normal"};` +
              `left:${t.x * 100}%;top:${t.y * 100}%;`;
    if (t.box) css += `background:${hexA(t.box_color, t.box_opacity)};padding:.12em .3em;`;
    else css += `-webkit-text-stroke:${stroke}px ${t.outline_color};paint-order:stroke fill;` + (t.shadow ? `text-shadow:0 ${px * .04}px ${px * .06}px rgba(0,0,0,.6);` : "");
    return css;
  }
  function drawTexts(){
    const layer = el("ws-tlayer"), fh = el("ws-frame").clientHeight, t = tOut();
    const seen = new Set();
    for (const tx of S.texts) {
      seen.add(tx.id);
      let n = layer.querySelector(`[data-id="${tx.id}"]`);
      if (!n) { n = document.createElement("div"); n.className = "tx"; n.dataset.id = tx.id; layer.appendChild(n); }
      n.style.cssText = textCss(tx, fh);
      if (n.textContent !== tx.text) n.textContent = tx.text;
      const on = t >= tx.start && t <= tx.end;
      n.classList.toggle("sel", tx.id === X.selText);
      n.classList.toggle("off", !on && tx.id === X.selText);
      n.classList.toggle("hide", !on && tx.id !== X.selText);
    }
    layer.querySelectorAll(".tx").forEach(n => { if (!seen.has(n.dataset.id)) n.remove(); });
  }
  const selImg = () => S.images.find(i => i.id === X.selImg);
  function drawImages(){
    const layer = el("ws-ilayer"), fw = el("ws-frame").clientWidth, t = tOut(), D = outDur();
    const seen = new Set();
    for (const im of S.images) {
      seen.add(im.id);
      let n = layer.querySelector(`[data-id="${im.id}"]`);
      if (!n) { n = document.createElement("img"); n.className = "im"; n.dataset.id = im.id; n.alt = "Watermark"; n.draggable = false; layer.appendChild(n); }
      const src = "/wm/" + encodeURIComponent(im.file);
      if (n.getAttribute("src") !== src) n.src = src;
      n.style.cssText = `left:${im.x * 100}%;top:${im.y * 100}%;width:${im.size * fw}px;opacity:${im.opacity};`;
      const on = t >= (im.start || 0) && t <= (im.end == null ? D : im.end);
      n.classList.toggle("sel", im.id === X.selImg);
      n.classList.toggle("off", !on && im.id === X.selImg);
      n.classList.toggle("hide", !on && im.id !== X.selImg);
    }
    layer.querySelectorAll(".im").forEach(n => { if (!seen.has(n.dataset.id)) n.remove(); });
  }
  function drawExtras(){
    const t = tOut(), D = outDur(), pb = S.progress_bar, bar = el("ws-pbar");
    bar.style.display = pb.on ? "" : "none";
    if (pb.on) {
      bar.style.background = pb.color; bar.style.width = clamp(t / D, 0, 1) * 100 + "%";
      bar.style.top = pb.position === "top" ? "0" : ""; bar.style.bottom = pb.position === "top" ? "" : "0";
    }
    let a = 0;
    if (S.fade_in > 0 && t < S.fade_in) a = Math.max(a, 1 - t / S.fade_in);
    if (S.fade_out > 0 && t > D - S.fade_out) a = Math.max(a, (t - (D - S.fade_out)) / S.fade_out);
    el("ws-fade").style.opacity = clamp(a, 0, 1);
    el("ws-tc").textContent = `${Math.max(0, t).toFixed(1)} / ${D.toFixed(1)} s` + (S.speed !== 1 ? ` · ${S.speed}×` : "");
  }
  function refresh(){
    if (!X.open) return;
    sizeFrame(); drawFrame(); drawImages(); drawCaptions(); drawTexts(); drawExtras(); renderTimeline(); drawSource();
    el("ws-title").value = S.title;
    const sp = el("ws-speed");
    if (![...sp.options].some(o => +o.value === S.speed)) sp.add(new Option(S.speed + "×", S.speed));
    sp.value = String(S.speed);
    document.querySelector('#ws [data-act="undo"]').disabled = !hist.length;
    document.querySelector('#ws [data-act="redo"]').disabled = !fut.length;
  }

  // ------------------------------------------------------------------ timeline
  function renderTimeline(){
    const span = X.hi - X.lo || 1, v = video(), pct = t => (t - X.lo) / span * 100;
    el("ws-sel").style.left = pct(S.start) + "%"; el("ws-sel").style.width = (S.end - S.start) / span * 100 + "%";
    el("ws-ph").style.left = pct(clamp(v.currentTime, X.lo, X.hi)) + "%";
    const d = S.end - S.start;
    const total = outDur() + hookLen() / S.speed;
    el("ws-tl-l").textContent = `Clip ${fmt(S.start)} – ${fmt(S.end)} · ${d.toFixed(1)} s source → ${total.toFixed(1)} s output` +
      (hookOn() ? ` (incl. ${(hookLen() / S.speed).toFixed(1)} s hook)` : "");
    const hk = el("ws-hk");
    if (hookOn()) { hk.hidden = false; hk.style.left = pct(S.hook.start) + "%"; hk.style.width = Math.max(0.4, (S.hook.end - S.hook.start) / span * 100) + "%"; }
    else hk.hidden = true;
    el("ws-tl-r").textContent = outDur() > 60 ? "Longer than 60 s: too long for YouTube Shorts" : "";
    const tt = el("ws-ttrack");
    tt.innerHTML = S.texts.map(t => {
      const a = S.start + t.start * S.speed, b = S.start + Math.min(t.end, outDur()) * S.speed;
      return `<div class="tbar${t.id === X.selText ? " sel" : ""}" data-id="${t.id}" style="left:${pct(a)}%;width:${Math.max(0.5, (b - a) / span * 100)}%;background:${t.box ? t.box_color : t.color};color:${t.box ? t.color : "#111"}" title="${esc(t.text)}">
        <span class="e l" data-edge="start"></span>${esc(t.text)}<span class="e r" data-edge="end"></span></div>`;
    }).join("");
  }
  function renderTicks(){
    const span = X.hi - X.lo || 1;
    el("ws-ticks").innerHTML = X.words.filter((w, i) => i % 2 === 0).map(w => `<span class="tick" style="left:${(w.s - X.lo) / span * 100}%"></span>`).join("");
  }

  // ------------------------------------------------------------------ source panel (facecam, game, crop)
  function srcPanel(){
    const fit = effectiveFit();
    if (fit !== "stack" && fit !== "crop") return "";
    return `<div class="src-wrap" id="ws-src">
      <canvas id="ws-srccv"></canvas>
      <div class="box gamebox" id="ws-gbox"><span>${fit === "stack" ? "GAME" : "VISIBLE AREA"}</span></div>
      ${fit === "stack" ? `<div class="box fcbox" id="ws-fbox"><span>FACECAM</span><i class="hnd"></i></div>` : ""}
    </div>
    <p class="note">${fit === "stack" ? "Drag the pink box onto the camera (corner resizes). Drag the blue box to choose the game area." : "Drag the box to choose what stays in frame."}</p>`;
  }
  function drawSource(){
    const wrap = el("ws-src"); if (!wrap) return;
    const v = video(), c = el("ws-srccv");
    if (v.readyState >= 2) {
      const dpr = Math.min(2, window.devicePixelRatio || 1), W = Math.round(wrap.clientWidth * dpr), H = Math.round(wrap.clientHeight * dpr);
      if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
      c.getContext("2d").drawImage(v, 0, 0, W, H);
    }
    const vw = v.videoWidth || 1920, vh = v.videoHeight || 1080, [W, H] = outSize(), fit = effectiveFit();
    const g = fit === "stack" ? win(vw, vh, W / (H - H * S.cam_share), S.game_zoom, S.game_x, S.game_y)
                              : win(vw, vh, W / H, S.zoom, S.crop_x, S.crop_y);
    Object.assign(el("ws-gbox").style, { left: g.x / vw * 100 + "%", top: g.y / vh * 100 + "%", width: g.w / vw * 100 + "%", height: g.h / vh * 100 + "%" });
    const fb = el("ws-fbox");
    if (fb) { const f = S.facecam || C.default_facecam; Object.assign(fb.style, { left: f.x * 100 + "%", top: f.y * 100 + "%", width: f.w * 100 + "%", height: f.h * 100 + "%" }); }
  }

  // ------------------------------------------------------------------ panels
  const rng = (label, path, min, max, step, fmtv) => {
    const [o, k] = getPath(path), val = o?.[k];
    return `<label class="fld"><span class="lab">${label}<span class="v" data-out="${path}">${fmtv(val)}</span></span>
      <input type="range" min="${min}" max="${max}" step="${step}" value="${val}" data-bind="${path}" data-fmt="${fmtv.name}"></label>`;
  };
  const FMT = { pct: v => Math.round(v * 100) + "%", sec: v => (+v).toFixed(1) + " s", x: v => (+v).toFixed(2) + "×",
                sgn: v => (v > 0 ? "+" : "") + Math.round(v * 100), px: v => Math.round(v) + " px", int: v => String(Math.round(v)) };
  Object.entries(FMT).forEach(([k, f]) => Object.defineProperty(f, "name", { value: k }));
  const chips = (act, items, cur) => `<div class="chips" role="radiogroup">${items.map(([v, l]) =>
    `<button type="button" role="radio" data-act="${act}" data-v="${v}" aria-checked="${String(v) === String(cur)}">${l}</button>`).join("")}</div>`;
  const seg = (act, items, cur) => `<div class="seg" role="radiogroup">${items.map(([v, l]) =>
    `<button type="button" role="radio" data-act="${act}" data-v="${v}" aria-checked="${v === cur}">${l}</button>`).join("")}</div>`;
  const color = (label, path) => { const [o, k] = getPath(path); return `<label class="fld">${label}<input type="color" value="${o[k]}" data-bind="${path}"></label>`; };
  const check = (label, path) => { const [o, k] = getPath(path); return `<label class="fld chk"><input type="checkbox" ${o[k] ? "checked" : ""} data-bind="${path}"> ${label}</label>`; };

  const PANELS = {
    layout(){
      const fit = effectiveFit();
      let h = `<h3>Ratio</h3>${chips("aspect", Object.entries(C.aspects).map(([k, a]) => [k, a.label]), S.aspect)}
        <h3>Template</h3><div class="tiles" role="radiogroup">${TEMPLATES.map(([k, l, svg]) =>
          `<button type="button" class="tile" role="radio" data-act="fit" data-v="${k}" aria-checked="${S.fit === k}"><svg viewBox="0 0 34 60" aria-hidden="true">${svg}</svg>${l}</button>`).join("")}</div>`;
      if (S.fit === "auto") h += `<p class="note">${S.aspect === "vertical" ? "Auto picks per clip when rendering: speaker crop, a pan that follows the action, or the full frame. The preview shows a centre crop." : "Auto uses the full frame for this ratio."}</p>`;
      if (fit === "stack") {
        h += `<h3>Facecam split</h3>${chips("split", SPLITS.map(([v, l]) => [v, l]), SPLITS.find(([v]) => Math.abs(v - S.cam_share) < 0.005)?.[0])}
          ${rng("Facecam height", "cam_share", 0.15, 0.7, 0.01, FMT.pct)}
          <div class="fld">Facecam position${seg("campos", [["top", "Top"], ["bottom", "Bottom"]], S.cam_pos)}</div>
          ${rng("Game zoom", "game_zoom", 1, 3, 0.05, FMT.x)}
          <div class="btnrow"><button type="button" data-act="detect">Detect facecam</button></div>
          <h3>Place facecam & game</h3>${srcPanel()}`;
      } else if (fit === "crop") {
        h += `<h3>Crop</h3>${rng("Zoom", "zoom", 1, 3, 0.05, FMT.x)}<h3>Visible area</h3>${srcPanel()}`;
      } else if (fit === "blur" || fit === "black") {
        h += `<h3>Video size</h3>${rng("Zoom", "zoom", 1, 3, 0.05, FMT.x)}<p class="note">Zoom above 1× fills more of the frame and trims the sides.</p>`;
      }
      return h;
    },
    trim(){
      return `<h3>Start & end</h3>
        ${["start", "end"].map(k => `<div class="fld"><span class="lab">${k === "start" ? "Start" : "End"}<span class="v">${fmt(S[k])}</span></span>
          <div class="btnrow" style="margin-top:4px">
            <button type="button" data-act="nudge" data-k="${k}" data-v="-0.5">−0.5s</button>
            <input type="number" step="0.1" value="${S[k].toFixed(1)}" data-num="${k}" style="width:90px" aria-label="${k} seconds">
            <button type="button" data-act="nudge" data-k="${k}" data-v="0.5">+0.5s</button>
            <button type="button" data-act="sethere" data-k="${k}">Set here</button>
            <button type="button" data-act="snap" data-k="${k}">Snap</button></div></div>`).join("")}
        <p class="note">Drag the orange handles on the timeline too. "Snap" moves to the nearest sentence edge.</p>
        <h3>Hook intro</h3>
        <p class="note" style="margin-top:0">Plays the most gripping 3-5 s first, then the clip from its start.</p>
        <label class="fld chk"><input type="checkbox" data-act-check="hookon" ${hookOn() ? "checked" : ""}> Start with a hook</label>
        <div class="btnrow"><button type="button" data-act="findhook">Find best hook</button>
          ${S.hook && S.hook.start != null ? `<button type="button" data-act="playhook">▶ Preview hook + clip</button>` : ""}</div>
        ${S.hook && S.hook.start != null ? `<div class="hookbox${hookOn() ? "" : " off"}">
          <div class="hooktext">“${esc(S.hook.text || "")}”</div>
          <div class="hookmeta">${fmt(S.hook.start)}–${fmt(S.hook.end)} · ${(S.hook.end - S.hook.start).toFixed(1)} s</div>
          <div class="btnrow"><button type="button" data-act="hookset" data-k="start">Start here</button>
            <button type="button" data-act="hookset" data-k="end">End here</button>
            <button type="button" data-act="hooknudge" data-k="start" data-v="-0.5">Start −0.5s</button>
            <button type="button" data-act="hooknudge" data-k="end" data-v="0.5">End +0.5s</button></div></div>` : ""}
        <h3>Speed</h3>${chips("speed", SPEEDS.map(v => [v, v + "×"]), S.speed)}${rng("Custom speed", "speed", 0.5, 2, 0.05, FMT.x)}
        <p class="note">Captions and text keep their timing at any speed. Output length: ${outDur().toFixed(1)} s.</p>`;
    },
    text(){
      const t = sel();
      let h = `<h3>Add text</h3><div class="btnrow">${Object.entries(TEXT_PRESETS).map(([k, p]) =>
        `<button type="button" data-act="addtext" data-v="${k}">+ ${p.label}</button>`).join("")}</div>
        <h3>Text boxes (${S.texts.length})</h3><div class="tlist" role="listbox" aria-label="Text boxes">${S.texts.map(x =>
          `<button type="button" class="titem" role="option" aria-selected="${x.id === X.selText}" data-act="seltext" data-v="${x.id}">
            <span class="sw" style="background:${x.box ? x.box_color : x.color}"></span><span class="tt">${esc(x.text)}</span>
            <span class="tm">${x.start.toFixed(1)}–${x.end.toFixed(1)}s</span></button>`).join("") || '<p class="note">No text yet. Add one above.</p>'}</div>`;
      if (!t) return h;
      return h + `<h3>Selected text</h3>
        <label class="fld">Text<textarea rows="2" data-bind="sel.text">${esc(t.text)}</textarea></label>
        <div class="row"><label class="fld">Font<select data-bind="sel.font">${C.fonts.map(f => `<option ${f === t.font ? "selected" : ""}>${f}</option>`).join("")}</select></label>
          <label class="fld">Animation<select data-bind="sel.anim">${[["none", "None"], ["fade", "Fade"], ["pop", "Pop in"]].map(([v, l]) => `<option value="${v}" ${v === t.anim ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
        ${rng("Size", "sel.size", 16, 300, 1, FMT.px)}
        <div class="row">${color("Text colour", "sel.color")}${check("Bold", "sel.bold")}</div>
        <div class="row">${check("Italic", "sel.italic")}${check("Shadow", "sel.shadow")}</div>
        <div class="row">${color("Outline colour", "sel.outline_color")}<div>${rng("Outline", "sel.outline", 0, 12, 1, FMT.int)}</div></div>
        ${check("Background box", "sel.box")}
        ${t.box ? `<div class="row">${color("Box colour", "sel.box_color")}<div>${rng("Box opacity", "sel.box_opacity", 0.1, 1, 0.05, FMT.pct)}</div></div>` : ""}
        <h3>Position</h3>
        <div class="btnrow">${[["0.5,0.1", "Top"], ["0.5,0.5", "Centre"], ["0.5,0.85", "Bottom"], ["0.15,0.06", "Top left"], ["0.85,0.95", "Bottom right"]].map(([v, l]) =>
          `<button type="button" data-act="textpos" data-v="${v}">${l}</button>`).join("")}</div>
        ${rng("Horizontal", "sel.x", 0, 1, 0.01, FMT.pct)}${rng("Vertical", "sel.y", 0, 1, 0.01, FMT.pct)}
        <h3>Timing (output seconds)</h3>
        <div class="row"><label class="fld">Show from<input type="number" step="0.1" min="0" value="${t.start}" data-bind="sel.start"></label>
          <label class="fld">Until<input type="number" step="0.1" min="0" value="${t.end}" data-bind="sel.end"></label></div>
        <div class="btnrow"><button type="button" data-act="tfrom">Start here</button><button type="button" data-act="tto">End here</button>
          <button type="button" data-act="twhole">Whole clip</button></div>
        <div class="btnrow"><button type="button" data-act="dupetext">Duplicate</button><button type="button" class="danger" data-act="deltext">Delete (Del)</button></div>`;
    },
    captions(){
      const st = S.style;
      return `${check("Show captions", "captions")}
        ${Object.entries(C.preset_groups).map(([g, gl]) => `<h3>${esc(gl)}</h3><div class="presets" role="radiogroup" aria-label="${esc(gl)}">${
          Object.entries(C.presets).filter(([, p]) => (p.group || "standard") === g).map(([k, p]) =>
          `<button type="button" class="preset${g === "gta" ? " gta" : ""}" role="radio" aria-checked="${k === st.preset}" data-act="preset" data-v="${k}">
            <span class="pv"><span class="cap" data-tile="${k}"></span></span><span class="nm">${esc(p.label)}</span></button>`).join("")}</div>`).join("")}
        <h3>Customize text</h3>
        <div class="fld">Position${seg("cappos", [["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]], st.position)}</div>
        <div class="row"><label class="fld">Font<select data-bind="style.font">${[...new Set([st.font, ...C.fonts])].map(f => `<option ${f === st.font ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
          <div>${rng("Size", "style.scale", 0.5, 1.8, 0.05, FMT.pct)}</div></div>
        <div class="row3">${check("Bold", "style.bold")}${check("Italic", "style.italic")}${check("UPPERCASE", "style.uppercase")}</div>
        ${rng("Letter spacing", "style.spacing", -2, 20, 1, FMT.int)}${rng("Words per line", "style.words_per_line", 1, 8, 1, FMT.int)}
        <div class="row">${color("Text colour", "style.color")}${color("Highlight colour", "style.highlight")}</div>
        <div class="fld">Spoken-word effect${seg("capfx", [["color", "Colour"], ["box", "Box"], ["pop", "Pop"], ["glow", "Glow"], ["plain", "None"]], st.mode)}</div>
        <h3>Border, shadow & background</h3>
        <div class="btnrow" style="margin-top:0"><button type="button" data-act="noborder">No border</button><button type="button" data-act="noshadow">No shadow</button><button type="button" data-act="cleantext">Clean text (no border, shadow or band)</button></div>
        ${check("Background band behind text", "style.background")}
        ${st.background ? `<div class="row">${color("Band colour", "style.band_color")}<div>${rng("Band opacity", "style.band_opacity", 0, 1, 0.05, FMT.pct)}</div></div>` : `
        <div class="row"><div>${rng("Border width", "style.outline", 0, 12, 1, FMT.int)}</div>${color("Border colour", "style.outline_color")}</div>
        <div class="row"><div>${rng("Shadow", "style.shadow", 0, 12, 1, FMT.int)}</div>${color("Shadow colour", "style.shadow_color")}</div>
        ${rng("Shadow opacity", "style.shadow_opacity", 0, 1, 0.05, FMT.pct)}`}
        <div class="btnrow"><button type="button" data-act="resetstyle">Reset to ${esc(C.presets[st.preset].label)} defaults</button></div>
        <h3>Caption text</h3><textarea rows="6" data-bind="text" aria-label="Caption text">${esc(S.text)}</textarea>
        <p class="note">Fix words or spelling. Timing follows the speech. <button type="button" data-act="resettext">Reset to transcript</button></p>`;
    },
    logo(){
      const lib = WM.images || [], im = selImg();
      const posName = (x, y) => Object.entries(WM.positions || {}).find(([, [px, py]]) => Math.abs(px - x) < 0.01 && Math.abs(py - y) < 0.01)?.[0];
      const full = S.images.length >= (WM.max || 3);
      let h = `<h3>Images on this clip (${S.images.length}/${WM.max || 3})</h3>
        <div class="tlist" role="listbox" aria-label="Images on this clip">${S.images.map((x, i) =>
          `<button type="button" class="titem" role="option" aria-selected="${x.id === X.selImg}" data-act="selimg" data-v="${x.id}">
            <img class="ithumb" alt="" src="/wm/${encodeURIComponent(x.file)}"><span class="tt">Image ${i + 1} · ${esc(POS_LABEL[posName(x.x, x.y)] || "Custom spot")}</span>
            <span class="tm">${x.end == null && !x.start ? "whole clip" : `${(x.start || 0).toFixed(1)}–${x.end == null ? outDur().toFixed(1) : x.end.toFixed(1)}s`}</span></button>`).join("")
          || '<p class="note">No images yet.</p>'}</div>
        <div class="btnrow">
          ${LOGO_KINDS.filter(([k]) => WM.all && WM.all[k].file).map(([k, l]) =>
            `<button type="button" data-act="adddefault" data-v="${k}" ${full ? "disabled" : ""}>+ ${l}</button>`).join("")}
          <label class="filebtn2">+ Upload image<input type="file" accept="image/png,image/jpeg,image/webp" data-upload="1" hidden ${full ? "disabled" : ""}></label>
        </div>
        ${lib.length ? `<h3>Your images</h3><div class="wmlib">${lib.map(x =>
          `<button type="button" data-act="addimg" data-v="${esc(x.name)}" title="Add this image (${x.w}×${x.h})" ${full ? "disabled" : ""}><img alt="" src="/wm/${encodeURIComponent(x.name)}"></button>`).join("")}</div>
          <p class="note">Click an image to add it. Transparent PNG logos look best.</p>` : ""}`;
      if (!im) return h + (S.images.length ? '<p class="note">Select an image above (or click it on the preview) to change it.</p>' : "");
      const pk = posName(im.x, im.y);
      return h + `<h3>Selected image</h3>
        <div class="fld">Location<div class="posgrid" role="radiogroup" aria-label="Image location">${Object.keys(WM.positions).map(k =>
          `<button type="button" role="radio" aria-checked="${k === pk}" data-act="imgpos" data-v="${k}" title="${POS_LABEL[k]}" aria-label="${POS_LABEL[k]}"></button>`).join("")}</div></div>
        <p class="note">Or drag it on the preview.</p>
        ${rng("Size", "selimg.size", 0.03, 0.9, 0.01, FMT.pct)}${rng("Opacity", "selimg.opacity", 0.05, 1, 0.05, FMT.pct)}
        <h3>When it shows</h3>
        <label class="fld chk"><input type="checkbox" data-act-check="imgwhole" ${im.end == null && !im.start ? "checked" : ""}> Whole clip</label>
        ${im.end == null && !im.start ? "" : `<div class="row"><label class="fld">From (s)<input type="number" step="0.1" min="0" value="${im.start || 0}" data-bind="selimg.start"></label>
          <label class="fld">Until (s)<input type="number" step="0.1" min="0" value="${im.end == null ? outDur().toFixed(1) : im.end}" data-bind="selimg.end"></label></div>
          <div class="btnrow"><button type="button" data-act="imgfrom">Start here</button><button type="button" data-act="imgto">End here</button></div>`}
        <div class="btnrow"><button type="button" data-act="imgdefault" data-v="watermark">Make this my channel logo</button>
          <button type="button" data-act="imgdefault" data-v="title">Make this my title logo</button>
          <button type="button" class="danger" data-act="delimg">Remove from clip</button></div>`;
    },
    filters(){
      const match = Object.entries(C.filters).find(([, p]) => ["brightness", "contrast", "saturation", "warmth", "sharpen"].every(k => Math.abs(p[k] - S.filters[k]) < 1e-3) && !!p.vignette === !!S.filters.vignette);
      return `<h3>Presets</h3>${chips("fpreset", Object.entries(C.filters).map(([k, p]) => [k, p.label]), match?.[0])}
        <h3>Adjust</h3>${rng("Brightness", "filters.brightness", -0.3, 0.3, 0.01, FMT.sgn)}${rng("Contrast", "filters.contrast", 0.5, 1.8, 0.01, FMT.pct)}
        ${rng("Saturation", "filters.saturation", 0, 2.5, 0.01, FMT.pct)}${rng("Warmth", "filters.warmth", -1, 1, 0.05, FMT.sgn)}
        ${rng("Sharpen", "filters.sharpen", 0, 1.5, 0.05, FMT.pct)}${check("Vignette", "filters.vignette")}
        <div class="btnrow"><button type="button" data-act="fpreset" data-v="none">Reset filters</button></div>`;
    },
    audio(){
      return `<h3>Volume</h3>${check("Mute", "mute")}${rng("Volume", "volume", 0, 2, 0.05, FMT.pct)}
        <p class="note">Loudness is normalised for social media first; this adjusts on top.</p>
        <h3>Fades</h3>${rng("Fade in", "fade_in", 0, 3, 0.1, FMT.sec)}${rng("Fade out", "fade_out", 0, 3, 0.1, FMT.sec)}
        <p class="note">Fades apply to both picture (from/to black) and sound.</p>`;
    },
    extras(){
      const pb = S.progress_bar;
      if (X.desc === undefined) { X.desc = null; loadClipDescription(); }
      const dtext = S.description ?? (X.desc ? X.desc.generated : "Loading…");
      return `<h3>Upload description</h3>
        <textarea rows="10" data-bind="description" aria-label="Upload description">${esc(dtext)}</textarea>
        <p class="note">${S.description ? "Edited for this clip." : "From your template (Settings > Description)."} ${(dtext || "").length} / 5000
          ${S.description ? '<button type="button" data-act="descreset">Reset to template</button>' : ""}
          <button type="button" data-act="desccopy">Copy</button></p>
        <h3>Details</h3><label class="fld">Hook (opening line, shown in the clip list)<input maxlength="140" value="${esc(S.hook)}" data-bind="hook"></label>
        <h3>Progress bar</h3>${check("Show progress bar", "progress_bar.on")}
        ${pb.on ? `<div class="row">${color("Colour", "progress_bar.color")}<div class="fld">Position${seg("pbpos", [["top", "Top"], ["bottom", "Bottom"]], pb.position)}</div></div>` : ""}
        <h3>Cover image</h3><p class="note">${S.cover_t == null ? "Picked automatically from the first seconds." : `Frame at ${S.cover_t.toFixed(1)} s of the clip.`}</p>
        <div class="btnrow"><button type="button" data-act="cover">Use current frame</button>${S.cover_t == null ? "" : '<button type="button" data-act="coverauto">Automatic</button>'}</div>
        <h3>Export quality</h3>${seg("quality", [["small", "Small file"], ["standard", "Standard"], ["high", "High"]], S.quality)}
        <h3>Shortcuts</h3><p class="note">Space play/pause · ←/→ 0.1 s (Shift 2 s) · Ctrl+Z undo · Ctrl+Shift+Z redo · Del removes the selected text · Esc closes.</p>`;
    },
  };

  function renderPanel(){
    document.querySelectorAll("#ws [data-tool]").forEach(b => b.setAttribute("aria-selected", b.dataset.tool === X.tool));
    el("ws-panel").innerHTML = PANELS[X.tool]();
    if (X.tool === "captions") document.querySelectorAll("#ws [data-tile]").forEach(n => {
      const k = n.dataset.tile, p = C.presets[k];
      const st = k === S.style.preset ? { ...S.style } : { preset: k, ...presetStyle(k) };
      drawCap(n, ["Something", "for", "me."], 2, st, p.mode === "pop" ? 20 : 12);
    });
    drawSource();
  }

  // ------------------------------------------------------------------ actions
  function addImage(p){
    if (S.images.length >= (WM.max || 3)) { toast(`At most ${WM.max || 3} images per clip`); return; }
    const used = new Set(S.images.map(i => `${i.x},${i.y}`));
    const spot = Object.values(WM.positions).find(([x, y]) => !used.has(`${x},${y}`)) || [0.5, 0.5];
    const im = { id: "w" + Math.random().toString(36).slice(2, 8), file: p.file, x: p.x ?? spot[0], y: p.y ?? spot[1],
                 size: p.size ?? 0.18, opacity: p.opacity ?? 0.9, start: 0, end: null };
    mutate("addimg", () => { S.images.push(im); X.selImg = im.id; });
  }
  async function loadClipDescription(){
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(X.folder)}/clips/${X.rank}/description`);
      X.desc = await r.json();
    } catch { X.desc = { generated: "" }; }
    if (X.tool === "extras") renderPanel();
  }
  function newTextId(){ return "t" + Math.random().toString(36).slice(2, 8); }
  const ACTS = {
    close: () => close(),
    undo, redo,
    save: () => save(),
    play: () => togglePlay(),
    back5: () => seekOut(tOut() - 2),
    fwd5: () => seekOut(tOut() + 2),
    aspect: v => mutate("aspect", () => { S.aspect = v; if (S.facecam) S.facecam = shapeFacecam(S.facecam); }),
    fit: v => mutate("fit", () => {
      S.fit = v;
      if (v === "stack" && S.style.position === "bottom") S.style.position = "middle";
    }) || (v === "stack" && !S.facecam && detect()),
    split: v => mutate("split", () => { S.cam_share = +v; if (S.facecam) S.facecam = shapeFacecam(S.facecam); }),
    campos: v => mutate("campos", () => { S.cam_pos = v; }),
    detect: () => detect(),
    nudge: (v, b) => mutate("trim", () => setRange(b.dataset.k, S[b.dataset.k] + +v)),
    sethere: (v, b) => mutate("trim", () => setRange(b.dataset.k, video().currentTime)),
    snap: (v, b) => mutate("trim", () => {
      const k = b.dataset.k, x = S[k];
      setRange(k, X.bounds.reduce((a, c) => Math.abs(c - x) < Math.abs(a - x) ? c : a, x));
    }),
    findhook: async () => {
      const b = document.querySelector('#ws [data-act="findhook"]'); if (b) { b.disabled = true; b.textContent = "Finding…"; }
      try {
        const r = await fetch(`/api/runs/${encodeURIComponent(X.folder)}/clips/${X.rank}/hook`);
        const d = await r.json();
        if (!r.ok || !d.hook) { toast(d.error || "No good hook found in this clip."); renderPanel(); return; }
        mutate("hook", () => { S.hook = { on: true, start: d.hook.start, end: d.hook.end, text: d.hook.text }; });
        toast("Hook found: " + d.hook.text.slice(0, 60));
      } finally { const b2 = document.querySelector('#ws [data-act="findhook"]'); if (b2) { b2.disabled = false; b2.textContent = "Find best hook"; } }
    },
    playhook: () => { const v = video(); X.phase = "hook"; v.currentTime = S.hook.start; v.playbackRate = S.speed; v.play(); },
    hookset: (v, b) => mutate("hooktime", () => {
      const t = video().currentTime, h = S.hook;
      if (b.dataset.k === "start") h.start = +Math.min(t, h.end - 1).toFixed(2); else h.end = +Math.max(t, h.start + 1).toFixed(2);
      h.text = X.words.filter(w => w.s >= h.start - 0.05 && w.s < h.end).map(w => w.w).join(" ");
    }),
    hooknudge: (v, b) => mutate("hooktime", () => {
      const h = S.hook, k = b.dataset.k;
      h[k] = +(h[k] + +v).toFixed(2);
      if (h.end - h.start < 1) h[k === "start" ? "start" : "end"] = k === "start" ? h.end - 1 : h.start + 1;
      h.text = X.words.filter(w => w.s >= h.start - 0.05 && w.s < h.end).map(w => w.w).join(" ");
    }),
    spdstep: v => {
      const steps = [0.5, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
      const i = steps.findIndex(s => s >= S.speed - 1e-6);
      const next = steps[Math.min(steps.length - 1, Math.max(0, (i < 0 ? steps.length - 1 : i) + +v))];
      mutate("speed", () => { S.speed = next; video().playbackRate = next; });
    },
    speed: v => mutate("speed", () => { S.speed = +v; video().playbackRate = S.speed; }),
    addtext: v => {
      const p = TEXT_PRESETS[v], D = outDur(), t0 = clamp(tOut(), 0, D - 0.5);
      const t = { id: newTextId(), text: p.text, x: p.x ?? 0.5, y: p.y ?? 0.15, size: p.size, font: p.font || "Arial Black",
        bold: p.bold ?? true, italic: false, color: p.color, outline: p.outline ?? 4, outline_color: "#000000", shadow: true,
        box: !!p.box, box_color: p.box_color || "#000000", box_opacity: 0.85, anim: p.anim,
        start: p.whole ? 0 : p.fromEnd ? Math.max(0, D - p.fromEnd) : +t0.toFixed(1),
        end: p.whole ? +D.toFixed(1) : p.fromEnd ? +D.toFixed(1) : +Math.min(D, t0 + 3).toFixed(1) };
      mutate("addtext", () => { S.texts.push(t); X.selText = t.id; });
    },
    seltext: v => { X.selText = v; renderPanel(); refresh(); },
    deltext: () => { if (sel()) mutate("deltext", () => { S.texts = S.texts.filter(t => t.id !== X.selText); X.selText = null; }); },
    dupetext: () => { const t = sel(); if (t) mutate("dupe", () => { const c = { ...t, id: newTextId(), y: clamp(t.y + 0.06, 0, 1) }; S.texts.push(c); X.selText = c.id; }); },
    textpos: v => { const [x, y] = v.split(",").map(Number); mutate("textpos", () => { Object.assign(sel(), { x, y }); }); },
    tfrom: () => mutate("ttime", () => { const t = sel(); t.start = +clamp(tOut(), 0, t.end - 0.1).toFixed(1); }),
    tto: () => mutate("ttime", () => { const t = sel(); t.end = +clamp(tOut(), t.start + 0.1, outDur()).toFixed(1); }),
    twhole: () => mutate("ttime", () => { const t = sel(); t.start = 0; t.end = +outDur().toFixed(1); }),
    selimg: v => { X.selImg = v; renderPanel(); refresh(); },
    addimg: v => addImage({ file: v }),
    adddefault: v => { const d = WM.all[v || "watermark"]; addImage({ file: d.file, x: d.x, y: d.y, size: d.size, opacity: d.opacity }); },
    imgpos: v => mutate("imgpos", () => { const [x, y] = WM.positions[v]; Object.assign(selImg(), { x, y }); }),
    imgfrom: () => mutate("imgtime", () => { const m = selImg(); m.start = +clamp(tOut(), 0, (m.end ?? outDur()) - 0.1).toFixed(1); }),
    imgto: () => mutate("imgtime", () => { const m = selImg(); m.end = +clamp(tOut(), (m.start || 0) + 0.1, outDur()).toFixed(1); }),
    delimg: () => { if (selImg()) mutate("delimg", () => { S.images = S.images.filter(i => i.id !== X.selImg); X.selImg = null; }); },
    imgdefault: async v => {
      const m = selImg(); if (!m) return;
      const kind = v || "watermark";
      const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [kind]: { file: m.file, x: m.x, y: m.y, size: m.size, opacity: m.opacity, auto: true } }) });
      if (r.ok) { await loadWatermarks(); toast(`Saved as your ${kind === "title" ? "title" : "channel"} logo - new clips get it automatically`); renderPanel(); }
      else toast("Couldn't save the default watermark.");
    },
    preset: v => mutate("preset", () => {
      const p = C.presets[v];
      Object.assign(S.style, { preset: v, ...presetStyle(v), scale: 1 });
      if (p.position) S.style.position = p.position;
      else if (S.style.position === "top" && C.presets[v]) S.style.position = effectiveFit() === "stack" ? "middle" : "bottom";
    }),
    cappos: v => mutate("cappos", () => { S.style.position = v; }),
    capfx: v => mutate("capfx", () => { S.style.mode = v; }),
    noborder: () => mutate("capborder", () => { S.style.outline = 0; S.style.background = false; }),
    noshadow: () => mutate("capshadow", () => { S.style.shadow = 0; }),
    cleantext: () => mutate("capclean", () => { S.style.outline = 0; S.style.shadow = 0; S.style.background = false; }),
    resetstyle: () => mutate("capreset", () => { Object.assign(S.style, presetStyle(S.style.preset), { scale: 1 }); }),
    resettext: () => mutate("captext", () => { S.textEdited = false; S.text = transcriptText(); }),
    fpreset: v => mutate("fpreset", () => { const { label, ...vals } = C.filters[v]; S.filters = { ...NEUTRAL, ...vals }; }),
    pbpos: v => mutate("pb", () => { S.progress_bar.position = v; }),
    cover: () => mutate("cover", () => { S.cover_t = +clamp(tOut(), 0, outDur()).toFixed(2); }),
    coverauto: () => mutate("cover", () => { S.cover_t = null; }),
    quality: v => mutate("quality", () => { S.quality = v; }),
    descreset: () => mutate("desc", () => { S.description = null; }),
    desccopy: () => navigator.clipboard.writeText(S.description ?? (X.desc ? X.desc.generated : "")).then(() => toast("Description copied")),
  };

  function transcriptText(){ return X.words.filter(w => w.s >= S.start - 0.05 && w.s < S.end).map(w => w.w).join(" "); }
  function setRange(k, val){
    if (k === "start") S.start = +clamp(val, X.lo, S.end - 1).toFixed(2);
    else S.end = +clamp(val, S.start + 1, X.hi).toFixed(2);
    if (!S.textEdited) S.text = transcriptText();
    const D = outDur();
    S.texts.forEach(t => { t.end = Math.min(t.end, +D.toFixed(2)); t.start = Math.min(t.start, Math.max(0, t.end - 0.1)); });
  }
  function seekOut(t){ video().currentTime = S.start + clamp(t, 0, outDur()) * S.speed; }
  function togglePlay(){
    const v = video();
    if (v.paused) {
      const fromTop = v.currentTime < S.start || v.currentTime >= S.end - 0.05;
      if (fromTop && hookOn()) { X.phase = "hook"; v.currentTime = S.hook.start; }
      else if (fromTop) { X.phase = "main"; v.currentTime = S.start; }
      v.playbackRate = S.speed; v.play();
    }
    else v.pause();
  }

  async function detect(){
    const b = document.querySelector('#ws [data-act="detect"]');
    if (b) { b.disabled = true; b.textContent = "Detecting…"; }
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(X.folder)}/clips/${X.rank}/facecam`);
      const d = await r.json();
      const found = r.ok && d.facecam;
      mutate("detect", () => { S.facecam = shapeFacecam(found || S.facecam || { ...C.default_facecam }); });
      toast(found ? "Facecam found" : "No facecam found - place the pink box by hand");
    } finally { const b2 = document.querySelector('#ws [data-act="detect"]'); if (b2) { b2.disabled = false; b2.textContent = "Detect facecam"; } }
  }

  // ------------------------------------------------------------------ wiring
  function wire(){
    const ws = el("ws"), v = video();
    ws.addEventListener("click", e => {
      const tb = e.target.closest("[data-tool]");
      if (tb) { X.tool = tb.dataset.tool; renderPanel(); return; }
      const b = e.target.closest("[data-act]");
      if (b && !b.disabled && ACTS[b.dataset.act]) ACTS[b.dataset.act](b.dataset.v, b);
    });
    // Generic two-way binding for inputs in the panel.
    const onBind = e => {
      const inp = e.target.closest("[data-bind]");
      if (inp) {
        const path = inp.dataset.bind, [o, k] = getPath(path);
        if (!o) return;
        let val = inp.type === "checkbox" ? inp.checked : inp.type === "range" || inp.type === "number" ? +inp.value : inp.value;
        if (inp.type === "color") val = val.toUpperCase();
        const structural = inp.type === "checkbox" || inp.tagName === "SELECT";
        mutate(path, () => {
          o[k] = val;
          if (path === "text") S.textEdited = true;
          if (path === "speed") video().playbackRate = val;
          if (path === "cam_share" && S.facecam) S.facecam = shapeFacecam(S.facecam);
          if (path === "sel.start" || path === "sel.end") { const t = sel(); t.start = clamp(t.start, 0, outDur()); t.end = clamp(t.end, t.start + 0.1, outDur()); }
        }, { panel: structural });
        const out = document.querySelector(`#ws [data-out="${path}"]`);
        if (out) out.textContent = FMT[inp.dataset.fmt]?.(val) ?? val;
        if (X.tool === "text" && path === "sel.text") { const it = document.querySelector(`#ws .titem[data-v="${X.selText}"] .tt`); if (it) it.textContent = val; }
        return;
      }
      const num = e.target.closest("[data-num]");
      if (num && e.type === "change") mutate("trim", () => setRange(num.dataset.num, +num.value));
    };
    ws.addEventListener("input", onBind);
    ws.addEventListener("change", async e => {
      if (e.target.matches("[data-upload]")) {
        const f = e.target.files[0]; e.target.value = "";
        try { const im = await uploadWatermark(f); addImage({ file: im.name }); toast("Image uploaded and added"); }
        catch (err) { toast(err.message); }
      }
      if (e.target.matches('[data-act-check="hookon"]')) {
        if (e.target.checked && !(S.hook && S.hook.start != null)) { e.target.checked = false; ACTS.findhook(); return; }
        mutate("hookon", () => { S.hook.on = e.target.checked; });
      }
      if (e.target.matches('[data-act-check="imgwhole"]')) {
        mutate("imgtime", () => { const m = selImg(); if (e.target.checked) { m.start = 0; m.end = null; } else { m.start = 0; m.end = +outDur().toFixed(1); } });
      }
    });
    ws.addEventListener("change", e => { if (e.target.closest("[data-num]") || e.target.closest("select,[type=checkbox]")) onBind(e); });
    el("ws-title").addEventListener("input", e => mutate("title", () => { S.title = e.target.value; }, { panel: false }));
    el("ws-speed").addEventListener("change", e => mutate("speed", () => { S.speed = +e.target.value; video().playbackRate = S.speed; }));

    v.addEventListener("loadeddata", () => { refresh(); renderPanel(); });
    v.addEventListener("seeked", refresh);
    const tick = () => {
      if (!X.open) return;
      if (X.phase === "hook" && v.currentTime >= S.hook.end) { X.phase = "main"; v.currentTime = S.start; }
      else if (!v.paused && v.currentTime >= S.end) { v.pause(); v.currentTime = S.end; X.phase = "main"; }
      refresh();
      document.querySelector('#ws [data-act="play"]').textContent = v.paused ? "▶ Play" : "❚❚ Pause";
      if (!v.paused) requestAnimationFrame(tick);
    };
    v.addEventListener("play", () => requestAnimationFrame(tick));
    v.addEventListener("pause", () => { document.querySelector('#ws [data-act="play"]').textContent = "▶ Play"; });
    window.addEventListener("resize", () => { if (X.open) refresh(); });

    // Timeline: click to seek, drag handles to trim, drag text bars to move / resize them.
    const track = el("ws-track");
    let tdrag = null;
    const tAt = (e, node) => { const r = node.getBoundingClientRect(); return X.lo + clamp((e.clientX - r.left) / r.width, 0, 1) * (X.hi - X.lo); };
    track.addEventListener("pointerdown", e => {
      const h = e.target.closest("[data-h]");
      track.setPointerCapture(e.pointerId);
      if (h) { tdrag = { k: h.dataset.h }; hist.push(JSON.stringify(S)); fut = []; lastKey = ""; }
      else { tdrag = { k: "seek" }; v.currentTime = tAt(e, track); }
    });
    track.addEventListener("pointermove", e => {
      if (!tdrag) return;
      const t = tAt(e, track);
      if (tdrag.k === "seek") v.currentTime = t;
      else { setRange(tdrag.k, t); v.currentTime = tdrag.k === "start" ? S.start : Math.max(S.start, S.end - 0.05); dirty = true; el("ws-dirty").textContent = "Unsaved changes"; refresh(); }
    });
    const tEnd = () => { if (tdrag && tdrag.k !== "seek") renderPanel(); tdrag = null; };
    track.addEventListener("pointerup", tEnd); track.addEventListener("pointercancel", tEnd);

    const tt = el("ws-ttrack");
    let bdrag = null;
    tt.addEventListener("pointerdown", e => {
      const bar = e.target.closest(".tbar"); if (!bar) return;
      const t = S.texts.find(x => x.id === bar.dataset.id); if (!t) return;
      X.selText = t.id; tt.setPointerCapture(e.pointerId);
      hist.push(JSON.stringify(S)); fut = []; lastKey = "";
      bdrag = { t, edge: e.target.dataset.edge || "move", x0: tAt(e, tt), s0: t.start, e0: t.end };
      if (X.tool !== "text") X.tool = "text";
      renderPanel();
    });
    tt.addEventListener("pointermove", e => {
      if (!bdrag) return;
      const d = (tAt(e, tt) - bdrag.x0) / S.speed, D = outDur(), t = bdrag.t;
      if (bdrag.edge === "start") t.start = +clamp(bdrag.s0 + d, 0, t.end - 0.1).toFixed(2);
      else if (bdrag.edge === "end") t.end = +clamp(bdrag.e0 + d, t.start + 0.1, D).toFixed(2);
      else { const len = bdrag.e0 - bdrag.s0; t.start = +clamp(bdrag.s0 + d, 0, D - len).toFixed(2); t.end = +(t.start + len).toFixed(2); }
      dirty = true; refresh();
    });
    const bEnd = () => { if (bdrag) renderPanel(); bdrag = null; };
    tt.addEventListener("pointerup", bEnd); tt.addEventListener("pointercancel", bEnd);

    // Drag text boxes on the preview.
    const ilayer = el("ws-ilayer");
    let idrag = null;
    ilayer.addEventListener("pointerdown", e => {
      const n = e.target.closest(".im"); if (!n) return;
      const m = S.images.find(x => x.id === n.dataset.id); if (!m) return;
      e.preventDefault(); ilayer.setPointerCapture(e.pointerId);
      X.selImg = m.id; hist.push(JSON.stringify(S)); fut = []; lastKey = "";
      const r = el("ws-frame").getBoundingClientRect();
      idrag = { m, r, dx: (e.clientX - r.left) / r.width - m.x, dy: (e.clientY - r.top) / r.height - m.y };
      if (X.tool !== "logo") X.tool = "logo";
      renderPanel(); refresh();
    });
    ilayer.addEventListener("pointermove", e => {
      if (!idrag) return;
      const { m, r } = idrag;
      m.x = +clamp((e.clientX - r.left) / r.width - idrag.dx, 0, 1).toFixed(3);
      m.y = +clamp((e.clientY - r.top) / r.height - idrag.dy, 0, 1).toFixed(3);
      dirty = true; el("ws-dirty").textContent = "Unsaved changes"; drawImages();
    });
    const iEnd = () => { if (idrag) renderPanel(); idrag = null; };
    ilayer.addEventListener("pointerup", iEnd); ilayer.addEventListener("pointercancel", iEnd);

    const layer = el("ws-tlayer");
    let pdrag = null;
    layer.addEventListener("pointerdown", e => {
      const n = e.target.closest(".tx"); if (!n) return;
      const t = S.texts.find(x => x.id === n.dataset.id); if (!t) return;
      e.preventDefault(); layer.setPointerCapture(e.pointerId);
      X.selText = t.id; hist.push(JSON.stringify(S)); fut = []; lastKey = "";
      const r = el("ws-frame").getBoundingClientRect();
      pdrag = { t, r, dx: (e.clientX - r.left) / r.width - t.x, dy: (e.clientY - r.top) / r.height - t.y };
      if (X.tool !== "text") X.tool = "text";
      renderPanel(); refresh();
    });
    layer.addEventListener("pointermove", e => {
      if (!pdrag) return;
      const { t, r } = pdrag;
      t.x = +clamp((e.clientX - r.left) / r.width - pdrag.dx, 0, 1).toFixed(3);
      t.y = +clamp((e.clientY - r.top) / r.height - pdrag.dy, 0, 1).toFixed(3);
      dirty = true; el("ws-dirty").textContent = "Unsaved changes"; drawTexts();
    });
    const pEnd = () => { if (pdrag) renderPanel(); pdrag = null; };
    layer.addEventListener("pointerup", pEnd); layer.addEventListener("pointercancel", pEnd);
    el("ws-frame").addEventListener("click", e => { if (!e.target.closest(".tx, .im")) togglePlay(); });

    // Source panel: facecam box move/resize, game / crop box move.
    let sdrag = null;
    el("ws-panel").addEventListener("pointerdown", e => {
      const wrap = e.target.closest("#ws-src"); if (!wrap) return;
      const kind = e.target.closest(".hnd") ? "resize" : e.target.closest("#ws-fbox") ? "move" : e.target.closest("#ws-gbox") ? "game" : null;
      if (!kind) return;
      e.preventDefault(); wrap.setPointerCapture(e.pointerId);
      hist.push(JSON.stringify(S)); fut = []; lastKey = "";
      const r = wrap.getBoundingClientRect();
      sdrag = { kind, r, x0: (e.clientX - r.left) / r.width, y0: (e.clientY - r.top) / r.height,
                fc: { ...(S.facecam || C.default_facecam) }, gx: effectiveFit() === "stack" ? [S.game_x, S.game_y] : [S.crop_x, S.crop_y] };
    });
    el("ws-panel").addEventListener("pointermove", e => {
      if (!sdrag) return;
      const { r } = sdrag, dx = (e.clientX - r.left) / r.width - sdrag.x0, dy = (e.clientY - r.top) / r.height - sdrag.y0, f = sdrag.fc;
      if (sdrag.kind === "move") S.facecam = { ...f, x: clamp(f.x + dx, 0, 1 - f.w), y: clamp(f.y + dy, 0, 1 - f.h) };
      else if (sdrag.kind === "resize") S.facecam = shapeFacecam({ ...f, w: clamp(f.w + dx, 0.05, 1 - f.x) });
      else {
        const [x, y] = [clamp(sdrag.gx[0] + dx, 0, 1), clamp(sdrag.gx[1] + dy, 0, 1)];
        if (effectiveFit() === "stack") { S.game_x = +x.toFixed(3); S.game_y = +y.toFixed(3); } else { S.crop_x = +x.toFixed(3); S.crop_y = +y.toFixed(3); }
      }
      dirty = true; el("ws-dirty").textContent = "Unsaved changes"; refresh();
    });
    const sEnd = () => { sdrag = null; };
    el("ws-panel").addEventListener("pointerup", sEnd); el("ws-panel").addEventListener("pointercancel", sEnd);

    // Keyboard shortcuts.
    document.addEventListener("keydown", e => {
      if (!X.open) return;
      const typing = e.target.closest("input:not([type=range]):not([type=checkbox]):not([type=color]), textarea, select");
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if (mod && e.key.toLowerCase() === "y" && !typing) { e.preventDefault(); redo(); return; }
      if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
      if (typing) return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); seekOut(tOut() + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 2 : 0.1)); }
      else if ((e.key === "Delete" || e.key === "Backspace") && X.tool === "logo" && selImg()) { e.preventDefault(); ACTS.delimg(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel()) { e.preventDefault(); ACTS.deltext(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
  }

  // ------------------------------------------------------------------ open / close / save
  async function open(folder, rank){
    buildDom();
    if (!C) C = await (await fetch("/api/presets")).json();
    const clip = currentRun.clips.find(c => c.rank === rank);
    const r = await fetch(`/api/runs/${encodeURIComponent(folder)}/clips/${rank}/words`);
    const d = await r.json();
    if (!r.ok) { toast(d.error || "Can't edit this clip."); return; }
    const st = clip.style || {}, preset = C.presets[st.preset] ? st.preset : "highlight", p = C.presets[preset];
    X = { open: true, folder, rank, words: d.words, bounds: d.sentence_bounds, videoId: d.video_id,
          lo: Math.max(0, clip.start - 30), hi: Math.min(d.duration || clip.end + 30, clip.end + 30), tool: "layout", selText: null, selImg: null };
    if (!WM.all) await loadWatermarks();
    const n = (v, def) => v === null || v === undefined ? def : v;
    S = {
      title: clip.title, hook: clip.hook, start: clip.start, end: clip.end,
      aspect: clip.aspect || d.settings.aspect || "vertical", fit: clip.fit || d.settings.fit || "auto",
      zoom: n(clip.zoom, 1), crop_x: n(clip.crop_x, 0.5), crop_y: n(clip.crop_y, 0.5),
      facecam: clip.facecam || null, cam_share: n(clip.cam_share, C.default_cam_share), cam_pos: clip.cam_pos || "top",
      game_x: n(clip.game_x, 0.5), game_y: n(clip.game_y, 0.5), game_zoom: n(clip.game_zoom, 1),
      speed: n(clip.speed, 1), filters: { ...NEUTRAL, ...(clip.filters || {}) },
      captions: clip.captions !== false, textEdited: !!clip.caption_text, text: clip.caption_text || "",
      style: { preset, ...presetStyle(preset), position: st.position || p.position || "bottom", scale: n(st.scale, 1),
               ...Object.fromEntries(STYLE_KEYS.filter(k => st[k] !== undefined && st[k] !== null).map(k => [k, st[k]])) },
      texts: (clip.texts || []).map(t => ({ ...t })),
      images: (clip.images || []).map(i => ({ ...i })),
      hook: clip.hook_intro ? { ...clip.hook_intro } : { on: false, start: null, end: null, text: "" },
      description: clip.description || null,
      volume: n(clip.volume, 1), mute: !!clip.mute, fade_in: n(clip.fade_in, 0), fade_out: n(clip.fade_out, 0),
      progress_bar: { on: false, color: "#FFD400", position: "bottom", ...(clip.progress_bar || {}) },
      cover_t: n(clip.cover_t, null), quality: clip.quality || "standard",
    };
    if (!S.textEdited) S.text = transcriptText();
    hist = []; fut = []; lastKey = ""; dirty = false; el("ws-dirty").textContent = "";
    el("ws").hidden = false; document.body.style.overflow = "hidden";
    const v = video();
    v.src = d.source_url || ("/source/" + X.videoId);
    v.addEventListener("loadedmetadata", () => { v.currentTime = S.start; v.playbackRate = S.speed; }, { once: true });
    renderTicks(); renderPanel(); refresh();
    el("ws-title").focus();
  }

  function close(force){
    if (!force && dirty && !confirm("Discard your unsaved changes to this clip?")) return;
    const v = video(); v.pause(); v.removeAttribute("src"); v.load();
    X.open = false; el("ws").hidden = true; document.body.style.overflow = "";
  }

  async function save(){
    const title = S.title.trim(), hook = (S.hook || "").trim();
    if (!title || !hook) { toast("Title and hook can't be empty."); return; }
    const typed = (S.text || "").replace(/\s+/g, " ").trim(), original = transcriptText().replace(/\s+/g, " ").trim();
    const body = {
      start: S.start, end: S.end, title, hook, aspect: S.aspect, fit: S.fit, zoom: S.zoom, crop_x: S.crop_x, crop_y: S.crop_y,
      cam_share: S.cam_share, cam_pos: S.cam_pos, game_x: S.game_x, game_y: S.game_y, game_zoom: S.game_zoom,
      speed: S.speed, filters: S.filters, captions: S.captions,
      caption_text: S.textEdited && typed && typed !== original ? typed : null,
      style: S.style, texts: S.texts, images: S.images,
      hook_intro: S.hook && S.hook.start != null ? S.hook : { on: false },
      description: S.description && X.desc && S.description.trim() === (X.desc.generated || "").trim() ? null : S.description, volume: S.volume, mute: S.mute, fade_in: S.fade_in, fade_out: S.fade_out,
      progress_bar: S.progress_bar, cover_t: S.cover_t, quality: S.quality,
    };
    if (S.fit === "stack") body.facecam = S.facecam || C.default_facecam;
    const btn = document.querySelector('#ws [data-act="save"]');
    btn.disabled = true;
    try {
      releaseVideos();
      const r = await fetch(`/api/runs/${encodeURIComponent(X.folder)}/clips/${X.rank}/edit`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { toast(d.error || "Could not save."); openRun(X.folder); return; }
      const rank = X.rank;
      close(true); setBusy(true);
      showJob({ status: "running", stage: `Rendering clip #${rank}`, progress: 0.05, log: [] });
      poll(d.id);
    } finally { btn.disabled = false; }
  }

  window.openEditor = open;
  window.ClipEditor = { open, close, state: () => S, ctx: () => X, undo, redo };
})();
