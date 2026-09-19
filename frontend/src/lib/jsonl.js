/**
 * Read a JSONL file the browser picked, into an array of objects.
 *
 * Parsed here rather than posted as multipart: it keeps python-multipart out
 * of the backend's requirements, gives one code path for "a file" and "rows I
 * already have", and means a malformed line is reported with its line number
 * before anything reaches the server.
 */
export async function readJsonl(file) {
  const text = await file.text()
  const rows = []
  const errors = []
  text.split('\n').forEach((line, i) => {
    const t = line.trim()
    if (!t) return
    try {
      rows.push(JSON.parse(t))
    } catch {
      errors.push(i + 1)
    }
  })
  // some llm.py replies arrive pretty-printed as one JSON array or envelope
  if (!rows.length && text.trim()) {
    try {
      const whole = JSON.parse(text)
      const list = Array.isArray(whole) ? whole : whole.answers
      if (Array.isArray(list)) return { rows: list, errors: [] }
    } catch { /* fall through to the line errors below */ }
  }
  return { rows, errors }
}
