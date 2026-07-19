import { useEffect, useRef, useState } from "react";
import { loadGoogleMaps } from "../googleMapsLoader.js";
import { buildJourneyStops, buildJourneyLegs, midpoint } from "../geo.js";

const API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY ?? "";

const STOP_COLOR = {
  origin: "#ff6600",
  hub: "#4d148c",
  destination: "#16a34a",
};

const ROUTE_COLOR = "#4d148c";

// Light, minimal styling on top of Google's standard default map — just
// trims POI/transit clutter so the route reads clearly.
const LIGHT_MAP_STYLE = [
  { featureType: "poi", elementType: "labels", stylers: [{ visibility: "off" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
];

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Small OverlayView subclass so each stop keeps a permanently-visible
// title/subtitle label (Marker.label only supports a single short string).
function createStopLabel(maps, position, title, subtitle) {
  class StopLabel extends maps.OverlayView {
    onAdd() {
      const div = document.createElement("div");
      div.className = "journey-map__stop-label";
      div.innerHTML =
        `<div class="journey-map__stop-title">${escapeHtml(title)}</div>` +
        `<div class="journey-map__stop-subtitle">${escapeHtml(subtitle)}</div>`;
      this.div = div;
      this.getPanes().overlayMouseTarget.appendChild(div);
    }
    draw() {
      const proj = this.getProjection();
      if (!proj || !this.div) return;
      const point = proj.fromLatLngToDivPixel(new maps.LatLng(position));
      this.div.style.left = `${point.x}px`;
      this.div.style.top = `${point.y + 10}px`;
    }
    onRemove() {
      this.div?.remove();
      this.div = null;
    }
  }
  return new StopLabel();
}

export default function JourneyMap({ detail }) {
  const containerRef = useRef(null);
  const overlaysRef = useRef([]);
  const resizeObserverRef = useRef(null);
  const [status, setStatus] = useState(API_KEY ? "loading" : "missing-key");

  useEffect(() => {
    if (!API_KEY || !detail) return;
    let cancelled = false;
    setStatus("loading");

    loadGoogleMaps(API_KEY)
      .then((maps) => {
        if (cancelled || !containerRef.current) return;

        overlaysRef.current.forEach((o) => o.setMap?.(null));
        overlaysRef.current = [];

        const stops = buildJourneyStops(detail);
        if (stops.length < 2) {
          setStatus("no-route");
          return;
        }
        const legs = buildJourneyLegs(stops);

        const map = new maps.Map(containerRef.current, {
          center: stops[0].coord,
          zoom: 4,
          disableDefaultUI: true,
          zoomControl: true,
          styles: LIGHT_MAP_STYLE,
        });

        const bounds = new maps.LatLngBounds();
        stops.forEach((s) => bounds.extend(s.coord));

        stops.forEach((stop) => {
          const marker = new maps.Marker({
            position: stop.coord,
            map,
            zIndex: 10,
            icon: {
              path: maps.SymbolPath.CIRCLE,
              scale: stop.kind === "hub" ? 6.5 : 9,
              fillColor: STOP_COLOR[stop.kind],
              fillOpacity: 1,
              strokeColor: "#ffffff",
              strokeWeight: 2,
            },
          });
          const label = createStopLabel(maps, stop.coord, stop.title, stop.subtitle);
          label.setMap(map);
          overlaysRef.current.push(marker, label);
        });

        legs.forEach((leg) => {
          const isFlight = leg.mode === "flight";
          const line = new maps.Polyline({
            path: [leg.from.coord, leg.to.coord],
            map,
            geodesic: true,
            strokeColor: ROUTE_COLOR,
            strokeOpacity: isFlight ? 0 : 0.85,
            strokeWeight: isFlight ? 0 : 3,
            icons: isFlight
              ? [{
                  icon: { path: "M 0,-1 0,1", strokeOpacity: 1, strokeColor: ROUTE_COLOR, scale: 3 },
                  offset: "0",
                  repeat: "14px",
                }]
              : [],
            zIndex: 1,
          });

          const iconMarker = new maps.Marker({
            position: midpoint(leg.from.coord, leg.to.coord),
            map,
            zIndex: 5,
            label: { text: isFlight ? "✈️" : "🚚", fontSize: "16px" },
            icon: { path: maps.SymbolPath.CIRCLE, scale: 0, fillOpacity: 0, strokeOpacity: 0 },
          });

          overlaysRef.current.push(line, iconMarker);
        });

        map.fitBounds(bounds, 20);
        setStatus("ready");

        // Maps doesn't notice container size changes on its own (e.g. the
        // drawer narrowing at small viewports) — resync so tiles/markers
        // don't go stale.
        const resizeObserver = new ResizeObserver(() => {
          maps.event.trigger(map, "resize");
          map.fitBounds(bounds, 20);
        });
        resizeObserver.observe(containerRef.current);
        resizeObserverRef.current = resizeObserver;
      })
      .catch((err) => {
        console.error("[JourneyMap] failed to render:", err);
        if (!cancelled) setStatus("error");
      });

    return () => {
      cancelled = true;
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
    };
  }, [detail]);

  if (!API_KEY) {
    return (
      <div className="journey-map journey-map--placeholder">
        Journey map disabled — set <code>VITE_GOOGLE_MAPS_API_KEY</code> to enable it.
      </div>
    );
  }

  return (
    <div className="journey-map">
      <div ref={containerRef} className="journey-map__canvas" />
      <div className="journey-map__legend">
        <span><i className="journey-map__legend-swatch journey-map__legend-swatch--dash" />Flight leg</span>
        <span><i className="journey-map__legend-swatch journey-map__legend-swatch--solid" />Truck leg</span>
      </div>
      {status === "loading" && <div className="journey-map__state">Loading map…</div>}
      {status === "error" && <div className="journey-map__state journey-map__state--error">Couldn't load Google Maps.</div>}
      {status === "no-route" && <div className="journey-map__state">Not enough location data to plot a route.</div>}
    </div>
  );
}
