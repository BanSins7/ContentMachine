(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { photos: [], renderFiles: new Map(), renderPlan: null, cta: null, slides: [], selected: 0, notes: [], urls: [], document: null, rewrite: "Готовый JSON", busy: false, videoJob: null, pollTimer: null };
  const DEMO = {
    carousel_id: "relationship_reply_001",
    format: "1080x1920",
    language: "ru",
    assets: {photos: [
      {id: "photo_01", filename: "01.jpg", tags: ["day"]},
      {id: "photo_02", filename: "02.jpg", tags: ["night"]}
    ]},
    slides: [
      {id: "1", role: "hook", photo_id: "photo_01", text_blocks: [
        {type: "headline", text: "он: «я ради тебя\nброшу свою девушку»", x: 50, y: 17, font_size: 57, font_weight: 400, color: "#FFFFFF"}
      ], duration_seconds: 3},
      {id: "2", role: "payoff", photo_id: "photo_02", text_blocks: [
        {type: "headline", text: "я: «а ради меня лучше\nостанься с ней»", x: 50, y: 17, font_size: 57, font_weight: 400, color: "#FFFFFF"}
      ], duration_seconds: 3}
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

  function photoSlots(doc) {
    const manifest = assetManifest(doc);
    if (manifest.length > 60) throw Error("Слишком много фотографий в assets.photos.");
    const slots = [];
    const seen = new Set();
    const add = (id, filename, role = "photo", photoTags = [], title = "") => {
      id = String(id || "").trim();
      if (!id || id.length > 128) throw Error("Недопустимый photo_id в JSON.");
      if (seen.has(id.toLowerCase())) throw Error("Повторный ID фотографии: " + id);
      seen.add(id.toLowerCase());
      slots.push({id, filename: String(filename || "").split(/[\\/]/).pop(), role, tags: tags(photoTags),
        label: title || (role === "cta" ? "Скрин приложения (CTA)" : "Фото " + slots.length)});
      return id;
    };
    if (manifest.length) {
      manifest.forEach((item, i) => {
        if (!item || typeof item !== "object") throw Error("Ошибка в assets.photos[" + i + "].");
        const role = ["cta", "app_screenshot"].includes(String(item.role || item.kind || item.type).toLowerCase()) ? "cta" : "photo";
        add(item.id || item.photo_id || item.filename || "photo_" + (i + 1),
          item.filename || item.path || "", role, item.tags || item.photo_tags,
          role === "cta" ? "Скрин приложения (CTA)" : "Фото " + (i + 1) + (item.filename ? " · " + item.filename : ""));
      });
    } else {
      doc.slides.forEach((slide, i) => {
        const name = slide.photoId || "photo_" + String(i + 1).padStart(2,"0");
        if (!seen.has(name.toLowerCase()))
          add(name, /\.(png|jpe?g|webp)$/i.test(name) ? name : "", "photo", slide.required, "Слайд " + (i + 1));
      });
    }
    const backgroundSlots = slots.filter(s => s.role === "photo");
    doc.slides.forEach((slide, i) => {
      if (slide.photoId) {
        const match = backgroundSlots.find(slot => slot.id.toLowerCase() === slide.photoId.toLowerCase() ||
          (slot.filename && slot.filename.toLowerCase() === slide.photoId.toLowerCase()));
        if (!match) throw Error("Слайд " + slide.id + ": фото «" + slide.photoId + "» отсутствует в assets.photos.");
        slide.photoId = match.id;
      } else {
        const match = backgroundSlots[i];
        if (!match) throw Error("Для слайда " + slide.id + " не указана фотография.");
        slide.photoId = match.id;
      }
    });
    for (const slide of doc.slides) {
      if (!slide.cta || slide.cta.enabled === false) continue;
      const explicit = String(slide.cta.asset_id || slide.cta.photo_id || "");
      let ctaSlot = slots.find(slot => slot.role === "cta" &&
        (!explicit || slot.id === explicit || slot.filename === explicit));
      if (!ctaSlot) {
        const id = explicit || "app_screenshot";
        const existing = slots.find(slot => slot.id === id);
        if (existing && existing.role !== "cta") throw Error("CTA " + id + " совпадает с ID фоновой фотографии.");
        if (!existing) add(id, /\.(png|jpe?g|webp)$/i.test(id) ? id : "", "cta");
        ctaSlot = slots.find(slot => slot.id === id);
      }
      slide.cta.asset_id = ctaSlot.id;
    }
    const declared = Number(doc.photo_count ?? doc.required_photo_count);
    if (Number.isInteger(declared) && declared > 0 && declared !== slots.length)
      throw Error("В JSON указано " + declared + " фотографий, а список ассетов требует " + slots.length + ". Исправь JSON.");
    if (slots.length > 60) throw Error("Слишком много файлов (максимум 60).");
    return slots;
  }
  function matchPhotos(doc) {
    const slots = state.renderPlan ? state.renderPlan.slots : photoSlots(doc);
    const uploaded = state.renderFiles;
    state.cta = null;
    for (const slot of slots) {
      if (slot.role === "cta") {
        state.cta = uploaded.get(slot.id) || null;
        continue;
      }
    }
    for (const slide of doc.slides) {
      const photo = uploaded.get(slide.photoId);
      slide.selectedPhoto = photo || null;
      if (!photo) buildNote("warn", "Слайд " + slide.id + ": фотография не загружена.");
      if (slide.cta && slide.cta.enabled !== false && !uploaded.has(slide.cta.asset_id))
        buildNote("warn", "Слайд " + slide.id + ": не загружен скриншот CTA.");
    }
  }
  function showPhotoSlots() {
    const root = $("renderPhotoSlots");
    root.replaceChildren();
    const plan = state.renderPlan;
    if (!plan) return;
    plan.slots.forEach((slot, index) => {
      const photo = state.renderFiles.get(slot.id);
      const card = document.createElement("div");
      card.className = "render-photo-slot" + (photo ? " loaded" : "");
      const preview = document.createElement("div");
      preview.className = "render-photo-preview";
      if (photo) {
        const img = document.createElement("img");
        img.src = photo.url; img.alt = "Фото для " + slot.label;
        preview.append(img);
      } else {
        const icon = document.createElement("span"); icon.textContent = String(index + 1).padStart(2, "0");
        preview.append(icon);
      }
      const info = document.createElement("div"); info.className = "render-photo-info";
      const label = document.createElement("strong"); label.textContent = slot.label;
      const hint = document.createElement("small");
      hint.textContent = photo ? photo.filename : slot.filename ? "Ожидается: " + slot.filename : "Ожидает файл";
      info.append(label, hint);
      const pick = document.createElement("label"); pick.className = "small-button";
      pick.textContent = photo ? "Заменить" : "Выбрать";
      const input = document.createElement("input");
      input.type = "file"; input.accept = "image/png,image/jpeg,image/webp";
      input.hidden = true;
      input.addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (file) await assignRenderPhoto(slot.id, file);
      });
      pick.append(input);
      card.append(preview, info, pick);
      root.append(card);
    });
  }
  function refreshPhotoState() {
    const plan = state.renderPlan;
    const count = plan ? plan.slots.length : 0;
    const loaded = plan ? plan.slots.filter(s => state.renderFiles.has(s.id)).length : 0;
    $("photoCount").textContent = plan ? loaded + " / " + count : "0 / —";
    $("buildButton").disabled = !plan || loaded !== count || state.busy;
    $("photoFiles").disabled = !plan || state.busy;
    $("photoUploadZone").classList.toggle("disabled-upload", !plan || state.busy);
    showPhotoSlots();
  }
  function invalidateRendered() {
    if (state.slides.length) clearRendered();
    state.document = null;
    state.videoJob = null;
    $("videoResult").hidden = true;
    $("videoProgress").hidden = true;
    $("videoPreview").removeAttribute("src");
    $("resultStatus").textContent = "Нет результата";
    $("resultStatus").className = "pill";
  }
  function updateRenderPlan() {
    invalidateRendered();
    const source = $("jsonInput").value.trim();
    const note = $("jsonValidation");
    if (!source) {
      state.renderPlan = null;
      note.textContent = "Вставь JSON, чтобы узнать, сколько фотографий требуется.";
      note.className = "validation-note";
      $("photoRequirement").textContent = "Количество и порядок фотографий появятся после проверки JSON.";
      refreshPhotoState();
      return;
    }
    try {
      if (new Blob([source]).size > 250000) throw Error("JSON превышает 250 КБ.");
      const data = JSON.parse(source);
      if (data.schema_version === "contentmachine.carousel_creative_brief.v1")
        throw Error("Это исходный creative brief. Вставь итоговый JSON, подготовленный для видеосборки.");
      const doc = normalizeDocument(data);
      // Keep the source object so every declared asset rule is used consistently.
      doc.photo_count = data.photo_count;
      doc.required_photo_count = data.required_photo_count;
      const slots = photoSlots(doc);
      const key = JSON.stringify(slots.map(s => [s.id, s.filename, s.role]));
      if (!state.renderPlan || state.renderPlan.key !== key) {
        for (const photo of state.renderFiles.values()) URL.revokeObjectURL(photo.url);
        state.renderFiles.clear();
      }
      state.renderPlan = {key, doc, slots};
      const cta = slots.filter(s => s.role === "cta").length;
      note.textContent = "JSON корректен · " + doc.slides.length + " слайд(ов) · " + slots.length + " файл(ов)";
      note.className = "validation-note good";
      const endings = slots.length % 100, last = slots.length % 10;
      const word = endings >= 11 && endings <= 14 ? "файлов" : last === 1 ? "файл" : [2,3,4].includes(last) ? "файла" : "файлов";
      $("photoRequirement").textContent = "Нужно загрузить " + slots.length + " " + word +
        (cta ? " (включая " + cta + " скриншот CTA)" : "") + ".";
      refreshPhotoState();
      alertBox("");
    } catch (err) {
      state.renderPlan = null;
      note.textContent = "Ошибка JSON: " + err.message;
      note.className = "validation-note invalid";
      $("photoRequirement").textContent = "Исправь JSON, чтобы перейти к фотографиям.";
      refreshPhotoState();
    }
  }
  async function assignRenderPhoto(slotId, file) {
    if (!state.renderPlan) return;
    if (!safeFile(file)) { alertBox("Допускаются JPG/PNG/WebP до 25 МБ: " + file.name); return; }
    try {
      const loaded = await loadImage(file);
      const previous = state.renderFiles.get(slotId);
      state.renderFiles.set(slotId, { ...loaded, file, filename: file.name, tags: fileTags(file.name), id: slotId });
      if (previous) URL.revokeObjectURL(previous.url);
      invalidateRendered();
      refreshPhotoState();
      alertBox("");
    } catch (err) { alertBox(err.message); }
  }
  async function uploadRenderPhotos(files) {
    const plan = state.renderPlan;
    if (!plan) { alertBox("Сначала вставь корректный JSON."); return; }
    if (!files.length) return;
    if (files.length > plan.slots.length) {
      alertBox("JSON требует " + plan.slots.length + " файлов, выбрано " + files.length + "."); return;
    }
    const fresh = [];
    const fullReplacement = files.length === plan.slots.length;
    const used = new Set();
    const target = fullReplacement ? new Map() : new Map(state.renderFiles);
    try {
      for (const file of files) {
        if (!safeFile(file)) throw Error("Допускаются JPG/PNG/WebP до 25 МБ: " + file.name);
      }
      // Match known filenames first. Remaining files fill free slots in selection order.
      for (const file of files) {
        const wanted = plan.slots.find(s => !used.has(s.id) && (
          (s.filename && s.filename.toLowerCase() === file.name.toLowerCase()) ||
          s.id.toLowerCase() === file.name.toLowerCase() ||
          s.id.toLowerCase() === formatName(file.name)));
        if (wanted) { used.add(wanted.id); fresh.push({slot: wanted, file}); }
        else fresh.push({slot: null, file});
      }
      for (const item of fresh) {
        if (item.slot) continue;
        const free = plan.slots.find(slot => !used.has(slot.id) && !target.has(slot.id));
        if (!free) throw Error("Все места уже заняты. Выбери полный комплект или замени кадр отдельно.");
        used.add(free.id); item.slot = free;
      }
      const decoded = [];
      try {
        for (const item of fresh) {
          const loaded = await loadImage(item.file);
          decoded.push({slot: item.slot, photo: {
            ...loaded, file: item.file, filename: item.file.name, tags: fileTags(item.file.name), id: item.slot.id
          }});
        }
      } catch (err) {
        for (const item of decoded) URL.revokeObjectURL(item.photo.url);
        throw err;
      }
      for (const item of decoded) target.set(item.slot.id, item.photo);
      for (const [id, old] of state.renderFiles.entries()) {
        if (target.get(id) !== old) URL.revokeObjectURL(old.url);
      }
      state.renderFiles = target;
      invalidateRendered();
      refreshPhotoState();
      alertBox("");
    } catch (err) { alertBox(err.message); }
  }

  // Final photos are NEVER painted into browser Canvas: native Pillow overlays
  // text on the decoded original and saves a lossless, ICC-tagged PNG.
  async function renderSlide(slide, w, h, layoutWidth) {
    if (!slide.selectedPhoto || !slide.selectedPhoto.file)
      throw Error("Не загружена исходная фотография для слайда " + slide.id);
    const payload = {
      image: await readBlobDataUrl(slide.selectedPhoto.file),
      width: w, height: h, layout_width: layoutWidth,
      slide: {layout: slide.layout, text_blocks: slide.textBlocks, cta: slide.cta},
    };
    if (slide.cta && slide.cta.enabled !== false) {
      if (!state.cta || !state.cta.file)
        throw Error("Не загружен скриншот CTA для слайда " + slide.id);
      payload.cta_image = await readBlobDataUrl(state.cta.file);
    }
    const response = await fetch("/api/carousel-video/compose", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-ContentMachine": "1"},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok || !result.slide || !result.slide.id)
      throw Error((result.error && result.error.message) || "Не удалось собрать PNG через Pillow.");
    const frame = await fetch(result.slide.url);
    if (!frame.ok) throw Error("Не удалось получить готовый PNG.");
    const blob = await frame.blob();
    return {blob, pngRef: result.slide.id};
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
      doc.photo_count = data.photo_count;
      doc.required_photo_count = data.required_photo_count;
      const slots = photoSlots(doc);
      if (!state.renderPlan || state.renderPlan.slots.length !== slots.length ||
          slots.some(slot => !state.renderFiles.has(slot.id)))
        throw Error("Сначала загрузи все фотографии в шаге 2.");
      state.notes = [];
      state.document = doc;
      matchPhotos(doc);
      state.rewrite = "Из финального JSON";
      clearRendered();
      state.videoJob = null;
      $("videoPreview").removeAttribute("src");
      $("videoResult").hidden = true;
      $("videoProgress").hidden = true;
      // Keep original dimensions when every source already has the required
      // aspect ratio and resolution. Otherwise Pillow crops/resizes ONCE to JSON.
      const first = doc.slides[0].selectedPhoto.img;
      const nativeW = first.naturalWidth, nativeH = first.naturalHeight;
      const nativeFits = nativeW >= 600 && nativeH >= 600 &&
        nativeW <= 4096 && nativeH <= 4096 &&
        Math.abs(nativeW / nativeH - doc.width / doc.height) < 0.001 &&
        doc.slides.every((s) => s.selectedPhoto.img.naturalWidth === nativeW &&
          s.selectedPhoto.img.naturalHeight === nativeH);
      const width = nativeFits ? nativeW : doc.width;
      const height = nativeFits ? nativeH : doc.height;
      for (let i = 0; i < doc.slides.length; i++) {
        $("buildButton").firstElementChild.textContent = "Pillow: PNG " + (i + 1) + "/" + doc.slides.length + "...";
        const slide = doc.slides[i];
        const composed = await renderSlide(slide, width, height, doc.width);
        slide.blob = composed.blob;
        slide.pngRef = composed.pngRef;
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
      refreshPhotoState();
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
        if (slide.pngRef) frames.push({image_ref: slide.pngRef, duration_seconds: slide.duration_seconds});
        else frames.push({image: await readBlobDataUrl(slide.blob), duration_seconds: slide.duration_seconds});
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
      $("prepPhotoCount").textContent = next.length + " фото";
      renderPrepPhotos();
      alertBox("");
    } catch (err) {
      next.forEach((p) => URL.revokeObjectURL(p.url)); alertBox(err.message);
    }
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
  $("jsonInput").addEventListener("input", () => {
    $("jsonCount").textContent = $("jsonInput").value.length.toLocaleString("ru") + " символов";
    updateRenderPlan();
  });
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
  $("photoFiles").addEventListener("change", async (e) => {
    await uploadRenderPhotos(Array.from(e.target.files));
    e.target.value = "";
  });
  $("prepPhotoFiles").addEventListener("change", (e) => photosChanged(Array.from(e.target.files)));
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
          texts: s.textBlocks.map((b) => b.text), duration_seconds: s.duration_seconds,
          tags: s.required, cta: Boolean(s.cta && s.cta.enabled !== false)
        })),
        rewrite: state.rewrite, qa: state.notes
      };
      entries.push({ name: prefix + "/manifest.json", blob: new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }) });
      downloadBlob(await makeZip(entries), prefix + ".zip");
    } catch (err) { alertBox("Не удалось создать ZIP: " + err.message); }
    finally { btn.disabled = false; btn.textContent = "↓ Скачать всю карусель ZIP"; }
  });
  updateRenderPlan();
  fetch("/api/carousel-video/status").then((response) => response.ok ? response.json() : null).then((data) => {
    if (data) $("videoEngineStatus").textContent = data.ffmpeg_available ? "FFmpeg найден" : "FFmpeg не найден";
  }).catch(() => { $("videoEngineStatus").textContent = "Статус FFmpeg неизвестен"; });
})();
