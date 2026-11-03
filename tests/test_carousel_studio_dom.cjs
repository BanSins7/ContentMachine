/* Offline DOM/Canvas smoke: real carousel script runs against browser-like stubs. */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const html = fs.readFileSync("site/carousel-studio.html", "utf8");
const source = fs.readFileSync("site/carousel-studio.js", "utf8");
const ids = [...html.matchAll(/id="([a-zA-Z0-9]+)"/g)].map((m) => m[1]);
const nodes = new Map();

function element(tag = "div") {
  const events = {};
  const attrs = {};
  const node = {
    tagName: tag.toUpperCase(), children: [], style: {}, dataset: {},
    hidden: false, value: "", files: [], checked: false, textContent: "",
    disabled: false, src: "", href: "", download: "",
    className: "", classList: {
      classes: new Set(), add(name) { this.classes.add(name); },
      remove(name) { this.classes.delete(name); },
      toggle(name, enabled) { if (enabled) this.classes.add(name); else this.classes.delete(name); }
    },
    firstElementChild: { textContent: "" },
    addEventListener(name, callback) { events[name] = callback; },
    dispatchEvent(event) { return events[event.type] && events[event.type](event); },
    append(...els) { this.children.push(...els); },
    appendChild(el) { this.children.push(el); },
    replaceChildren(...els) { this.children = [...els]; },
    setAttribute(name, value) { attrs[name] = value; },
    getAttribute(name) { return attrs[name]; },
    remove() {}, click() { if (events.click) return events.click(); },
    scrollIntoView() {}
  };
  node._events = events;
  return node;
}
for (const id of ids) nodes.set(id, element());
nodes.get("resultArea").hidden = true;
nodes.get("errorBanner").hidden = true;
nodes.get("rewriteToggle").checked = true;

let urlCounter = 0, downloaded = 0, rewriteCalls = 0;
class FakeImage {
  constructor() { this.naturalWidth = 600; this.naturalHeight = 900; }
  set src(value) { this._src = value; if (this.onload) this.onload(); }
  get src() { return this._src; }
}
const fakeContext = {
  fillRect() {}, fillText() {}, drawImage() {}, beginPath() {}, arc() {},
  fill() {}, roundRect() {}, save() {}, restore() {},
  measureText(text) { return { width: String(text).length * 23 }; },
  createLinearGradient() { return { addColorStop() {} }; }
};
const document = {
  activeElement: { tagName: "BODY" },
  body: element("body"),
  getElementById(id) { return nodes.get(id) || null; },
  createElement(tag) {
    const n = element(tag);
    if (tag === "canvas") {
      n.getContext = () => fakeContext;
      n.toBlob = (callback) => callback(new Blob(["FAKE-PNG"], { type: "image/png" }));
    }
    if (tag === "a") {
      n.click = () => { downloaded += 1; };
    }
    return n;
  }
};
const context = {
  document, Blob, TextEncoder,
  Event: class Event { constructor(type) { this.type = type; } },
  URL: {
    createObjectURL() { return "blob:unit-test-" + (++urlCounter); },
    revokeObjectURL() {}
  },
  window: { addEventListener() {}, setTimeout() {} },
  Image: FakeImage,
  fetch: async (url, options) => {
    if (!url.endsWith("/rewrite")) return { ok: true, json: async () => ({ configured: false }) };
    rewriteCalls += 1;
    const payload = JSON.parse(options.body);
    const slides = payload.slides.map((slide) => ({
      id: slide.id, texts: slide.texts.map((value) => "Новая версия: " + value)
    }));
    return { ok: true, json: async () => ({ slides, model: payload.model, usage: { cost_usd: .004 } }) };
  },
  console
};

(async () => {
  vm.runInNewContext(source, context, { filename: "carousel-studio.js" });
  nodes.get("demoButton").click();
  assert.match(nodes.get("jsonInput").value, /depuff_lifestyle_001/);
  nodes.get("rewriteToggle").checked = false;
  nodes.get("rewriteToggle").dispatchEvent({ type: "change" });
  await nodes.get("buildButton").click();
  assert.equal(nodes.get("resultArea").hidden, false);
  assert.equal(nodes.get("emptyResult").hidden, true);
  assert.equal(nodes.get("thumbnailRail").children.length, 5);
  assert.match(nodes.get("largeSlide").src, /^blob:unit-test-/);
  assert.match(nodes.get("qaList").children[0].textContent +
    nodes.get("qaList").children[0].children.map((n) => n.textContent).join(" "), /.+/);
  nodes.get("nextSlide").click();
  assert.equal(nodes.get("slideIndex").textContent, "2 / 5");
  await nodes.get("downloadAll").click();
  assert.equal(downloaded, 1);
  const names = [
    "sad__puffy.jpg", "sad__morning.jpg", "neutral__outdoor.jpg",
    "happy__cafe.jpg", "happy__outdoor.jpg"
  ];
  const files = names.map((name) => ({ name, type: "image/png", size: 200 }));
  await nodes.get("photoFiles").dispatchEvent({ type: "change", target: { files } });
  await nodes.get("ctaFile").dispatchEvent({ type: "change", target: {
    files: [{ name: "app_screen.png", type: "image/png", size: 100 }] } });
  nodes.get("rewriteToggle").checked = true;
  nodes.get("rewriteToggle").dispatchEvent({ type: "change" });
  await nodes.get("buildButton").click();
  assert.equal(nodes.get("resultStatus").textContent, "Собрано");
  assert.equal(nodes.get("textStatus").textContent, "LLM");
  assert.equal(nodes.get("thumbnailRail").children.length, 5);
  assert.equal(rewriteCalls, 1);
  assert.equal(nodes.get("photoCount").textContent, "5 фотографий");
  await nodes.get("downloadAll").click();
  assert.equal(downloaded, 2);
  console.log("PASS: JSON -> tagged assets + CTA -> mocked LLM -> 5 composed slides -> ZIP");
})().catch((error) => { console.error(error); process.exitCode = 1; });
