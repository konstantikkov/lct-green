/**
 * Non-reactive bridge between the viewer (60fps pointer events) and the
 * status bar. Writing through React state for every mousemove would thrash;
 * instead the status bar registers DOM nodes and the viewer writes directly.
 */
export const hud = {
  coordsEl: null as HTMLElement | null,
  zoomEl: null as HTMLElement | null,

  setCursor(x: number, y: number) {
    if (this.coordsEl) {
      this.coordsEl.textContent = `X ${x.toFixed(2)} m   Y ${y.toFixed(2)} m`;
    }
  },
  setZoom(pxPerMeter: number) {
    if (this.zoomEl) {
      this.zoomEl.textContent =
        pxPerMeter >= 1 ? `${pxPerMeter.toFixed(1)} px/m` : `1:${(1 / pxPerMeter).toFixed(0)} m/px`;
    }
  },
};
