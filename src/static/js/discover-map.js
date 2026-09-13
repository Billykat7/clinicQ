/**
 * The discovery map (Issue 33): the clinics in the list, as pins on OpenStreetMap tiles.
 *
 * Four rules shape this file:
 *
 *   1. The map shows exactly the clinics in the filtered list. It never asks the server for
 *      anything: every pin is read from a clinic card in #results (its data-lat, data-lon and
 *      data-shape), and it is redrawn after every htmx swap of that list. There is no second query
 *      to disagree with the first.
 *   2. The map is an enhancement. Leaflet (vendored under /static/vendor/leaflet/) is loaded only
 *      when the map is first shown. If the tiles fail, or none has arrived within TILE_TIMEOUT_MS,
 *      the map gives way to the list with a sentence saying why, never a blank grey area.
 *   3. Pins differ by shape as well as colour: a square for a public clinic and a diamond for a
 *      private one, as on the badges. Nearby pins are clustered, and a cluster zooms in when chosen.
 *   4. Choosing a pin opens a preview that is a copy of that clinic's own card, with Directions,
 *      so the preview cannot say anything the list does not.
 *
 * Switching between list and map keeps the filters (they are the same form) and the list's scroll
 * position. The OpenStreetMap attribution is the tile licence's requirement, not decoration.
 *
 * External file, no inline handlers, no style attributes built into markup (CSP).
 */
(function () {
  "use strict";

  var TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  var ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a> contributors';
  var TILE_TIMEOUT_MS = 8000;
  var TILE_ERRORS_BEFORE_FALLBACK = 3;
  var CLUSTER_PIXELS = 44;
  var PIN_SIZE = 26;
  var SHAPES = {
    square: '<svg viewBox="0 0 12 12" aria-hidden="true"><rect x="1.5" y="1.5" width="9" height="9" rx="1"/></svg>',
    diamond: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 .9 11.1 6 6 11.1.9 6z"/></svg>',
    origin: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.5"/></svg>'
  };

  var section = document.getElementById("discover-map");
  var form = document.getElementById("filters");
  var results = document.getElementById("results");
  var wrapper = document.getElementById("discover-results");
  if (!section || !form || !results || !wrapper) return;

  var canvas = document.getElementById("map-canvas");
  var statusLine = document.getElementById("map-status");
  var preview = document.getElementById("map-preview");
  var fallbackLine = document.getElementById("map-fallback");

  var map = null;
  var pinLayer = null;
  var leafletPromise = null;
  var tileArrived = false;
  var tileErrors = 0;
  var failed = false;
  var listScroll = 0;

  function chosenView() {
    var checked = form.querySelector('input[name="view"]:checked');
    return checked ? checked.value : "list";
  }

  /** Load Leaflet's stylesheet and script from this origin, once. */
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise(function (resolve, reject) {
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "/static/vendor/leaflet/leaflet.css";
      document.head.appendChild(css);
      var script = document.createElement("script");
      script.src = "/static/vendor/leaflet/leaflet.js";
      script.onload = function () { resolve(); };
      script.onerror = function () { reject(new Error("leaflet")); };
      document.head.appendChild(script);
    });
    return leafletPromise;
  }

  /** Give way to the list, saying why. Used for a tile failure, a slow connection or no Leaflet. */
  function fallBack(reason) {
    if (failed) return;
    failed = true;
    section.hidden = true;
    wrapper.classList.remove("is-map");
    fallbackLine.textContent =
      reason + " The list below shows the same clinics, and Directions on each one still works.";
    fallbackLine.hidden = false;
    if (window.BKP && typeof window.BKP.toast === "function") {
      window.BKP.toast("The map could not load, so the list is shown instead.", { kind: "error" });
    }
  }

  function cards() {
    return Array.prototype.slice.call(results.querySelectorAll(".clinic-card[data-lat]"));
  }

  function pinIcon(shape, label) {
    return window.L.divIcon({
      className: "map-pin map-pin-" + shape,
      html: SHAPES[shape] || SHAPES.square,
      iconSize: [PIN_SIZE, PIN_SIZE],
      iconAnchor: [PIN_SIZE / 2, PIN_SIZE / 2]
    });
  }

  function clusterIcon(count) {
    var size = count < 10 ? 34 : 40;
    return window.L.divIcon({
      className: "map-cluster",
      html: String(count),
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2]
    });
  }

  /** A copy of the clinic's own card, plus Directions: the preview can only say what the list says. */
  function showPreview(card) {
    var copy = card.cloneNode(true);
    copy.removeAttribute("data-clinic");
    Array.prototype.forEach.call(copy.querySelectorAll("[id]"), function (el) { el.removeAttribute("id"); });
    var article = copy.querySelector("article");
    if (article) article.removeAttribute("aria-labelledby");
    var list = document.createElement("ul");
    list.className = "clinic-list";
    list.appendChild(copy);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn-quiet btn-sm map-preview-close";
    close.textContent = "Close the preview";
    close.addEventListener("click", function () {
      preview.hidden = true;
      preview.textContent = "";
    });
    preview.textContent = "";
    preview.appendChild(list);
    preview.appendChild(close);
    preview.hidden = false;
    var heading = copy.querySelector("h3 a, h3");
    if (heading && heading.focus) heading.focus({ preventScroll: true });
  }

  /** Group pins that would overlap at this zoom: a simple, dependency-free grid clustering. */
  function drawPins() {
    if (!map) return;
    var L = window.L;
    pinLayer.clearLayers();
    var buckets = {};
    var shown = cards();
    shown.forEach(function (card) {
      var latlng = L.latLng(Number(card.dataset.lat), Number(card.dataset.lon));
      var point = map.project(latlng, map.getZoom());
      var key = Math.floor(point.x / CLUSTER_PIXELS) + ":" + Math.floor(point.y / CLUSTER_PIXELS);
      (buckets[key] = buckets[key] || []).push({ card: card, latlng: latlng });
    });
    Object.keys(buckets).forEach(function (key) {
      var group = buckets[key];
      if (group.length === 1) {
        var only = group[0];
        var marker = L.marker(only.latlng, {
          icon: pinIcon(only.card.dataset.shape),
          title: only.card.dataset.name + " (" + only.card.dataset.sectorLabel + ")",
          alt: only.card.dataset.name + ", " + only.card.dataset.sectorLabel,
          keyboard: true,
          riseOnHover: true
        });
        marker.on("click", function () { showPreview(only.card); });
        pinLayer.addLayer(marker);
        return;
      }
      var bounds = L.latLngBounds(group.map(function (item) { return item.latlng; }));
      var cluster = L.marker(bounds.getCenter(), {
        icon: clusterIcon(group.length),
        title: group.length + " clinics here: zoom in",
        alt: group.length + " clinics here",
        keyboard: true
      });
      cluster.on("click", function () { map.fitBounds(bounds.pad(0.5), { maxZoom: 17 }); });
      pinLayer.addLayer(cluster);
    });
    statusLine.textContent =
      shown.length === 1
        ? "1 clinic on the map, the same one as in the list."
        : shown.length + " clinics on the map, the same ones as in the list.";
  }

  function fitToList() {
    var L = window.L;
    var points = cards().map(function (card) {
      return L.latLng(Number(card.dataset.lat), Number(card.dataset.lon));
    });
    if (section.dataset.originLat) {
      points.push(L.latLng(Number(section.dataset.originLat), Number(section.dataset.originLon)));
    }
    if (points.length === 1) map.setView(points[0], 14);
    else if (points.length) map.fitBounds(L.latLngBounds(points).pad(0.15), { maxZoom: 16 });
  }

  function ensureMap() {
    if (map) return Promise.resolve();
    return loadLeaflet().then(function () {
      var L = window.L;
      map = L.map(canvas, { zoomControl: true, attributionControl: true });
      map.attributionControl.setPrefix(false);
      var tiles = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION });
      tiles.on("tileload", function () { tileArrived = true; });
      tiles.on("tileerror", function () {
        tileErrors += 1;
        if (!tileArrived && tileErrors >= TILE_ERRORS_BEFORE_FALLBACK) {
          fallBack("The map tiles could not be loaded.");
        }
      });
      tiles.addTo(map);
      pinLayer = L.layerGroup().addTo(map);
      if (section.dataset.originLat) {
        L.marker([Number(section.dataset.originLat), Number(section.dataset.originLon)], {
          icon: pinIcon("origin"),
          title: section.dataset.originLabel,
          alt: section.dataset.originLabel,
          keyboard: false,
          interactive: false
        }).addTo(map);
      }
      map.on("zoomend", drawPins);
      fitToList();
      window.setTimeout(function () {
        if (!tileArrived) fallBack("The map is taking too long to load on this connection.");
      }, TILE_TIMEOUT_MS);
    });
  }

  /** Put the list back where the patient left it: now, and again once the swap has settled. */
  var restorePending = false;
  function restoreListScroll() {
    if (!listScroll) return;
    restorePending = true;
    window.requestAnimationFrame(function () { window.scrollTo(0, listScroll); });
  }

  function show(view) {
    var wantMap = view === "map" && !failed;
    if (wantMap && section.hidden) listScroll = window.scrollY;
    section.hidden = !wantMap;
    wrapper.classList.toggle("is-map", wantMap);
    if (!wantMap) {
      preview.hidden = true;
      restoreListScroll();
      return;
    }
    ensureMap()
      .then(function () {
        map.invalidateSize();
        drawPins();
      })
      .catch(function () { fallBack("The map could not be started."); });
  }

  form.addEventListener("change", function (event) {
    if (event.target && event.target.name === "view") show(chosenView());
  });

  document.body.addEventListener("htmx:afterSettle", function (event) {
    if (event.detail.target !== results || !restorePending) return;
    restorePending = false;
    window.scrollTo(0, listScroll);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target !== results || section.hidden || !map) return;
    preview.hidden = true;
    fitToList();
    drawPins();
  });

  if (chosenView() === "map") show("map");
})();
