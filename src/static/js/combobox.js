/**
 * Accessible dropdown / combobox for a fixed set of choices (Issue #108).
 *
 * A single reusable control that progressively enhances a native ``<select>``: mark any select
 * ``data-combobox`` and it is upgraded on load into an ARIA combobox that opens a listbox
 * **anchored below the field** (never overlapping it), is keyboard-navigable (↑/↓/Home/End/Enter/
 * Esc, type-to-filter), exposes the correct ARIA (``combobox``/``listbox``/``option``,
 * ``aria-expanded``, ``aria-activedescendant``, ``aria-selected``), closes on click-outside, and
 * filters long lists as you type. ``<select multiple data-combobox>`` becomes a multi-select with
 * removable chips.
 *
 * The native ``<select>`` stays in the DOM as the single source of truth — it still holds the
 * value(s), still submits under the same field name, and still emits ``change`` — so existing form
 * JS and server-side handling are unchanged (no behaviour regression). The widget mirrors the
 * select and re-reads it on every ``change`` and whenever its ``<option>`` list changes (a
 * ``MutationObserver``), so a select populated or set programmatically by page JS stays in sync.
 *
 * No inline handlers or styles — external file only (CSP ``script-src 'self'``). Styling lives in
 * ``admin.css`` under the ``.cbx`` namespace, sharing the design tokens with the autocomplete.
 */
(function () {
  "use strict";

  var idSeq = 0;
  function nextId(prefix) {
    idSeq += 1;
    return prefix + "-" + idSeq;
  }

  var CHEVRON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  var CLOSE =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

  /** Resolve the accessible name for the control from the select's label / aria-label. */
  function labelFor(select) {
    if (select.getAttribute("aria-label")) return select.getAttribute("aria-label");
    if (select.id) {
      var lab = document.querySelector('label[for="' + select.id + '"]');
      if (lab) return lab.textContent.trim();
    }
    return "Select an option";
  }

  function Combobox(select) {
    var multiple = select.multiple;
    var placeholder =
      select.getAttribute("data-combobox-placeholder") ||
      (multiple ? "Select…" : "Search…");
    var name = labelFor(select);
    var listId = nextId("cbx-list");

    // ── Build the DOM around the (now visually hidden) native select ──────────────
    var root = document.createElement("div");
    root.className = "cbx" + (multiple ? " cbx-multiple" : "");
    var control = document.createElement("div");
    control.className = "cbx-control";
    var chips = document.createElement("span");
    chips.className = "cbx-chips";
    var input = document.createElement("input");
    input.type = "text";
    input.className = "cbx-input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-haspopup", "listbox");
    input.setAttribute("aria-label", name);
    input.setAttribute("autocomplete", "off");
    input.placeholder = placeholder;
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "cbx-toggle";
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-hidden", "true");
    toggle.innerHTML = CHEVRON;
    var menu = document.createElement("ul");
    menu.className = "cbx-menu";
    menu.id = listId;
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", name);
    if (multiple) menu.setAttribute("aria-multiselectable", "true");
    menu.hidden = true;

    control.appendChild(chips);
    control.appendChild(input);
    control.appendChild(toggle);
    root.appendChild(control);
    root.appendChild(menu);

    // Insert the widget after the select and move the select inside it (hidden, kept for submit).
    select.parentNode.insertBefore(root, select.nextSibling);
    root.appendChild(select);
    select.classList.add("cbx-native");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");

    var options = []; // [{value,text,disabled}]
    var activeIndex = -1; // index into the *filtered* option list
    var filtered = []; // indices into options currently shown
    var suppressSync = false; // guard against our own change events re-entering sync

    function readOptions() {
      options = Array.prototype.map.call(select.options, function (o) {
        return { value: o.value, text: o.text, disabled: o.disabled };
      });
    }

    function selectedValues() {
      return Array.prototype.filter
        .call(select.options, function (o) { return o.selected && o.value !== ""; })
        .map(function (o) { return o.value; });
    }

    function optionByValue(value) {
      for (var i = 0; i < options.length; i += 1) {
        if (options[i].value === value) return options[i];
      }
      return null;
    }

    // ── Rendering ─────────────────────────────────────────────────────────────────
    function renderChips() {
      if (!multiple) return;
      chips.textContent = "";
      selectedValues().forEach(function (value) {
        var opt = optionByValue(value);
        if (!opt) return;
        var chip = document.createElement("span");
        chip.className = "cbx-chip";
        var lab = document.createElement("span");
        lab.textContent = opt.text;
        var rm = document.createElement("button");
        rm.type = "button";
        rm.className = "cbx-chip-x";
        rm.setAttribute("aria-label", "Remove " + opt.text);
        rm.innerHTML = CLOSE;
        rm.addEventListener("click", function (e) {
          e.stopPropagation();
          setSelected(value, false);
          input.focus();
        });
        chip.appendChild(lab);
        chip.appendChild(rm);
        chips.appendChild(chip);
      });
    }

    function renderInputValue() {
      if (multiple) {
        input.placeholder = selectedValues().length ? "" : placeholder;
        return;
      }
      // Single: show the chosen label when the menu is closed and the user isn't typing.
      if (menu.hidden) {
        var vals = selectedValues();
        input.value = vals.length ? (optionByValue(vals[0]) || {}).text || "" : "";
      }
    }

    function currentFilter() {
      // In single mode a closed input shows the label; only treat text as a filter when open.
      return input.value.trim().toLowerCase();
    }

    function renderMenu() {
      var q = menu.hidden ? "" : currentFilter();
      // In single mode the input holds the selected label; don't let it filter to nothing on open.
      if (!multiple && q && optionMatchesSelectedLabel(q)) q = "";
      menu.textContent = "";
      filtered = [];
      var selected = selectedValues();
      options.forEach(function (opt, i) {
        if (opt.value === "" && multiple) return; // the empty "all" option is single-only
        if (q && opt.text.toLowerCase().indexOf(q) === -1) return;
        filtered.push(i);
        var li = document.createElement("li");
        li.className = "cbx-opt";
        li.id = listId + "-opt-" + i;
        li.setAttribute("role", "option");
        var isSel = selected.indexOf(opt.value) !== -1 || (!multiple && opt.value === (select.value || ""));
        li.setAttribute("aria-selected", isSel ? "true" : "false");
        if (opt.disabled) li.setAttribute("aria-disabled", "true");
        var lab = document.createElement("span");
        lab.className = "cbx-opt-lab";
        lab.textContent = opt.text || "—";
        li.appendChild(lab);
        if (isSel) {
          var tick = document.createElement("span");
          tick.className = "cbx-opt-tick";
          tick.setAttribute("aria-hidden", "true");
          tick.textContent = "✓";
          li.appendChild(tick);
        }
        li.addEventListener("mousedown", function (e) {
          e.preventDefault(); // keep focus in the input
          if (opt.disabled) return;
          choose(opt.value);
        });
        menu.appendChild(li);
      });
      if (!filtered.length) {
        var empty = document.createElement("li");
        empty.className = "cbx-empty";
        empty.setAttribute("role", "presentation");
        empty.textContent = "No matches";
        menu.appendChild(empty);
      }
      // Keep the active row valid after a re-filter.
      if (activeIndex >= filtered.length) activeIndex = filtered.length - 1;
      paintActive();
    }

    function optionMatchesSelectedLabel(q) {
      var vals = selectedValues();
      if (!vals.length) return false;
      var opt = optionByValue(vals[0]) || optionByValue(select.value);
      return !!opt && opt.text.toLowerCase() === q;
    }

    function paintActive() {
      var rows = menu.querySelectorAll(".cbx-opt");
      Array.prototype.forEach.call(rows, function (r) { r.classList.remove("active"); });
      if (activeIndex >= 0 && activeIndex < filtered.length) {
        var id = listId + "-opt-" + filtered[activeIndex];
        var el = document.getElementById(id);
        if (el) {
          el.classList.add("active");
          input.setAttribute("aria-activedescendant", id);
          el.scrollIntoView({ block: "nearest" });
          return;
        }
      }
      input.removeAttribute("aria-activedescendant");
    }

    // ── State changes ───────────────────────────────────────────────────────────
    function setSelected(value, on) {
      Array.prototype.forEach.call(select.options, function (o) {
        if (o.value === value) o.selected = on;
      });
      emitChange();
      sync();
    }

    function choose(value) {
      if (multiple) {
        var opt = optionByValue(value);
        var currentlyOn = selectedValues().indexOf(value) !== -1;
        setSelected(value, !currentlyOn);
        input.value = "";
        renderMenu(); // keep open, reflect the toggle
        return;
      }
      suppressSync = true;
      select.value = value;
      suppressSync = false;
      emitChange();
      close();
      sync();
      input.focus();
    }

    function emitChange() {
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // Re-read the native select and repaint the widget (the mirror step).
    function sync() {
      readOptions();
      renderChips();
      renderInputValue();
      if (!menu.hidden) renderMenu();
    }

    // ── Open / close ────────────────────────────────────────────────────────────
    function open() {
      if (!menu.hidden) return;
      readOptions();
      menu.hidden = false;
      root.classList.add("cbx-open");
      input.setAttribute("aria-expanded", "true");
      if (!multiple) input.value = ""; // clear the label so the full list shows; typing filters
      activeIndex = firstSelectedFilteredIndex();
      renderMenu();
    }

    function firstSelectedFilteredIndex() {
      var selected = selectedValues();
      for (var i = 0; i < filtered.length; i += 1) {
        var opt = options[filtered[i]];
        if (selected.indexOf(opt.value) !== -1) return i;
      }
      return filtered.length ? 0 : -1;
    }

    function close() {
      if (menu.hidden) return;
      menu.hidden = true;
      root.classList.remove("cbx-open");
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
      renderInputValue(); // restore the selected label (single)
    }

    function move(delta) {
      if (menu.hidden) { open(); return; }
      if (!filtered.length) return;
      var next = activeIndex + delta;
      if (next < 0) next = 0;
      if (next > filtered.length - 1) next = filtered.length - 1;
      activeIndex = next;
      paintActive();
    }

    // ── Wiring ────────────────────────────────────────────────────────────────────
    input.addEventListener("focus", function () {
      if (!multiple) input.select();
    });
    // A pointer click into the field opens the list (a real dropdown, not just a text box). Click
    // fires only for pointer input, so keyboard tab-focus does not force the menu open.
    input.addEventListener("click", function () {
      if (menu.hidden) open();
    });
    input.addEventListener("input", function () {
      if (menu.hidden) open();
      else renderMenu();
      activeIndex = filtered.length ? 0 : -1;
      paintActive();
    });
    input.addEventListener("keydown", function (e) {
      switch (e.key) {
        case "ArrowDown": e.preventDefault(); move(1); break;
        case "ArrowUp": e.preventDefault(); move(-1); break;
        case "Home": if (!menu.hidden) { e.preventDefault(); activeIndex = 0; paintActive(); } break;
        case "End": if (!menu.hidden) { e.preventDefault(); activeIndex = filtered.length - 1; paintActive(); } break;
        case "Enter":
          if (!menu.hidden && activeIndex >= 0 && activeIndex < filtered.length) {
            e.preventDefault();
            choose(options[filtered[activeIndex]].value);
          }
          break;
        case "Escape":
          if (!menu.hidden) { e.preventDefault(); close(); }
          break;
        case "Backspace":
          if (multiple && input.value === "") {
            var vals = selectedValues();
            if (vals.length) setSelected(vals[vals.length - 1], false);
          }
          break;
        case "Tab":
          close();
          break;
        default:
          break;
      }
    });
    toggle.addEventListener("mousedown", function (e) {
      e.preventDefault();
      if (menu.hidden) { input.focus(); open(); } else { close(); }
    });
    control.addEventListener("mousedown", function (e) {
      // A click anywhere on the control (chips gaps, padding) focuses the input and opens.
      if (e.target === input || e.target === toggle) return;
      if (e.target.closest(".cbx-chip-x")) return;
      e.preventDefault();
      input.focus();
      if (menu.hidden) open();
    });
    document.addEventListener("click", function (e) {
      if (!root.contains(e.target)) close();
    });
    // Page JS may repopulate or reset the select; mirror those changes.
    select.addEventListener("change", function () {
      if (!suppressSync) sync();
    });
    new MutationObserver(function () { sync(); }).observe(select, {
      childList: true,
      subtree: true,
    });
    // Existing per-page code sets ``select.value = …`` to reflect loaded state (e.g. picking a
    // user to edit). That assignment fires no ``change``, so wrap this instance's value setter to
    // re-sync the widget — page JS keeps reading/writing ``.value`` unchanged, and the visible
    // control follows without any per-page edit. Reads and form submission are untouched.
    var valueDesc = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      "value"
    );
    if (valueDesc && valueDesc.get && valueDesc.set) {
      Object.defineProperty(select, "value", {
        configurable: true,
        enumerable: valueDesc.enumerable,
        get: function () {
          return valueDesc.get.call(this);
        },
        set: function (v) {
          valueDesc.set.call(this, v);
          if (!suppressSync) sync();
        },
      });
    }

    // Initial paint.
    sync();
  }

  function enhanceAll(root) {
    var scope = root || document;
    Array.prototype.forEach.call(
      scope.querySelectorAll("select[data-combobox]:not(.cbx-native)"),
      function (select) {
        try {
          Combobox(select);
        } catch (err) {
          // A single broken control must never take down the page's other scripts.
          if (window.console) window.console.error("combobox: enhance failed", err);
        }
      }
    );
  }

  // Public hook so a page that injects a select later can upgrade it (e.g. HTMX swaps).
  window.BKPCombobox = { enhance: enhanceAll };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { enhanceAll(); });
  } else {
    enhanceAll();
  }
})();
