# Gemini reverse proxy

Google rejects Gemini calls from some hosting networks with `400 FAILED_PRECONDITION: User location is not
supported for the API use`, whatever country the account is in. The check uses the IP the request comes
from. A reverse proxy on a host Google accepts fixes it without any change to the app other than one setting:

```
# ~/docs-extractor/.env on the server
GEMINI_BASE_URL=https://<proxy-host>/v1beta/openai
```

Then `bash deploy/vps.sh`. The app refuses a non-local `http://` URL, because the API key goes in a header.
The proxy never holds the key; it forwards the request as is.

Two ways to run the proxy:

* `worker.js`: a Cloudflare Worker (free plan is enough). Deploy it in your own Cloudflare account. Whether
  Google accepts the Cloudflare address that the call leaves from depends on which edge location handles it,
  so test it once before relying on it.
* `nginx.conf.example`: nginx on any VPS in a supported country, with a TLS certificate.

After deploying the Worker, open its address in a browser: it must show `gemini-proxy ok`. Any other page
(for example `error code: 1042`) means the code on Cloudflare is not `worker.js` or was not deployed.

Test with one request while billing is off on the Google project (free tier only):

```
curl -s "$GEMINI_BASE_URL/models" -H "Authorization: Bearer $GEMINI_API_KEY" | head -c 300
```

An answer listing models means the proxy works; the same location error means Google still rejects that exit.
