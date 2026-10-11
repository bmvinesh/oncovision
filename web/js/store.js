// Minimal observable store. One source of truth for the simulation state
// (patient, scenario, time) that the viewer, chart and readout all read.
export function createStore(initial) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const changed = [];
      for (const k of Object.keys(patch)) if (state[k] !== patch[k]) changed.push(k);
      if (!changed.length) return;
      state = { ...state, ...patch };
      subs.forEach((fn) => fn(state, changed));
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}
