/** @odoo-module **/

// What speech needs, and what every provider reads: one channel, 16 bits,
// 16000 samples a second
export const SAMPLE_RATE = 16000;

const HEADER_SIZE = 44;

/**
 * @param {Float32Array} samples between -1 and 1
 * @param {number} [sampleRate]
 * @returns {ArrayBuffer} a WAV file
 */
export function encodeWav(samples, sampleRate = SAMPLE_RATE) {
    const size = samples.length * 2;
    const view = new DataView(new ArrayBuffer(HEADER_SIZE + size));
    const writeText = (offset, text) => {
        for (let index = 0; index < text.length; index++) {
            view.setUint8(offset + index, text.charCodeAt(index));
        }
    };
    writeText(0, "RIFF");
    view.setUint32(4, HEADER_SIZE - 8 + size, true);
    writeText(8, "WAVE");
    writeText(12, "fmt ");
    view.setUint32(16, 16, true);
    // Plain samples, one channel
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeText(36, "data");
    view.setUint32(40, size, true);
    for (let index = 0; index < samples.length; index++) {
        const sample = Math.max(-1, Math.min(1, samples[index]));
        view.setInt16(HEADER_SIZE + 2 * index, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return view.buffer;
}

/**
 * Browsers record in a format of their own choice. It is read back and
 * written again as the WAV file above.
 *
 * @param {Blob} recording as the recorder of the browser made it
 * @returns {Promise<Blob>}
 */
export async function toSpeechWav(recording) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContext();
    let decoded;
    try {
        decoded = await context.decodeAudioData(await recording.arrayBuffer());
    } finally {
        context.close();
    }
    const length = Math.max(1, Math.ceil(decoded.duration * SAMPLE_RATE));
    const offline = new window.OfflineAudioContext(1, length, SAMPLE_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    return new Blob([encodeWav(rendered.getChannelData(0))], { type: "audio/wav" });
}
