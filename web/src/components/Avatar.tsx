import { useState } from "react";

// Circular headshot with a mono monogram fallback (accent initials on accent-soft)
// when headshot_url is null OR the image fails to load. Matches the design's
// "object-position: top center" crop.
function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Avatar({
  name,
  src,
  size = 46,
}: {
  name: string;
  src: string | null;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const showImg = src && !failed;
  return (
    <div
      className="relative flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-white/10 bg-accent-soft font-mono font-bold text-accent"
      style={{ width: size, height: size, fontSize: size * 0.33 }}
    >
      {initials(name)}
      {showImg && (
        <img
          src={src}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover"
          style={{ objectPosition: "top center", background: "#12101d" }}
        />
      )}
    </div>
  );
}
