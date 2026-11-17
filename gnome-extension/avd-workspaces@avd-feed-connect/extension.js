import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {WorkspaceGroup} from 'resource:///org/gnome/shell/ui/workspaceAnimation.js';

const BUS_NAME = 'io.github.btaylor9273.AvdWorkspaces';
const OBJECT_PATH = '/io/github/btaylor9273/AvdWorkspaces';
const XML = `<node><interface name="${BUS_NAME}">
<method name="PlaceWindow"><arg type="s" direction="in" name="title"/>
<arg type="b" direction="out" name="placed"/></method>
</interface></node>`;

// A multi-monitor session maps one window per monitor, a few hundred
// milliseconds apart. The switch to the session's workspace waits for a quiet
// gap of this length after the last window arrived (see _activateWhenSettled).
const SETTLE_MS = 400;

export default class AvdWorkspaces extends Extension {
    enable() {
        this._placed = new WeakSet();
        this._pending = null;
        this._patchWorkspaceAnimation();
        this._object = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            connection => this._object.export(connection, OBJECT_PATH), null, null);
    }

    PlaceWindow(title) {
        // Exact random session token: never match user-visible resource names,
        // sandbox PIDs, arbitrary titles, credential prompts, or remote apps.
        if (!/^AVD-[0-9a-f]{32}$/.test(title))
            return false;
        const windows = global.get_window_actors().map(actor => actor.meta_window)
            .filter(window => window.get_title() === title &&
                window.get_window_type() === Meta.WindowType.NORMAL);
        if (windows.length === 0)
            return false;
        if (windows.every(window => this._placed.has(window)))
            return true;
        // Reuse the first session window's workspace for multi-monitor windows.
        let workspace = windows.find(window => this._placed.has(window))?.get_workspace();
        if (!workspace) {
            const manager = global.workspace_manager;
            const last = manager.get_workspace_by_index(manager.n_workspaces - 1);
            if (Meta.prefs_get_dynamic_workspaces()) {
                workspace = last.list_windows().length === 0 && last !== manager.get_active_workspace()
                    ? last : manager.append_new_workspace(false, global.get_current_time());
            } else {
                // Mutter persists num-workspaces when appending in fixed mode.
                // Reserve an existing empty workspace instead of changing it.
                for (let i = manager.n_workspaces - 1; i >= 0; i--) {
                    const candidate = manager.get_workspace_by_index(i);
                    if (candidate !== manager.get_active_workspace() && candidate.list_windows().length === 0) {
                        workspace = candidate;
                        break;
                    }
                }
                if (!workspace)
                    return false;
            }
        }
        let moved = false;
        for (const window of windows) {
            if (!this._placed.has(window)) {
                window.change_workspace(workspace);
                this._placed.add(window);
                moved = true;
            }
        }
        if (moved)
            this._activateWhenSettled(workspace);
        return true;
    }

    _activateWhenSettled(workspace) {
        // Switching the moment the first window is placed starts the shell's
        // switch animation while the session's other windows are still being
        // moved in, which is the very race _patchWorkspaceAnimation covers.
        // Switch once, after the windows stop arriving.
        if (this._pending)
            GLib.source_remove(this._pending);
        this._pending = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SETTLE_MS, () => {
            this._pending = null;
            // Dynamic workspaces drop the workspace if the session died meanwhile.
            if (workspace.index() >= 0)
                workspace.activate(global.get_current_time());
            return GLib.SOURCE_REMOVE;
        });
    }

    _patchWorkspaceAnimation() {
        // GNOME Shell's workspace-switch animation snapshots a workspace's
        // windows when the switch starts and throws ("TypeError: record is
        // undefined" in _syncStacking, gnome-shell#8114, still present in 46)
        // when another window lands on that workspace before the switch ends.
        // The new window then stays hidden and the desktop looks frozen. A
        // session window moved in by PlaceWindow, or remapped by the RDP client
        // on a display-layout change, is exactly such a window. Give it the
        // clone the snapshot would have made, then run the shell's own code.
        const proto = WorkspaceGroup?.prototype;
        if (!proto?._syncStacking || !proto._createClone || !proto._shouldShowWindow)
            return;
        const original = proto._syncStacking;
        proto._syncStacking = function (...args) {
            if (this._windowRecords) {
                for (const actor of global.get_window_actors()) {
                    if (this._shouldShowWindow(actor.meta_window) &&
                        !this._windowRecords.some(record => record.windowActor === actor))
                        this.add_child(this._createClone(actor));
                }
            }
            return original.apply(this, args);
        };
        this._unpatch = () => {
            proto._syncStacking = original;
        };
    }

    disable() {
        if (this._pending)
            GLib.source_remove(this._pending);
        this._pending = null;
        this._unpatch?.();
        this._unpatch = null;
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._object?.unexport();
        this._owner = null;
        this._object = null;
        this._placed = null;
        // GNOME handles empty dynamic workspaces. Never delete a workspace:
        // unrelated windows may have been moved into it by the user.
    }
}
