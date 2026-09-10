/**
 * RBAC admin behaviour: roles, permission matrix, user role assignment (Issue 9).
 *
 * Talks to /api/v1/admin/rbac:
 *   - GET    /roles?q=&system_filter=&limit=       → list roles
 *   - POST   /roles                                → create a custom role
 *   - PATCH  /roles/{name}                         → update description
 *   - DELETE /roles/{name}                         → delete a custom role
 *   - GET    /roles/{name}/permissions             → permission matrix
 *   - PUT    /roles/{name}/permissions             → replace matrix
 *   - GET    /roles/{name}/policy                  → the same grants as one JSON document (#161)
 *   - PUT    /roles/{name}/policy                  → apply a JSON document (create/update/revoke)
 *   - GET    /users?q=&role=&limit=                → list users
 *   - GET    /users/{id}/roles                     → current role
 *   - PUT    /users/{id}/roles                     → assign role
 *
 * The page re-checks RBAC server-side; this is the UI only. No inline handlers.
 */
(function () {
  "use strict";

  var API = "/api/v1/admin/rbac";
  var VERBS = ["read", "create", "update", "delete"];

  // The scope-tier ladder, narrowest first (Issues #156/#171), labelled the way an operator thinks
  // about it rather than by the enum value. The stored value is still the enum — these strings never
  // travel to the API — because "whole business" is what an admin is deciding, not "business".
  var SCOPES = [
    { value: "own", label: "their own rows" },
    { value: "assigned", label: "assigned properties" },
    { value: "business", label: "whole business" }
  ];

  /** Operator-language label for a scope tier value ("" for an unknown/absent one). */
  function scopeLabel(value) {
    for (var i = 0; i < SCOPES.length; i += 1) {
      if (SCOPES[i].value === value) return SCOPES[i].label;
    }
    return value || "";
  }
  // Grant-usage telemetry (Issue #176, M29). Advisory data for pruning — never an audit trail, and
  // the wording below is deliberate about that: "no recorded use", not "never used", because a
  // young collection window and a dead grant look identical and only one of them is a finding.
  var usageState = { enabled: false, since: null, unusedDays: 90 };

  /** Days between ``iso`` and now, or null when there is no timestamp. */
  function daysSince(iso) {
    if (!iso) return null;
    var then = new Date(iso);
    if (isNaN(then.getTime())) return null;
    return Math.floor((Date.now() - then.getTime()) / 86400000);
  }

  /** How a grant's last use reads in the matrix cell. */
  function usageLabel(row) {
    if (!usageState.enabled) return "—";
    if (!row.max_verb && !row.effective_max_verb) return "";
    var days = daysSince(row.last_used_at);
    if (days === null) return "no recorded use";
    if (days <= 0) return "used today";
    if (days === 1) return "used yesterday";
    return "used " + days + " days ago";
  }

  /** True when this row is a grant the pruning worklist should offer up. */
  function isUnusedGrant(row) {
    if (!usageState.enabled || !row.max_verb) return false;
    var days = daysSince(row.last_used_at);
    return days === null || days >= usageState.unusedDays;
  }

  var PAGE_LIMIT = 100;
  var PAGE_SIZES = [10, 20, 50, 100];

  /** A page size restored from the URL, only if it is one the pager offers (else its default). */
  function pageSizeFromQuery(raw) {
    var n = parseInt(raw, 10);
    return PAGE_SIZES.indexOf(n) >= 0 ? n : undefined;
  }

  /** Summarise a bulk-delete response for the list message, naming skipped rows' reasons. */
  function bulkResultText(data, noun) {
    var deleted = data.deleted_count || 0;
    var failed = data.failed_count || 0;
    var text = deleted + " " + (deleted === 1 ? noun : noun + "s") + " deleted";
    if (failed) {
      var reasons = [];
      (data.results || []).forEach(function (row) {
        if (!row.deleted && row.detail && reasons.indexOf(row.detail) < 0) reasons.push(row.detail);
      });
      text += ", " + failed + " skipped" + (reasons.length ? " (" + reasons.join("; ") + ")" : "");
    }
    return text;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tabs = setupTabs();
    var roles = setupRolesTab(tabs);
    var perms = setupPermissionsTab();
    var users = setupUsersTab();
    var audit = setupAuditTab();
    var catalog = setupCatalogTab();

    // Cross-tab refresh hooks.
    tabs.onShow(function (name) {
      if (name === "permissions") perms.refresh();
      if (name === "users") users.refresh();
      if (name === "audit") audit.refresh();
      if (name === "catalog") catalog.refresh();
    });
    roles.onChange(function () { perms.reloadRoleOptions(); users.reloadRoleOptions(); });

    var initial = document.getElementById("rbac-initial-tab");
    tabs.show(initial ? initial.getAttribute("data-tab") || "roles" : "roles");
  });

  // ── HTTP helpers ───────────────────────────────────────────────────────────

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }
  function getJson(path) { return fetch(API + path, { credentials: "same-origin" }).then(readResponse); }
  function writeJson(method, path, body) {
    return fetch(API + path, {
      method: method,
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(readResponse);
  }
  function errorText(r, fallback) {
    var d = r && r.data;
    if (d && typeof d.detail === "string") return d.detail;
    if (d && Array.isArray(d.detail) && d.detail.length && d.detail[0].msg) return d.detail[0].msg;
    return fallback;
  }
  function setMsg(el, text, kind) { if (el) { el.textContent = text || ""; el.className = "msg" + (kind ? " msg-" + kind : ""); } }
  function debounce(fn, ms) {
    var t;
    return function () { var self = this, args = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(self, args); }, ms); };
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────

  // Each section is now its own URL, so the tabs are plain links (they navigate on click). This
  // only reveals the active section's panel on load and notifies listeners so its data loads.
  function setupTabs() {
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
    return { show: show, onShow: function (fn) { listeners.push(fn); } };
  }

  // ── Roles tab ──────────────────────────────────────────────────────────────

  function setupRolesTab() {
    var search = document.getElementById("roles-search");
    var systemFilter = document.getElementById("roles-system-filter");
    var body = document.getElementById("roles-body");
    var msg = document.getElementById("roles-msg");
    var newBtn = document.getElementById("new-role-btn");

    // A slide-in over the full-width list (Issue #132), not a second column beside it.
    var detail = window.BKPAdmin.mountDetailSlideover("role-detail", {
      onClose: function () { selected = null; window.BKPAdmin.selectRow(body, null); },
    });
    var rdName = document.getElementById("rd-name");
    var rdSystem = document.getElementById("rd-system");
    var rdDescription = document.getElementById("rd-description");
    var rdSave = document.getElementById("rd-save");
    var rdEditPerms = document.getElementById("rd-edit-perms");
    var rdDelete = document.getElementById("rd-delete");
    var rdMsg = document.getElementById("rd-msg");
    var rdInheritsList = document.getElementById("rd-inherits-list");
    var rdInheritsSelect = document.getElementById("rd-inherits-select");
    var rdInheritsAdd = document.getElementById("rd-inherits-add");
    var rdInheritsMsg = document.getElementById("rd-inherits-msg");
    var rdAccessList = document.getElementById("rd-access-list");

    var newCard = document.getElementById("new-role-card");
    var nrName = document.getElementById("nr-name");
    var nrDescription = document.getElementById("nr-description");
    var nrCreate = document.getElementById("nr-create");
    var nrCancel = document.getElementById("nr-cancel");
    var nrMsg = document.getElementById("nr-msg");

    var clearBtn = document.getElementById("roles-clear");
    var selected = null;
    var changeListeners = [];
    function fireChange() { changeListeners.forEach(function (fn) { fn(); }); }

    // Restore search + filter + page from the URL query so a filtered view is linkable and
    // survives a refresh (Issue 114). The system filter is a data-combobox, whose display syncs
    // itself when we set the underlying select's value.
    var q0 = window.BKPAdmin.readQuery();
    if (q0.q) search.value = q0.q;
    if (q0.system_filter) systemFilter.value = q0.system_filter;
    var pager = window.BKPAdmin.createPager({
      onChange: function () { load(); },
      size: pageSizeFromQuery(q0.limit),
    });
    pager.mount(document.getElementById("roles-pager"));
    pager.offset = Math.max(0, parseInt(q0.offset, 10) || 0);

    var rolesTable = body.closest("table");

    // Click-to-sort headers (Issue 115), restored from the URL and composed with filter + page.
    var sortState = { sort: q0.sort || null, order: q0.order || null };
    window.BKPAdmin.mountSortableHeaders(rolesTable, {
      initial: sortState,
      onSort: function (sort, order) {
        sortState.sort = sort;
        sortState.order = order;
        reload(); // a sort change resets to page 1
      },
    });

    // Row selection + single bulk-delete (Issue 116). Only present when the caller holds the
    // DELETE grant (the button + checkbox column are gated server-side in the template).
    var bulkBtn = document.getElementById("roles-bulk-delete");
    var bulk = bulkBtn
      ? window.BKPAdmin.mountBulkDelete({
          table: rolesTable,
          deleteBtn: bulkBtn,
          countEl: document.getElementById("roles-selected"),
          noun: "role",
          onDelete: bulkDelete,
        })
      : null;
    var canBulk = !!rolesTable.querySelector("thead .select-all");

    /** True when a search term or a non-default filter is applied (drives the empty-state copy). */
    function hasActiveFilters() {
      return !!(search.value.trim() || (systemFilter.value && systemFilter.value !== "all"));
    }

    function bulkDelete(ids) {
      setMsg(msg, "Deleting…", "muted");
      writeJson("POST", "/roles/bulk-delete", { ids: ids }).then(function (r) {
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not delete the selection."), "error");
        setMsg(msg, bulkResultText(r.data, "role"), r.data.failed_count ? "muted" : "ok");
        // A deleted role may have been open in the detail panel — close it.
        detail.close();
        reload();
        fireChange();
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    }

    function load() {
      var params = new URLSearchParams();
      if (search.value.trim()) params.set("q", search.value.trim());
      if (systemFilter.value && systemFilter.value !== "all") {
        params.set("system_filter", systemFilter.value);
      }
      if (sortState.sort) {
        params.set("sort", sortState.sort);
        if (sortState.order) params.set("order", sortState.order);
      }
      pager.applyTo(params);
      window.BKPAdmin.writeQuery(params); // reflect the exact request in the address bar
      if (clearBtn) clearBtn.hidden = !hasActiveFilters();
      setMsg(msg, "Loading…", "muted");
      getJson("/roles?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not load roles."), "error");
        render(r.data.items || [], r.data.total || 0);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    }
    // A new search/filter restarts paging from the first page.
    function reload() { pager.reset(); load(); }

    function clearFilters() {
      search.value = "";
      systemFilter.value = "all";
      reload();
    }

    function render(items, total) {
      body.innerHTML = "";
      if (!items.length) {
        // Distinguish "nothing here yet" from "your filter matched nothing" (Issue 114).
        setMsg(msg, hasActiveFilters() ? "No roles match your filters." : "No roles yet.", "muted");
        if (bulk) bulk.bind(); // reset the selection count / disable the Delete button
        return;
      }
      setMsg(msg, "");
      items.forEach(function (role) {
        var tr = document.createElement("tr");
        if (canBulk) tr.appendChild(window.BKPAdmin.selectionCell(role.name));
        var nameCell = document.createElement("td");
        nameCell.textContent = role.name;
        if (role.is_system) {
          var badge = document.createElement("span");
          badge.className = "badge badge-muted";
          badge.textContent = "system";
          badge.style.marginLeft = ".4rem";
          nameCell.appendChild(badge);
        }
        tr.appendChild(nameCell);
        tr.appendChild(td(String(role.user_count)));
        tr.appendChild(td(String(role.permission_count)));
        var actions = window.BKPAdmin.actionsCell();
        actions.appendChild(window.BKPAdmin.eyeLink("/admin/rbac/roles/" + encodeURIComponent(role.name), "View role"));
        tr.appendChild(actions);
        tr.addEventListener("click", function () {
          Array.prototype.forEach.call(body.children, function (row) { row.classList.remove("selected"); });
          tr.classList.add("selected");
          select(role);
        });
        body.appendChild(tr);
      });
      if (bulk) bulk.bind(); // (re)bind the freshly built row checkboxes
    }

    function select(role) {
      selected = role;
      detail.setFullHref("/admin/rbac/roles/" + encodeURIComponent(role.name));
      detail.open();
      rdName.textContent = role.name;
      rdSystem.textContent = role.is_system ? "Built-in system role." : "Custom role.";
      rdDescription.value = role.description || "";
      rdDescription.disabled = false;
      if (rdDelete) rdDelete.hidden = role.is_system;
      setMsg(rdMsg, "");
      loadInherits(role.name);
      loadEffectiveAccess(role.name);
    }

    /**
     * Fill the quick view's "Effective access" list (Issue #173).
     *
     * Reads the same ``/permissions`` matrix the grid renders — one source, two presentations —
     * and keeps only the resources the role actually reaches, so the panel answers "what can this
     * role do" without the operator scrolling a catalog of blanks. Both axes of each grant are
     * shown, because the verb alone has never been the whole answer.
     */
    function loadEffectiveAccess(roleName) {
      if (!rdAccessList) return;
      rdAccessList.innerHTML = "";
      getJson("/roles/" + encodeURIComponent(roleName) + "/permissions").then(function (r) {
        if (!r.ok || !r.data) return;
        var reached = (r.data.permissions || []).filter(function (row) {
          return !!row.effective_max_verb;
        });
        if (!reached.length) {
          var empty = document.createElement("li");
          empty.className = "muted";
          empty.textContent = "Reaches nothing yet — no grant on any resource.";
          rdAccessList.appendChild(empty);
          return;
        }
        reached.forEach(function (row) {
          var li = document.createElement("li");
          var name = document.createElement("span");
          name.textContent = row.resource;
          li.appendChild(name);
          var value = document.createElement("span");
          value.className = "access-value";
          value.textContent = row.effective_max_verb.toUpperCase();
          var tier = document.createElement("span");
          tier.className = "scope-tag";
          tier.textContent = scopeLabel(row.effective_scope);
          value.appendChild(document.createTextNode(" "));
          value.appendChild(tier);
          li.appendChild(value);
          rdAccessList.appendChild(li);
        });
      });
    }

    // Role inheritance editor (Issue #135): show the roles this one inherits and let the operator
    // add/remove edges. The union over the closure resolves with deny-beats-allow.
    function loadInherits(roleName) {
      setMsg(rdInheritsMsg, "");
      rdInheritsList.innerHTML = "";
      Promise.all([
        getJson("/roles/" + encodeURIComponent(roleName) + "/inherits"),
        getJson("/roles?limit=100"),
      ]).then(function (results) {
        var inh = (results[0].ok && results[0].data && results[0].data.inherits) || [];
        var all = (results[1].ok && results[1].data && results[1].data.items) || [];
        renderInherits(roleName, inh, all);
      });
    }

    function renderInherits(roleName, inherits, allRoles) {
      rdInheritsList.innerHTML = "";
      if (!inherits.length) {
        var empty = document.createElement("li");
        empty.className = "muted";
        empty.textContent = "Inherits from no other role.";
        rdInheritsList.appendChild(empty);
      }
      inherits.forEach(function (name) {
        var li = document.createElement("li");
        var label = document.createElement("span");
        label.textContent = name;
        li.appendChild(label);
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn-quiet btn-sm";
        remove.textContent = "Remove";
        remove.addEventListener("click", function () {
          setMsg(rdInheritsMsg, "Removing…", "muted");
          writeJson("DELETE", "/roles/" + encodeURIComponent(roleName) + "/inherits/" + encodeURIComponent(name))
            .then(function (r) {
              if (!r.ok) return setMsg(rdInheritsMsg, errorText(r, "Could not remove."), "error");
              setMsg(rdInheritsMsg, "");
              loadInherits(roleName);
            }).catch(function () { setMsg(rdInheritsMsg, "Network error.", "error"); });
        });
        li.appendChild(remove);
        rdInheritsList.appendChild(li);
      });
      // Candidates: every role except this one and the ones already inherited.
      rdInheritsSelect.innerHTML = "";
      allRoles.map(function (r) { return r.name; })
        .filter(function (n) { return n !== roleName && inherits.indexOf(n) < 0; })
        .forEach(function (n) {
          var opt = document.createElement("option");
          opt.value = n; opt.textContent = n;
          rdInheritsSelect.appendChild(opt);
        });
    }

    rdInheritsAdd.addEventListener("click", function () {
      if (!selected) return;
      var target = rdInheritsSelect.value;
      if (!target) return;
      setMsg(rdInheritsMsg, "Adding…", "muted");
      writeJson("POST", "/roles/" + encodeURIComponent(selected.name) + "/inherits", { inherits_role: target })
        .then(function (r) {
          if (!r.ok) return setMsg(rdInheritsMsg, errorText(r, "Could not add."), "error");
          setMsg(rdInheritsMsg, "");
          loadInherits(selected.name);
        }).catch(function () { setMsg(rdInheritsMsg, "Network error.", "error"); });
    });

    rdSave.addEventListener("click", function () {
      if (!selected) return;
      setMsg(rdMsg, "Saving…", "muted");
      writeJson("PATCH", "/roles/" + encodeURIComponent(selected.name), { description: rdDescription.value.trim() || null })
        .then(function (r) {
          if (!r.ok) return setMsg(rdMsg, errorText(r, "Could not save."), "error");
          setMsg(rdMsg, "Saved.", "ok");
          load();
        }).catch(function () { setMsg(rdMsg, "Network error.", "error"); });
    });

    // rd-delete only renders when the caller holds rbac:delete (Issue #155) — guard the listener
    // the same way admin-properties.js/admin-vendors.js already guard their own gated delete
    // buttons, so a caller without the grant doesn't crash the whole tab's setup.
    if (rdDelete) rdDelete.addEventListener("click", function () {
      if (!selected) return;
      if (!window.confirm("Delete role \"" + selected.name + "\"? This cannot be undone.")) return;
      setMsg(rdMsg, "Deleting…", "muted");
      writeJson("DELETE", "/roles/" + encodeURIComponent(selected.name)).then(function (r) {
        if (!r.ok) return setMsg(rdMsg, errorText(r, "Could not delete."), "error");
        detail.close();
        load();
        fireChange();
      }).catch(function () { setMsg(rdMsg, "Network error.", "error"); });
    });

    rdEditPerms.addEventListener("click", function () {
      if (!selected) return;
      var evt = new CustomEvent("bkp:edit-permissions", { detail: { role: selected.name } });
      document.dispatchEvent(evt);
    });

    // new-role-btn/new-role-card only render when the caller holds rbac:create (Issue #155) —
    // same guard shape as admin-vendors.js's "New vendor" wiring.
    if (newBtn && nrCreate) {
    newBtn.addEventListener("click", function () {
      // The create card opens under the full-width list, where the detail panel would cover it.
      detail.close();
      newCard.hidden = !newCard.hidden;
      nrName.focus();
    });
    nrCancel.addEventListener("click", function () { newCard.hidden = true; nrName.value = ""; nrDescription.value = ""; setMsg(nrMsg, ""); });
    nrCreate.addEventListener("click", function () {
      var name = nrName.value.trim();
      if (!name) return setMsg(nrMsg, "Enter a role name.", "error");
      setMsg(nrMsg, "Creating…", "muted");
      writeJson("POST", "/roles", { name: name, description: nrDescription.value.trim() || null }).then(function (r) {
        if (!r.ok) return setMsg(nrMsg, errorText(r, "Could not create the role."), "error");
        newCard.hidden = true; nrName.value = ""; nrDescription.value = "";
        setMsg(nrMsg, "");
        load();
        fireChange();
      }).catch(function () { setMsg(nrMsg, "Network error.", "error"); });
    });
    }

    search.addEventListener("input", debounce(reload, 250));
    systemFilter.addEventListener("change", reload);
    if (clearBtn) clearBtn.addEventListener("click", clearFilters);
    load();

    return { onChange: function (fn) { changeListeners.push(fn); } };
  }

  // ── Permissions tab ─────────────────────────────────────────────────────────

  function setupPermissionsTab() {
    var roleSelect = document.getElementById("perm-role");
    var search = document.getElementById("perm-search");
    var stateFilter = document.getElementById("perm-state-filter");
    var clearBtn = document.getElementById("perm-clear");
    var loading = document.getElementById("perm-loading");
    var body = document.getElementById("perm-body");
    var saveBtn = document.getElementById("perm-save");
    var msg = document.getElementById("perm-msg");
    var loadedOptions = false;

    /** True when a resource search term or a non-default grant-state filter is applied. */
    function hasActiveFilters() {
      return !!(search.value.trim() || (stateFilter.value && stateFilter.value !== "all"));
    }

    /** A resource row's grant state, matching the state-filter options. */
    function rowState(row) {
      if (row.max_verb) return "granted";
      if (row.effective_inherited && row.effective_max_verb) return "inherited";
      return "none";
    }

    /** Whether a single matrix row matches the current search term + grant-state filter. */
    function rowMatches(row) {
      var term = search.value.trim().toLowerCase();
      if (term && row.resource.toLowerCase().indexOf(term) < 0) return false;
      var want = stateFilter.value;
      // The pruning worklist (Issue #176) is a state of its own rather than a rowState() value:
      // it is a question about *usage*, orthogonal to whether the grant is held here or inherited.
      if (want === "unused") return isUnusedGrant(row);
      if (want && want !== "all" && rowState(row) !== want) return false;
      return true;
    }

    /** Expand or collapse every module group (used so a filter reveals matching sub-sections). */
    function setAllGroupsExpanded(open) {
      Array.prototype.forEach.call(body.querySelectorAll(".group-toggle"), function (toggle) {
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.textContent = (open ? "▾ " : "▸ ") + toggle.getAttribute("data-resource");
      });
      Array.prototype.forEach.call(body.querySelectorAll("tr.group-child"), function (childRow) {
        childRow.hidden = !open;
      });
    }

    // Client-side filter over the loaded matrix (Issue #119). The matrix is a fixed per-role set,
    // so narrowing it needs no server round-trip. A group stays visible when it or any of its
    // sub-sections match; an active filter expands groups so matching children are revealed.
    function applyFilter() {
      var active = hasActiveFilters();
      if (clearBtn) clearBtn.hidden = !active;
      var rows = Array.prototype.slice.call(body.children);
      if (!rows.length) return;
      if (active) setAllGroupsExpanded(true);
      else setAllGroupsExpanded(false);

      var direct = {};
      rows.forEach(function (tr) { direct[tr._permResource] = rowMatches(tr._permRow); });

      var anyVisible = false;
      rows.forEach(function (tr) {
        var show;
        if (tr._permIsGroup) {
          show = direct[tr._permResource] || rows.some(function (o) {
            return o._permParent === tr._permResource && direct[o._permResource];
          });
        } else {
          show = direct[tr._permResource];
        }
        tr.classList.toggle("filter-hidden", !show);
        if (show) anyVisible = true;
      });

      if (active) setMsg(msg, anyVisible ? "" : "No resources match your filters.", "muted");
    }

    function reloadRoleOptions() {
      return getJson("/roles?limit=" + PAGE_LIMIT).then(function (r) {
        if (!r.ok || !r.data) return;
        var current = roleSelect.value;
        roleSelect.innerHTML = "";
        (r.data.items || []).forEach(function (role) {
          var opt = document.createElement("option");
          opt.value = role.name; opt.textContent = role.name;
          roleSelect.appendChild(opt);
        });
        if (current) roleSelect.value = current;
        loadedOptions = true;
      });
    }

    /**
     * Take the usage window off the matrix payload and label the worklist filter from it.
     *
     * Everything here comes from the server: whether collection is on, when it started, and the
     * threshold in force. Nothing is hardcoded, so a deployment that changed
     * ``PERMISSION_USAGE_UNUSED_DAYS`` sees its own number, and one that turned collection off sees
     * the option disabled rather than a matrix that reads "never used" from top to bottom.
     */
    function applyUsageState(data) {
      usageState.enabled = !!data.usage_enabled;
      usageState.since = data.usage_since || null;
      usageState.unusedDays = data.usage_unused_days || 90;
      var option = document.getElementById("perm-state-unused");
      if (option) {
        option.textContent = "Granted, never used in " + usageState.unusedDays + " days";
        option.disabled = !usageState.enabled;
      }
      var caveat = document.getElementById("perm-usage-since");
      if (!caveat) return;
      if (!usageState.enabled) {
        caveat.hidden = false;
        caveat.textContent = "Usage collection is off, so last-used is not recorded.";
        return;
      }
      if (!usageState.since) {
        caveat.hidden = false;
        caveat.textContent = "No usage recorded yet — every grant will read as unused.";
        return;
      }
      // The caveat that keeps a young window from being misread as evidence of disuse: usage is
      // only recorded since the feature shipped here, so "never used" means "not since this date".
      caveat.hidden = false;
      caveat.textContent =
        "Usage recorded since " + new Date(usageState.since).toLocaleDateString() + ".";
    }

    function loadMatrix() {
      var role = roleSelect.value;
      if (!role) { body.innerHTML = ""; return; }
      loading.hidden = false;
      setMsg(msg, "");
      getJson("/roles/" + encodeURIComponent(role) + "/permissions").then(function (r) {
        loading.hidden = true;
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not load permissions."), "error");
        applyUsageState(r.data);
        render(r.data.permissions || []);
      }).catch(function () { loading.hidden = true; setMsg(msg, "Network error.", "error"); });
    }

    // Build the grant <select> for one resource row (shared by group headers and sub-sections).
    function verbSelect(row) {
      var select = document.createElement("select");
      select.setAttribute("data-resource", row.resource);
      var none = document.createElement("option");
      none.value = ""; none.textContent = "— none —";
      select.appendChild(none);
      VERBS.forEach(function (v) {
        var opt = document.createElement("option");
        opt.value = v; opt.textContent = v;
        select.appendChild(opt);
      });
      select.value = row.max_verb || "";
      return select;
    }

    // The ALLOW/DENY effect selector (Issue #134). Paired with the verb select on the same cell;
    // DENY removes this verb and every higher verb and beats any inherited ALLOW. Disabled while
    // the cell grants nothing (effect is meaningless without a verb).
    function effectSelect(row, verbSel) {
      var select = document.createElement("select");
      select.className = "effect-select";
      select.setAttribute("data-effect-for", row.resource);
      [["allow", "allow"], ["deny", "deny"]].forEach(function (pair) {
        var opt = document.createElement("option");
        opt.value = pair[0]; opt.textContent = pair[1];
        select.appendChild(opt);
      });
      select.value = (row.effect === "deny") ? "deny" : "allow";
      function sync() {
        var hasVerb = !!verbSel.value;
        select.disabled = !hasVerb;
        // A DENY row is visually distinct so an operator can spot a carve-out at a glance.
        var tr = verbSel.closest("tr");
        if (tr) tr.classList.toggle("deny", hasVerb && select.value === "deny");
      }
      verbSel.addEventListener("change", sync);
      select.addEventListener("change", sync);
      sync();
      return select;
    }

    // The scope-tier selector (Issue #173). The third field of the same grant, paired with verb and
    // effect on the same cell exactly as the effect selector was (Issue #134) — and disabled while
    // the cell grants nothing, for the same reason: a tier is meaningless without a verb.
    function scopeSelect(row, verbSel) {
      var select = document.createElement("select");
      select.className = "scope-select";
      select.setAttribute("data-scope-for", row.resource);
      select.setAttribute("aria-label", "How wide the " + row.resource + " grant reaches");
      SCOPES.forEach(function (tier) {
        var opt = document.createElement("option");
        opt.value = tier.value; opt.textContent = tier.label;
        select.appendChild(opt);
      });
      // A cell with no row of its own shows the tier it would *inherit*, so the control never
      // implies a narrower grant than the caller actually resolves.
      select.value = row.scope || row.effective_scope || "own";
      function sync() { select.disabled = !verbSel.value; }
      verbSel.addEventListener("change", sync);
      sync();
      return select;
    }

    function effectiveCell(row) {
      var effective = document.createElement("td");
      var verb;
      // A verb changed by an inherited role (role_hierarchy) is called out distinctly from one
      // inherited via the resource tree (Issue #135).
      if (row.effective_via_role) {
        effective.className = "inherited via-role";
        verb = (row.effective_max_verb || "—") + " (via role)";
      } else if (row.effective_inherited && !row.max_verb) {
        effective.className = "inherited";
        verb = (row.effective_max_verb || "—") + " (inherited)";
      } else {
        verb = row.effective_max_verb || "—";
      }
      effective.textContent = verb;
      // Both axes of the grant, not just the verb (Issue #173): a role holding READ on properties
      // reaches three of them or all four hundred depending on this, and the one screen whose job
      // is answering "what can this role do" used to omit it.
      if (row.effective_max_verb) {
        var tier = document.createElement("span");
        tier.className = "scope-tag";
        tier.textContent = scopeLabel(row.effective_scope);
        effective.appendChild(document.createTextNode(" "));
        effective.appendChild(tier);
      }
      return effective;
    }

    /**
     * Order the matrix so every sub-section sits directly under the resource it belongs to.
     *
     * The API returns the rows in its own order, which for most groups puts the sub-sections after
     * *all* the parents — so expanding a group revealed its sections at the bottom of the table,
     * nowhere near the row that opened them. Placing each child with its parent here makes every
     * group behave the way the ones that happened to be adjacent already did.
     */
    function groupedRows(rows) {
      var childrenOf = {};
      rows.forEach(function (r) {
        if (!r.parent_resource) return;
        (childrenOf[r.parent_resource] = childrenOf[r.parent_resource] || []).push(r);
      });
      var out = [];
      rows.forEach(function (r) {
        if (r.parent_resource) return; // placed with its parent, below
        out.push(r);
        (childrenOf[r.resource] || []).forEach(function (child) { out.push(child); });
      });
      // A child whose parent the matrix doesn't list still has to render somewhere.
      rows.forEach(function (r) {
        if (r.parent_resource && out.indexOf(r) < 0) out.push(r);
      });
      return out;
    }

    // A resource is a module group when it is the parent of at least one other resource
    // (Issue #105). The group header row toggles its sub-section rows open/closed; granting the
    // group's own verb cascades to every sub-section, or expand to grant sections individually.
    function render(rows) {
      body.innerHTML = "";
      var parents = {};
      rows.forEach(function (r) { if (r.parent_resource) parents[r.parent_resource] = true; });

      groupedRows(rows).forEach(function (row) {
        var isGroup = !!parents[row.resource];
        var isChild = !!row.parent_resource;
        var tr = document.createElement("tr");
        // Metadata for the client-side filter (Issue #119).
        tr._permRow = row;
        tr._permResource = row.resource;
        tr._permIsGroup = isGroup;
        tr._permParent = row.parent_resource || null;
        // A three-deep resource (Issue #145, e.g. communications.messages under communications) is
        // both a group (it has its own children) *and* a child (it has a parent) — keep both
        // classes/behaviours rather than the second assignment silently overwriting the first.
        var classes = [];
        if (isGroup) classes.push("group-head");
        if (isChild) {
          classes.push("group-child");
          tr.setAttribute("data-child-of", row.parent_resource);
          tr.hidden = true;
        }
        if (classes.length) tr.className = classes.join(" ");

        var res = document.createElement("td");
        res.className = "res";
        if (isGroup) {
          var toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "group-toggle";
          toggle.setAttribute("aria-expanded", "false");
          toggle.setAttribute("data-resource", row.resource);
          // A mid-tree group (both a group and a child) shows only its section suffix, same as a
          // plain child, so its place in the hierarchy reads the same way at any depth.
          var label = isChild
            ? "↳ " + row.resource.replace(row.parent_resource + ".", "")
            : row.resource;
          toggle.textContent = "▸ " + label;
          toggle.addEventListener("click", function () {
            var open = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", open ? "false" : "true");
            toggle.textContent = (open ? "▸ " : "▾ ") + label;
            Array.prototype.forEach.call(
              body.querySelectorAll('tr[data-child-of="' + row.resource + '"]'),
              function (childRow) { childRow.hidden = open; }
            );
          });
          res.appendChild(toggle);
        } else if (isChild) {
          var indent = document.createElement("span");
          indent.className = "child";
          // Show only the section suffix after the dotted parent prefix (e.g. "details").
          indent.textContent = "↳ " + row.resource.replace(row.parent_resource + ".", "");
          res.appendChild(indent);
        } else {
          res.textContent = row.resource;
        }
        tr.appendChild(res);

        var grant = document.createElement("td");
        var vSel = verbSelect(row);
        grant.appendChild(vSel);
        grant.appendChild(effectSelect(row, vSel));
        grant.appendChild(scopeSelect(row, vSel));
        tr.appendChild(grant);
        tr.appendChild(effectiveCell(row));
        var used = document.createElement("td");
        used.className = "usage";
        used.textContent = usageLabel(row);
        if (isUnusedGrant(row)) used.classList.add("usage-stale");
        tr.appendChild(used);
        body.appendChild(tr);
      });
      applyFilter(); // re-apply the active resource/state filter to the freshly built matrix
    }

    saveBtn.addEventListener("click", function () {
      var role = roleSelect.value;
      if (!role) return;
      var permissions = {};
      Array.prototype.forEach.call(body.querySelectorAll("select[data-resource]"), function (sel) {
        var resource = sel.getAttribute("data-resource");
        if (!sel.value) { permissions[resource] = null; return; }
        var effSel = body.querySelector('select[data-effect-for="' + resource + '"]');
        var scopeSel = body.querySelector('select[data-scope-for="' + resource + '"]');
        var cell = { max_verb: sel.value, effect: effSel ? effSel.value : "allow" };
        // Only send a tier the control actually offered. Omitting it tells the API to preserve
        // whatever is stored, which is what keeps a grid edit from resetting a JSON-authored
        // grant's breadth (Issue #173).
        if (scopeSel) cell.scope = scopeSel.value;
        permissions[resource] = cell;
      });
      setMsg(msg, "Saving…", "muted");
      writeJson("PUT", "/roles/" + encodeURIComponent(role) + "/permissions", { permissions: permissions }).then(function (r) {
        if (!r.ok) return setMsg(msg, errorText(r, "Could not save the matrix."), "error");
        setMsg(msg, "Permissions saved.", "ok");
        if (r.data) applyUsageState(r.data);
        render((r.data && r.data.permissions) || []);
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    });

    // ── Matrix ⇄ JSON (Issue #161, M28) ──────────────────────────────────────
    // Two views over the *same* role_permission rows: the grid above and the role's policy
    // document. Neither is a cache of the other — each view reloads from its own endpoint when
    // shown, so switching tabs can never present stale state as current.
    var matrixView = document.getElementById("perm-matrix-view");
    var jsonView = document.getElementById("perm-json-view");
    var jsonArea = document.getElementById("perm-json");
    var matrixBtn = document.getElementById("perm-view-matrix");
    var jsonBtn = document.getElementById("perm-view-json");
    var jsonSaveBtn = document.getElementById("perm-json-save");

    /** Show one of the two views and load it fresh. */
    function showView(which) {
      var json = which === "json";
      if (jsonView) jsonView.hidden = !json;
      if (matrixView) matrixView.hidden = json;
      if (saveBtn) saveBtn.hidden = json;
      if (jsonSaveBtn) jsonSaveBtn.hidden = !json;
      if (matrixBtn) {
        matrixBtn.setAttribute("aria-pressed", json ? "false" : "true");
        matrixBtn.className = json ? "btn btn-quiet btn-sm" : "btn btn-sm";
      }
      if (jsonBtn) {
        jsonBtn.setAttribute("aria-pressed", json ? "true" : "false");
        jsonBtn.className = json ? "btn btn-sm" : "btn btn-quiet btn-sm";
      }
      setMsg(msg, "");
      if (json) loadPolicy(); else loadMatrix();
    }

    /** Load the role's policy document into the editor. */
    function loadPolicy() {
      var role = roleSelect.value;
      if (!role || !jsonArea) return;
      loading.hidden = false;
      getJson("/roles/" + encodeURIComponent(role) + "/policy").then(function (r) {
        loading.hidden = true;
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not load the policy."), "error");
        jsonArea.value = JSON.stringify(r.data, null, 2);
      }).catch(function () { loading.hidden = true; setMsg(msg, "Network error.", "error"); });
    }

    /** Client-side shape check before submitting; the server's validation is authoritative. */
    function parsePolicy(raw) {
      var parsed = JSON.parse(raw); // throws → caller reports "not valid JSON"
      if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.statements))
        throw new Error("A policy document needs a 'statements' array.");
      parsed.statements.forEach(function (s, i) {
        if (!s || typeof s.resource !== "string" || !s.resource)
          throw new Error("Statement " + (i + 1) + " needs a 'resource'.");
        if (!s.verb === !s.action)
          throw new Error("Statement " + (i + 1) + " needs exactly one of 'verb' or 'action'.");
      });
      return parsed;
    }

    if (jsonSaveBtn) jsonSaveBtn.addEventListener("click", function () {
      var role = roleSelect.value;
      if (!role || !jsonArea) return;
      var document_;
      try {
        document_ = parsePolicy(jsonArea.value);
      } catch (err) {
        return setMsg(msg, err.message || "That is not valid JSON.", "error");
      }
      document_.role = role; // the URL is authoritative; keep the body consistent with it
      setMsg(msg, "Saving…", "muted");
      writeJson("PUT", "/roles/" + encodeURIComponent(role) + "/policy", document_).then(function (r) {
        if (!r.ok) return setMsg(msg, errorText(r, "Could not save the policy."), "error");
        var d = r.data || {};
        setMsg(msg, "Policy saved — " + (d.created || 0) + " added, " + (d.updated || 0) +
          " changed, " + (d.deleted || 0) + " revoked.", "ok");
        if (d.document) jsonArea.value = JSON.stringify(d.document, null, 2);
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    });

    if (matrixBtn) matrixBtn.addEventListener("click", function () { showView("matrix"); });
    if (jsonBtn) jsonBtn.addEventListener("click", function () { showView("json"); });

    roleSelect.addEventListener("change", function () {
      if (jsonView && !jsonView.hidden) loadPolicy(); else loadMatrix();
    });
    search.addEventListener("input", debounce(applyFilter, 200));
    stateFilter.addEventListener("change", applyFilter);
    if (clearBtn) clearBtn.addEventListener("click", function () {
      search.value = "";
      stateFilter.value = "all";
      applyFilter();
    });

    // Jump here from the Roles tab "Edit permissions" button.
    document.addEventListener("bkp:edit-permissions", function (e) {
      var role = e.detail && e.detail.role;
      document.getElementById("tab-permissions").click();
      var apply = function () { roleSelect.value = role; loadMatrix(); };
      if (loadedOptions) apply(); else reloadRoleOptions().then(apply);
    });

    return {
      refresh: function () {
        // Refresh whichever view is showing, so the tab never reopens on stale state.
        var reload = function () {
          if (jsonView && !jsonView.hidden) loadPolicy(); else loadMatrix();
        };
        (loadedOptions ? Promise.resolve() : reloadRoleOptions()).then(reload);
      },
      reloadRoleOptions: reloadRoleOptions,
    };
  }

  // ── Users tab ────────────────────────────────────────────────────────────────

  function setupUsersTab() {
    var search = document.getElementById("users-search");
    var roleFilter = document.getElementById("users-role-filter");
    var body = document.getElementById("users-body");
    var msg = document.getElementById("users-msg");
    var newBtn = document.getElementById("new-user-btn");

    // A slide-in over the full-width list (Issue #132), not a second column beside it.
    var detail = window.BKPAdmin.mountDetailSlideover("user-detail", {
      onClose: function () { selected = null; window.BKPAdmin.selectRow(body, null); },
    });
    var udEmail = document.getElementById("ud-email");
    var udId = document.getElementById("ud-id");
    var udFirst = document.getElementById("ud-first");
    var udLast = document.getElementById("ud-last");
    var udEmailIn = document.getElementById("ud-email-in");
    var udRole = document.getElementById("ud-role");
    var udSave = document.getElementById("ud-save");
    var udResend = document.getElementById("ud-resend");
    var udDelete = document.getElementById("ud-delete");
    var udMsg = document.getElementById("ud-msg");
    var udAsgList = document.getElementById("ud-assignments-list");
    var udAsgRole = document.getElementById("ud-asg-role");
    var udAsgScopeType = document.getElementById("ud-asg-scope-type");
    var udAsgScopeId = document.getElementById("ud-asg-scope-id");
    var udAsgPropertyPicker = document.getElementById("ud-asg-property-picker");
    var udAsgPropertyId = document.getElementById("ud-asg-property-id");
    var udAsgExpires = document.getElementById("ud-asg-expires");
    var udAsgAdd = document.getElementById("ud-asg-add");
    var udAsgMsg = document.getElementById("ud-asg-msg");

    // A "property" scope_type swaps the free-text scope-id box for a real property search
    // (Issue #147) — both write the same underlying id the submit handler below reads.
    function syncScopeIdWidget() {
      var isProperty = udAsgScopeType.value.trim().toLowerCase() === "property";
      udAsgScopeId.hidden = isProperty;
      udAsgPropertyPicker.hidden = !isProperty;
    }
    udAsgScopeType.addEventListener("input", syncScopeIdWidget);
    syncScopeIdWidget();

    var newCard = document.getElementById("new-user-card");
    var nuFirst = document.getElementById("nu-first");
    var nuLast = document.getElementById("nu-last");
    var nuEmail = document.getElementById("nu-email");
    var nuRole = document.getElementById("nu-role");
    var nuActivation = document.getElementById("nu-activation");
    var nuCreate = document.getElementById("nu-create");
    var nuCancel = document.getElementById("nu-cancel");
    var nuMsg = document.getElementById("nu-msg");

    var clearBtn = document.getElementById("users-clear");
    var selected = null;
    var rolesLoaded = false;

    // Restore search + role filter + page from the URL query (Issue 114). The role options load
    // asynchronously, so hold the wanted role until they exist, then apply it.
    var q0 = window.BKPAdmin.readQuery();
    if (q0.q) search.value = q0.q;
    var pendingRoleFilter = q0.role || null;
    var pager = window.BKPAdmin.createPager({
      onChange: function () { load(); },
      size: pageSizeFromQuery(q0.limit),
    });
    pager.mount(document.getElementById("users-pager"));
    pager.offset = Math.max(0, parseInt(q0.offset, 10) || 0);

    var usersTable = body.closest("table");

    // Click-to-sort headers (Issue 115), restored from the URL and composed with filter + page.
    var sortState = { sort: q0.sort || null, order: q0.order || null };
    window.BKPAdmin.mountSortableHeaders(usersTable, {
      initial: sortState,
      onSort: function (sort, order) {
        sortState.sort = sort;
        sortState.order = order;
        reload(); // a sort change resets to page 1
      },
    });

    // Row selection + single bulk-delete (Issue 116), gated on the DELETE grant in the template.
    var bulkBtn = document.getElementById("users-bulk-delete");
    var bulk = bulkBtn
      ? window.BKPAdmin.mountBulkDelete({
          table: usersTable,
          deleteBtn: bulkBtn,
          countEl: document.getElementById("users-selected"),
          noun: "user",
          onDelete: bulkDelete,
        })
      : null;
    var canBulk = !!usersTable.querySelector("thead .select-all");

    /** True when a search term or a role filter is applied (drives the empty-state copy). */
    function hasActiveFilters() { return !!(search.value.trim() || roleFilter.value); }

    function bulkDelete(ids) {
      setMsg(msg, "Deleting…", "muted");
      writeJson("POST", "/users/bulk-delete", { ids: ids }).then(function (r) {
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not delete the selection."), "error");
        setMsg(msg, bulkResultText(r.data, "user"), r.data.failed_count ? "muted" : "ok");
        // A deleted user may have been open in the detail panel — close it.
        detail.close();
        reload();
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    }

    function fillRoleSelect(sel, names, keepValue) {
      var current = keepValue ? sel.value : "";
      sel.innerHTML = "";
      names.forEach(function (n) {
        var o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
      });
      if (current) sel.value = current;
    }

    function reloadRoleOptions() {
      return getJson("/roles?limit=" + PAGE_LIMIT).then(function (r) {
        if (!r.ok || !r.data) return;
        var names = (r.data.items || []).map(function (x) { return x.name; });
        // Filter dropdown keeps its "All roles" sentinel.
        var currentFilter = roleFilter.value || pendingRoleFilter || "";
        pendingRoleFilter = null;
        roleFilter.innerHTML = '<option value="">All roles</option>';
        names.forEach(function (n) {
          var o = document.createElement("option"); o.value = n; o.textContent = n; roleFilter.appendChild(o);
        });
        // Only keep the value if it still names a live role (a stale URL filter falls back to all).
        roleFilter.value = names.indexOf(currentFilter) >= 0 ? currentFilter : "";
        // Edit + create role (group) dropdowns.
        fillRoleSelect(udRole, names, true);
        fillRoleSelect(nuRole, names, true);
        rolesLoaded = true;
      });
    }

    function load() {
      var params = new URLSearchParams();
      if (search.value.trim()) params.set("q", search.value.trim());
      if (roleFilter.value) params.set("role", roleFilter.value);
      if (sortState.sort) {
        params.set("sort", sortState.sort);
        if (sortState.order) params.set("order", sortState.order);
      }
      pager.applyTo(params);
      window.BKPAdmin.writeQuery(params); // reflect the exact request in the address bar
      if (clearBtn) clearBtn.hidden = !hasActiveFilters();
      setMsg(msg, "Loading…", "muted");
      getJson("/users?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not load users."), "error");
        render(r.data.items || [], r.data.total || 0);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    }
    // A new search/filter restarts paging from the first page.
    function reload() { pager.reset(); load(); }

    function clearFilters() {
      search.value = "";
      roleFilter.value = "";
      reload();
    }

    function render(items, total) {
      body.innerHTML = "";
      if (!items.length) {
        // Distinguish "no users yet" from "your filter matched nothing" (Issue 114).
        setMsg(msg, hasActiveFilters() ? "No users match your filters." : "No users yet.", "muted");
        if (bulk) bulk.bind(); // reset the selection count / disable the Delete button
        return;
      }
      setMsg(msg, "");
      items.forEach(function (user) {
        var tr = document.createElement("tr");
        if (canBulk) tr.appendChild(window.BKPAdmin.selectionCell(user.id));
        tr.appendChild(td(user.email));
        tr.appendChild(td(user.role));
        var status = document.createElement("td");
        var badge = document.createElement("span");
        badge.className = "badge " + (user.is_verified ? "badge-ok" : "badge-warn");
        badge.textContent = user.is_verified ? "verified" : "unverified";
        status.appendChild(badge);
        tr.appendChild(status);
        var actions = window.BKPAdmin.actionsCell();
        actions.appendChild(window.BKPAdmin.eyeLink("/admin/rbac/users/" + encodeURIComponent(user.id), "View user"));
        tr.appendChild(actions);
        tr.addEventListener("click", function () {
          Array.prototype.forEach.call(body.children, function (row) { row.classList.remove("selected"); });
          tr.classList.add("selected");
          select(user.id);
        });
        body.appendChild(tr);
      });
      if (bulk) bulk.bind(); // (re)bind the freshly built row checkboxes
    }

    function select(userId) {
      // Fetch full detail (name fields aren't in the list row).
      getJson("/users/" + encodeURIComponent(userId)).then(function (r) {
        if (!r.ok || !r.data) return setMsg(udMsg, errorText(r, "Could not load the user."), "error");
        var user = r.data;
        selected = user;
        detail.setFullHref("/admin/rbac/users/" + encodeURIComponent(user.id));
        detail.open();
        udEmail.textContent = user.email;
        udId.textContent = "ID " + user.id + (user.is_verified ? " · verified" : " · unverified");
        udFirst.value = user.first_name || "";
        udLast.value = user.last_name || "";
        udEmailIn.value = user.email || "";
        udResend.hidden = !!user.is_verified;
        setMsg(udMsg, "");
        var apply = function () { udRole.value = user.role; };
        if (rolesLoaded) apply(); else reloadRoleOptions().then(apply);
        loadAssignments(user.id);
      }).catch(function () { setMsg(udMsg, "Network error.", "error"); });
    }

    // Multi-role, scoped & time-boxed assignments (Issue #136): list a user's assignments and let
    // an operator add/remove scoped and/or expiring ones alongside the plain "Role (group)" flow.
    function loadAssignments(userId) {
      setMsg(udAsgMsg, "");
      udAsgList.innerHTML = "";
      Promise.all([
        getJson("/users/" + encodeURIComponent(userId) + "/assignments"),
        getJson("/roles?limit=" + PAGE_LIMIT),
      ]).then(function (results) {
        var asg = (results[0].ok && results[0].data && results[0].data.assignments) || [];
        var roles = (results[1].ok && results[1].data && results[1].data.items) || [];
        renderAssignments(userId, asg, roles.map(function (r) { return r.name; }));
      });
    }

    function renderAssignments(userId, assignments, roleNames) {
      udAsgList.innerHTML = "";
      if (!assignments.length) {
        var empty = document.createElement("li");
        empty.className = "muted";
        empty.textContent = "No assignments.";
        udAsgList.appendChild(empty);
      }
      assignments.forEach(function (a) {
        var li = document.createElement("li");
        if (!a.active) li.className = "expired";
        var label = document.createElement("span");
        var scope = a.scope_type ? (" @ " + a.scope_type + ":" + a.scope_id) : " (global)";
        var expiry = a.expires_at ? (" · until " + a.expires_at.slice(0, 16).replace("T", " ")) : "";
        var state = a.active ? "" : " · expired";
        label.textContent = a.role + scope + expiry + state;
        li.appendChild(label);
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn-quiet btn-sm";
        remove.textContent = "Remove";
        remove.addEventListener("click", function () {
          setMsg(udAsgMsg, "Removing…", "muted");
          writeJson("DELETE", "/users/" + encodeURIComponent(userId) + "/assignments/" + encodeURIComponent(a.id))
            .then(function (r) {
              if (!r.ok) return setMsg(udAsgMsg, errorText(r, "Could not remove."), "error");
              setMsg(udAsgMsg, "");
              loadAssignments(userId);
              load(); // the row's mirror role may have changed
            }).catch(function () { setMsg(udAsgMsg, "Network error.", "error"); });
        });
        li.appendChild(remove);
        udAsgList.appendChild(li);
      });
      udAsgRole.innerHTML = "";
      roleNames.forEach(function (n) {
        var opt = document.createElement("option");
        opt.value = n; opt.textContent = n;
        udAsgRole.appendChild(opt);
      });
    }

    udAsgAdd.addEventListener("click", function () {
      if (!selected) return;
      var body = { role: udAsgRole.value };
      var scopeType = udAsgScopeType.value.trim();
      var scopeId = scopeType.toLowerCase() === "property"
        ? udAsgPropertyId.value.trim()
        : udAsgScopeId.value.trim();
      if (scopeType) body.scope_type = scopeType;
      if (scopeId) body.scope_id = scopeId;
      if (udAsgExpires.value) body.expires_at = udAsgExpires.value;
      setMsg(udAsgMsg, "Adding…", "muted");
      writeJson("POST", "/users/" + encodeURIComponent(selected.id) + "/assignments", body)
        .then(function (r) {
          if (!r.ok) return setMsg(udAsgMsg, errorText(r, "Could not add the assignment."), "error");
          setMsg(udAsgMsg, "");
          udAsgScopeType.value = ""; udAsgScopeId.value = ""; udAsgExpires.value = "";
          if (window.BKPAC) window.BKPAC.clear(udAsgPropertyId);
          syncScopeIdWidget();
          loadAssignments(selected.id);
          load();
        }).catch(function () { setMsg(udAsgMsg, "Network error.", "error"); });
    });

    udSave.addEventListener("click", function () {
      if (!selected) return;
      setMsg(udMsg, "Saving…", "muted");
      // One PATCH updates identity + role (the access group) together.
      writeJson("PATCH", "/users/" + encodeURIComponent(selected.id), {
        email: udEmailIn.value.trim(),
        role: udRole.value,
        first_name: udFirst.value.trim() || null,
        last_name: udLast.value.trim() || null,
      }).then(function (r) {
        if (!r.ok) return setMsg(udMsg, errorText(r, "Could not save."), "error");
        setMsg(udMsg, "Saved.", "ok");
        load();
      }).catch(function () { setMsg(udMsg, "Network error.", "error"); });
    });

    udResend.addEventListener("click", function () {
      if (!selected) return;
      setMsg(udMsg, "Sending…", "muted");
      writeJson("PATCH", "/users/" + encodeURIComponent(selected.id), { resend_verification: true }).then(function (r) {
        if (!r.ok) return setMsg(udMsg, errorText(r, "Could not resend the activation email."), "error");
        setMsg(udMsg, "Activation email sent.", "ok");
      }).catch(function () { setMsg(udMsg, "Network error.", "error"); });
    });

    // ud-delete only renders when the caller holds users:delete (Issue #155) — same guard shape
    // as admin-properties.js/admin-vendors.js's own gated delete buttons.
    if (udDelete) udDelete.addEventListener("click", function () {
      if (!selected) return;
      if (!window.confirm('Delete user "' + selected.email + '"? They will no longer be able to sign in.')) return;
      setMsg(udMsg, "Deleting…", "muted");
      writeJson("DELETE", "/users/" + encodeURIComponent(selected.id)).then(function (r) {
        if (!r.ok) return setMsg(udMsg, errorText(r, "Could not delete the user."), "error");
        detail.close();
        load();
      }).catch(function () { setMsg(udMsg, "Network error.", "error"); });
    });

    // New user
    function clearNew() {
      nuFirst.value = ""; nuLast.value = ""; nuEmail.value = "";
      nuActivation.checked = true; setMsg(nuMsg, "");
    }
    // new-user-btn/new-user-card only render when the caller holds users:create (Issue #155).
    if (newBtn && nuCreate) {
    newBtn.addEventListener("click", function () {
      // The create card opens under the full-width list, where the detail panel would cover it.
      detail.close();
      newCard.hidden = !newCard.hidden;
      if (!newCard.hidden) {
        (rolesLoaded ? Promise.resolve() : reloadRoleOptions()).then(function () { nuEmail.focus(); });
      }
    });
    nuCancel.addEventListener("click", function () { newCard.hidden = true; clearNew(); });
    nuCreate.addEventListener("click", function () {
      if (!nuEmail.value.trim()) return setMsg(nuMsg, "Enter an email address.", "error");
      setMsg(nuMsg, "Creating…", "muted");
      writeJson("POST", "/users", {
        email: nuEmail.value.trim(),
        role: nuRole.value,
        first_name: nuFirst.value.trim() || null,
        last_name: nuLast.value.trim() || null,
        send_activation_email: nuActivation.checked,
      }).then(function (r) {
        if (!r.ok) return setMsg(nuMsg, errorText(r, "Could not create the user."), "error");
        newCard.hidden = true; clearNew();
        load();
      }).catch(function () { setMsg(nuMsg, "Network error.", "error"); });
    });
    }

    search.addEventListener("input", debounce(reload, 250));
    roleFilter.addEventListener("change", reload);
    if (clearBtn) clearBtn.addEventListener("click", clearFilters);

    return {
      refresh: function () { (rolesLoaded ? Promise.resolve() : reloadRoleOptions()).then(load); },
      reloadRoleOptions: reloadRoleOptions,
    };
  }

  function td(text) { var el = document.createElement("td"); el.textContent = text; return el; }

  // ── Audit tab (Issue #138) ───────────────────────────────────────────────────
  // Read-only trail of RBAC grant/revoke changes, filterable by actor/target/action.
  function setupAuditTab() {
    var body = document.getElementById("audit-body");
    if (!body) return { refresh: function () {} };
    var actorInput = document.getElementById("audit-actor");
    var targetType = document.getElementById("audit-target-type");
    var actionSel = document.getElementById("audit-action");
    var msg = document.getElementById("audit-msg");
    var pager = window.BKPAdmin.createPager({ onChange: function () { load(); }, size: 20 });
    pager.mount(document.getElementById("audit-pager"));

    function load() {
      var params = new URLSearchParams();
      if (actorInput.value.trim()) params.set("actor", actorInput.value.trim());
      if (targetType.value) params.set("target_type", targetType.value);
      if (actionSel.value) params.set("action", actionSel.value);
      pager.applyTo(params);
      setMsg(msg, "Loading…", "muted");
      getJson("/audit?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return setMsg(msg, errorText(r, "Could not load the audit trail."), "error");
        render(r.data.items || [], r.data.total || 0);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { setMsg(msg, "Network error.", "error"); });
    }

    function fmtState(before, after) {
      var b = before ? JSON.stringify(before) : "∅";
      var a = after ? JSON.stringify(after) : "∅";
      return b + " → " + a;
    }

    function render(items, total) {
      body.innerHTML = "";
      if (!items.length) { setMsg(msg, "No audit entries.", "muted"); return; }
      setMsg(msg, "");
      items.forEach(function (row) {
        var tr = document.createElement("tr");
        var when = row.created_at ? new Date(row.created_at).toLocaleString("en-ZA", { timeZone: "Africa/Johannesburg" }) : "—";
        tr.appendChild(td(when));
        tr.appendChild(td(row.actor || "—"));
        var action = document.createElement("td");
        var badge = document.createElement("span");
        badge.className = "badge " + (row.action === "revoke" ? "badge-warn" : "badge-ok");
        badge.textContent = row.action;
        action.appendChild(badge);
        tr.appendChild(action);
        tr.appendChild(td(row.target_type + ": " + row.target_id));
        tr.appendChild(td(fmtState(row.before, row.after)));
        body.appendChild(tr);
      });
    }

    var reload = function () { pager.reset(); load(); };
    actorInput.addEventListener("input", debounce(reload, 250));
    targetType.addEventListener("change", reload);
    actionSel.addEventListener("change", reload);

    var loaded = false;
    return { refresh: function () { if (!loaded) { loaded = true; load(); } } };
  }

  // ── Catalog tab (Issue #141) ────────────────────────────────────────────────
  //
  // Manage the DB catalog — resources, actions and (resource, action) permissions. Every mutation
  // hits the rbac-gated, audited catalog API; system rows are read-only. Newly added resources
  // appear as grantable rows in the Permissions grid automatically (it reads the same catalog).

  /**
   * The catalog's "quick view": a non-modal slide-in (same pattern as the role detail panel)
   * showing where a resource sits in the parent/child tree — or, opened from an action, permission
   * or nav gate, the tree of whatever resource that record touches. Always fetches fresh (the
   * catalog's sub-tabs are separate page loads, so another tab's cached rows aren't available).
   */
  function setupQuickView() {
    var root = document.getElementById("catalog-quickview");
    if (!root) return { open: function () {} };
    var eyebrow = document.getElementById("qv-eyebrow");
    var title = document.getElementById("qv-title");
    var sub = document.getElementById("qv-sub");
    var tree = document.getElementById("qv-tree");
    var ctrl = window.BKPAdmin.mountDetailSlideover("catalog-quickview");

    function loading() {
      tree.innerHTML = "";
      var p = document.createElement("p");
      p.className = "card-sub";
      p.textContent = "Loading…";
      tree.appendChild(p);
    }
    function empty(text) {
      tree.innerHTML = "";
      var p = document.createElement("p");
      p.className = "card-sub";
      p.textContent = text;
      tree.appendChild(p);
    }

    /** The resource tree (root-down), the ``targetKey`` node marked current and scrolled to. */
    function paintResourceTree(items, targetKey) {
      var byParent = {};
      (items || []).forEach(function (r) {
        var key = r.parent_key || "";
        (byParent[key] = byParent[key] || []).push(r);
      });
      if (!(byParent[""] || []).length && !items.length) return empty("No resources yet.");
      function addNode(parentUl, resource) {
        var li = document.createElement("li");
        var node = document.createElement("span");
        node.className = "tree-node" + (resource.key === targetKey ? " tree-current" : "");
        var name = document.createElement("span");
        name.textContent = resource.name;
        var key = document.createElement("span");
        key.className = "tree-key";
        key.textContent = resource.key;
        node.appendChild(name);
        node.appendChild(key);
        li.appendChild(node);
        var children = byParent[resource.key];
        if (children && children.length) {
          var childUl = document.createElement("ul");
          children.forEach(function (c) { addNode(childUl, c); });
          li.appendChild(childUl);
        }
        parentUl.appendChild(li);
      }
      var list = document.createElement("ul");
      list.className = "tree-view";
      (byParent[""] || []).forEach(function (r) { addNode(list, r); });
      tree.innerHTML = "";
      var found = (items || []).some(function (r) { return r.key === targetKey; });
      if (!found) {
        var notice = document.createElement("p");
        notice.className = "card-sub";
        notice.textContent = targetKey + " isn't in the resource catalog yet — showing the full tree.";
        tree.appendChild(notice);
      }
      tree.appendChild(list);
      var current = list.querySelector(".tree-current");
      if (current) current.scrollIntoView({ block: "center" });
    }

    /** An action's quick view: the flat list of resources it's currently paired with. */
    function paintActionUsage(permissions, actionKey) {
      var matches = (permissions || []).filter(function (p) { return p.action_key === actionKey; });
      if (!matches.length) return empty("Not paired with any resource yet.");
      var list = document.createElement("ul");
      list.className = "tree-view tree-flat";
      matches.forEach(function (p) {
        var li = document.createElement("li");
        var node = document.createElement("span");
        node.className = "tree-node";
        node.textContent = p.resource_key;
        li.appendChild(node);
        list.appendChild(li);
      });
      tree.innerHTML = "";
      tree.appendChild(list);
    }

    /**
     * Open the panel for one record. ``kind`` is ``"resource"|"action"|"permission"|"nav-gate"``;
     * ``item`` is that record's own row data from the list it was opened from.
     */
    function open(kind, item) {
      if (!ctrl) return;
      ctrl.open();
      loading();
      if (kind === "resource") {
        eyebrow.textContent = "Resource";
        title.textContent = item.name;
        sub.textContent = item.key;
        getJson("/resources").then(function (r) {
          if (r.ok && r.data) paintResourceTree(r.data.items, item.key);
        });
      } else if (kind === "permission") {
        eyebrow.textContent = "Permission";
        title.textContent = item.key;
        sub.textContent = "Where " + item.resource_key + " sits in the resource tree.";
        getJson("/resources").then(function (r) {
          if (r.ok && r.data) paintResourceTree(r.data.items, item.resource_key);
        });
      } else if (kind === "nav-gate") {
        eyebrow.textContent = "Nav gate";
        title.textContent = item.surface_key;
        sub.textContent = "Live gate: " + item.resource_key + ":"
          + (item.action ? "!" + item.action : item.verb) + " @" + item.scope;
        getJson("/resources").then(function (r) {
          if (r.ok && r.data) paintResourceTree(r.data.items, item.resource_key);
        });
      } else if (kind === "action") {
        eyebrow.textContent = "Action";
        title.textContent = item.name;
        sub.textContent = item.key;
        getJson("/permissions").then(function (r) {
          if (r.ok && r.data) paintActionUsage(r.data.items, item.key);
        });
      }
    }

    return { open: open };
  }

  function setupCatalogTab() {
    var resBody = document.getElementById("catalog-resources-body");
    if (!resBody) return { refresh: function () {} };
    var actBody = document.getElementById("catalog-actions-body");
    var permBody = document.getElementById("catalog-permissions-body");
    var gateBody = document.getElementById("catalog-nav-gates-body");
    var msg = document.getElementById("catalog-msg");
    var quickview = setupQuickView();

    function cell(text) { var td = document.createElement("td"); td.textContent = text == null ? "" : text; return td; }

    /** A compact "eye" trigger for the quick-view tree, its own column like a list's View cell. */
    function viewCell(onView) {
      var td = document.createElement("td");
      td.className = "col-view";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-quiet btn-icon";
      btn.setAttribute("aria-label", "Quick view");
      btn.title = "Quick view";
      btn.innerHTML = window.BKPAdmin.EYE_SVG;
      btn.addEventListener("click", onView);
      td.appendChild(btn);
      return td;
    }

    // The caller's verdicts for the catalog tables, stamped on the panel by the template and read
    // through the shared helper (Issue #168, M28). A JS-rendered row cannot carry a Jinja gate, so
    // this is how the four catalog renderers below stop appending a Delete a caller cannot use.
    // UX only — every route re-checks the grant regardless.
    var catalogVerdicts = window.BKPAdmin && window.BKPAdmin.verdicts
      ? window.BKPAdmin.verdicts("catalog-verdicts")
      : { can: function () { return true; } };

    /** A delete button for a non-system row; system rows render a muted label instead. */
    function actionCell(isSystem, onDelete) {
      var td = document.createElement("td");
      td.setAttribute("data-col", "actions");
      if (isSystem) { td.className = "muted"; td.textContent = "system"; return td; }
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-danger btn-sm";
      btn.textContent = "Delete";
      btn.addEventListener("click", onDelete);
      td.appendChild(btn);
      return td;
    }

    /** Edit + (Delete, unless system) — every row can have its name/description patched;
     * only a custom (non-system) row can be removed entirely. */
    function editActionCell(isSystem, onEdit, onDelete) {
      var td = document.createElement("td");
      td.className = "row-actions";
      td.setAttribute("data-col", "actions");
      if (catalogVerdicts.can("update")) {
        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "btn btn-quiet btn-sm";
        editBtn.textContent = "Edit";
        editBtn.addEventListener("click", onEdit);
        td.appendChild(editBtn);
      }
      if (isSystem) {
        var label = document.createElement("span");
        label.className = "muted";
        label.textContent = "system";
        td.appendChild(label);
      } else {
        var delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.className = "btn btn-danger btn-sm";
        delBtn.textContent = "Delete";
        delBtn.addEventListener("click", onDelete);
        td.appendChild(delBtn);
      }
      return td;
    }

    function saveCancelCell(onSave, onCancel) {
      var td = document.createElement("td");
      td.className = "row-actions";
      var saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn btn-primary btn-sm";
      saveBtn.textContent = "Save";
      saveBtn.addEventListener("click", onSave);
      var cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btn-quiet btn-sm";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", onCancel);
      td.appendChild(saveBtn);
      td.appendChild(cancelBtn);
      return td;
    }

    function textInputCell(value, placeholder) {
      var td = document.createElement("td");
      var input = document.createElement("input");
      input.type = "text";
      input.className = "cell-input";
      input.value = value || "";
      if (placeholder) input.placeholder = placeholder;
      td.appendChild(input);
      return { td: td, input: input };
    }

    /** A resource-picker cell (autocomplete over the catalog tree), for reparenting in place. */
    function resourcePickerCell(currentKey) {
      var td = document.createElement("td");
      var root = document.createElement("div");
      root.className = "ac";
      root.setAttribute("data-ac-source", "rbac-resources");
      var input = document.createElement("input");
      input.type = "text";
      input.className = "ac-input";
      input.setAttribute("autocomplete", "off");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-autocomplete", "list");
      input.placeholder = "parent key (blank = root)";
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.className = "ac-value";
      var menu = document.createElement("ul");
      menu.className = "ac-menu";
      menu.hidden = true;
      root.appendChild(input);
      root.appendChild(hidden);
      root.appendChild(menu);
      td.appendChild(root);
      if (window.BKPAC && window.BKPAC.attach) window.BKPAC.attach(root);
      if (currentKey && window.BKPAC) window.BKPAC.set(hidden, currentKey, currentKey);
      return { td: td, value: hidden };
    }

    function del(path, label) {
      writeJson("DELETE", path).then(function (r) {
        if (!r.ok && r.status !== 204) return setMsg(msg, errorText(r, "Could not delete " + label + "."), "error");
        setMsg(msg, label + " deleted.", "success");
        load();
      });
    }

    function patch(path, body, label, onDone) {
      writeJson("PATCH", path, body).then(function (r) {
        if (!r.ok) return setMsg(msg, errorText(r, "Could not update " + label + "."), "error");
        setMsg(msg, label + " updated.", "success");
        editingResourceId = null;
        editingActionId = null;
        load();
        if (onDone) onDone();
      });
    }

    var editingResourceId = null;
    var editingActionId = null;

    function renderResources(items) {
      resBody.innerHTML = "";
      (items || []).forEach(function (r) {
        var tr = document.createElement("tr");
        if (r.id === editingResourceId) {
          var nameField = textInputCell(r.name, "Name");
          var parentField = resourcePickerCell(r.parent_key);
          tr.appendChild(cell(r.key));
          tr.appendChild(nameField.td);
          tr.appendChild(parentField.td);
          tr.appendChild(cell(r.is_system ? "yes" : "no"));
          tr.appendChild(viewCell(function () { quickview.open("resource", r); }));
          tr.appendChild(saveCancelCell(
            function () {
              var name = nameField.input.value.trim();
              if (!name) return setMsg(msg, "Name is required.", "error");
              patch("/resources/" + encodeURIComponent(r.id), {
                name: name,
                parent_key: parentField.value.value.trim(),
              }, "Resource");
            },
            function () { editingResourceId = null; paintResources(); }
          ));
        } else {
          tr.appendChild(cell(r.key));
          tr.appendChild(cell(r.name));
          tr.appendChild(cell(r.parent_key || "—"));
          tr.appendChild(cell(r.is_system ? "yes" : "no"));
          tr.appendChild(viewCell(function () { quickview.open("resource", r); }));
          tr.appendChild(editActionCell(
            r.is_system,
            function () { editingResourceId = r.id; paintResources(); },
            function () { del("/resources/" + encodeURIComponent(r.id), "Resource"); }
          ));
        }
        resBody.appendChild(tr);
      });
    }

    function renderActions(items) {
      actBody.innerHTML = "";
      (items || []).forEach(function (a) {
        var tr = document.createElement("tr");
        if (a.id === editingActionId) {
          var nameField = textInputCell(a.name, "Name");
          tr.appendChild(cell(a.key));
          tr.appendChild(nameField.td);
          tr.appendChild(cell(a.is_system ? "yes" : "no"));
          tr.appendChild(viewCell(function () { quickview.open("action", a); }));
          tr.appendChild(saveCancelCell(
            function () {
              var name = nameField.input.value.trim();
              if (!name) return setMsg(msg, "Name is required.", "error");
              patch("/actions/" + encodeURIComponent(a.id), { name: name }, "Action");
            },
            function () { editingActionId = null; paintActions(); }
          ));
        } else {
          tr.appendChild(cell(a.key));
          tr.appendChild(cell(a.name));
          tr.appendChild(cell(a.is_system ? "yes" : "no"));
          tr.appendChild(viewCell(function () { quickview.open("action", a); }));
          tr.appendChild(editActionCell(
            a.is_system,
            function () { editingActionId = a.id; paintActions(); },
            function () { del("/actions/" + encodeURIComponent(a.id), "Action"); }
          ));
        }
        actBody.appendChild(tr);
      });
    }

    function renderPermissions(items) {
      permBody.innerHTML = "";
      (items || []).forEach(function (p) {
        var tr = document.createElement("tr");
        tr.appendChild(cell(p.key));
        tr.appendChild(viewCell(function () { quickview.open("permission", p); }));
        tr.appendChild(actionCell(false, function () { del("/permissions/" + encodeURIComponent(p.id), "Permission"); }));
        permBody.appendChild(tr);
      });
    }

    /** ``resource:verb`` or ``resource:!action`` (a named action is marked apart from a verb). */
    function fmtGate(resourceKey, verb, action) {
      return resourceKey + ":" + (action ? "!" + action : verb);
    }

    function renderNavGates(items) {
      if (!gateBody) return;
      gateBody.innerHTML = "";
      (items || []).forEach(function (g) {
        var tr = document.createElement("tr");
        tr.appendChild(cell(g.surface_key));
        tr.appendChild(cell(g.label));
        tr.appendChild(cell(fmtGate(g.resource_key, g.verb, g.action)));
        tr.appendChild(cell(g.scope));
        tr.appendChild(cell(fmtGate(g.default_resource_key, g.default_verb, g.default_action)
          + " @" + g.default_scope));
        tr.appendChild(viewCell(function () { quickview.open("nav-gate", g); }));
        var actionsTd = document.createElement("td");
        actionsTd.setAttribute("data-col", "actions");
        if (g.is_overridden) {
          var resetBtn = document.createElement("button");
          resetBtn.type = "button";
          resetBtn.className = "btn btn-danger btn-sm";
          resetBtn.textContent = "Reset";
          resetBtn.addEventListener("click", function () {
            del("/nav-gates/" + encodeURIComponent(g.surface_key), "Nav gate");
          });
          actionsTd.appendChild(resetBtn);
        } else {
          actionsTd.className = "muted";
          actionsTd.textContent = "default";
        }
        tr.appendChild(actionsTd);
        gateBody.appendChild(tr);
      });
    }

    // ── Column-header sorting ── every catalog list loads in full (no pagination), so re-ordering
    // is a pure client-side reshuffle of the last-fetched rows — no re-fetch needed.
    var lastResources = [], lastActions = [], lastPermissions = [], lastNavGates = [];
    var resourcesSort = { key: null, order: null };
    var actionsSort = { key: null, order: null };
    var permissionsSort = { key: null, order: null };
    var navGatesSort = { key: null, order: null };

    function compareValues(a, b) {
      if (typeof a === "boolean" || typeof b === "boolean") { a = a ? 1 : 0; b = b ? 1 : 0; }
      else if (typeof a === "string" || typeof b === "string") { a = (a || "").toLowerCase(); b = (b || "").toLowerCase(); }
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    }
    function sorted(items, state, getters) {
      var get = state.key && getters[state.key];
      if (!get || !state.order) return items;
      var out = items.slice().sort(function (x, y) { return compareValues(get(x), get(y)); });
      if (state.order === "desc") out.reverse();
      return out;
    }

    var RESOURCE_GETTERS = {
      key: function (r) { return r.key; },
      name: function (r) { return r.name; },
      parent: function (r) { return r.parent_key || ""; },
      system: function (r) { return !!r.is_system; },
    };
    var ACTION_GETTERS = {
      key: function (a) { return a.key; },
      name: function (a) { return a.name; },
      system: function (a) { return !!a.is_system; },
    };
    var PERMISSION_GETTERS = { key: function (p) { return p.key; } };
    var NAV_GATE_GETTERS = {
      surface: function (g) { return g.surface_key; },
      label: function (g) { return g.label; },
      gate: function (g) { return fmtGate(g.resource_key, g.verb, g.action); },
      scope: function (g) { return g.scope; },
      default: function (g) { return fmtGate(g.default_resource_key, g.default_verb, g.default_action); },
    };

    // After every repaint, drop the actions column outright for a caller with no ``rbac:DELETE`` —
    // header and cells together, via the shared helper, so the column counts stay aligned (Issue
    // #168, M28). Done here rather than inside each renderer because a repaint rebuilds the body
    // but not the header, so the two have to be reconciled at the same moment.
    function dropActionsWhenDenied(tableId) {
      if (catalogVerdicts.can("delete")) return;
      if (window.BKPAdmin && window.BKPAdmin.dropColumn) {
        window.BKPAdmin.dropColumn(tableId, "actions");
      }
    }

    function paintResources() {
      renderResources(sorted(lastResources, resourcesSort, RESOURCE_GETTERS));
      dropActionsWhenDenied("catalog-resources");
    }
    function paintActions() {
      renderActions(sorted(lastActions, actionsSort, ACTION_GETTERS));
      dropActionsWhenDenied("catalog-actions");
    }
    function paintPermissions() {
      renderPermissions(sorted(lastPermissions, permissionsSort, PERMISSION_GETTERS));
      dropActionsWhenDenied("catalog-permissions");
    }
    function paintNavGates() {
      renderNavGates(sorted(lastNavGates, navGatesSort, NAV_GATE_GETTERS));
      dropActionsWhenDenied("catalog-nav-gates");
    }

    if (window.BKPAdmin && window.BKPAdmin.mountSortableHeaders) {
      window.BKPAdmin.mountSortableHeaders("catalog-resources", {
        onSort: function (key, order) { resourcesSort = { key: key, order: order }; paintResources(); },
      });
      window.BKPAdmin.mountSortableHeaders("catalog-actions", {
        onSort: function (key, order) { actionsSort = { key: key, order: order }; paintActions(); },
      });
      window.BKPAdmin.mountSortableHeaders("catalog-permissions", {
        onSort: function (key, order) { permissionsSort = { key: key, order: order }; paintPermissions(); },
      });
      window.BKPAdmin.mountSortableHeaders("catalog-nav-gates", {
        onSort: function (key, order) { navGatesSort = { key: key, order: order }; paintNavGates(); },
      });
    }

    // Each sub-tab is its own URL, so only one sub-panel is on the page at a time; fetch just the
    // section the caller is looking at (and re-fetch it after a create/delete on that section).
    function sectionVisible(id) { var el = document.getElementById(id); return !!el && !el.hidden; }
    function load() {
      if (sectionVisible("catalog-panel-resources"))
        getJson("/resources").then(function (r) { if (r.ok && r.data) { lastResources = r.data.items || []; paintResources(); } });
      if (sectionVisible("catalog-panel-actions"))
        getJson("/actions").then(function (r) { if (r.ok && r.data) { lastActions = r.data.items || []; paintActions(); } });
      if (sectionVisible("catalog-panel-permissions"))
        getJson("/permissions").then(function (r) { if (r.ok && r.data) { lastPermissions = r.data.items || []; paintPermissions(); } });
      if (sectionVisible("catalog-panel-nav-gates"))
        getJson("/nav-gates").then(function (r) { if (r.ok && r.data) { lastNavGates = r.data.items || []; paintNavGates(); } });
    }

    function bindCreate(formId, buildBody, path, label) {
      var form = document.getElementById(formId);
      if (!form) return;
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var body = buildBody();
        if (!body) return;
        writeJson("POST", path, body).then(function (r) {
          if (!r.ok) return setMsg(msg, errorText(r, "Could not create " + label + "."), "error");
          setMsg(msg, label + " created.", "success");
          form.reset();
          load();
        });
      });
    }

    function val(id) { var el = document.getElementById(id); return el ? el.value.trim() : ""; }

    bindCreate("catalog-resource-form", function () {
      var key = val("cat-res-key"), name = val("cat-res-name");
      if (!key || !name) return null;
      var parent = val("cat-res-parent");
      var body = { key: key, name: name };
      if (parent) body.parent_key = parent;
      return body;
    }, "/resources", "Resource");

    bindCreate("catalog-action-form", function () {
      var key = val("cat-act-key"), name = val("cat-act-name");
      if (!key || !name) return null;
      return { key: key, name: name };
    }, "/actions", "Action");

    bindCreate("catalog-permission-form", function () {
      var resource = val("cat-perm-resource"), action = val("cat-perm-action");
      if (!resource || !action) return null;
      return { resource_key: resource, action_key: action };
    }, "/permissions", "Permission");

    var gateForm = document.getElementById("catalog-nav-gate-form");
    if (gateForm) {
      gateForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var surface = val("cat-gate-surface"), resource = val("cat-gate-resource");
        var verb = val("cat-gate-verb"), action = val("cat-gate-action");
        var scope = val("cat-gate-scope");
        if (!surface || !resource || (!verb && !action) || (verb && action)) {
          return setMsg(msg, "Give a surface, a resource, and exactly one of verb/action.", "error");
        }
        var body = { resource_key: resource };
        if (action) { body.action = action; } else { body.verb = verb; }
        // Blank means "leave the tier as it is" — the API treats a null scope as unchanged.
        if (scope) { body.scope = scope; }
        writeJson("PATCH", "/nav-gates/" + encodeURIComponent(surface), body).then(function (r) {
          if (!r.ok) return setMsg(msg, errorText(r, "Could not re-gate " + surface + "."), "error");
          setMsg(msg, surface + " re-gated.", "success");
          gateForm.reset();
          load();
        });
      });
    }

    var loaded = false;
    return { refresh: function () { if (!loaded) { loaded = true; load(); } } };
  }
})();
