import { workflow, node, trigger, sticky, ifElse, expr } from '@n8n/workflow-sdk';

const formTrigger = trigger({
  type: 'n8n-nodes-base.formTrigger',
  version: 2.3,
  config: {
    name: 'Bulk MP3 Request',
    parameters: {
      formTitle: 'Bulk YouTube → MP3',
      formDescription: 'One item per line: a YouTube / YouTube Music link, a playlist link, a song name or a podcast episode title.<br>Prefix a line with <b>podcast:</b> or <b>song:</b> to force the search type. Add <b>| Artist - Song Name</b> to force the file name and/or <b>| 0:12-3:45</b> to force the cut.<br>Output: <b>Artist - Song Name.mp3</b>, LAME VBR (V2, ~190 kbps), ID3v2.3 tags + cover art.',
      formFields: {
        values: [
          { fieldLabel: 'Links, songs or podcast titles', fieldName: 'items', fieldType: 'textarea', requiredField: true, placeholder: 'https://www.youtube.com/watch?v=...\nDaft Punk - Around the World\npodcast: Huberman Lab sleep toolkit\nhttps://youtu.be/... | Artist - Song Name | 0:08-3:52' },
          { fieldLabel: 'Content type', fieldName: 'content_type', fieldType: 'dropdown', fieldOptions: { values: [ { option: 'Auto-detect' }, { option: 'Music' }, { option: 'Podcast' } ] }, defaultValue: 'Auto-detect', requiredField: true },
          { fieldLabel: 'Trim', fieldName: 'trim', fieldType: 'dropdown', fieldOptions: { values: [ { option: 'Smart: silence, non-music talk & intro/outro loops' }, { option: 'Silence only' }, { option: 'Off (keep full audio)' } ] }, defaultValue: 'Smart: silence, non-music talk & intro/outro loops', requiredField: true },
          { fieldLabel: 'Review names & cut points before export?', fieldName: 'review', fieldType: 'radio', fieldOptions: { values: [ { option: 'Yes, show me the suggestions' }, { option: 'No, export automatically' } ] }, defaultValue: 'Yes, show me the suggestions', requiredField: true }
        ]
      },
      options: { appendAttribution: false, buttonLabel: 'Find & analyze', path: 'yt-to-mp3' }
    }
  }
});

const settings = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Settings',
    parameters: {
      mode: 'manual',
      includeOtherFields: true,
      assignments: {
        assignments: [
          { id: 'processor-url', name: 'processor_url', value: 'http://audio-processor:8000', type: 'string' }
        ]
      }
    }
  }
});

const splitLines = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'Split lines', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// One item per pasted line. Blank lines, \"# comments\" and duplicates are dropped.\nconst form = $('Bulk MP3 Request').first().json;\nconst pick = (...keys) => {\n  for (const k of keys) if (form[k] !== undefined && form[k] !== null && form[k] !== '') return String(form[k]);\n  return '';\n};\nconst text = pick('items', 'Links, songs or podcast titles');\nconst typeLabel = pick('content_type', 'Content type');\nconst trimLabel = pick('trim', 'Trim');\nconst reviewLabel = pick('review', 'Review names & cut points before export?');\n\nconst type = /podcast/i.test(typeLabel) ? 'podcast' : /^music/i.test(typeLabel) ? 'music' : 'auto';\nconst trim = /^off/i.test(trimLabel) ? 'off' : /^silence only/i.test(trimLabel) ? 'silence' : 'smart';\nconst review = !/^no/i.test(reviewLabel);\nconst batch = 'b' + $now.toFormat('yyyyLLdd-HHmmss') + '-' + $execution.id;\n\nconst seen = new Set();\nconst out = [];\nfor (const raw of text.split(/\\r?\\n/)) {\n  const line = raw.trim();\n  if (!line || line.startsWith('#')) continue;\n  const key = line.toLowerCase();\n  if (seen.has(key)) continue;\n  seen.add(key);\n  out.push({ json: { input: line, type, trim, review, batch } });\n}\nif (!out.length) throw new Error('Nothing to do: paste at least one link, song or podcast title.');\nreturn out;\n" } }
});

const resolveNode = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Resolve (search / playlist)',
    onError: 'continueRegularOutput',
    parameters: {
      method: 'POST',
      url: expr("{{ $('Settings').first().json.processor_url }}/resolve"),
      sendBody: true,
      contentType: 'json',
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify({ input: $json.input, type: $json.type }) }}'),
      options: { timeout: 180000, batching: { batch: { batchSize: 4, batchInterval: 0 } } }
    }
  }
});

const flatten = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'One item per video', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// /resolve returns 1 entry per video (a playlist returns many). Emit one item per video.\n// If nothing resolved, emit a single marker item so the run still reaches the summary page.\nconst lines = $('Split lines').all();\nconst out = [];\n$input.all().forEach((item, i) => {\n  const src = lines[i] ? lines[i].json : {};\n  for (const e of item.json.entries || []) {\n    out.push({ json: { ...e, trim: src.trim, batch: src.batch } });\n  }\n});\nreturn out.length ? out : [{ json: { nothing: true } }];\n" } }
});

const foundAnything = ifElse({
  version: 2.2,
  config: {
    name: 'Found anything?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose' },
        conditions: [ { leftValue: expr('{{ $json.url }}'), operator: { type: 'string', operation: 'notEmpty', singleValue: true } } ],
        combinator: 'and'
      }
    }
  }
});

const analyze = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Download & analyze',
    onError: 'continueRegularOutput',
    parameters: {
      method: 'POST',
      url: expr("{{ $('Settings').first().json.processor_url }}/analyze"),
      sendBody: true,
      contentType: 'json',
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify({ url: $json.url, type: $json.type, trim: $json.trim, input: $json.input, name_override: $json.name_override, start_override: $json.start_override, end_override: $json.end_override }) }}'),
      options: { timeout: 1800000, batching: { batch: { batchSize: 2, batchInterval: 0 } } }
    }
  }
});

const collect = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'Collect analyses', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// Gather every analysis into one item: tracks that are ready + everything that failed.\nconst msg = (e) => (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e));\nconst lines = $('Split lines').all().map((i) => i.json);\nconst failures = [];\n\n$('Resolve (search / playlist)').all().forEach((it, i) => {\n  const r = it.json;\n  if (r.error || !(r.entries && r.entries.length)) {\n    failures.push({ input: r.input || (lines[i] && lines[i].input), error: r.error ? msg(r.error) : 'no results' });\n  }\n});\n\nconst tracks = [];\nconst seen = new Set();\nif ($('Download & analyze').isExecuted) {\n  const entries = $('Found anything?').all().map((i) => i.json);\n  $('Download & analyze').all().forEach((it, i) => {\n    const r = it.json;\n    const e = entries[i] || {};\n    if (r.ok) {\n      if (seen.has(r.id)) return; // same video pasted twice / via a playlist\n      seen.add(r.id);\n      tracks.push(r);\n    } else {\n      failures.push({ input: r.input || e.input || e.url, error: msg(r.error || 'analysis failed') });\n    }\n  });\n}\n\nreturn [{ json: { tracks, failures, review: lines[0].review, batch: lines[0].batch } }];\n" } }
});

const wantsReview = ifElse({
  version: 2.2,
  config: {
    name: 'Review wanted?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose' },
        conditions: [
          { leftValue: expr('{{ $json.review }}'), operator: { type: 'boolean', operation: 'true', singleValue: true } },
          { leftValue: expr('{{ $json.tracks.length }}'), operator: { type: 'number', operation: 'gt' }, rightValue: 0 }
        ],
        combinator: 'and'
      }
    }
  }
});

const buildReview = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'Build review form', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// One block of fields per track: file name, keep-range, export/skip - pre-filled with the suggestions.\nconst esc = (s) => String(s == null ? '' : s).replace(/[&<>\"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;' }[c]));\nconst fmt = (sec) => { const m = Math.floor(sec / 60); const s = (sec - m * 60).toFixed(1); return m + ':' + (s < 10 ? '0' : '') + s; };\nconst { tracks } = $json;\nconst fields = [];\ntracks.forEach((t, idx) => {\n  const n = idx + 1;\n  const reasons = (t.reasons || []).length\n    ? '<ul>' + t.reasons.map((r) => '<li>' + esc(r) + '</li>').join('') + '</ul>'\n    : '<p><i>No edits suggested - the whole audio is kept.</i></p>';\n  fields.push({\n    fieldType: 'html',\n    html:\n      '<hr><h3>' + n + '. ' + esc(t.video_title) + '</h3>' +\n      '<p><a href=\"' + esc(t.url) + '\" target=\"_blank\">source</a> \u00b7 ' + esc(t.type) + ' \u00b7 original ' + fmt(t.duration) +\n      ' \u2192 keeps ' + fmt(t.kept_seconds) + ' \u00b7 name from ' + esc(t.name_source) + '</p>' +\n      reasons +\n      '<p>Listen: <a href=\"' + esc(t.preview_start_url) + '\" target=\"_blank\">\u25b6 new start</a> \u00b7 ' +\n      '<a href=\"' + esc(t.preview_end_url) + '\" target=\"_blank\">\u25b6 new ending</a></p>',\n  });\n  fields.push({\n    fieldLabel: n + '. File name (Artist - Song Name)',\n    fieldName: 'name_' + n,\n    fieldType: 'text',\n    defaultValue: t.artist + ' - ' + t.title,\n    requiredField: true,\n  });\n  fields.push({\n    fieldLabel: n + '. Keep from - to (m:ss.s - m:ss.s)',\n    fieldName: 'range_' + n,\n    fieldType: 'text',\n    defaultValue: t.range,\n    requiredField: true,\n  });\n  fields.push({\n    fieldLabel: n + '. Action',\n    fieldName: 'action_' + n,\n    fieldType: 'dropdown',\n    fieldOptions: { values: [{ option: 'Export' }, { option: 'Skip' }] },\n    defaultValue: 'Export',\n  });\n});\nreturn [{ json: { fields } }];\n" } }
});

const reviewForm = node({
  type: 'n8n-nodes-base.form',
  version: 2.3,
  config: {
    name: 'Review names & cuts',
    parameters: {
      operation: 'page',
      defineForm: 'json',
      jsonOutput: expr('{{ JSON.stringify($json.fields) }}'),
      options: {
        formTitle: 'Review names & cut points',
        formDescription: 'Edit anything that looks wrong. File name must be <b>Artist - Song Name</b>. Times are m:ss.s (or h:mm:ss). Use the ▶ links to hear the new start / ending.',
        buttonLabel: 'Export MP3s'
      }
    }
  }
});

const prepareExport = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'Prepare export', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// Merge the (optionally edited) review answers back onto the analysed tracks.\nconst c = $('Collect analyses').first().json;\nconst reviewed = $('Review names & cuts').isExecuted ? $('Review names & cuts').first().json : null;\nconst answer = (keys, fallback) => {\n  if (!reviewed) return fallback;\n  for (const k of keys) {\n    const v = reviewed[k];\n    if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim();\n  }\n  return fallback;\n};\n\nconst out = [];\nc.tracks.forEach((t, idx) => {\n  const n = idx + 1;\n  const action = answer(['action_' + n, n + '. Action'], 'Export');\n  if (/^skip/i.test(action)) return;\n  let name = answer(['name_' + n, n + '. File name (Artist - Song Name)'], t.artist + ' - ' + t.title);\n  const range = answer(['range_' + n, n + '. Keep from - to (m:ss.s - m:ss.s)'], t.range);\n  name = name.replace(/\\.mp3$/i, '').trim();\n  const sep = name.indexOf(' - ');\n  const artist = sep > 0 ? name.slice(0, sep).trim() : t.artist;\n  const title = sep > 0 ? name.slice(sep + 3).trim() : name;\n  const untouched = range === t.range;\n  out.push({\n    json: {\n      id: t.id,\n      artist,\n      title,\n      range,\n      batch: c.batch,\n      fade_in: untouched ? t.fade_in : null,\n      fade_out: untouched ? t.fade_out : null,\n      source: t.url,\n    },\n  });\n});\nreturn out.length ? out : [{ json: { nothing: true } }];\n" } }
});

const anythingToExport = ifElse({
  version: 2.2,
  config: {
    name: 'Anything to export?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose' },
        conditions: [ { leftValue: expr('{{ $json.id }}'), operator: { type: 'string', operation: 'notEmpty', singleValue: true } } ],
        combinator: 'and'
      }
    }
  }
});

const encode = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.2,
  config: {
    name: 'Encode MP3',
    onError: 'continueRegularOutput',
    parameters: {
      method: 'POST',
      url: expr("{{ $('Settings').first().json.processor_url }}/export"),
      sendBody: true,
      contentType: 'json',
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify({ id: $json.id, artist: $json.artist, title: $json.title, range: $json.range, fade_in: $json.fade_in, fade_out: $json.fade_out, batch: $json.batch }) }}'),
      options: { timeout: 900000, batching: { batch: { batchSize: 2, batchInterval: 0 } } }
    }
  }
});

const summary = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: { name: 'Build summary', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: "// Build the final results page.\nconst esc = (s) => String(s == null ? '' : s).replace(/[&<>\"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;' }[c]));\nconst msg = (e) => (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e));\nconst c = $('Collect analyses').first().json;\nconst ran = $('Encode MP3').isExecuted;\nconst requests = ran ? $('Anything to export?').all().map((i) => i.json) : [];\nconst results = ran ? $('Encode MP3').all().map((i) => i.json) : [];\n\nconst done = [];\nconst failed = c.failures.map((f) => ({ what: f.input, error: f.error }));\nresults.forEach((r, i) => {\n  const req = requests[i] || {};\n  if (r.ok) done.push(r);\n  else failed.push({ what: req.artist ? req.artist + ' - ' + req.title : req.source, error: msg(r.error || r.detail || 'export failed') });\n});\nconst skipped = c.tracks.length - requests.filter((r) => !r.nothing).length;\n\nlet html = '<h2>' + done.length + ' MP3' + (done.length === 1 ? '' : 's') + ' ready</h2>';\nif (done.length) {\n  const base = done[0].url.split('/files/')[0];\n  html += '<p><a href=\"' + esc(base) + '/batches/' + encodeURIComponent(c.batch) + '.zip\"><b>\u2b07 Download all as .zip</b></a>' +\n    ' &nbsp;\u00b7&nbsp; also saved to the processor output folder</p>';\n  html += '<table style=\"width:100%;border-collapse:collapse;text-align:left;font-size:14px\">' +\n    '<tr><th>File</th><th>Kept</th><th>Length</th><th>Avg kbps</th><th>Size</th></tr>';\n  for (const d of done) {\n    html += '<tr style=\"border-top:1px solid #ddd\"><td><a href=\"' + esc(d.url) + '\">' + esc(d.file) + '</a></td>' +\n      '<td>' + esc(d.range) + '</td><td>' + Math.floor(d.seconds / 60) + ':' + String(Math.round(d.seconds % 60)).padStart(2, '0') +\n      '</td><td>' + esc(d.avg_kbps) + '</td><td>' + esc(d.size_mb) + ' MB</td></tr>';\n  }\n  html += '</table>';\n}\nif (skipped > 0) html += '<p>' + skipped + ' skipped in review.</p>';\nif (failed.length) {\n  html += '<h3>' + failed.length + ' failed</h3><ul>' +\n    failed.map((f) => '<li><b>' + esc(f.what) + '</b>: ' + esc(f.error) + '</li>').join('') + '</ul>';\n}\nreturn [{ json: { html, exported: done.length, failed: failed.length, skipped, batch: c.batch } }];\n" } }
});

const done = node({
  type: 'n8n-nodes-base.form',
  version: 2.3,
  config: {
    name: 'Results page',
    parameters: {
      operation: 'completion',
      respondWith: 'showText',
      responseText: expr('{{ $json.html }}')
    }
  }
});

const setupNote = sticky('## Bulk YouTube → MP3\n**Needs the `audio-processor` container** from this repo (`docker compose up -d --build`). It does the downloading (yt-dlp), trimming and MP3 encoding (ffmpeg / LAME V2 VBR).\n\nIf n8n does not run in the same docker compose project, change `processor_url` in **Settings** to wherever the processor is reachable (e.g. `http://192.168.1.20:8000`).\n\nOpen the form via the **Bulk MP3 Request** node → Production URL (activate the workflow first).', [formTrigger, settings], { color: 4 });

const trimNote = sticky('## What "Smart" trim does\n1. cuts leading / trailing silence\n2. skips SponsorBlock `music_offtopic` segments (talk, skits, ads) at the start/end of music videos\n3. skips long quiet ambience at the edges\n4. skips long repetitive loops (≥20 s, ≥3 repeats) before / after the song\n\nEverything is only a *suggestion* until you confirm it on the review page.', [analyze, collect], { color: 6 });

export default workflow('yt-to-mp3', 'Bulk YouTube → MP3 (Artist - Song Name)')
  .add(formTrigger)
  .to(settings)
  .to(splitLines)
  .to(resolveNode)
  .to(flatten)
  .to(foundAnything
    .onTrue(analyze.to(collect))
    .onFalse(collect))
  .add(collect)
  .to(wantsReview
    .onTrue(buildReview.to(reviewForm.to(prepareExport)))
    .onFalse(prepareExport))
  .add(prepareExport)
  .to(anythingToExport
    .onTrue(encode.to(summary))
    .onFalse(summary))
  .add(summary)
  .to(done)
  .add(setupNote)
  .add(trimNote);
