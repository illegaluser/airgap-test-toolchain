import io
import json
import re
import base64


def extract_json_safely(text: str):
    """Safely extract a JSON array or object from LLM response text.

    Recovery strategy (in order):
      1) Strip <think> / markdown, then regex match + json.loads
      2) Remove trailing commas
      3) Convert single quotes → double quotes
      4) Linear scan of individual ``{...}`` objects via JSONDecoder.raw_decode
      5) Parse markdown analysis notes — observed handling for small LLMs
         (gemma4:e4b etc.) that respond as ``**Step 01. ...** / Action: /
         Target: / Value: / Description:`` instead of JSON. Extracts directly
         from **the original** text (covers cases where ``<think>`` was opened
         and never closed but step analysis lives inside it).
    """
    original = text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    # An asymmetric <think> with no closing tag — strip only the tag and keep the body
    # (so individual JSON object lines remain). Covers small LLMs that open <think>
    # and dump analysis + step JSON inside it.
    cleaned = re.sub(r"</?think>", "", cleaned)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"//.*?\n|/\*.*?\*/", "", cleaned, flags=re.S)

    match = re.search(r"\[\s*\{.*\}\s*\]|\{\s*\".*\}\s*", cleaned, re.DOTALL)
    if match:
        raw = match.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        cleaned_raw = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(cleaned_raw)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(cleaned_raw.replace("'", '"'))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    objs = []
    i = 0
    while i < len(cleaned):
        brace_pos = cleaned.find("{", i)
        if brace_pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(cleaned[brace_pos:])
        except json.JSONDecodeError:
            i = brace_pos + 1
            continue
        if isinstance(obj, dict) and (
            "action" in obj or "op" in obj or "step" in obj
        ):
            objs.append(obj)
        i = brace_pos + end
    if objs:
        return objs

    return _parse_markdown_steps(original)


def _parse_markdown_steps(text: str):
    """Convert markdown-style step analysis notes into DSL step dicts. None if absent."""
    step_pattern = re.compile(
        r"(?:\*\*)?Step\s*(\d{1,3})[\.\s].*?(?=(?:\*\*)?Step\s*\d{1,3}[\.\s]|\Z)",
        re.S | re.I,
    )
    field_pattern = re.compile(
        r"(?:\*\s*|-\s*)?(Action|Target|Value|Description)\s*:\s*"
        r'(?:"([^"\n]*)"|([^\n]*))',
        re.I,
    )
    steps = []
    for m in step_pattern.finditer(text):
        block = m.group(0)
        step_num = int(m.group(1))
        fields = {}
        for f in field_pattern.finditer(block):
            key = f.group(1).lower()
            val = (f.group(2) or f.group(3) or "").strip().rstrip(".")
            if key not in fields:
                fields[key] = val
        if "action" in fields and fields["action"]:
            steps.append(
                {
                    "step": step_num,
                    "action": fields["action"].lower(),
                    "target": fields.get("target", ""),
                    "value": fields.get("value", ""),
                    "description": fields.get("description", ""),
                    "fallback_targets": [],
                }
            )
    return steps if steps else None


def parse_structured_doc_steps(text: str):
    """Convert machine-readable step markers in a structured document into a DSL step list.

    Supported format:
      ZTQA_STEP|<step>|<action>|<target>|<value>|<description>

    Example:
      ZTQA_STEP|1|navigate||https://playwright.dev/|Navigate to the main page
      ZTQA_STEP|2|verify|a[href="/docs/intro"]|Docs|Confirm the Docs link is shown

    PDF text extraction frequently breaks line ordering and column structure of
    regular tables, so we expect a 1-line marker block at the end of the document
    and parse it first.
    """
    step_re = re.compile(
        r"^ZTQA_STEP\|(\d{1,4})\|([a-zA-Z_]+)\|([^|]*)\|([^|]*)\|(.+)$",
        re.M,
    )
    steps = []
    for m in step_re.finditer(text):
        steps.append(
            {
                "step": int(m.group(1)),
                "action": m.group(2).strip().lower(),
                "target": m.group(3).strip(),
                "value": m.group(4).strip(),
                "description": m.group(5).strip(),
                "fallback_targets": [],
            }
        )
    return steps if steps else None


def compress_image_to_b64(
    file_path: str, max_size: int = 1024, quality: int = 60
) -> str:
    """Resize and JPEG-compress the image, returning a base64 string."""
    from PIL import Image

    with Image.open(file_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
