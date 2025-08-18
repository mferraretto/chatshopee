document.addEventListener("DOMContentLoaded", () => {
  const start = document.getElementById("btn-start");
  const stop = document.getElementById("btn-stop");

  if (start) start.onclick = async () => {
    await fetch("/api/loop/start", { method: "POST" });
  };
  if (stop) stop.onclick = async () => {
    await fetch("/api/loop/stop", { method: "POST" });
  };

  // Atualização periódica do snapshot
  async function refresh() {
    try {
      const r = await fetch("/api/snapshot");
      const data = await r.json();
      const rs = document.getElementById("read-snapshot");
      const rp = document.getElementById("reply-preview");
      if (rs) rs.textContent = data.read || "";
      if (rp) rp.textContent = data.reply || "";
    } catch (e) {}
  }
  setInterval(refresh, 2000);
  refresh();
});
