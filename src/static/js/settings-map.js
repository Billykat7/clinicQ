/* The clinic profile's map picker (Issue 54).
 *
 * Leaflet, vendored under /static/vendor/leaflet/, on OpenStreetMap tiles. The pin writes the two
 * coordinate fields and the fields move the pin; the form saves the fields. So the map is a way of
 * choosing a point, and the server (PUT /api/v1/sites/{id}) decides whether that point is allowed.
 * Without Leaflet or tiles the coordinate fields still work on their own.
 *
 * "Find" asks the clinic's own geocoding route (POST /api/v1/sites/{id}/geocode), never a geocoder
 * directly from the browser, and offers the candidates for a person to choose.
 *
 * External file, no inline handlers or styles (CSP).
 */
(function () {
  'use strict';

  var TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
  var ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a> contributors';
  var ZOOM = 16;

  var root = document.getElementById('settings-map');
  var lat = document.getElementById('pf-lat');
  var lon = document.getElementById('pf-lon');
  if (!root || !lat || !lon) return;
  var siteId = root.getAttribute('data-site-id');
  var query = document.getElementById('pf-geocode');
  var go = document.getElementById('pf-geocode-go');
  var results = document.getElementById('pf-geocode-results');
  var map = null;
  var pin = null;

  function point() {
    return [Number(lat.value), Number(lon.value)];
  }

  function writeFields(latlng) {
    lat.value = latlng.lat.toFixed(6);
    lon.value = latlng.lng.toFixed(6);
  }

  function moveTo(latlng, zoom) {
    if (!map) return;
    pin.setLatLng(latlng);
    map.setView(latlng, zoom || map.getZoom());
  }

  if (window.L) {
    map = window.L.map('pf-map', { zoomControl: true, attributionControl: true }).setView(point(), ZOOM);
    map.attributionControl.setPrefix(false);
    window.L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(map);
    pin = window.L.marker(point(), { draggable: true, keyboard: true, title: 'Clinic location' }).addTo(map);
    pin.on('dragend', function () { writeFields(pin.getLatLng()); });
    map.on('click', function (event) {
      writeFields(event.latlng);
      moveTo(event.latlng);
    });
    [lat, lon].forEach(function (field) {
      field.addEventListener('change', function () {
        var value = point();
        if (!isNaN(value[0]) && !isNaN(value[1])) moveTo(window.L.latLng(value[0], value[1]));
      });
    });
  } else {
    document.getElementById('pf-map').hidden = true;
  }

  function lookUp() {
    var address = query.value.trim();
    if (address.length < 3) return;
    results.hidden = false;
    results.innerHTML = '<li class="hint">Looking up…</li>';
    fetch('/api/v1/sites/' + encodeURIComponent(siteId) + '/geocode', {
      method: 'POST',
      credentials: 'same-origin',
      headers: window.BKP.writeHeaders(),
      body: JSON.stringify({ address: address }),
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) { return { ok: response.ok, data: data }; });
      })
      .then(function (result) {
        results.innerHTML = '';
        var candidates = result.ok && result.data.candidates ? result.data.candidates : [];
        if (!result.ok || !candidates.length) {
          var none = document.createElement('li');
          none.className = 'hint';
          none.textContent = result.ok ? 'No match. Drag the pin instead.' : (typeof result.data.detail === 'string' ? result.data.detail : 'The address could not be looked up. Drag the pin instead.');
          results.appendChild(none);
          return;
        }
        candidates.forEach(function (candidate) {
          var item = document.createElement('li');
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'geocode-choice';
          button.textContent = candidate.label;
          button.addEventListener('click', function () {
            var latlng = { lat: candidate.location.latitude, lng: candidate.location.longitude };
            writeFields(latlng);
            if (window.L) moveTo(window.L.latLng(latlng.lat, latlng.lng), ZOOM);
            results.hidden = true;
          });
          item.appendChild(button);
          results.appendChild(item);
        });
      })
      .catch(function () {
        results.innerHTML = '<li class="hint">The clinic could not be reached. Drag the pin instead.</li>';
      });
  }

  go.addEventListener('click', lookUp);
  query.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      lookUp();
    }
  });
})();
