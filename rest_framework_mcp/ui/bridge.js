/*
 * The view half of the MCP Apps ``ui/*`` postMessage bridge.
 *
 * Inlined into every document ``build_app_document`` composes, so a project
 * writing an interactive view writes columns rather than JSON-RPC. The host
 * owns the other half: it builds the sandboxed iframe, enforces the CSP, and
 * speaks this protocol from the outside.
 *
 * Why this file exists at all, when the package deliberately implements no
 * client: every failure mode in this bridge is silent. Nothing throws, the
 * console stays empty, the server sees a clean ``resources/read``, and the
 * host keeps the frame hidden -- so an error message written into the document
 * is itself invisible. That combination is not something each consumer should
 * rediscover, and one of its failure modes is unrecoverable (see
 * ``completeHandshake``).
 *
 * Loaded from ``<head>``, before the view's own markup and scripts, so a
 * fragment can assign ``mcpApp.onToolResult`` during parsing. The handshake
 * itself waits for ``DOMContentLoaded``, so those assignments always land
 * first.
 */
(function () {
  "use strict";

  // The revision this bridge speaks, matching the extension's own
  // LATEST_PROTOCOL_VERSION. Hosts negotiate down; the result carries whatever
  // they chose, on `mcpApp.protocolVersion`.
  var PROTOCOL_VERSION = "2026-01-26";

  // How long to wait for a reply to `ui/initialize` before completing the
  // handshake anyway. A host that never answers would otherwise leave the
  // frame hidden forever, which is the one failure this file exists to make
  // impossible -- see `completeHandshake`.
  var HANDSHAKE_TIMEOUT_MS = 3000;

  // Whether protocol failures are written into the document as well as logged.
  // Stamped on the root element by `build_app_document`, which follows
  // `settings.DEBUG` unless the registration overrides it.
  var DIAGNOSTICS = document.documentElement.getAttribute("data-mcp-diagnostics") === "1";

  var nextId = 1;
  var pending = {};
  var handshakeDone = false;
  // Whether the host ever answered `ui/initialize` with a result. Distinct from
  // `handshakeDone`, which only records that `initialized` has gone out.
  var handshakeAnswered = false;
  // Set by `mcpApp.measureWidthFrom` when the view scrolls wide content inside
  // a container of its own -- see `contentElement`.
  var widthElement = null;
  var handshakeTimer = null;
  var host = window.parent;
  // False when this document was opened directly rather than framed by a host.
  // Every send checks it: `showError` and the size notifications run in that
  // case too, and posting to our own window would be a message the document
  // then receives back from itself.
  var hasHost = host !== window;

  var mcpApp = {
    /* Assigned by the view's own script. All optional. */
    onToolResult: null, // (structuredContent, callToolResult) => void
    onToolInput: null, // (argumentsObject) => void
    onToolCancelled: null, // (reason) => void
    onHostContext: null, // (hostContext) => void
    onTeardown: null, // (reason) => void

    /* Populated by the handshake and kept current by host-context-changed. */
    hostContext: {},
    hostInfo: null,
    hostCapabilities: null,
    protocolVersion: null,

    /* The most recent values pushed by the host, for a late-arriving render. */
    toolInput: null,
    toolResult: null,

    callTool: callTool,
    requestDisplayMode: requestDisplayMode,
    sendMessage: sendMessage,
    openLink: openLink,
    notifySize: notifySize,
    measureWidthFrom: measureWidthFrom,
    showError: showError,
    clearError: clearError,
  };
  window.mcpApp = mcpApp;

  /* ---------- JSON-RPC over postMessage ---------- */

  function post(message) {
    if (!hasHost) {
      return;
    }
    // `"*"` rather than a fixed origin: the host frames this document from an
    // origin the view is never told, and the sandbox is what confines the
    // message. Nothing secret travels this direction.
    host.postMessage(message, "*");
  }

  function notify(method, params) {
    post({ jsonrpc: "2.0", method: method, params: params || {} });
  }

  function request(method, params) {
    var id = nextId++;
    return new Promise(function (resolve, reject) {
      pending[id] = { resolve: resolve, reject: reject };
      post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    });
  }

  function respond(id, result) {
    post({ jsonrpc: "2.0", id: id, result: result });
  }

  function respondError(id, code, message) {
    post({ jsonrpc: "2.0", id: id, error: { code: code, message: message } });
  }

  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!message || message.jsonrpc !== "2.0") {
      return;
    }
    if (message.method === undefined) {
      settle(message);
      return;
    }
    if (message.id === undefined || message.id === null) {
      handleNotification(message.method, message.params || {});
      return;
    }
    handleRequest(message);
  });

  function settle(message) {
    var waiter = pending[message.id];
    if (!waiter) {
      return;
    }
    delete pending[message.id];
    if (message.error) {
      waiter.reject(message.error);
      return;
    }
    waiter.resolve(message.result);
  }

  /* ---------- Host -> View ---------- */

  function handleNotification(method, params) {
    if (method === "ui/notifications/tool-result") {
      mcpApp.toolResult = params;
      call(mcpApp.onToolResult, params.structuredContent, params);
      notifySize();
      return;
    }
    if (
      method === "ui/notifications/tool-input" ||
      method === "ui/notifications/tool-input-partial"
    ) {
      mcpApp.toolInput = params.arguments;
      call(mcpApp.onToolInput, params.arguments);
      return;
    }
    if (method === "ui/notifications/tool-cancelled") {
      call(mcpApp.onToolCancelled, params.reason);
      return;
    }
    if (method === "ui/notifications/host-context-changed") {
      // `params` IS a partial HostContext, not an envelope around one, so it
      // merges straight in. Confirmed against a real host by the consumer this
      // bridge came from -- it was an open question in their report.
      applyHostContext(params);
    }
  }

  function handleRequest(message) {
    if (message.method === "ui/resource-teardown") {
      call(mcpApp.onTeardown, (message.params || {}).reason);
      respond(message.id, {});
      return;
    }
    // Answered rather than ignored: an unanswered request leaves the host
    // waiting on a promise that never settles. -32601 is JSON-RPC's
    // "method not found".
    respondError(message.id, -32601, "Method not implemented by this view: " + message.method);
  }

  /* ---------- The handshake ---------- */

  /*
   * Every exit from `ui/initialize` funnels through here, and the notification
   * goes out FIRST -- before the result is read, before anything renders, and
   * whether the host replied with a result or an error.
   *
   * The spec is explicit that "the Host MUST NOT send any request or
   * notification to the View before it receives an initialized notification",
   * and says nothing at all about what a View should do when `ui/initialize`
   * comes back an error. A View that treats the error as fatal and returns --
   * which is the natural shape, and what the extension's own SDK does in
   * `App.connect` -- never sends `initialized`, so the host never reveals the
   * frame, so the explanation the View just wrote into the document is sealed
   * inside a frame nobody can see. Nothing throws and nothing is logged.
   *
   * Sending it unconditionally trades a small protocol liberty for a view that
   * can always explain itself. That is the right trade in a gap the spec
   * leaves open: the worst case is a host that starts sending notifications to
   * a view which failed to initialise, and this file handles those regardless.
   */
  function completeHandshake(result, error) {
    if (!handshakeDone) {
      handshakeDone = true;
      // Cancelled on the first completion whatever it was, so a host that
      // answers with an error is not then also accused of never answering.
      if (handshakeTimer !== null) {
        clearTimeout(handshakeTimer);
        handshakeTimer = null;
      }
      notify("ui/notifications/initialized", {});
      observeSize();
      notifySize();
    }

    // Applied outside the once-only guard, because the timeout and the reply
    // are two paths to the same funnel and the reply can lose the race. Only
    // the notification must not repeat; a host that answers late is still
    // answering, and discarding its `hostContext` here left the view on the
    // UA's default theme underneath a banner claiming the host never replied
    // -- self-concealing in the same way the hidden frame is.
    if (result) {
      handshakeAnswered = true;
      mcpApp.protocolVersion = result.protocolVersion;
      mcpApp.hostInfo = result.hostInfo;
      mcpApp.hostCapabilities = result.hostCapabilities;
      clearError();
      applyHostContext(result.hostContext || {});
      return;
    }
    if (error && !handshakeAnswered) {
      report(
        "This view could not complete the MCP Apps handshake with its host: " +
          (error.message || String(error))
      );
    }
  }

  function connect() {
    if (!hasHost) {
      // Opened directly rather than framed by a host -- during development,
      // usually. Say so in the document instead of posting into the void.
      handshakeDone = true;
      // Shown unconditionally, unlike every other diagnostic: there is no host
      // here, so there is no end user to show developer text to. Whoever is
      // looking at this opened the file themselves.
      showError(
        "This document is an MCP Apps view. It is rendering outside a host, " +
          "so there is no tool result to display."
      );
      return;
    }
    handshakeTimer = setTimeout(function () {
      handshakeTimer = null;
      completeHandshake(null, { message: "the host did not answer ui/initialize" });
    }, HANDSHAKE_TIMEOUT_MS);

    request("ui/initialize", {
      appInfo: { name: document.title || "view", version: "1.0.0" },
      // Declared empty on purpose: this bridge renders tool results and calls
      // server tools. It registers no tools of its own, so a host has no
      // reason to send `tools/call` or `tools/list` this way.
      appCapabilities: {},
      protocolVersion: PROTOCOL_VERSION,
    }).then(
      function (result) {
        completeHandshake(result, null);
      },
      function (error) {
        completeHandshake(null, error);
      }
    );
  }

  /* ---------- Host context ---------- */

  function applyHostContext(patch) {
    var context = mcpApp.hostContext;
    for (var key in patch) {
      if (Object.prototype.hasOwnProperty.call(patch, key)) {
        context[key] = patch[key];
      }
    }
    if (context.theme) {
      document.documentElement.setAttribute("data-theme", context.theme);
    }
    if (context.locale) {
      // The document ships with `lang="en"` because the shell is composed
      // server-side, where the host's locale is not yet known.
      document.documentElement.lang = context.locale;
    }
    applyStyleVariables((context.styles || {}).variables);
    call(mcpApp.onHostContext, context);
    notifySize();
  }

  function applyStyleVariables(variables) {
    if (!variables) {
      return;
    }
    var root = document.documentElement;
    for (var name in variables) {
      if (
        Object.prototype.hasOwnProperty.call(variables, name) &&
        variables[name] !== undefined
      ) {
        // The host sends bare names; CSS custom properties need the prefix.
        root.style.setProperty(name.indexOf("--") === 0 ? name : "--" + name, variables[name]);
      }
    }
  }

  /* ---------- Sizing ---------- */

  /*
   * Measured from the content element, never from `documentElement`: content
   * that overflows an `overflow-x: auto` container does not widen its scrolling
   * ancestor, so the document measures exactly as wide as the frame it already
   * has and the view asks the host for the width it was just given.
   *
   * That reasoning applies to the measured element too, and measuring
   * `#mcp-app-root` only moves the problem one level up. A view laid out as
   * `#mcp-app-root > .scroller[overflow-x: auto] > table` has its table clipped
   * by `.scroller` and `.scroller` sized by the root, so the root's
   * `scrollWidth` is the frame width again. **The measured element has to be
   * an ancestor of the overflow, not of the scroller.** A view that scrolls
   * wide content internally says so with `mcpApp.measureWidthFrom(element)`,
   * passing the content inside the scroller.
   *
   * Height is what hosts actually act on, and it is unaffected: a horizontal
   * scroller does not clip its own height. Width is reported anyway, and a host
   * that fixes the width ignores it -- silently, with no error and no log.
   */
  function contentElement() {
    return document.getElementById("mcp-app-root") || document.body;
  }

  function measureWidthFrom(element) {
    widthElement = element;
    notifySize();
  }

  function notifySize() {
    var element = contentElement();
    if (!element) {
      return;
    }
    notify("ui/notifications/size-changed", {
      width: Math.ceil((widthElement || element).scrollWidth),
      height: Math.ceil(element.scrollHeight),
    });
  }

  function observeSize() {
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    var element = contentElement();
    if (!element) {
      return;
    }
    new ResizeObserver(function () {
      notifySize();
    }).observe(element);
  }

  /* ---------- View -> Host ---------- */

  function callTool(name, args) {
    // Goes back through the host to the ordinary `tools/call` endpoint, so the
    // server's permissions and rate limits apply unchanged.
    return request("tools/call", { name: name, arguments: args || {} });
  }

  function requestDisplayMode(mode) {
    return request("ui/request-display-mode", { mode: mode });
  }

  function sendMessage(text) {
    return request("ui/message", { role: "user", content: { type: "text", text: text } });
  }

  function openLink(url) {
    return request("ui/open-link", { url: url });
  }

  /* ---------- Diagnostics ---------- */

  /*
   * Report a protocol or handler failure.
   *
   * Always to the console, where a developer with devtools open will find it.
   * Into the document only when the composed page asked for it, because this
   * text is written for whoever wrote the view and the audience of a rendered
   * view is whoever is using the product. A handshake that fails while the
   * host still delivers a tool result produces a view that works and a banner
   * of raw protocol text above it -- and the bridge cannot tell that case from
   * a fatal one.
   *
   * The unrecoverable failure is the hidden frame, and completing the handshake
   * is what fixes that. Showing the reason is a debugging convenience, so it
   * follows `settings.DEBUG` unless a project says otherwise.
   */
  function report(text) {
    if (typeof console !== "undefined" && console.error) {
      console.error("[mcp-app] " + text);
    }
    if (DIAGNOSTICS) {
      showError(text);
    }
  }

  /*
   * The diagnostic surface a view otherwise does not have. By the time this is
   * called the `initialized` notification has already gone out, so the host
   * has revealed the frame and there is somewhere for the text to appear.
   */
  function showError(text) {
    var banner = document.getElementById("mcp-app-error");
    if (!banner) {
      banner = document.createElement("pre");
      banner.id = "mcp-app-error";
      var parent = contentElement() || document.body;
      if (!parent) {
        return;
      }
      parent.insertBefore(banner, parent.firstChild);
    }
    banner.textContent = text;
    notifySize();
  }

  /* Retract a message that has since been proved wrong -- see `completeHandshake`. */
  function clearError() {
    var banner = document.getElementById("mcp-app-error");
    if (banner && banner.parentNode) {
      banner.parentNode.removeChild(banner);
      notifySize();
    }
  }

  function call(handler, first, second) {
    if (typeof handler !== "function") {
      return;
    }
    // A throwing handler must not take the bridge down with it: sizing and the
    // error banner are what is left to explain the failure.
    try {
      handler(first, second);
    } catch (error) {
      report("The view's handler raised: " + (error && error.message ? error.message : error));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
