// One block of fields per track: file name, keep-range, export/skip - pre-filled with the suggestions.
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (sec) => { const m = Math.floor(sec / 60); const s = (sec - m * 60).toFixed(1); return m + ':' + (s < 10 ? '0' : '') + s; };
const { tracks } = $json;
const fields = [];
tracks.forEach((t, idx) => {
  const n = idx + 1;
  const reasons = (t.reasons || []).length
    ? '<ul>' + t.reasons.map((r) => '<li>' + esc(r) + '</li>').join('') + '</ul>'
    : '<p><i>No edits suggested - the whole audio is kept.</i></p>';
  fields.push({
    fieldType: 'html',
    html:
      '<hr><h3>' + n + '. ' + esc(t.video_title) + '</h3>' +
      '<p><a href="' + esc(t.url) + '" target="_blank">source</a> · ' + esc(t.type) + ' · original ' + fmt(t.duration) +
      ' → keeps ' + fmt(t.kept_seconds) + ' · name from ' + esc(t.name_source) + '</p>' +
      reasons +
      '<p>Listen: <a href="' + esc(t.preview_start_url) + '" target="_blank">▶ new start</a> · ' +
      '<a href="' + esc(t.preview_end_url) + '" target="_blank">▶ new ending</a></p>',
  });
  fields.push({
    fieldLabel: n + '. File name (Artist - Song Name)',
    fieldName: 'name_' + n,
    fieldType: 'text',
    defaultValue: t.artist + ' - ' + t.title,
    requiredField: true,
  });
  fields.push({
    fieldLabel: n + '. Keep from - to (m:ss.s - m:ss.s)',
    fieldName: 'range_' + n,
    fieldType: 'text',
    defaultValue: t.range,
    requiredField: true,
  });
  fields.push({
    fieldLabel: n + '. Action',
    fieldName: 'action_' + n,
    fieldType: 'dropdown',
    fieldOptions: { values: [{ option: 'Export' }, { option: 'Skip' }] },
    defaultValue: 'Export',
  });
});
return [{ json: { fields } }];
