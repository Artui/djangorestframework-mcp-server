/*
 * Drives `rest_framework_mcp/ui/bridge.js` against a fake host and a fake DOM,
 * and prints one JSON object describing what each scenario did.
 *
 * The bridge is the one piece of this package that runs in someone else's
 * browser, and every one of its failure modes is silent -- so asserting on its
 * source text would reproduce exactly the problem it exists to fix. This runs
 * it instead. Node supplies the engine; the DOM below is only as real as the
 * handful of APIs the bridge touches, which is enough to exercise the whole
 * protocol and nothing else.
 *
 * Usage: node bridge_harness.mjs <path to bridge.js>
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

function makeElement(id) {
  return {
    id: id || "",
    textContent: "",
    children: [],
    scrollWidth: 320,
    scrollHeight: 240,
    style: { setProperty() {} },
    get firstChild() {
      return this.children[0] || null;
    },
    insertBefore(node, before) {
      this.children.unshift(node);
      return node;
    },
  };
}

/* A run of the bridge: a fresh DOM, a fresh fake host, a controllable clock. */
function run(options) {
  const posted = [];
  const timers = [];
  const listeners = { window: {}, document: {} };
  const root = makeElement("mcp-app-root");
  const elements = { "mcp-app-root": root };

  const documentElement = {
    lang: "en",
    attributes: {},
    style: { properties: {}, setProperty(k, v) { this.properties[k] = v; } },
    setAttribute(k, v) { this.attributes[k] = v; },
  };

  const document = {
    title: "Invoices",
    readyState: "loading",
    documentElement,
    addEventListener(type, fn) {
      (listeners.document[type] = listeners.document[type] || []).push(fn);
    },
    getElementById(id) {
      return elements[id] || null;
    },
    createElement() {
      return makeElement();
    },
    body: root,
  };

  const parent = {
    postMessage(message) {
      posted.push(message);
    },
  };

  const window = {
    parent: options.unframed ? null : parent,
    document,
    addEventListener(type, fn) {
      (listeners.window[type] = listeners.window[type] || []).push(fn);
    },
  };
  // An unframed view is one whose `window.parent` IS the window, which is what
  // a browser reports for a top-level document.
  if (options.unframed) {
    window.parent = window;
  }

  const context = vm.createContext({
    window,
    document,
    Promise,
    Object,
    Math,
    String,
    setTimeout(fn, ms) {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimeout() {},
    console,
  });
  vm.runInContext(source, context);

  const api = {
    posted,
    root,
    documentElement,
    mcpApp: window.mcpApp,
    /* Deliver a message from the host, the way the iframe would. */
    deliver(message) {
      for (const fn of listeners.window.message || []) {
        fn({ data: message });
      }
    },
    /* Let the parser finish, which is what starts the handshake. */
    domReady() {
      document.readyState = "interactive";
      for (const fn of listeners.document.DOMContentLoaded || []) {
        fn();
      }
    },
    fireTimers() {
      const due = timers.splice(0, timers.length);
      for (const timer of due) {
        timer.fn();
      }
    },
    methods() {
      return posted.filter((m) => m.method).map((m) => m.method);
    },
    lastRequestId() {
      const requests = posted.filter((m) => m.id !== undefined);
      return requests.length ? requests[requests.length - 1].id : null;
    },
  };
  return api;
}

/* Let the promise callbacks the bridge registered actually run. */
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/* A host that answers `ui/initialize` however the scenario says. */
async function handshake(app, reply) {
  app.domReady();
  const id = app.lastRequestId();
  app.deliver(Object.assign({ jsonrpc: "2.0", id }, reply));
  await flush();
  return id;
}

const INITIALIZE_RESULT = {
  protocolVersion: "2026-01-26",
  hostInfo: { name: "test-host", version: "1.0" },
  hostCapabilities: {},
  hostContext: { theme: "dark", locale: "fr-FR", styles: { variables: { "text-primary": "#eee" } } },
};

const scenarios = {};

/* The ordinary path: the host answers, the view initialises and reports size. */
scenarios.success = async () => {
  const app = run({});
  await handshake(app, { result: INITIALIZE_RESULT });
  return {
    methods: app.methods(),
    initialize_params: app.posted[0].params,
    theme: app.documentElement.attributes["data-theme"],
    lang: app.documentElement.lang,
    css_variables: app.documentElement.style.properties,
    protocol_version: app.mcpApp.protocolVersion,
  };
};

/*
 * The path that cost a consumer a day, and that the extension's own SDK still
 * gets wrong: the host answers `ui/initialize` with an ERROR. The frame stays
 * hidden until `initialized` arrives, so a view that treats the error as fatal
 * is invisible AND unable to say why.
 */
scenarios.error_reply = async () => {
  const app = run({});
  await handshake(app, { error: { code: -32603, message: "host said no" } });
  return {
    methods: app.methods(),
    banner: app.root.children.map((c) => c.textContent),
  };
};

/* The host never answers at all. Same requirement, different cause. */
scenarios.no_reply = async () => {
  const app = run({});
  app.domReady();
  app.fireTimers();
  await flush();
  return { methods: app.methods(), banner: app.root.children.map((c) => c.textContent) };
};

/* A late reply must not send `initialized` a second time. */
scenarios.timeout_then_reply = async () => {
  const app = run({});
  const id = app.lastRequestId();
  app.domReady();
  app.fireTimers();
  app.deliver({ jsonrpc: "2.0", id: app.lastRequestId(), result: INITIALIZE_RESULT });
  await flush();
  return { methods: app.methods() };
};

/* Handlers assigned while the parser is still running must receive results. */
scenarios.tool_result = async () => {
  const app = run({});
  const seen = [];
  app.mcpApp.onToolResult = (structured, full) => seen.push([structured, full.isError === true]);
  app.mcpApp.onToolInput = (args) => seen.push(["input", args]);
  await handshake(app, { result: INITIALIZE_RESULT });
  app.deliver({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-input",
    params: { arguments: { ordering: "-total" } },
  });
  app.deliver({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-result",
    params: { structuredContent: { results: [{ id: 1 }] } },
  });
  return { seen, methods: app.methods() };
};

/* A throwing handler must not take the bridge down with it. */
scenarios.handler_throws = async () => {
  const app = run({});
  app.mcpApp.onToolResult = () => {
    throw new Error("render blew up");
  };
  await handshake(app, { result: INITIALIZE_RESULT });
  app.deliver({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-result",
    params: { structuredContent: {} },
  });
  return { banner: app.root.children.map((c) => c.textContent), methods: app.methods() };
};

/* A host request the view does not implement must be answered, not ignored. */
scenarios.unknown_request = async () => {
  const app = run({});
  await handshake(app, { result: INITIALIZE_RESULT });
  app.deliver({ jsonrpc: "2.0", id: 99, method: "tools/list", params: {} });
  app.deliver({ jsonrpc: "2.0", id: 100, method: "ui/resource-teardown", params: { reason: "gone" } });
  const answers = app.posted.filter((m) => m.id === 99 || m.id === 100);
  return { answers };
};

/* A view opened directly in a browser says so instead of posting into the void. */
scenarios.unframed = async () => {
  const app = run({ unframed: true });
  app.domReady();
  await flush();
  return { posted: app.posted.length, banner: app.root.children.map((c) => c.textContent) };
};

/* The view calling a server tool goes back out as an ordinary `tools/call`. */
scenarios.call_tool = async () => {
  const app = run({});
  await handshake(app, { result: INITIALIZE_RESULT });
  app.mcpApp.callTool("list_invoices", { ordering: "-total" });
  await flush();
  const last = app.posted[app.posted.length - 1];
  return { method: last.method, params: last.params, has_id: last.id !== undefined };
};

const out = {};
for (const [name, scenario] of Object.entries(scenarios)) {
  out[name] = await scenario();
}
process.stdout.write(JSON.stringify(out));
