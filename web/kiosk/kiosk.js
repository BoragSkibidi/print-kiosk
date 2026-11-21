// Kiosk UI: polls /api/state every 500 ms and draws the screen for the current state.
const $app = document.getElementById("app");
const $paid = document.getElementById("paid");
const $toast = document.getElementById("toast");
let S = null;          // last state from the server
let drawn = "";        // what is on screen, to avoid redrawing every poll
let usbFiles = null;   // file list while on the "usb" screen
let usbPresent = null;
let selPage = 0;       // scanned page shown in the preview (0-based)
let seenScans = 0;     // page count last drawn, to jump to a newly scanned page
let seenJob = null;    // job id last drawn on "choose", to start each job at page 1
 
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const peso = n => "₱" + n;
 
function toast(msg) {
  $toast.textContent = msg; $toast.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => $toast.hidden = true, 3500);
}
 
async function api(path, body) {
  const opt = body === undefined ? {} :
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast(data.detail || "Something went wrong"); throw new Error(data.detail); }
  return data;
}
const post = (path, body = {}) => api(path, body).then(poll).catch(() => {});
 
async function loadUsb() {
  usbFiles = null; draw(true);
  try { usbFiles = await api("/api/usb/files"); } catch { usbFiles = []; }
  draw(true);
}
 
const ACT = {
  phone: () => post("/api/phone"),
  usb: () => post("/api/usb"),          // draw() loads the file list
  pick: i => post("/api/usb/select", {path: usbFiles[i].path}),
  copy: () => post("/api/scan/start", {action: "copy"}),
  scanusb: () => post("/api/scan/start", {action: "usb"}),
  more: () => post("/api/scan/more"),
  scandone: () => post("/api/scan/done"),
  view: i => { selPage = Number(i); draw(true); },
  zoom: () => openViewer(),
  rescan: () => post("/api/scan/rescan", {index: selPage}),
  copies: d => post("/api/copies", {copies: S.job.copies + Number(d)}),
  color: () => post("/api/mode", {mode: "color"}),
  bw: () => post("/api/mode", {mode: "bw"}),
  confirm: () => post("/api/confirm"),
  cancel: () => post("/api/cancel"),
};
$app.addEventListener("click", e => {
  const b = e.target.closest("[data-act]");
  if (b && !b.disabled) ACT[b.dataset.act](b.dataset.arg);
});
 
// Dev only (hardware.coin = "mock"): keys 1, 5, 0 insert P1, P5, P10.
document.addEventListener("keydown", e => {
  const coins = {"1": 1, "5": 5, "0": 10};
  if (S && S.mock_coins && coins[e.key]) api("/api/dev/coin", {value: coins[e.key]}).then(poll).catch(() => {});
});
 
const cancelBtn = (label = "Cancel") => `<button data-act="cancel">${label}</button>`;
 
// Page images: scans while scanning, rendered PDF pages on "choose". ?v= changes per scan / job.
const pageCount = s => s.screen === "choose" ? s.job.pages : s.scans;
const pageSrc = (s, i, full) => (s.screen === "choose"
  ? `/api/job/preview/${i}?v=${s.job.id}` : `/api/scan/preview/${i}?v=${s.scan_pages[i]}`) + (full ? "&full=1" : "");
 
// Loading / busy screen: spinner, title, optional line, optional progress bar.
// pct = number -> real progress (scanner), pct = "busy" -> moving bar (no real progress known).
const loader = (title, sub = "", pct = null) => `
  <div class="center">
    <div class="spinner"></div>
    <p class="big">${title}</p>
    ${sub ? `<p>${sub}</p>` : ""}
    ${pct === null ? "" : bar(pct)}
  </div>`;
const bar = pct => pct === "busy"
  ? `<div class="bar busy"><div></div></div>`
  : `<div class="bar"><div style="width:${pct}%"></div></div><p class="small">${pct}%</p>`;
 
// page strip (own scroll area) | big preview (tap = fullscreen) | side panel
const pagesView = (s, side, empty = "") => {
  const n = pageCount(s);
  return `
  <div class="scanview">
    <div class="strip"><div class="thumbs">${Array.from({length: n}, (_, i) => `
      <button data-act="view" data-arg="${i}" class="${i === selPage ? "selected" : ""}">
        <div class="page"><img src="${pageSrc(s, i)}" loading="lazy" alt=""></div><span>${i + 1}</span></button>`).join("")}
    </div></div>
    ${n ? `<button class="preview" data-act="zoom"><img src="${pageSrc(s, selPage)}" alt="Page ${selPage + 1}">
      <span class="hint">Tap to enlarge</span></button>`
        : `<div class="preview empty"><p>${empty}</p></div>`}
    <div class="side">${side}</div>
  </div>`;
};
 
const SCREENS = {
  idle: s => `
    <h1>Kiosk Print</h1>
    <div class="grid">
      <button data-act="phone">Print from phone</button>
      <button data-act="usb">Print from USB drive</button>
      <button data-act="copy">Photo Copy</button>
      <button data-act="scanusb">Scan to USB drive</button>
    </div>
    <p class="small">${peso(s.prices.color.pesos)} = up to ${s.prices.color.pages} colored pages &nbsp;|&nbsp;
      ${peso(s.prices.bw.pesos)} = up to ${s.prices.bw.pages} black and white pages.
      Scan to flash drive: ${peso((s.prices.scan || s.prices.bw).pesos)} = up to ${(s.prices.scan || s.prices.bw).pages} pages.
      Scanning is free, pay only to print or save. No change given. Files are deleted after printing.</p>`,
 
  phone_wait: s => {
    const ph = s.phone;
    if (!ph || ph.state === "starting") return loader("Starting Wi-Fi...", "Getting a private hotspot ready for your phone.", "busy")
      + `<div class="row">${cancelBtn()}</div>`;
    const err = s.message
      ? `<p class="error">${esc(s.message.replace(/([^.!?])$/, "$1."))} Send another file from your phone.</p>` : "";
    const v = encodeURIComponent(ph.ssid);
    const body = ph.connected ? `
      <div class="center">
        <p class="ok big">Phone connected</p>
        <p>Pick your file on your phone and tap <b>Upload</b>.</p>
      </div>` : `
      <div class="qrs">
        <div class="qr">
          <p class="step"><b>1.</b> Join the Wi-Fi</p>
          <img class="qrcode" src="/api/qr/wifi?v=${v}" alt="">
          <p class="small">Wi-Fi: <b>${esc(ph.ssid)}</b><br>Password: <b>${esc(ph.password)}</b></p>
        </div>
        <div class="qr">
          <p class="step"><b>2.</b> Open the upload page</p>
          <img class="qrcode" src="/api/qr/url?v=${v}" alt="">
          <p class="small">${esc(ph.url.replace(/^http:\/\//, ""))}</p>
        </div>
      </div>
      <p class="small hint">Scan both with your phone camera, in order. Page won't load? Turn off mobile data
        and stay on this Wi-Fi. New Wi-Fi every time, one phone at a time.</p>`;
    return `
    <h2>Print from your phone</h2>
    ${err}
    ${body}
    <div class="spacer"></div>
    <div class="row">${cancelBtn()}</div>`;
  },
 
  usb: s => {
    let body;
    if (!s.usb_present) body = `
      <div class="center">
        <p class="big">Plug in your flash drive</p>
      </div>`;
 
    else if (usbFiles === null) body = loader("Reading flash drive...");
 
    else if (!usbFiles.length) body = `
      <div class="center">
        <p>No PDF, Word or PowerPoint files found.</p>
      </div>`;
    
    else body = `
      <div class="files">${usbFiles.map((f, i) => `
        <button data-act="pick" data-arg="${i}">
          ${esc(f.name)}${f.folder ? `
          
          <div class="folder">
            ${esc(f.folder)}
          </div>` : ""}
        </button>`).join("")}
      </div>`;
    return `<h2>Pick a file</h2>${body}<div class="row">${cancelBtn()}</div>`;
  },
 
  converting: () => loader("Processing your file...", "Please wait.", "busy") + `<div class="row">${cancelBtn()}</div>`,
 
  scanning: s => loader(s.rescan_index === null ? "Scanning..." : `Scanning page ${s.rescan_index + 1} again...`,
    "Keep the lid closed.", s.progress === null ? "busy" : s.progress),
 
  scan_review: s => {
    const n = s.scans, p = selPage, q = s.scan_quote, usbScan = s.scan_action === "usb";
    const price = !q ? "" : usbScan
      ? `Save to flash drive: <b>${peso(q.scan)}</b>`
      : `Colored <b>${peso(q.color)}</b> &nbsp;|&nbsp; B&amp;W <b>${peso(q.bw)}</b>`;
    const ok = s.message && !s.message.startsWith("Scan failed");
    const msg = s.message ? `<p class="${ok ? "small" : "error"}">${esc(s.message)}</p>` : "";
    const title = usbScan ? "Scan to flash drive" : "Photocopy";
    if (!n) return pagesView(s, `
        <h2>${title}</h2>
        ${msg}
        <p>Put your page <b>face down</b> on the glass and close the lid.</p>
        <p class="small">Scanning is free. You pay only to ${usbScan ? "save" : "print"}.</p>
        <div class="spacer"></div>
        <button class="primary" data-act="more">Scan</button>
        <div class="row">${cancelBtn()}<button data-act="scandone" disabled>Done</button></div>`,
      "Your scanned pages will show here.");
    return pagesView(s, `
        <h2>${n} page(s) scanned</h2>
        ${msg}
        <p class="small">Showing page ${p + 1}. Tap a page on the left to check it.</p>
        <p>${price}</p>
        <div class="spacer"></div>
        <button data-act="rescan">Rescan page ${p + 1} <span class="small">(free)</span></button>
        <button data-act="more">Scan next page</button>
        <div class="row">
          ${cancelBtn()}
          <button class="primary" data-act="scandone">Done</button>
        </div>`);
  },
 
  choose: s => {
    const j = s.job, isPrint = j.action === "print";
    const due = j.price === null ? null : Math.max(j.price - s.paid, 0);
    const total = j.pages * j.copies;
    return pagesView(s, `
      <h2 class="name">${esc(j.name)}</h2>
      <p class="small">${j.pages} page(s)${j.copies > 1 ? ` &times; ${j.copies} copies = ${total} pages` : ""}</p>
      ${isPrint ? `
      <div class="row">
        <button data-act="color" class="${j.mode === "color" ? "selected" : ""}">Colored</button>
        <button data-act="bw" class="${j.mode === "bw" ? "selected" : ""}">Black and white</button>
      </div>
      <div class="copies">
        <span>Copies</span>
        <button data-act="copies" data-arg="-1" ${j.copies <= 1 ? "disabled" : ""}>&minus;</button>
        <b>${j.copies}</b>
        <button data-act="copies" data-arg="1" ${j.copies >= j.max_copies ? "disabled" : ""}>+</button>
      </div>` : `<p>Scan saved to your flash drive after payment.</p>`}
      <div class="row" style="align-items:baseline">
        <p>Price: <span class="big">${j.price === null ? "--" : peso(j.price)}</span></p>
        <p>Inserted: <span class="big">${peso(s.paid)}</span></p>
      </div>
      <p>${j.price === null ? "Choose Colored or Black and white." :
           due > 0 ? `Insert <b>${peso(due)}</b> more.` : "Paid. Tap the button below."}
         <span class="small">No change given.</span>
         ${s.mock_coins ? `<span class="small">(dev: keys 1 / 5 / 0)</span>` : ""}</p>
      <div class="spacer"></div>
      <div class="row">
        ${cancelBtn()}
        <button class="primary" data-act="confirm" ${s.enough ? "" : "disabled"}>
          ${isPrint ? "Print" : "Save to flash drive"}</button>
      </div>`);
  },
 
  printing: s => loader("Printing...",
    s.job && s.job.copies > 1 ? `${s.job.copies} copies. Please wait for all pages.` : "Please wait for all pages.", "busy"),
 
  saving: () => loader("Saving...", "Do not remove your flash drive.", "busy"),
 
  done: s => `
    <div class="center">
      <p class="big">
        ${esc(s.message)}
      </p>
      <p>
        Thank you!
      </p>
    </div>
    <div class="row">
      ${cancelBtn("OK")}
    </div>
  `,
 
  error: s => `
    <div class="center">
      <p class="big error">
        Sorry
      </p>
      <p>
        ${esc(s.message)}
      </p>
    </div>
    <div class="row">
      ${cancelBtn("OK")}
    </div>
  `,
 
  out_of_service: s => `
    <div class="center">
      <p class="big">
        Out of service
      </p>
      <p>
        ${esc(s.message)}
      </p>
      <p class="small">
        Please come back later.
      </p>
    </div>
  `,
};
 
function draw(force) {
  if (!S) return;
  if (V && S.screen !== V.screen) closeViewer();            // timed out / cancelled under the viewer
  if (S.screen === "usb" && usbPresent !== S.usb_present) {   // drive plugged in / removed
    usbPresent = S.usb_present;
    if (S.usb_present) { loadUsb(); return; }
  }
  if (S.screen !== "usb") usbPresent = null;
  if (S.screen === "scan_review") {
    if (S.scans !== seenScans) selPage = S.scans - 1;     // new page -> show it
    selPage = Math.max(Math.min(selPage, S.scans - 1), 0);
    seenScans = S.scans;
  } else if (S.screen === "choose") {
    if (S.job.id !== seenJob) { seenJob = S.job.id; selPage = 0; }   // new job -> page 1
    selPage = Math.min(Math.max(selPage, 0), S.job.pages - 1);
  } else if (S.screen !== "scanning") { seenScans = 0; selPage = 0; seenJob = null; }
  const key = JSON.stringify(S) + (S.screen === "usb" ? JSON.stringify(usbFiles) : "") + "|" + selPage;
  if (!force && key === drawn) return;
  drawn = key;
  $paid.textContent = S.paid > 0 && S.screen !== "choose" ? "Inserted " + peso(S.paid) : "";
  const strip = $app.querySelector(".strip"), top = strip ? strip.scrollTop : 0;
  $app.innerHTML = (SCREENS[S.screen] || (() => `<p>${esc(S.screen)}</p>`))(S);
  const strip2 = $app.querySelector(".strip");               // keep the strip where it was
  if (strip2) { strip2.scrollTop = top; strip2.querySelector(".selected")?.scrollIntoView({block: "nearest"}); }
}
 
// ---------- fullscreen page viewer: pinch or +/- to zoom, drag to pan, double-tap to toggle ----------
const $viewer = document.getElementById("viewer");
const $vstage = document.getElementById("vstage");
const $vimg = document.getElementById("vimg");
const $vload = document.getElementById("vload");
const $vpage = document.getElementById("vpage");
const ZOOM_MAX = 5;
let V = null;          // {screen, i, n, scale, x, y} while the viewer is open
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
 
function openViewer() {
  V = {screen: S.screen, i: selPage, n: pageCount(S), scale: 1, x: 0, y: 0};
  $viewer.hidden = false;
  showPage();
}
 
function closeViewer() {
  if (!V) return;
  selPage = V.i;                                   // the page strip follows what was viewed
  V = null;
  $viewer.hidden = true;
  $vimg.removeAttribute("src");
  draw(true);
}
 
function showPage() {
  V.scale = 1; V.x = V.y = 0; applyZoom();
  $vload.textContent = "Loading..."; $vload.hidden = false;
  $vimg.src = pageSrc(S, V.i, true);
  $vpage.textContent = `Page ${V.i + 1} of ${V.n}`;
  $viewer.querySelector("[data-v=prev]").disabled = V.i === 0;
  $viewer.querySelector("[data-v=next]").disabled = V.i === V.n - 1;
}
$vimg.onload = () => { $vload.hidden = true; };
$vimg.onerror = () => { if (V) $vload.textContent = "Preview not available"; };
 
function applyZoom() {          // keep the page from being dragged out of view
  const mx = Math.max(0, ($vimg.offsetWidth * V.scale - $vstage.clientWidth) / 2);
  const my = Math.max(0, ($vimg.offsetHeight * V.scale - $vstage.clientHeight) / 2);
  V.x = clamp(V.x, -mx, mx); V.y = clamp(V.y, -my, my);
  $vimg.style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.scale})`;
}
 
function zoomAt(scale, cx, cy) {  // zoom keeping the point (cx, cy) under the finger
  const r = $vstage.getBoundingClientRect();
  const px = cx - (r.left + r.width / 2), py = cy - (r.top + r.height / 2);
  const s = clamp(scale, 1, ZOOM_MAX), k = s / V.scale;
  V.x = px - (px - V.x) * k; V.y = py - (py - V.y) * k; V.scale = s;
  if (s === 1) V.x = V.y = 0;
  applyZoom();
}
const zoomCenter = f => { const r = $vstage.getBoundingClientRect(); zoomAt(V.scale * f, r.left + r.width / 2, r.top + r.height / 2); };
 
$viewer.addEventListener("click", e => {
  const b = e.target.closest("[data-v]");
  if (!b || b.disabled || !V) return;
  ({prev: () => { V.i--; showPage(); }, next: () => { V.i++; showPage(); },
    in: () => zoomCenter(1.5), out: () => zoomCenter(1 / 1.5), close: closeViewer})[b.dataset.v]();
});
 
const pts = new Map();          // active fingers: pointerId -> {x, y}
let pinch = null, lastTap = 0;
const fingerDist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y) || 1; };
$vstage.addEventListener("pointerdown", e => {
  $vstage.setPointerCapture(e.pointerId);
  pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pts.size === 2) { pinch = {d: fingerDist(), s: V.scale}; lastTap = 0; }
  else if (pts.size === 1) {
    const now = Date.now();
    if (now - lastTap < 300) { zoomAt(V.scale > 1 ? 1 : 2.5, e.clientX, e.clientY); lastTap = 0; }
    else lastTap = now;
  }
});
$vstage.addEventListener("pointermove", e => {
  const p = pts.get(e.pointerId);
  if (!p || !V) return;
  if (pts.size === 1) { V.x += e.clientX - p.x; V.y += e.clientY - p.y; applyZoom(); }
  p.x = e.clientX; p.y = e.clientY;
  if (pts.size === 2 && pinch) {
    const [a, b] = [...pts.values()];
    zoomAt(pinch.s * fingerDist() / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
  }
});
const fingerUp = e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; };
$vstage.addEventListener("pointerup", fingerUp);
$vstage.addEventListener("pointercancel", fingerUp);
$vstage.addEventListener("wheel", e => {           // dev: mouse wheel zoom on the PC
  e.preventDefault();
  if (V) zoomAt(V.scale * (e.deltaY < 0 ? 1.25 : 0.8), e.clientX, e.clientY);
}, {passive: false});
 
async function poll() {
  try {
    const r = await fetch("/api/state");
    if (!r.ok) throw new Error(r.status);
    S = await r.json();
    draw();
  } catch {
    // never had state: the backend is still starting. Had state: we lost it (restart / crash).
    $app.innerHTML = S ? loader("Reconnecting...", "The machine will be back in a moment.")
                       : loader("Starting...", "Getting the machine ready.");
    $paid.textContent = "";
    if (V) { V = null; $viewer.hidden = true; }
    drawn = "";
  }
}
(async function loop() { await poll(); setTimeout(loop, 500); })();