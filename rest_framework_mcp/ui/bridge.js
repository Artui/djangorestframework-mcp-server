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

  var nextId = 1;
  var pending = {};
  var handshakeDone = false;
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
    showError: showError,
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
    if (handshakeDone) {
      return;
    }
    handshakeDone = true;
    notify("ui/notifications/initialized", {});

    if (result) {
      mcpApp.protocolVersion = result.protocolVersion;
      mcpApp.hostInfo = result.hostInfo;
      mcpApp.hostCapabilities = result.hostCapabilities;
      applyHostContext(result.hostContext || {});
    }
    if (error) {
      showError(
        "This view could not complete the MCP Apps handshake with its host: " +
          (error.message || String(error))
      );
    }
    observeSize();
    notifySize();
  }

  function connect() {
    if (!hasHost) {
      // Opened directly rather than framed by a host -- during development,
      // usually. Say so in the document instead of posting into the void.
      handshakeDone = true;
      showError(
        "This document is an MCP Apps view. It is rendering outside a host, " +
          "so there is no tool result to display."
      );
      return;
    }
    setTimeout(function () {
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
   * Measured from the content element, never from `documentElement`. When the
   * view puts a wide table inside an `overflow-x: auto` container, the
   * overflowing child does not widen its scrolling ancestor -- so the document
   * measures exactly as wide as the frame it already has, and the view asks
   * the host for the width it was just given.
   *
   * Height is what hosts actually act on. Width is reported too, and a host
   * that fixes the width simply ignores it.
   */
  function contentElement() {
    return document.getElementById("mcp-app-root") || document.body;
  }

  function notifySize() {
    var element = contentElement();
    if (!element) {
      return;
    }
    notify("ui/notifications/size-changed", {
      width: Math.ceil(element.scrollWidth),
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

  function call(handler, first, second) {
    if (typeof handler !== "function") {
      return;
    }
    // A throwing handler must not take the bridge down with it: sizing and the
    // error banner are what is left to explain the failure.
    try {
      handler(first, second);
    } catch (error) {
      showError("The view's handler raised: " + (error && error.message ? error.message : error));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
