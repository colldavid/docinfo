# DocInfo Send Screening (Outlook add-in)

## What this is

This is an Outlook **Smart Alerts** add-in. When a consultant clicks Send on a message
with attachments, our handler runs *before* the mail goes out: it gathers the recipients
(to + cc + bcc) and the attachment bytes, POSTs them to DocInfo's `/screen` endpoint, and
if the server returns a `warn` verdict it shows Outlook's built-in warning dialog with the
server's message. The single most important property of this add-in is that it **fails
open**: if the DocInfo server is unreachable, slow (we give up after 8 seconds), returns
an error or a non-200, sends back something we can't parse, or if literally anything in
the handler throws, the message is allowed to send. A screening outage must never stop the
firm's email. The only thing that ever interrupts a send is an explicit, well-formed
`warn` verdict — and because we run in `PromptUser` mode, even then the user always has a
**Send Anyway** button. This add-in informs; it never blocks.

## Configuration

Three edits, all find-and-replace:

1. **`screen.js`** — set the two constants at the top of the file:
   - `APP_BASE_URL` — your DocInfo server's base URL, no trailing slash, e.g.
     `https://docinfo.yourfirm.com`.
   - `SCREEN_API_KEY` — sent as the `X-Screen-Key` header on every request. Get this
     value from the **DocInfo Settings page** (`screen_api_key`).
2. **`manifest.xml`** — every URL in the manifest points at the placeholder host
   `https://YOUR-DOCINFO-SERVER`. Find-replace that one string with your real host and
   the whole file (source location, JS runtime, icons, app domain, support URL) is
   configured at once. Don't change the paths after the host — they must stay
   `/addin/screen.html` and `/addin/screen.js` to match what the server serves.
3. **`<Id>`** — the manifest ships with a generated GUID
   (`979c9952-6fc0-4a69-96d4-8bfc0c6c7907`). Keep it as-is. Only generate a new one if
   you need to run two independent copies of this add-in side by side in one mailbox.

## HTTPS is mandatory

Outlook will not load add-in code over plain `http`, and the manifest validator rejects
non-HTTPS URLs. `http://localhost:3000` will not work. For local testing you need either a
dev certificate (`npx office-addin-dev-certs install`, which is what the Yeoman generator
uses) or an HTTPS tunnel such as `ngrok`/`dev tunnels` pointed at your local FastAPI
server. For anything real, deploy to the actual HTTPS host.

## Sideloading (Outlook on the web)

1. Open Outlook on the web and go to **Get Add-ins** (gear menu, or the "..." overflow in
   a message).
2. Choose **My add-ins**.
3. Under *Custom Addins*, choose **Add a custom add-in → Add from file**.
4. Select `addin/manifest.xml` and confirm the trust prompt.
5. Compose a message, attach a file, and hit Send to exercise the handler.

Note: **many corporate tenants block sideloading** entirely. If "Add a custom add-in" is
missing or errors out, your tenant admin has disabled it, and the add-in must instead be
deployed centrally through the Microsoft 365 admin center (Integrated Apps → Upload custom
app). That is an admin action, not something a consultant can self-serve.

## Validation

From the repo root:

```
npx --yes office-addin-manifest validate addin/manifest.xml
```

This was run against the current manifest and reported **"The manifest is valid."** with
no errors and no warnings.

## Known limitations

- **Not verified in a live Outlook.** No tenant or Outlook client was available during
  development. The manifest is schema-valid and the JavaScript follows Microsoft's
  documented Smart Alerts patterns, but no send has ever actually been screened
  end-to-end. Treat the first real sideload as a test, not a rollout.
- **No icon files.** The manifest references `icon-16/32/64/80/128.png` under `/addin/`,
  but we don't ship them. Sideloading generally tolerates missing icons (they render as
  blanks or placeholders) — though this is unverified in our case. Real PNGs must be added
  before any store or broad tenant distribution.
- **PromptUser only.** The user can always override the warning. This is a deliberate
  decision, not a gap: we are not building a DLP hard block.
- **Attachments only.** Messages with no attachments are allowed instantly without
  contacting the server. Message body text is never screened.
- **Some attachments are skipped:** inline images, anything over ~10MB, and any attachment
  whose content isn't returned as Base64 (cloud/`Url` links, attached `.eml` messages,
  calendar items). Skipped attachments are simply not screened, consistent with the
  fail-open philosophy.
- **Desktop form factor only.** The manifest declares `DesktopFormFactor`, which covers
  Outlook on the web, Windows, and Mac. Outlook mobile is not targeted.
