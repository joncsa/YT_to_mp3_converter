// One item per pasted line. Blank lines, "# comments" and duplicates are dropped.
const form = $('Bulk MP3 Request').first().json;
const pick = (...keys) => {
  for (const k of keys) if (form[k] !== undefined && form[k] !== null && form[k] !== '') return String(form[k]);
  return '';
};
const text = pick('items', 'Links, songs or podcast titles');
const typeLabel = pick('content_type', 'Content type');
const trimLabel = pick('trim', 'Trim');
const reviewLabel = pick('review', 'Review names & cut points before export?');

const type = /podcast/i.test(typeLabel) ? 'podcast' : /^music/i.test(typeLabel) ? 'music' : 'auto';
const trim = /^off/i.test(trimLabel) ? 'off' : /^silence only/i.test(trimLabel) ? 'silence' : 'smart';
const review = !/^no/i.test(reviewLabel);
const batch = 'b' + $now.toFormat('yyyyLLdd-HHmmss') + '-' + $execution.id;

const seen = new Set();
const out = [];
for (const raw of text.split(/\r?\n/)) {
  const line = raw.trim();
  if (!line || line.startsWith('#')) continue;
  const key = line.toLowerCase();
  if (seen.has(key)) continue;
  seen.add(key);
  out.push({ json: { input: line, type, trim, review, batch } });
}
if (!out.length) throw new Error('Nothing to do: paste at least one link, song or podcast title.');
return out;
