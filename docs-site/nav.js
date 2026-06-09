// docs-site/nav.js — active link, copy buttons, in-page TOC, search
(function () {
  // ───── active nav link
  const path = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.getAttribute("href") === path) a.classList.add("active");
  });

  // ───── copy buttons on every <pre>
  document.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = "copy";
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const code = pre.querySelector("code")?.innerText ?? pre.innerText;
      try {
        await navigator.clipboard.writeText(code);
        btn.textContent = "copied";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "copy"; btn.classList.remove("copied"); }, 1400);
      } catch (_) {
        btn.textContent = "err";
        setTimeout(() => { btn.textContent = "copy"; }, 1400);
      }
    });
    pre.appendChild(btn);
  });

  // ───── build in-page TOC (right rail) on wide screens
  const toc = document.getElementById("toc");
  if (toc) {
    const heads = Array.from(document.querySelectorAll(".content h2, .content h3"));
    if (heads.length) {
      heads.forEach((h) => {
        if (!h.id) {
          h.id = h.textContent.trim().toLowerCase()
            .replace(/[^a-z0-9\s-]/g, "").replace(/\s+/g, "-");
        }
        const a = document.createElement("a");
        a.href = "#" + h.id;
        a.textContent = h.textContent.replace(/#$/, "").trim();
        a.className = h.tagName === "H3" ? "h3" : "h2";
        toc.appendChild(a);
      });
    } else {
      toc.style.display = "none";
    }
  }

  // ───── simple search filter
  const search = document.getElementById("search");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value.toLowerCase().trim();
      document.querySelectorAll(".nav a").forEach((a) => {
        const t = a.textContent.toLowerCase();
        a.style.display = (!q || t.includes(q)) ? "" : "none";
      });
    });
  }
})();
