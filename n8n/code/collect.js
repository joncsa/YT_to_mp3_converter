// Gather every analysis into one item: tracks that are ready + everything that failed.
const msg = (e) => (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e));
const lines = $('Split lines').all().map((i) => i.json);
const failures = [];

$('Resolve (search / playlist)').all().forEach((it, i) => {
  const r = it.json;
  if (r.error || !(r.entries && r.entries.length)) {
    failures.push({ input: r.input || (lines[i] && lines[i].input), error: r.error ? msg(r.error) : 'no results' });
  }
});

const tracks = [];
const seen = new Set();
if ($('Download & analyze').isExecuted) {
  const entries = $('Found anything?').all().map((i) => i.json);
  $('Download & analyze').all().forEach((it, i) => {
    const r = it.json;
    const e = entries[i] || {};
    if (r.ok) {
      if (seen.has(r.id)) return; // same video pasted twice / via a playlist
      seen.add(r.id);
      tracks.push(r);
    } else {
      failures.push({ input: r.input || e.input || e.url, error: msg(r.error || 'analysis failed') });
    }
  });
}

return [{ json: { tracks, failures, review: lines[0].review, batch: lines[0].batch } }];
