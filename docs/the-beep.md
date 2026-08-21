# The Beep

*A debugging story about a machine that hummed its own thoughts into its own microphone — and the two very different kinds of listener it took to catch it.*

<p align="center">
    <img src="../assets/coil-whine-spectrogram.png" alt="Spectrogram of the beep" width="720">
</p>

## The symptom

One morning, after a macOS update (Tahoe 26.6.2), the hold-to-dictate key started misbehaving in two ways at once: transcribed text landed in an unfocused window, and holding the key produced a faint, low, *distorted* beep — the kind of sound you'd describe as "component failure" if you had to describe it at all. Pulsing, roughly once a second. Quiet enough to doubt, persistent enough to annoy.

Only one of us could hear it. The human. The assistant (Claude) has no ears.

## Narrowing it down

The human's first instinct was good experimental method: **move the key**. If the beep belongs to the key, it follows the key. The hotkey moved from right ⌥ to right ⌘ — and the beep came along. So it wasn't the key. It was something about *recording*.

Then came the observation that changed everything: the beep was **visible in the app's own frequency analyzer**, even in total silence. The human's reasoning: *if it's in the analyzer, the microphone is picking it up — which means the assistant can see it too.* The sound had crossed from a private sense into shared data.

So we gave the app a temporary debug tap: every recording also saved as a WAV file. The human held the key and said nothing for twenty seconds. That silence became the most informative recording the app ever made.

## What the silence contained

Analysis of the "silent" capture found:

- **36 bursts**, each ~150 ms long, repeating every **0.535 s** with clockwork regularity
- each burst a **harmonic comb** — overtones spaced ~430 Hz apart, stacked from ~1.5 to 6.5 kHz (a missing-fundamental buzz: heard as low and distorted, exactly as described)
- about 13 dB above a very quiet noise floor
- and crucially: **absent for the first ~1.8 seconds** of the recording

That last detail ruled out the room, the neighbors, the hardware idling. Something *started* shortly after recording began.

## The tell

The app's live-preview feature re-transcribed the growing audio buffer every **0.35 s**, and each inference pass took about **0.15 s** of hard GPU work.

0.35 + 0.15 = **0.5 s cycle**. Measured burst period: **0.535 s**. Burst length: ~150 ms. Inference length: ~150 ms.

The beep was **coil whine**: the laptop's power inductors singing under the pulsed GPU load of the preview loop — picked up by the internal microphone sitting centimeters away. The app was literally recording the sound of its own thinking, displaying it in its own spectrum analyzer, and occasionally transcribing its own hum.

## Proof

Toggle the live preview off. Record silence again. The bursts vanished — envelope periodicity dropped from 0.83 to 0.18, and the human heard nothing. Toggle it back on: beeps. No ambiguity left.

## Epilogue

The fix ended up being a set of choices rather than a single patch (preview modes with different GPU load profiles), but the diagnosis is the part worth writing down. It took a human ear to notice a −60 dBFS pulse, a human's insight that the frequency analyzer made the sound *mutually observable*, and a machine's patience to autocorrelate twenty seconds of silence into a confession.

As the human put it, having tuned into the rhythm: *"The sound is like an instant feedback loop… like having my finger on the pulse of the GPU."*

Some bugs you fix. This one we listened to.
