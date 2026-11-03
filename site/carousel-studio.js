(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { photos: [], cta: null, slides: [], selected: 0, notes: [], urls: [], document: null, rewrite: "—", busy: false };
  const DEMO = {
    carousel_id: "depuff_lifestyle_001",
    format: "1080x1350",
    language: "ru",
    slides: [
      { id: "1", role: "hook", photo_tags: ["puffy", "sad"], layout: "text_top",
        text_blocks: [{ type: "headline", text: "Каждое утро я просыпалась с отёкшим лицом" }] },
      { id: "2", role: "problem", photo_tags: ["sad", "morning"], layout: "text_bottom",
        text_blocks: [{ type: "headline", text: "И никак не могла понять, почему так происходит" }] },
      { id: "3", role: "discovery", photo_tags: ["neutral", "outdoor"], layout: "text_top",
        text_blocks: [{ type: "headline", text: "Я решила внимательнее относиться к утренним привычкам" }] },
      { id: "4", role: "routine", photo_tags: ["happy", "cafe"], layout: "text_bottom",
        text_blocks: [{ type: "headline", text: "Начала уделять несколько минут уходу за собой" }] },
      { id: "5", role: "cta", photo_tags: ["happy", "outdoor"], layout: "text_top",
        text_blocks: [{ type: "headline", text: "Мой ежедневный уход теперь в одном приложении" }],
        cta: { enabled: true, type: "app_screenshot_overlay", position: "bottom_center", width_percent: 43 } }
    ]
  };

  const normalized = (s) => String(s || "").trim().toLowerCase().replace(/\s+/g, "_");
  const asArray = (x) => Array.isArray(x) ? x : (typeof x === "string" && x.trim() ? [x] : []);
  const tags = (value) => asArray(value).map(normalized).filter(Boolean);
  const safeFile = (file) => file && /^image\/(jpeg|png|webp)$/.test(file.type) && file.size <= 25 * 1024 * 1024;
  const alertBox = (message) => { $("errorBanner").hidden = !message; $("errorBanner").textContent = message || ""; };
  const formatName = (name) => String(name || "").replace(/\.[^.]+$/, "").toLowerCase();
  const fileTags = (name) => formatName(name).split(/[^a-zа-яё0-9]+/i).map(normalized).filter(Boolean);
  const buildNote = (kind, text) => state.notes.push({ kind, text });
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const loadImage = (file) => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onload = () => resolve({ img, url });
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Не удалось открыть " + file.name)); };
    img.src = url;
  });
  const downloadBlob = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  function normalizeDocument(doc) {
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw Error("JSON должен быть объектом с полем slides.");
    if (!Array.isArray(doc.slides) || !doc.slides.length || doc.slides.length > 40)
      throw Error("В JSON требуется от 1 до 40 слайдов (slides).");
    const rawFormat = doc.format || doc.size || "1080x1350";
    let width = 1080, height = 1350;
    if (typeof rawFormat === "string") {
      const match = rawFormat.match(/^(\d{3,4})[x×](\d{3,4})$/);
      if (!match) throw Error("Формат должен быть 1080x1350, 1080x1920 и т. п.");
      width = Number(match[1]); height = Number(match[2]);
    } else if (rawFormat && typeof rawFormat === "object") {
      width = Number(rawFormat.width); height = Number(rawFormat.height);
    }
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 600 || height < 600 || width > 2160 || height > 2160)
      throw Error("Размеры слайда: от 600 до 2160 пикселей.");
    const seen = new Set();
    const slides = doc.slides.map((s, index) => {
      if (!s || typeof s !== "object") throw Error("Слайд №" + (index + 1) + " должен быть объектом.");
      const id = String(s.id || s.slide_id || index + 1);
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || seen.has(id)) throw Error("Неверный или повторяющийся slide_id: " + id);
      seen.add(id);
      let source = s.text_blocks || s.textBlocks || s.texts;
      if (!source) source = s.text || s.headline || s.body || "";
      const textBlocks = asArray(source).map((b, bi) => {
        const obj = typeof b === "string" ? { text: b } : b;
        if (!obj || typeof obj.text !== "string" || !obj.text.trim()) throw Error("Пустой текст на слайде " + id);
        if (obj.text.length > 900) throw Error("Слишком длинный текст на слайде " + id);
        return {
          text: obj.text.trim(),
          type: String(obj.type || (bi === 0 ? "headline" : "body")),
          position: String(obj.position || s.text_position || ""),
          color: /^#[0-9a-f]{6}$/i.test(obj.color || "") ? obj.color : "#ffffff",
          max_chars: Number.isInteger(obj.max_chars) ? obj.max_chars : Math.max(40, Math.min(900, obj.text.length * 2))
        };
      });
      if (!textBlocks.length || textBlocks.length > 6) throw Error("Слайд " + id + ": от 1 до 6 текстовых блоков.");
      const image = s.image_query || s.image || {};
      const required = tags(s.photo_tags || s.required_tags || image.required_tags || image.tags || []);
      const optional = tags(s.optional_tags || image.optional_tags || []);
      const forbidden = tags(s.forbidden_tags || image.forbidden_tags || []);
      const cta = s.cta || null;
      return { id, role: String(s.role || s.goal || ""),
        layout: String(s.layout || "text_top"),
        required, optional, forbidden, photoId: String(s.photo_id || image.asset_id || image.photo_id || ""),
        textBlocks, cta: cta && typeof cta === "object" ? cta : null,
        avatarId: String(s.avatar_id || doc.avatar_id || ""),
        selectedPhoto: null, blob: null, url: null
      };
    });
    return { carousel_id: String(doc.carousel_id || doc.id || "carousel_" + Date.now()).slice(0, 80),
             width, height, language: String(doc.language || "ru"), slides, assets: doc.assets };
  }

  function assetManifest(doc) {
    const value = doc.assets;
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object" && Array.isArray(value.photos)) return value.photos;
    return [];
  }
  function matchPhotos(doc) {
    const manifest = assetManifest(doc);
    const used = new Set();
    const photoInfo = state.photos.map((photo) => {
      const def = manifest.find((a) => a && (
        (a.filename && a.filename.toLowerCase() === photo.filename.toLowerCase()) ||
        (a.path && String(a.path).split("/").pop().toLowerCase() === photo.filename.toLowerCase()) ||
        (a.id && a.id === formatName(photo.filename))
      ));
      return { ...photo, tags: def && (def.tags || def.photo_tags) ? tags(def.tags || def.photo_tags) : photo.tags,
               id: String((def && (def.id || def.photo_id)) || formatName(photo.filename)),
               avatar: String((def && def.avatar_id) || "") };
    });
    for (const slide of doc.slides) {
      let candidates = photoInfo.filter((photo) => {
        const matchName = slide.photoId && (slide.photoId === photo.id || slide.photoId.toLowerCase() === photo.filename.toLowerCase());
        if (slide.photoId && !matchName) return false;
        if (slide.avatarId && photo.avatar && slide.avatarId !== photo.avatar) return false;
        return slide.required.every((tag) => photo.tags.includes(tag)) &&
          slide.forbidden.every((tag) => !photo.tags.includes(tag));
      });
      candidates = candidates.map((photo) => ({ photo, score:
        slide.required.length * 20 + slide.optional.filter((tag) => photo.tags.includes(tag)).length * 4 +
        (used.has(photo.filename) ? -12 : 0) + (photo.img.naturalHeight >= photo.img.naturalWidth ? 3 : 0)
      })).sort((a, b) => b.score - a.score || a.photo.filename.localeCompare(b.photo.filename));
      slide.selectedPhoto = candidates.length ? candidates[0].photo : null;
      if (slide.selectedPhoto) {
        if (used.has(slide.selectedPhoto.filename)) buildNote("warn", "Слайд " + slide.id + ": повторно используется " + slide.selectedPhoto.filename + ".");
        used.add(slide.selectedPhoto.filename);
      } else buildNote("warn", "Слайд " + slide.id + ": нет фото для тегов " +
        (slide.required.length ? slide.required.join(", ") : "(любое фото)") + " — показана демонстрационная заглушка.");
    }
  }

  async function rewriteTexts(doc) {
    if (!$("rewriteToggle").checked) { state.rewrite = "Исходный"; return; }
    const payload = {
      slides: doc.slides.map((s) => ({
        id: s.id, role: s.role, texts: s.textBlocks.map((b) => b.text),
        max_chars: s.textBlocks.map((b) => b.max_chars)
      })),
      model: $("modelInput").value.trim(),
      api_key: $("keyInput").value.trim(),
      language: doc.language
    };
    const response = await fetch("/api/carousel-studio/rewrite", {
      method: "POST", headers: { "Content-Type": "application/json", "X-ContentMachine": "1" },
      body: JSON.stringify(payload)
    });
    let data;
    try { data = await response.json(); } catch (_) { throw Error("Сервер вернул некорректный ответ."); }
    if (!response.ok) throw Error((data.error && data.error.message) || "Ошибка перефразирования.");
    if (!data.slides || data.slides.length !== doc.slides.length) throw Error("Модель вернула неполный результат.");
    for (let i = 0; i < doc.slides.length; i++) {
      const rewritten = data.slides[i];
      if (rewritten.id !== doc.slides[i].id || rewritten.texts.length !== doc.slides[i].textBlocks.length)
        throw Error("Неверный порядок слайдов после перефразирования.");
      for (let j = 0; j < rewritten.texts.length; j++) doc.slides[i].textBlocks[j].text = rewritten.texts[j];
    }
    state.rewrite = "LLM";
    if (data.usage && typeof data.usage.cost_usd === "number")
      buildNote("good", "Перефразирование завершено · расход $" + data.usage.cost_usd.toFixed(4) + ".");
    else buildNote("good", "Текст перефразирован через " + (data.model || "OpenRouter") + ".");
    buildNote("warn", "Смысл и фактические утверждения текста рекомендуется проверить перед публикацией.");
  }

  function paintBackground(ctx, slide, width, height) {
    const palette = [["#dbe9ee", "#8aa5ba"], ["#f4daca", "#b8a3a8"], ["#e8e4da", "#99b2b1"], ["#e8e6f5", "#b8c7e0"], ["#e6d9c9", "#a1bcb0"]];
    const index = state.document.slides.indexOf(slide) % palette.length;
    const gradient = ctx.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, palette[index][0]); gradient.addColorStop(1, palette[index][1]);
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, width, height);
    ctx.save(); ctx.globalAlpha = .2;
    ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.arc(width * .8, height * .27, width * .35, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#3c5571"; ctx.beginPath(); ctx.arc(width * .15, height * .87, width * .48, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    ctx.fillStyle = "rgba(255,255,255,.92)"; ctx.textAlign = "center";
    ctx.font = "700 " + Math.round(width * .035) + "px Arial";
    ctx.fillText("ДЕМО • ФОТО НЕ ЗАГРУЖЕНО", width / 2, height / 2);
    ctx.font = Math.round(width * .025) + "px Arial";
    ctx.fillText("Теги: " + (slide.required.join(", ") || "без ограничений").slice(0, 56), width / 2, height / 2 + 45);
  }
  function drawCover(ctx, img, w, h) {
    const ratio = Math.max(w / img.naturalWidth, h / img.naturalHeight);
    const sw = w / ratio, sh = h / ratio;
    const sx = (img.naturalWidth - sw) / 2, sy = (img.naturalHeight - sh) / 2;
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, w, h);
  }
  function linesOf(ctx, text, maxWidth) {
    const out = [];
    for (const paragraph of text.split("\n")) {
      let line = "";
      for (const word of paragraph.split(/\s+/).filter(Boolean)) {
        const attempt = line ? line + " " + word : word;
        if (line && ctx.measureText(attempt).width > maxWidth) { out.push(line); line = word; }
        else line = attempt;
      }
      out.push(line);
    }
    return out;
  }
  function drawTexts(ctx, slide, w, h, ctaTop) {
    const margin = Math.round(w * .073), maxWidth = w - 2 * margin;
    const isBottom = /bottom/i.test(slide.layout) || slide.textBlocks[0].position.includes("bottom");
    const defaultPos = isBottom ? "bottom" : "top";
    const blocks = slide.textBlocks.map((b) => {
      let size = Math.round(w * (b.type === "body" ? .045 : .066));
      let lines = [];
      do {
        ctx.font = (b.type === "body" ? "500 " : "750 ") + size + "px Arial, sans-serif";
        lines = linesOf(ctx, b.text, maxWidth);
        if (lines.length <= (b.type === "body" ? 7 : 6)) break;
        size -= 2;
      } while (size > 28);
      const lineHeight = Math.round(size * 1.27);
      return { ...b, lines, size, lineHeight, total: lines.length * lineHeight + Math.round(h * .02) };
    });
    let top = margin + Math.round(h * .04);
    if (defaultPos === "bottom") {
      const sum = blocks.reduce((a, b) => a + b.total, 0);
      top = Math.max(margin, Math.min(h - sum - margin, ctaTop !== null ? ctaTop - sum - margin : h - sum - margin));
    }
    const total = blocks.reduce((a, b) => a + b.total, 0);
    const grad = ctx.createLinearGradient(0, Math.max(0, top - margin), 0, Math.min(h, top + total + margin));
    grad.addColorStop(0, "rgba(10,18,32,0)"); grad.addColorStop(.2, "rgba(10,18,32,.48)");
    grad.addColorStop(.8, "rgba(10,18,32,.55)"); grad.addColorStop(1, "rgba(10,18,32,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, Math.max(0, top - margin), w, Math.min(h, top + total + margin) - Math.max(0, top - margin));
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (const b of blocks) {
      ctx.font = (b.type === "body" ? "500 " : "750 ") + b.size + "px Arial, sans-serif";
      ctx.fillStyle = b.color;
      ctx.shadowColor = "rgba(0,0,0,.32)"; ctx.shadowBlur = 8;
      for (const line of b.lines) {
        if (ctx.measureText(line).width > maxWidth + 2) buildNote("warn", "Слайд " + slide.id + ": слишком длинное слово может выйти за край.");
        if (top + b.lineHeight > h - margin || (ctaTop !== null && top + b.lineHeight > ctaTop - 18))
          buildNote("warn", "Слайд " + slide.id + ": текст может пересекаться с CTA или краем.");
        ctx.fillText(line, w / 2, top);
        top += b.lineHeight;
      }
      top += Math.round(h * .02);
    }
    ctx.shadowBlur = 0; ctx.textBaseline = "alphabetic";
  }

  function drawCta(ctx, slide, w, h) {
    if (!slide.cta || slide.cta.enabled === false) return null;
    const cta = slide.cta;
    const size = clamp(Number(cta.width_percent) || 42, 20, 80) / 100 * w;
    const img = state.cta && state.cta.img;
    const width = size;
    const height = img ? Math.min(h * .42, width * img.naturalHeight / img.naturalWidth) : Math.round(h * .23);
    const isTop = String(cta.position || "").includes("top");
    const x = (w - width) / 2, y = isTop ? h * .11 : h - height - h * .066;
    ctx.shadowColor = "rgba(12,21,41,.28)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 9;
    ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.roundRect(x - 12, y - 12, width + 24, height + 24, 18); ctx.fill();
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    if (img) ctx.drawImage(img, x, y, width, height);
    else {
      ctx.fillStyle = "#f2f4fa"; ctx.fillRect(x, y, width, height);
      ctx.fillStyle = "#61718a"; ctx.textAlign = "center";
      ctx.font = "700 " + Math.round(w * .027) + "px Arial";
      ctx.fillText("CTA · НЕТ СКРИНШОТА", w / 2, y + height / 2);
      buildNote("warn", "Слайд " + slide.id + ": CTA ожидает изображение приложения.");
    }
    return y - 12;
  }
  async function renderSlide(slide, w, h) {
    const canvas = document.createElement("canvas"); canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw Error("Браузер не поддерживает Canvas.");
    if (slide.selectedPhoto) drawCover(ctx, slide.selectedPhoto.img, w, h);
    else paintBackground(ctx, slide, w, h);
    const ctaTop = drawCta(ctx, slide, w, h);
    drawTexts(ctx, slide, w, h, ctaTop);
    return await new Promise((resolve, reject) => canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("Не удалось сохранить PNG.")), "image/png"));
  }

  function clearRendered() {
    for (const url of state.urls) URL.revokeObjectURL(url);
    state.urls = []; state.slides = [];
    $("resultArea").hidden = true; $("emptyResult").hidden = false;
  }
  function renderQA() {
    const root = $("qaList"); root.replaceChildren();
    const warnings = state.notes.filter((note) => note.kind !== "good").length;
    $("qaCount").textContent = warnings ? warnings + " замечаний" : "Без замечаний";
    for (const note of state.notes) {
      const item = document.createElement("div");
      item.className = "qa-item " + note.kind;
      const icon = document.createElement("span"); icon.textContent = note.kind === "good" ? "✓" : "!";
      const text = document.createElement("span"); text.textContent = note.text;
      item.append(icon, text); root.append(item);
    }
    if (!state.notes.length) {
      const item = document.createElement("div"); item.className = "qa-item good";
      item.textContent = "✓ Все обязательные данные присутствуют. Проверьте визуальный результат перед публикацией.";
      root.append(item);
    }
  }
  function showSlide(index) {
    if (!state.slides.length) return;
    state.selected = (index + state.slides.length) % state.slides.length;
    const slide = state.slides[state.selected];
    $("largeSlide").src = slide.url;
    $("largeSlide").alt = "Слайд " + (state.selected + 1) + ": " + slide.textBlocks.map((b) => b.text).join(" ");
    $("currentSlideLabel").textContent = "Слайд " + (state.selected + 1);
    $("currentTags").textContent = slide.required.join(" · ");
    $("slideIndex").textContent = (state.selected + 1) + " / " + state.slides.length;
    Array.from($("thumbnailRail").children).forEach((el, i) => {
      el.classList.toggle("active", i === state.selected); el.setAttribute("aria-pressed", String(i === state.selected));
    });
  }
  function showResult(doc) {
    const hasPlaceholders = doc.slides.some((s) => !s.selectedPhoto || (s.cta && s.cta.enabled !== false && !state.cta));
    $("resultStatus").textContent = hasPlaceholders ? "Демо · не все ассеты" : "Собрано";
    $("resultStatus").className = "pill " + (hasPlaceholders ? "warn" : "ready");
    $("carouselName").textContent = doc.carousel_id;
    $("slidesTotal").textContent = doc.slides.length;
    $("textStatus").textContent = state.rewrite;
    $("thumbnailRail").replaceChildren();
    for (let i = 0; i < state.slides.length; i++) {
      const slide = state.slides[i];
      const button = document.createElement("button"); button.type = "button"; button.className = "thumb";
      const img = document.createElement("img"); img.src = slide.url; img.alt = "";
      const label = document.createElement("span"); label.textContent = "Слайд " + (i + 1);
      button.append(img, label); button.addEventListener("click", () => showSlide(i));
      $("thumbnailRail").append(button);
    }
    $("emptyResult").hidden = true; $("resultArea").hidden = false;
    showSlide(0); renderQA();
    $("resultArea").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function build() {
    if (state.busy) return;
    state.busy = true; $("buildButton").disabled = true;
    $("buildButton").firstElementChild.textContent = "Проверка JSON...";
    alertBox("");
    try {
      const raw = $("jsonInput").value;
      if (new Blob([raw]).size > 250_000) throw Error("JSON больше 250 КБ. Уменьшите размер.");
      const data = JSON.parse(raw);
      const doc = normalizeDocument(data);
      state.notes = [];
      state.document = doc;
      matchPhotos(doc);
      $("buildButton").firstElementChild.textContent = "Подготовка текста...";
      try {
        await rewriteTexts(doc);
      } catch (err) {
        state.rewrite = "Исходный · LLM не выполнен";
        buildNote("warn", "Перефразирование не выполнено: " + err.message + " Для предпросмотра использован исходный текст.");
      }
      clearRendered();
      for (let i = 0; i < doc.slides.length; i++) {
        $("buildButton").firstElementChild.textContent = "Сборка " + (i + 1) + "/" + doc.slides.length + "...";
        const slide = doc.slides[i];
        slide.blob = await renderSlide(slide, doc.width, doc.height);
        slide.url = URL.createObjectURL(slide.blob);
        state.urls.push(slide.url); state.slides.push(slide);
      }
      showResult(doc);
    } catch (err) {
      alertBox(err instanceof SyntaxError ? "JSON содержит синтаксическую ошибку: " + err.message : err.message);
    } finally {
      state.busy = false; $("buildButton").disabled = false;
      $("buildButton").firstElementChild.textContent = "Собрать карусель";
    }
  }

  const crcTable = (() => {
    const table = [];
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let j = 0; j < 8; j++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      table.push(c >>> 0);
    }
    return table;
  })();
  function crc32(bytes) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) crc = crcTable[(crc ^ bytes[i]) & 255] ^ (crc >>> 8);
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }
  function zipHeader(length, sig) {
    const buf = new Uint8Array(length), view = new DataView(buf.buffer); view.setUint32(0, sig, true);
    return { buf, view };
  }
  async function makeZip(entries) {
    const encoder = new TextEncoder(), local = [], central = [];
    let offset = 0;
    for (const entry of entries) {
      const name = encoder.encode(entry.name), data = new Uint8Array(await entry.blob.arrayBuffer());
      const crc = crc32(data);
      const a = zipHeader(30 + name.length, 0x04034b50);
      a.view.setUint16(4, 20, true); a.view.setUint16(6, 0x0800, true);
      a.view.setUint32(14, crc, true); a.view.setUint32(18, data.length, true); a.view.setUint32(22, data.length, true);
      a.view.setUint16(26, name.length, true); a.buf.set(name, 30);
      const b = zipHeader(46 + name.length, 0x02014b50);
      b.view.setUint16(4, 20, true); b.view.setUint16(6, 20, true); b.view.setUint16(8, 0x0800, true);
      b.view.setUint32(16, crc, true); b.view.setUint32(20, data.length, true); b.view.setUint32(24, data.length, true);
      b.view.setUint16(28, name.length, true); b.view.setUint32(42, offset, true); b.buf.set(name, 46);
      local.push(a.buf, data); central.push(b.buf); offset += a.buf.length + data.length;
    }
    const centralSize = central.reduce((s, n) => s + n.length, 0);
    const end = zipHeader(22, 0x06054b50);
    end.view.setUint16(8, entries.length, true); end.view.setUint16(10, entries.length, true);
    end.view.setUint32(12, centralSize, true); end.view.setUint32(16, offset, true);
    return new Blob([...local, ...central, end.buf], { type: "application/zip" });
  }
  function safeName(s) { return String(s).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 60) || "carousel"; }

  async function photosChanged(files) {
    if (files.length > 100) { alertBox("Для этой версии можно загрузить до 100 фотографий."); return; }
    const next = [];
    try {
      for (const file of files) {
        if (!safeFile(file)) throw Error("Допускаются JPG/PNG/WebP до 25 МБ на файл: " + file.name);
        const loaded = await loadImage(file);
        next.push({ filename: file.name, tags: fileTags(file.name), ...loaded });
      }
      for (const old of state.photos) URL.revokeObjectURL(old.url);
      state.photos = next;
      $("photoCount").textContent = next.length ? next.length + " фотографий" : "Пока не загружена";
      alertBox("");
    } catch (err) {
      next.forEach((p) => URL.revokeObjectURL(p.url)); alertBox(err.message);
    }
  }
  async function ctaChanged(file) {
    if (!file) return;
    if (!safeFile(file)) { alertBox("Скриншот CTA должен быть JPG/PNG/WebP до 25 МБ."); return; }
    try {
      const next = await loadImage(file);
      if (state.cta) URL.revokeObjectURL(state.cta.url);
      state.cta = next; $("ctaName").textContent = file.name; alertBox("");
    } catch (err) { alertBox(err.message); }
  }

  $("jsonInput").addEventListener("input", () => { $("jsonCount").textContent = $("jsonInput").value.length.toLocaleString("ru") + " символов"; });
  $("jsonFile").addEventListener("change", async (event) => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 250_000) { alertBox("JSON больше 250 КБ."); return; }
    $("jsonInput").value = await file.text(); $("jsonInput").dispatchEvent(new Event("input")); alertBox("");
  });
  for (const target of [$("jsonDrop"), $("jsonInput")]) {
    target.addEventListener("dragover", (e) => { e.preventDefault(); $("jsonDrop").classList.add("dragging"); });
    target.addEventListener("dragleave", () => $("jsonDrop").classList.remove("dragging"));
    target.addEventListener("drop", async (e) => {
      e.preventDefault(); $("jsonDrop").classList.remove("dragging");
      const f = e.dataTransfer.files[0];
      if (!f || !f.name.toLowerCase().endsWith(".json") || f.size > 250_000) { alertBox("Перетащи JSON-файл до 250 КБ."); return; }
      $("jsonInput").value = await f.text(); $("jsonInput").dispatchEvent(new Event("input")); alertBox("");
    });
  }
  $("demoButton").addEventListener("click", () => {
    $("jsonInput").value = JSON.stringify(DEMO, null, 2);
    $("jsonInput").dispatchEvent(new Event("input")); alertBox("");
  });
  $("photoFiles").addEventListener("change", (e) => photosChanged(Array.from(e.target.files)));
  $("ctaFile").addEventListener("change", (e) => ctaChanged(e.target.files[0]));
  $("rewriteToggle").addEventListener("change", () => { $("rewriteOptions").hidden = !$("rewriteToggle").checked; });
  $("showKey").addEventListener("click", () => {
    const input = $("keyInput"), show = input.type === "password"; input.type = show ? "text" : "password";
    $("showKey").textContent = show ? "Скрыть" : "Показать";
  });
  $("buildButton").addEventListener("click", build);
  $("prevSlide").addEventListener("click", () => showSlide(state.selected - 1));
  $("nextSlide").addEventListener("click", () => showSlide(state.selected + 1));
  window.addEventListener("keydown", (e) => {
    if (!state.slides.length || ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.key === "ArrowLeft") showSlide(state.selected - 1);
    if (e.key === "ArrowRight") showSlide(state.selected + 1);
  });
  $("downloadSlide").addEventListener("click", () => {
    if (!state.slides.length) return;
    downloadBlob(state.slides[state.selected].blob, safeName(state.document.carousel_id) + "_slide_" + String(state.selected + 1).padStart(2, "0") + ".png");
  });
  $("downloadAll").addEventListener("click", async () => {
    if (!state.slides.length) return;
    const btn = $("downloadAll"); btn.disabled = true; btn.textContent = "Упаковка ZIP...";
    try {
      const prefix = safeName(state.document.carousel_id);
      const entries = state.slides.map((s, i) => ({
        name: prefix + "/slide_" + String(i + 1).padStart(2, "0") + ".png", blob: s.blob
      }));
      const manifest = {
        carousel_id: state.document.carousel_id,
        slides: state.slides.map((s) => ({
          id: s.id, photo: s.selectedPhoto ? s.selectedPhoto.filename : null,
          texts: s.textBlocks.map((b) => b.text), tags: s.required, cta: Boolean(s.cta && s.cta.enabled !== false)
        })),
        rewrite: state.rewrite, qa: state.notes
      };
      entries.push({ name: prefix + "/manifest.json", blob: new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }) });
      downloadBlob(await makeZip(entries), prefix + ".zip");
    } catch (err) { alertBox("Не удалось создать ZIP: " + err.message); }
    finally { btn.disabled = false; btn.textContent = "↓ Скачать всю карусель ZIP"; }
  });
  fetch("/api/carousel-studio/status").then((response) => response.ok ? response.json() : null).then((data) => {
    if (data && data.configured) $("keyInput").placeholder = "Общий ключ OpenRouter уже настроен";
  }).catch(() => {});
})();
