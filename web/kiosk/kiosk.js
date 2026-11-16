// Kiosk UI: polls /api/state every 500 ms and draws the screen for the current state.
const $app = document.getElementById("app");
const $paid = document.getElementById("paid");
const $toast = document.getElementById("toast");
let S = null;          // last state from the server
let drawn = "";        // what is on screen, to avoid redrawing every poll
let usbFiles = null;   // file list while on the "usb" screen
let usbPresent = null;

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

const SCREENS = {
  idle: s => `
    <h1>Kiosk Print</h1>
    <div class="grid">
      <button data-act="phone">Print from phone</button>
      <button data-act="usb">Print from flash drive</button>
      <button data-act="copy">Copy</button>
      <button data-act="scanusb">Scan to flash drive</button>
    </div>
    <p class="small">${peso(s.prices.color.pesos)} = up to ${s.prices.color.pages} colored pages &nbsp;|&nbsp;
      ${peso(s.prices.bw.pesos)} = up to ${s.prices.bw.pages} black and white pages.
      No change given. Files are deleted after printing.</p>`,

  phone_wait: s => `
    <h2>Print from your phone</h2>
    <div class="qr">
      <div><img src="/api/qr/wifi" alt=""><p><b>1. Join Wi-Fi</b></p>
        <p class="small">${esc(s.hotspot.ssid)} / ${esc(s.hotspot.password)}</p></div>
      <div><img src="/api/qr/url" alt=""><p><b>2. Open and upload</b></p>
        <p class="small">${esc(s.hotspot.url)}</p></div>
    </div>
    <div class="spacer"></div>
    <div class="row">${cancelBtn()}</div>`,

  usb: s => {
    let body;
    if (!s.usb_present) body = `<div class="center"><p class="big">Plug in your flash drive</p></div>`;
    else if (usbFiles === null) body = `<div class="center"><p>Reading flash drive...</p></div>`;
    else if (!usbFiles.length) body = `<div class="center"><p>No PDF, Word or PowerPoint files found.</p></div>`;
    else body = `<div class="files">${usbFiles.map((f, i) => `
        <button data-act="pick" data-arg="${i}">${esc(f.name)}
        ${f.folder ? `<div class="folder">${esc(f.folder)}</div>` : ""}</button>`).join("")}</div>`;
    return `<h2>Pick a file</h2>${body}<div class="row">${cancelBtn()}</div>`;
  },

  converting: () => `<div class="center"><p class="big">Processing your file...</p><p>Please wait.</p></div>
    <div class="row">${cancelBtn()}</div>`,

  scanning: () => `<div class="center"><p class="big">Scanning...</p><p>Keep the lid closed.</p></div>`,

  scan_review: s => `
    <h2>${s.scans} page(s) scanned</h2>
    ${s.message ? `<p class="error">${esc(s.message)}</p>` : ""}
    <p>Place the next page face down and tap <b>Scan next page</b>, or tap <b>Done</b>.</p>
    <div class="spacer"></div>
    <div class="row">
      ${cancelBtn()}
      <button data-act="more">Scan next page</button>
      <button class="primary" data-act="scandone">Done</button>
    </div>`,

  choose: s => {
    const j = s.job, isPrint = j.action === "print";
    const due = j.price === null ? null : Math.max(j.price - s.paid, 0);
    return `
    <h2>${esc(j.name)}</h2>
    <p>${j.pages} page(s)</p>
    ${isPrint ? `<div class="row">
      <button data-act="color" class="${j.mode === "color" ? "selected" : ""}">Colored</button>
      <button data-act="bw" class="${j.mode === "bw" ? "selected" : ""}">Black and white</button>
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
    </div>`;
  },

  printing: () => `<div class="center"><p class="big">Printing...</p><p>Please wait for all pages.</p></div>`,
  saving: () => `<div class="center"><p class="big">Saving...</p><p>Do not remove your flash drive.</p></div>`,
  done: s => `<div class="center"><p class="big">${esc(s.message)}</p><p>Thank you!</p></div>
    <div class="row">${cancelBtn("OK")}</div>`,
  error: s => `<div class="center"><p class="big error">Sorry</p><p>${esc(s.message)}</p></div>
    <div class="row">${cancelBtn("OK")}</div>`,
  out_of_service: s => `<div class="center"><p class="big">Out of service</p><p>${esc(s.message)}</p>
    <p class="small">Please come back later.</p></div>`,
};

function draw(force) {
  if (!S) return;
  if (S.screen === "usb" && usbPresent !== S.usb_present) {   // drive plugged in / removed
    usbPresent = S.usb_present;
    if (S.usb_present) { loadUsb(); return; }
  }
  if (S.screen !== "usb") usbPresent = null;
  const key = JSON.stringify(S) + (S.screen === "usb" ? JSON.stringify(usbFiles) : "");
  if (!force && key === drawn) return;
  drawn = key;
  $paid.textContent = S.paid > 0 && S.screen !== "choose" ? "Inserted " + peso(S.paid) : "";
  $app.innerHTML = (SCREENS[S.screen] || (() => `<p>${esc(S.screen)}</p>`))(S);
}

async function poll() {
  try { S = await (await fetch("/api/state")).json(); draw(); }
  catch { $app.innerHTML = `<div class="center"><p class="big">Starting...</p></div>`; drawn = ""; }
}
(async function loop() { await poll(); setTimeout(loop, 500); })();
