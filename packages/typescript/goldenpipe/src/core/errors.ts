/** goldenpipe-local exceptions. */
export class PipeNotConfidentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PipeNotConfidentError";
  }
}
