// Merge the (optionally edited) review answers back onto the analysed tracks.
const c = $('Collect analyses').first().json;
const reviewed = $('Review names & cuts').isExecuted ? $('Review names & cuts').first().json : null;
const answer = (keys, fallback) => {
  if (!reviewed) return fallback;
  for (const k of keys) {
    const v = reviewed[k];
    if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim();
  }
  return fallback;
};

const out = [];
c.tracks.forEach((t, idx) => {
  const n = idx + 1;
  const action = answer(['action_' + n, n + '. Action'], 'Export');
  if (/^skip/i.test(action)) return;
  let name = answer(['name_' + n, n + '. File name (Artist - Song Name)'], t.artist + ' - ' + t.title);
  const range = answer(['range_' + n, n + '. Keep from - to (m:ss.s - m:ss.s)'], t.range);
  name = name.replace(/\.mp3$/i, '').trim();
  const sep = name.indexOf(' - ');
  const artist = sep > 0 ? name.slice(0, sep).trim() : t.artist;
  const title = sep > 0 ? name.slice(sep + 3).trim() : name;
  const untouched = range === t.range;
  out.push({
    json: {
      id: t.id,
      artist,
      title,
      range,
      batch: c.batch,
      fade_in: untouched ? t.fade_in : null,
      fade_out: untouched ? t.fade_out : null,
      source: t.url,
    },
  });
});
return out.length ? out : [{ json: { nothing: true } }];
