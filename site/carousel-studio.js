(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { photos: [], cta: null, slides: [], selected: 0, notes: [], urls: [], document: null, rewrite: "Готовый JSON", busy: false, videoJob: null, pollTimer: null };
  const DEMO = {
    carousel_id: "depuff_lifestyle_001",
    format: "1080x1920",
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
    const rawFormat = doc.format || doc.size || "1080x1920";
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
      const layoutConfig = s.layout && typeof s.layout === "object" ? s.layout : {};
      const typography = s.typography && typeof s.typography === "object" ? s.typography : {};
      let source = s.text_blocks || s.textBlocks || s.texts;
      if (!source) source = s.text || s.headline || s.body || "";
      if (source && !Array.isArray(source) && typeof source === "object") source = [source];
      const textBlocks = asArray(source).map((b, bi) => {
        const obj = typeof b === "string" ? { text: b } : b;
        if (!obj || typeof (obj.text || obj.content || obj.original) !== "string" ||
            !(obj.text || obj.content || obj.original).trim())
          throw Error("Пустой текст на слайде " + id);
        const actualText = String(obj.text || obj.content || obj.original);
        const layoutX = obj.x ?? obj.x_percent ?? layoutConfig.x_percent;
        const layoutY = obj.y ?? obj.y_percent ?? layoutConfig.y_percent;
        if (actualText.length > 900) throw Error("Слишком длинный текст на слайде " + id);
        return {
          text: actualText.trim(),
          type: String(obj.type || (bi === 0 ? "headline" : "body")),
          position: String(obj.position || layoutConfig.text_position || s.text_position || ""),
          color: /^#[0-9a-f]{6}$/i.test(obj.color || typography.color || "") ? (obj.color || typography.color) : "#ffffff",
          max_chars: Number.isInteger(obj.max_chars) ? obj.max_chars : Math.max(40, Math.min(900, actualText.length * 2)),
          x: layoutX !== undefined && Number.isFinite(Number(layoutX)) ? clamp(Number(layoutX), 0, 100) : null,
          y: layoutY !== undefined && Number.isFinite(Number(layoutY)) ? clamp(Number(layoutY), 0, 100) : null,
          font_size: Number.isFinite(Number(obj.font_size ?? typography.font_size_px)) ? clamp(Number(obj.font_size ?? typography.font_size_px), 20, 150) : null,
          width_percent: Number.isFinite(Number(obj.width_percent ?? layoutConfig.max_width_percent)) ? clamp(Number(obj.width_percent ?? layoutConfig.max_width_percent), 30, 94) : null,
          align: ["left", "center", "right"].includes(obj.align || layoutConfig.text_align) ? (obj.align || layoutConfig.text_align) : "center",
          font_weight: Number.isFinite(Number(obj.font_weight ?? typography.font_weight)) ? clamp(Number(obj.font_weight ?? typography.font_weight), 100, 900) : 400
        };
      });
      if (!textBlocks.length || textBlocks.length > 6) throw Error("Слайд " + id + ": от 1 до 6 текстовых блоков.");
      const image = s.image_query || s.image || {};
      const required = tags(s.photo_tags || s.required_tags || image.required_tags || image.tags || []);
      const optional = tags(s.optional_tags || image.optional_tags || []);
      const forbidden = tags(s.forbidden_tags || image.forbidden_tags || []);
      const cta = s.cta || null;
      const duration = Number(s.duration_seconds ?? s.duration ?? 3);
      if (!Number.isFinite(duration) || duration < 0.25 || duration > 60) throw Error("Слайд " + id + ": длительность от 0.25 до 60 секунд.");
      return { id, role: String(s.role || s.goal || ""),
        layout: String(layoutConfig.text_position || (typeof s.layout === "string" ? s.layout : "text_top")), duration_seconds: duration,
        required, optional, forbidden, photoId: String(s.photo_id || s.photo_filename || image.asset_id || image.photo_id || ""),
        textBlocks, cta: cta && typeof cta === "object" ? cta : null,
        avatarId: String(s.avatar_id || doc.avatar_id || ""),
        selectedPhoto: null, blob: null, url: null
      };
    });
    const durationTotal = slides.reduce((sum, slide) => sum + slide.duration_seconds, 0);
    if (durationTotal > 600) throw Error("Суммарная длительность не может превышать 10 минут.");
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
        return (slide.photoId || slide.required.every((tag) => photo.tags.includes(tag))) &&
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
      let size = Math.round(b.font_size || w * (b.type === "body" ? .045 : .066));
      const blockWidth = b.width_percent ? w * b.width_percent / 100 : maxWidth;
      let lines = [];
      do {
        ctx.font = b.font_weight + " " + size + "px Arial, sans-serif";
        lines = linesOf(ctx, b.text, blockWidth);
        if (lines.length <= (b.type === "body" ? 7 : 6)) break;
        size -= 2;
      } while (size > 28);
      const lineHeight = Math.round(size * 1.27);
      return { ...b, blockWidth, lines, size, lineHeight, total: lines.length * lineHeight + Math.round(h * .02) };
    });
    let top = margin + Math.round(h * .04);
    if (defaultPos === "bottom") {
      const sum = blocks.reduce((a, b) => a + b.total, 0);
      top = Math.max(margin, Math.min(h - sum - margin, ctaTop !== null ? ctaTop - sum - margin : h - sum - margin));
    }
    ctx.textBaseline = "top";
    for (const b of blocks) {
      const custom = b.x !== null || b.y !== null;
      const x = b.x !== null ? w * b.x / 100 : w / 2;
      let y = b.y !== null ? h * b.y / 100 : top;
      ctx.textAlign = b.align;
      ctx.font = b.font_weight + " " + b.size + "px Arial, sans-serif";
      ctx.fillStyle = b.color;
      ctx.shadowColor = "transparent"; ctx.shadowBlur = 0;
      for (const line of b.lines) {
        if (ctx.measureText(line).width > b.blockWidth + 2) buildNote("warn", "Слайд " + slide.id + ": длинное слово может выйти за край.");
        if (y + b.lineHeight > h - margin || (ctaTop !== null && y + b.lineHeight > ctaTop - 18))
          buildNote("warn", "Слайд " + slide.id + ": текст может пересекаться с CTA или краем.");
        if (b.align === "center" && (x - ctx.measureText(line).width / 2 < margin || x + ctx.measureText(line).width / 2 > w - margin))
          buildNote("warn", "Слайд " + slide.id + ": текст расположен близко к краю.");
        ctx.fillText(line, x, y);
        y += b.lineHeight;
      }
      top += b.total;
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
    // CTA is the actual app screenshot placed directly over the photograph.
    // No artificial frame, drop shadow or decorative card.
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
      state.rewrite = "Из финального JSON";
      clearRendered();
      state.videoJob = null;
      $("videoResult").hidden = true;
      $("videoProgress").hidden = true;
      for (let i = 0; i < doc.slides.length; i++) {
        $("buildButton").firstElementChild.textContent = "Сборка " + (i + 1) + "/" + doc.slides.length + "...";
        const slide = doc.slides[i];
        slide.blob = await renderSlide(slide, doc.width, doc.height);
        slide.url = URL.createObjectURL(slide.blob);
        state.urls.push(slide.url); state.slides.push(slide);
      }
      showResult(doc);
      return true;
    } catch (err) {
      alertBox(err instanceof SyntaxError ? "JSON содержит синтаксическую ошибку: " + err.message : err.message);
      return false;
    } finally {
      state.busy = false; $("buildButton").disabled = false;
      $("buildButton").firstElementChild.textContent = "Собрать видео и просмотреть";
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

  function renderPrepPhotos() {
    const root = $("prepPhotoList");
    root.replaceChildren();
    for (const photo of state.photos) {
      const card = document.createElement("div");
      card.className = "prep-photo";
      const preview = document.createElement("img");
      preview.src = photo.url; preview.alt = "Фотография " + photo.filename;
      const label = document.createElement("span");
      label.textContent = photo.filename; label.title = photo.filename;
      const tag = document.createElement("small");
      tag.textContent = photo.tags.join(" · ") || "Без тегов";
      card.append(preview, label, tag);
      root.append(card);
    }
  }
  function setStage(stage) {
    const prepare = stage !== "render";
    $("prepareView").hidden = !prepare;
    $("renderWorkspace").hidden = prepare;
    $("tabPrepare").classList.toggle("active", prepare);
    $("tabRender").classList.toggle("active", !prepare);
    $("tabPrepare").setAttribute("aria-current", String(prepare));
    $("tabRender").setAttribute("aria-current", String(!prepare));
  }
  function syncStage() {
    setStage(String(window.location?.hash || "") === "#render" ? "render" : "prepare");
  }
  function preparePackage() {
    const source = $("briefInput").value.trim();
    if (!source || new Blob([source]).size > 250000)
      throw Error("Вставь исходный JSON размером до 250 КБ.");
    let brief;
    try { brief = JSON.parse(source); }
    catch (_) { throw Error("Исходная инструкция должна быть валидным JSON."); }
    if (!brief || typeof brief !== "object" || Array.isArray(brief))
      throw Error("Входная инструкция должна быть JSON-объектом.");
    const manifest = state.photos.map((photo) => ({
      photo_id: formatName(photo.filename),
      filename: photo.filename,
      tags: photo.tags,
      width: photo.img.naturalWidth,
      height: photo.img.naturalHeight
    }));
    return {
      schema_version: "content-machine-carousel-director-1",
      source_json: brief,
      photo_manifest: manifest,
      instruction: [
        "Подготовь ФИНАЛЬНЫЙ JSON для Content Machine Carousel Studio. Верни один JSON-объект и ничего кроме него.",
        "Используй загруженные мною фотографии как референсы. Имена фотографий строго соответствуют photo_manifest.",
        "Не придумывай несуществующие фото. У каждого слайда должен быть photo_id, совпадающий с именем photo_id или filename из photo_manifest.",
        "Сохрани точное число и порядок слайдов, TikTok-задумку, роль и последовательность эмоций.",
        "Тексты с mode=exact скопируй символ в символ; mode=rewrite перефразируй без изменения смысла; mode=generate создай в рамках описания, не выдумывай факты и результаты.",
        "Если исходный JSON задаёт точные параметры композиции, не меняй их.",
        "Формат вывода: {carousel_id, format:'1080x1920', language, assets:{photos:[{id,filename,tags}]}, slides:[{id,role,photo_id,photo_tags,layout,text_blocks:[{type,text,font_size,font_weight,color,x,y,width_percent,align}],duration_seconds,cta:{enabled,position,width_percent}}]}.",
        "Типографика по умолчанию: Arial, белый цвет #FFFFFF, обычная насыщенность font_weight=400, НЕТ обводки, НЕТ тени, НЕТ декоративных эффектов.",
        "Координаты x/y задавай в процентах (0–100). Длительность duration_seconds — число от 0.25 до 60. Видео: 9:16, прямые склейки, без звука.",
        "На слайдах CTA укажи cta.enabled=true; готовый скриншот приложения будет загружен отдельно в студию.",
        "Если невозможно найти нужное фото, укажи это в отдельном поле missing_assets, не подменяй эмоции и не выдумывай имена.",
        "Верни валидный JSON без Markdown и без пояснений."
      ].join(" ")
    };
  }
  function prepStatus(text, isError = false) {
    $("prepareStatus").textContent = text;
    $("prepareStatus").style.color = isError ? "#b5434a" : "#427359";
  }
  async function copyPreparation() {
    try {
      const request = preparePackage();
      const payload = "Сделай финальную JSON-инструкцию для Carousel Studio на основе следующего пакета. Фотографии прикреплю к этому сообщению отдельно.\n\n" +
        JSON.stringify(request, null, 2);
      if (!navigator.clipboard || !navigator.clipboard.writeText)
        throw Error("Браузер не поддерживает копирование. Скачай пакет задания JSON.");
      await navigator.clipboard.writeText(payload);
      prepStatus("Задание скопировано. Вставь его в наш чат и прикрепи те же фотографии отдельными файлами.");
    } catch (err) { prepStatus(err.message, true); }
  }
  function downloadPreparation() {
    try {
      const payload = preparePackage();
      const name = safeName(payload.source_json.carousel_id || "carousel_brief");
      downloadBlob(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }), name + "_brief.json");
      prepStatus("Пакет сохранён. Передай JSON в чат вместе с фотографиями.");
    } catch (err) { prepStatus(err.message, true); }
  }

  function readBlobDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("Невозможно прочитать кадр."));
      reader.onerror = () => reject(new Error("Не удалось загрузить PNG кадра."));
      reader.readAsDataURL(blob);
    });
  }
  function videoStatus(message, isError = false) {
    const box = $("videoProgress");
    box.hidden = false;
    box.className = "video-progress" + (isError ? " error" : "");
    box.textContent = message;
  }
  async function waitForVideo(id) {
    if (state.videoJob !== id) return;
    try {
      const response = await fetch("/api/carousel-video/jobs/" + id);
      const data = await response.json();
      if (!response.ok || !data.job) throw Error("Не удалось получить статус видеосборки.");
      const job = data.job;
      if (job.status === "failed") throw Error((job.error && job.error.message) || "Ошибка FFmpeg.");
      if (job.status === "succeeded") {
        const uri = "/api/carousel-video/files/" + id + "/video";
        $("videoPreview").src = uri;
        $("videoDownload").href = uri;
        $("videoDownload").download = safeName(state.document.carousel_id) + ".mp4";
        $("videoResult").hidden = false;
        $("renderVideoBtn").disabled = false;
        videoStatus("Готово: " + job.slides + " кадров, " + job.duration_seconds + " секунд. MP4 доступен для просмотра и скачивания.");
        $("videoResult").scrollIntoView({ behavior: "smooth", block: "nearest" });
        return;
      }
      videoStatus(job.status === "queued" ? "Видео в очереди FFmpeg…" : "FFmpeg кодирует MP4. Это может занять некоторое время.");
      window.setTimeout(() => waitForVideo(id), 1400);
    } catch (err) {
      videoStatus(err.message, true);
      $("renderVideoBtn").disabled = false;
    }
  }
  async function renderVideo() {
    if (!state.slides.length || !state.document || state.busy || state.videoBusy) return;
    const missing = state.slides.filter((s) => !s.selectedPhoto || (s.cta && s.cta.enabled !== false && !state.cta));
    if (missing.length) {
      videoStatus("Сначала добавь подходящие фотографии и скрин CTA. Отсутствуют ассеты на " +
        missing.length + " слайдах. Заглушки нельзя экспортировать как готовый ролик.", true);
      return;
    }
    state.videoBusy = true;
    $("renderVideoBtn").disabled = true;
    $("videoResult").hidden = true;
    try {
      const frames = [];
      for (let i = 0; i < state.slides.length; i++) {
        const slide = state.slides[i];
        videoStatus("Подготовка кадров для FFmpeg: " + (i + 1) + " / " + state.slides.length);
        const image = await readBlobDataUrl(slide.blob);
        frames.push({ image, duration_seconds: slide.duration_seconds });
      }
      videoStatus("Передача слайдов локальному видеорендереру…");
      const response = await fetch("/api/carousel-video/render", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-ContentMachine": "1" },
        body: JSON.stringify({ carousel_id: safeName(state.document.carousel_id), slides: frames })
      });
      const data = await response.json();
      if (!response.ok) throw Error((data.error && data.error.message) || "Ошибка сборки MP4.");
      if (!data.job || !data.job.id) throw Error("Не получен ID сборки.");
      state.videoJob = data.job.id;
      await waitForVideo(data.job.id);
    } catch (err) {
      videoStatus("Не удалось создать видео: " + err.message, true);
      $("renderVideoBtn").disabled = false;
    } finally { state.videoBusy = false; }
  }

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
      $("prepPhotoCount").textContent = next.length + " фото";
      renderPrepPhotos();
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

  $("briefInput").addEventListener("input", () => {
    $("briefCount").textContent = $("briefInput").value.length.toLocaleString("ru") + " символов";
  });
  $("briefFile").addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    if (file.size > 250000) { prepStatus("JSON больше 250 КБ.", true); return; }
    $("briefInput").value = await file.text();
    $("briefInput").dispatchEvent(new Event("input"));
  });
  $("briefDemo").addEventListener("click", () => {
    $("briefInput").value = JSON.stringify({
      carousel_id: "breakup_001", concept: "POV: расставание изменило девушку",
      format: "1080x1920", language: "en",
      slides: [
        { id: "1", role: "hook", photo_tags: ["sad", "bedroom"], text: { mode: "rewrite", original: "POV: The break up changed you" }, duration_seconds: 3 },
        { id: "2", role: "emotion", photo_tags: ["sad", "mirror"], text: { mode: "exact", original: "You barely recognize yourself" }, duration_seconds: 3 },
        { id: "3", role: "cta", photo_tags: ["happy"], text: { mode: "generate", instruction: "Мягкий CTA без недоказанных обещаний" }, cta: { enabled: true }, duration_seconds: 3 }
      ]
    }, null, 2);
    $("briefInput").dispatchEvent(new Event("input"));
  });
  $("prepareCopyBtn").addEventListener("click", copyPreparation);
  $("prepareManifestBtn").addEventListener("click", downloadPreparation);
  $("tabPrepare").addEventListener("click", () => setStage("prepare"));
  $("tabRender").addEventListener("click", () => setStage("render"));
  $("goRenderBtn").addEventListener("click", () => {
    setStage("render");
    if (window.location) window.location.hash = "#render";
  });
  window.addEventListener("hashchange", syncStage);
  syncStage();
  $("renderVideoBtn").addEventListener("click", renderVideo);
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
  $("prepPhotoFiles").addEventListener("change", (e) => photosChanged(Array.from(e.target.files)));
  $("ctaFile").addEventListener("change", (e) => ctaChanged(e.target.files[0]));
  $("buildButton").addEventListener("click", async () => {
    const succeeded = await build();
    if (succeeded) await renderVideo();
  });
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
  fetch("/api/carousel-video/status").then((response) => response.ok ? response.json() : null).then((data) => {
    if (data) $("videoEngineStatus").textContent = data.ffmpeg_available ? "FFmpeg найден" : "FFmpeg не найден";
  }).catch(() => { $("videoEngineStatus").textContent = "Статус FFmpeg неизвестен"; });
})();
