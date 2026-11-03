import React, { useEffect, useRef, useContext, createContext } from 'react';
import echarts from './echarts.js';

/* ---------------- theme + chart helpers ---------------- */
export const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);
export const pal = () => {
  const cs = getComputedStyle(document.documentElement); const g = n => cs.getPropertyValue('--' + n).trim();
  return { bg: g('bg'), panel: g('panel'), ink: g('ink'), ink2: g('ink-2'), ink3: g('ink-3'), line: g('line'), line2: g('line-2'), accent: g('accent'), accentSoft: g('accent-soft'), band: g('band'), bar: g('bar'),
    fail: g('fail'), warn: g('warn'), ok: g('ok'), stale: g('stale'), muted: g('muted'), bronze: g('bronze'), silver: g('silver'), gold: g('gold'), transient: g('transient'), sources: g('sources'), snapshot: g('snapshot'), l4: g('l4'), l5: g('l5'), l6: g('l6'), l7: g('l7'), l8: g('l8') };
};
export const statusColor = (p, s) => ({ failed: p.fail, warn: p.warn, stale: p.stale, ok: p.ok, notrun: p.muted }[s] || p.muted);
export const layerColor = (p, g) => p[g] || p.muted;
export const FONT = '"Public Sans",system-ui,sans-serif';
export const base = (p, o) => Object.assign({
  animationDuration: 250, textStyle: { fontFamily: FONT, color: p.ink2, fontSize: 11 },
  grid: { left: 64, right: 24, top: 30, bottom: 50 },
  tooltip: { trigger: 'axis', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12, fontFamily: FONT }, extraCssText: 'box-shadow:0 6px 18px rgba(0,0,0,.14);border-radius:6px;' },
  legend: { top: 0, right: 0, textStyle: { color: p.ink2, fontSize: 11 }, itemWidth: 12, itemHeight: 8, icon: 'roundRect' },
}, o);
export const ax = (p, name, extra) => Object.assign({
  name, nameLocation: 'middle', nameGap: 32, nameTextStyle: { color: p.ink3, fontSize: 11 },
  axisLine: { lineStyle: { color: p.line } }, axisTick: { show: false }, axisLabel: { color: p.ink3, fontSize: 10.5 },
  splitLine: { lineStyle: { color: p.line2 } },
}, extra || {});

export function EChart({ option, height = 260, onClick }) {
  const ref = useRef(null), inst = useRef(null), cb = useRef(onClick);
  cb.current = onClick;
  useEffect(() => {
    const c = echarts.init(ref.current, null, { renderer: 'canvas' }); inst.current = c;
    c.on('click', e => cb.current && cb.current(e));
    const ro = new ResizeObserver(() => c.resize()); ro.observe(ref.current);
    return () => { ro.disconnect(); c.dispose(); inst.current = null; };
  }, []);
  useEffect(() => { if (inst.current) inst.current.setOption(option, true); }, [option]);
  return <div ref={ref} style={{ height, width: '100%', cursor: onClick ? 'pointer' : 'default' }} />;
}
