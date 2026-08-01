// progress_bar.js — one file's progress arrow bar: a row of chevron segments,
// each editable by clicking it and picking from that segment's options.
//
// Pure DOM. It does no fetching: the caller supplies the project definition and
// this file's values, and persists through onChange. That is what makes it
// droppable next to any filename — the management card, the 2D tab and the 3D
// tab all render the same component.
"use strict";

import { isValidHexColor } from "./hex_color.mjs";

const UNSET_OUTLINE = "transparent";

export function makeProgressBar({ definition, values, onChange, readOnly } = {}) {
  const segments = (definition && definition.segments) || [];
  const wrap = document.createElement("span");
  wrap.className = "pb-bar";
  wrap.style.cssText = "display:inline-flex;align-items:center;gap:2px;flex-shrink:0";
  // An empty definition renders nothing at all — no container, no layout shift.
  if (!segments.length) return wrap;

  const current = Object.assign({}, values || {});
  let openMenu = null;

  function _optionOf(seg, optionId) {
    return (seg.options || []).find((o) => o.option_id === optionId) || null;
  }

  // A value referencing a deleted segment/option simply does not resolve, so
  // the chevron paints as unset. No error, no cleanup.
  function _paint(btn, seg) {
    const opt = _optionOf(seg, current[seg.segment_id]);
    const color = opt && isValidHexColor(opt.color) ? opt.color : null;
    btn.style.background = color || UNSET_OUTLINE;
    btn.style.borderColor = color || "var(--border)";
    btn.style.color = color ? "#fff" : "var(--text-dim)";
    btn.title = `${seg.name}: ${opt ? opt.label : "unset"}`;
    btn.textContent = seg.name;
  }

  function _onKeydown(e) {
    if (e.key === "Escape") _closeMenu();
  }

  // Document listeners live ONLY while a menu is open. Attaching them per bar
  // at construction would leak two listeners per row, re-added on every list
  // refresh — with a long tracked list that grows without bound.
  function _closeMenu() {
    if (!openMenu) return;
    openMenu.remove();
    openMenu = null;
    document.removeEventListener("click", _closeMenu);
    document.removeEventListener("keydown", _onKeydown);
  }

  function _openMenu(btn, seg) {
    _closeMenu();
    const menu = document.createElement("div");
    menu.className = "pb-menu";
    menu.style.cssText =
      "position:absolute;z-index:40;min-width:9rem;background:var(--surface-2);" +
      "border:1px solid var(--border);border-radius:6px;padding:.2rem;" +
      "box-shadow:0 4px 16px rgba(0,0,0,.4);font-size:.75rem";

    const opts = seg.options || [];
    if (!opts.length) {
      const none = document.createElement("div");
      none.textContent = "No options defined";
      none.style.cssText = "padding:.25rem .45rem;color:var(--text-dim);font-style:italic";
      menu.appendChild(none);
    }
    opts.forEach((opt) => {
      const row = document.createElement("button");
      row.type = "button";
      row.style.cssText =
        "display:flex;align-items:center;gap:.4rem;width:100%;text-align:left;" +
        "background:none;border:none;color:var(--text);padding:.25rem .45rem;" +
        "border-radius:4px;cursor:pointer";
      const dot = document.createElement("span");
      dot.style.cssText = "width:10px;height:10px;border-radius:2px;flex-shrink:0";
      dot.style.background = isValidHexColor(opt.color) ? opt.color : UNSET_OUTLINE;
      const label = document.createElement("span");
      label.textContent = opt.label;
      row.appendChild(dot);
      row.appendChild(label);
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        _choose(btn, seg, opt.option_id);
      });
      menu.appendChild(row);
    });

    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "Clear";
    clear.style.cssText =
      "display:block;width:100%;text-align:left;background:none;border:none;" +
      "border-top:1px solid var(--border);margin-top:.15rem;color:var(--text-dim);" +
      "padding:.25rem .45rem;cursor:pointer";
    clear.addEventListener("click", (e) => {
      e.stopPropagation();
      _choose(btn, seg, null);
    });
    menu.appendChild(clear);

    btn.parentNode.style.position = "relative";
    btn.parentNode.appendChild(menu);
    openMenu = menu;
    // The opening click called stopPropagation, so these cannot fire on it.
    document.addEventListener("click", _closeMenu);
    document.addEventListener("keydown", _onKeydown);
  }

  async function _choose(btn, seg, optionId) {
    _closeMenu();
    const previous = current[seg.segment_id];
    // Optimistic: paint first so the bar feels instant.
    if (optionId === null) delete current[seg.segment_id];
    else current[seg.segment_id] = optionId;
    _paint(btn, seg);
    try {
      if (onChange) await onChange(seg.segment_id, optionId);
    } catch (_err) {
      // Write failed — restore what was there and repaint, so the bar never
      // shows a value the server does not have.
      if (previous === undefined) delete current[seg.segment_id];
      else current[seg.segment_id] = previous;
      _paint(btn, seg);
    }
  }

  segments.forEach((seg, i) => {
    const cell = document.createElement("span");
    cell.style.cssText = "display:inline-flex;align-items:center";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.segmentId = seg.segment_id;
    btn.style.cssText =
      "font-size:.66rem;line-height:1;padding:.2rem .4rem;border:1px solid;" +
      "border-radius:3px;cursor:" + (readOnly ? "default" : "pointer") +
      ";max-width:6rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    _paint(btn, seg);
    if (!readOnly) {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();     // the row opens the video; the bar must not
        if (openMenu) _closeMenu();
        else _openMenu(btn, seg);
      });
    }
    cell.appendChild(btn);
    wrap.appendChild(cell);
    if (i < segments.length - 1) {
      const sep = document.createElement("span");
      sep.textContent = "›";
      sep.style.cssText = "color:var(--text-dim);font-size:.7rem";
      wrap.appendChild(sep);
    }
  });

  return wrap;
}
