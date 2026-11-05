"""Lossless PNG slide composition for Carousel Studio.

Only Pillow touches uploaded photographs.  JPEG/PNG/WebP sources are decoded once,
the requested text is rasterized directly over the image, and the output is PNG.
No video codec, browser canvas, color enhancement, or filter is involved.
"""
from __future__ import annotations

import base64
import binascii
import io
import math
import re
import struct
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps, PngImagePlugin, UnidentifiedImageError

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
DATA_URL = re.compile(r"data:image/(png|jpeg|webp);base64,([a-zA-Z0-9+/]+={0,2})\\Z")


class ImageError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _source(value: object) -> tuple[Image.Image, bytes | None, dict]:
    if not isinstance(value, str):
        raise ImageError("invalid_image", "Загрузите исходную фотографию.")
    match = DATA_URL.fullmatch(value)
    if not match or len(match[2]) > (MAX_SOURCE_BYTES * 4 // 3 + 8):
        raise ImageError("invalid_image", "Ожидается PNG/JPEG/WebP до 25 МБ.")
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error):
        raise ImageError("invalid_image", "Повреждена кодировка изображения.") from None
    if len(raw) > MAX_SOURCE_BYTES:
        raise ImageError("image_too_large", "Исходная фотография превышает 25 МБ.")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in {"PNG", "JPEG", "WEBP"}:
                raise ImageError("invalid_image", "Допускаются только PNG, JPEG и WebP.")
            width, height = opened.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ImageError("image_too_large", "Слишком большое разрешение фотографии.")
            opened.load()
            metadata = dict(opened.info)
            icc = metadata.get("icc_profile")
            image = ImageOps.exif_transpose(opened)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageError("invalid_image", "Не удалось декодировать исходную фотографию.") from exc

    if image.mode not in {"RGB", "RGBA"}:
        if icc:
            try:
                source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                target_profile = ImageCms.createProfile("sRGB")
                image = ImageCms.profileToProfile(image, source_profile, target_profile, outputMode="RGB")
                icc = ImageCms.ImageCmsProfile(target_profile).tobytes()
            except (OSError, ValueError, ImageCms.PyCMSError) as exc:
                raise ImageError("invalid_icc", "ICC-профиль не удалось корректно преобразовать в RGB.") from exc
        else:
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    return image, icc, metadata


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    bold = weight >= 600
    choices = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/Arialbd.ttf" if bold else "C:/Windows/Fonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in choices:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except OSError as exc:
            raise ImageError("font_missing", "Не найден системный шрифт Arial или DejaVu Sans.", 503) from exc


def _wrap(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont, limit: float) -> list[str]:
    lines: list[str] = []
    for paragraph in value.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if line and draw.textlength(candidate, font=font) > limit:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _number(value: object, default: float, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ImageError("invalid_layout", "Некорректное числовое значение макета.")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ImageError("invalid_layout", "Некорректное числовое значение макета.") from None
    if not math.isfinite(number):
        raise ImageError("invalid_layout", "Недопустимое значение в макете.")
    return max(minimum, min(maximum, number))


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    if image.size == (width, height):
        return image  # Pixel-for-pixel unchanged outside the letters.
    iw, ih = image.size
    source_ratio, target_ratio = iw / ih, width / height
    if source_ratio > target_ratio:
        crop_width = round(ih * target_ratio)
        left = (iw - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, ih))
    elif source_ratio < target_ratio:
        crop_height = round(iw / target_ratio)
        top = (ih - crop_height) // 2
        image = image.crop((0, top, iw, top + crop_height))
    # Only one resampling operation, and only if the canvas size actually differs.
    return image if image.size == (width, height) else image.resize((width, height), Image.Resampling.LANCZOS)


def _draw_cta(image: Image.Image, slide: dict, value: object) -> float | None:
    cta = slide.get("cta")
    if not isinstance(cta, dict) or cta.get("enabled") is False:
        return None
    if not value:
        raise ImageError("missing_cta", "Нужен скриншот CTA для этого слайда.")
    overlay, overlay_icc, _ = _source(value)
    base_icc = image.info.get("icc_profile")
    if overlay_icc and base_icc and overlay_icc != base_icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(overlay_icc))
            dst = ImageCms.ImageCmsProfile(io.BytesIO(base_icc))
            if overlay.mode == "RGBA":
                alpha = overlay.getchannel("A")
                colored = ImageCms.profileToProfile(overlay.convert("RGB"), src, dst, outputMode="RGB")
                colored.putalpha(alpha)
                overlay = colored
            else:
                overlay = ImageCms.profileToProfile(overlay, src, dst, outputMode="RGB")
        except (OSError, ValueError, ImageCms.PyCMSError) as exc:
            raise ImageError("invalid_icc", "Цветовой профиль скриншота CTA несовместим с фотографией.") from exc

    w, h = image.size
    percent = _number(cta.get("width_percent"), 42, 20, 80)
    target_width = max(1, round(w * percent / 100))
    target_height = max(1, min(round(h * .42), round(target_width * overlay.height / overlay.width)))
    target_width = max(1, round(target_height * overlay.width / overlay.height)) if target_height < round(target_width * overlay.height / overlay.width) else target_width
    overlay = overlay if overlay.size == (target_width, target_height) else overlay.resize(
        (target_width, target_height), Image.Resampling.LANCZOS)
    y = round(h * .11) if "top" in str(cta.get("position", "")) else round(h - target_height - h * .066)
    x = round((w - target_width) / 2)
    image.paste(overlay, (x, y), overlay.getchannel("A") if overlay.mode == "RGBA" else None)
    return y - 12


def _draw_text(image: Image.Image, slide: dict, cta_top: float | None, width_scale: float) -> None:
    blocks = slide.get("text_blocks")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 6:
        raise ImageError("invalid_text", "На слайде должно быть от 1 до 6 текстовых блоков.")
    draw = ImageDraw.Draw(image)
    w, h = image.size
    margin = round(w * .073)
    max_width = w - 2 * margin
    bottom = "bottom" in str(slide.get("layout", "")).lower() or "bottom" in str(
        blocks[0].get("position", "") if isinstance(blocks[0], dict) else "").lower()
    prepared = []
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str) or not block["text"].strip():
            raise ImageError("invalid_text", "У текстового блока нет содержимого.")
        if len(block["text"]) > 900:
            raise ImageError("invalid_text", "Текст превышает 900 символов.")
        font_weight = round(_number(block.get("font_weight"), 400, 100, 900))
        size = round(_number(block.get("font_size"), w * (.045 if block.get("type") == "body" else .066) / width_scale, 1, 1000) * width_scale)
        size = max(12, size)
        block_width = w * _number(block.get("width_percent"), 0, 0, 94) / 100 if block.get("width_percent") is not None else max_width
        if block_width < 1:
            raise ImageError("invalid_layout", "Недопустимая ширина текста.")
        while True:
            font = _font(size, font_weight)
            lines = _wrap(draw, block["text"], font, block_width)
            if len(lines) <= (7 if block.get("type") == "body" else 6) or size <= max(12, round(28 * width_scale)):
                break
            size = max(12, size - max(1, round(2 * width_scale)))
        step = round(size * 1.27)
        prepared.append((block, font, lines, step, len(lines) * step + round(h * .02)))
    top = margin + round(h * .04)
    if bottom:
        total = sum(item[4] for item in prepared)
        top = max(margin, min(h - total - margin, (cta_top if cta_top is not None else h) - total - margin))

    for block, font, lines, step, total in prepared:
        x_percent, y_percent = block.get("x"), block.get("y")
        x = w * _number(x_percent, 50, 0, 100) / 100
        y = h * _number(y_percent, 0, 0, 100) / 100 if y_percent is not None else top
        align = block.get("align", "center")
        if align not in ("left", "center", "right"):
            align = "center"
        anchor = {"left": "lt", "center": "mt", "right": "rt"}[align]
        color = str(block.get("color") or "#FFFFFF")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#FFFFFF"
        for line in lines:
            if line:
                draw.text((round(x), round(y)), line, font=font, fill=color, anchor=anchor)
            y += step
        top += total


def compose_slide(data: object) -> tuple[bytes, tuple[int, int]]:
    """Return a losslessly encoded PNG and its dimensions, never a video frame."""
    if not isinstance(data, dict) or not isinstance(data.get("slide"), dict):
        raise ImageError("invalid_payload", "Укажите фотографию и описание слайда.")
    image, icc, metadata = _source(data.get("image"))
    try:
        width = int(data.get("width"))
        height = int(data.get("height"))
        layout_width = int(data.get("layout_width", width))
    except (ValueError, TypeError):
        raise ImageError("invalid_size", "Некорректные размеры изображения.") from None
    if not 600 <= width <= 4096 or not 600 <= height <= 4096 or width * height > MAX_PIXELS:
        raise ImageError("invalid_size", "Разрешение PNG должно быть от 600 до 4096 пикселей.")
    if layout_width <= 0:
        raise ImageError("invalid_size", "Некорректный масштаб текста.")
    image = _fit(image, width, height)
    # Images retain their original ICC/color numbers, including Adobe RGB.
    # Only color-space modes incompatible with RGB PNG are converted via ImageCms.
    if icc:
        image.info["icc_profile"] = icc
    cta_top = _draw_cta(image, data["slide"], data.get("cta_image"))
    _draw_text(image, data["slide"], cta_top, width / layout_width)

    save_options = {"format": "PNG", "compress_level": 6}
    if icc:
        save_options["icc_profile"] = icc
    elif metadata.get("srgb") is not None:
        info = PngImagePlugin.PngInfo()
        info.add(b"sRGB", bytes([int(metadata["srgb"]) & 3]))
        save_options["pnginfo"] = info
    elif isinstance(metadata.get("gamma"), (int, float)) and metadata["gamma"] > 0:
        info = PngImagePlugin.PngInfo()
        info.add(b"gAMA", struct.pack(">I", round(metadata["gamma"] * 100000)))
        save_options["pnginfo"] = info
    with io.BytesIO() as buffer:
        image.save(buffer, **save_options)
        content = buffer.getvalue()
    if len(content) > MAX_OUTPUT_BYTES:
        raise ImageError("output_too_large", "PNG слайд превышает 64 МБ.", 413)
    return content, (width, height)
