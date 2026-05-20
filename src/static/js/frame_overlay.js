"use strict";
/**
 * Pure read-only frame & bodypart drawing for the Test-set Picker.
 *
 * This module is intentionally NEW (not extracted from frame_labeler.js).
 * The labeler's draw code remains untouched so its interactive behavior
 * stays exactly as it was. The picker uses these simpler pure functions
 * to render frames and bodypart dots in display-only mode.
 *
 * Exports:
 *   drawFrame(ctx, image, opts?) → placement | undefined
 *   drawBodyparts(ctx, labels, palette, placement, opts?)
 */

/**
 * Paint an image into a canvas, sized to fit the canvas's pixel dimensions.
 *
 * Returns an object describing where the image was drawn:
 *   { dx, dy, dw, dh, iw, ih }
 * — useful for mapping image-space coordinates to canvas-space later.
 *
 * Returns undefined if no image is supplied or the image has no dimensions.
 */
export function drawFrame(ctx, image, opts = {}) {
    const { fit = "contain" } = opts;
    const cw = ctx.canvas.width;
    const ch = ctx.canvas.height;
    ctx.clearRect(0, 0, cw, ch);
    if (!image) return undefined;
    const iw = image.naturalWidth || image.width;
    const ih = image.naturalHeight || image.height;
    if (!iw || !ih) return undefined;
    let dw = cw, dh = ch, dx = 0, dy = 0;
    if (fit === "contain") {
        const s = Math.min(cw / iw, ch / ih);
        dw = iw * s; dh = ih * s;
        dx = (cw - dw) / 2;
        dy = (ch - dh) / 2;
    }
    ctx.drawImage(image, dx, dy, dw, dh);
    return { dx, dy, dw, dh, iw, ih };
}

/**
 * Paint bodypart dots + optional name labels on top of a frame.
 *
 * labels    : { "<bodypart>": [x, y] | null }   (coords in image space)
 * palette   : { "<bodypart>": "#hexcolor" }
 * placement : { dx, dy, dw, dh, iw, ih }        (returned by drawFrame)
 * opts      : { markerSize?: number, showNames?: boolean }
 */
export function drawBodyparts(ctx, labels, palette, placement, opts = {}) {
    const { markerSize = 4, showNames = true } = opts;
    if (!labels || !placement) return;
    const { dx, dy, dw, dh, iw, ih } = placement;
    const sx = dw / iw;
    const sy = dh / ih;

    ctx.save();
    ctx.lineWidth = Math.max(1, Math.floor(markerSize / 3));
    for (const [bp, xy] of Object.entries(labels)) {
        if (!xy) continue;
        const [imgX, imgY] = xy;
        if (typeof imgX !== "number" || typeof imgY !== "number") continue;
        const canvasX = dx + imgX * sx;
        const canvasY = dy + imgY * sy;
        const color = (palette && palette[bp]) || "#ff5050";

        // Dot
        ctx.beginPath();
        ctx.arc(canvasX, canvasY, markerSize, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = "#0008";
        ctx.stroke();

        // Optional name label with a contrasting backdrop
        if (showNames) {
            const fontPx = Math.max(10, markerSize * 2);
            ctx.font = `${fontPx}px var(--mono, monospace)`;
            ctx.textBaseline = "top";
            const textWidth = ctx.measureText(bp).width;
            ctx.fillStyle = "#000c";
            ctx.fillRect(canvasX + markerSize + 1, canvasY - 2,
                         textWidth + 6, fontPx + 3);
            ctx.fillStyle = color;
            ctx.fillText(bp, canvasX + markerSize + 4, canvasY - 1);
        }
    }
    ctx.restore();
}
