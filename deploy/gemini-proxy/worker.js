// Cloudflare Worker: forwards /v1beta/* to the Gemini API, so calls leave from a Cloudflare edge instead
// of the server's own IP. It stores no key; the caller sends its own Authorization header.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/v1beta/")) return new Response("not found", { status: 404 });
    url.protocol = "https:";
    url.hostname = "generativelanguage.googleapis.com";
    url.port = "";
    return fetch(new Request(url, request));
  },
};
