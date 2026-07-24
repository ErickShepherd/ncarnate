#!/usr/bin/env python3
"""Compose the GitHub social-preview card (1280x640) from the shipped dark lockup.

GitHub's "Social preview" is the OpenGraph image served wherever the repo URL is shared
(LinkedIn, Slack, X, Discord). Without one, GitHub auto-generates a generic templated card
carrying no brand of its own. This builds a branded replacement from the committed lockup,
so the card is reproducible and versioned rather than existing only as a settings upload.

The card must read at THUMBNAIL size, so it carries only the lockup, a one-line tagline,
and a short fact line; no dense screenshots.

Steps:
  1. Paint the deep-navy field, then centre the shipped dark lockup (raster mark + off-white word).
  2. Rule a thin ember accent under the wordmark.
  3. Typeset the tagline (off-white) and the fact line (ember) in the wordmark's own Sora.

Deps: pillow. Run from this directory (inputs resolve relative to the CWD):
  build_social_preview.py                      # writes ncarnate-social-preview.png
  build_social_preview.py -o /tmp/card.png
"""
import argparse

from PIL import Image, ImageDraw, ImageFont

# ---- inputs / palette (match brand/README.md) -------------------------------------------------
LOCKUP = "ncarnate-lockup-dark.png"  # shipped raster lockup, off-white wordmark for dark bg
FONT = "sora-600.ttf"                # Sora SemiBold (weight 600), SIL Open Font License 1.1
FIELD = (21, 42, 71)                 # #152A47 deep navy (field / primary ink)
STRUCTURE = (242, 237, 225)          # #F2EDE1 warm off-white (phoenix + graticule)
EMBER = (232, 132, 60)              # #E8843C crest flame (the spark of rebirth)

# ---- card geometry ----------------------------------------------------------------------------
CARD_W, CARD_H = 1280, 640           # GitHub's recommended social-preview size (2:1)
LOCKUP_W = 900                       # lockup width within the card
LOCKUP_Y = 150                       # lockup top edge
RULE_W, RULE_H = 210, 4              # ember accent rule
RULE_GAP = 28                        # gap from lockup bottom to the rule
TAGLINE_GAP = 48                     # gap from lockup bottom to the tagline
FACTS_GAP = 118                      # gap from lockup bottom to the fact line
TAGLINE_SIZE, FACTS_SIZE = 40, 26
TAGLINE = "Reincarnate legacy scientific data as modern netCDF4"
FACTS = "reads HDF4, HDF-EOS2, netCDF   ·   reconstructs CF coordinates   ·   MIT"


def build(output: str) -> None:
    """Renders the social-preview card and writes it to ``output``."""
    lockup = Image.open(LOCKUP).convert("RGBA")
    scale = LOCKUP_W / lockup.width
    lockup = lockup.resize((LOCKUP_W, int(lockup.height * scale)), Image.LANCZOS)

    card = Image.new("RGB", (CARD_W, CARD_H), FIELD)
    card.paste(lockup, ((CARD_W - lockup.width) // 2, LOCKUP_Y), lockup)

    draw = ImageDraw.Draw(card)
    tag_font = ImageFont.truetype(FONT, TAGLINE_SIZE)
    fact_font = ImageFont.truetype(FONT, FACTS_SIZE)

    def centre(text: str, font: ImageFont.FreeTypeFont, y: int,
               fill: tuple[int, int, int]) -> None:
        draw.text(((CARD_W - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    baseline = LOCKUP_Y + lockup.height
    draw.rectangle(
        [(CARD_W - RULE_W) // 2, baseline + RULE_GAP,
         (CARD_W + RULE_W) // 2, baseline + RULE_GAP + RULE_H],
        fill=EMBER,
    )
    centre(TAGLINE, tag_font, baseline + TAGLINE_GAP, STRUCTURE)
    centre(FACTS, fact_font, baseline + FACTS_GAP, EMBER)

    card.save(output)
    print(f"wrote {output} ({card.width}x{card.height})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the ncarnate GitHub social-preview card.")
    parser.add_argument("-o", "--output", default="ncarnate-social-preview.png")
    build(parser.parse_args().output)
