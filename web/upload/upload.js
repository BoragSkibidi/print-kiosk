// Served under /<session token>/, so every API path here is relative ("api/...").
const $ = id => document.getElementById(id);

let maxMB = 25;
let allowed = [".pdf", ".doc", ".docx", ".ppt", ".pptx"];
let chosen = null;          // the File picked by the customer
let uploading = false;

const fmtSize = b => b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`;
const ext = name => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();

function message(text, cls = "") {
  $("msg").className = cls;
  $("msg").textContent = text;
}

// On every load (also the reload after an upload) the machine tells us where we are:
// waiting = show the form, processing / ready = file is on the machine, failed, expired.
fetch("api/status", {cache: "no-store"}).then(r => r.json()).then(s => {
  if (s.max_mb) maxMB = s.max_mb;
  if (s.allowed) allowed = s.allowed;
  $("limits").textContent = `PDF, Word or PowerPoint · up to ${maxMB} MB`;
  if (s.stage === "processing" || s.stage === "ready") finished("ready");
  else if (s.stage === "failed") finished("failed", s.message);
  else if (!s.accepting) finished("expired");
}).catch(() => {});

// ---------- choosing a file ----------
function choose(file) {
  if (!file || uploading) return;
  const e = ext(file.name);
  chosen = file;
  $("fname").textContent = file.name;
  $("fsize").textContent = fmtSize(file.size);
  const kind = e.startsWith(".doc") ? "doc" : e.startsWith(".ppt") ? "ppt" : e === ".pdf" ? "pdf" : "";
  $("kind").textContent = kind ? kind.toUpperCase() : "FILE";
  $("kind").className = `kind ${kind}`;
  $("drop").hidden = true;
  $("card").hidden = false;
  $("progress").hidden = true;
  $("change").hidden = false;
  $("change").textContent = "Change";
  $("go").hidden = false;
  message("");

  // Check here first so the customer doesn't wait for an upload that will be refused.
  let problem = "";
  if (!allowed.includes(e)) problem = "Only PDF, Word and PowerPoint files can be printed.";
  else if (file.size > maxMB * 1048576) problem = `This file is too big (max ${maxMB} MB).`;
  $("card").classList.toggle("bad", !!problem);
  $("go").disabled = !!problem;
  if (problem) message(problem, "err");
}

$("file").onchange = () => choose($("file").files[0]);

$("change").onclick = () => {
  if (uploading) return;
  $("file").value = "";
  $("file").click();
};

// Drag and drop (computer browsers; phones just tap)
const drop = $("drop");
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", e => choose(e.dataTransfer.files[0]));

// ---------- uploading ----------
function setProgress(pct, state) {
  $("fill").style.width = `${pct}%`;
  $("pct").textContent = `${Math.round(pct)}%`;
  if (state) $("pstate").textContent = state;
}

$("go").onclick = () => {
  if (!chosen || uploading) return;
  const form = new FormData();
  form.append("file", chosen);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "api/upload");

  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    setProgress(100 * e.loaded / e.total, `${fmtSize(e.loaded)} of ${fmtSize(e.total)}`);
  };

  // Everything sent; the machine is saving the file before it answers.
  xhr.upload.onload = () => {
    $("fill").classList.add("busy");
    $("pstate").textContent = "Sending to the machine...";
    $("pct").textContent = "";
  };

  const finish = () => {
    uploading = false;
    $("fill").classList.remove("busy");
  };

  xhr.onload = () => {
    finish();
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status === 200) {
      $("go").hidden = true;
      $("change").hidden = true;
      $("fill").classList.add("busy");        // uploaded; the machine is opening the file
      $("pstate").textContent = "Opening on the machine...";
      $("pct").textContent = "";
      watchMachine();
    } else {
      $("progress").hidden = true;
      $("change").hidden = false;
      $("go").disabled = false;
      message(d.detail || "Upload failed. Try again.", "err");
    }
  };

  xhr.onerror = () => {
    finish();
    $("progress").hidden = true;
    $("change").hidden = false;
    $("go").disabled = false;
    message("Connection lost. Make sure you're still on the machine's Wi-Fi, then try again.", "err");
  };

  uploading = true;
  $("go").disabled = true;
  $("change").hidden = true;
  $("fill").className = "fill";
  $("progress").hidden = false;
  setProgress(0, "Starting...");
  message("");
  xhr.send(form);
};


// ---------- after the upload: follow the file, reload when the machine hangs up ----------
// The machine can't push to the phone, so we ask every second. Once the file is on the
// machine's screen we show "done". A few seconds later the machine stops its servers and
// turns the Wi-Fi off; the next check fails and we reload, which closes the session page.
function watchMachine() {
  let shown = false, misses = 0;
  const timer = setInterval(async () => {
    try {
      const s = await (await fetch("api/status", {cache: "no-store"})).json();
      misses = 0;
      if (s.stage === "waiting" && s.message) {
        // The machine couldn't open the file but kept the session: let them try again.
        // No reload here; the page and the Wi-Fi stay as they are.
        clearInterval(timer);
        tryAgain(s.message);
        return;
      }
      if (!shown && s.stage !== "processing" && s.stage !== "waiting") {
        shown = true;
        finished(s.stage, s.message);
      }
    } catch {
      if (++misses < 2) return;                // one dropped request isn't the end
      clearInterval(timer);
      window.location.reload();                // server is gone: reload = page closed
    }
  }, 1000);
}

// Back to the file card with the reason, so the customer can pick another file.
function tryAgain(reason) {
  uploading = false;
  $("fill").className = "fill";
  $("progress").hidden = true;
  $("card").classList.add("bad");
  $("change").hidden = false;
  $("go").hidden = false;
  $("go").disabled = true;            // same file would fail again: choose another first
  $("change").textContent = "Choose another";
  const r = /[.!?]$/.test(reason) ? reason : reason + ".";
  message(`${r} Choose another file and upload again.`, "err");
}

// ---------- finished screen ----------
function finished(stage, detail = "") {
  if (stage === "failed") {
    $("final").classList.add("bad");
    $("ftitle").textContent = "The machine couldn't open your file";
    $("ftext").textContent = detail || "Check the machine's screen, then try another file.";
  } else if (stage === "expired") {
    $("ftitle").textContent = "This session has ended";
    $("ftext").textContent = "Tap \"Print from phone\" on the machine to start again.";
  }
  if (stage === "failed" || stage === "expired") document.querySelector("#final .check").hidden = true;
  $("form").hidden = true;
  $("final").hidden = false;
}