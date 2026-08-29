/*
 * Botanical artwork, drawn rather than photographed.
 *
 * The reference design leans on full-bleed leaf photography. Shipping binary
 * images would bloat the bundle and tie the look to assets nobody can edit, so
 * the same weight is carried by layered SVG leaf forms. They scale to any
 * viewport and recolour with the theme.
 */

type Props = { className?: string; tone?: "light" | "dark" };

export function LeafField({ className, tone = "dark" }: Props) {
  const stroke = tone === "dark" ? "#f3f4ef" : "#2c3a22";
  return (
    <svg
      className={className}
      viewBox="0 0 1200 500"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="leafwash" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={tone === "dark" ? "#243019" : "#cfd6c2"} />
          <stop offset="55%" stopColor={tone === "dark" ? "#39492a" : "#e2e6d8"} />
          <stop offset="100%" stopColor={tone === "dark" ? "#141c0f" : "#f0f2ea"} />
        </linearGradient>
      </defs>
      <rect width="1200" height="500" fill="url(#leafwash)" />
      {/* Broad blades sweeping across the band, at varying scale and opacity
          so the eye reads depth rather than a repeated motif. */}
      {[
        { d: "M-40 470 C 180 380, 300 200, 250 20", w: 150, o: 0.34 },
        { d: "M180 520 C 420 400, 520 230, 470 40", w: 210, o: 0.22 },
        { d: "M520 500 C 700 400, 800 210, 745 10", w: 175, o: 0.3 },
        { d: "M840 520 C 1010 410, 1090 220, 1035 30", w: 195, o: 0.2 },
        { d: "M1080 480 C 1230 390, 1290 210, 1250 40", w: 145, o: 0.28 },
      ].map((leaf, i) => (
        <g key={i} opacity={leaf.o}>
          <path d={leaf.d} fill="none" stroke={stroke} strokeWidth={leaf.w}
                strokeLinecap="round" opacity={0.45} />
          <path d={leaf.d} fill="none" stroke={stroke} strokeWidth={1.1} />
        </g>
      ))}
    </svg>
  );
}

/** A single upright stem, for quiet corners of the page. */
export function Sprig({ className, tone = "light" }: Props) {
  const stroke = tone === "dark" ? "#f3f4ef" : "#2c3a22";
  return (
    <svg className={className} viewBox="0 0 160 320" fill="none" aria-hidden="true">
      <path d="M80 320 C 80 220, 78 140, 80 20" stroke={stroke} strokeWidth="1.2" opacity="0.5" />
      {[0, 1, 2, 3, 4].map((i) => {
        const y = 60 + i * 48;
        const dir = i % 2 === 0 ? 1 : -1;
        return (
          <path
            key={i}
            d={`M80 ${y} C ${80 + 46 * dir} ${y - 24}, ${80 + 62 * dir} ${y - 4}, ${80 + 20 * dir} ${y + 20}`}
            stroke={stroke}
            strokeWidth="1.1"
            opacity={0.42}
            fill={stroke}
            fillOpacity={0.07}
          />
        );
      })}
    </svg>
  );
}
