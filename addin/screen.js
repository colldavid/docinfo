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
  const listed = await getAttachments(item);

  const candidates = listed.filter(function (attachment) {
    return !attachment.isInline && attachment.size <= MAX_ATTACHMENT_BYTES;
  });

  const fetched = await Promise.all(
    candidates.map(async function (attachment) {
      const result = await getAttachmentContent(item, attachment.id);
      if (!result || result.format !== Office.MailboxEnums.AttachmentContentFormat.Base64) {
        return null;
      }
      return { filename: attachment.name, content_base64: result.content };
    })
  );

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
  const controller = new AbortController();
  const timer = setTimeout(function () {
    controller.abort();
  }, REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(APP_BASE_URL + "/screen", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Screen-Key": SCREEN_API_KEY
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    // Any non-200 (auth failure, 500, rate limit) is treated as "no opinion".
    if (!response.ok) {
      return null;
    }
    return await response.json();
  } catch (error) {
    // Includes the AbortError raised by the timeout above.
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

async function onMessageSendHandler(event) {
  try {
    const item = Office.context.mailbox.item;

    // D1: attachments-only screening. No attachments means nothing to screen, so
    // skip the network call entirely and keep Send instant for ordinary email.
    const attachments = await collectAttachments(item);
    if (attachments.length === 0) {
      event.completed({ allowEvent: true });
      return;
    }

    const recipients = await collectRecipients(item);
    const verdict = await requestVerdict({ recipients: recipients, attachments: attachments });

    // Warn ONLY on an explicit "warn" verdict carrying a message to display.
    // Anything else - "allow", an unknown verdict, a null result from a failed
    // call - falls through to allowing the send.
    if (verdict && verdict.verdict === "warn" && verdict.message) {
      event.completed({ allowEvent: false, errorMessage: verdict.message });
      return;
    }

    event.completed({ allowEvent: true });
  } catch (error) {
    // Last line of defence. If anything above threw unexpectedly, the user's mail
    // still goes out - we never hold email hostage to a bug in this add-in.
    event.completed({ allowEvent: true });
  }
}

// Maps the FunctionName in the manifest's LaunchEvent to this function. Required on
// every platform.
//
// Note there is deliberately no Office.onReady() wrapper: in classic Outlook on
// Windows the handler runs in a JavaScript-only runtime where Office.onReady and
// Office.initialize never fire, so any startup logic placed there would be dead
// code on that platform. All setup lives inside the handler itself.
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
