/**
 * Shared plumbing for the admin CRUD dashboard pages (properties, invoices, leases,
 * tenants). Factored out of the RBAC admin page so every admin console talks to the
 * REST API the same way: same JSON read/write, same CSRF handling, same tabs, tables,
 * messages and toasts.
 *
 * Exposes ``window.BKPAdmin``. Writes go through ``window.BKP.writeHeaders()`` (csrf-htmx.js)
 * so the double-submit CSRF token is always attached. Everything is credentials:same-origin;
 * the server re-checks RBAC on every call, so this is UI only.
 *
 * External file only, to satisfy ``script-src 'self'``.
 */
(function () {
  "use strict";

  var ns = (window.BKPAdmin = window.BKPAdmin || {});

  // ── HTTP ─────────────────────────────────────────────────────────────────────

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }

  /** GET an absolute API path, resolving to {ok,status,data}. */
  ns.getJson = function getJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(readResponse);
  };

  /** Send an unsafe JSON request (POST/PATCH/PUT/DELETE) with the CSRF token attached. */
  ns.writeJson = function writeJson(method, url, body) {
    return fetch(url, {
      method: method,
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(readResponse);
  };

  /** Best-effort human message out of a FastAPI error envelope. */
  ns.errorText = function errorText(r, fallback) {
    var d = r && r.data;
    if (d && typeof d.detail === "string") return d.detail;
    if (d && Array.isArray(d.detail) && d.detail.length && d.detail[0].msg) {
      var f = d.detail[0];
      var loc = Array.isArray(f.loc) ? f.loc[f.loc.length - 1] : null;
      return (loc ? loc + ": " : "") + f.msg;
    }
    if (r && r.status === 403) return "You do not have permission for that.";
    if (r && r.status === 401) return "Your session has expired. Sign in again.";
    return fallback;
  };

  // ── DOM helpers ───────────────────────────────────────────────────────────────

  ns.setMsg = function setMsg(el, text, kind) {
    if (el) { el.textContent = text || ""; el.className = "msg" + (kind ? " msg-" + kind : ""); }
  };

  ns.td = function td(text) {
    var el = document.createElement("td");
    el.textContent = text == null ? "" : String(text);
    return el;
  };

  ns.debounce = function debounce(fn, ms) {
    var t;
    return function () {
      var self = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  };

  /** Wire ``.tab[data-tab]`` buttons to ``#panel-<tab>`` sections. */
  ns.setupTabs = function setupTabs() {
    var buttons = Array.prototype.slice.call(document.querySelectorAll(".tab[data-tab]"));
    var listeners = [];
    function show(name) {
      buttons.forEach(function (b) {
        var on = b.getAttribute("data-tab") === name;
        b.classList.toggle("active", on);
        var panel = document.getElementById("panel-" + b.getAttribute("data-tab"));
        if (panel) panel.hidden = !on;
      });
      listeners.forEach(function (fn) { fn(name); });
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { show(b.getAttribute("data-tab")); });
    });
    return { show: show, onShow: function (fn) { listeners.push(fn); } };
  };

  /** Mark one row selected within a table body. */
  ns.selectRow = function selectRow(body, tr) {
    Array.prototype.forEach.call(body.children, function (row) { row.classList.remove("selected"); });
    // A null row clears the selection — what a closing detail panel wants.
    if (tr) tr.classList.add("selected");
  };

  // ── Money helpers (integer minor units + currency, never a float) ───────────────

  /** Format {amount_minor,currency} for display, e.g. 125000 → "R 1,250.00". */
  ns.formatMoney = function formatMoney(money) {
    if (!money || typeof money.amount_minor !== "number") return "—";
    var symbols = { ZAR: "R", USD: "$", EUR: "€", GBP: "£" };
    var major = (money.amount_minor / 100).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    return (symbols[money.currency] || (money.currency + " ")) + " " + major;
  };

  /** Parse a major-unit string ("1250.00") into minor units (125000); null when blank. */
  ns.moneyMinor = function moneyMinor(majorStr) {
    var s = String(majorStr == null ? "" : majorStr).trim();
    if (s === "") return null;
    var n = Number(s);
    if (!isFinite(n) || n < 0) return NaN;
    return Math.round(n * 100);
  };

  // ── URL query state ─────────────────────────────────────────────────────────────

  /**
   * Read the current location's query string as a plain ``{key: value}`` map (Issue 114).
   *
   * The list consoles restore their search term, typed filters and page from this on load, so a
   * filtered/paged (and, from Issue 115, sorted) view is linkable and survives a refresh.
   */
  ns.readQuery = function readQuery() {
    var out = {};
    new URLSearchParams(window.location.search).forEach(function (value, key) { out[key] = value; });
    return out;
  };

  /**
   * Reflect a list's state in the URL query **without navigating** (Issue 114).
   *
   * Called after every load with the same ``URLSearchParams`` the request used, so the address bar
   * always matches what is on screen and the view is copy-pasteable. Uses ``replaceState`` so paging
   * and typing never flood the back-stack; empty params should be dropped by the caller before this.
   *
   * @param {URLSearchParams} params the query the list just requested with
   */
  ns.writeQuery = function writeQuery(params) {
    var qs = params.toString();
    var url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
    try { window.history.replaceState(null, "", url); } catch (e) { /* history unavailable */ }
  };

  // ── Sortable column headers ─────────────────────────────────────────────────────

  var CARET_SVG =
    '<svg viewBox="0 0 20 20" width="14" height="14"><path d="M6 8l4-4 4 4" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M6 12l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  /**
   * Make a table's marked columns click-to-sort (Issue 115), globally consistent across consoles.
   *
   * A console opts a column in by marking its ``<th data-sort-key="name">``; this helper wraps the
   * header label in a keyboard-operable button, tracks the active column + direction (exposed via
   * ``aria-sort`` and a caret), and cycles **ascending → descending → default** on each activation
   * (Enter/Space/click). Sorting is server-backed: the callback hands back the column key + order (or
   * ``null``/``null`` for the default), which the caller sends as ``sort``/``order`` — the server maps
   * them to a per-resource allow-list, so this never trusts a column name blindly.
   *
   * @param {Element|string} table the ``<table>`` (or its id) whose ``thead`` carries the headers
   * @param {{onSort:Function, initial?:{sort?:string, order?:string}}} opts
   *        ``onSort(sortKey|null, order|null)`` runs on every change; ``initial`` restores URL state
   * @returns {{set:Function, current:Function}|null}
   */
  ns.mountSortableHeaders = function mountSortableHeaders(table, opts) {
    opts = opts || {};
    if (typeof table === "string") table = document.getElementById(table);
    if (!table) return null;
    var onSort = typeof opts.onSort === "function" ? opts.onSort : function () {};
    var headers = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
    if (!headers.length) return null;

    var state = { sort: null, order: null };

    headers.forEach(function (th) {
      var key = th.getAttribute("data-sort-key");
      // Wrap the existing label in a real button so it is focusable and Enter/Space-activatable
      // with the right semantics; the th keeps aria-sort as the screen-reader signal.
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "th-sort";
      while (th.firstChild) btn.appendChild(th.firstChild);
      var caret = document.createElement("span");
      caret.className = "th-caret";
      caret.setAttribute("aria-hidden", "true");
      caret.innerHTML = CARET_SVG;
      btn.appendChild(caret);
      th.appendChild(btn);
      th.setAttribute("aria-sort", "none");
      btn.addEventListener("click", function () { cycle(key); });
    });

    /** ascending → descending → default (null) for the clicked column. */
    function nextOrder(key) {
      if (state.sort !== key) return "asc";
      if (state.order === "asc") return "desc";
      return null;
    }

    function paint() {
      headers.forEach(function (th) {
        var active = state.sort === th.getAttribute("data-sort-key");
        th.setAttribute(
          "aria-sort",
          active ? (state.order === "desc" ? "descending" : "ascending") : "none"
        );
        th.classList.toggle("is-sorted", active);
        th.classList.toggle("is-desc", active && state.order === "desc");
      });
    }

    function apply(sort, order, fire) {
      state.sort = order ? sort : null;
      state.order = order || null;
      paint();
      if (fire) onSort(state.sort, state.order);
    }

    function cycle(key) {
      var order = nextOrder(key);
      apply(order ? key : null, order, true);
    }

    if (opts.initial && opts.initial.sort) {
      apply(opts.initial.sort, opts.initial.order === "desc" ? "desc" : "asc", false);
    }

    return {
      set: function (sort, order) { apply(sort || null, order || null, false); },
      current: function () { return { sort: state.sort, order: state.order }; },
    };
  };

  // ── Bulk selection + single delete ──────────────────────────────────────────────

  /**
   * Wire per-row selection + one bulk-delete button into a framework list (Issue 116).
   *
   * The table carries a checkbox column (a header ``.select-all`` and a per-row ``.row-check``
   * with ``data-id``); this helper makes the header a tri-state select-all (none / some / all),
   * keeps a live selection count, and drives a single Delete button that stays **disabled**
   * (``aria-disabled``) until at least one row is selected — then confirms (count shown) and hands
   * the selected ids to ``onDelete`` for the server bulk-delete. The DOM checkboxes are the source
   * of truth, so selection naturally clears whenever the list re-renders (filter / sort / page).
   * Call ``bind()`` after each render to (re)attach the freshly built row checkboxes.
   *
   * @param {{table:(Element|string), deleteBtn?:Element, countEl?:Element, noun?:string,
   *          onDelete:Function}} opts ``onDelete(ids)`` runs after the confirm
   * @returns {{bind:Function, selectedIds:Function, clear:Function}|null}
   */
  ns.mountBulkDelete = function mountBulkDelete(opts) {
    opts = opts || {};
    var table = typeof opts.table === "string" ? document.getElementById(opts.table) : opts.table;
    if (!table) return null;
    var selectAll = table.querySelector("thead .select-all");
    var deleteBtn = opts.deleteBtn || null;
    var countEl = opts.countEl || null;
    var noun = opts.noun || "item";
    var onDelete = typeof opts.onDelete === "function" ? opts.onDelete : function () {};

    function rowBoxes() {
      return Array.prototype.slice.call(table.querySelectorAll("tbody .row-check"));
    }
    function selectedIds() {
      return rowBoxes()
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.getAttribute("data-id"); });
    }

    /** Repaint the select-all tri-state, the count, and the Delete button's disabled state. */
    function updateState() {
      var boxes = rowBoxes();
      var count = boxes.filter(function (cb) { return cb.checked; }).length;
      if (selectAll) {
        selectAll.checked = count > 0 && count === boxes.length;
        selectAll.indeterminate = count > 0 && count < boxes.length;
        selectAll.disabled = boxes.length === 0;
      }
      if (countEl) countEl.textContent = count ? count + " selected" : "";
      if (deleteBtn) {
        deleteBtn.disabled = count === 0;
        deleteBtn.setAttribute("aria-disabled", String(count === 0));
      }
    }

    if (selectAll) {
      selectAll.addEventListener("change", function () {
        var on = selectAll.checked;
        rowBoxes().forEach(function (cb) { cb.checked = on; });
        updateState();
      });
    }
    if (deleteBtn) {
      deleteBtn.addEventListener("click", function () {
        var ids = selectedIds();
        if (!ids.length) return;
        var label = ids.length === 1 ? noun : noun + "s";
        if (!window.confirm("Delete " + ids.length + " " + label + "? This cannot be undone.")) return;
        onDelete(ids);
      });
    }

    /** (Re)attach listeners to the current row checkboxes and refresh the derived state. */
    function bind() {
      rowBoxes().forEach(function (cb) { cb.addEventListener("change", updateState); });
      updateState();
    }
    function clear() {
      rowBoxes().forEach(function (cb) { cb.checked = false; });
      if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
      updateState();
    }

    return { bind: bind, selectedIds: selectedIds, clear: clear };
  };

  /**
   * Build a per-row selection cell (a checkbox carrying ``data-id``) for a framework list row
   * (Issue 116). Clicks inside the cell don't bubble to the row's own select handler, so ticking a
   * box never also opens the row's detail.
   */
  ns.selectionCell = function selectionCell(id) {
    var cell = document.createElement("td");
    cell.className = "col-check";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "row-check";
    cb.setAttribute("data-id", id == null ? "" : String(id));
    cb.setAttribute("aria-label", "Select row");
    cell.appendChild(cb);
    cell.addEventListener("click", function (e) { e.stopPropagation(); });
    return cell;
  };

  // ── Pagination ────────────────────────────────────────────────────────────────

  var DEFAULT_PAGE_SIZES = [10, 20, 50, 100];

  /**
   * Build a reusable pager for an offset/limit list endpoint. It owns the ``offset`` and
   * ``limit`` (page size) the caller reads on each load, renders a page-size selector, a
   * "Page X of Y (N items)" info line, and a numbered nav — First/Prev, individual page
   * buttons with the current one highlighted (an ``…`` gap collapses large ranges), and
   * Next/Last — and calls ``onChange`` whenever the user pages or changes the size (so the
   * caller just re-runs its load with ``pager.offset``/``pager.limit``).
   *
   * Usage:
   *   var pager = BKPAdmin.createPager({ onChange: load });
   *   pager.mount(document.getElementById("tn-pager"));
   *   // in load(): fetch(...?offset=pager.offset&limit=pager.limit)
   *   //            then pager.update(items.length, total);
   *   // on a new search/filter: pager.reset(); load();
   *
   * @param {{sizes?:number[], size?:number, onChange:Function}} opts
   */
  ns.createPager = function createPager(opts) {
    opts = opts || {};
    var sizes = opts.sizes || DEFAULT_PAGE_SIZES;
    var onChange = typeof opts.onChange === "function" ? opts.onChange : function () {};

    var pager = { offset: 0, limit: opts.size || sizes[0], total: 0 };
    var infoEl, pagesEl, firstBtn, prevBtn, nextBtn, lastBtn, sizeSel;

    function fire() { onChange(); }

    /** Total pages for the current total/limit — always at least 1 so the nav has a home. */
    function pageCount() { return pager.total > 0 ? Math.ceil(pager.total / pager.limit) : 1; }
    /** 1-based index of the page the current ``offset`` sits on. */
    function currentPage() { return Math.floor(pager.offset / pager.limit) + 1; }

    /** Move to page ``n`` (clamped to the valid range) and reload — a no-op if already there. */
    function goToPage(n) {
      var target = Math.min(Math.max(1, n), pageCount());
      var offset = (target - 1) * pager.limit;
      if (offset === pager.offset) return;
      pager.offset = offset;
      fire();
    }

    /** The page tokens to render: always page 1 and the last page, a ±1 window around the
     *  current page, and an ``…`` marker wherever a run of numbers is skipped. */
    function pageTokens(current, total) {
      var tokens = [1];
      var left = Math.max(2, current - 1);
      var right = Math.min(total - 1, current + 1);
      if (left > 2) tokens.push("…");
      for (var i = left; i <= right; i++) tokens.push(i);
      if (right < total - 1) tokens.push("…");
      if (total > 1) tokens.push(total);
      return tokens;
    }

    function navBtn(label, aria) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-quiet btn-sm pager-btn";
      b.textContent = label;
      b.setAttribute("aria-label", aria);
      return b;
    }

    /** Repaint the numbered page buttons for the current position. */
    function renderPages() {
      if (!pagesEl) return;
      pagesEl.innerHTML = "";
      var total = pageCount();
      var current = currentPage();
      pageTokens(current, total).forEach(function (tok) {
        if (tok === "…") {
          var gap = document.createElement("span");
          gap.className = "pager-gap";
          gap.setAttribute("aria-hidden", "true");
          gap.textContent = "…";
          pagesEl.appendChild(gap);
          return;
        }
        var b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-quiet btn-sm pager-page" + (tok === current ? " is-active" : "");
        b.textContent = String(tok);
        b.setAttribute("aria-label", "Page " + tok);
        if (tok === current) b.setAttribute("aria-current", "page");
        b.addEventListener("click", function () { goToPage(tok); });
        pagesEl.appendChild(b);
      });
    }

    pager.mount = function mount(container) {
      if (!container) return pager;
      container.classList.add("pager");
      container.innerHTML = "";

      var sizeWrap = document.createElement("label");
      sizeWrap.className = "pager-size";
      sizeWrap.appendChild(document.createTextNode("Rows"));
      sizeSel = document.createElement("select");
      sizeSel.setAttribute("aria-label", "Rows per page");
      sizes.forEach(function (s) {
        var o = document.createElement("option");
        o.value = String(s); o.textContent = String(s);
        if (s === pager.limit) o.selected = true;
        sizeSel.appendChild(o);
      });
      sizeWrap.appendChild(sizeSel);

      infoEl = document.createElement("span");
      infoEl.className = "pager-info";
      infoEl.setAttribute("aria-live", "polite");
      infoEl.textContent = "—";

      firstBtn = navBtn("«", "First page");
      prevBtn = navBtn("‹", "Previous page");
      nextBtn = navBtn("›", "Next page");
      lastBtn = navBtn("»", "Last page");

      pagesEl = document.createElement("div");
      pagesEl.className = "pager-pages";

      var nav = document.createElement("nav");
      nav.className = "pager-nav";
      nav.setAttribute("aria-label", "Pagination");
      nav.appendChild(firstBtn);
      nav.appendChild(prevBtn);
      nav.appendChild(pagesEl);
      nav.appendChild(nextBtn);
      nav.appendChild(lastBtn);

      container.appendChild(sizeWrap);
      container.appendChild(infoEl);
      container.appendChild(nav);

      sizeSel.addEventListener("change", function () {
        pager.limit = parseInt(sizeSel.value, 10) || sizes[0];
        pager.offset = 0; // a new page size restarts from the first page
        fire();
      });
      firstBtn.addEventListener("click", function () { goToPage(1); });
      prevBtn.addEventListener("click", function () { goToPage(currentPage() - 1); });
      nextBtn.addEventListener("click", function () { goToPage(currentPage() + 1); });
      lastBtn.addEventListener("click", function () { goToPage(pageCount()); });
      return pager;
    };

    /**
     * After a load, record the grand total and repaint the info line and numbered nav.
     * ``count`` (rows returned this page) is kept for call-site compatibility; the numbered
     * layout derives everything it needs from ``offset``/``limit``/``total``.
     */
    pager.update = function update(count, total) {
      pager.total = total || 0;
      if (!infoEl) return pager;
      var pages = pageCount();
      var current = currentPage();
      if (!pager.total) {
        infoEl.textContent = "0 items";
      } else {
        var noun = pager.total === 1 ? " item" : " items";
        infoEl.textContent = "Page " + current + " of " + pages + " (" + pager.total + noun + ")";
      }
      renderPages();
      var atStart = current <= 1;
      var atEnd = !pager.total || current >= pages;
      firstBtn.disabled = prevBtn.disabled = atStart;
      nextBtn.disabled = lastBtn.disabled = atEnd;
      return pager;
    };

    /** Jump back to the first page (e.g. when a search term or filter changes). */
    pager.reset = function reset() { pager.offset = 0; return pager; };

    /** Append ``offset``/``limit`` to a URLSearchParams for the caller's request. */
    pager.applyTo = function applyTo(params) {
      params.set("offset", String(pager.offset));
      params.set("limit", String(pager.limit));
      return params;
    };

    return pager;
  };

  // ── Filter bar ────────────────────────────────────────────────────────────────

  var FILTER_STORE_PREFIX = "bkp.fb.";

  /**
   * Upgrade a ``.filter-bar`` into a collapsible, full-width filter panel (Issue 93).
   *
   * The markup carries the controls; this helper wires the behaviour so no console repeats
   * it: a header toggle that shows/hides the ``.filter-bar-body`` (open by default, remembered
   * per page in ``sessionStorage`` under ``data-filter-key``), a one-line summary of the active
   * filters shown while collapsed, and a "Clear filters" control that resets every field and
   * calls back so the list reloads unfiltered.
   *
   * Fields opt in by marking their ``.field`` wrapper ``data-filter`` with a ``data-filter-label``;
   * the control inside may be a ``<select>``, an ``.ac`` autocomplete, or a text/search input. IDs
   * are untouched, so each console's existing ``getElementById`` wiring keeps working — this is a
   * drop-in shell around the controls it already had.
   *
   * @param {Element|string} root the ``.filter-bar`` element (or its id)
   * @param {{onClear?:Function}} opts ``onClear`` runs after the fields are reset (usually ``reload``)
   */
  ns.mountFilterBar = function mountFilterBar(root, opts) {
    opts = opts || {};
    if (typeof root === "string") root = document.getElementById(root);
    if (!root) return null;

    var onClear = typeof opts.onClear === "function" ? opts.onClear : function () {};
    var toggle = root.querySelector(".filter-bar-toggle");
    var bodyEl = root.querySelector(".filter-bar-body");
    var summaryEl = root.querySelector(".filter-bar-summary");
    var clearBtn = root.querySelector(".filter-clear");
    var storeKey = root.getAttribute("data-filter-key");
    storeKey = storeKey ? FILTER_STORE_PREFIX + storeKey : "";

    // Give the toggle a chevron that rotates with the open/closed state (kept out of the
    // templates so every bar looks the same).
    if (toggle && !toggle.querySelector(".fb-chevron")) {
      var chev = document.createElement("span");
      chev.className = "fb-chevron";
      chev.setAttribute("aria-hidden", "true");
      chev.innerHTML =
        '<svg viewBox="0 0 20 20"><path d="M5.5 7.5 10 12l4.5-4.5" fill="none" ' +
        'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      toggle.insertBefore(chev, toggle.firstChild);
    }

    function fields() {
      return Array.prototype.slice.call(root.querySelectorAll(".filter-bar-body [data-filter]"));
    }

    /** The controls that currently hold a value, as ``{label, value}`` for the summary. */
    function activeFilters() {
      var out = [];
      fields().forEach(function (f) {
        var label = f.getAttribute("data-filter-label") || "";
        var sel = f.querySelector("select");
        var acValue = f.querySelector(".ac .ac-value");
        if (sel) {
          if (sel.value) out.push({ label: label, value: sel.options[sel.selectedIndex].text });
        } else if (acValue) {
          if (acValue.value) {
            var acInput = f.querySelector(".ac .ac-input");
            out.push({ label: label, value: (acInput && acInput.value.trim()) || acValue.value });
          }
        } else {
          var input = f.querySelector("input");
          if (input && input.value.trim()) out.push({ label: label, value: input.value.trim() });
        }
      });
      return out;
    }

    function renderSummary() {
      var active = activeFilters();
      if (summaryEl) {
        summaryEl.classList.toggle("is-empty", active.length === 0);
        summaryEl.textContent = active.length
          ? active.map(function (a) { return a.label + ": " + a.value; }).join(" · ")
          : "No filters applied";
      }
      if (clearBtn) clearBtn.hidden = active.length === 0;
    }

    function setOpen(open, persist) {
      root.classList.toggle("is-collapsed", !open);
      if (toggle) toggle.setAttribute("aria-expanded", String(open));
      if (bodyEl) bodyEl.hidden = !open;
      if (summaryEl) summaryEl.hidden = open; // the summary stands in for the controls only when hidden
      renderSummary();
      if (persist && storeKey) {
        try { window.sessionStorage.setItem(storeKey, open ? "open" : "closed"); } catch (e) { /* private mode */ }
      }
    }

    function clearAll() {
      fields().forEach(function (f) {
        var sel = f.querySelector("select");
        var acValue = f.querySelector(".ac .ac-value");
        if (sel) {
          var empty = sel.querySelector('option[value=""]');
          sel.value = empty ? "" : (sel.options.length ? sel.options[0].value : "");
        } else if (acValue && window.BKPAC) {
          window.BKPAC.clear(acValue);
        } else {
          var input = f.querySelector("input");
          if (input) input.value = "";
        }
      });
      renderSummary();
      onClear();
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        setOpen(root.classList.contains("is-collapsed"), true);
      });
    }
    if (clearBtn) clearBtn.addEventListener("click", clearAll);
    // Keep the summary and the clear affordance in step as the user edits filters.
    if (bodyEl) {
      bodyEl.addEventListener("change", renderSummary);
      bodyEl.addEventListener("input", renderSummary);
      bodyEl.addEventListener("ac:select", renderSummary);
    }

    // Collapsed by default to keep the results in view; a remembered "open" for this page (within
    // the session) reopens it, and the active-filter summary stands in while it is closed.
    var stored = "";
    try { stored = storeKey ? (window.sessionStorage.getItem(storeKey) || "") : ""; } catch (e) { stored = ""; }
    setOpen(stored === "open", false);

    return { refresh: renderSummary, setOpen: setOpen, clear: clearAll };
  };

  // ── Slide-over detail panel ────────────────────────────────────────────────────

  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  /**
   * Wire a ``.slideover`` into a reusable right-hand detail dialog (Issue 95).
   *
   * The markup carries the panel; this helper gives it modal-dialog behaviour so any console
   * can adopt "full-width list + slide-over detail": opening records the trigger and traps Tab
   * within the panel, ``Esc`` / the backdrop / any ``[data-slideover-close]`` control closes it,
   * and focus is restored to the trigger on close. Visibility is a state class (``is-open``), so
   * the CSS transition runs and the closed panel stays out of the tab order and a11y tree.
   *
   * @param {Element|string} root the ``.slideover`` element (or its id)
   * @param {{onClose?:Function, onOpen?:Function, lockScroll?:boolean, trapFocus?:boolean}} opts
   *   ``onClose`` runs after the panel closes (e.g. clear selection); ``onOpen`` runs as it opens
   * @returns {{open:Function, close:Function, isOpen:Function, panel:Element}|null}
   */
  ns.mountSlideover = function mountSlideover(root, opts) {
    opts = opts || {};
    if (typeof root === "string") root = document.getElementById(root);
    if (!root) return null;

    var panel = root.querySelector(opts.panelSelector || ".slideover-panel");
    if (!panel) return null;
    var onClose = typeof opts.onClose === "function" ? opts.onClose : function () {};
    var onOpen = typeof opts.onOpen === "function" ? opts.onOpen : function () {};
    // A full centred modal locks the page behind it; an inset side panel (e.g. the log detail)
    // leaves the page scrollable so the wheel still scrolls the list behind it.
    var lockScroll = opts.lockScroll !== false;
    // A modal panel keeps Tab inside itself. A *non-modal* one (the properties detail, which
    // opens beside a list whose rows stay live) must not: the list behind it is still part of the
    // page, and trapping focus would make it unreachable from the keyboard. Esc still closes.
    var trapFocus = opts.trapFocus !== false;
    var lastFocus = null;

    function isOpen() { return root.classList.contains("is-open"); }

    function focusable() {
      return Array.prototype.slice.call(panel.querySelectorAll(FOCUSABLE))
        .filter(function (el) { return el.offsetParent !== null; });
    }

    function onKeydown(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key !== "Tab" || !trapFocus) return;
      var items = focusable();
      if (!items.length) { e.preventDefault(); panel.focus(); return; }
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    function moveFocusIn() {
      // The first control in the panel, else the panel itself.
      var items = focusable();
      (items.length ? items[0] : panel).focus();
    }

    function open(trigger) {
      if (isOpen()) return;
      lastFocus = trigger || (document.activeElement && document.activeElement !== document.body
        ? document.activeElement : null);
      root.classList.add("is-open");
      if (lockScroll) document.body.classList.add("slideover-lock");
      document.addEventListener("keydown", onKeydown, true);
      onOpen();
      // Defer focus to the next frame so the panel's visibility is applied first — focus() is a
      // no-op on a still-hidden element in the same frame the state class is added.
      if (window.requestAnimationFrame) window.requestAnimationFrame(moveFocusIn);
      else moveFocusIn();
    }

    function close() {
      if (!isOpen()) return;
      root.classList.remove("is-open");
      if (lockScroll) document.body.classList.remove("slideover-lock");
      document.removeEventListener("keydown", onKeydown, true);
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
      lastFocus = null;
      onClose();
    }

    Array.prototype.forEach.call(root.querySelectorAll("[data-slideover-close]"), function (b) {
      b.addEventListener("click", close);
    });

    return { open: open, close: close, isOpen: isOpen, panel: panel };
  };

  /**
   * Keep an inset slide-in panel lined up with the block it belongs to (``.slideover-inset``).
   *
   * The panel is fixed to the window, but it should read as though it opened out of the list: its
   * top follows the top of the anchor block as the page scrolls (never above a small gap from the
   * top of the window), its right edge sits on the anchor's right edge, and its bottom stops
   * short of the bottom of the window so the page's own bottom margin — the footer's room — is
   * left clear. The measurements go out as custom properties the CSS consumes.
   *
   * @param {Element} root the ``.slideover`` element
   * @param {Element} anchor the block the panel lines up with (e.g. the list's ``.split``)
   * @returns {{sync:Function, watch:Function, unwatch:Function}}
   */
  ns.mountInsetPanel = function mountInsetPanel(root, anchor) {
    var GAP = 16; // px — the breathing room at the window's edges
    var watching = false;

    function sync() {
      if (!anchor) return;
      var box = anchor.getBoundingClientRect();
      var viewportHeight = document.documentElement.clientHeight;
      var viewportWidth = document.documentElement.clientWidth;
      // Pages in this shell have no footer element; those that do get its height reserved.
      var footer = document.querySelector(".footer");
      var reserved = GAP;
      if (footer) {
        reserved = Math.max(GAP, viewportHeight - footer.getBoundingClientRect().top + GAP);
      }
      root.style.setProperty("--panel-top", Math.max(GAP, box.top) + "px");
      root.style.setProperty("--panel-bottom", reserved + "px");
      root.style.setProperty("--panel-right", Math.max(0, viewportWidth - box.right) + "px");
    }

    // Only measure while the panel is open — a closed panel has nothing to line up with.
    function onChange() { sync(); }
    // Captured on ``document``, not ``window``: that catches the page's own scroll *and* any
    // nested scroller the anchor sits inside (scroll events do not bubble), which a listener
    // bound to ``window`` alone can miss.
    var SCROLL_OPTS = { capture: true, passive: true };
    function watch() {
      if (watching) return;
      watching = true;
      sync();
      document.addEventListener("scroll", onChange, SCROLL_OPTS);
      window.addEventListener("resize", onChange);
    }
    function unwatch() {
      if (!watching) return;
      watching = false;
      document.removeEventListener("scroll", onChange, SCROLL_OPTS);
      window.removeEventListener("resize", onChange);
    }

    return { sync: sync, watch: watch, unwatch: unwatch };
  };

  /**
   * Wire the console pattern "full-width list + inset detail slide-in" in one call.
   *
   * Every CRUD console here has the same shape: a list card that owns the content width and a
   * detail panel that opens over its right for the selected row. This composes the two halves of
   * that — the dialog behaviour (:func:`mountSlideover`) and the geometry that keeps the panel
   * level with the list (:func:`mountInsetPanel`) — with the settings the pattern always wants:
   * non-modal (the rows behind stay clickable, so picking another record swaps the contents) and
   * unlocked (the page keeps its own scroll).
   *
   * The panel names the block it lines up with in ``data-anchor``.
   *
   * @param {string} id the ``.slideover.slideover-inset`` element's id
   * @param {{onOpen?:Function, onClose?:Function}} opts hooks for the page's own state
   * @returns {{open:Function, close:Function, isOpen:Function, panel:Element}|null}
   */
  ns.mountDetailSlideover = function mountDetailSlideover(id, opts) {
    opts = opts || {};
    var root = typeof id === "string" ? document.getElementById(id) : id;
    if (!root) return null;
    var anchor = document.querySelector(root.getAttribute("data-anchor"));
    // The rows that open the panel are usually inside the block it lines up with, but not always:
    // the invoices console lines its panel up with the filter bar and is opened from the list
    // below it. ``keepWithin`` names that region when the two differ.
    var keepWithin = opts.keepWithin ? document.querySelector(opts.keepWithin) : anchor;
    var geometry = ns.mountInsetPanel(root, anchor);
    var panel = root.querySelector(".slideover-panel");
    var chrome = ns.mountPanelChrome(panel, opts.fullLabel);

    var ctrl = ns.mountSlideover(root, {
      lockScroll: false,
      trapFocus: false,
      onOpen: function () {
        geometry.watch();
        // Defer arming the outside-click listener: the very click that opened the panel is still
        // propagating, and would otherwise close it again on the way up.
        window.setTimeout(function () { document.addEventListener("click", onDocClick, true); }, 0);
        if (opts.onOpen) opts.onOpen();
      },
      onClose: function () {
        geometry.unwatch();
        document.removeEventListener("click", onDocClick, true);
        if (opts.onClose) opts.onClose();
      },
    });

    /**
     * Dismiss on a click that isn't "in" the panel's world.
     *
     * Two things keep it open: a click inside the panel itself, and a click on a list row — the
     * rows are what *open* the panel, so picking another record swaps the contents rather than
     * closing and reopening. Everything else — the list's own chrome, the page around it — closes.
     */
    function onDocClick(event) {
      var target = event.target;
      if (panel.contains(target)) return;
      if (keepWithin && keepWithin.contains(target) && target.closest("tbody tr")) return;
      ctrl.close();
    }

    ctrl.setFullHref = chrome.setFullHref;
    return ctrl;
  };

  /**
   * Give a detail panel its standard chrome, so twelve consoles can't drift apart.
   *
   * Both ends of the panel get the same pair of controls — "Full view" (the record's own page) and
   * "Close" — because a panel tall enough to scroll strands whichever set it only has at the other
   * end. The head's pair is built into a cluster beside the title; the foot's is appended after the
   * record's own actions, separated by a spacer so save/delete stay on the left where they belong.
   * A panel whose records have no page hides the "Full view" controls (see ``setFullHref``).
   *
   * Exported because not every panel is a list's detail: the invoices console has its own
   * column-sliders, which need the same head and foot without the anchored geometry.
   *
   * @returns {{setFullHref: Function}} points the "Full view" controls at a record, or hides them
   */
  ns.mountPanelChrome = function mountPanelChrome(panel, fullLabel) {
    var label = fullLabel || "Full view";
    var links = [];

    function fullLink() {
      var a = document.createElement("a");
      a.className = "btn btn-quiet btn-sm";
      a.textContent = label;
      a.hidden = true;
      links.push(a);
      return a;
    }
    function closeButton(ariaLabel) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-quiet btn-sm";
      b.textContent = "Close";
      b.setAttribute("aria-label", ariaLabel);
      b.setAttribute("data-slideover-close", "");
      return b;
    }

    // Head: replace the template's lone Close with the pair, kept in one cluster.
    var head = panel.querySelector(".slideover-head");
    if (head) {
      var existing = head.querySelector("[data-slideover-close]");
      if (existing) existing.remove();
      var headActions = document.createElement("div");
      headActions.className = "slideover-actions";
      headActions.appendChild(fullLink());
      headActions.appendChild(closeButton("Close detail"));
      head.appendChild(headActions);
    }

    // Foot: reuse the record's action bar when it has one, else add a bar of our own.
    var foot = panel.querySelector(".slideover-foot");
    if (!foot) {
      foot = document.createElement("div");
      foot.className = "slideover-foot";
      panel.appendChild(foot);
    }
    var spacer = document.createElement("span");
    spacer.className = "spacer";
    foot.appendChild(spacer);
    var footActions = document.createElement("div");
    footActions.className = "slideover-actions";
    footActions.appendChild(fullLink());
    footActions.appendChild(closeButton("Close detail"));
    foot.appendChild(footActions);

    return {
      /** Point the panel's "Full view" controls at a record, or hide them when it has no page. */
      setFullHref: function setFullHref(href) {
        links.forEach(function (link) {
          if (href) {
            link.href = href;
            link.hidden = false;
          } else {
            link.hidden = true;
            link.removeAttribute("href");
          }
        });
      },
    };
  };

  // ── Document viewer ────────────────────────────────────────────────────────────

  var PREVIEWABLE = /\.(pdf|png|jpe?g|gif|webp|svg)$/i;
  var docViewer = null; // lazily-built singleton

  /** Append a query parameter to a URL, respecting any existing query string. */
  function withParam(url, key, value) {
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + key + "=" + encodeURIComponent(value);
  }

  /** Whether a filename looks like something the browser can render inline. */
  ns.isPreviewable = function isPreviewable(filename) {
    return PREVIEWABLE.test(String(filename || ""));
  };

  function buildDocViewer() {
    var root = document.createElement("div");
    root.className = "doc-modal";
    root.id = "bkp-doc-viewer";
    root.innerHTML =
      '<div class="doc-modal-backdrop" data-slideover-close></div>' +
      '<div class="doc-modal-panel" role="dialog" aria-modal="true" aria-labelledby="bkp-dv-title" tabindex="-1">' +
        '<div class="doc-modal-head">' +
          '<div class="doc-modal-titles"><h2 id="bkp-dv-title">Document</h2><p class="card-sub" id="bkp-dv-sub"></p></div>' +
          '<div class="doc-viewer-actions">' +
            '<a class="btn btn-quiet btn-sm" id="bkp-dv-download" rel="noopener">Download</a>' +
            '<button type="button" class="btn btn-quiet btn-sm" data-slideover-close aria-label="Close document viewer">Close</button>' +
          '</div>' +
        '</div>' +
        '<div class="doc-modal-body">' +
          '<iframe class="doc-frame" id="bkp-dv-frame" title="Document preview"></iframe>' +
          '<div class="doc-fallback" id="bkp-dv-fallback" hidden>' +
            '<p class="msg msg-muted">This file can’t be previewed here — download it to view.</p>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(root);
    var frame = root.querySelector("#bkp-dv-frame");
    // Release the document when the viewer closes so bytes aren't held open in the background.
    var ctrl = ns.mountSlideover(root, {
      panelSelector: ".doc-modal-panel",
      onClose: function () { frame.removeAttribute("src"); },
    });
    return { root: root, ctrl: ctrl, frame: frame };
  }

  /**
   * Preview a document inline in a reusable, centred modal, alongside a Download (Issue 99).
   *
   * Both actions use the **same** short-lived signed link: the viewer requests it with
   * ``disposition=inline`` (the browser's native PDF/image viewer renders it), and Download uses it
   * as-is (the route defaults to ``attachment``). No second auth path, same audit. A file the browser
   * can't render inline falls back to a Download prompt.
   *
   * @param {string} url the signed download URL (already carrying its ``?token=``)
   * @param {{filename?:string, subtitle?:string, previewable?:boolean, trigger?:Element}} opts
   */
  ns.openDocViewer = function openDocViewer(url, opts) {
    opts = opts || {};
    if (!docViewer) docViewer = buildDocViewer();
    var name = opts.filename || "Document";
    var previewable = opts.previewable != null ? opts.previewable : ns.isPreviewable(name);

    docViewer.root.querySelector("#bkp-dv-title").textContent = name;
    docViewer.root.querySelector("#bkp-dv-sub").textContent = opts.subtitle || "";
    var dl = docViewer.root.querySelector("#bkp-dv-download");
    dl.href = url;                       // attachment by default → saves the file
    dl.setAttribute("download", name);
    var fallback = docViewer.root.querySelector("#bkp-dv-fallback");

    if (previewable) {
      fallback.hidden = true;
      docViewer.frame.hidden = false;
      docViewer.frame.src = withParam(url, "disposition", "inline");
    } else {
      docViewer.frame.hidden = true;
      docViewer.frame.removeAttribute("src");
      fallback.hidden = false;
    }
    docViewer.ctrl.open(opts.trigger || null);
    return docViewer.ctrl;
  };

  // ── Row actions ─────────────────────────────────────────────────────────────────

  var EYE_SVG =
    '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4c-4 0-7 3.2-8 6 1 2.8 4 6 8 6s7-3.2 ' +
    '8-6c-1-2.8-4-6-8-6z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' +
    '<circle cx="10" cy="10" r="2.4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
  // Exposed so a console with no per-record page (e.g. the RBAC catalog's quick-view tree) can
  // build its own eye-icon trigger with the same glyph, without duplicating the markup.
  ns.EYE_SVG = EYE_SVG;

  /**
   * Build a per-row "view" eye link for the list actions column.
   *
   * One icon, one meaning: the eye opens the record's own page. Glancing at a record is what the
   * row click is for — it opens the detail slide-over beside the list — so the eye no longer
   * duplicates that as a quick view. Clicks don't bubble to the row's select handler, so following
   * the eye never also opens the panel behind the navigation.
   *
   * @param {string} href the record's full-detail page
   * @param {string} [label] accessible label / tooltip (default "View")
   */
  ns.eyeLink = function eyeLink(href, label) {
    var a = document.createElement("a");
    a.className = "btn btn-quiet btn-icon";
    a.href = href;
    a.setAttribute("aria-label", label || "View");
    a.title = label || "View";
    a.innerHTML = EYE_SVG;
    a.addEventListener("click", function (e) { e.stopPropagation(); });
    return a;
  };

  /** A trailing, right-aligned actions cell for a list row (holds the eye link). */
  ns.actionsCell = function actionsCell() {
    var cell = document.createElement("td");
    cell.className = "col-actions";
    // Don't let a click on the empty part of the cell select the row.
    cell.addEventListener("click", function (e) { e.stopPropagation(); });
    return cell;
  };

  /**
   * Read the caller's permission verdicts off a console's marker element (Issue #168, M28).
   *
   * A JS-rendered row cannot carry a Jinja ``{% if can(...) %}`` gate, so the server stamps its
   * verdicts onto one element as ``data-can-<name>="true|false"`` and the renderer reads them from
   * here instead of hand-rolling ``getAttribute`` at each site — which is what
   * ``admin-invoices.js`` and ``lease_templates.html`` had been doing, in three ad-hoc places, as
   * the only working pattern for this in the codebase.
   *
   * The verdicts are **UX only**, exactly like the template gates they stand in for: the route
   * re-checks every grant regardless, so a caller who edits the attribute in devtools gains a
   * button and a 403, never access.
   *
   * @param {Element|string} marker element, or the id of one
   * @returns {{can: function(string): boolean}} reader; unknown names are ``false`` (fail closed)
   */
  ns.verdicts = function verdicts(marker) {
    var el = typeof marker === "string" ? document.getElementById(marker) : marker;
    return {
      can: function (name) {
        if (!el) return false;
        return el.getAttribute("data-can-" + name) === "true";
      },
    };
  };

  /**
   * Drop a table column — header and every body cell — behind one verdict (Issue #168, M28).
   *
   * Column counts must stay aligned, so the ``<th>`` and the cells have to disappear together;
   * doing it in one helper is what stops the two drifting apart. Call it once after a render:
   * a ``<th data-col="actions">`` and every ``<td data-col="actions">`` vanish as a unit.
   *
   * @param {Element|string} table element, or the id of one
   * @param {string} column the ``data-col`` value to remove
   */
  ns.dropColumn = function dropColumn(table, column) {
    var el = typeof table === "string" ? document.getElementById(table) : table;
    if (!el) return;
    var cells = el.querySelectorAll('[data-col="' + column + '"]');
    for (var i = 0; i < cells.length; i += 1) {
      if (cells[i].parentNode) cells[i].parentNode.removeChild(cells[i]);
    }
  };

  /** Format an ISO timestamp for read-only display, or "—" when absent. */
  ns.formatDateTime = function formatDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
  };

})();
