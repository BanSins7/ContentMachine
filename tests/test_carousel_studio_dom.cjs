/* Browserless full Carousel Studio smoke: source handoff -> rendered slides -> MP4 job. */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const html = fs.readFileSync("site/carousel-studio.html", "utf8");
const source = fs.readFileSync("site/carousel-studio.js", "utf8");
const ids = [...html.matchAll(/id="([a-zA-Z0-9]+)"/g)].map((match) => match[1]);
const nodes = new Map();

function element(tag = "div") {
  const events = {};
  const attrs = {};
  const el = {
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
  return el;
}
for (const id of ids) nodes.set(id, element());
nodes.get("resultArea").hidden = true;
nodes.get("errorBanner").hidden = true;
let urlCounter = 0, downloaded = 0, encoded = 0, clipboard = "";

class FakeImage {
  constructor() { this.naturalWidth = 1080; this.naturalHeight = 1920; }
  set src(value) { this._src = value; if (this.onload) this.onload(); }
  get src() { return this._src; }
}
class FakeFileReader {
  readAsDataURL() { this.result = "data:image/png;base64," + Buffer.from("FAKEPNG").toString("base64"); this.onload(); }
}
const ctx = {
  fillRect() {}, fillText() {}, drawImage() {}, beginPath() {}, arc() {},
  fill() {}, roundRect() {}, save() {}, restore() {}, shadowBlur: 0,
  measureText(s) { return { width: String(s).length * 22 }; },
  createLinearGradient() { return { addColorStop() {} }; }
};
const document = {
  activeElement: { tagName: "BODY" },
  body: element("body"),
  getElementById(id) { return nodes.get(id) || null; },
  createElement(tag) {
    const el = element(tag);
    if (tag === "canvas") {
      el.getContext = () => ctx;
      el.toBlob = (callback) => callback(new Blob(["FAKE-PNG"], { type: "image/png" }));
    }
    if (tag === "a") el.click = () => { downloaded += 1; };
    return el;
  }
};
const context = {
  document, Blob, TextEncoder,
  Event: class Event { constructor(type) { this.type = type; } },
  URL: { createObjectURL() { return "blob:test-" + (++urlCounter); }, revokeObjectURL() {} },
  window: { location: { hash: "#prepare" }, addEventListener() {}, setTimeout() {} },
  navigator: { clipboard: { writeText: async (value) => { clipboard = value; } } },
  Image: FakeImage, FileReader: FakeFileReader,
  fetch: async (url, options) => {
    if (url === "/api/carousel-video/status") {
      return { ok: true, json: async () => ({ ffmpeg_available: true }) };
    }
    if (url === "/api/carousel-video/render") {
      assert.equal(options.headers["X-ContentMachine"], "1");
      const request = JSON.parse(options.body);
      assert.equal(request.slides.length, 5);
      for (const item of request.slides) assert.equal(item.duration_seconds, 3);
      encoded += 1;
      return { ok: true, json: async () => ({ job: { id: "a".repeat(32) } }) };
    }
    if (url.startsWith("/api/carousel-video/jobs/")) {
      return { ok: true, json: async () => ({ job: { status: "succeeded", slides: 5, duration_seconds: 15 } }) };
    }
    throw Error("Unexpected network request: " + url);
  },
  console
};

(async () => {
  vm.runInNewContext(source, context, { filename: "carousel-studio.js" });
  assert.equal(nodes.get("prepareView").hidden, false);
  assert.equal(nodes.get("renderWorkspace").hidden, true);

  nodes.get("briefDemo").click();
  assert.match(nodes.get("briefInput").value, /breakup_001/);
  const files = [
    "sad__puffy.jpg", "sad__morning.jpg", "neutral__outdoor.jpg",
    "happy__cafe.jpg", "happy__outdoor.jpg"
  ].map((name) => ({ name, type: "image/png", size: 200 }));
  await nodes.get("prepPhotoFiles").dispatchEvent({ type: "change", target: { files } });
  assert.equal(nodes.get("prepPhotoList").children.length, 5);
  assert.equal(nodes.get("prepPhotoCount").textContent, "5 фото");

  await nodes.get("prepareCopyBtn").click();
  assert.match(clipboard, /breakup_001/);
  assert.match(clipboard, /sad__puffy.jpg/);
  assert.match(clipboard, /NO|НЕТ обводки/);
  await nodes.get("prepareManifestBtn").click();
  assert.equal(downloaded, 1);

  nodes.get("goRenderBtn").click();
  assert.equal(nodes.get("prepareView").hidden, true);
  assert.equal(nodes.get("renderWorkspace").hidden, false);

  nodes.get("demoButton").click();
  await nodes.get("buildButton").click();
  assert.equal(nodes.get("thumbnailRail").children.length, 5);
  assert.equal(nodes.get("resultStatus").textContent, "Демо · не все ассеты");
  nodes.get("nextSlide").click();
  assert.equal(nodes.get("slideIndex").textContent, "2 / 5");

  // App screenshot is required before final MP4 can be exported.
  await nodes.get("renderVideoBtn").click();
  assert.match(nodes.get("videoProgress").textContent, /Сначала добавь/);

  await nodes.get("ctaFile").dispatchEvent({ type: "change", target: {
    files: [{ name: "screen.png", type: "image/png", size: 100 }]
  } });
  await nodes.get("buildButton").click();
  assert.equal(nodes.get("resultStatus").textContent, "Собрано");
  assert.equal(nodes.get("textStatus").textContent, "Из финального JSON");

  await nodes.get("downloadAll").click();
  assert.equal(downloaded, 2);
  await nodes.get("renderVideoBtn").click();
  assert.equal(encoded, 1);
  assert.equal(nodes.get("videoResult").hidden, false);
  assert.match(nodes.get("videoPreview").src, /\/api\/carousel-video\/files\/a{32}\/video/);
  assert.match(nodes.get("videoDownload").href, /\/api\/carousel-video\/files\/a{32}\/video/);
  assert.equal(nodes.get("renderVideoBtn").disabled, false);
  console.log("PASS: prepare JSON + 5 photos -> copied chat handoff -> gallery + CTA -> real MP4 endpoint -> download");
})().catch((error) => { console.error(error); process.exitCode = 1; });
