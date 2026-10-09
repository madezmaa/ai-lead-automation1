/* Deployment configuration for the demo frontend (no build step required).
 *
 * The default (empty object) auto-detects the backend:
 *   - served by the API itself (:8000) -> same origin as the page
 *   - a separate local server / file:// -> http://localhost:8000
 *   - any other host (production)       -> same origin as the page
 *
 * This means a production deployment where the backend serves this demo needs
 * no changes here at all. Override it only when the API lives elsewhere, e.g.:
 *   window.LEAD_DEMO_CONFIG = { apiBaseUrl: "https://your-api.onrender.com" };
 * or force a same-origin call:
 *   window.LEAD_DEMO_CONFIG = { apiBaseUrl: "" };
 */
window.LEAD_DEMO_CONFIG = {};
