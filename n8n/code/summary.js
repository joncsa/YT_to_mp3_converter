// Build the final results page.
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const msg = (e) => (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e));
const c = $('Collect analyses').first().json;
const ran = $('Encode MP3').isExecuted;
const requests = ran ? $('Anything to export?').all().map((i) => i.json) : [];
const results = ran ? $('Encode MP3').all().map((i) => i.json) : [];

const done = [];
const failed = c.failures.map((f) => ({ what: f.input, error: f.error }));
results.forEach((r, i) => {
  const req = requests[i] || {};
  if (r.ok) done.push(r);
  else failed.push({ what: req.artist ? req.artist + ' - ' + req.title : req.source, error: msg(r.error || r.detail || 'export failed') });
});
const skipped = c.tracks.length - requests.filter((r) => !r.nothing).length;

let html = '<h2>' + done.length + ' MP3' + (done.length === 1 ? '' : 's') + ' ready</h2>';
if (done.length) {
  const base = done[0].url.split('/files/')[0];
  html += '<p><a href="' + esc(base) + '/batches/' + encodeURIComponent(c.batch) + '.zip"><b>⬇ Download all as .zip</b></a>' +
    ' &nbsp;·&nbsp; also saved to the processor output folder</p>';
  html += '<table style="width:100%;border-collapse:collapse;text-align:left;font-size:14px">' +
    '<tr><th>File</th><th>Kept</th><th>Length</th><th>Avg kbps</th><th>Size</th></tr>';
  for (const d of done) {
    html += '<tr style="border-top:1px solid #ddd"><td><a href="' + esc(d.url) + '">' + esc(d.file) + '</a></td>' +
      '<td>' + esc(d.range) + '</td><td>' + Math.floor(d.seconds / 60) + ':' + String(Math.round(d.seconds % 60)).padStart(2, '0') +
      '</td><td>' + esc(d.avg_kbps) + '</td><td>' + esc(d.size_mb) + ' MB</td></tr>';
  }
  html += '</table>';
}
if (skipped > 0) html += '<p>' + skipped + ' skipped in review.</p>';
if (failed.length) {
  html += '<h3>' + failed.length + ' failed</h3><ul>' +
    failed.map((f) => '<li><b>' + esc(f.what) + '</b>: ' + esc(f.error) + '</li>').join('') + '</ul>';
}
return [{ json: { html, exported: done.length, failed: failed.length, skipped, batch: c.batch } }];
