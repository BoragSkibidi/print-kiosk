
// Served under /<session token>/, so every API path here is relative ("api/...").
const $ = id => document.getElementById(id);
 
let maxMB = 25;
let allowed = [".pdf", ".doc", ".docx", ".ppt", ".pptx"];
let chosen = null;          // the File picked by the customer
let uploading = false;
 
// Android's captive portal window is a WebView ("; wv)" in the user agent) without
// file picking. iPhone's sign-in window can pick files, so it uses the form directly.
const ua = navigator.userAgent;
if (/Android/.test(ua) && /; wv\)/.test(ua)) {
  $("android").hidden = false;
  $("form").hidden = true;
  $("addr").textContent = location.hostname;     // the captive portal answers on port 80
}
 
const fmtSize = b => b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`;
const ext = name => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
 
function message(text, cls = "") {
  $("msg").className = cls;
  $("msg").textContent = text;
}
 
// Limits and "is this link still good?" come from the machine.
fetch("api/status").then(r => r.json()).then(s => {
  if (s.max_mb) maxMB = s.max_mb;
  if (s.allowed) allowed = s.allowed;
  $("limits").textContent = `PDF, Word or PowerPoint · up to ${maxMB} MB`;
  if (!s.accepting) {
    $("busy").hidden = false;
    $("drop").classList.add("disabled");
    $("file").disabled = true;
  }
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
      $("fill").classList.add("done");
      setProgress(100, "Uploaded");
      $("go").hidden = true;
      $("change").hidden = true;
      message("Done! Go to the machine to choose Colored or Black and white and pay. You can leave this Wi-Fi now.", "ok");
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
 