/** Display-only extraction from an incomplete JSON object, never report validation. */
export function reportStreamSummary(input: string): string {
  if (input.length > 1_000_000) return "";
  let index = 0;
  const whitespace = () => { while (/\s/.test(input[index] ?? "") && index < input.length) index++; };
  const string = (): { text: string; complete: boolean } | null => {
    if (input[index++] !== '"') return null;
    let text = "";
    while (index < input.length) {
      const char = input[index++]!;
      if (char === '"') return { text, complete: true };
      if (char.charCodeAt(0) < 32) return null;
      if (char !== "\\") { text += char; continue; }
      if (index === input.length) break;
      const escape = input[index++]!;
      if (escape === "u") {
        const digits = input.slice(index, index + 4);
        if (!/^[a-fA-F0-9]{0,4}$/.test(digits)) return null;
        if (digits.length < 4) break;
        text += String.fromCharCode(parseInt(digits, 16)); index += 4;
      } else {
        const decoded: Record<string, string> = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
        if (!(escape in decoded)) return null;
        text += decoded[escape];
      }
    }
    // Never render half of an astral character at a chunk boundary.
    if (/[\uD800-\uDBFF]$/.test(text)) text = text.slice(0, -1);
    return { text, complete: false };
  };
  whitespace();
  if (input[index++] !== "{") return "";
  while (index < input.length) {
    whitespace();
    if (input[index] === "}") return "";
    const key = string();
    if (!key?.complete) return "";
    whitespace(); if (input[index++] !== ":") return ""; whitespace();
    if (key.text === "summary") return string()?.text ?? "";
    // Skip one complete value, respecting nested structures and escaped strings.
    const start = index; let depth = 0, inString = false, escaped = false;
    for (; index < input.length; index++) {
      const char = input[index]!;
      if (inString) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') inString = false;
      } else if (char === '"') inString = true;
      else if (char === "{" || char === "[") depth++;
      else if ((char === "," || char === "}") && depth === 0) break;
      else if (char === "}" || char === "]") depth--;
    }
    if (index === input.length || inString || depth !== 0) return "";
    try { JSON.parse(input.slice(start, index)); } catch { return ""; }
    if (input[index++] !== ",") return "";
  }
  return "";
}
