/** Unwraps the `${status} {json}` error shape thrown by the api client
 *  (see `json()` in `./api.ts`) into the server's `detail` message, when
 *  present. Falls back to the raw message otherwise. */
export function humanizeError(message: string): string {
  const match = message.match(/^\d+\s+(\{.*\})$/s);
  if (match) {
    try {
      const body = JSON.parse(match[1]!);
      if (typeof body.detail === "string") return body.detail;
    } catch {
      // ignore
    }
  }
  return message;
}
