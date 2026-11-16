const $ = id => document.getElementById(id);
$("file").onchange = () => { 
  $("go").disabled = !$("file").files.length;
};

fetch("/api/status").then(r => r.json()).then(s => {
  $("busy").hidden = s.accepting; 
}).catch(() => {});

$("go").onclick = () => {
  const f = $("file").files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");

  xhr.upload.onprogress = e => {
    if (e.lengthComputable) $("bar").value = 100 * e.loaded / e.total;
  };

  xhr.onload = () => {
    $("bar").hidden = true;
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch {}

    if (xhr.status === 200) {
      $("msg").className = ""; $("msg").textContent = "Uploaded! Go to the machine to choose Colored or Black and white and pay.";
    } else {
      $("msg").className = "err"; $("msg").textContent = d.detail || "Upload failed. Try again.";
      $("go").disabled = false;
    }
  };

  xhr.onerror = () => {
    $("bar").hidden = true;
    $("msg").className = "err";
    $("msg").textContent = "Connection lost. Try again.";
    $("go").disabled = false; 
  };

  $("go").disabled = true;
  $("bar").hidden = false;
  $("bar").value = 0;
  $("msg").textContent = "Uploading...";

  xhr.send(form);
};
