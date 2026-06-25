import { useState } from "react";

// Player headshot with a deterministic monogram fallback (colored circle) when
// headshot_url is null OR the image fails to load.
const MONO_COLORS = [
  "#1F6FEB",
  "#8957E5",
  "#DB61A2",
  "#E3582C",
  "#2DA44E",
  "#BF8700",
  "#0F8B8D",
];

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function colorFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return MONO_COLORS[h % MONO_COLORS.length];
}

export function Avatar({ name, src }: { name: string; src: string | null }) {
  const [failed, setFailed] = useState(false);
  const showImg = src && !failed;

  return (
    <div
      className="relative h-14 w-14 shrink-0 overflow-hidden rounded-full ring-1 ring-white/10"
      style={showImg ? undefined : { background: colorFor(name) }}
    >
      {showImg ? (
        <img
          src={src}
          alt={name}
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-base font-bold text-white/95">
          {initials(name)}
        </div>
      )}
    </div>
  );
}
