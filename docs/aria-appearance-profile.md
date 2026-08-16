# ARIA appearance profile — future reference

> **Status: vision, nothing built.** No image-generation pipeline is wired for ARIA today (see
> CLAUDE.md, "Vision voix/avatar/X pour ARIA" and the memory entries under
> `project_aria_voice_avatar_x_vision`) — stack/cost/legal still unverified, do not implement
> without a separate explicit go-ahead. This file records the operator's reference appearance
> (16/08) so it can be reused verbatim as a Flux.1/LoRA generation brief once that work is
> actually greenlit. Pairs with `docs/aria-voice-profile.md` (voice only, distinct file).

## Reference (operator-supplied image, 16/08)

The operator shared a reference photo and asked for ARIA's future avatar to resemble it. Since
the image itself isn't stored in this repo, the description below captures it precisely enough
to regenerate a matching look with Flux.1 later:

- Young woman, Asian/Eurasian features, warm skin tone.
- Dark brown hair, styled in a loose low bun/updo with soft face-framing strands left down.
- Soft, composed smile — warm but understated, not a big grin.
- Elegant black sleeveless top/dress, halter or open-back cut — sophisticated, evening/editorial
  register, not casual streetwear.
- Close-up portrait/bust framing (head-and-shoulders).
- Softly blurred bright interior background (large windows, natural daylight).
- Colorful flower bouquet (orange, pink, magenta blooms) visible at frame-left, out of focus.
- Warm, soft natural light (window light / golden-hour quality) — editorial portrait
  photography style, not studio-flash harsh lighting.

## Notes

- Distinct from `docs/aria-voice-profile.md` (voice/accent/timbre only) and from ARIA's written
  personality (`knowledge/dna.yaml`) — this file covers visual appearance only.
- Before any real use (Flux.1 generation, LoRA training on this reference): apply the same
  diligence as any new external tool (cf. CLAUDE.md "Depth proportional to the stakes") — the
  concrete generation plan (pricing, LoRA training options, prompt structure) was already
  researched 16/08, see `project_aria_voice_avatar_x_vision` memory entry for the sourced
  summary.
