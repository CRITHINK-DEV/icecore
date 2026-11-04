// Run by tests/test_console_strip.py: prints JSON of the Overview layer health strip layout for 1 to 8 layers.
const root = process.argv[2];
const s = await import(root + '/streamlit/components/console/frontend/src/lib/strip.js');
const out = {};
for (let n = 1; n <= 8; n++) {
  const ids = [...Array(n)].map((_, i) => 'l' + i);
  out[n] = { cls: s.stripClass(n), joins: ids.map((_, i) => s.stripJoin(i, n)) };
}
console.log(JSON.stringify({ perRow: s.STRIP_PER_ROW, out }));
