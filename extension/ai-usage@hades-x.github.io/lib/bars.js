// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
/* exported VerticalGauge, QuotaBar, Sparkline */
// St.DrawingArea painters (GNOME 43 legacy API). Drawing only: no timers, no I/O.
// Each painter owns one St.DrawingArea and must be destroyed by its owner.
// Colours follow docs/DESIGN.md §1–§2.

const { St } = imports.gi;

var TRACK_GAUGE = [1, 1, 1, 0.18];
var TRACK_BAR = [1, 1, 1, 0.12];
var PACE_TICK = [1, 1, 1, 0.7];

function hexRgb(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m)
        return [0.5, 0.5, 0.5];
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

function clamp01(v) {
    if (typeof v !== 'number' || !isFinite(v))
        return 0;
    return Math.min(1, Math.max(0, v / 100));
}

// Rounded rectangle path (radius r clamped to half the smaller side).
function roundedRect(cr, x, y, w, h, r) {
    const rr = Math.max(0, Math.min(r, w / 2, h / 2));
    cr.newSubPath();
    cr.arc(x + w - rr, y + rr, rr, -Math.PI / 2, 0);
    cr.arc(x + w - rr, y + h - rr, rr, 0, Math.PI / 2);
    cr.arc(x + rr, y + h - rr, rr, Math.PI / 2, Math.PI);
    cr.arc(x + rr, y + rr, rr, Math.PI, 3 * Math.PI / 2);
    cr.closePath();
}

function setRgba(cr, rgba) {
    cr.setSourceRGBA(rgba[0], rgba[1], rgba[2], rgba[3] === undefined ? 1 : rgba[3]);
}

// Base for painters: one DrawingArea, `repaint` handler, `update()` triggers a repaint.
class Painter {
    constructor(width, height) {
        this.actor = new St.DrawingArea({ style_class: 'ai-usage-painter' });
        this.actor.set_width(width);
        this.actor.set_height(height);
        this._repaintId = this.actor.connect('repaint', this._onRepaint.bind(this));
        this._destroyed = false;
    }

    update() {
        if (!this._destroyed)
            this.actor.queue_repaint();
    }

    _onRepaint(area) {
        // GJS wraps the Cairo context; GNOME Shell releases it explicitly with $dispose().
        const cr = area.get_context();
        try {
            const [w, h] = area.get_surface_size();
            this.paint(cr, w, h);
        } finally {
            cr.$dispose();
        }
    }

    // Overridden by subclasses.
    paint(_cr, _w, _h) {
    }

    destroy() {
        if (this._destroyed)
            return;
        this._destroyed = true;
        if (this._repaintId) {
            this.actor.disconnect(this._repaintId);
            this._repaintId = 0;
        }
        this.actor.destroy();
        this.actor = null;
    }
}

// 4 x 14 px vertical gauge, fills bottom -> top (chip mini bar).
var VerticalGauge = class extends Painter {
    constructor(width, height) {
        super(width || 4, height || 14);
        this._percent = null;
        this._color = '#9a9996';
    }

    setValue(percent, color) {
        this._percent = percent;
        this._color = color;
        this.update();
    }

    paint(cr, w, h) {
        roundedRect(cr, 0, 0, w, h, 2);
        setRgba(cr, TRACK_GAUGE);
        cr.fill();

        const frac = clamp01(this._percent);
        if (frac <= 0)
            return;
        const fh = Math.max(1, h * frac);
        roundedRect(cr, 0, h - fh, w, fh, 2);
        cr.setSourceRGB(...hexRgb(this._color));
        cr.fill();
    }
};

// Horizontal quota bar (140 x 8 in the popup, 90 x 8 in the date card) with a
// 1 px pace tick at elapsed_percent.
var QuotaBar = class extends Painter {
    constructor(width, height) {
        super(width || 140, height || 8);
        this._percent = null;
        this._elapsed = null;
        this._color = '#9a9996';
        this._empty = false;
    }

    setValue({ percent, elapsed, color, empty }) {
        this._percent = percent;
        this._elapsed = elapsed;
        this._color = color;
        this._empty = !!empty;
        this.update();
    }

    paint(cr, w, h) {
        roundedRect(cr, 0, 0, w, h, h / 2);
        setRgba(cr, TRACK_BAR);
        cr.fill();

        const frac = this._empty ? 0 : clamp01(this._percent);
        if (frac > 0) {
            const fw = Math.max(h, w * frac);
            roundedRect(cr, 0, 0, Math.min(w, fw), h, h / 2);
            cr.setSourceRGB(...hexRgb(this._color));
            cr.fill();
        }

        if (typeof this._elapsed === 'number' && isFinite(this._elapsed)) {
            const x = Math.round(w * clamp01(this._elapsed)) + 0.5;
            cr.setLineWidth(1);
            setRgba(cr, PACE_TICK);
            cr.moveTo(x, -1);
            cr.lineTo(x, h + 1);
            cr.stroke();
        }
    }
};

// 14-bar sparkline (DESIGN §2). heights are 0..1, oldest first.
var Sparkline = class extends Painter {
    constructor(width, height) {
        super(width || 84, height || 14);
        this._heights = [];
        this._color = '#9a9996';
    }

    setValue(heights, color) {
        this._heights = Array.isArray(heights) ? heights : [];
        this._color = color;
        this.update();
    }

    paint(cr, w, h) {
        const n = this._heights.length;
        if (n === 0)
            return;
        const gap = 1;
        const bw = Math.max(1, (w - gap * (n - 1)) / n);
        cr.setSourceRGB(...hexRgb(this._color));
        for (let i = 0; i < n; i++) {
            const v = Math.max(0, Math.min(1, this._heights[i] || 0));
            const bh = Math.max(v > 0 ? 1 : 0, h * v);
            if (bh <= 0)
                continue;
            const x = i * (bw + gap);
            cr.rectangle(x, h - bh, bw, bh);
        }
        cr.fill();
    }
};
