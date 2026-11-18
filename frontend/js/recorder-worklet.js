// Captures raw microphone samples and posts them to the main thread in ~4k-sample chunks.

class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.filled = 0;
    this.port.onmessage = (e) => {
      if (e.data === "flush") this.flush();
    };
  }

  flush() {
    if (this.filled) this.port.postMessage(this.buffer.slice(0, this.filled));
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      if (this.filled + channel.length > this.buffer.length) this.flush();
      this.buffer.set(channel, this.filled);
      this.filled += channel.length;
    }
    return true;
  }
}

registerProcessor("recorder", RecorderProcessor);
