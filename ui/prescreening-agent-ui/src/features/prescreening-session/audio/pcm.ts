/**
 * Base64 <-> PCM16 conversion for the intake call's audio frames.
 *
 * Hand-written rather than pulled from a package: this is two loops over a
 * typed array, and a dependency for it would cost more than it saves.
 */

// btoa/atob work a character at a time, and spreading a whole buffer into
// String.fromCharCode overflows the argument limit on anything but tiny
// frames — so both directions walk in chunks.
const CHUNK_SIZE = 8_192;

/** Decode a base64 PCM16 frame into signed 16-bit samples. */
export function decodeBase64Pcm16(base64: string): Int16Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  // A truncated frame would misalign every sample after it, so the odd
  // trailing byte is dropped rather than read as half a sample.
  const sampleCount = Math.floor(bytes.length / 2);
  return new Int16Array(bytes.buffer, 0, sampleCount);
}

/** Encode signed 16-bit samples as a base64 frame. */
export function encodePcm16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += CHUNK_SIZE) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK_SIZE));
  }
  return btoa(binary);
}

/** Convert PCM16 samples to the -1..1 floats an `AudioBuffer` holds. */
export function pcm16ToFloat32(samples: Int16Array): Float32Array {
  const floats = new Float32Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    // 0x8000, not 0x7fff: full-scale negative is -32768, and dividing by
    // 32767 would clip it past -1 and audibly distort the loudest samples.
    floats[index] = (samples[index] ?? 0) / 0x8000;
  }
  return floats;
}
