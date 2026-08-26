/*
 * DocInfo Send Screening - Outlook Smart Alerts handler.
 *
 * Runs when the user hits Send, BEFORE the message leaves the outbox. It collects
 * recipients + attachments, asks DocInfo's /screen endpoint for a verdict, and either
 * lets the send proceed or shows Outlook's built-in warning dialog.
 *
 * DESIGN RULE - FAIL OPEN. A screening outage must never stop the firm's email.
 * Every failure path (network down, timeout, non-200, malformed JSON, unexpected
 * throw, missing Office API) ends in event.completed({ allowEvent: true }). The only
 * way a send is ever interrupted is an explicit, well-formed "warn" verdict from the
 * server. See the single try/catch in onMessageSendHandler for the guarantee.
 */

// ---------------------------------------------------------------------------
// CONFIGURATION - set these two before deploying. See README.md.
// ---------------------------------------------------------------------------

// Base URL of your DocInfo server. No trailing slash. Must be HTTPS (Outlook
// refuses to load add-in code or let it call out over plain http).
const APP_BASE_URL = "https://YOUR-DOCINFO-SERVER";

// Screening API key, sent as the X-Screen-Key header. Copy it from the DocInfo
// Settings page.
const SCREEN_API_KEY = "YOUR-SCREEN-API-KEY";

// ---------------------------------------------------------------------------
// Tuning constants
// ---------------------------------------------------------------------------

// Hard ceiling on the whole /screen round trip. Outlook shows a spinner while the
// handler runs, so a hung request would freeze the user's Send. At 8s we give up
// and allow the send.
const REQUEST_TIMEOUT_MS = 8000;

// Skip attachments over ~10MB. Base64 inflates payloads ~33%, and a huge upload
// would blow the timeout budget for every other attachment on the message.
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

// Ceiling on any single Office.js call. Some Outlook runtimes have been observed
// never invoking a callback at all; without this, the handler hangs forever and
// the user stares at an eternal "processing" dialog.
const OFFICE_CALL_TIMEOUT_MS = 5000;

// Absolute ceiling on the whole handler. Belt-and-braces guarantee that the
// send dialog can never hang: when it fires, the send is allowed (fail-open).
const HANDLER_WATCHDOG_MS = 20000;

// Fire-and-forget breadcrumb to the server's access log, so handler progress is
// visible in the uvicorn terminal without Outlook dev tools. Never awaited,
// never allowed to throw.
function trace(stage) {
  try {
    fetch(APP_BASE_URL + "/health?stage=" + encodeURIComponent(stage)).catch(function () {});
  } catch (ignored) {}
}

// Resolves with `fallback` if `promise` doesn't settle within `ms`.
function withTimeout(promise, ms, fallback) {
  return Promise.race([
    promise,
    new Promise(function (resolve) {
      setTimeout(function () { resolve(fallback); }, ms);
    })
  ]);
}

// ---------------------------------------------------------------------------
// Promise wrappers around Office.js callback APIs, so the handler reads linearly.
// Each RESOLVES on failure instead of rejecting, returning a caller-supplied
// fallback. Rationale: a failed recipient/attachment lookup must not abort
// screening - we screen with what we could read, which keeps us fail-open.
// ---------------------------------------------------------------------------

function asyncResultToValue(resolve, fallback) {
  return function (asyncResult) {
    if (asyncResult.status === Office.AsyncResultStatus.Succeeded && asyncResult.value != null) {
      resolve(asyncResult.value);
    } else {
      resolve(fallback);
    }
  };
}

// Reads one recipient field (to/cc/bcc). Returns [] if the field is unavailable.
function getRecipients(field) {
  return new Promise(function (resolve) {
    if (!field || typeof field.getAsync !== "function") {
      resolve([]);
      return;
    }
    field.getAsync(asyncResultToValue(resolve, []));
  });
}

// Lists attachments on the draft. Returns [] if unavailable.
function getAttachments(item) {
  return new Promise(function (resolve) {
    item.getAttachmentsAsync(asyncResultToValue(resolve, []));
  });
}

// Fetches one attachment's content. Returns null if unavailable.
function getAttachmentContent(item, attachmentId) {
  return new Promise(function (resolve) {
    item.getAttachmentContentAsync(attachmentId, asyncResultToValue(resolve, null));
  });
}

// ---------------------------------------------------------------------------
// Collection helpers
// ---------------------------------------------------------------------------

// All recipients across to + cc + bcc, de-duplicated.
async function collectRecipients(item) {
  const fields = await Promise.all([
    getRecipients(item.to),
    getRecipients(item.cc),
    getRecipients(item.bcc)
  ]);

  const seen = Object.create(null);
  const recipients = [];
  fields.forEach(function (list) {
    list.forEach(function (entry) {
      // EmailAddressDetails.emailAddress; guard against odd/empty entries.
      const address = entry && entry.emailAddress;
      if (address && !seen[address]) {
        seen[address] = true;
        recipients.push(address);
      }
    });
  });
  return recipients;
}

// Base64 payloads for the real, screenable attachments on the draft.
//
// Three kinds are skipped on purpose, all consistent with fail-open: inline images
// (signature logos, pasted screenshots - noise, not deliverables), oversized files,
// and any attachment whose content does not come back as Base64. That last case
// covers cloud/Url links, .eml items and calendar items: we have no bytes to screen,
// so we let them through rather than guessing.
async function collectAttachments(item) {
  // Every Office.js call is capped: a callback that never fires must degrade to
  // "couldn't read it" (fail-open), never to an eternal spinner.
  const listed = await withTimeout(getAttachments(item), OFFICE_CALL_TIMEOUT_MS, []);
  trace("attachments-listed-" + listed.length);

  const candidates = listed.filter(function (attachment) {
    return !attachment.isInline && attachment.size <= MAX_ATTACHMENT_BYTES;
  });

  const fetched = await Promise.all(
    candidates.map(async function (attachment) {
      const result = await withTimeout(
        getAttachmentContent(item, attachment.id), OFFICE_CALL_TIMEOUT_MS, null
      );
      if (!result || result.format !== Office.MailboxEnums.AttachmentContentFormat.Base64) {
        return null;
      }
      return { filename: attachment.name, content_base64: result.content };
    })
  );
  trace("attachments-content-read");

  return fetched.filter(function (entry) {
    return entry !== null;
  });
}

// ---------------------------------------------------------------------------
// Server call
// ---------------------------------------------------------------------------

// POSTs to /screen and returns the parsed verdict, or null on ANY problem
// (timeout, transport error, non-200, unparseable body). null means "allow".
async function requestVerdict(payload) {
  // AbortController may not exist in every add-in runtime; a missing abort just
  // means we rely on the handler watchdog instead of a precise fetch timeout.
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = controller
    ? setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS)
    : null;

  try {
    const options = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Screen-Key": SCREEN_API_KEY
      },
      body: JSON.stringify(payload)
    };
    if (controller) {
      options.signal = controller.signal;
    }
    const response = await fetch(APP_BASE_URL + "/screen", options);

    // Any non-200 (auth failure, 500, rate limit) is treated as "no opinion".
    if (!response.ok) {
      return null;
    }
    return await response.json();
  } catch (error) {
    // Includes the AbortError raised by the timeout above.
    return null;
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

async function onMessageSendHandler(event) {
  // Single completion point. Guards against double event.completed calls (which
  // some runtimes treat as an error) and backs the watchdog below.
  let done = false;
  let watchdog = null;
  function finish(result, stage) {
    if (done) { return; }
    done = true;
    if (watchdog) { clearTimeout(watchdog); }
    trace("finish-" + stage);
    try { event.completed(result); } catch (ignored) {}
  }

  // Absolute guarantee: whatever hangs, the dialog resolves and mail flows.
  watchdog = setTimeout(function () {
    finish({ allowEvent: true }, "watchdog");
  }, HANDLER_WATCHDOG_MS);

  try {
    trace("handler-start");
    const item = Office.context.mailbox.item;

    // D1: attachments-only screening. No attachments means nothing to screen, so
    // skip the network call entirely and keep Send instant for ordinary email.
    const attachments = await collectAttachments(item);
    if (attachments.length === 0) {
      finish({ allowEvent: true }, "no-attachments");
      return;
    }

    const recipients = await collectRecipients(item);
    trace("recipients-" + recipients.length);
    const verdict = await requestVerdict({ recipients: recipients, attachments: attachments });

    // Warn ONLY on an explicit "warn" verdict carrying a message to display.
    // Anything else - "allow", an unknown verdict, a null result from a failed
    // call - falls through to allowing the send.
    if (verdict && verdict.verdict === "warn" && verdict.message) {
      finish({ allowEvent: false, errorMessage: verdict.message }, "warn");
      return;
    }

    finish({ allowEvent: true }, verdict ? "allow" : "fetch-failed");
  } catch (error) {
    // Last line of defence. If anything above threw unexpectedly, the user's mail
    // still goes out - we never hold email hostage to a bug in this add-in.
    finish({ allowEvent: true }, "error");
  }
}

// Load-time breadcrumbs: prove in the server log that (1) this script executed
// and (2) office.js completed its host handshake. Their absence on a test send
// distinguishes "script never ran" from "office.js never initialized" from
// "handler never dispatched".
trace("script-loaded");

// Kick the Office.js initialization handshake. In browser runtimes (Outlook on
// the web / new Outlook), calling Office.onReady is what triggers office.js to
// finish initializing against the host — without SOME call to it, event
// dispatch may never reach the handler and the send dialog spins forever.
// No logic belongs inside the callback (it never fires in classic Outlook's
// JS-only runtime); the call itself is the point.
if (typeof Office !== "undefined" && Office.onReady) {
  Office.onReady(function () {
    trace("office-ready");
  });
}

// Maps the FunctionName in the manifest's LaunchEvent to this function. Required on
// every platform.
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
