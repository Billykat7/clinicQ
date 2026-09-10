/**
 * Lightweight autocomplete / combobox for admin forms.
 *
 * Declarative: any element like
 *   <div class="ac" data-ac-source="users">
 *     <input class="ac-input" ...>
 *     <input type="hidden" class="ac-value" id="owner_id">
 *     <ul class="ac-menu" hidden></ul>
 *   </div>
 * is upgraded on load. The visible input searches as you type; picking a result stores the
 * chosen row's **id** in the hidden ``.ac-value`` (so existing form JS keeps reading that id
 * unchanged) and shows a human label. A ``ac:select`` event (bubbling) carries the full row
 * for callers that want to prefill other fields (e.g. create-tenant-from-user).
 *
 * It behaves like a searchable dropdown, not just a type-ahead: a chevron toggle and a click
 * (focus) into the field both open the menu and *browse* the available options with an empty
 * query, so an admin never has to know what to type to see what exists — but can still filter
 * by typing. This is why required-id fields (unit, tenant, owner, user) use it instead of a
 * raw text box: you pick from real rows and can never mistype an id.
 *
 * Sources map to real search endpoints; add one here when the backend gains a new searchable
 * resource. No inline handlers — external file only (script-src 'self').
 */
(function () {
  "use strict";

  var SOURCES = {
    users: {
      url: function (q) {
        return "/api/v1/admin/rbac/users?q=" + encodeURIComponent(q) + "&limit=8";
      },
      map: function (it) { return { id: it.id, label: it.email, sub: it.role || "" }; },
    },
    tenants: {
      url: function (q) {
        return "/api/v1/tenants?q=" + encodeURIComponent(q) + "&limit=8";
      },
      map: function (it) {
        return { id: it.id, label: it.full_name || it.email, sub: it.email || "" };
      },
    },
    units: {
      url: function (q) {
        return "/api/v1/properties/units/search?q=" + encodeURIComponent(q) + "&limit=8";
      },
      map: function (it) {
        return {
          id: it.id,
          label: it.label + (it.property_name ? " — " + it.property_name : ""),
          sub: it.status || "",
        };
      },
    },
    vendors: {
      // Only active vendors are assignable to a new job, so the assignment picker browses
      // that set (a retired vendor keeps its history but never appears here). Backed by the
      // maintenance vendor directory search (Issue 83).
      url: function (q) {
        return "/api/v1/maintenance/vendors?q=" + encodeURIComponent(q) + "&active=true&limit=8";
      },
      map: function (it) {
        return { id: it.id, label: it.name, sub: it.trade || "" };
      },
    },
    "lease-templates": {
      // The active lease-document templates, picked when generating a document from a lease
      // (Issue 86). The list endpoint has no server-side ``q`` filter, so this browses the
      // active set (there is one in-force version per name, typically a handful). The chosen
      // id is the template NAME — what POST /leases/{id}/document takes as ``template_name``.
      url: function () {
        return "/api/v1/lease-templates?limit=100";
      },
      map: function (it) {
        return { id: it.name, label: it.name, sub: "v" + it.version };
      },
    },
    properties: {
      // The property-scope picker on the RBAC user-detail assignment editor (Issue #147) — a
      // manager/agent's UserRoleAssignment(scope_type='property') scope_id is a real property id,
      // not a free-text guess.
      url: function (q) {
        return "/api/v1/properties?q=" + encodeURIComponent(q) + "&limit=8";
      },
      map: function (it) {
        return { id: it.id, label: it.name, sub: it.address ? it.address.city : "" };
      },
    },
    "rbac-resources": {
      // The catalog's own resource tree (Issue #141 follow-up): picking a parent/resource key by
      // browsing real rows instead of retyping a dotted key freehand, which the catalog API 422s on
      // any mismatch. No server-side ``q`` filter (same as lease-templates below) — the tree is
      // small, so this browses the full set; the chosen "id" is the resource's ``key``, not a db id.
      url: function () {
        return "/api/v1/admin/rbac/resources";
      },
      map: function (it) {
        return { id: it.key, label: it.key, sub: it.name || "" };
      },
    },
    "rbac-actions": {
      // The catalog's action list, same rationale as rbac-resources above.
      url: function () {
        return "/api/v1/admin/rbac/actions";
      },
      map: function (it) {
        return { id: it.key, label: it.key, sub: it.name || "" };
      },
    },
    "rbac-nav-surfaces": {
      // Every surface with a known gate (Issue #146) — re-gating one starts from picking a real
      // surface key rather than guessing what a rail icon or console tab is called internally.
      url: function () {
        return "/api/v1/admin/rbac/nav-gates";
      },
      map: function (it) {
        return { id: it.surface_key, label: it.surface_key, sub: it.label || "" };
      },
    },
  };

  function debounce(fn, ms) {
    var t;
    return function () {
      var self = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function attach(root) {
    var source = SOURCES[root.getAttribute("data-ac-source")];
    var input = root.querySelector(".ac-input");
    var value = root.querySelector(".ac-value");
    var menu = root.querySelector(".ac-menu");
    if (!source || !input || !value || !menu) return;

    // Chevron toggle — injected so every existing widget gains the dropdown affordance
    // without touching each template. Clicking it browses all options.
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "ac-toggle";
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-label", "Show options");
    toggle.innerHTML =
      '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 7.5 10 12l4.5-4.5" ' +
      'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg>';
    root.appendChild(toggle);

    var items = [], active = -1, seq = 0;

    function close() {
      menu.hidden = true; menu.innerHTML = ""; items = []; active = -1;
      input.setAttribute("aria-expanded", "false");
      root.classList.remove("ac-open");
    }
    function open() {
      menu.hidden = false;
      input.setAttribute("aria-expanded", "true");
      root.classList.add("ac-open");
    }

    function pick(i) {
      var it = items[i];
      if (!it) return;
      value.value = it.id;
      input.value = it.label;
      root.dispatchEvent(new CustomEvent("ac:select", { bubbles: true, detail: { id: it.id, item: it.raw } }));
      close();
    }

    function render() {
      menu.innerHTML = "";
      if (!items.length) {
        var empty = document.createElement("li");
        empty.className = "ac-empty";
        empty.textContent = "No matches";
        menu.appendChild(empty);
        open();
        return;
      }
      items.forEach(function (it, i) {
        var li = document.createElement("li");
        li.className = "ac-opt" + (i === active ? " active" : "");
        li.setAttribute("role", "option");
        var lab = document.createElement("span");
        lab.className = "ac-lab"; lab.textContent = it.label;
        li.appendChild(lab);
        if (it.sub) {
          var s = document.createElement("span");
          s.className = "ac-sub"; s.textContent = it.sub;
          li.appendChild(s);
        }
        li.addEventListener("mousedown", function (e) { e.preventDefault(); pick(i); });
        menu.appendChild(li);
      });
      open();
    }

    // Fetch results for a query (may be empty, which browses the first page of options).
    function fetchResults(q) {
      var mine = ++seq;
      fetch(source.url(q), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (mine !== seq) return; // a newer request already superseded this
          var arr = (d && d.items) || [];
          items = arr.map(function (it) { var m = source.map(it); m.raw = it; return m; });
          active = -1;
          render();
        })
        .catch(function () { close(); });
    }

    var search = debounce(function () {
      value.value = "";            // typing invalidates any prior selection
      fetchResults(input.value.trim());
    }, 220);

    input.addEventListener("input", search);
    // Focusing the field opens it and shows options (filtered by any text already there).
    input.addEventListener("focus", function () {
      if (menu.hidden) fetchResults(input.value.trim());
    });
    // The chevron browses *all* options (empty query), and toggles closed when open.
    toggle.addEventListener("mousedown", function (e) {
      e.preventDefault();
      if (!menu.hidden) { close(); return; }
      input.focus();
      fetchResults("");
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && menu.hidden) { e.preventDefault(); fetchResults(input.value.trim()); return; }
      if (menu.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(active); }
      else if (e.key === "Escape") { close(); }
    });
    input.addEventListener("blur", function () { setTimeout(close, 120); });

    // Per-widget handle, reachable via the hidden value element (what form JS already holds).
    value._ac = {
      clear: function () { input.value = ""; value.value = ""; close(); },
      set: function (id, label) { value.value = id || ""; input.value = label || ""; },
    };
  }

  var api = (window.BKPAC = window.BKPAC || {});
  api.clear = function (valueEl) { if (valueEl && valueEl._ac) valueEl._ac.clear(); };
  api.set = function (valueEl, id, label) { if (valueEl && valueEl._ac) valueEl._ac.set(id, label); };
  // Wire up a ``.ac[data-ac-source]`` root built after load (e.g. an inline table-row editor) —
  // the DOMContentLoaded sweep below only covers what's in the template at first render.
  api.attach = attach;

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".ac[data-ac-source]"), attach);
  });
})();
