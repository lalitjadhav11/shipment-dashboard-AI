// Coordinate lookup for the fixed city/hub set the seeder draws from
// (seeder/03_generate_phase1_data.py: DOMESTIC_CITIES, INTERNATIONAL_CITIES,
// DOMESTIC_HUBS, CONNECTING_HUBS_INTL). The dataset has no lat/lng columns,
// but that set is small and fixed, so a static lookup avoids a geocoding
// API call per shipment.

const CITY_COORDS = {
  // domestic
  "San Francisco": { lat: 37.7749, lng: -122.4194 },
  "New York": { lat: 40.7128, lng: -74.0060 },
  "Austin": { lat: 30.2672, lng: -97.7431 },
  "Chicago": { lat: 41.8781, lng: -87.6298 },
  "Atlanta": { lat: 33.7490, lng: -84.3880 },
  "Seattle": { lat: 47.6062, lng: -122.3321 },
  "Pasadena": { lat: 34.1478, lng: -118.1445 },
  "Washington": { lat: 38.9072, lng: -77.0369 },
  "Miami": { lat: 25.7617, lng: -80.1918 },
  "Kansas City": { lat: 39.0997, lng: -94.5786 },
  // international
  "Berlin": { lat: 52.5200, lng: 13.4050 },
  "London": { lat: 51.5074, lng: -0.1278 },
  "Paris": { lat: 48.8566, lng: 2.3522 },
  "Tokyo": { lat: 35.6762, lng: 139.6503 },
  "Hong Kong": { lat: 22.3193, lng: 114.1694 },
  "Toronto": { lat: 43.6532, lng: -79.3832 },
  "Melbourne": { lat: -37.8136, lng: 144.9631 },
  "Singapore": { lat: 1.3521, lng: 103.8198 },
  "Sao Paulo": { lat: -23.5505, lng: -46.6333 },
  "Mumbai": { lat: 19.0760, lng: 72.8777 },
};

// Keyed on the exact hub label strings the seeder writes into
// tracking_events.location (DOMESTIC_HUBS / CONNECTING_HUBS_INTL).
const HUB_COORDS = {
  "Memphis SuperHub, TN, US": { lat: 35.0424, lng: -89.9767 },
  "Indianapolis Hub, IN, US": { lat: 39.7173, lng: -86.2944 },
  "Louisville Hub, KY, US": { lat: 38.1744, lng: -85.7360 },
  "Oakland Sort Facility, CA, US": { lat: 37.7126, lng: -122.2197 },
  "Frankfurt Hub, DE": { lat: 50.0379, lng: 8.5622 },
  "Memphis Gateway Hub, US": { lat: 35.0424, lng: -89.9767 },
  "Hong Kong Gateway Hub, HK": { lat: 22.3080, lng: 113.9185 },
  "Dubai Gateway Hub, AE": { lat: 25.2532, lng: 55.3657 },
};

const FLIGHT_DISTANCE_KM = 400;

function toRad(deg) {
  return (deg * Math.PI) / 180;
}

function haversineKm(a, b) {
  const R = 6371;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function locationTitle(loc) {
  if (!loc) return "";
  return loc.state ? `${loc.city}, ${loc.state}` : `${loc.city}, ${loc.country_code}`;
}

/** Ordered list of {key, title, subtitle, coord, kind} stops for a shipment's
 * journey map — origin, each distinct hub the timeline actually passed
 * through (in chronological order), then the destination. */
export function buildJourneyStops(detail) {
  if (!detail) return [];
  const { src_loc, dest_loc, journey_timeline = [] } = detail;
  const stops = [];

  const originCoord = src_loc && CITY_COORDS[src_loc.city];
  if (originCoord) {
    stops.push({
      key: "origin",
      title: locationTitle(src_loc),
      subtitle: "Origin · Pickup",
      coord: originCoord,
      kind: "origin",
    });
  }

  const seenHubs = new Set();
  for (const ev of journey_timeline) {
    const loc = ev.location;
    const hubCoord = loc && HUB_COORDS[loc];
    if (hubCoord && !seenHubs.has(loc)) {
      seenHubs.add(loc);
      stops.push({
        key: loc,
        title: loc.split(",")[0],
        subtitle: "Hub",
        coord: hubCoord,
        kind: "hub",
      });
    }
  }

  const destCoord = dest_loc && CITY_COORDS[dest_loc.city];
  if (destCoord) {
    stops.push({
      key: "destination",
      title: locationTitle(dest_loc),
      subtitle: "Destination",
      coord: destCoord,
      kind: "destination",
    });
  }

  return stops;
}

/** Consecutive-stop legs with a flight/truck mode assigned by great-circle
 * distance — long hub-to-hub hops fly, short hops (incl. final-mile) drive. */
export function buildJourneyLegs(stops) {
  const legs = [];
  for (let i = 0; i < stops.length - 1; i++) {
    const from = stops[i];
    const to = stops[i + 1];
    const isLast = i === stops.length - 2;
    const km = haversineKm(from.coord, to.coord);
    const mode = km > FLIGHT_DISTANCE_KM ? "flight" : "truck";
    const label = mode === "flight" ? "Flight · Line-haul" : isLast ? "Truck · Final mile" : "Truck · Ground";
    legs.push({ from, to, mode, label, km });
  }
  return legs;
}

export function midpoint(a, b) {
  return { lat: (a.lat + b.lat) / 2, lng: (a.lng + b.lng) / 2 };
}
