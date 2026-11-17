"""Run the real extension's placement and animation guard with deterministic Mutter objects."""
import shutil
import subprocess
from pathlib import Path
import pytest

# Runtime-neutral: Node.js or GNOME's own gjs, whichever the machine has.
PRELUDE = '''
globalThis.global = globalThis;
const assert = {
    equal(a, b, m) { if (a !== b) throw new Error(m ?? `${String(a)} !== ${String(b)}`); },
    notEqual(a, b, m) { if (a === b) throw new Error(m ?? `${String(a)} === ${String(b)}`); },
    ok(v, m) { if (!v) throw new Error(m ?? 'expected truthy'); },
    deepEqual(a, b, m) { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m ?? `${x} !== ${y}`); },
};
const Meta = {WindowType: {NORMAL: 0}, prefs_get_dynamic_workspaces: () => true};
// Timers are collected and fired by the test, never on their own.
const timers = [];
const GLib = {PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
    timeout_add(priority, ms, fn) { const id = timers.length + 1; timers.push({id, ms, fn}); return id; },
    source_remove(id) { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); }};
function fireTimers() { const due = timers.splice(0); for (const t of due) t.fn(); return due.length; }
class Extension {}
const workspaces = [];
function workspace() {
    const ws = {windows: [], activations: 0, list_windows() {return this.windows},
        index() {return workspaces.indexOf(this)}, activate() {this.activations++}};
    workspaces.push(ws); return ws;
}
const local = workspace();
const trailing = workspace();
const windows = [];
global.get_current_time = () => 1;
// Window actors are stable objects in the shell; keep one per window.
global.get_window_actors = () => windows.map(w => (w.actor ??= {meta_window: w}));
global.workspace_manager = {get n_workspaces() {return workspaces.length},
    get_workspace_by_index: i => workspaces[i], get_active_workspace: () => local,
    append_new_workspace: () => workspace()};
function window(title) {
    const w = {ws: local, get_title: () => title, get_window_type: () => 0,
        get_workspace() {return this.ws},
        change_workspace(ws) {this.ws = ws; ws.windows.push(this)}};
    windows.push(w); return w;
}
// Stand-in for GNOME Shell's WorkspaceGroup: the real one throws when a
// window has no record. The original is kept so the test can see it ran.
const stackingCalls = [];
const WorkspaceGroup = {prototype: {
    _syncStacking() { stackingCalls.push(this._windowRecords.length); for (const r of this._windowRecords) if (!r) throw new TypeError('record is undefined'); },
    _createClone(windowActor) { const record = {windowActor, clone: {of: windowActor}}; this._windowRecords.push(record); return record.clone; },
    _shouldShowWindow(w) { return true; },
}};
'''

PLACEMENT = '''
const bridge = new AvdWorkspaces();
bridge._placed = new WeakSet();
bridge._pending = null;
const first = 'AVD-' + 'a'.repeat(32), second = 'AVD-' + 'b'.repeat(32);
assert.equal(bridge.PlaceWindow(first), false);
assert.equal(workspaces.length, 2); // Failed/missing window creates nothing.
const unrelated = window('Finance Desktop');
const one = window(first);
assert.equal(bridge.PlaceWindow('Finance Desktop'), false);
assert.equal(bridge.PlaceWindow(first), true);
assert.equal(one.ws, trailing);
assert.equal(unrelated.ws, local);
// The switch is deferred: nothing is activated until the windows settle.
assert.equal(trailing.activations, 0);
assert.equal(timers.length, 1);
bridge.PlaceWindow(first); // Idempotent reconnect/focus: no new timer.
assert.equal(workspaces.length, 2);
assert.equal(timers.length, 1);
// A second monitor's window arriving within the gap restarts the wait, so the
// session is switched to exactly once, after both windows are in place.
const extraMonitor = window(first);
bridge.PlaceWindow(first);
assert.equal(extraMonitor.ws, one.ws);
assert.equal(timers.length, 1);
assert.equal(trailing.activations, 0);
assert.equal(fireTimers(), 1);
assert.equal(trailing.activations, 1);
assert.equal(bridge._pending, null);
const two = window(second);
bridge.PlaceWindow(second);
assert.notEqual(two.ws, one.ws);
assert.equal(workspaces.length, 3);
fireTimers();
assert.equal(two.ws.activations, 1);
// A workspace dropped before the gap ends is left alone.
const gone = window('AVD-' + 'd'.repeat(32));
bridge.PlaceWindow(gone.get_title());
const removed = workspaces.pop();
assert.equal(removed, gone.ws);
fireTimers();
assert.equal(removed.activations, 0);
Meta.prefs_get_dynamic_workspaces = () => false;
const fixed = window('AVD-' + 'c'.repeat(32));
assert.equal(bridge.PlaceWindow(fixed.get_title()), false);
assert.equal(workspaces.length, 3); // Never persist num-workspaces in fixed mode.
const spare = workspace();
assert.equal(bridge.PlaceWindow(fixed.get_title()), true);
assert.equal(fixed.ws, spare);
assert.equal(workspaces.length, 4);
// disable() cancels a pending switch.
assert.equal(timers.length, 1);
bridge._unpatch = null;
bridge.disable();
assert.equal(timers.length, 0);
assert.equal(spare.activations, 0);
'''

ANIMATION_GUARD = '''
const bridge = new AvdWorkspaces();
const original = WorkspaceGroup.prototype._syncStacking;
bridge._patchWorkspaceAnimation();
assert.notEqual(WorkspaceGroup.prototype._syncStacking, original);
const known = window('AVD-' + 'a'.repeat(32));
const group = Object.create(WorkspaceGroup.prototype);
group._windowRecords = [];
group.children = [];
group.add_child = function (c) { this.children.push(c); };
group._createClone(global.get_window_actors()[0]);
// A window that appeared after the snapshot gets a clone, then the shell's
// own restack runs over the completed records.
const late = window('AVD-' + 'b'.repeat(32));
group._syncStacking();
assert.equal(group._windowRecords.length, 2);
assert.equal(group._windowRecords[1].windowActor.meta_window, late);
assert.equal(group.children.length, 1);
assert.deepEqual(stackingCalls, [2]);
// Nothing new: no clone is added and the original still runs.
group._syncStacking();
assert.equal(group._windowRecords.length, 2);
assert.deepEqual(stackingCalls, [2, 2]);
// A group that is not ours (no records) is passed straight through.
const foreign = Object.create(WorkspaceGroup.prototype);
foreign._windowRecords = undefined;
let threw = false;
try { foreign._syncStacking(); } catch (e) { threw = true; }
assert.ok(threw, 'original ran on the foreign group');
// disable() restores the shell's method.
bridge._pending = null;
bridge.disable();
assert.equal(WorkspaceGroup.prototype._syncStacking, original);
'''


def _runtime():
    for name, args in (('node', []), ('gjs', [])):
        path = shutil.which(name)
        if path:
            return [path, *args]
    pytest.skip('Neither Node.js nor gjs is available')


def _run(tmp_path, body):
    runtime = _runtime()
    source = (Path(__file__).parents[1] / 'gnome-extension/avd-workspaces@avd-feed-connect/extension.js').read_text()
    source = '\n'.join(line for line in source.splitlines() if not line.startswith('import '))
    source = source.replace('export default class', 'class')
    path = tmp_path / 'test.js'
    path.write_text(PRELUDE + source + body)
    result = subprocess.run([*runtime, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_extension_session_isolation_and_settled_switch(tmp_path):
    _run(tmp_path, PLACEMENT)


def test_extension_guards_the_shell_switch_animation(tmp_path):
    _run(tmp_path, ANIMATION_GUARD)
