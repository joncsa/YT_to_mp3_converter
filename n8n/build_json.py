"""Build the importable n8n workflow JSON (yt-to-mp3-workflow.json) from ./code/*.js.

Mirrors workflow.sdk.ts (used to create the workflow through the n8n MCP/SDK). Run:
    python3 build_sdk.py && python3 build_json.py
"""
import json
import re
import uuid
from pathlib import Path

HERE = Path(__file__).parent
code = {p.stem: p.read_text() for p in (HERE / "code").glob("*.js")}
sdk = (HERE / "workflow.sdk.ts").read_text()


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "yt-to-mp3/" + name))


def http(name, path, body, timeout, batch, pos):
    return {
        "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos,
        "onError": "continueRegularOutput",
        "parameters": {
            "method": "POST", "url": "={{ $('Settings').first().json.processor_url }}" + path,
            "sendBody": True, "contentType": "json", "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify(" + body + ") }}",
            "options": {"timeout": timeout, "batching": {"batch": {"batchSize": batch, "batchInterval": 0}}},
        },
    }


def js(name, stem, pos):
    return {"name": name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos,
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": code[stem]}}


def cond_if(name, conditions, pos):
    return {"name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos,
            "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                                          "conditions": conditions, "combinator": "and"}}}


def not_empty(expr):
    return {"leftValue": expr, "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}


def sdk_string(var):
    """Pull a single-quoted string literal (form description / sticky text) out of the SDK file."""
    m = re.search(var + r"\s*:?\s*\(?'((?:[^'\\]|\\.)*)'", sdk)
    return m.group(1).encode().decode("unicode_escape").encode("latin-1").decode("utf-8")



nodes = [
    {"name": "Bulk MP3 Request", "type": "n8n-nodes-base.formTrigger", "typeVersion": 2.3, "position": [0, 80],
     "webhookId": uid("form"),
     "parameters": {
         "formTitle": "Bulk YouTube → MP3",
         "formDescription": sdk_string("formDescription"),
         "formFields": {"values": [
             {"fieldLabel": "Links, songs or podcast titles", "fieldName": "items", "fieldType": "textarea", "requiredField": True,
              "placeholder": "https://www.youtube.com/watch?v=...\nDaft Punk - Around the World\npodcast: Huberman Lab sleep toolkit\nhttps://youtu.be/... | Artist - Song Name | 0:08-3:52"},
             {"fieldLabel": "Content type", "fieldName": "content_type", "fieldType": "dropdown", "requiredField": True,
              "fieldOptions": {"values": [{"option": "Auto-detect"}, {"option": "Music"}, {"option": "Podcast"}]}, "defaultValue": "Auto-detect"},
             {"fieldLabel": "Trim", "fieldName": "trim", "fieldType": "dropdown", "requiredField": True,
              "fieldOptions": {"values": [{"option": "Smart: silence, non-music talk & intro/outro loops"}, {"option": "Silence only"}, {"option": "Off (keep full audio)"}]},
              "defaultValue": "Smart: silence, non-music talk & intro/outro loops"},
             {"fieldLabel": "Review names & cut points before export?", "fieldName": "review", "fieldType": "radio", "requiredField": True,
              "fieldOptions": {"values": [{"option": "Yes, show me the suggestions"}, {"option": "No, export automatically"}]},
              "defaultValue": "Yes, show me the suggestions"},
         ]},
         "options": {"appendAttribution": False, "buttonLabel": "Find & analyze", "path": "yt-to-mp3"},
     }},
    {"name": "Settings", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [224, 80],
     "parameters": {"mode": "manual", "includeOtherFields": True, "assignments": {"assignments": [
         {"id": "processor-url", "name": "processor_url", "value": "http://audio-processor:8000", "type": "string"}]}}},
    js("Split lines", "split_lines", [448, 80]),
    http("Resolve (search / playlist)", "/resolve", "{ input: $json.input, type: $json.type }", 180000, 4, [672, 80]),
    js("One item per video", "flatten", [896, 80]),
    cond_if("Found anything?", [not_empty("={{ $json.url }}")], [1120, 80]),
    http("Download & analyze", "/analyze",
         "{ url: $json.url, type: $json.type, trim: $json.trim, input: $json.input, name_override: $json.name_override, start_override: $json.start_override, end_override: $json.end_override }",
         1800000, 2, [1344, 0]),
    js("Collect analyses", "collect", [1568, 80]),
    cond_if("Review wanted?", [
        {"leftValue": "={{ $json.review }}", "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
        {"leftValue": "={{ $json.tracks.length }}", "operator": {"type": "number", "operation": "gt"}, "rightValue": 0},
    ], [1792, 80]),
    js("Build review form", "build_review_form", [2016, 0]),
    {"name": "Review names & cuts", "type": "n8n-nodes-base.form", "typeVersion": 2.3, "position": [2240, 0],
     "webhookId": uid("review"),
     "parameters": {"operation": "page", "defineForm": "json", "jsonOutput": "={{ JSON.stringify($json.fields) }}",
                    "options": {"formTitle": "Review names & cut points",
                                "formDescription": "Edit anything that looks wrong. File name must be <b>Artist - Song Name</b>. Times are m:ss.s (or h:mm:ss). Use the ▶ links to hear the new start / ending.",
                                "buttonLabel": "Export MP3s"}}},
    js("Prepare export", "prepare_export", [2464, 80]),
    cond_if("Anything to export?", [not_empty("={{ $json.id }}")], [2688, 80]),
    http("Encode MP3", "/export",
         "{ id: $json.id, artist: $json.artist, title: $json.title, range: $json.range, fade_in: $json.fade_in, fade_out: $json.fade_out, batch: $json.batch }",
         900000, 2, [2912, 0]),
    js("Build summary", "summary", [3136, 80]),
    {"name": "Results page", "type": "n8n-nodes-base.form", "typeVersion": 2.3, "position": [3360, 80],
     "webhookId": uid("done"),
     "parameters": {"operation": "completion", "respondWith": "showText", "responseText": "={{ $json.html }}"}},
    {"name": "Setup note", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [-40, -380],
     "parameters": {"content": sdk_string("setupNote = sticky"), "color": 4, "width": 520, "height": 340}},
    {"name": "Trim note", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [1300, -380],
     "parameters": {"content": sdk_string("trimNote = sticky"), "color": 6, "width": 480, "height": 340}},
]
for n in nodes:
    n["id"] = uid(n["name"])


def link(*targets):
    return [[{"node": t, "type": "main", "index": 0}] for t in targets]


connections = {
    "Bulk MP3 Request": {"main": link("Settings")},
    "Settings": {"main": link("Split lines")},
    "Split lines": {"main": link("Resolve (search / playlist)")},
    "Resolve (search / playlist)": {"main": link("One item per video")},
    "One item per video": {"main": link("Found anything?")},
    "Found anything?": {"main": link("Download & analyze", "Collect analyses")},
    "Download & analyze": {"main": link("Collect analyses")},
    "Collect analyses": {"main": link("Review wanted?")},
    "Review wanted?": {"main": link("Build review form", "Prepare export")},
    "Build review form": {"main": link("Review names & cuts")},
    "Review names & cuts": {"main": link("Prepare export")},
    "Prepare export": {"main": link("Anything to export?")},
    "Anything to export?": {"main": link("Encode MP3", "Build summary")},
    "Encode MP3": {"main": link("Build summary")},
    "Build summary": {"main": link("Results page")},
}

wf = {"name": "Bulk YouTube → MP3 (Artist - Song Name)", "nodes": nodes, "connections": connections,
      "settings": {"executionOrder": "v1"}, "pinData": {}, "meta": {"templateCredsSetupCompleted": True}}
(HERE / "yt-to-mp3-workflow.json").write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n")
print("wrote yt-to-mp3-workflow.json with", len(nodes), "nodes")
