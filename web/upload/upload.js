
// Served under /<session token>/, so every API path here is relative ("api/...").
const $ = id => document.getElementById(id);
 
// Android's captive portal window is a WebView ("; wv)" in the user agent) without
// file picking. iPhone's sign-in window can pick files, so it uses the form directly.
const ua = navigator.userAgent;
if (/Android/.test(ua) && /; wv\)/.test(ua)) {
  $("android").hidden = false;
  $("form").hidden = true;
  $("addr").textContent = location.hostname;     // the captive portal answers on port 80
}
 
$("file").onchange = () => {
  $("go").disabled = !$("file").files.length;
};
 
fetch("api/status").then(r => r.json()).then(s => {
  $("busy").hidden = s.accepting;
  if (!s.accepting) $("file").disabled = true;
}).catch(() => {});
 
$("go").onclick = () => {
  const f = $("file").files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "api/upload");
 
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) $("bar").value = 100 * e.loaded / e.total;
  };
 
  xhr.onload = () => {
    $("bar").hidden = true;
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch {}
 
    if (xhr.status === 200) {
      $("msg").className = "ok";
      $("msg").textContent = "Uploaded! Go to the machine to choose Colored or Black and white and pay. You can leave this Wi-Fi now.";
      $("file").disabled = true;
    } else {
      $("msg").className = "err"; $("msg").textContent = d.detail || "Upload failed. Try again.";
      $("go").disabled = false;
    }
  };
 
  xhr.onerror = () => {
    $("bar").hidden = true;
    $("msg").className = "err";
    $("msg").textContent = "Connection lost. Make sure you're still on the machine's Wi-Fi, then try again.";
    $("go").disabled = false;
  };
 
  $("go").disabled = true;
  $("bar").hidden = false;
  $("bar").value = 0;
  $("msg").className = "";
  $("msg").textContent = "Uploading...";
 
  xhr.send(form);
};
 