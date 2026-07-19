let loadPromise = null;

/** Loads the Google Maps JS API script once and caches the promise, so
 * re-opening the shipment drawer never injects a second <script> tag. */
export function loadGoogleMaps(apiKey) {
  if (!apiKey) return Promise.reject(new Error("missing Google Maps API key"));
  if (window.google?.maps) return Promise.resolve(window.google.maps);
  if (loadPromise) return loadPromise;

  loadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    // No `loading=async` param: that opts into Google's newer bootstrap loader,
    // which only exposes google.maps.importLibrary() and leaves google.maps.Map
    // etc. undefined until each library is dynamically imported. The classic
    // load populates the full google.maps namespace synchronously on script load.
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}`;
    script.async = true;
    script.onload = () => {
      if (window.google?.maps) resolve(window.google.maps);
      else reject(new Error("Google Maps script loaded but window.google.maps is missing"));
    };
    script.onerror = () => {
      loadPromise = null;
      reject(new Error("Failed to load the Google Maps script"));
    };
    document.head.appendChild(script);
  });

  return loadPromise;
}
