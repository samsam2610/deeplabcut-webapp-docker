// src/static/js/components/file_browser.js
//
// Canonical multi-select directory-tree file browser for the main webapp.
//
// Parallel to dlc-3D's
// `deeplabcut-webapp-docker-supports/dlc-3D/src/static/components/file_browser.js`.
// The two stay in sync deliberately; each module governs its own copy via its
// own policy doc + static-analysis tests.
//
// USAGE:
//   import { makeFileBrowser } from "./components/file_browser.js";
//   const picker = makeFileBrowser({
//     inputEl: document.getElementById("my-target"),
//     paneEl:  document.getElementById("my-browser"),
//     dirOnly: false,                  // true → hide files entirely
//     fileFilter: (name) => name.toLowerCase().endsWith(".h5"),  // optional
//     onPick:  (path) => addToQueue(path),  // called on dblclick file
//   });
//   document.getElementById("my-browse-btn").addEventListener("click",
//     () => picker.openAt("/user-data"));
//
// SEMANTICS:
//   - Single-click a row: highlight + write its path to inputEl. Folders also
//     expand inline.
//   - Double-click a file row: emit a transient "Added ✓" badge that fades
//     out, AND invoke onPick(path) if provided. THE BROWSER STAYS OPEN —
//     never auto-hide on dblclick. Users batch-select many files in one
//     browsing session.
//   - paneEl also receives a `file-browser:pick` CustomEvent on dblclick
//     for back-compat with listeners that prefer DOM events.
//
// SHARED HISTORICAL EVENT: legacy lp_cards code listens for
// `lp-picker-dblclick`. We dispatch BOTH names on the pane so the old wiring
// (if it lingers in any consumer) keeps working during transitions.

const _VIDEO_EXTS = new Set([".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"]);
const _IMAGE_EXTS = new Set([".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"]);

function _supportedFile(name, filter) {
  if (filter) return filter(name);
  const i = name.lastIndexOf(".");
  if (i < 0) return false;
  const ext = name.slice(i).toLowerCase();
  return _VIDEO_EXTS.has(ext) || _IMAGE_EXTS.has(ext);
}

function _flashAddedBadge(rowEl) {
  // Build (or reuse) a small badge that fades out after ~1.2s.
  let badge = rowEl.querySelector(".file-browser-added-badge");
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "file-browser-added-badge";
    badge.textContent = "Added ✓";
    badge.style.cssText =
      "margin-left:.4rem;padding:.05rem .35rem;border-radius:3px;" +
      "background:var(--accent, #63b3ed);color:#0a0a1a;" +
      "font-size:.65rem;font-weight:600;font-family:var(--mono);" +
      "transition:opacity .9s ease-out;opacity:1;";
    rowEl.appendChild(badge);
  } else {
    // Restart the fade if the same row is dblclicked again.
    badge.style.transition = "none";
    badge.style.opacity = "1";
    // force reflow so the next transition runs
    void badge.offsetWidth;
    badge.style.transition = "opacity .9s ease-out";
  }
  // Fade and remove
  setTimeout(() => { badge.style.opacity = "0"; }, 300);
  setTimeout(() => { if (badge.parentNode) badge.parentNode.removeChild(badge); }, 1300);
}

export function makeFileBrowser({ inputEl, paneEl, dirOnly = false, fileFilter = null, onPick = null }) {
  let highlightedRow = null;
  let highlightedPath = "";
  let currentDir = "";

  function setHighlight(row, path) {
    if (highlightedRow && highlightedRow !== row) {
      highlightedRow.style.background = "";
      highlightedRow.style.outline = "";
    }
    highlightedRow = row;
    highlightedPath = path;
    inputEl.value = path;
    row.style.background = "var(--accent-dim, rgba(99,179,237,.18))";
    row.style.outline = "1px solid var(--accent, #63b3ed)";
  }

  function emitPick(path, rowEl) {
    if (onPick) {
      try { onPick(path); } catch (e) { /* swallow — never break the browser */ }
    }
    // Legacy + canonical DOM events (bubble:false → only the owner pane sees them)
    paneEl.dispatchEvent(new CustomEvent("file-browser:pick",
      { detail: { path }, bubbles: false }));
    paneEl.dispatchEvent(new CustomEvent("lp-picker-dblclick",
      { detail: { path }, bubbles: false }));
    if (rowEl) _flashAddedBadge(rowEl);
  }

  function makeEntry(name, fullPath, isDir) {
    const wrapper = document.createElement("div");
    const row = document.createElement("div");
    row.style.cssText =
      "display:flex;align-items:center;gap:.3rem;padding:.15rem .4rem;" +
      "border-radius:3px;cursor:pointer";
    const arrow = document.createElement("span");
    arrow.style.cssText = "width:.8rem;color:var(--text-dim);font-size:.7rem";
    arrow.textContent = isDir ? "▶" : "·";
    const label = document.createElement("span");
    label.style.cssText =
      "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;" +
      "white-space:nowrap;font-family:var(--mono);font-size:.74rem";
    label.textContent = name + (isDir ? "/" : "");
    row.appendChild(arrow); row.appendChild(label);
    wrapper.appendChild(row);

    const childContainer = document.createElement("div");
    childContainer.style.cssText = "display:none;padding-left:1rem";
    wrapper.appendChild(childContainer);

    let loaded = false, expanded = false;

    if (isDir) {
      row.addEventListener("click", async () => {
        setHighlight(row, fullPath);
        if (!expanded && !loaded) {
          childContainer.innerHTML =
            "<span style=\"font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block\">Loading…</span>";
          childContainer.style.display = "block";
          try {
            const res = await fetch(`/fs/ls?path=${encodeURIComponent(fullPath)}`);
            const d = await res.json();
            childContainer.innerHTML = "";
            if (!d.error) {
              const vis = (d.entries || []).filter((e) =>
                (e.type === "dir" && e.has_media !== false) ||
                (!dirOnly && e.type === "file" && _supportedFile(e.name, fileFilter)));
              vis.forEach((e) =>
                childContainer.appendChild(makeEntry(
                  e.name,
                  fullPath.replace(/\/+$/, "") + "/" + e.name,
                  e.type === "dir",
                )));
              if (!vis.length) {
                childContainer.innerHTML =
                  "<span style=\"font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block\">(no supported entries)</span>";
              }
            } else {
              childContainer.innerHTML =
                `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">${d.error}</span>`;
            }
          } catch (e) {
            childContainer.innerHTML =
              "<span style=\"font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block\">Error loading.</span>";
          }
          loaded = true; expanded = true; arrow.textContent = "▼";
        } else {
          expanded = !expanded;
          childContainer.style.display = expanded ? "block" : "none";
          arrow.textContent = expanded ? "▼" : "▶";
        }
      });
      // Directories on dblclick: same as single-click here (no auto-pick).
    } else {
      // File row
      row.addEventListener("click", () => setHighlight(row, fullPath));
      row.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        inputEl.value = fullPath;
        // IMPORTANT: do NOT hide paneEl here. The browser stays open so the
        // user can keep adding more files in the same browse session. The
        // transient "Added ✓" badge gives feedback that the click registered.
        emitPick(fullPath, row);
      });
    }

    return wrapper;
  }

  async function browseDir(dirPath) {
    currentDir = dirPath;
    inputEl.value = dirPath;
    paneEl.innerHTML = "<span style=\"font-size:.8rem;color:var(--text-dim)\">Loading…</span>";
    try {
      const res = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
      const data = await res.json();
      if (data.error) { paneEl.textContent = data.error; return; }
      paneEl.innerHTML = "";
      const visible = (data.entries || []).filter((e) =>
        (e.type === "dir" && e.has_media !== false) ||
        (!dirOnly && e.type === "file" && _supportedFile(e.name, fileFilter)));
      if (!visible.length) {
        const empty = document.createElement("span");
        empty.style.cssText = "font-size:.78rem;color:var(--text-dim);padding:.3rem;display:block";
        empty.textContent = dirOnly ? "(no subfolders)" : "(no supported video or image files)";
        paneEl.appendChild(empty);
      } else {
        visible.forEach((e) =>
          paneEl.appendChild(makeEntry(
            e.name,
            (data.path || dirPath).replace(/\/+$/, "") + "/" + e.name,
            e.type === "dir",
          )));
      }
    } catch (err) {
      paneEl.textContent = "Failed to load.";
    }
  }

  function openAt(initialPath) {
    const isHidden = paneEl.classList.contains("hidden");
    paneEl.classList.toggle("hidden");
    if (!isHidden) return;
    const typed = inputEl.value.trim() || initialPath || "/user-data";
    browseDir(typed);
  }

  function up() {
    const cur = (inputEl.value.trim() || currentDir).replace(/\/+$/, "");
    if (!cur) return;
    const parent = cur.split("/").slice(0, -1).join("/") || "/";
    if (parent !== cur) { browseDir(parent); paneEl.classList.remove("hidden"); }
  }

  function refresh() {
    if (currentDir) browseDir(currentDir);
  }

  return { browseDir, openAt, up, refresh, getHighlighted: () => highlightedPath };
}
