// /resolve returns 1 entry per video (a playlist returns many). Emit one item per video.
// If nothing resolved, emit a single marker item so the run still reaches the summary page.
const lines = $('Split lines').all();
const out = [];
$input.all().forEach((item, i) => {
  const src = lines[i] ? lines[i].json : {};
  for (const e of item.json.entries || []) {
    out.push({ json: { ...e, trim: src.trim, batch: src.batch } });
  }
});
return out.length ? out : [{ json: { nothing: true } }];
