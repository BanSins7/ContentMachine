/* Browserless end-to-end: 01 brief handoff; 02 final JSON -> exact photo count -> MP4. */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const html = fs.readFileSync("site/carousel-studio.html", "utf8");
const source = fs.readFileSync("site/carousel-studio.js", "utf8");
const ids = [...html.matchAll(/id="([a-zA-Z0-9]+)"/g)].map(m => m[1]);
const nodes = new Map();
function element(tag = "div") {
  const events = {}, attrs = {};
  const el = {
    tagName: tag.toUpperCase(), children: [], style: {}, dataset: {},
    hidden: false, value: "", files: [], checked: false, textContent: "",
    disabled: false, src: "", href: "", download: "",
    className: "", classList: {
      classes: new Set(),
      add(name) { this.classes.add(name); }, remove(name) { this.classes.delete(name); },
      toggle(name, enabled) { if (enabled) this.classes.add(name); else this.classes.delete(name); }
    },
    firstElementChild: {textContent: ""},
    addEventListener(name, callback) { events[name] = callback; },
    dispatchEvent(event) { return events[event.type] && events[event.type](event); },
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); },
    replaceChildren(...items) { this.children = [...items]; },
    setAttribute(name, value) { attrs[name] = value; },
    getAttribute(name) { return attrs[name]; },
    removeAttribute(name) { delete attrs[name]; if (name === "src") this.src = ""; },
    remove() {}, click() { if (events.click) return events.click(); },
    scrollIntoView() {}
  };
  return el;
}
for (const id of ids) nodes.set(id, element());
nodes.get("resultArea").hidden = true;
nodes.get("errorBanner").hidden = true;
let generated = 0, downloaded = 0, encoded = 0, composed = 0, clipboard = "", expectedFrames = 2;
class FakeImage {
  constructor() { this.naturalWidth = 1080; this.naturalHeight = 1920; }
  set src(value) { this._src = value; if (this.onload) this.onload(); }
  get src() { return this._src; }
}
class FakeFileReader {
  readAsDataURL() {
    this.result = "data:image/png;base64," + Buffer.from("FAKEPNG").toString("base64");
    this.onload();
  }
}
const document = {
  activeElement: {tagName: "BODY"}, body: element("body"),
  getElementById(id) { return nodes.get(id) || null; },
  createElement(tag) {
    if (tag === "canvas") throw Error("Canvas MUST NOT compose final PNG slides");
    const el = element(tag);
    if (tag === "a") el.click = () => { downloaded++; };
    return el;
  }
};
const file = (name) => ({name, type: "image/png", size: 100});
const context = {
  document, Blob, TextEncoder,
  Event: class Event {constructor(type) {this.type = type;}},
  URL: {createObjectURL() {return "blob:mock-" + (++generated);}, revokeObjectURL() {}},
  window: {location: {hash: "#prepare"}, addEventListener() {}, setTimeout() {}},
  navigator: {clipboard: {writeText: async x => {clipboard = x;}}},
  Image: FakeImage, FileReader: FakeFileReader,
  fetch: async (url, options) => {
    if (url === "/api/carousel-video/status")
      return {ok: true, json: async () => ({ffmpeg_available: true})};
    if (url === "/api/carousel-video/compose") {
      const body = JSON.parse(options.body);
      assert.match(body.image, /^data:image\/png;base64,/);
      assert.equal(options.headers["X-ContentMachine"], "1");
      assert.ok(body.slide.text_blocks.length);
      assert.equal(body.width, 1080);
      assert.equal(body.height, 1920);
      if (body.slide.cta && body.slide.cta.enabled)
        assert.match(body.cta_image, /^data:image\/png;base64,/);
      const id = (++composed).toString(16).padStart(32, "0");
      return {ok: true, json: async () => ({
        slide: {id, url: "/api/carousel-video/slides/" + id + ".png"}
      })};
    }
    if (url.startsWith("/api/carousel-video/slides/"))
      return {ok: true, blob: async () => new Blob(["PNGfromPILLOW"], {type: "image/png"})};
    if (url === "/api/carousel-video/render") {
      const body = JSON.parse(options.body);
      assert.equal(body.slides.length, expectedFrames);
      assert.ok(body.slides.every(x => /^[a-f0-9]{32}$/.test(x.image_ref)));
      assert.equal(options.headers["X-ContentMachine"], "1");
      encoded++;
      return {ok: true, json: async () => ({job: {id: "a".repeat(32)}})};
    }
    if (url.startsWith("/api/carousel-video/jobs/"))
      return {ok: true, json: async () => ({job: {status: "succeeded", slides: expectedFrames, duration_seconds: expectedFrames * 3}})};
    throw Error("Unexpected request: " + url);
  },
  console
};
(async () => {
  vm.runInNewContext(source, context, {filename: "carousel-studio.js"});

  // 01 is unchanged: photo + initial JSON -> clipboard handoff.
  nodes.get("briefDemo").click();
  await nodes.get("prepPhotoFiles").dispatchEvent({type:"change",target:{files:[file("a.jpg")]}});
  await nodes.get("prepareCopyBtn").click();
  assert.match(clipboard, /breakup_001/);
  assert.match(clipboard, /a.jpg/);

  nodes.get("goRenderBtn").click();
  assert.equal(nodes.get("renderWorkspace").hidden, false);
  assert.equal(nodes.get("photoFiles").disabled, true);
  assert.equal(nodes.get("buildButton").disabled, true);

  // 02: reject incomplete JSON and raw creative briefs.
  nodes.get("jsonInput").value = '{"slides":[';
  nodes.get("jsonInput").dispatchEvent({type:"input"});
  assert.match(nodes.get("jsonValidation").textContent, /Ошибка JSON/);
  assert.equal(nodes.get("photoFiles").disabled, true);
  nodes.get("jsonInput").value = JSON.stringify({schema_version:"contentmachine.carousel_creative_brief.v1",slides:[]});
  nodes.get("jsonInput").dispatchEvent({type:"input"});
  assert.equal(nodes.get("buildButton").disabled, true);

  // Demo is a final two-slide specification: exactly 2 uploads.
  nodes.get("demoButton").click();
  assert.match(nodes.get("jsonValidation").textContent, /2 слайд/);
  assert.match(nodes.get("photoRequirement").textContent, /2 файла/);
  assert.equal(nodes.get("photoCount").textContent, "0 / 2");
  assert.equal(nodes.get("renderPhotoSlots").children.length, 2);
  assert.equal(nodes.get("buildButton").disabled, true);
  assert.equal(nodes.get("photoFiles").disabled, false);

  // Cannot upload an extra file.
  await nodes.get("photoFiles").dispatchEvent({type:"change",target:{files:[file("01.jpg"),file("02.jpg"),file("03.jpg")]}});
  assert.match(nodes.get("errorBanner").textContent, /требует 2 файлов/);
  assert.equal(nodes.get("photoCount").textContent, "0 / 2");

  // Upload out of order: filenames from JSON select correct slide.
  await nodes.get("photoFiles").dispatchEvent({type:"change",target:{files:[file("02.jpg")]}});
  assert.equal(nodes.get("photoCount").textContent, "1 / 2");
  assert.equal(nodes.get("buildButton").disabled, true);
  await nodes.get("photoFiles").dispatchEvent({type:"change",target:{files:[file("01.jpg")]}});
  assert.equal(nodes.get("photoCount").textContent, "2 / 2");
  assert.equal(nodes.get("buildButton").disabled, false);
  assert.equal(nodes.get("renderPhotoSlots").children.length, 2);
  assert.equal(nodes.get("renderPhotoSlots").children[0].children[1].children[1].textContent, "01.jpg");

  // User can swap one slot without re-uploading all photos.
  const replaceInput = nodes.get("renderPhotoSlots").children[1].children[2].children[0];
  await replaceInput.dispatchEvent({type:"change",target:{files:[file("new_night.jpg")]}});
  assert.equal(nodes.get("photoCount").textContent, "2 / 2");
  assert.equal(nodes.get("renderPhotoSlots").children[1].children[1].children[1].textContent, "new_night.jpg");

  await nodes.get("buildButton").click();
  assert.equal(encoded, 1);
  assert.equal(composed, 2);
  assert.equal(nodes.get("thumbnailRail").children.length, 2);
  assert.equal(nodes.get("resultStatus").textContent, "Собрано");
  assert.equal(nodes.get("videoResult").hidden, false);
  assert.match(nodes.get("videoPreview").src, /files\/a{32}\/video/);
  await nodes.get("downloadAll").click();
  assert.equal(downloaded, 1);

  // A JSON with CTA simply adds one extra slot within the SAME photo step.
  const ctaDoc = {
    carousel_id:"cta_test",format:"1080x1920",
    assets:{photos:[{id:"p01",filename:"a.jpg"},{id:"p02",filename:"b.jpg"}]},
    slides:[
      {id:"1",photo_id:"p01",text:"Hello",duration_seconds:3},
      {id:"2",photo_id:"p02",text:"Go",duration_seconds:3,cta:{enabled:true,asset_id:"app_screen"}}
    ]
  };
  nodes.get("jsonInput").value = JSON.stringify(ctaDoc);
  nodes.get("jsonInput").dispatchEvent({type:"input"});
  assert.equal(nodes.get("photoCount").textContent, "0 / 3");
  assert.match(nodes.get("photoRequirement").textContent, /3 файла/);
  assert.equal(nodes.get("renderPhotoSlots").children.length, 3);
  await nodes.get("photoFiles").dispatchEvent({type:"change",target:{files:[file("a.jpg"),file("b.jpg"),file("app_screen.png")]}});
  assert.equal(nodes.get("photoCount").textContent, "3 / 3");
  await nodes.get("buildButton").click();
  assert.equal(encoded, 2);
  assert.equal(composed, 4);
  assert.equal(nodes.get("resultStatus").textContent, "Собрано");
  assert.equal(nodes.get("textStatus").textContent, "Из финального JSON");

  console.log("PASS: JSON -> exact file slots -> ordered/replaceable photos -> preview + MP4; CTA same step");
})().catch(err => {console.error(err);process.exitCode=1;});
