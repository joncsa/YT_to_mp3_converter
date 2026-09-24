"""Assemble workflow.sdk.ts from the Code-node sources in ./code (keeps the JS readable/lintable)."""
import json
from pathlib import Path

HERE = Path(__file__).parent
js = {p.stem: json.dumps(p.read_text()) for p in (HERE / "code").glob("*.js")}

TEMPLATE = r'''import { workflow, node, trigger, sticky, ifElse, expr } from '@n8n/workflow-sdk';

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
  config: { name: 'Split lines', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __split_lines__ } }
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
  config: { name: 'One item per video', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __flatten__ } }
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
  config: { name: 'Collect analyses', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __collect__ } }
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
  config: { name: 'Build review form', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __build_review_form__ } }
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
  config: { name: 'Prepare export', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __prepare_export__ } }
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
  config: { name: 'Build summary', parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode: __summary__ } }
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
'''

out = TEMPLATE
for stem, code in js.items():
    out = out.replace(f"__{stem}__", code)
assert "__" not in out.replace("__agentproxy", ""), "unreplaced placeholder"
(HERE / "workflow.sdk.ts").write_text(out)
print("wrote workflow.sdk.ts", len(out))
