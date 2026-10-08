// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
/* exported StateLoader */
// Reads state.json asynchronously and reloads on directory changes.
// Watching the DIRECTORY (not the file) because the collector writes atomically
// (tmp + os.replace), which replaces the inode. Events are debounced.

const { Gio, GLib } = imports.gi;

const DEBOUNCE_MS = 300;
const FALLBACK_RELOAD_S = 60;     // backstop if inotify misses something
const DIR_RETRY_S = 15;           // retry monitor creation while the dir is missing

function defaultStatePath() {
    return GLib.build_filenamev([GLib.get_user_runtime_dir(), 'ai-usage', 'state.json']);
}

function decodeBytes(bytes) {
    if (typeof TextDecoder !== 'undefined')
        return new TextDecoder().decode(bytes);
    return imports.byteArray.toString(bytes);
}

// onUpdate({ status: 'ok'|'missing'|'invalid', state: object|null, path })
var StateLoader = class {
    constructor(getPath, onUpdate) {
        this._getPath = getPath;     // () => string (may be '' -> default)
        this._onUpdate = onUpdate;
        this._monitor = null;
        this._monitorId = 0;
        this._debounceId = 0;
        this._retryId = 0;
        this._fallbackId = 0;
        this._cancellable = null;
        this._seq = 0;
        this._lastDir = null;
        this._started = false;
    }

    path() {
        const p = this._getPath();
        return p && p.length > 0 ? p : defaultStatePath();
    }

    start() {
        if (this._started)
            return;
        this._started = true;
        this._cancellable = new Gio.Cancellable();
        this._ensureMonitor();
        this._fallbackId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, FALLBACK_RELOAD_S, () => {
            this._ensureMonitor();
            this.reload();
            return GLib.SOURCE_CONTINUE;
        });
        this.reload();
    }

    // Path (or its directory) changed in settings: rewatch and reload.
    restart() {
        this._stopMonitor();
        this._ensureMonitor();
        this.reload();
    }

    stop() {
        this._started = false;
        if (this._fallbackId) {
            GLib.source_remove(this._fallbackId);
            this._fallbackId = 0;
        }
        if (this._retryId) {
            GLib.source_remove(this._retryId);
            this._retryId = 0;
        }
        if (this._debounceId) {
            GLib.source_remove(this._debounceId);
            this._debounceId = 0;
        }
        this._stopMonitor();
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        this._onUpdate = null;
    }

    _stopMonitor() {
        if (this._monitor) {
            if (this._monitorId) {
                this._monitor.disconnect(this._monitorId);
                this._monitorId = 0;
            }
            this._monitor.cancel();
            this._monitor = null;
        }
        this._lastDir = null;
    }

    _ensureMonitor() {
        if (!this._started)
            return;
        const file = Gio.File.new_for_path(this.path());
        const dir = file.get_parent();
        if (!dir)
            return;
        const dirPath = dir.get_path();
        if (this._monitor && this._lastDir === dirPath)
            return;
        this._stopMonitor();

        if (!GLib.file_test(dirPath, GLib.FileTest.IS_DIR)) {
            // Directory not created yet (collector not run): poll for it.
            if (!this._retryId) {
                this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, DIR_RETRY_S, () => {
                    if (!this._started || !GLib.file_test(dirPath, GLib.FileTest.IS_DIR))
                        return GLib.SOURCE_CONTINUE;
                    this._retryId = 0;
                    this._ensureMonitor();
                    this.reload();
                    return GLib.SOURCE_REMOVE;
                });
            }
            return;
        }

        try {
            this._monitor = dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, this._cancellable);
        } catch (e) {
            this._monitor = null;
            return;
        }
        this._lastDir = dirPath;
        this._monitorId = this._monitor.connect('changed', (_m, child, otherFile, eventType) => {
            // Only events that touch the watched file (rename/create/change) matter; the
            // collector's state.json.tmp writes are ignored. A null child means the
            // directory itself changed: reload to be safe.
            const target = Gio.File.new_for_path(this.path()).get_basename();
            const names = [child, otherFile]
                .filter(f => f)
                .map(f => f.get_basename());
            if (child && names.indexOf(target) < 0)
                return;
            if (eventType === Gio.FileMonitorEvent.PRE_UNMOUNT ||
                eventType === Gio.FileMonitorEvent.UNMOUNTED)
                return;
            this._scheduleReload();
        });
    }

    _scheduleReload() {
        if (this._debounceId)
            GLib.source_remove(this._debounceId);
        this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._debounceId = 0;
            this.reload();
            return GLib.SOURCE_REMOVE;
        });
    }

    reload() {
        if (!this._started || !this._cancellable)
            return;
        const path = this.path();
        const seq = ++this._seq;
        const file = Gio.File.new_for_path(path);
        file.load_contents_async(this._cancellable, (f, res) => {
            let contents = null;
            let ok = false;
            try {
                [ok, contents] = f.load_contents_finish(res);
            } catch (e) {
                // Cancelled (stop) or missing file.
                if (e.matches && e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                    this._deliver(seq, { status: 'missing', state: null, path });
                else if (!(e.matches && e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)))
                    this._deliver(seq, { status: 'invalid', state: null, path });
                return;
            }
            if (!ok) {
                this._deliver(seq, { status: 'invalid', state: null, path });
                return;
            }
            let state = null;
            try {
                state = JSON.parse(decodeBytes(contents));
            } catch (e) {
                this._deliver(seq, { status: 'invalid', state: null, path });
                return;
            }
            if (!state || typeof state !== 'object' || Array.isArray(state)) {
                this._deliver(seq, { status: 'invalid', state: null, path });
                return;
            }
            this._deliver(seq, { status: 'ok', state, path });
        });
    }

    _deliver(seq, result) {
        // Drop results superseded by a newer read, and anything after stop().
        if (seq !== this._seq || !this._onUpdate)
            return;
        this._onUpdate(result);
    }
};
