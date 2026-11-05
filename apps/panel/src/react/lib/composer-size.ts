/** Single-line field height must keep the text vertically centered.

scrollHeight already includes vertical padding. Rounding the whole box up to a
line multiple leaves the extra pixels under the glyphs.
*/
export function composerFieldHeight(scrollHeight: number, pad: number, line = 24, max = 240): number {
  const content = Math.max(line, scrollHeight - pad);
  const lines = Math.max(1, Math.ceil(content / line));
  return Math.min(max, lines * line + pad);
}
